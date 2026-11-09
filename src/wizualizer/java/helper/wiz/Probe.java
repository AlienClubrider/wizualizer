package wiz;

import com.sun.jdi.AbsentInformationException;
import com.sun.jdi.IncompatibleThreadStateException;
import com.sun.jdi.Location;
import com.sun.jdi.Method;
import com.sun.jdi.ReferenceType;
import com.sun.jdi.StackFrame;
import com.sun.jdi.VirtualMachine;
import com.sun.jdi.event.BreakpointEvent;
import com.sun.jdi.event.ClassPrepareEvent;
import com.sun.jdi.event.Event;
import com.sun.jdi.event.EventSet;
import com.sun.jdi.event.VMDeathEvent;
import com.sun.jdi.event.VMDisconnectEvent;
import com.sun.jdi.request.BreakpointRequest;
import com.sun.jdi.request.ClassPrepareRequest;
import com.sun.jdi.request.EventRequest;
import com.sun.jdi.request.EventRequestManager;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * Which tests run which watched lines, for a whole test run.
 *
 * <p>Breakpoints go only on the watched lines (the PR's changed statements), so the tests run
 * close to full speed. On a hit, the stack tells which test method is running. Output:
 * {"hits": {"pkg.FooTest#bar": {"src/main/java/pkg/Foo.java": [12, 14]}}}.
 */
final class Probe {
  private final Path root;
  private final List<Path> sourceRoots;
  private final List<String> packages;
  private final Map<String, Set<Integer>> watch; // project-relative file -> lines
  private final Set<String> tests; // Class#method
  private final Path out;
  private final Map<String, Map<String, Set<Integer>>> hits = new TreeMap<>();
  private final Map<ReferenceType, String> files = new HashMap<>();

  Probe(Path root, List<Path> sourceRoots, List<String> packages, Map<String, Set<Integer>> watch,
        Set<String> tests, Path out) {
    this.root = root;
    this.sourceRoots = sourceRoots;
    this.packages = Recorder.minimalPrefixes(packages);
    this.watch = watch;
    this.tests = tests;
    this.out = out;
  }

  void run(int timeoutMs) throws Exception {
    VirtualMachine vm = Recorder.accept(timeoutMs);
    EventRequestManager erm = vm.eventRequestManager();
    for (String pkg : packages) {
      ClassPrepareRequest r = erm.createClassPrepareRequest();
      r.addClassFilter(pkg + ".*");
      r.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
      r.enable();
    }
    vm.resume();
    loop:
    while (true) {
      EventSet set;
      try {
        set = vm.eventQueue().remove();
      } catch (InterruptedException e) {
        break;
      }
      for (Event e : set) {
        try {
          if (e instanceof ClassPrepareEvent cp) onPrepare(erm, cp.referenceType());
          else if (e instanceof BreakpointEvent bp) onHit(bp);
          else if (e instanceof VMDeathEvent || e instanceof VMDisconnectEvent) break loop;
        } catch (com.sun.jdi.VMDisconnectedException gone) {
          break loop;
        } catch (RuntimeException | IncompatibleThreadStateException err) {
          System.err.println("wiz: skipped an event: " + err);
        }
      }
      try {
        set.resume();
      } catch (com.sun.jdi.VMDisconnectedException gone) {
        break;
      }
    }
    write();
  }

  private void onPrepare(EventRequestManager erm, ReferenceType t) {
    String file = fileOf(t);
    Set<Integer> lines = file == null ? null : watch.get(file);
    if (lines == null) return;
    files.put(t, file);
    for (Method m : t.methods()) {
      try {
        for (Location l : m.allLineLocations()) {
          if (!lines.contains(l.lineNumber())) continue;
          BreakpointRequest bp = erm.createBreakpointRequest(l);
          bp.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
          bp.enable();
        }
      } catch (AbsentInformationException ignored) {
        // compiled without -g: no lines to watch
      }
    }
  }

  private String fileOf(ReferenceType t) {
    String name = t.name();
    String sourceName;
    try {
      sourceName = t.sourceName();
    } catch (AbsentInformationException e) {
      return null;
    }
    int dot = name.lastIndexOf('.');
    String dir = dot < 0 ? "" : name.substring(0, dot).replace('.', '/') + "/";
    for (Path sr : sourceRoots) {
      Path p = sr.resolve(dir + sourceName);
      if (Files.isRegularFile(p)) return root.relativize(p).toString().replace('\\', '/');
    }
    return null;
  }

  private void onHit(BreakpointEvent e) throws IncompatibleThreadStateException {
    Location at = e.location();
    String file = files.get(at.declaringType());
    if (file == null) return;
    for (StackFrame f : e.thread().frames()) {
      Location l = f.location();
      String id = l.declaringType().name() + "#" + l.method().name();
      if (tests.contains(id)) {
        hits.computeIfAbsent(id, k -> new TreeMap<>())
            .computeIfAbsent(file, k -> new TreeSet<>())
            .add(at.lineNumber());
        return;
      }
    }
  }

  private void write() throws IOException {
    Map<String, Object> perTest = new TreeMap<>();
    hits.forEach((test, byFile) -> {
      Map<String, Object> m = new TreeMap<>();
      byFile.forEach((file, lines) -> m.put(file, List.copyOf(lines)));
      perTest.put(test, m);
    });
    Files.writeString(out, Json.write(Map.of("hits", perTest)), StandardCharsets.UTF_8);
  }

  /** Watch file lines: "path\tline" per line. */
  static Map<String, Set<Integer>> readWatch(Path file) throws IOException {
    Map<String, Set<Integer>> out = new HashMap<>();
    for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
      int tab = line.lastIndexOf('\t');
      if (tab < 0) continue;
      out.computeIfAbsent(line.substring(0, tab), k -> new HashSet<>())
          .add(Integer.parseInt(line.substring(tab + 1).trim()));
    }
    return out;
  }
}
