package wiz;

import com.sun.jdi.ArrayReference;
import com.sun.jdi.ClassType;
import com.sun.jdi.Field;
import com.sun.jdi.IntegerValue;
import com.sun.jdi.LongValue;
import com.sun.jdi.ObjectReference;
import com.sun.jdi.PrimitiveValue;
import com.sun.jdi.ReferenceType;
import com.sun.jdi.Value;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;

/**
 * Reads the elements of common JDK collections and maps from their private fields (JDK 17 to 25),
 * without invoking any method in the test JVM. Each reader returns the collection's size, or -1
 * when it does not know the type (the caller then shows the class name).
 */
final class JdkCollections {
  private static final int CHUNK = 64; // array slots read per JDWP request

  private JdkCollections() {}

  /** Up to `max` elements of a JDK collection into `out`. */
  static int items(ObjectReference o, int max, List<Value> out) {
    ReferenceType t = o.referenceType();
    String n = t.name();
    switch (n) {
      case "java.util.ArrayList", "java.util.Arrays$ArrayList", "java.util.PriorityQueue",
          "java.util.concurrent.CopyOnWriteArrayList", "java.util.ImmutableCollections$ListN" -> {
        ArrayReference a = array(o, "elementData", "a", "queue", "array", "elements");
        if (a == null) return -1;
        int size = n.equals("java.util.ArrayList") || n.equals("java.util.PriorityQueue") ? intField(o, "size") : a.length();
        return slots(a, 0, size, max, out, false);
      }
      case "java.util.Vector", "java.util.Stack" -> {
        ArrayReference a = array(o, "elementData");
        return a == null ? -1 : slots(a, 0, intField(o, "elementCount"), max, out, false);
      }
      case "java.util.ImmutableCollections$List12", "java.util.ImmutableCollections$Set12" -> {
        int size = 0;
        for (String f : new String[] {"e0", "e1"}) {
          Value v = field(o, f);
          if (isEmptyMarker(v)) continue; // List.of(x): e1 is a private sentinel Object
          size++;
          if (out.size() < max) out.add(v);
        }
        return size;
      }
      case "java.util.ImmutableCollections$SetN" -> {
        ArrayReference a = array(o, "elements");
        if (a == null) return -1;
        slots(a, 0, a.length(), max, out, true);
        return intField(o, "size");
      }
      case "java.util.Collections$SingletonList", "java.util.Collections$SingletonSet" -> {
        out.add(field(o, "element"));
        return 1;
      }
      case "java.util.Collections$EmptyList", "java.util.Collections$EmptySet" -> {
        return 0;
      }
      case "java.util.LinkedList" -> {
        int size = intField(o, "size");
        for (Value node = field(o, "first"); node instanceof ObjectReference r && out.size() < max; node = field(r, "next")) {
          out.add(field(r, "item"));
        }
        return size;
      }
      case "java.util.ArrayDeque" -> {
        ArrayReference a = array(o, "elements");
        if (a == null) return -1;
        int head = intField(o, "head"), tail = intField(o, "tail"), len = a.length();
        int size = len == 0 ? 0 : ((tail - head) % len + len) % len;
        for (int i = 0; i < Math.min(size, max); i++) out.add(a.getValue((head + i) % len));
        return size;
      }
      case "java.util.HashSet", "java.util.LinkedHashSet" -> {
        return keysOf(field(o, "map"), max, out);
      }
      case "java.util.TreeSet", "java.util.Collections$SetFromMap" -> {
        return keysOf(field(o, "m"), max, out);
      }
      case "java.util.RegularEnumSet" -> {
        ArrayReference universe = array(o, "universe");
        if (universe == null || !(field(o, "elements") instanceof LongValue bits)) return -1;
        long b = bits.value();
        for (int i = 0; i < universe.length() && out.size() < max; i++) {
          if ((b & (1L << i)) != 0) out.add(universe.getValue(i));
        }
        return Long.bitCount(b);
      }
      default -> {
        // keySet() and values() views: HashMap$KeySet, LinkedHashMap$LinkedValues, TreeMap$KeySet, ...
        boolean keys = n.endsWith("$KeySet") || n.endsWith("$LinkedKeySet");
        boolean vals = n.endsWith("$Values") || n.endsWith("$LinkedValues");
        if (!keys && !vals) return -1;
        Value map = field(o, "this$0", "m");
        if (!(map instanceof ObjectReference m)) return -1;
        List<Value[]> entries = new java.util.ArrayList<>();
        int size = entries(m, max, entries);
        for (Value[] kv : entries) out.add(kv[keys ? 0 : 1]);
        return size;
      }
    }
  }

  /** Up to `max` {key, value} pairs of a JDK map into `out`, in iteration order. */
  static int entries(ObjectReference o, int max, List<Value[]> out) {
    String n = known(o.referenceType(), MAPS);
    if (n == null) return -1;
    switch (n) {
      case "java.util.LinkedHashMap" -> {
        for (Value e = field(o, "head"); e instanceof ObjectReference r && out.size() < max; e = field(r, "after")) {
          out.add(new Value[] {field(r, "key"), field(r, "value")});
        }
        return intField(o, "size");
      }
      case "java.util.HashMap", "java.util.Hashtable" -> {
        ArrayReference table = array(o, "table");
        if (table != null) buckets(table, "key", "value", max, out);
        return intField(o, n.equals("java.util.HashMap") ? "size" : "count");
      }
      case "java.util.Properties" -> {
        Value map = field(o, "map");
        return map instanceof ObjectReference m ? entries(m, max, out) : -1;
      }
      case "java.util.concurrent.ConcurrentHashMap" -> {
        ArrayReference table = array(o, "table");
        if (table != null) buckets(table, "key", "val", max, out);
        long size = longField(o, "baseCount");
        if (array(o, "counterCells") instanceof ArrayReference cells) {
          for (Value c : cells.getValues()) if (c instanceof ObjectReference cell) size += longField(cell, "value");
        }
        return (int) Math.min(size, Integer.MAX_VALUE);
      }
      case "java.util.TreeMap" -> {
        Deque<ObjectReference> path = new ArrayDeque<>();
        Value node = field(o, "root");
        while ((node instanceof ObjectReference || !path.isEmpty()) && out.size() < max) {
          while (node instanceof ObjectReference r) {
            path.push(r);
            node = field(r, "left");
          }
          ObjectReference r = path.pop();
          out.add(new Value[] {field(r, "key"), field(r, "value")});
          node = field(r, "right");
        }
        return intField(o, "size");
      }
      case "java.util.ImmutableCollections$Map1" -> {
        out.add(new Value[] {field(o, "k0"), field(o, "v0")});
        return 1;
      }
      case "java.util.Collections$SingletonMap" -> {
        out.add(new Value[] {field(o, "k"), field(o, "v")});
        return 1;
      }
      case "java.util.ImmutableCollections$MapN" -> {
        ArrayReference table = array(o, "table");
        if (table == null) return -1;
        for (int i = 0; i + 1 < table.length() && out.size() < max; i += 2) {
          Value k = table.getValue(i);
          if (k != null) out.add(new Value[] {k, table.getValue(i + 1)});
        }
        return intField(o, "size");
      }
      case "java.util.Collections$EmptyMap" -> {
        return 0;
      }
      default -> {
        return -1;
      }
    }
  }

  private static final List<String> MAPS = List.of(
      "java.util.LinkedHashMap", "java.util.HashMap", "java.util.Properties", "java.util.Hashtable",
      "java.util.concurrent.ConcurrentHashMap", "java.util.TreeMap", "java.util.ImmutableCollections$Map1",
      "java.util.Collections$SingletonMap", "java.util.ImmutableCollections$MapN", "java.util.Collections$EmptyMap");

  /** The first of `names` that is `t` or one of its superclasses (a JDK subclass reads like its parent). */
  private static String known(ReferenceType t, List<String> names) {
    for (ReferenceType s = t; s instanceof ClassType ct; s = ct.superclass()) {
      if (names.contains(s.name())) return s.name();
    }
    return null;
  }

  private static int keysOf(Value map, int max, List<Value> out) {
    if (!(map instanceof ObjectReference m)) return -1;
    List<Value[]> entries = new java.util.ArrayList<>();
    int size = entries(m, max, entries);
    for (Value[] kv : entries) out.add(kv[0]);
    return size;
  }

  /** Hash table buckets: each slot is a chain of nodes linked by `next` (a ConcurrentHashMap tree bin by `first`). */
  private static void buckets(ArrayReference table, String key, String value, int max, List<Value[]> out) {
    for (int from = 0; from < table.length() && out.size() < max; from += CHUNK) {
      for (Value slot : table.getValues(from, Math.min(CHUNK, table.length() - from))) {
        Value node = slot;
        if (node instanceof ObjectReference bin && bin.referenceType().name().endsWith("ConcurrentHashMap$TreeBin")) {
          node = field(bin, "first");
        }
        for (; node instanceof ObjectReference r && out.size() < max; node = field(r, "next")) {
          if (r.referenceType().name().endsWith("$ForwardingNode")) break; // mid-resize: skip
          out.add(new Value[] {field(r, key), field(r, value)});
        }
        if (out.size() >= max) return;
      }
    }
  }

  /** Elements [from, from+size) of an array; `skipNulls` for open-addressed tables (Set.of). */
  private static int slots(ArrayReference a, int from, int size, int max, List<Value> out, boolean skipNulls) {
    int end = Math.min(size, a.length());
    for (int i = from; i < end && out.size() < max; i += CHUNK) {
      for (Value v : a.getValues(i, Math.min(CHUNK, end - i))) {
        if (skipNulls && v == null) continue;
        if (out.size() >= max) break;
        out.add(v);
      }
    }
    return size;
  }

  private static boolean isEmptyMarker(Value v) {
    return v instanceof ObjectReference r && r.referenceType().name().equals("java.lang.Object");
  }

  static Value field(ObjectReference o, String... names) {
    for (String n : names) {
      Field f = o.referenceType().fieldByName(n);
      if (f != null) return o.getValue(f);
    }
    return null;
  }

  private static ArrayReference array(ObjectReference o, String... names) {
    return field(o, names) instanceof ArrayReference a ? a : null;
  }

  static int intField(ObjectReference o, String name) {
    Value v = field(o, name);
    return v instanceof IntegerValue i ? i.value() : v instanceof PrimitiveValue p ? p.intValue() : 0;
  }

  static long longField(ObjectReference o, String name) {
    Value v = field(o, name);
    return v instanceof PrimitiveValue p ? p.longValue() : 0;
  }
}
