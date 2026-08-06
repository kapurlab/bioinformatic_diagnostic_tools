#!/usr/bin/env python3
"""The dashboard page's JavaScript must parse — checked AS SERVED.

A syntax error anywhere in the page's main script block is not a small bug: the
whole block fails to parse, so `load()`, `loadInfo()` and `pollUpdates()` never
run and the user gets the header, the CSS and an empty page — no tool cards, no
host label, no update banner, no Shut down / Restart buttons. Nothing in the
server log says anything is wrong, because nothing is wrong on the server.

This has to check the string AFTER Python has evaluated it, which is the part that
is easy to get wrong. PAGE is a `\"\"\"...\"\"\"` literal, so Python processes escapes
inside it before it is ever sent: a `\\'` written in dashboard.py arrives at the
browser as a bare `'` and terminates the JS string it was meant to sit inside.
Checking the source text (or any unescaped copy of it) does NOT catch that — it
looks correctly escaped there. Only the evaluated string tells the truth, so the
rule for JS inside PAGE is: prefer double-quoted JS strings for text containing
apostrophes, and write a JS newline as `\\\\n`.

Requires node for the parse itself; skipped without it.
"""
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def load_page():
    """dashboard.PAGE, evaluated — i.e. exactly the bytes the browser receives."""
    sys.path.insert(0, str(ROOT / "bin/lib"))
    spec = importlib.util.spec_from_file_location(
        "bdtools_dashboard_page", ROOT / "bin/dashboard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PAGE


@unittest.skipUnless(NODE, "node is required to parse the page's JavaScript")
class PageScriptTests(unittest.TestCase):
    def setUp(self):
        self.page = load_page()
        self.blocks = re.findall(r"<script>(.*?)</script>", self.page, re.S)

    def test_the_page_has_its_script_blocks(self):
        # Two: the early theme block in <head>, and the main app block.
        self.assertEqual(len(self.blocks), 2, "expected 2 <script> blocks in PAGE")

    def test_every_served_script_block_parses(self):
        for i, block in enumerate(self.blocks):
            with self.subTest(block=i):
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
                    fh.write(block)
                    path = fh.name
                proc = subprocess.run([NODE, "--check", path],
                                      capture_output=True, text=True, timeout=60)
                Path(path).unlink(missing_ok=True)
                self.assertEqual(
                    proc.returncode, 0,
                    f"script block {i} does not parse as served:\n{proc.stderr}")

    def test_the_functions_the_page_calls_at_load_are_defined(self):
        # The bottom of the page calls these unconditionally. A missing one throws
        # at top level and aborts every statement after it, which looks identical
        # to a syntax error from the user's side.
        main = self.blocks[-1]
        for name in ("load", "loadInfo", "pollUpdates", "renderUpdates",
                     "versionBlock", "restartDash", "shutdownDash"):
            self.assertIn(f"function {name}", main, f"{name}() is not defined")


@unittest.skipUnless(NODE, "node is required to render the update banner")
class BannerRenderTests(unittest.TestCase):
    """Run renderUpdates() for real, against each combination of update kinds.

    A template-literal typo or a bad field name only shows up when the function
    actually runs, and the banner is rendered from data the tests can supply.
    """

    def render(self, items):
        page = load_page()
        main = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
        body = main[main.index("function renderUpdates"):
                    main.index("// Poll the cached result")]
        harness = (
            "function esc(s){return String(s).replace(/[&<>]/g,"
            "c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}\n"
            "const _el={className:'',innerHTML:'',textContent:''};\n"
            "document={getElementById:()=>_el};\n"
            + body +
            f"\nrenderUpdates({{checked:true,items:{items}}});\n"
            "console.log(_el.innerHTML);\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(harness)
            path = fh.name
        proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        Path(path).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, f"renderUpdates threw:\n{proc.stderr}")
        return proc.stdout

    SUITE = ("{name:'bdtools',label:'bdtools (suite + dashboard)',installed:'a',"
             "latest:'3 new commit(s)',update_available:true,kind:'suite'}")
    TOOL = ("{name:'vsnp_gui',label:'vSNP3',installed:'v0.4.36',latest:'v0.4.37',"
            "update_available:true,kind:'tool'}")
    PKG = ("{name:'vsnp_gui:vsnp3',label:'vSNP3 — vsnp3',installed:'3.35',"
           "latest:'3.36',update_available:true,kind:'package'}")

    def test_all_three_kinds_render_in_run_order(self):
        html = self.render(f"[{self.SUITE},{self.TOOL},{self.PKG}]")
        labels = re.findall(r"<button[^>]*>(?:<span[^>]*>\d</span>)?([^<]*)", html)
        buttons = [l for l in labels if l.strip()]
        self.assertEqual(buttons[0], "Update bdtools")
        self.assertTrue(buttons[1].startswith("Install tool updates"))
        self.assertTrue(buttons[2].startswith("Update conda packages"))

    def test_each_kind_alone_renders_and_is_numbered_1(self):
        for name, item in (("suite", self.SUITE), ("tool", self.TOOL),
                           ("package", self.PKG)):
            with self.subTest(kind=name):
                html = self.render(f"[{item}]")
                # Numbered over the groups PRESENT, so a lone group is step 1 — not
                # step 3 with two missing predecessors.
                self.assertIn('<span class="ustep">1</span>', html)
                self.assertNotIn('<span class="ustep">2</span>', html)

    def test_a_package_item_shows_its_tool_and_that_it_is_conda(self):
        html = self.render(f"[{self.PKG}]")
        self.assertIn("vSNP3 — vsnp3", html)
        self.assertIn("conda package", html)

    def test_up_to_date_renders_without_buttons(self):
        html = self.render(f"[{self.TOOL.replace('update_available:true',
                                                 'update_available:false')}]")
        self.assertIn("Up to date", html)
        self.assertNotIn("Install tool updates", html)


if __name__ == "__main__":
    unittest.main()
