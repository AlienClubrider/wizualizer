package wiz;

import com.sun.jdi.ArrayReference;
import com.sun.jdi.CharValue;
import com.sun.jdi.ClassType;
import com.sun.jdi.Field;
import com.sun.jdi.InterfaceType;
import com.sun.jdi.ObjectReference;
import com.sun.jdi.PrimitiveValue;
import com.sun.jdi.ReferenceType;
import com.sun.jdi.StringReference;
import com.sun.jdi.Value;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.function.Predicate;

/**
 * Bounded, crash-proof rendering of JDI values as short strings (depth 3, 8 items, 80 chars per
 * string, 240 in total), the same limits as the Python recorder.
 *
 * <p>Nothing is ever invoked in the test JVM: a method call on a thread stopped at an event can
 * hit one of the recorder's own requests (or a lock the thread holds) and never return. Project
 * objects are shown by their fields, JDK collections and a few JDK value types (BigDecimal,
 * java.time, UUID, URI) are read from their fields, other objects show as their class name.
 */
final class Values {
  static final int MAX_DEPTH = 3, MAX_ITEMS = 8, MAX_STR = 80, MAX_TOTAL = 240;

  private static final Set<String> BOXED =
      Set.of(
          "java.lang.Integer", "java.lang.Long", "java.lang.Short", "java.lang.Byte",
          "java.lang.Double", "java.lang.Float", "java.lang.Boolean", "java.lang.Character",
          "java.util.concurrent.atomic.AtomicInteger", "java.util.concurrent.atomic.AtomicLong",
          "java.util.concurrent.atomic.AtomicBoolean");

  private final Predicate<ReferenceType> isProject;

  Values(Predicate<ReferenceType> isProject) {
    this.isProject = isProject;
  }

  String render(Value v) {
    try {
      return clip(render(v, MAX_DEPTH), MAX_TOTAL);
    } catch (Exception e) {
      return "<" + (v == null ? "?" : simple(v.type().name())) + ": unreadable>";
    }
  }

  private String render(Value v, int depth) {
    if (v == null) return "null";
    if (v instanceof CharValue c) return "'" + c.value() + "'";
    if (v instanceof PrimitiveValue p) return p.toString();
    if (v instanceof StringReference s) return quote(s.value());
    if (v instanceof ArrayReference a) return renderArray(a, depth);
    ObjectReference o = (ObjectReference) v;
    ReferenceType t = o.referenceType();
    String name = t.name();
    if (BOXED.contains(name)) return render(o.getValue(t.fieldByName("value")), depth);
    if (isEnum(t)) return simple(name) + "." + str(o, "name");
    if (name.contains("$MockitoMock$")) return "mock " + simple(name.substring(0, name.indexOf("$MockitoMock$")));
    if (name.contains("$$Lambda")) return "<lambda>";
    if (isThrowable(t)) {
      String msg = str(o, "detailMessage");
      return simple(name) + (msg == null ? "" : ": " + clip(msg, MAX_STR));
    }
    if (name.equals("java.util.Optional")) {
      Value inner = o.getValue(t.fieldByName("value"));
      return inner == null ? "Optional.empty" : "Optional[" + render(inner, depth - 1) + "]";
    }
    if (name.startsWith("java.time.") || name.equals("java.math.BigDecimal") || name.equals("java.math.BigInteger")
        || name.equals("java.util.UUID") || name.equals("java.net.URI")) {
      String s = JdkValues.text(o);
      return s != null ? clip(s, MAX_STR) : "<" + simple(name) + ">";
    }
    if (depth <= 0) return "<" + simple(name) + ">";
    if (isProject.test(t)) return renderFields(o, t, depth);
    if (name.startsWith("java.util.") && implementsType(t, "java.util.Map")) return renderMap(o, depth);
    if (name.startsWith("java.util.") && implementsType(t, "java.util.Collection")) return renderCollection(o, depth);
    return "<" + simple(name) + ">";
  }

  private String renderFields(ObjectReference o, ReferenceType t, int depth) {
    List<String> parts = new ArrayList<>();
    for (Field f : t.allFields()) {
      if (f.isStatic() || f.isSynthetic()) continue;
      if (parts.size() >= MAX_ITEMS) {
        parts.add("…");
        break;
      }
      parts.add(f.name() + "=" + render(o.getValue(f), depth - 1));
    }
    return simple(t.name()) + "(" + String.join(", ", parts) + ")";
  }

  private String renderArray(ArrayReference a, int depth) {
    if (depth <= 0) return "<array[" + a.length() + "]>";
    int n = Math.min(a.length(), MAX_ITEMS);
    List<String> parts = new ArrayList<>();
    for (Value item : n == 0 ? List.<Value>of() : a.getValues(0, n)) parts.add(render(item, depth - 1));
    if (a.length() > MAX_ITEMS) parts.add("…+" + (a.length() - MAX_ITEMS));
    return "[" + String.join(", ", parts) + "]";
  }

  private String renderCollection(ObjectReference o, int depth) {
    // Collections.unmodifiableList and friends wrap another collection that may be project code.
    ObjectReference inner = wrapped(o);
    if (inner != null) return render(inner, depth);
    List<Value> items = new ArrayList<>();
    int size = JdkCollections.items(o, MAX_ITEMS, items);
    if (size < 0) return "<" + simple(o.referenceType().name()) + ">";
    List<String> parts = new ArrayList<>();
    for (Value item : items) parts.add(render(item, depth - 1));
    if (size > items.size()) parts.add("…+" + (size - items.size()));
    return "[" + String.join(", ", parts) + "]";
  }

  private String renderMap(ObjectReference o, int depth) {
    ObjectReference inner = wrapped(o);
    if (inner != null) return render(inner, depth);
    List<Value[]> entries = new ArrayList<>();
    int size = JdkCollections.entries(o, MAX_ITEMS, entries);
    if (size < 0) return "<" + simple(o.referenceType().name()) + ">";
    List<String> parts = new ArrayList<>();
    for (Value[] kv : entries) parts.add(render(kv[0], depth - 1) + "=" + render(kv[1], depth - 1));
    if (size > entries.size()) parts.add("…+" + (size - entries.size()));
    return "{" + String.join(", ", parts) + "}";
  }

  private ObjectReference wrapped(ObjectReference o) {
    if (!o.referenceType().name().startsWith("java.util.Collections$")) return null;
    Value v = JdkCollections.field(o, "c", "m", "list");
    return v instanceof ObjectReference r ? r : null;
  }

  // JDI helpers -----------------------------------------------------------------------------
  private static Value field(ObjectReference o, String... names) {
    return JdkCollections.field(o, names);
  }

  private static String str(ObjectReference o, String fieldName) {
    Value v = field(o, fieldName);
    return v instanceof StringReference s ? s.value() : null;
  }

  private static boolean isEnum(ReferenceType t) {
    if (!(t instanceof ClassType ct)) return false;
    for (ClassType s = ct.superclass(); s != null; s = s.superclass()) {
      if (s.name().equals("java.lang.Enum")) return true;
    }
    return false;
  }

  private static boolean isThrowable(ReferenceType t) {
    if (!(t instanceof ClassType ct)) return false;
    for (ClassType s = ct; s != null; s = s.superclass()) {
      if (s.name().equals("java.lang.Throwable")) return true;
    }
    return false;
  }

  private static boolean implementsType(ReferenceType t, String iface) {
    if (!(t instanceof ClassType ct)) return false;
    for (InterfaceType i : ct.allInterfaces()) if (i.name().equals(iface)) return true;
    return false;
  }

  static String simple(String binaryName) {
    String s = binaryName.substring(binaryName.lastIndexOf('.') + 1);
    return s.replace('$', '.');
  }

  static String quote(String s) {
    String body = s.length() > MAX_STR ? s.substring(0, MAX_STR - 1) + "…" : s;
    return "\"" + body.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\"";
  }

  static String clip(String s, int limit) {
    return s.length() <= limit ? s : s.substring(0, limit - 1) + "…";
  }

}
