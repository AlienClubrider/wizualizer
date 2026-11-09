package wiz;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * wizualizer's Java helper. Commands:
 *
 * <pre>
 *   record --root DIR --out FILE --test pkg.Class#method [--max-steps N] [--timeout MS]
 *          --source-root DIR... --package NAME...
 *       Listen for the test JVM (prints "LISTENING host:port"), record, write FILE.
 *   outline FILE...
 *       Print {"FILE": outline} as JSON.
 *   serve
 *       Read requests "&lt;byte length&gt;\n&lt;source&gt;" from stdin; answer each with one JSON line.
 * </pre>
 */
public final class Main {
  public static void main(String[] argv) throws Exception {
    if (argv.length == 0) {
      System.err.println("usage: wiz.Main record|outline|serve ...");
      System.exit(2);
    }
    switch (argv[0]) {
      case "record" -> record(argv);
      case "outline" -> {
        StringBuilder sb = new StringBuilder("{");
        for (int i = 1; i < argv.length; i++) {
          if (i > 1) sb.append(',');
          Json.quote(sb, argv[i]);
          sb.append(':');
          Json.write(sb, Outline.parse(Files.readString(Path.of(argv[i]))).toJson());
        }
        System.out.println(sb.append('}'));
      }
      case "serve" -> serve();
      default -> {
        System.err.println("unknown command " + argv[0]);
        System.exit(2);
      }
    }
  }

  private static void record(String[] argv) throws Exception {
    Path root = null, out = null;
    String test = null;
    int maxSteps = 10_000, timeout = 900_000;
    List<Path> sourceRoots = new ArrayList<>();
    List<String> packages = new ArrayList<>();
    for (int i = 1; i < argv.length; i += 2) {
      String v = argv[i + 1];
      switch (argv[i]) {
        case "--root" -> root = Path.of(v).toAbsolutePath().normalize();
        case "--out" -> out = Path.of(v);
        case "--test" -> test = v;
        case "--max-steps" -> maxSteps = Integer.parseInt(v);
        case "--timeout" -> timeout = Integer.parseInt(v);
        case "--source-root" -> sourceRoots.add(Path.of(v).toAbsolutePath().normalize());
        case "--package" -> packages.add(v);
        default -> throw new IllegalArgumentException("unknown option " + argv[i]);
      }
    }
    if (root == null || out == null || test == null || !test.contains("#")) {
      throw new IllegalArgumentException("record needs --root, --out and --test pkg.Class#method");
    }
    new Recorder(root, sourceRoots, packages, test, maxSteps, out).run(timeout);
  }

  private static void serve() throws IOException {
    DataInputStream in = new DataInputStream(new BufferedInputStream(System.in));
    PrintStream out = new PrintStream(System.out, true, StandardCharsets.UTF_8);
    while (true) {
      String header = readLine(in);
      if (header == null) return;
      if (header.isBlank()) continue;
      byte[] buf = new byte[Integer.parseInt(header.trim())];
      in.readFully(buf);
      String reply;
      try {
        reply = Json.write(Outline.parse(new String(buf, StandardCharsets.UTF_8)).toJson());
      } catch (RuntimeException e) {
        reply = Json.write(Map.of("error", String.valueOf(e)));
      }
      out.println(reply);
    }
  }

  private static String readLine(DataInputStream in) throws IOException {
    ByteArrayOutputStream b = new ByteArrayOutputStream();
    while (true) {
      int c;
      try {
        c = in.readUnsignedByte();
      } catch (EOFException e) {
        return b.size() == 0 ? null : b.toString(StandardCharsets.UTF_8);
      }
      if (c == '\n') return b.toString(StandardCharsets.UTF_8);
      b.write(c);
    }
  }
}
