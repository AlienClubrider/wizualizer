package wiz;

import com.sun.jdi.ArrayReference;
import com.sun.jdi.BooleanValue;
import com.sun.jdi.ClassObjectReference;
import com.sun.jdi.ClassType;
import com.sun.jdi.Field;
import com.sun.jdi.IncompatibleThreadStateException;
import com.sun.jdi.IntegerValue;
import com.sun.jdi.ObjectReference;
import com.sun.jdi.ReferenceType;
import com.sun.jdi.StackFrame;
import com.sun.jdi.StringReference;
import com.sun.jdi.ThreadReference;
import com.sun.jdi.Value;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Predicate;

/**
 * The framework hops a Spring service slice goes through, each told as one short line:
 * transactions, MVC dispatch and its answer, validation rejecting a request, aspects, @Async,
 * method security. Probes are breakpoints on a few Spring methods, set only while the test runs
 * and only on its thread. Everything is read from fields of Spring's own objects: no code in the
 * test JVM is invoked.
 */
final class Framework {
  static final String TX_MANAGER = "org.springframework.transaction.support.AbstractPlatformTransactionManager";
  static final String TX_SUPPORT = "org.springframework.transaction.interceptor.TransactionAspectSupport";
  static final String DISPATCHER = "org.springframework.web.servlet.DispatcherServlet";
  static final int MAX_NOTES = 500;

  /** kind: what the note is about (transaction, mvc, validation, aspect, async, security). */
  record Probe(String cls, String method, String event, String kind) {}

  static final List<Probe> PROBES = List.of(
      new Probe(TX_MANAGER, "startTransaction", "began", "transaction"),
      new Probe(TX_MANAGER, "handleExistingTransaction", "joined", "transaction"),
      new Probe(TX_MANAGER, "processCommit", "commit", "transaction"),
      new Probe(TX_MANAGER, "processRollback", "rollback", "transaction"),
      new Probe(DISPATCHER, "doDispatch", "dispatch", "mvc"),
      new Probe(DISPATCHER, "noHandlerFound", "no-handler", "mvc"),
      new Probe("org.springframework.web.servlet.HandlerExecutionChain", "triggerAfterCompletion", "answered", "mvc"),
      // Called only when binding or @Valid found errors in a @ModelAttribute (a form)
      new Probe("org.springframework.web.method.annotation.ModelAttributeMethodProcessor", "isBindExceptionRequired", "form-errors", "validation"),
      new Probe("org.springframework.aop.aspectj.AbstractAspectJAdvice", "invokeAdviceMethodWithGivenArgs", "advice", "aspect"),
      new Probe("org.springframework.aop.interceptor.AsyncExecutionInterceptor", "invoke", "async", "async"),
      new Probe("org.springframework.security.authorization.method.AuthorizationManagerBeforeMethodInterceptor",
          "attemptAuthorization", "authorize", "security"));

  static Set<String> probedClasses() {
    Set<String> out = new LinkedHashSet<>();
    for (Probe p : PROBES) out.add(p.cls());
    return out;
  }

  /** A short line for a framework exception, by type (or a supertype), or null if it is not one. */
  record Rejection(String kind, String label) {}

  private final Predicate<ReferenceType> isProject;

  Framework(Predicate<ReferenceType> isProject) {
    this.isProject = isProject;
  }

  /** The note for a probe hit on the (suspended) thread, or null when there is nothing worth saying. */
  String label(Probe p, ThreadReference t) throws IncompatibleThreadStateException {
    StackFrame f = t.frame(0);
    List<Value> args = f.getArgumentValues();
    return switch (p.event()) {
      case "began" -> transaction(t, "began a transaction", true);
      case "joined" -> joins(args) ? transaction(t, "joined the open transaction", false) : null;
      case "commit" -> isNew(args) ? transaction(t, "committed", true) : null;
      case "rollback" -> transaction(t, isNew(args) ? "rolled back" : "marked the open transaction for rollback", true);
      case "dispatch" -> "Spring MVC dispatched " + request(arg(args, 0));
      case "no-handler" -> "Spring MVC found no handler for " + request(arg(args, 0)) + ": 404";
      case "answered" -> answered(args);
      case "form-errors" -> arg(args, 0) instanceof ObjectReference binder && field(binder, "bindingResult") != null
          ? "@Valid found errors in the form" + errors(binder) : null;
      case "advice" -> advice(f.thisObject());
      case "async" -> "@Async: " + orSomething(invoked(arg(args, 0))) + " handed to another thread (not recorded)";
      case "authorize" -> authorize(f.thisObject(), arg(args, 0));
      default -> null;
    };
  }

  // transactions --------------------------------------------------------------------------------
  /** The @Transactional method being run, from TransactionAspectSupport.invokeWithinTransaction's frame. */
  private String transaction(ThreadReference t, String what, boolean libraryToo) throws IncompatibleThreadStateException {
    String source = null;
    boolean project = false;
    for (StackFrame f : t.frames()) {
      String type = f.location().declaringType().name();
      String name = f.location().method().name();
      if (type.equals(TX_SUPPORT) && name.equals("invokeWithinTransaction")) {
        ObjectReference m = arg(f.getArgumentValues(), 0) instanceof ObjectReference o ? o : null;
        source = "@Transactional " + orSomething(methodName(m));
        project = m != null && isProject.test(declaring(m));
        break;
      }
      if (type.equals("org.springframework.transaction.support.TransactionTemplate")) {
        source = "TransactionTemplate";
        break;
      }
    }
    if (source == null) source = "transaction";
    if (!libraryToo && !project) return null; // every repository call joins: only say so for the app's methods
    return source + ": " + what;
  }

  /** handleExistingTransaction: REQUIRED, SUPPORTS and MANDATORY join (the others start or suspend one). */
  private static boolean joins(List<Value> args) {
    if (!(arg(args, 0) instanceof ObjectReference def)) return false;
    Value v = field(def, "propagationBehavior");
    int p = v instanceof IntegerValue i ? i.value() : 0;
    return p <= 2;
  }

  private static boolean isNew(List<Value> args) {
    if (!(arg(args, 0) instanceof ObjectReference status)) return false;
    return field(status, "newTransaction") instanceof BooleanValue b && b.value();
  }

  // MVC -----------------------------------------------------------------------------------------
  private static String answered(List<Value> args) {
    String req = request(arg(args, 0));
    ObjectReference res = unwrap(arg(args, 1), "response", "status");
    String status = res != null && field(res, "status") instanceof IntegerValue i ? String.valueOf(i.value()) : "?";
    String after = arg(args, 2) instanceof ObjectReference ex ? " after " + Values.simple(ex.referenceType().name()) : "";
    return "Spring MVC answered " + req + ": " + status + after;
  }

  /** "POST /quotes" from a (possibly wrapped) servlet request: MockHttpServletRequest's fields. */
  static String request(Value v) {
    ObjectReference r = unwrap(v, "request", "requestURI");
    if (r == null) return "the request";
    String method = str(field(r, "method"));
    String uri = str(field(r, "requestURI"));
    return ((method == null ? "" : method + " ") + (uri == null ? "" : uri)).trim();
  }

  /** Follow ServletRequestWrapper's `request` (or ServletResponseWrapper's `response`) to the object holding `wanted`. */
  private static ObjectReference unwrap(Value v, String via, String wanted) {
    for (int i = 0; i < 8 && v instanceof ObjectReference o; i++) {
      if (o.referenceType().fieldByName(wanted) != null) return o;
      v = field(o, via);
    }
    return null;
  }

  // aspects, @Async, method security ------------------------------------------------------------
  private String advice(ObjectReference adviceObj) {
    if (adviceObj == null) return null;
    String type = Values.simple(adviceObj.referenceType().name());
    String annotation = switch (type) {
      case "AspectJAroundAdvice" -> "@Around";
      case "AspectJMethodBeforeAdvice" -> "@Before";
      case "AspectJAfterAdvice" -> "@After";
      case "AspectJAfterReturningAdvice" -> "@AfterReturning";
      case "AspectJAfterThrowingAdvice" -> "@AfterThrowing";
      default -> "aspect";
    };
    ObjectReference m = field(adviceObj, "aspectJAdviceMethod") instanceof ObjectReference o ? o : null;
    return annotation + " advice " + orSomething(methodName(m));
  }

  private static String authorize(ObjectReference interceptor, Value invocation) {
    String annotation = "method security";
    if (interceptor != null && field(interceptor, "authorizationManager") instanceof ObjectReference am) {
      String t = Values.simple(am.referenceType().name());
      if (t.startsWith("PreAuthorize")) annotation = "@PreAuthorize";
      else if (t.startsWith("Secured")) annotation = "@Secured";
      else if (t.startsWith("Jsr250")) annotation = "@RolesAllowed";
    }
    return annotation + ": checked access to " + orSomething(invoked(invocation));
  }

  /** The method of an AOP MethodInvocation (ReflectiveMethodInvocation.method). */
  private static String invoked(Value invocation) {
    if (!(invocation instanceof ObjectReference mi)) return null;
    return field(mi, "method") instanceof ObjectReference m ? methodName(m) : null;
  }

  // exceptions ----------------------------------------------------------------------------------
  /** What a framework exception means for the request or call, or null for any other exception. */
  static Rejection rejection(ObjectReference exc) {
    for (ReferenceType t = exc.referenceType(); t != null; t = t instanceof ClassType c ? c.superclass() : null) {
      switch (t.name()) {
        case "org.springframework.web.bind.MethodArgumentNotValidException":
          return new Rejection("validation", "@Valid rejected the request" + errors(exc));
        case "org.springframework.web.method.annotation.HandlerMethodValidationException":
          return new Rejection("validation", "@Valid rejected the request's parameters");
        case "org.springframework.validation.BindException":
          return new Rejection("validation", "@Valid rejected the submitted form" + errors(exc));
        case "jakarta.validation.ConstraintViolationException", "javax.validation.ConstraintViolationException":
          return new Rejection("validation", "@Validated rejected the call" + message(exc));
        case "org.springframework.security.access.AccessDeniedException":
          return new Rejection("security", "Spring Security denied access" + message(exc));
        case "org.springframework.security.core.AuthenticationException":
          return new Rejection("security", "Spring Security rejected the authentication" + message(exc));
        default:
      }
    }
    return null;
  }

  /** ": field: message; ..." from the BindingResult of a BindException or a DataBinder (AbstractBindingResult.errors). */
  private static String errors(ObjectReference holder) {
    List<String> out = new ArrayList<>();
    if (field(holder, "bindingResult") instanceof ObjectReference br && field(br, "errors") instanceof ObjectReference list) {
      for (Value e : items(list)) {
        if (!(e instanceof ObjectReference err)) continue;
        String name = str(field(err, "field"));
        String msg = str(field(err, "defaultMessage"));
        out.add((name == null ? "" : name + ": ") + (msg == null ? "invalid" : msg));
      }
    }
    return errorList(out);
  }

  /** ": a; b; c (+2 more)" or "". */
  static String errorList(List<String> errors) {
    if (errors.isEmpty()) return "";
    List<String> shown = errors.subList(0, Math.min(3, errors.size()));
    String more = errors.size() > shown.size() ? " (+" + (errors.size() - shown.size()) + " more)" : "";
    return ": " + Values.clip(String.join("; ", shown), 160) + more;
  }

  private static String message(ObjectReference exc) {
    String m = str(field(exc, "detailMessage"));
    return m == null || m.isBlank() ? "" : ": " + Values.clip(m, 160);
  }

  // field reads ---------------------------------------------------------------------------------
  /** java.util.ArrayList's items, read from its fields. Other lists: none. */
  private static List<Value> items(ObjectReference list) {
    if (!(field(list, "elementData") instanceof ArrayReference arr) || !(field(list, "size") instanceof IntegerValue n)) {
      return List.of();
    }
    int size = Math.min(n.value(), Math.min(arr.length(), 20));
    return size <= 0 ? List.of() : arr.getValues(0, size);
  }

  /** "UserService.signUp" from a java.lang.reflect.Method. */
  private static String methodName(ObjectReference m) {
    if (m == null) return null;
    String name = str(field(m, "name"));
    ReferenceType owner = declaring(m);
    if (name == null) return null;
    return owner == null ? name : Values.simple(owner.name()) + "." + name;
  }

  private static ReferenceType declaring(ObjectReference m) {
    return m != null && field(m, "clazz") instanceof ClassObjectReference c ? c.reflectedType() : null;
  }

  static Value field(ObjectReference o, String name) {
    if (o == null) return null;
    Field f = o.referenceType().fieldByName(name);
    return f == null ? null : o.getValue(f);
  }

  private static Value arg(List<Value> args, int i) {
    return i < args.size() ? args.get(i) : null;
  }

  private static String str(Value v) {
    return v instanceof StringReference s ? s.value() : null;
  }

  private static String orSomething(String s) {
    return s == null ? "a method" : s;
  }

  static Map<String, Object> note(int frame, int line, int after, String kind, String label) {
    Map<String, Object> n = new java.util.LinkedHashMap<>();
    n.put("frame", frame);
    n.put("line", line);
    n.put("after", after);
    n.put("kind", kind);
    n.put("label", label);
    return n;
  }
}
