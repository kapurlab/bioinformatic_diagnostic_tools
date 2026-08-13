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
        return self.render_state(f"{{checked:true,items:{items}}}")

    def render_state(self, payload):
        page = load_page()
        main = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
        body = main[main.index("function renderUpdates"):
                    main.index("// Poll the cached result")]
        harness = (
            "function esc(s){return String(s).replace(/[&<>]/g,"
            "c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}\n"
            "const _el={className:'',innerHTML:'',textContent:''};\n"
            "document={getElementById:()=>_el};\n"
            # Declared just above renderUpdates in PAGE, outside this slice.
            "const releaseInfo={};\n"
            # Declared in load()'s section BELOW this slice (api_info fields);
            # served defaults. Without these stubs every state that reaches the
            # offered-updates branch throws ReferenceError — which is exactly
            # what happened when they were added below the slice unstubbed.
            "let canUpdate=true;\n"
            "let canControlG=false;\n"
            "let suiteDir='/srv/kapurlab/tools/bioinformatic_diagnostic_tools';\n"
            + body +
            f"\nrenderUpdates({payload});\n"
            "console.log(_el.className+'|'+(_el.innerHTML||_el.textContent));\n"
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

    HELD = ("{name:'amr_plus_gui:kraken2',label:'AMRFinderPlus — kraken2',"
            "installed:'2.1.3',latest:'2.17.1',update_available:false,held:true,"
            "held_reason:'2.17.1 was tried on this machine and could not be installed',"
            "held_fix:'bdtools install amr_plus_gui --rebuild',kind:'package'}")

    def test_up_to_date_renders_without_buttons(self):
        html = self.render(f"[{self.TOOL.replace('update_available:true',
                                                 'update_available:false')}]")
        self.assertIn("Up to date", html)
        self.assertNotIn("Install tool updates", html)

    def test_a_held_package_is_not_offered_but_is_counted(self):
        # The whole point: a package that cannot be installed here must not put the
        # banner back up — and must not vanish without trace either, or a dashboard
        # claiming "up to date" is claiming the newest version is installed.
        html = self.render(f"[{self.HELD}]")
        self.assertIn("Up to date", html)
        self.assertNotIn("Update conda packages", html)
        self.assertNotIn("Updates available", html)
        self.assertIn("1 analysis package is held", html)

    def test_held_packages_are_pluralised_and_do_not_inflate_the_count(self):
        second = self.HELD.replace("kraken2", "ncbi-amrfinderplus")
        html = self.render(f"[{self.HELD},{second},{self.PKG}]")
        # One real update -> the banner is up, and the two held ones are not in it.
        self.assertIn("Updates available (1)", html)
        self.assertIn("Update conda packages (1)", html)

    def test_a_recheck_does_not_redisplay_the_previous_answer(self):
        # `checked` stays true while a re-check runs (the cache still holds the old
        # answer), and the re-check that follows an update is exactly when the old
        # answer is wrong. Showing it there is what made an update look like it had
        # done nothing, every single time.
        out = self.render_state(f"{{checked:true,checking:true,items:[{self.PKG}]}}")
        self.assertIn("checking for updates", out)
        self.assertNotIn("Update conda packages", out)

    KEPT = ("{name:'kraken_id_parse_gui',label:'Kraken ID / Parse',"
            "installed:'v0.2.3',latest:'v0.2.4',update_available:false,"
            "newer_exists:true,report_only:true,kind:'tool'}")

    def test_a_report_only_tool_is_named_but_never_offered(self):
        # tools.yml decides what bdtools may change. A button here would lead
        # straight to the CLI refusing it — and one click on "Install tool updates"
        # used to reach every tool in the manifest, which is how a rebuild nobody
        # asked for broke a working install.
        html = self.render(f"[{self.KEPT}]")
        self.assertIn("Up to date", html)
        # What must not exist is an actionable CONTROL. The explainer panel is
        # allowed to name the button in prose (that is how it tells you what
        # opting in changes), so assert on the click handler, not the words.
        self.assertNotIn("applyUpdates(", html)
        self.assertNotIn("Updates available", html)
        self.assertIn("1 newer version is available and not offered", html)
        self.assertIn("v0.2.4", html)      # still says WHICH version

    def test_the_not_offered_line_explains_itself_without_interaction(self):
        # First a title= tooltip nobody could see, then a panel behind a click
        # nobody found — twice reported as "there are no directions". The
        # instructions must therefore be in the rendered page with no hover and
        # no click: visible by default, collapsible after the fact.
        html = self.render(f"[{self.KEPT}]")
        self.assertIn("toggleKeptPanel", html)
        self.assertNotIn('id="keptPanel" class="kpanel" style="display:none"', html)
        self.assertIn("To update one of these", html)
        self.assertIn("bin/bdtools update kraken_id_parse_gui --allow-report-only",
                      html)
        self.assertIn("v0.2.3", html)      # installed version, in the table row
        self.assertIn("Do you need to?", html)
        # And it must answer "how do I opt one in?", which is a tools.yml edit,
        # not a flag — the question the flag alone left unanswered.
        self.assertIn("To stop being asked", html)
        self.assertIn("updates: install", html)
        self.assertIn("tools.yml", html)
        # The cd path must be the real directory, not a placeholder.
        self.assertIn("/srv/kapurlab/tools/bioinformatic_diagnostic_tools", html)

    def test_a_report_only_tool_does_not_join_the_offered_group(self):
        html = self.render(f"[{self.TOOL},{self.KEPT}]")
        self.assertIn("Updates available (1)", html)
        self.assertIn("Install tool updates (1)", html)
        self.assertIn("vSNP3", html)
        self.assertNotIn("Kraken ID / Parse", html)

    def test_the_run_log_is_not_part_of_the_banner(self):
        # The log lives in #urun so that repainting the banner cannot erase the
        # record of the run that just finished.
        html = self.render(f"[{self.PKG}]")
        self.assertNotIn('id="ulog"', html)
        self.assertNotIn('id="udone"', html)


@unittest.skipUnless(NODE, "node is required to render the version block")
class CardVersionTests(unittest.TestCase):
    """The card footer is where a held package's reason has to be reachable.

    Once the banner stops offering it (which is the fix), the card is the only
    place left that can answer "why is this env still on 2.1.3?".
    """

    def render(self, tool):
        page = load_page()
        main = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
        body = main[main.index("const releaseInfo"):
                    main.index("// tool -> launch-time warning")]
        harness = (
            "function esc(s){return String(s).replace(/[&<>]/g,"
            "c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}\n"
            + body + f"\nconsole.log(versionBlock({tool}));\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(harness)
            path = fh.name
        proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        Path(path).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, f"versionBlock threw:\n{proc.stderr}")
        return proc.stdout

    def test_a_held_package_shows_the_recorded_reason_and_the_way_out(self):
        html = self.render(
            "{installed:true,version:'v0.3.3',packages:[{name:'kraken2',"
            "installed:'2.1.3',latest:'2.17.1',update_available:false,held:true,"
            "held_reason:'2.17.1 was tried on this machine and could not be installed',"
            "held_fix:'bdtools install amr_plus_gui --rebuild'}]}")
        self.assertIn("held (2.17.1)", html)
        self.assertIn("was tried on this machine", html)
        self.assertIn("bdtools install amr_plus_gui --rebuild", html)
        # Never an upgrade arrow: that is the badge for something you can act on.
        self.assertNotIn("↑2.17.1", html)

    def test_a_held_package_with_no_recorded_reason_still_says_something(self):
        html = self.render(
            "{installed:true,version:'v0.3.3',packages:[{name:'mlst',"
            "installed:'2.33.1',latest:'2.35.0',update_available:false,held:true}]}")
        self.assertIn("held (2.35.0)", html)
        self.assertIn("cannot take it", html)

    def test_an_available_update_still_shows_the_arrow(self):
        html = self.render(
            "{installed:true,version:'v0.4.36',packages:[{name:'vsnp3',"
            "installed:'3.35',latest:'3.36',update_available:true,held:false}]}")
        self.assertIn("↑3.36", html)


if __name__ == "__main__":
    unittest.main()
