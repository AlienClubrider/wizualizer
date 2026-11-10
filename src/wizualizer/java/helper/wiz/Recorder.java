package wiz;

import com.sun.jdi.AbsentInformationException;
import com.sun.jdi.Bootstrap;
import com.sun.jdi.IncompatibleThreadStateException;
import com.sun.jdi.LocalVariable;
import com.sun.jdi.Location;
import com.sun.jdi.Method;
import com.sun.jdi.ObjectReference;
import com.sun.jdi.ReferenceType;
import com.sun.jdi.StackFrame;
import com.sun.jdi.StringReference;
import com.sun.jdi.ThreadReference;
import com.sun.jdi.Value;
import com.sun.jdi.VirtualMachine;
import com.sun.jdi.connect.Connector;
import com.sun.jdi.connect.ListeningConnector;
import com.sun.jdi.event.BreakpointEvent;
import com.sun.jdi.event.ClassPrepareEvent;
import com.sun.jdi.event.Event;
import com.sun.jdi.event.EventSet;
import com.sun.jdi.event.ExceptionEvent;
import com.sun.jdi.event.MethodEntryEvent;
import com.sun.jdi.event.MethodExitEvent;
import com.sun.jdi.event.VMDeathEvent;
import com.sun.jdi.event.VMDisconnectEvent;
import com.sun.jdi.request.BreakpointRequest;
import com.sun.jdi.request.ClassPrepareRequest;
import com.sun.jdi.request.EventRequest;
import com.sun.jdi.request.EventRequestManager;
import com.sun.jdi.request.ExceptionRequest;
import com.sun.jdi.request.MethodEntryRequest;
import com.sun.jdi.request.MethodExitRequest;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Records one test over JDWP with JDI. Listens on a local port; the test JVM (started by Maven
 * or Gradle with -agentlib:jdwp=...,server=n) connects to it, suspended.
 *
 * <p>Line breakpoints on every line of project methods give `line` steps. Method entry/exit and
 * exception requests, limited to the test's thread and to project packages, are switched on only
 * while the test method runs. Output: the `steps / frames / functions` part of a trace.
 */
final class Recorder {
  static final int MAX_LOCALS = 40;
  static final int MAX_CALLS = 3000;
  static final String MOCK_ADVICE = "org.mockito.internal.creation.bytebuddy.MockMethodAdvice";

  // config
  private final Path root;
  private final List<Path> sourceRoots;
  private final List<String> packages;
  private final String testClass, testMethod;
  private final int maxSteps;
  private final Path out;

  // state
  private VirtualMachine vm;
  private EventRequestManager erm;
  private final List<Map<String, Object>> steps = new ArrayList<>();
  private final Map<String, Object> frames = new LinkedHashMap<>();
  private final Map<String, Object> functions = new LinkedHashMap<>();
  private final Deque<Frame> stack = new ArrayDeque<>();
  private final List<EventRequest> windowRequests = new ArrayList<>();
  private final Map<ReferenceType, Optional<ClassInfo>> classes = new HashMap<>();
  private final Map<Path, Outline> outlines = new HashMap<>();
  private final Map<Method, Optional<Outline.Method>> mapped = new HashMap<>();
  private final Map<Long, String> actors = new HashMap<>();
  private final Map<String, Integer> actorCounts = new HashMap<>();
  private final Values values;
  private ThreadReference thread;
  private boolean windowOpen, windowDone, truncated;
  private String[] pending; // exception unwinding: {type, message}
  private String outcome = "not run", message = "";
  private int nextFrame = 1;
  // Calls from project code that run no project code (mocks, the JDK, libraries): one at a time.
  private final List<Map<String, Object>> calls = new ArrayList<>();
  private MethodEntryRequest anyEntry; // every method entry on the test's thread; on only while armed
  private MethodEntryRequest adviceEntry; // Mockito's inline-mock advice: where a redefined method's arguments are
  private boolean armed;
  private Outside outside;

  private static final class Outside {
    Frame caller;
    int depth; // stack depth of the called method
    boolean byMethod; // match the exit by method alone (inline mocks report other depths)
    Method method;
    Map<String, Object> call; // null: hidden (class loading, invokedynamic plumbing)
    MethodExitRequest exit;
  }

  private record ClassInfo(String file, Outline outline) {}

  private static final class Frame {
    int id, depth, line;
    String fn;
    Outline.Method src;
    ClassInfo info;
  }

  Recorder(Path root, List<Path> sourceRoots, List<String> packages, String test, int maxSteps, Path out) {
    this.root = root;
    this.sourceRoots = sourceRoots;
    this.packages = minimalPrefixes(packages);
    int hash = test.indexOf('#');
    this.testClass = test.substring(0, hash);
    this.testMethod = test.substring(hash + 1);
    this.maxSteps = maxSteps;
    this.out = out;
    this.values = new Values(t -> classInfo(t) != null);
  }

  // connection --------------------------------------------------------------------------------
  void run(int timeoutMs) throws Exception {
    vm = accept(timeoutMs);
    erm = vm.eventRequestManager();
    for (String pkg : packages) {
      ClassPrepareRequest r = erm.createClassPrepareRequest();
      r.addClassFilter(pkg + ".*");
      r.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
      r.enable();
    }
    loop();
  }

  private void loop() throws IOException {
    vm.resume();
    while (true) {
      EventSet set;
      try {
        set = vm.eventQueue().remove();
      } catch (InterruptedException e) {
        break;
      }
      List<Event> events = new ArrayList<>();
      set.forEach(events::add);
      events.sort(Comparator.comparingInt(this::order));
      boolean dead = false;
      try {
        disarm(); // values are rendered by invoking JDK methods: no entry events while that runs
      } catch (com.sun.jdi.VMDisconnectedException gone) {
        break;
      }
      for (Event e : events) {
        try {
          if (e instanceof ClassPrepareEvent cp) onPrepare(cp.referenceType());
          else if (e instanceof MethodEntryEvent me && me.request() == anyEntry) onOutsideEntry(me);
          else if (e instanceof MethodEntryEvent me && me.request() == adviceEntry) onMockAdvice(me);
          else if (e instanceof MethodExitEvent mx && outside != null && mx.request() == outside.exit) onOutsideExit(mx);
          else if (e instanceof MethodEntryEvent me) onEntry(me);
          else if (e instanceof BreakpointEvent bp) onLine(bp);
          else if (e instanceof ExceptionEvent ex) onException(ex);
          else if (e instanceof MethodExitEvent mx) onExit(mx);
          else if (e instanceof VMDeathEvent || e instanceof VMDisconnectEvent) dead = true;
        } catch (com.sun.jdi.VMDisconnectedException gone) {
          dead = true;
        } catch (RuntimeException | InternalError | IncompatibleThreadStateException err) {
          System.err.println("wiz: skipped an event: " + err + " at " + java.util.Arrays.toString(java.util.Arrays.copyOf(err.getStackTrace(), 6)));
        }
      }
      if (dead) break;
      try {
        arm();
        set.resume();
      } catch (com.sun.jdi.VMDisconnectedException gone) {
        break;
      }
    }
    if (!windowDone) {
      if (windowOpen) {
        outcome = "unknown";
        message = "the test JVM exited while the test was running";
      }
      write();
    }
  }

  private int order(Event e) {
    if (e instanceof ClassPrepareEvent) return 0;
    if (e instanceof MethodEntryEvent) return e.request() == anyEntry ? 2 : 1; // after a project frame opens
    if (e instanceof BreakpointEvent) return 3;
    if (e instanceof ExceptionEvent) return 4;
    if (e instanceof MethodExitEvent) return 5;
    return 6;
  }

  // project code ------------------------------------------------------------------------------
  /** Source file and outline for a project class, or null for JDK, library, generated classes. */
  private ClassInfo classInfo(ReferenceType t) {
    return classes.computeIfAbsent(t, this::lookup).orElse(null);
  }

  private Optional<ClassInfo> lookup(ReferenceType t) {
    String name = t.name();
    if (name.contains("$$") || name.contains("$MockitoMock$") || name.contains("$ByteBuddy$")
        || name.contains("$HibernateProxy$")) return Optional.empty();
    if (packages.stream().noneMatch(p -> name.startsWith(p + "."))) return Optional.empty();
    String sourceName;
    try {
      sourceName = t.sourceName();
    } catch (AbsentInformationException e) {
      return Optional.empty();
    }
    int dot = name.lastIndexOf('.');
    String dir = dot < 0 ? "" : name.substring(0, dot).replace('.', '/') + "/";
    for (Path sr : sourceRoots) {
      Path p = sr.resolve(dir + sourceName);
      if (!Files.isRegularFile(p)) continue;
      Outline o = outlines.computeIfAbsent(p, Recorder::parse);
      if (o == null) return Optional.empty();
      String rel = root.relativize(p).toString().replace('\\', '/');
      return Optional.of(new ClassInfo(rel, o));
    }
    return Optional.empty();
  }

  private static Outline parse(Path p) {
    try {
      return Outline.parse(Files.readString(p, StandardCharsets.UTF_8));
    } catch (IOException | RuntimeException e) {
      return null;
    }
  }

  /** The source method a JDI method belongs to (a lambda maps to its enclosing method). */
  private Outline.Method sourceMethod(Method m) {
    return mapped.computeIfAbsent(m, this::mapMethod).orElse(null);
  }

  private Optional<Outline.Method> mapMethod(Method m) {
    ClassInfo info = classInfo(m.declaringType());
    if (info == null || m.isAbstract() || m.isNative() || m.name().equals("<clinit>")) return Optional.empty();
    boolean lambda = m.name().startsWith("lambda$");
    if (!lambda && (m.isSynthetic() || m.isBridge())) return Optional.empty();
    List<Location> locs;
    try {
      locs = m.allLineLocations();
    } catch (AbsentInformationException e) {
      return Optional.empty();
    }
    for (Location l : locs) {
      // Generated code (Lombok, records, default constructors) has no matching source method.
      Outline.Method sm = info.outline.methodAt(l.lineNumber(), lambda ? null : m.name());
      if (sm != null) return Optional.of(sm);
    }
    return Optional.empty();
  }

  /** The line of a location; `fallback` for methods redefined at runtime (Mockito inline mocks). */
  private static int lineOf(Location l, int fallback) {
    try {
      return l.lineNumber();
    } catch (InternalError e) {
      return fallback;
    }
  }

  private static boolean isLambda(Method m) {
    return m.name().startsWith("lambda$");
  }

  private void onPrepare(ReferenceType t) {
    if (classInfo(t) == null) return;
    for (Method m : t.methods()) {
      if (sourceMethod(m) == null) continue;
      try {
        for (Location l : m.allLineLocations()) {
          BreakpointRequest bp = erm.createBreakpointRequest(l);
          bp.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
          bp.enable();
        }
      } catch (AbsentInformationException ignored) {
        // no line numbers: compiled without -g
      }
    }
  }

  // window: the test method's run ---------------------------------------------------------------
  private boolean isTestMethod(Method m) {
    return m.name().equals(testMethod) && m.declaringType().name().equals(testClass);
  }

  private void openWindow(ThreadReference t) throws IncompatibleThreadStateException {
    windowOpen = true;
    thread = t;
    for (String pkg : packages) {
      MethodEntryRequest en = erm.createMethodEntryRequest();
      en.addClassFilter(pkg + ".*");
      en.addThreadFilter(t);
      MethodExitRequest ex = erm.createMethodExitRequest();
      ex.addClassFilter(pkg + ".*");
      ex.addThreadFilter(t);
      windowRequests.add(en);
      windowRequests.add(ex);
    }
    ExceptionRequest exc = erm.createExceptionRequest(null, true, true);
    exc.addThreadFilter(t);
    windowRequests.add(exc);
    for (EventRequest r : windowRequests) {
      r.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
      r.enable();
    }
    anyEntry = erm.createMethodEntryRequest();
    anyEntry.addThreadFilter(t);
    anyEntry.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
    windowRequests.add(anyEntry);
    adviceEntry = erm.createMethodEntryRequest();
    adviceEntry.addClassFilter(MOCK_ADVICE);
    adviceEntry.addThreadFilter(t);
    adviceEntry.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
    windowRequests.add(adviceEntry);
  }

  private void closeWindow() {
    if (windowDone) return;
    windowOpen = false;
    windowDone = true;
    armed = false;
    try {
      if (outside != null && outside.exit != null) erm.deleteEventRequest(outside.exit);
      outside = null;
      erm.deleteEventRequests(windowRequests);
      erm.deleteAllBreakpoints();
      erm.deleteEventRequests(erm.classPrepareRequests());
    } catch (RuntimeException ignored) {
      // VM already gone
    }
    try {
      write();
    } catch (IOException e) {
      System.err.println("wiz: could not write the recording: " + e);
    }
  }

  // events ------------------------------------------------------------------------------------
  private void onLine(BreakpointEvent e) throws IncompatibleThreadStateException {
    Location loc = e.location();
    Method m = loc.method();
    if (!windowOpen) {
      if (windowDone || !isTestMethod(m)) return;
      openWindow(e.thread());
    }
    if (!e.thread().equals(thread)) return;
    Outline.Method src = sourceMethod(m);
    if (src == null) return;
    int depth = thread.frameCount();
    if (!isLambda(m)) outsideEnded(depth);
    unwind(depth, false);
    Frame f;
    Map<String, Value> raw;
    if (isLambda(m)) {
      // Lambdas are folded into the method that contains them, like Python comprehensions.
      f = findOnStack(src);
      if (f == null) return;
      raw = snapshot(thread.frame(depth - f.depth));
      raw.putAll(snapshot(thread.frame(0)));
    } else {
      f = stack.peek();
      if (f == null || f.depth < depth || f.src != src) {
        if (f != null && f.depth == depth) pop(f, null); // a frame we lost track of
        if (!enter(m, src, depth)) return;
        f = stack.peek();
      }
      raw = snapshot(thread.frame(0));
    }
    pending = null;
    int line = loc.lineNumber();
    if (line == src.end() && src.end() != src.open() && !isLambda(m)) {
      f.line = line; // the closing brace: the implicit `return` of a void method
      return;
    }
    Map<String, Object> last = steps.isEmpty() ? null : steps.get(steps.size() - 1);
    if (last != null && "line".equals(last.get("kind")) && last.get("frame").equals(f.id) && last.get("line").equals(line)) {
      return; // several line-table entries for one line
    }
    f.line = line;
    Map<String, Object> s = step("line", f, line);
    s.put("locals", render(raw));
    emit(s);
  }

  private void onEntry(MethodEntryEvent e) throws IncompatibleThreadStateException {
    if (!windowOpen || !e.thread().equals(thread) || isLambda(e.method())) return;
    Outline.Method src = sourceMethod(e.method());
    if (src == null) return;
    int depth = thread.frameCount();
    if (outside != null && depth > outside.depth && outside.call != null
        && e.method().name().equals(outside.method.name())) {
      outside.call.put("proxied", true); // a proxy (Spring, a spy) passing the call on to real code
    }
    outsideEnded(depth - 1);
    unwind(depth, true);
    enter(e.method(), src, depth);
  }

  private void onExit(MethodExitEvent e) throws IncompatibleThreadStateException {
    if (!windowOpen || !e.thread().equals(thread) || isLambda(e.method())) return;
    Outline.Method src = sourceMethod(e.method());
    if (src == null) return;
    if (outside == null || !e.method().equals(outside.method)) outsideEnded(thread.frameCount());
    // Match by method, not stack depth: instrumented methods (Mockito inline mocks) can report
    // a different depth on entry and exit. Anything above the frame exited by an exception.
    Frame f = findOnStack(src);
    if (f == null) return;
    while (stack.peek() != f) pop(stack.peek(), pending != null ? pending : new String[] {"Throwable", ""});
    f.line = lineOf(e.location(), f.line);
    if (pending != null) {
      pop(f, pending);
      return;
    }
    Value v;
    if (e.method().isConstructor()) v = thread.frame(0).thisObject();
    else if (e.method().returnTypeName().equals("void")) v = null;
    else v = e.returnValue();
    boolean isVoid = v == null && e.method().returnTypeName().equals("void");
    values.setThread(thread);
    Map<String, Object> s = step("return", f, f.line);
    s.put("value", isVoid ? "void" : values.render(v));
    popFrame(f);
    emit(s);
    if (stack.isEmpty()) finish(null);
  }

  private void onException(ExceptionEvent e) throws IncompatibleThreadStateException {
    if (!windowOpen || !e.thread().equals(thread)) return;
    Location thrown = e.location(), caught = e.catchLocation();
    boolean thrownInProject = sourceMethod(thrown.method()) != null && !isLambda(thrown.method());
    boolean caughtInProject = caught != null && classInfo(caught.declaringType()) != null;
    if (!thrownInProject && caught != null && !caughtInProject) return; // library-internal
    ObjectReference exc = e.exception();
    String type = Values.simple(exc.referenceType().name());
    Value msg = exc.getValue(exc.referenceType().fieldByName("detailMessage"));
    String text = msg instanceof StringReference sr ? Values.clip(sr.value(), 200) : "";
    pending = new String[] {type, text};
    if (thrownInProject) unwind(thread.frameCount(), false);
    Frame f = stack.peek();
    if (f == null) return;
    Map<String, Object> s = step("exception", f, thrownInProject ? lineOf(thrown, f.line) : f.line);
    s.put("type", type);
    s.put("message", text);
    emit(s);
  }

  // calls that run no project code -------------------------------------------------------------
  /** Watch for the next call the top project frame makes, unless one is in progress. */
  private void arm() {
    boolean want = windowOpen && outside == null && !stack.isEmpty() && calls.size() < MAX_CALLS;
    if (want != armed && anyEntry != null) {
      if (want) anyEntry.enable();
      else anyEntry.disable();
      armed = want;
    }
  }

  private void disarm() {
    if (armed) {
      anyEntry.disable();
      armed = false;
    }
  }

  private static boolean plumbing(Method m) {
    String t = m.declaringType().name();
    boolean superCtor = m.isConstructor() && (t.equals("java.lang.Object") || t.equals("java.lang.Record") || t.equals("java.lang.Enum"));
    return m.isStaticInitializer() || superCtor || t.startsWith("java.lang.invoke.")
        || t.startsWith("jdk.internal.") || t.startsWith("sun.") || t.contains("$$Lambda")
        || m.name().equals("loadClass");
  }

  /**
   * A method entered below the top project frame while armed. The direct callee, if it is not
   * project code (a mock, the JDK, a library), or a project method that a Mockito inline mock
   * stood in for, is recorded as a call; anything else is skipped until it returns.
   */
  private void onOutsideEntry(MethodEntryEvent e) throws IncompatibleThreadStateException {
    if (!windowOpen || !e.thread().equals(thread) || outside != null) return;
    Method m = e.method();
    Frame top = stack.peek();
    if (top == null) return;
    int depth = thread.frameCount();
    if (depth <= top.depth) return; // the project frame opened for this very call
    boolean project = sourceMethod(m) != null;
    // An inline mock's method reports one extra frame on entry (Mockito's dispatcher).
    boolean inlineMock = project && depth <= top.depth + 2;
    Outside o = new Outside();
    o.caller = top;
    o.depth = depth;
    o.method = m;
    o.byMethod = inlineMock;
    if (inlineMock || (depth == top.depth + 1 && !project && !plumbing(m))) o.call = describe(m, top, inlineMock);
    MethodExitRequest x = erm.createMethodExitRequest();
    x.addClassFilter(m.declaringType());
    x.addThreadFilter(thread);
    x.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
    x.enable();
    o.exit = x;
    outside = o;
    if (inlineMock && o.call != null && ((Map<?, ?>) o.call.get("args")).isEmpty()) adviceEntry.enable();
  }

  /** Mockito's inline advice handles a call: its last argument holds the mocked method's arguments. */
  private void onMockAdvice(MethodEntryEvent e) throws IncompatibleThreadStateException {
    if (!e.method().name().equals("handle")) return;
    adviceEntry.disable();
    if (outside == null || outside.call == null) return;
    List<Value> av;
    try {
      av = thread.frame(0).getArgumentValues();
    } catch (com.sun.jdi.InternalException ignored) {
      return;
    }
    if (av.isEmpty() || !(av.get(av.size() - 1) instanceof com.sun.jdi.ArrayReference arr)) return;
    Outline.Method src = sourceMethod(outside.method);
    List<Value> items = arr.getValues();
    values.setThread(thread);
    Map<String, Object> args = new LinkedHashMap<>();
    for (int i = 0; i < items.size(); i++) {
      String name = src != null && src.params().size() == items.size() ? src.params().get(i) : "arg" + i;
      args.put(name, values.render(items.get(i)));
    }
    outside.call.put("args", args);
  }

  private Map<String, Object> describe(Method m, Frame caller, boolean inlineMock) throws IncompatibleThreadStateException {
    Map<String, Object> c = new LinkedHashMap<>();
    ObjectReference self = null;
    List<Value> argValues = List.of();
    int line = caller.line;
    int n = thread.frameCount();
    for (int i = 0; i < Math.min(4, n); i++) {
      StackFrame jf = thread.frame(i);
      if (!jf.location().method().equals(m)) continue;
      try {
        self = jf.thisObject();
        argValues = jf.getArgumentValues();
      } catch (com.sun.jdi.InternalException | com.sun.jdi.InvalidStackFrameException ignored) {
        // redefined (inline mocks) or native: no arguments to show
      }
      if (i + 1 < n) line = lineOf(thread.frame(i + 1).location(), caller.line);
      break;
    }
    String type = m.declaringType().name();
    boolean mock = inlineMock;
    if (self != null) {
      String runtime = self.referenceType().name();
      int k = runtime.indexOf("$MockitoMock$");
      if (k > 0) {
        type = runtime.substring(0, k);
        mock = true;
      }
    }
    c.put("frame", caller.id);
    c.put("line", line);
    c.put("after", steps.size() - 1);
    c.put("name", m.isConstructor() ? "new " + Values.simple(type) : m.name());
    c.put("type", type);
    c.put("arity", m.argumentTypeNames().size());
    List<String> names = new ArrayList<>();
    try {
      for (LocalVariable v : m.arguments()) names.add(v.name());
    } catch (AbsentInformationException ignored) {
      // no debug info (the JDK, generated mocks): the source's names if it is project code, else arg0, arg1, ...
      Outline.Method src = sourceMethod(m);
      if (src != null && src.params().size() == argValues.size()) names.addAll(src.params());
    }
    values.setThread(thread);
    Map<String, Object> args = new LinkedHashMap<>();
    for (int i = 0; i < argValues.size(); i++) {
      args.put(i < names.size() ? names.get(i) : "arg" + i, values.render(argValues.get(i)));
    }
    c.put("args", args);
    if (mock) c.put("mock", true);
    return c;
  }

  private void onOutsideExit(MethodExitEvent e) throws IncompatibleThreadStateException {
    if (!e.thread().equals(thread) || !e.method().equals(outside.method)) return;
    if (!outside.byMethod && thread.frameCount() != outside.depth) return;
    Outside o = outside;
    erm.deleteEventRequest(o.exit);
    o.exit = null;
    if (o.call != null) {
      Value v;
      if (o.method.isConstructor()) v = thread.frame(0).thisObject();
      else v = o.method.returnTypeName().equals("void") ? null : e.returnValue();
      values.setThread(thread);
      boolean isVoid = v == null && o.method.returnTypeName().equals("void");
      o.call.put("value", isVoid ? "void" : values.render(v));
    }
    finishOutside();
  }

  /** The call in progress is over if the stack is back at its caller's depth (it threw, or no exit came). */
  private void outsideEnded(int depth) {
    if (outside == null || depth >= outside.depth) return;
    if (outside.call != null && !outside.call.containsKey("value")) {
      outside.call.put("raised", true);
      outside.call.put("error", pending == null ? "Throwable" : pending[0] + (pending[1].isEmpty() ? "" : ": " + pending[1]));
    }
    if (outside.exit != null) {
      try {
        erm.deleteEventRequest(outside.exit);
      } catch (RuntimeException ignored) {
        // VM gone
      }
    }
    finishOutside();
  }

  private void finishOutside() {
    if (adviceEntry != null) adviceEntry.disable();
    Map<String, Object> c = outside.call;
    outside = null;
    if (c != null && !c.containsKey("proxied") && calls.size() < MAX_CALLS) calls.add(c);
  }

  // frames ------------------------------------------------------------------------------------
  private boolean enter(Method m, Outline.Method src, int depth) throws IncompatibleThreadStateException {
    // A Mockito inline mock runs Mockito's dispatcher from advice woven into the mocked method.
    // Mocks are skipped like library code (interface mocks are generated classes anyway).
    int k = 0;
    for (int i = 0; i < Math.min(6, thread.frameCount()); i++) {
      Method at = thread.frame(i).location().method();
      if (at.equals(m)) {
        k = i;
        break;
      }
      if (at.declaringType().name().startsWith("org.mockito.")) return false;
    }
    ClassInfo info = classInfo(m.declaringType());
    String fn = info.file + ":" + src.line();
    if (!functions.containsKey(fn)) {
      Map<String, Object> d = new LinkedHashMap<>();
      String simple = src.cls().contains(".") ? src.cls().substring(src.cls().lastIndexOf('.') + 1) : src.cls();
      String name = src.ctor() ? "new " + simple : src.name();
      d.put("name", name);
      d.put("qualname", src.ctor() ? "new " + src.cls() : src.cls() + "." + src.name());
      d.put("file", info.file);
      d.put("line", src.line());
      functions.put(fn, d);
    }
    StackFrame jf = thread.frame(k);
    depth -= k;
    ObjectReference self = null;
    Map<String, Value> args = new LinkedHashMap<>();
    try {
      self = jf.thisObject();
      List<Value> argValues = jf.getArgumentValues();
      List<String> names = argNames(m, src, argValues.size());
      for (int i = 0; i < argValues.size(); i++) args.put(names.get(i), argValues.get(i));
    } catch (com.sun.jdi.InternalException e) {
      // a method redefined at runtime (Mockito) may not expose `this` or its argument slots
    }

    Frame f = new Frame();
    f.id = nextFrame++;
    f.depth = depth;
    f.fn = fn;
    f.src = src;
    f.info = info;
    f.line = src.line();
    Map<String, Object> fr = new LinkedHashMap<>();
    fr.put("fn", fn);
    fr.put("parent", stack.isEmpty() ? null : stack.peek().id);
    fr.put("actor", actor(self, m.declaringType()));
    frames.put(String.valueOf(f.id), fr);
    stack.push(f);
    Map<String, Object> s = step("call", f, src.line());
    s.put("args", render(args));
    return emit(s);
  }

  private static List<String> argNames(Method m, Outline.Method src, int n) {
    List<String> out = new ArrayList<>();
    try {
      for (LocalVariable v : m.arguments()) out.add(v.name());
    } catch (AbsentInformationException ignored) {
      // compiled without -g, or instrumented (Mockito): take the names from the source
      if (src.params().size() == n) out.addAll(src.params());
    }
    for (int i = out.size(); i < n; i++) out.add("arg" + i);
    return out;
  }

  private Frame findOnStack(Outline.Method src) {
    for (Frame f : stack) if (f.src == src) return f;
    return null;
  }

  /** Close frames deeper than `depth` (or at it, if inclusive): they exited by an exception. */
  private void unwind(int depth, boolean inclusive) {
    while (!stack.isEmpty() && windowOpen) {
      Frame top = stack.peek();
      if (top.depth > depth || (inclusive && top.depth == depth)) {
        pop(top, pending != null ? pending : new String[] {"Throwable", ""});
      } else {
        break;
      }
    }
  }

  private void pop(Frame f, String[] raised) {
    Map<String, Object> s = step("return", f, f.line);
    if (raised != null) {
      s.put("raised", true);
      s.put("type", raised[0]);
      s.put("message", raised[1]);
    } else {
      s.put("value", "?");
    }
    popFrame(f);
    emit(s);
    if (stack.isEmpty()) finish(raised);
  }

  private void popFrame(Frame f) {
    while (!stack.isEmpty()) {
      if (stack.pop() == f) break;
    }
  }

  private void finish(String[] raised) {
    if (raised != null) {
      outcome = "failed";
      message = raised[0] + (raised[1].isEmpty() ? "" : ": " + raised[1]);
    } else {
      outcome = "passed";
    }
    closeWindow();
  }

  private String actor(ObjectReference self, ReferenceType type) {
    if (self == null) return Values.simple(type.name());
    return actors.computeIfAbsent(self.uniqueID(), id -> {
      String cls = Values.simple(self.referenceType().name());
      int n = actorCounts.merge(cls, 1, Integer::sum);
      return cls + "#" + n;
    });
  }

  // values ------------------------------------------------------------------------------------
  /** Locals then fields of `this`, read before anything is rendered (rendering may resume the thread). */
  private Map<String, Value> snapshot(StackFrame jf) {
    Map<String, Value> raw = new LinkedHashMap<>();
    try {
      for (Map.Entry<LocalVariable, Value> e : jf.getValues(jf.visibleVariables()).entrySet()) {
        raw.put(e.getKey().name(), e.getValue());
      }
    } catch (AbsentInformationException e) {
      List<Value> args = jf.getArgumentValues();
      for (int i = 0; i < args.size(); i++) raw.put("arg" + i, args.get(i));
    }
    ObjectReference self = jf.thisObject();
    if (self != null) {
      for (com.sun.jdi.Field fld : self.referenceType().allFields()) {
        if (fld.isStatic() || fld.isSynthetic()) continue;
        raw.put("this." + fld.name(), self.getValue(fld));
      }
    }
    return raw;
  }

  private Map<String, Object> render(Map<String, Value> raw) {
    values.setThread(thread);
    Map<String, Object> out = new LinkedHashMap<>();
    int i = 0;
    for (Map.Entry<String, Value> e : raw.entrySet()) {
      if (i++ >= MAX_LOCALS) {
        out.put("…", "+" + (raw.size() - MAX_LOCALS) + " more");
        break;
      }
      out.put(e.getKey(), values.render(e.getValue()));
    }
    return out;
  }

  // output ------------------------------------------------------------------------------------
  private Map<String, Object> step(String kind, Frame f, int line) {
    Map<String, Object> s = new LinkedHashMap<>();
    s.put("kind", kind);
    s.put("frame", f.id);
    s.put("line", line);
    return s;
  }

  private boolean emit(Map<String, Object> s) {
    if (truncated || windowDone) return false;
    if (steps.size() >= maxSteps) {
      truncated = true;
      outcome = "unknown";
      message = "recording stopped at " + maxSteps + " steps";
      closeWindow();
      return false;
    }
    steps.add(s);
    return true;
  }

  private void write() throws IOException {
    Map<String, Object> test = new LinkedHashMap<>();
    test.put("test", testClass + "#" + testMethod);
    test.put("outcome", outcome);
    test.put("message", message);
    Map<String, Object> rec = new LinkedHashMap<>();
    rec.put("steps", steps);
    rec.put("frames", frames);
    rec.put("functions", functions);
    rec.put("calls", calls);
    rec.put("truncated", truncated);
    rec.put("test", test);
    Files.writeString(out, Json.write(rec), StandardCharsets.UTF_8);
  }

  /**
   * Listen on a free 127.0.0.1 port, print "LISTENING host:port", accept the test JVM (timeout 0:
   * wait for as long as the build takes; wiz stops this process if the build ends first), then
   * print "CONNECTED".
   */
  static VirtualMachine accept(int timeoutMs) throws Exception {
    ListeningConnector lc =
        Bootstrap.virtualMachineManager().listeningConnectors().stream()
            .filter(c -> c.name().equals("com.sun.jdi.SocketListen"))
            .findFirst()
            .orElseThrow();
    Map<String, Connector.Argument> args = lc.defaultArguments();
    args.get("port").setValue("0");
    if (args.containsKey("localAddress")) args.get("localAddress").setValue("127.0.0.1");
    if (args.containsKey("timeout")) args.get("timeout").setValue(String.valueOf(timeoutMs));
    String address = lc.startListening(args);
    System.out.println("LISTENING " + address);
    System.out.flush();
    try {
      VirtualMachine vm = lc.accept(args);
      System.out.println("CONNECTED");
      System.out.flush();
      return vm;
    } finally {
      lc.stopListening(args);
    }
  }

  static List<String> minimalPrefixes(List<String> pkgs) {
    List<String> sorted = new ArrayList<>(pkgs.stream().filter(p -> !p.isEmpty()).distinct().sorted().toList());
    List<String> out = new ArrayList<>();
    for (String p : sorted) {
      if (out.stream().noneMatch(q -> p.equals(q) || p.startsWith(q + "."))) out.add(p);
    }
    return out;
  }
}
