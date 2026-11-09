from wizualizer.diff import parse_unified_diff

DIFF = """\
diff --git a/shop/pricing.py b/shop/pricing.py
index 5f4c2f2..c7041be 100644
--- a/shop/pricing.py
+++ b/shop/pricing.py
@@ -4,0 +5,2 @@ from datetime import date
+A = 1
+B = 2
@@ -20,2 +22,2 @@ def f():
-    old()
-    old2()
+    new()
+    new2()
@@ -30,3 +31,0 @@ def g():
-    gone()
-    gone2()
-    gone3()
diff --git a/docs/x.md b/docs/x.md
--- a/docs/x.md
+++ b/docs/x.md
@@ -1 +1 @@
-a
+b
diff --git a/dead.py b/dead.py
deleted file mode 100644
--- a/dead.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x = 1
-y = 2
"""


def test_added_and_modified_lines_map_to_new_file_numbers():
    changes = parse_unified_diff(DIFF)
    assert changes["shop/pricing.py"].added == {5, 6, 22, 23}


def test_pure_deletions_are_remembered_by_the_line_before_them():
    assert parse_unified_diff(DIFF)["shop/pricing.py"].removed_after == {31}


def test_single_line_hunks_without_counts():
    assert parse_unified_diff(DIFF)["docs/x.md"].added == {1}


def test_deleted_files_are_dropped():
    assert "dead.py" not in parse_unified_diff(DIFF)


def test_context_lines_advance_the_new_line_counter():
    diff = "--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,4 @@\n a\n+b\n c\n+d\n"
    assert parse_unified_diff(diff)["m.py"].added == {2, 4}
