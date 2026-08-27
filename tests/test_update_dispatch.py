#!/usr/bin/env python3
"""Which command the dashboard's update button runs — `update` or `sync`.

The 2026-08-27 incident: a dashboard serving a SITE deployment (BDTOOLS_TOOLSDIR
pointing at /<root>/tools, tool checkouts as siblings of the umbrella) offered
"Install tool updates", and the run failed for all nine tools with

    refusing to update external checkout: <site>/vsnp_gui
    'bdtools update' force-refreshes managed personal checkouts only.

Both halves of the banner were behaving as written. The CHECK half resolves
tool_dir(), so it correctly read the site tree's versions and reported a real
"v0.4.79 -> v0.4.80 available". The APPLY half hardcoded `bdtools update`, which
owns only <BDTOOLS_HOME>/checkouts/<tool> and refuses a site tree on purpose —
such a tree can carry reviewed site/licensing commits. The two halves disagreed
about what kind of deployment they were looking at, so the button could not
succeed for any tool, and the log named the guardrail instead of the mismatch.

The rule pinned here: whoever may be moved, is moved by the verb that owns it —
`sync` (code only) for a site checkout, `update` (tag + environment rebuild) for
a managed personal one — and a set that is entirely one kind keeps its single
'all' form so `update all`'s per-tool leniency is preserved.
"""
import contextlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LIB_FILES = ("suite_common.py", "tool_launch.py", "site_paths.py", "manifest.py",
             "config_hygiene.py", "packages.py")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UpdateDispatch(unittest.TestCase):
    """A fake site layout, so nothing here depends on the host it runs on."""

    TOOLS = ("vsnp_gui", "irma_gui")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)

        # <root>/tools/{bioinformatic_diagnostic_tools, <tool>...} — a site tree.
        self.tools = base / "site/tools"
        self.umbrella = self.tools / "bioinformatic_diagnostic_tools"
        (self.umbrella / "bin/lib").mkdir(parents=True)
        for name in LIB_FILES:
            src = ROOT / "bin/lib" / name
            if src.exists():
                (self.umbrella / "bin/lib" / name).write_text(
                    src.read_text(encoding="utf-8"), encoding="utf-8")
        self.manifest = self.umbrella / "tools.yml"
        self.manifest.write_text((ROOT / "tools.yml").read_text(encoding="utf-8"),
                                 encoding="utf-8")

        self.home = base / "bdhome"
        (self.home / "checkouts").mkdir(parents=True)

    def make_site_checkouts(self, *names):
        for name in names:
            (self.tools / name / ".git").mkdir(parents=True)

    @contextlib.contextmanager
    def suite(self, **env):
        """suite_common loaded FROM the fake umbrella, with its tool list stubbed.

        Loading it from there is the point: REPO_DIR and tool_launch's notion of
        "my parent directory" both come from the file's location, which is exactly
        what decides site-vs-managed. list_tools() is stubbed because it shells out
        to `bdtools list`, which is not what this test is about.
        """
        clean = {k: v for k, v in os.environ.items() if not k.startswith("BDTOOLS_")}
        clean["BDTOOLS_HOME"] = str(self.home)
        clean.update(env)
        libdir = str(self.umbrella / "bin/lib")
        # tool_launch is imported lazily BY NAME inside suite_common, so the fake
        # copy has to win over any real one another test already imported.
        saved = sys.modules.pop("tool_launch", None)
        sys.path.insert(0, libdir)
        try:
            with mock.patch.dict(os.environ, clean, clear=True):
                sc = load_module("sc_probe", self.umbrella / "bin/lib/suite_common.py")
                sc.list_tools = lambda: list(self.TOOLS)
                yield sc
        finally:
            if libdir in sys.path:
                sys.path.remove(libdir)
            sys.modules.pop("tool_launch", None)
            if saved is not None:
                sys.modules["tool_launch"] = saved

    @staticmethod
    def verbs(cmds):
        """[(verb, target), ...] — the command list with the bdtools path dropped."""
        return [tuple(c[1:]) for c in cmds]

    # ---- the incident -------------------------------------------------------
    def test_a_site_deployment_syncs_instead_of_updating(self):
        self.make_site_checkouts(*self.TOOLS)
        with self.suite() as sc:
            self.assertEqual(sc.tool_update_mode(), "sync")
            self.assertEqual(self.verbs(sc.tool_update_commands("all")),
                             [("sync", "all")])

    def test_an_explicit_toolsdir_is_a_site_deployment_too(self):
        """The live case: the dashboard is launched with BDTOOLS_TOOLSDIR exported,
        so tool_dir never even reaches the sibling rule."""
        explicit = Path(self.tmp.name) / "explicit"
        for name in self.TOOLS:
            (explicit / name / ".git").mkdir(parents=True)
        with self.suite(BDTOOLS_TOOLSDIR=str(explicit)) as sc:
            self.assertEqual(sc.tool_update_mode(), "sync")
            self.assertEqual(self.verbs(sc.tool_update_commands("vsnp_gui")),
                             [("sync", "vsnp_gui")])

    def test_a_managed_deployment_is_untouched(self):
        """A laptop: no siblings, no override. This must stay exactly `update all`
        — including the single 'all' form, which is what lets check-updates.sh skip
        a report-only tool with a note instead of failing the run."""
        with self.suite() as sc:
            self.assertEqual(sc.tool_update_mode(), "update")
            self.assertEqual(self.verbs(sc.tool_update_commands("all")),
                             [("update", "all")])

    def test_a_mixed_tree_uses_the_right_verb_per_tool(self):
        """One tool deployed to the site tree, one only as a personal checkout."""
        self.make_site_checkouts("vsnp_gui")
        with self.suite() as sc:
            self.assertEqual(sc.tool_source_kind("vsnp_gui"), "site")
            self.assertEqual(sc.tool_source_kind("irma_gui"), "managed")
            self.assertEqual(self.verbs(sc.tool_update_commands("all")),
                             [("sync", "vsnp_gui"), ("update", "irma_gui")])

    def test_a_frozen_tool_is_dropped_from_a_mixed_all_run(self):
        """`updates: report` is a deliberate freeze. Exploding an 'all' run into
        per-tool commands must not turn that freeze into a reported failure."""
        self.make_site_checkouts("vsnp_gui")
        text = self.manifest.read_text(encoding="utf-8")
        head, sep, tail = text.partition("  - name: irma_gui\n")
        self.assertTrue(sep, "tools.yml no longer lists irma_gui")
        # REPLACE the policy line inside that record — do not prepend one. The
        # manifest parser keeps the LAST value for a repeated key, so an inserted
        # line is silently overwritten by the record's own `updates: install`.
        self.assertIn("    updates: install\n", tail)
        tail = tail.replace("    updates: install\n", "    updates: report\n", 1)
        self.manifest.write_text(head + sep + tail, encoding="utf-8")
        with self.suite() as sc:
            self.assertEqual(self.verbs(sc.tool_update_commands("all")),
                             [("sync", "vsnp_gui")])

    def test_an_uninstalled_tool_stays_with_update(self):
        """Nothing checked out anywhere resolves to the managed path, so it keeps
        the verb whose output says "not installed — run bdtools install"."""
        with self.suite() as sc:
            self.assertEqual(sc.tool_source_kind("no_such_gui"), "managed")
            self.assertEqual(self.verbs(sc.tool_update_commands("no_such_gui")),
                             [("update", "no_such_gui")])

    # ---- the two halves must agree ------------------------------------------
    def test_the_banner_mode_matches_the_command_that_will_run(self):
        """update_mode drives the wording; if it ever says 'update' while the
        command list says sync (or the reverse), the banner is lying again."""
        for label, site in (("site", self.TOOLS), ("managed", ())):
            with self.subTest(deployment=label):
                self.setUp()
                self.make_site_checkouts(*site)
                with self.suite() as sc:
                    mode = sc.tool_update_mode()
                    verbs = {v for v, _ in self.verbs(sc.tool_update_commands("all"))}
                    self.assertEqual({mode}, verbs)


if __name__ == "__main__":
    unittest.main()
