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
 * <p>Speed comes from watching as little as possible. Before the test method runs (while a Spring
 * context starts, say) the only breakpoints are on the test method. While it runs, every line of
 * the project's methods has a breakpoint on the test's thread (`line` steps; a method's first
 * line opens its frame). Method entry/exit events put the whole thread in interpreter mode and
 * report every call, so they are on only while project code itself runs: to see the calls it
 * makes and its own return. A call into a library is followed to its return by watching that
 * class's exits if it is cheap (the JDK, mocks, test libraries); a framework call (MockMvc, a
 * proxy, a repository) runs unwatched until it returns (a breakpoint right after the call) or
 * calls back into project code. A few Spring methods are probed for the framework's own hops
 * (see Framework). Output: the `steps / frames / functions / calls / framework` part of a trace.
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
  private final java.util.Set<Method> withBreakpoints = new java.util.HashSet<>();
  private final Map<ReferenceType, Optional<ClassInfo>> classes = new HashMap<>();
  private final Map<Path, Outline> outlines = new HashMap<>();
  private final Map<Method, Optional<Outline.Method>> mapped = new HashMap<>();
  private final Map<Method, byte[]> bytecodes = new HashMap<>();
  private final Map<Long, String> actors = new HashMap<>();
  private final Map<String, Integer> actorCounts = new HashMap<>();
  private final Values values;
  private ThreadReference thread;
  private boolean windowOpen, windowDone, truncated;
  private String[] pending; // exception unwinding: {type, message}
  private String outcome = "not run", message = "";
  private int nextFrame = 1;
  // Calls from project code that run no project code (mocks, the JDK, libraries): one at a time per
  // project frame. A library that calls back into project code (MockMvc into a controller) has its
  // call still open under the callback's frames.
  private final List<Map<String, Object>> calls = new ArrayList<>();
  private final Deque<Outside> outsides = new ArrayDeque<>();
  // On only while project code runs (see the class comment)
  private MethodEntryRequest anyEntry; // every method entry on the test's thread: the calls project code makes
  private final List<MethodExitRequest> frameExits = new ArrayList<>(); // project methods' exits
  private boolean watching;
  private final java.util.Set<EventRequest> adviceBreaks = new java.util.HashSet<>(); // Mockito's inline-mock handler
  // Framework hops (Framework.PROBES) and what they said
  private final Framework spring;
  private final Map<EventRequest, Framework.Probe> probes = new HashMap<>();
  private final List<Map<String, Object>> framework = new ArrayList<>();
  private final java.util.Set<Long> rejected = new java.util.HashSet<>();
  private final Stats stats = new Stats();

  /** A call from a project frame into code that is not the project's (or a mock standing in for it). */
  private static final class Outside {
    Frame caller;
    int depth; // stack depth of the called method
    boolean byMethod; // match the exit by method alone (inline mocks report other depths)
    boolean mockOf; // an inline mock standing in for this project method
    Method method;
    Map<String, Object> call; // null: hidden (class loading, invokedynamic plumbing, Mockito's dispatch)
    MethodExitRequest exit; // a cheap call: its exit, for the value
    BreakpointRequest back; // a framework call: where the caller resumes when it returns
  }

  private record ClassInfo(String file, Outline outline) {}

  private static final class Frame {
    int id, depth, line;
    int callStep; // index of its `call` step
    boolean newFn; // its function was first seen with this frame
    String fn;
    Method method;
    Outline.Method src;
    ClassInfo info;
  }

  /** Where the time goes (WIZ_RECORDER_STATS=file appends one line per recording). */
  private static final class Stats {
    final long start = System.nanoTime();
    long windowOpened, windowClosed;
    int beforeWindow, events, breakpoints, probeHits, mockHits, unwatched;
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
    this.spring = new Framework(t -> classInfo(t) != null);
  }

  // connection --------------------------------------------------------------------------------
  void run(int timeoutMs) throws Exception {
    vm = accept(timeoutMs);
    erm = vm.eventRequestManager();
    // Only the test class is watched before the test runs: whatever starts first (a Spring
    // context, a logging system) runs at full speed.
    ClassPrepareRequest r = erm.createClassPrepareRequest();
    r.addClassFilter(testClass);
    r.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
    r.enable();
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
      stats.events += events.size();
      if (!windowOpen && !windowDone) stats.beforeWindow += events.size();
      events.sort(Comparator.comparingInt(this::order));
      boolean dead = false;
      try {
        unwatch(); // no entry/exit events while this event set is handled
      } catch (com.sun.jdi.VMDisconnectedException gone) {
        break;
      } catch (RuntimeException err) {
        // never let the helper die on this: its exit would end the test JVM mid-run
        System.err.println("wiz: could not pause watching: " + err);
      }
      for (Event e : events) {
        if (DEBUG != null) debug(e);
        try {
          if (e instanceof ClassPrepareEvent cp) onPrepare(cp.referenceType());
          else if (e instanceof MethodEntryEvent me && me.request() == anyEntry) onCall(me);
          else if (e instanceof MethodExitEvent mx && outsideOf(mx) != null) onOutsideExit(mx, outsideOf(mx));
          else if (e instanceof MethodExitEvent mx) onExit(mx);
          else if (e instanceof BreakpointEvent bp && backOf(bp) != null) onBack(bp, backOf(bp));
          else if (e instanceof BreakpointEvent bp && adviceBreaks.contains(bp.request())) onMockAdvice(bp);
          else if (e instanceof BreakpointEvent bp && probes.containsKey(bp.request())) onProbe(bp, probes.get(bp.request()));
          else if (e instanceof BreakpointEvent bp) onLine(bp);
          else if (e instanceof ExceptionEvent ex) onException(ex);
          else if (e instanceof VMDeathEvent || e instanceof VMDisconnectEvent) dead = true;
        } catch (com.sun.jdi.VMDisconnectedException gone) {
          dead = true;
        } catch (RuntimeException | InternalError | IncompatibleThreadStateException err) {
          System.err.println("wiz: skipped an event: " + err + " at " + java.util.Arrays.toString(java.util.Arrays.copyOf(err.getStackTrace(), 6)));
        }
      }
      if (dead) break;
      try {
        watch();
      } catch (com.sun.jdi.VMDisconnectedException gone) {
        break;
      } catch (RuntimeException err) {
        System.err.println("wiz: could not resume watching: " + err);
      }
      try {
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

  private static final java.io.PrintStream DEBUG = debugStream();

  private static java.io.PrintStream debugStream() {
    String f = System.getenv("WIZ_RECORDER_DEBUG");
    try {
      return f == null || f.isEmpty() ? null : new java.io.PrintStream(new java.io.FileOutputStream(f, true), true);
    } catch (IOException e) {
      return null;
    }
  }

  private long lastDebug = System.nanoTime();

  private void debug(Event e) {
    String what = e.getClass().getSimpleName();
    if (e instanceof com.sun.jdi.event.LocatableEvent le) what += " " + le.location().method().declaringType().name() + "." + le.location().method().name() + ":" + lineOf(le.location(), -1) + "@" + le.location().codeIndex();
    if (backOf(e instanceof BreakpointEvent bp ? bp : null) != null) what += " [back]";
    long now = System.nanoTime();
    what = String.format("%6d ms  ", (now - lastDebug) / 1_000_000) + what;
    lastDebug = now;
    DEBUG.println(what + "  stack=" + stack.size() + " outsides=" + outsides.size() + " watching=" + watching);
  }

  private int order(Event e) {
    if (e instanceof ClassPrepareEvent) return 0;
    if (e instanceof MethodEntryEvent) return 1;
    if (e instanceof BreakpointEvent bp) return backOf(bp) != null ? 2 : 3; // a call returned, then the line
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
    if (t.name().equals(testClass) && !windowOpen && !windowDone) {
      // The test method's own lines: the first one to run opens the window.
      for (Method m : t.methodsByName(testMethod)) {
        if (sourceMethod(m) != null) setBreakpoints(m, null);
      }
    }
    if (!windowOpen) return;
    if (classInfo(t) != null) setBreakpoints(t);
    probe(t);
  }

  /** Line breakpoints on every project method of a project class, on the test's thread. */
  private void setBreakpoints(ReferenceType t) {
    try {
      for (Method m : t.methods()) {
        if (sourceMethod(m) != null) setBreakpoints(m, thread);
      }
    } catch (com.sun.jdi.ClassNotPreparedException ignored) {
      // loaded but not prepared yet: its ClassPrepareEvent comes later
    }
  }

  /** Line breakpoints on every line of a project method, once (on one thread, if given). */
  private void setBreakpoints(Method m, ThreadReference only) {
    if (!withBreakpoints.add(m)) return;
    try {
      for (Location l : m.allLineLocations()) {
        BreakpointRequest bp = erm.createBreakpointRequest(l);
        if (only != null) bp.addThreadFilter(only);
        bp.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
        bp.enable();
        stats.breakpoints++;
      }
    } catch (AbsentInformationException | InternalError ignored) {
      // no line numbers (compiled without -g), or redefined at runtime
    }
  }

  /** Breakpoints on Mockito's inline-mock handler and the framework methods probed, in one loaded class. */
  private void probe(ReferenceType t) {
    List<Method> handlers = t.name().equals(MOCK_ADVICE) ? t.methodsByName("handle") : List.of();
    for (Method m : handlers) {
      BreakpointRequest bp = probeAt(m);
      if (bp != null) adviceBreaks.add(bp);
    }
    for (Framework.Probe p : Framework.PROBES) {
      if (!p.cls().equals(t.name())) continue;
      for (Method m : t.methodsByName(p.method())) {
        BreakpointRequest bp = probeAt(m);
        if (bp != null) probes.put(bp, p);
      }
    }
  }

  private BreakpointRequest probeAt(Method m) {
    if (m.isAbstract() || m.isNative() || m.location() == null) return null;
    BreakpointRequest bp = erm.createBreakpointRequest(m.location());
    bp.addThreadFilter(thread);
    bp.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
    bp.enable();
    windowRequests.add(bp);
    return bp;
  }

  // window: the test method's run ---------------------------------------------------------------
  private boolean isTestMethod(Method m) {
    return m.name().equals(testMethod) && m.declaringType().name().equals(testClass);
  }

  private void openWindow(ThreadReference t) {
    windowOpen = true;
    thread = t;
    stats.windowOpened = System.nanoTime();
    for (String pkg : packages) {
      MethodExitRequest ex = erm.createMethodExitRequest();
      ex.addClassFilter(pkg + ".*");
      ex.addThreadFilter(t);
      ex.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
      frameExits.add(ex);
      windowRequests.add(ex);
      ClassPrepareRequest cp = erm.createClassPrepareRequest(); // its breakpoints before its code runs
      cp.addClassFilter(pkg + ".*");
      cp.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
      cp.enable();
      windowRequests.add(cp);
    }
    ExceptionRequest exc = erm.createExceptionRequest(null, true, true);
    exc.addThreadFilter(t);
    exc.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
    exc.enable();
    windowRequests.add(exc);
    anyEntry = erm.createMethodEntryRequest();
    anyEntry.addThreadFilter(t);
    anyEntry.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
    windowRequests.add(anyEntry);
    // Every project class loaded so far gets its breakpoints, on this thread only.
    for (ReferenceType type : vm.allClasses()) {
      String name = type.name();
      if (packages.stream().anyMatch(p -> name.startsWith(p + ".")) && classInfo(type) != null) setBreakpoints(type);
    }
    java.util.Set<String> watched = new java.util.LinkedHashSet<>(Framework.probedClasses());
    watched.add(MOCK_ADVICE);
    for (String name : watched) {
      for (ReferenceType loaded : vm.classesByName(name)) probe(loaded);
      ClassPrepareRequest later = erm.createClassPrepareRequest();
      later.addClassFilter(name);
      later.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
      later.enable();
      windowRequests.add(later);
    }
  }

  private void closeWindow() {
    if (windowDone) return;
    windowOpen = false;
    windowDone = true;
    watching = false;
    stats.windowClosed = System.nanoTime();
    try {
      for (Outside o : outsides) forget(o);
      outsides.clear();
      erm.deleteEventRequests(windowRequests);
      anyEntry = null; // deleted with windowRequests: watch() must not touch it again
      frameExits.clear();
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
    lineStep(loc);
  }

  /** A `line` step at `loc`. A method's first line opens its frame. */
  private void lineStep(Location loc) throws IncompatibleThreadStateException {
    Method m = loc.method();
    Outline.Method src = sourceMethod(m);
    if (src == null) return;
    int depth = thread.frameCount();
    int line = lineOf(loc, -1);
    if (line < 0 || inMockedCall(m, depth)) return;
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
        if (!enterCallee(m, src, depth)) return;
        f = stack.peek();
      }
      raw = snapshot(thread.frame(0));
    }
    pending = null;
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

  /** A project method was entered (seen by the call event or its first line). */
  private boolean enterCallee(Method m, Outline.Method src, int depth) throws IncompatibleThreadStateException {
    Outside cur = current();
    if (cur != null && depth > cur.depth && cur.call != null && m.name().equals(cur.method.name())) {
      cur.call.put("proxied", true); // a proxy (Spring, a spy) passing the call on to real code
    }
    outsideEnded(depth - 1);
    unwind(depth, true);
    return enter(m, src, depth);
  }

  private void onExit(MethodExitEvent e) throws IncompatibleThreadStateException {
    if (!windowOpen || !e.thread().equals(thread) || isLambda(e.method())) return;
    Outline.Method src = sourceMethod(e.method());
    if (src == null) return;
    Outside cur = current();
    if (cur == null || !e.method().equals(cur.method)) outsideEnded(thread.frameCount());
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
    Map<String, Object> s = step("return", f, f.line);
    s.put("value", isVoid ? "void" : values.render(v));
    popFrame(f);
    emit(s);
    if (stack.isEmpty()) finish(null);
  }

  /**
   * An exception on the test's thread. Thrown and caught inside a library (above the top project
   * frame): ignored, unless it is the framework turning a request down. Otherwise it unwinds the
   * project frames above the frame that catches it.
   */
  private void onException(ExceptionEvent e) throws IncompatibleThreadStateException {
    if (!windowOpen || !e.thread().equals(thread)) return;
    Location thrown = e.location(), caught = e.catchLocation();
    ObjectReference exc = e.exception();
    boolean inProject = thrownInProject(thrown);
    if (!inProject) {
      Framework.Rejection r = Framework.rejection(exc); // validation or security turning the call down
      if (r != null && rejected.add(exc.uniqueID())) note(r.kind(), r.label());
    }
    Frame top = stack.peek();
    if (top == null) return;
    int catchDepth = catchDepth(caught);
    if (!inProject && catchDepth > top.depth) return; // library-internal
    String type = Values.simple(exc.referenceType().name());
    Value msg = exc.getValue(exc.referenceType().fieldByName("detailMessage"));
    String text = msg instanceof StringReference sr ? Values.clip(sr.value(), 200) : "";
    pending = new String[] {type, text};
    if (inProject) unwind(thread.frameCount(), false);
    Frame f = stack.peek();
    if (f == null) return;
    Map<String, Object> s = step("exception", f, inProject ? lineOf(thrown, f.line) : f.line);
    s.put("type", type);
    s.put("message", text);
    emit(s);
    // Every frame above the one that catches it ends here, by this exception (method exit events
    // are not sent for frames an exception pops). Not caught in the test method: the test is over.
    outsideEnded(catchDepth);
    unwind(catchDepth, false);
  }

  /** Stack depth of the frame that will catch (the first, from the top, running the handler's method); 0 if none. */
  private int catchDepth(Location caught) throws IncompatibleThreadStateException {
    if (caught == null) return 0;
    List<StackFrame> fs = thread.frames();
    for (int i = 0; i < fs.size(); i++) {
      if (fs.get(i).location().method().equals(caught.method())) return fs.size() - i;
    }
    return 0;
  }

  private boolean thrownInProject(Location thrown) {
    return sourceMethod(thrown.method()) != null && !isLambda(thrown.method());
  }

  // framework hops ------------------------------------------------------------------------------
  private void onProbe(BreakpointEvent e, Framework.Probe p) throws IncompatibleThreadStateException {
    if (!windowOpen || !e.thread().equals(thread)) return;
    stats.probeHits++;
    String label = spring.label(p, thread);
    if (label != null) note(p.kind(), label);
  }

  /** A framework hop, told at the top project frame's current line (where the call into it is). */
  private void note(String kind, String label) {
    Frame f = stack.peek();
    if (f == null || framework.size() >= Framework.MAX_NOTES || windowDone) return;
    framework.add(Framework.note(f.id, f.line, steps.size() - 1, kind, label));
  }

  // what is watched ---------------------------------------------------------------------------
  /** Project code is running (not a call it made): watch the calls it makes and its own exit. */
  private void watch() {
    boolean want = windowOpen && !stack.isEmpty() && current() == null;
    if (want != watching && anyEntry != null) {
      for (MethodExitRequest x : frameExits) x.setEnabled(want);
      watching = want;
    }
    if (anyEntry != null) anyEntry.setEnabled(want && calls.size() < MAX_CALLS);
    // Only the innermost open call can return next. An outer one's exit watch (say on
    // java.util.Optional, for `orElseGet(() -> repo.find(...))`) would stop the thread at every
    // Optional method that returns anywhere below it, inside Hibernate too: a 50x slowdown.
    Outside innermost = outsides.peek();
    for (Outside o : outsides) {
      if (o.exit != null && o.exit.isEnabled() != (o == innermost)) o.exit.setEnabled(o == innermost);
    }
  }

  /** Nothing that a method invoked to render a value could trigger. */
  private void unwatch() {
    if (anyEntry != null && anyEntry.isEnabled()) anyEntry.disable();
    for (Outside o : outsides) if (o.exit != null && o.exit.isEnabled()) o.exit.disable();
  }

  // calls that run no project code -------------------------------------------------------------
  /** The library call in progress from the top project frame, if any. */
  private Outside current() {
    Outside o = outsides.peek();
    return o != null && o.caller == stack.peek() ? o : null;
  }

  private Outside outsideOf(MethodExitEvent e) {
    for (Outside o : outsides) if (o.exit != null && e.request() == o.exit) return o;
    return null;
  }

  private Outside backOf(BreakpointEvent e) {
    if (e == null) return null;
    for (Outside o : outsides) if (o.back != null && e.request() == o.back) return o;
    return null;
  }

  /** Inside a call to an inline mock of `m` (its own activation, not a real call it makes). */
  private boolean inMockedCall(Method m, int depth) {
    Outside o = current();
    return o != null && o.mockOf && o.method.equals(m) && depth <= o.depth + 1;
  }

  private static boolean plumbing(Method m) {
    String t = m.declaringType().name();
    boolean superCtor = m.isConstructor() && (t.equals("java.lang.Object") || t.equals("java.lang.Record") || t.equals("java.lang.Enum"));
    return m.isStaticInitializer() || superCtor || t.startsWith("java.lang.invoke.")
        || t.startsWith("jdk.internal.") || t.startsWith("sun.") || t.contains("$$Lambda")
        || t.startsWith("org.mockito.internal.") // an inline mock's dispatch, woven into the mocked class
        || m.name().equals("loadClass");
  }

  /** Cheap to follow to its exit (few calls inside): the JDK, mocks, test libraries, the project's generated code. */
  private boolean cheap(Method m, Map<String, Object> call) {
    String t = m.declaringType().name();
    return (call != null && call.containsKey("mock")) || (t.startsWith("java.") && !m.name().equals("loadClass"))
        || classInfo(m.declaringType()) != null // a project class's generated code: records' accessors, Lombok
        || t.startsWith("org.mockito.") || t.startsWith("org.junit.") || t.startsWith("org.assertj.")
        || t.startsWith("org.hamcrest.");
  }

  /**
   * Project code called a method. A project method opens its frame. Anything else (a mock, the JDK,
   * a library) is recorded as a call and followed to its return; what runs inside is not watched.
   */
  private void onCall(MethodEntryEvent e) throws IncompatibleThreadStateException {
    if (!windowOpen || !e.thread().equals(thread) || current() != null) return;
    Method m = e.method();
    Frame top = stack.peek();
    if (top == null) return;
    int depth = thread.frameCount();
    if (depth <= top.depth) return; // the project frame opened for this very call
    Outline.Method src = sourceMethod(m);
    if (src != null && isLambda(m)) return; // folded into its method by its lines
    if (src != null && inMockedCall(m, depth)) return;
    if (src != null && enterCallee(m, src, depth)) {
      lineStep(e.location()); // its first line (its breakpoint is the same location)
      return;
    }
    Outside o = new Outside();
    if (src != null) {
      // Not entered: Mockito's dispatch runs first, an inline mock of a project class.
      o.caller = top;
      o.depth = depth;
      o.method = m;
      o.byMethod = true;
      o.mockOf = true;
      o.call = describe(m, top);
      o.call.put("mock", true);
      o.exit = exitOf(m);
      outsides.push(o);
      return;
    }
    o.caller = top;
    o.depth = depth;
    o.method = m;
    if (depth == top.depth + 1 && !plumbing(m)) o.call = describe(m, top);
    if (!cheap(m, o.call)) o.back = returnPoint(depth);
    if (o.back == null) o.exit = exitOf(m); // no call instruction to come back to (class loading on `new`)
    else stats.unwatched++;
    outsides.push(o);
  }

  private MethodExitRequest exitOf(Method m) {
    MethodExitRequest x = erm.createMethodExitRequest();
    x.addClassFilter(m.declaringType().name()); // that class only (a type filter would match its subclasses too)
    x.addThreadFilter(thread);
    x.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
    return x; // enabled by watch(), after this event
  }

  /** A breakpoint where the caller (the frame below the callee at `depth`) resumes, right after the call instruction. */
  private BreakpointRequest returnPoint(int depth) throws IncompatibleThreadStateException {
    if (depth < 2) return null;
    Location at = thread.frame(1).location();
    Method caller = at.method();
    if (sourceMethod(caller) == null) return null;
    byte[] code = bytecodes.computeIfAbsent(caller, Method::bytecodes);
    int bci = (int) at.codeIndex();
    if (bci < 0 || bci >= code.length) return null;
    int len = switch (code[bci] & 0xff) {
      case 0xb6, 0xb7, 0xb8 -> 3; // invokevirtual, invokespecial, invokestatic
      case 0xb9, 0xba -> 5; // invokeinterface, invokedynamic
      default -> 0;
    };
    if (len == 0) return null;
    Location next = caller.locationOfCodeIndex(bci + len);
    if (next == null) return null;
    BreakpointRequest bp = erm.createBreakpointRequest(next);
    bp.addThreadFilter(thread);
    bp.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
    bp.enable();
    return bp;
  }

  /** The caller resumed right after an unwatched call: it returned normally (its value was not watched). */
  private void onBack(BreakpointEvent e, Outside o) throws IncompatibleThreadStateException {
    if (!e.thread().equals(thread) || thread.frameCount() != o.depth - 1) return; // a deeper (recursive) run of that line
    unwind(o.depth - 1, false); // project frames it called back into, that ended unseen
    while (outsides.peek() != o) finishOutside(outsides.peek()); // calls it made that we lost track of
    finishOutside(o);
  }

  /**
   * Mockito's inline-mock handler runs (MockMethodAdvice.handle(mock, method, arguments)). Mockito 5
   * mocks concrete classes inline (@MockitoBean, @Mock): the mocked method itself is entered, so it
   * looked like real code. If its frame has done nothing yet, it was a mock: the frame is taken back
   * and the call is recorded as a call to a mock, its result the stubbed answer.
   */
  private void onMockAdvice(BreakpointEvent e) throws IncompatibleThreadStateException {
    if (!windowOpen || !e.thread().equals(thread)) return;
    stats.mockHits++;
    List<Value> av;
    try {
      av = thread.frame(0).getArgumentValues();
    } catch (com.sun.jdi.InternalException ignored) {
      return;
    }
    List<Value> items = !av.isEmpty() && av.get(av.size() - 1) instanceof com.sun.jdi.ArrayReference arr ? arr.getValues() : null;
    Frame f = stack.peek();
    if (f != null && untouched(f) && calledFrom(f.method)) {
      mockCall(f, items);
      return;
    }
    Outside cur = current();
    if (cur == null || cur.call == null || items == null || !((Map<?, ?>) cur.call.get("args")).isEmpty()) return;
    cur.call.put("args", mockArgs(cur.method, items));
  }

  /** The handler was called from `m`: the first project method below it (the advice is woven into the mocked method). */
  private boolean calledFrom(Method m) throws IncompatibleThreadStateException {
    for (int i = 1; i < Math.min(5, thread.frameCount()); i++) {
      Method at = thread.frame(i).location().method();
      if (at.equals(m)) return true;
      if (sourceMethod(at) != null) return false;
    }
    return false;
  }

  /** A frame that has opened no frames since it was entered (the advice's own calls, like Class.getMethod, aside). */
  private boolean untouched(Frame f) {
    if (nextFrame != f.id + 1 || stack.size() < 2) return false;
    for (int i = f.callStep; i < steps.size(); i++) if (!steps.get(i).get("frame").equals(f.id)) return false;
    return true;
  }

  private void mockCall(Frame f, List<Value> items) {
    for (java.util.Iterator<Outside> it = outsides.iterator(); it.hasNext(); ) {
      Outside o = it.next();
      if (o.caller != f) continue;
      forget(o);
      it.remove(); // Mockito's own dispatch, made from the mocked method
    }
    framework.removeIf(n -> n.get("frame").equals(f.id));
    calls.removeIf(c -> c.get("frame").equals(f.id));
    while (steps.size() > f.callStep) steps.remove(steps.size() - 1);
    frames.remove(String.valueOf(f.id));
    if (f.newFn) functions.remove(f.fn);
    nextFrame = f.id;
    stack.pop();
    Frame caller = stack.peek();
    Method m = f.method;
    Map<String, Object> c = new LinkedHashMap<>();
    c.put("frame", caller.id);
    c.put("line", caller.line);
    c.put("after", steps.size() - 1);
    c.put("name", m.isConstructor() ? "new " + Values.simple(m.declaringType().name()) : m.name());
    c.put("type", m.declaringType().name());
    c.put("arity", m.argumentTypeNames().size());
    c.put("args", items == null ? new LinkedHashMap<>() : mockArgs(m, items));
    c.put("mock", true);
    Outside o = new Outside();
    o.caller = caller;
    o.depth = f.depth;
    o.method = m;
    o.byMethod = true;
    o.mockOf = true;
    o.call = c;
    o.exit = exitOf(m);
    outsides.push(o);
  }

  private Map<String, Object> mockArgs(Method m, List<Value> items) {
    Outline.Method src = sourceMethod(m);
    Map<String, Object> args = new LinkedHashMap<>();
    for (int i = 0; i < items.size(); i++) {
      String name = src != null && src.params().size() == items.size() ? src.params().get(i) : "arg" + i;
      args.put(name, values.render(items.get(i)));
    }
    return args;
  }

  private Map<String, Object> describe(Method m, Frame caller) throws IncompatibleThreadStateException {
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
    boolean mock = false;
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
    Map<String, Object> args = new LinkedHashMap<>();
    for (int i = 0; i < argValues.size(); i++) {
      args.put(i < names.size() ? names.get(i) : "arg" + i, values.render(argValues.get(i)));
    }
    c.put("args", args);
    if (mock) c.put("mock", true);
    return c;
  }

  private void onOutsideExit(MethodExitEvent e, Outside o) throws IncompatibleThreadStateException {
    if (!e.thread().equals(thread) || !e.method().equals(o.method)) return;
    if (!o.byMethod && thread.frameCount() != o.depth) return;
    forget(o);
    if (o.call != null && pending == null) {
      Value v;
      if (o.method.isConstructor()) v = thread.frame(0).thisObject();
      else v = o.method.returnTypeName().equals("void") ? null : e.returnValue();
      boolean isVoid = v == null && o.method.returnTypeName().equals("void");
      o.call.put("value", isVoid ? "void" : values.render(v));
    }
    while (outsides.peek() != o) finishOutside(outsides.peek());
    finishOutside(o);
  }

  /** Calls in progress deeper than `depth` are over: if an exception is unwinding, they raised it. */
  private void outsideEnded(int depth) {
    while (!outsides.isEmpty() && depth < outsides.peek().depth) finishOutside(outsides.peek());
  }

  private void finishOutside(Outside o) {
    forget(o);
    outsides.remove(o);
    Map<String, Object> c = o.call;
    if (c == null || c.containsKey("proxied") || calls.size() >= MAX_CALLS) return;
    if (!c.containsKey("value") && pending != null) {
      c.put("raised", true);
      c.put("error", pending[0] + (pending[1].isEmpty() ? "" : ": " + pending[1]));
    }
    calls.add(c);
  }

  /** Stop watching for a call's end. */
  private void forget(Outside o) {
    try {
      if (o.exit != null) erm.deleteEventRequest(o.exit);
      if (o.back != null) erm.deleteEventRequest(o.back);
    } catch (RuntimeException ignored) {
      // VM gone
    }
    o.exit = null;
    o.back = null;
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
    boolean newFn = !functions.containsKey(fn);
    if (newFn) {
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
    f.newFn = newFn;
    f.method = m;
    f.callStep = steps.size();
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
  /** Locals then fields of `this`, read before anything is rendered. */
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
    rec.put("framework", framework);
    rec.put("truncated", truncated);
    rec.put("test", test);
    Files.writeString(out, Json.write(rec), StandardCharsets.UTF_8);
    String statsFile = System.getenv("WIZ_RECORDER_STATS");
    if (statsFile != null && !statsFile.isEmpty()) {
      long end = stats.windowClosed > 0 ? stats.windowClosed : System.nanoTime();
      String line = String.format("%s\tstart_to_window_s=%.1f\twindow_s=%.1f\tevents=%d\tbefore_window=%d\tbreakpoints=%d\tprobe_hits=%d\tmock_hits=%d\tunwatched_calls=%d\tsteps=%d\tnotes=%d%n",
          testClass + "#" + testMethod,
          stats.windowOpened > 0 ? (stats.windowOpened - stats.start) / 1e9 : -1.0,
          stats.windowOpened > 0 ? (end - stats.windowOpened) / 1e9 : -1.0,
          stats.events, stats.beforeWindow, stats.breakpoints, stats.probeHits, stats.mockHits, stats.unwatched, steps.size(), framework.size());
      Files.writeString(Path.of(statsFile), line, StandardCharsets.UTF_8,
          java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.APPEND);
    }
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
      // However this helper ends (done, SIGTERM from wiz, wiz gone), the test JVM must not
      // outlive it suspended: JDWP's Exit ends it even with every thread stopped at an event.
      Runtime.getRuntime().addShutdownHook(new Thread(() -> endTestJvm(vm), "wiz-end-test-jvm"));
      System.out.println("CONNECTED");
      System.out.flush();
      return vm;
    } finally {
      lc.stopListening(args);
    }
  }

  private static void endTestJvm(VirtualMachine vm) {
    try {
      vm.exit(1);
    } catch (RuntimeException alreadyGone) {
      // it exited on its own
    }
  }

  /**
   * wiz keeps this process's stdin open and never writes to it. When it closes, wiz has gone
   * (even by kill -9): exit, which ends the test JVM too (see accept).
   */
  static void exitWithParent() {
    Thread t = new Thread(() -> {
      try {
        while (System.in.read() >= 0) {
          // nothing is ever sent
        }
      } catch (IOException ignored) {
        // closed
      }
      System.exit(143);
    }, "wiz-parent-watch");
    t.setDaemon(true);
    t.start();
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
