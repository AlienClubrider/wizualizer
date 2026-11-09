package wiz;

import com.sun.source.tree.BlockTree;
import com.sun.source.tree.BreakTree;
import com.sun.source.tree.CaseTree;
import com.sun.source.tree.CatchTree;
import com.sun.source.tree.ClassTree;
import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.ContinueTree;
import com.sun.source.tree.DoWhileLoopTree;
import com.sun.source.tree.EnhancedForLoopTree;
import com.sun.source.tree.ForLoopTree;
import com.sun.source.tree.IfTree;
import com.sun.source.tree.LabeledStatementTree;
import com.sun.source.tree.LambdaExpressionTree;
import com.sun.source.tree.LineMap;
import com.sun.source.tree.MethodTree;
import javax.lang.model.element.Modifier;
import com.sun.source.tree.StatementTree;
import com.sun.source.tree.SwitchTree;
import com.sun.source.tree.SynchronizedTree;
import com.sun.source.tree.Tree;
import com.sun.source.tree.TryTree;
import com.sun.source.tree.VariableTree;
import com.sun.source.tree.WhileLoopTree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.TreePathScanner;
import com.sun.source.util.Trees;
import java.net.URI;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.SimpleJavaFileObject;
import javax.tools.ToolProvider;

/**
 * Reads one Java source file with javac's own parser (jdk.compiler, no extra dependency) and
 * lists: its methods with their line spans and a statement tree (for flowcharts), and every
 * executable statement with its line span (for changed-line coverage).
 */
final class Outline {
  /** A method or constructor that has a body. Lines are 1-based. */
  record Method(String name, String cls, boolean ctor, int line, int open, int end, List<String> params) {
    boolean contains(int l) {
      return l >= line && l <= end;
    }
  }

  final List<Method> methods = new ArrayList<>();
  final List<Map<String, Object>> methodJson = new ArrayList<>();
  final List<int[]> statements = new ArrayList<>(); // {first, lo, hi, measurable 0|1}

  private final CompilationUnitTree cu;
  private final SourcePositions sp;
  private final LineMap lm;
  private final String src;

  private Outline(CompilationUnitTree cu, SourcePositions sp, String src) {
    this.cu = cu;
    this.sp = sp;
    this.lm = cu.getLineMap();
    this.src = src;
  }

  static Outline parse(String source) {
    JavaCompiler javac = ToolProvider.getSystemJavaCompiler();
    if (javac == null) throw new IllegalStateException("no Java compiler: run wiz with a JDK, not a JRE");
    JavaFileObject file =
        new SimpleJavaFileObject(URI.create("string:///Source.java"), JavaFileObject.Kind.SOURCE) {
          @Override
          public CharSequence getCharContent(boolean ignoreEncodingErrors) {
            return source;
          }
        };
    JavacTask task =
        (JavacTask) javac.getTask(null, null, d -> {}, List.of("-proc:none"), null, List.of(file));
    try {
      CompilationUnitTree cu = task.parse().iterator().next();
      Outline o = new Outline(cu, Trees.instance(task).getSourcePositions(), source);
      o.new Scanner().scan(cu, Boolean.FALSE);
      return o;
    } catch (java.io.IOException e) {
      throw new IllegalStateException(e);
    }
  }

  Map<String, Object> toJson() {
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("methods", methodJson);
    List<Object> st = new ArrayList<>();
    for (int[] s : statements) st.add(List.of(s[0], s[1], s[2], s[3] == 1));
    out.put("statements", st);
    return out;
  }

  /** The innermost method whose span holds this line, optionally with this name. */
  Method methodAt(int line, String name) {
    Method best = null;
    for (Method m : methods) {
      if (!m.contains(line) || (name != null && !m.name.equals(name))) continue;
      if (best == null || m.end - m.line < best.end - best.line) best = m;
    }
    return best;
  }

  // positions ---------------------------------------------------------------------------
  private long start(Tree t) {
    return sp.getStartPosition(cu, t);
  }

  private long end(Tree t) {
    return sp.getEndPosition(cu, t);
  }

  private int line(long pos) {
    return pos < 0 ? 0 : (int) lm.getLineNumber(pos);
  }

  private int lastLine(Tree t) {
    long e = end(t);
    return e <= 0 ? line(start(t)) : line(e - 1);
  }

  private String text(long from, long to) {
    if (from < 0 || to <= from || to > src.length()) return "";
    return src.substring((int) from, (int) to).replaceAll("\\s+", " ").trim();
  }

  private String text(Tree t) {
    return text(start(t), end(t));
  }

  /** Source from the start of t up to (not including) child, without a trailing "{", "->" or ":". */
  private String header(Tree t, Tree child) {
    String h = text(start(t), start(child));
    return h.replaceAll("(\\{|->|:)\\s*$", "").trim();
  }

  /** Line of the last non-blank character before `child` starts (end of a header). */
  private int headerLastLine(Tree t, Tree child) {
    long p = start(child) - 1;
    while (p > start(t) && Character.isWhitespace(src.charAt((int) p))) p--;
    if (p > start(t) && src.charAt((int) p) == '{') p--;
    while (p > start(t) && Character.isWhitespace(src.charAt((int) p))) p--;
    return line(p);
  }

  private static List<Integer> span(int a, int b) {
    return List.of(a, Math.max(a, b));
  }

  // flowchart statement tree ---------------------------------------------------------------
  private List<Object> flowBlock(List<? extends StatementTree> stmts) {
    List<Object> out = new ArrayList<>();
    for (StatementTree s : stmts) {
      Map<String, Object> n = flow(s);
      if (n != null) out.add(n);
    }
    return out;
  }

  private Map<String, Object> node(String kind, int a, int b, String label) {
    Map<String, Object> n = new LinkedHashMap<>();
    n.put("k", kind);
    n.put("l", span(a, b));
    n.put("t", label);
    return n;
  }

  private Map<String, Object> flow(StatementTree s) {
    if (s == null) return null;
    int a = line(start(s));
    switch (s.getKind()) {
      case BLOCK -> {
        Map<String, Object> n = new LinkedHashMap<>();
        n.put("k", "block");
        n.put("body", flowBlock(((BlockTree) s).getStatements()));
        return n;
      }
      case IF -> {
        IfTree t = (IfTree) s;
        Map<String, Object> n = node("if", a, lastLine(t.getCondition()), text(start(t), end(t.getCondition())));
        n.put("then", flow(t.getThenStatement()));
        n.put("else", flow(t.getElseStatement()));
        return n;
      }
      case WHILE_LOOP -> {
        WhileLoopTree t = (WhileLoopTree) s;
        Map<String, Object> n = node("while", a, lastLine(t.getCondition()), text(start(t), end(t.getCondition())));
        n.put("body", flow(t.getStatement()));
        return n;
      }
      case DO_WHILE_LOOP -> {
        DoWhileLoopTree t = (DoWhileLoopTree) s;
        long w = src.lastIndexOf("while", (int) start(t.getCondition()));
        Map<String, Object> n = node("do", line(w), lastLine(t.getCondition()), "while " + text(t.getCondition()));
        n.put("do_line", a);
        n.put("body", flow(t.getStatement()));
        return n;
      }
      case FOR_LOOP -> {
        ForLoopTree t = (ForLoopTree) s;
        Map<String, Object> n = node("for", a, headerLastLine(t, t.getStatement()), header(t, t.getStatement()));
        n.put("cond", t.getCondition() != null);
        n.put("body", flow(t.getStatement()));
        return n;
      }
      case ENHANCED_FOR_LOOP -> {
        EnhancedForLoopTree t = (EnhancedForLoopTree) s;
        Map<String, Object> n = node("foreach", a, headerLastLine(t, t.getStatement()), header(t, t.getStatement()));
        n.put("body", flow(t.getStatement()));
        return n;
      }
      case SWITCH -> {
        SwitchTree t = (SwitchTree) s;
        Map<String, Object> n = node("switch", a, lastLine(t.getExpression()), text(start(t), end(t.getExpression())));
        List<Object> cases = new ArrayList<>();
        for (CaseTree c : t.getCases()) cases.add(caseJson(c));
        n.put("cases", cases);
        return n;
      }
      case TRY -> {
        TryTree t = (TryTree) s;
        Map<String, Object> n = node("try", a, headerLastLine(t, t.getBlock()), header(t, t.getBlock()));
        n.put("body", flow(t.getBlock()));
        List<Object> catches = new ArrayList<>();
        for (CatchTree c : t.getCatches()) {
          Map<String, Object> cj = node("catch", line(start(c)), headerLastLine(c, c.getBlock()), header(c, c.getBlock()));
          cj.put("body", flow(c.getBlock()));
          catches.add(cj);
        }
        n.put("catches", catches);
        n.put("finally", flow(t.getFinallyBlock()));
        return n;
      }
      case SYNCHRONIZED -> {
        SynchronizedTree t = (SynchronizedTree) s;
        Map<String, Object> n = node("sync", a, lastLine(t.getExpression()), text(start(t), end(t.getExpression())));
        n.put("body", flow(t.getBlock()));
        return n;
      }
      case LABELED_STATEMENT -> {
        LabeledStatementTree t = (LabeledStatementTree) s;
        Map<String, Object> n = new LinkedHashMap<>();
        n.put("k", "labeled");
        n.put("label", t.getLabel().toString());
        n.put("body", flow(t.getStatement()));
        return n;
      }
      case BREAK -> {
        Map<String, Object> n = node("break", a, lastLine(s), text(s));
        BreakTree t = (BreakTree) s;
        n.put("label", t.getLabel() == null ? null : t.getLabel().toString());
        return n;
      }
      case CONTINUE -> {
        Map<String, Object> n = node("continue", a, lastLine(s), text(s));
        ContinueTree t = (ContinueTree) s;
        n.put("label", t.getLabel() == null ? null : t.getLabel().toString());
        return n;
      }
      case RETURN -> {
        return node("return", a, lastLine(s), text(s));
      }
      case THROW -> {
        return node("throw", a, lastLine(s), text(s));
      }
      case CLASS, INTERFACE, ENUM, RECORD -> {
        return node("class", a, a, "class " + ((ClassTree) s).getSimpleName());
      }
      case EMPTY_STATEMENT -> {
        return null;
      }
      case VARIABLE -> {
        // `long fee;` compiles to nothing: no step, no node.
        return ((VariableTree) s).getInitializer() == null ? null : node("stmt", a, lastLine(s), text(s));
      }
      default -> {
        return node("stmt", a, lastLine(s), text(s));
      }
    }
  }

  private Map<String, Object> caseJson(CaseTree c) {
    Tree first =
        c.getCaseKind() == CaseTree.CaseKind.RULE
            ? c.getBody()
            : (c.getStatements() != null && !c.getStatements().isEmpty() ? c.getStatements().get(0) : null);
    String label = first != null ? header(c, first) : text(c).replaceAll(":\\s*$", "");
    int last = first != null ? headerLastLine(c, first) : lastLine(c);
    Map<String, Object> n = node("case", line(start(c)), last, label);
    n.put("rule", c.getCaseKind() == CaseTree.CaseKind.RULE);
    n.put("default", c.getExpressions().isEmpty() && label.startsWith("default"));
    List<Object> body = new ArrayList<>();
    if (c.getCaseKind() == CaseTree.CaseKind.RULE) {
      Tree b = c.getBody();
      if (b instanceof BlockTree bt) body.addAll(flowBlock(bt.getStatements()));
      else if (b instanceof StatementTree st) body.add(flow(st));
      else if (b != null) body.add(node("stmt", line(start(b)), lastLine(b), text(b)));
    } else if (c.getStatements() != null) {
      body.addAll(flowBlock(c.getStatements()));
    }
    n.put("body", body);
    return n;
  }

  // scanner: methods + statements ------------------------------------------------------------
  private final class Scanner extends TreePathScanner<Void, Boolean> {
    private final Deque<String> classes = new ArrayDeque<>();

    @Override
    public Void scan(Tree tree, Boolean measurable) {
      if (tree instanceof StatementTree st && getCurrentPath() != null && inStatementPosition(getCurrentPath().getLeaf(), tree)) {
        addStatement(st, measurable);
      }
      return super.scan(tree, measurable);
    }

    @Override
    public Void visitClass(ClassTree t, Boolean measurable) {
      String name = t.getSimpleName().toString();
      classes.push(name.isEmpty() ? (classes.isEmpty() ? "" : classes.peek()) + "$anon" : name);
      try {
        for (Tree m : t.getMembers()) {
          if (m instanceof VariableTree v) {
            boolean isStatic = v.getModifiers().getFlags().contains(Modifier.STATIC);
            if (v.getInitializer() != null) {
              int a = line(start(v));
              statements.add(new int[] {a, a, lastLine(v), isStatic ? 0 : 1});
            }
            scan(v.getInitializer(), !isStatic);
          } else if (m instanceof BlockTree b) {
            scan(b, !b.isStatic());
          } else {
            scan(m, measurable);
          }
        }
      } finally {
        classes.pop();
      }
      return null;
    }

    @Override
    public Void visitMethod(MethodTree t, Boolean measurable) {
      BlockTree body = t.getBody();
      if (body == null) return null;
      boolean ctor = t.getName().contentEquals("<init>");
      String cls = String.join(".", reversed(classes));
      String simple = classes.isEmpty() ? "" : classes.peek();
      int line = line(start(t)), open = line(start(body)), end = lastLine(body);
      String name = t.getName().toString();
      List<String> params = new ArrayList<>();
      for (VariableTree p : t.getParameters()) params.add(p.getName().toString());
      methods.add(new Method(name, cls, ctor, line, open, end, params));
      statements.add(new int[] {line, line, open, 1});

      Map<String, Object> m = new LinkedHashMap<>();
      m.put("name", ctor ? simple : name);
      m.put("cls", cls);
      m.put("ctor", ctor);
      m.put("line", line);
      m.put("open", open);
      m.put("end", end);
      m.put("params", params);
      m.put("body", flowBlock(body.getStatements()));
      methodJson.add(m);
      scan(body, Boolean.TRUE);
      return null;
    }

    @Override
    public Void visitLambdaExpression(LambdaExpressionTree t, Boolean measurable) {
      return super.visitLambdaExpression(t, Boolean.TRUE);
    }
  }

  private static List<String> reversed(Deque<String> d) {
    List<String> out = new ArrayList<>(d);
    java.util.Collections.reverse(out);
    return out;
  }

  /** True if `tree` sits where a statement goes (not a parameter, for-loop init, resource, field). */
  private static boolean inStatementPosition(Tree parent, Tree tree) {
    return switch (parent.getKind()) {
      case BLOCK, CASE, LABELED_STATEMENT, IF -> true;
      case WHILE_LOOP -> ((WhileLoopTree) parent).getStatement() == tree;
      case DO_WHILE_LOOP -> ((DoWhileLoopTree) parent).getStatement() == tree;
      case FOR_LOOP -> ((ForLoopTree) parent).getStatement() == tree;
      case ENHANCED_FOR_LOOP -> ((EnhancedForLoopTree) parent).getStatement() == tree;
      default -> false;
    };
  }

  private void addStatement(StatementTree s, boolean measurable) {
    int m = measurable ? 1 : 0;
    int a = line(start(s));
    switch (s.getKind()) {
      case BLOCK, LABELED_STATEMENT, EMPTY_STATEMENT, CLASS, INTERFACE, ENUM, RECORD -> {}
      case IF -> statements.add(new int[] {a, a, lastLine(((IfTree) s).getCondition()), m});
      case WHILE_LOOP -> statements.add(new int[] {a, a, lastLine(((WhileLoopTree) s).getCondition()), m});
      case DO_WHILE_LOOP -> {
        DoWhileLoopTree t = (DoWhileLoopTree) s;
        int w = line(src.lastIndexOf("while", (int) start(t.getCondition())));
        statements.add(new int[] {w, w, lastLine(t.getCondition()), m});
      }
      case FOR_LOOP -> statements.add(new int[] {a, a, headerLastLine(s, ((ForLoopTree) s).getStatement()), m});
      case ENHANCED_FOR_LOOP ->
          statements.add(new int[] {a, a, headerLastLine(s, ((EnhancedForLoopTree) s).getStatement()), m});
      case SWITCH -> statements.add(new int[] {a, a, lastLine(((SwitchTree) s).getExpression()), m});
      case SYNCHRONIZED -> statements.add(new int[] {a, a, lastLine(((SynchronizedTree) s).getExpression()), m});
      case TRY -> {
        TryTree t = (TryTree) s;
        // A plain `try {` compiles to nothing; with resources, the resources run.
        if (!t.getResources().isEmpty()) statements.add(new int[] {a, a, headerLastLine(t, t.getBlock()), m});
        for (CatchTree c : t.getCatches()) {
          int ca = line(start(c));
          statements.add(new int[] {ca, ca, headerLastLine(c, c.getBlock()), m});
        }
      }
      case VARIABLE -> {
        // `int x;` compiles to nothing.
        if (((VariableTree) s).getInitializer() != null) statements.add(new int[] {a, a, lastLine(s), m});
      }
      default -> statements.add(new int[] {a, a, lastLine(s), m});
    }
  }
}
