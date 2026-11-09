package wiz;

import com.sun.jdi.ArrayReference;
import com.sun.jdi.CharValue;
import com.sun.jdi.ClassType;
import com.sun.jdi.Field;
import com.sun.jdi.InterfaceType;
import com.sun.jdi.Method;
import com.sun.jdi.ObjectReference;
import com.sun.jdi.PrimitiveValue;
import com.sun.jdi.ReferenceType;
import com.sun.jdi.StringReference;
import com.sun.jdi.ThreadReference;
import com.sun.jdi.Value;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.function.Predicate;

/**
 * Bounded, crash-proof rendering of JDI values as short strings (depth 3, 8 items, 80 chars per
 * string, 240 in total), the same limits as the Python recorder.
 *
 * <p>Project objects are shown by their fields. JDK collections are read with toArray() and a
 * few JDK value types (BigDecimal, java.time, UUID) with toString(); nothing else is invoked, so
 * no project code runs while recording. Other library objects show as their class name.
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
  private ThreadReference thread; // suspended by the current event; null = never invoke

  Values(Predicate<ReferenceType> isProject) {
    this.isProject = isProject;
  }

  void setThread(ThreadReference thread) {
    this.thread = thread;
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
      String s = invokeToString(o);
      return s != null ? s : "<" + simple(name) + ">";
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
    Value arr = invoke(o, "toArray", "()[Ljava/lang/Object;");
    if (!(arr instanceof ArrayReference a)) return "<" + simple(o.referenceType().name()) + ">";
    return renderArray(a, depth);
  }

  private String renderMap(ObjectReference o, int depth) {
    ObjectReference inner = wrapped(o);
    if (inner != null) return render(inner, depth);
    Value entries = invoke(o, "entrySet", "()Ljava/util/Set;");
    Value arr = entries instanceof ObjectReference e ? invoke(e, "toArray", "()[Ljava/lang/Object;") : null;
    if (!(arr instanceof ArrayReference a)) return "<" + simple(o.referenceType().name()) + ">";
    int n = Math.min(a.length(), MAX_ITEMS);
    List<String> parts = new ArrayList<>();
    for (Value item : n == 0 ? List.<Value>of() : a.getValues(0, n)) {
      ObjectReference entry = (ObjectReference) item;
      parts.add(render(field(entry, "key"), depth - 1) + "=" + render(field(entry, "value", "val"), depth - 1));
    }
    if (a.length() > MAX_ITEMS) parts.add("…+" + (a.length() - MAX_ITEMS));
    return "{" + String.join(", ", parts) + "}";
  }

  private ObjectReference wrapped(ObjectReference o) {
    if (!o.referenceType().name().startsWith("java.util.Collections$")) return null;
    Value v = field(o, "c", "m", "list");
    return v instanceof ObjectReference r ? r : null;
  }

  // JDI helpers -----------------------------------------------------------------------------
  private static Value field(ObjectReference o, String... names) {
    for (String n : names) {
      Field f = o.referenceType().fieldByName(n);
      if (f != null) return o.getValue(f);
    }
    return null;
  }

  private static String str(ObjectReference o, String fieldName) {
    Value v = field(o, fieldName);
    return v instanceof StringReference s ? s.value() : null;
  }

  private String invokeToString(ObjectReference o) {
    Value v = invoke(o, "toString", "()Ljava/lang/String;");
    return v instanceof StringReference s ? clip(s.value(), MAX_STR) : null;
  }

  /** Invoke a no-argument JDK method on the suspended thread. Never invoked on project types. */
  private Value invoke(ObjectReference o, String name, String signature) {
    if (thread == null) return null;
    try {
      ReferenceType t = o.referenceType();
      if (!(t instanceof ClassType ct)) return null;
      Method m = ct.concreteMethodByName(name, signature);
      if (m == null || !m.declaringType().name().startsWith("java.")) return null;
      return o.invokeMethod(thread, m, List.of(), ObjectReference.INVOKE_SINGLE_THREADED);
    } catch (Exception e) {
      return null;
    }
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
