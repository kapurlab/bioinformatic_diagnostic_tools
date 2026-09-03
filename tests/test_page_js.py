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

    # Separates the banner from the foot-of-page panel in one captured line, so
    # a test can assert WHERE something rendered and not merely that it exists.
    HOST_MARK = "@@KEPT_HOST@@"

    def render(self, items):
        return self.render_state(f"{{checked:true,items:{items}}}")

    def parts(self, items):
        """(banner, foot-of-page panel) for one render."""
        out = self.render(items)
        banner, _, host = out.partition(self.HOST_MARK)
        return banner, host

    def render_state(self, payload):
        page = load_page()
        main = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
        body = main[main.index("function renderUpdates"):
                    main.index("// Poll the cached result")]
        harness = (
            "function esc(s){return String(s).replace(/[&<>]/g,"
            "c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}\n"
            # One stub element PER id. A single shared stub used to be enough,
            # but the banner (#updates) and the kept-updates panel
            # (#keptPanelHost, at the foot of the page) are now two elements —
            # and with one shared stub whichever was written last silently
            # became "the banner", so an assertion could pass against the wrong
            # element's HTML entirely.
            "const _els={};\n"
            "const _get=id=>(_els[id]=_els[id]||"
            "{className:'',innerHTML:'',textContent:'',style:{}});\n"
            "document={getElementById:_get};\n"
            "const _el=_get('updates');\n"
            # Defined above renderUpdates in PAGE, outside this slice; the
            # panel is written through it.
            "function setKeptPanel(h){_get('keptPanelHost').innerHTML=h||'';}\n"
            # Declared just above renderUpdates in PAGE, outside this slice.
            "const releaseInfo={};\n"
            # Declared in load()'s section BELOW this slice (api_info fields);
            # served defaults. Without these stubs every state that reaches the
            # offered-updates branch throws ReferenceError — which is exactly
            # what happened when they were added below the slice unstubbed.
            "let canUpdate=true;\n"
            "let canControlG=false;\n"
            # Written by renderUpdates, declared below this slice (like canUpdate):
            # it carries the verb into applyUpdates' confirm text.
            "let updateModeG='update';\n"
            "let suiteDir='/srv/kapurlab/tools/bioinformatic_diagnostic_tools';\n"
            + body +
            f"\nrenderUpdates({payload});\n"
            "console.log(_el.className+'|'+(_el.innerHTML||_el.textContent)"
            f"+'{self.HOST_MARK}'+_get('keptPanelHost').innerHTML);\n"
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

    def test_a_site_deployment_offers_sync_not_a_rebuild(self):
        """update_mode:'sync' — the deployment `bdtools update` refuses.

        The banner must offer the verb that can actually run there and must not
        promise an environment rebuild that `bdtools sync` does not do. This is the
        2026-08-27 incident rendered: nine tools offered, nine refusals.
        """
        html = self.render_state(
            f"{{checked:true,update_mode:'sync',items:[{self.TOOL}]}}")
        self.assertIn("Deploy tool updates", html)
        self.assertNotIn("Install tool updates", html)
        self.assertIn("bdtools sync", html)
        self.assertNotIn("Installing rebuilds environments", html)
        # Envs and OOD cards are NOT moved by sync, so the page has to say what
        # does move them — otherwise "code only" reads as "nothing else needed".
        self.assertIn("install --server", html)

    def test_a_managed_deployment_keeps_the_rebuild_wording(self):
        """No update_mode (or 'update'): unchanged from before the sync split."""
        for payload in (f"{{checked:true,items:[{self.TOOL}]}}",
                        f"{{checked:true,update_mode:'update',items:[{self.TOOL}]}}"):
            with self.subTest(payload=payload):
                html = self.render_state(payload)
                self.assertIn("Install tool updates", html)
                self.assertNotIn("Deploy tool updates", html)
                self.assertIn("Installing rebuilds environments", html)

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

    def test_the_directions_render_at_the_foot_of_the_page_not_in_the_banner(self):
        # The panel is a screenful of prose about a decision made rarely. Above
        # the tool cards it pushed the tools off the screen on arrival, so it
        # renders into #keptPanelHost at the foot of the page instead. The
        # banner keeps the one-line notice and a link down to it — the
        # directions must stay one click from the top, never a hover again.
        banner, host = self.parts(f"[{self.KEPT}]")
        self.assertIn("1 newer version is available and not offered", banner)
        self.assertIn("How to update", banner)
        self.assertIn("jumpToKeptPanel", banner)
        self.assertNotIn("To update one of these", banner)
        self.assertNotIn("kpanel", banner)
        self.assertIn('id="keptPanel"', host)
        self.assertIn("To update one of these", host)
        self.assertIn("bin/bdtools update kraken_id_parse_gui --allow-report-only",
                      host)
        # Down here the panel is on its own, so it has to say what it is.
        self.assertIn("Newer versions that are not offered", host)
        # Hiding is offered ON the panel, where the thing being hidden is
        # visible; a "Hide" at the top would act on something off-screen.
        self.assertIn("toggleKeptPanel", host)

    def test_a_repaint_leaves_no_stale_panel_at_the_foot_of_the_page(self):
        # renderUpdates repaints on every poll. A tool that has just been
        # updated (or a check that came back blind) must not leave its
        # instructions standing at the bottom of the page.
        for label, payload in (
                ("an update is offered", f"[{self.PKG}]"),
                ("nothing is kept", f"[{self.HELD}]"),
        ):
            with self.subTest(state=label):
                _banner, host = self.parts(payload)
                self.assertEqual(host.strip(), "",
                                 "the foot-of-page panel should be empty here")
        # …and while a check is in flight (an early return).
        out = self.render_state(f"{{checked:true,checking:true,items:[{self.KEPT}]}}")
        _banner, _, host = out.partition(self.HOST_MARK)
        self.assertEqual(host.strip(), "", "a check in flight shows no panel")

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


@unittest.skipUnless(NODE, "node is required to run the headline split")
class CardHeadlineTests(unittest.TestCase):
    """The FUNCTION is the card's headline and the tool's name sits beneath it.

    The blurbs were written for the old order (name first) and often end by
    repeating the tool in parentheses; splitBlurb() decides, per blurb, whether
    that parenthetical is dropped, becomes the tool line, or stays as a
    qualifier. These are the nine real blurbs, so a data edit that changes the
    outcome shows up here rather than on the dashboard.
    """

    def split(self, label, blurb):
        page = load_page()
        main = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
        body = main[main.index("function splitBlurb"):main.index("async function load(){")]
        harness = (
            "function esc(s){return String(s).replace(/[&<>]/g,"
            "c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}\n"
            + body
            + f"\nconst r=splitBlurb({{label:{label!r},blurb:{blurb!r}}});"
            "r.html=headlineHtml(r.head);console.log(JSON.stringify(r));\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(harness)
            path = fh.name
        proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        Path(path).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, f"splitBlurb threw:\n{proc.stderr}")
        import json
        return json.loads(proc.stdout)

    def test_a_parenthetical_that_repeats_the_tool_is_dropped(self):
        for label, blurb, head in (
                ("AMRFinderPlus", "Antimicrobial resistance genes (AMRFinderPlus)",
                 "Antimicrobial resistance genes"),
                ("kSNP", "Reference-free SNP phylogeny (kSNP4)", "Reference-free SNP phylogeny"),
                ("Kraken ID / Parse", "Taxonomic identification (Kraken2)", "Taxonomic identification"),
        ):
            with self.subTest(label=label):
                r = self.split(label, blurb)
                self.assertEqual(r["head"], head)
                self.assertEqual(r["tool"], label)
                self.assertEqual(r["qual"], "")

    def test_a_parenthetical_that_contains_the_label_becomes_the_tool_line(self):
        r = self.split("IRMA", "Influenza / SARS-CoV-2 assembly (CDC IRMA)")
        self.assertEqual(r["head"], "Influenza / SARS-CoV-2 assembly")
        self.assertEqual(r["tool"], "CDC IRMA")
        self.assertEqual(r["qual"], "")
        # …and the organism name never breaks at its hyphen.
        self.assertIn('<span class="nowrap">SARS-CoV-2</span>', r["html"])

    def test_a_parenthetical_that_says_something_new_is_a_qualifier(self):
        r = self.split("vSNP3", "SNP analysis & phylogeny (High resolution genotyping)")
        self.assertEqual(r["head"], "SNP analysis & phylogeny")
        self.assertEqual(r["tool"], "vSNP3")
        self.assertEqual(r["qual"], "High resolution genotyping")
        self.assertIn("&amp;", r["html"])          # escaped on the way into innerHTML

    def test_a_parenthetical_mid_sentence_is_left_alone(self):
        r = self.split("Bovine MHC Typer", "Bovine MHC (BoLA) typing from Nanopore amplicons")
        self.assertEqual(r["head"], "Bovine MHC (BoLA) typing from Nanopore amplicons")
        self.assertEqual(r["tool"], "Bovine MHC Typer")
        self.assertEqual(r["qual"], "")

    def test_a_blurb_without_a_parenthetical_is_used_whole(self):
        r = self.split("GenoFLU", "H5 2.3.4.4b influenza genotyping")
        self.assertEqual(r["head"], "H5 2.3.4.4b influenza genotyping")
        self.assertEqual(r["tool"], "GenoFLU")

    def test_a_plain_installed_tool_carries_no_pill(self):
        # The version stamp already says the tool is here; a pill is reserved
        # for a state the reader must know about (running, starting, updating,
        # needs setup, not installed).
        page = load_page()
        main = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
        load = main[main.index("async function load(){"):main.index("async function recheck(")]
        self.assertIn("t.installed ? '' : `<span class=\"pill\">not installed</span>`", load)
        self.assertNotIn("'installed':", load)

    def test_the_card_template_puts_the_function_before_the_tool(self):
        page = load_page()
        main = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
        load = main[main.index("async function load(){"):main.index("async function recheck(")]
        self.assertLess(load.index('<div class="blurb">'), load.index('<div class="name">'))
        # The versions follow the tool line, and the notices come after the
        # action row — full-width strips, so they never break the two-column card.
        self.assertLess(load.index('<div class="name">'), load.index("${versionBlock(t)}"))
        self.assertLess(load.index('<div class="row">'), load.index("${devBlock(t)}"))


class PolicyPopoverTests(unittest.TestCase):
    """The version policy is one line at the foot of the page, opened on demand.

    A hover-only disclosure is unreachable from a keyboard or a touch screen,
    so the markup must also open on focus and on click, and the trigger must be
    a real button that reports its state.
    """

    def test_the_policy_is_a_single_line_with_the_text_folded_behind_it(self):
        page = load_page()
        self.assertIn('id="poltrig"', page)
        self.assertIn(">Software version policy</button>", page)
        self.assertIn('aria-expanded="false"', page)
        self.assertIn('aria-controls="policy"', page)
        # The full statement and the legend are still on the page, verbatim.
        self.assertIn("A pin is a quality control, not a limitation", page)
        self.assertIn('class="plegend"', page)
        for badge in ("↑ 1.3.5", "held (2.17.1)", "≠ pinned 2.17.1", "no badge"):
            self.assertIn(badge, page)

    def test_it_opens_on_hover_focus_and_click(self):
        page = load_page()
        css = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
        # ONE attribute drives the display…
        self.assertIn(".pol .policy{display:none", css)
        self.assertIn('.pol[data-open="true"] .policy{display:', css)
        # …and no CSS-only path may open the panel behind the script's back: a
        # :hover/:focus-within rule kept it standing after Escape and a second
        # click while the button still had focus, with aria-expanded="false" on
        # a visible panel.
        self.assertNotIn(".pol:hover", css)
        self.assertNotIn(".pol:focus-within", css)
        main = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
        for needle in ("function togglePolicy", "function setPolicy", "function fitPolicy",
                       "'mouseenter'", "'mouseleave'", "'focusin'", "'focusout'",
                       "e.key === 'Escape'", "aria-expanded"):
            self.assertIn(needle, main)

    def test_it_cannot_open_above_the_top_of_a_short_page(self):
        # The line is the last thing on the page and the panel opens upward. With
        # two tools there is less page above the line than the panel is tall, and
        # a box above y=0 cannot be scrolled to. The opening measures the room
        # and caps the panel to it, or flips it below the line.
        page = load_page()
        css = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
        self.assertIn("var(--pol-room", css)
        self.assertIn('.pol[data-flip="true"] .policy{bottom:auto;top:100%}', css)
        main = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
        self.assertIn("--pol-room", main)
        self.assertIn("dataset.flip", main)


class ButtonStateTests(unittest.TestCase):
    def test_disabled_outranks_every_button_variant(self):
        # button.open (green) and .updates button.u (accent) set their own ground.
        # The redesign gave disabled buttons a muted ink, so a disabled Open
        # button that kept its green ground was unreadable (1.1:1) for as long
        # as it read Starting… — the disabled rule must come last and be at
        # least as specific as every variant.
        page = load_page()
        css = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
        disabled = css.rindex("button:disabled,button:disabled:hover")
        self.assertGreater(disabled, css.index("button.open:hover"))
        self.assertGreater(disabled, css.index(".updates button.u:hover"))
        self.assertIn("button.open:disabled", css)
        self.assertIn(".updates button.u:disabled", css)

    def test_the_policy_is_the_last_thing_on_the_page(self):
        # The kept-updates panel host stays ABOVE the policy line: the banner's
        # "How to update ↓" link jumps to it, and a jump target below a popover
        # trigger would open the policy under the reader's pointer.
        page = load_page()
        self.assertLess(page.index('id="keptPanelHost"'), page.index('id="poltrig"'))


class PageFootTests(unittest.TestCase):
    """The header is the title; everything about the dashboard itself sits below the tools."""

    def test_the_header_is_only_the_title(self):
        page = load_page()
        body = page[page.index("<body>"):]
        self.assertIn('<span class="tag">bdtools</span>', body)
        self.assertNotIn("Pick a tool to launch", body)

    def test_controls_and_update_state_sit_below_the_grid(self):
        page = load_page()
        grid = page.index('id="grid"')
        for marker in ('class="theme-switch"', 'id="ctl"', 'id="updates"', 'id="urun"',
                       'id="keptPanelHost"', 'id="poltrig"'):
            self.assertGreater(page.index(marker), grid, marker)
        # The expired-sign-in notice is the one exception: it stays above the
        # cards because nothing below it is live until the reader acts on it.
        self.assertLess(page.index('id="sessexp"'), grid)


if __name__ == "__main__":
    unittest.main()
