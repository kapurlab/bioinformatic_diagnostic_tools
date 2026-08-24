#!/usr/bin/env python3
"""Regression tests for the foreign-site-path repair.

The bug these pin down, from a real 2026-08-24 HPC run: irma_gui's pipeline was
invoked with `--genoflu-db /srv/kapurlab/databases/genoflu/dependencies` on a
cluster that has no /srv/kapurlab. The literal had already been removed from the
tool's DEFAULTS, which fixed nothing on that machine — the value was persisted
in ~/.config/irma_gui/config.json before the fix, and every tool's
`load_config()` only setdefaults keys that are MISSING. A stale path therefore
survives the tool release, the pin bump and the env rebuild that were supposed
to cure it.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HYG = load_module("bdtools_config_hygiene", ROOT / "bin/lib/config_hygiene.py")
REQ = load_module("bdtools_requirements", ROOT / "bin/lib/requirements.py")
CHECK = load_module("bdtools_check", ROOT / "bin/lib/check.py")

FOREIGN_DB = "/srv/kapurlab/databases/genoflu/dependencies"


class ForeignPathRule(unittest.TestCase):
    """The three-clause rule, at its boundaries. Each case here is a way the
    check could be wrong in production, not a way it could be wrong in theory."""

    def test_another_sites_path_is_foreign(self):
        self.assertTrue(HYG.is_foreign(FOREIGN_DB, ["/home/tstuber"]))

    def test_a_path_under_a_declared_local_root_is_kept(self):
        """The lab server really does own /srv/kapurlab. During an NFS blip the
        path is unreadable and clause 2 alone would evict a CORRECT setting —
        which is the one moment it must not."""
        self.assertFalse(HYG.is_foreign(FOREIGN_DB, ["/srv/kapurlab"]))

    def test_an_existing_path_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(HYG.is_foreign(tmp, []))

    def test_a_directory_not_created_yet_is_kept(self):
        """A projects root the tool makes on first use is absent and correct.
        What separates it from another site's layout is that its parent is here."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(HYG.is_foreign(os.path.join(tmp, "projects"), []))

    def test_a_path_whose_whole_branch_is_missing_is_foreign(self):
        self.assertTrue(HYG.is_foreign("/srv/someothersite/refs/set", ["/home/x"]))

    def test_relative_values_and_non_paths_are_left_alone(self):
        for value in ("FLU", "", "ncbi", "relative/dir", "98.0"):
            self.assertFalse(HYG.is_foreign(value, []), value)

    def test_the_platform_temp_dir_is_not_foreign(self):
        """macOS hands each user /var/folders/<hash>/T. A scratch path parked
        there is expected to vanish and says nothing about which site wrote it."""
        scratch = os.path.join(tempfile.gettempdir(), "gone-" + "x" * 8)
        self.assertFalse(HYG.is_foreign(scratch, []))


class Sweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_home = Path(self.tmp.name) / "config"
        (self.cfg_home / "irma_gui").mkdir(parents=True)
        self.cfg = self.cfg_home / "irma_gui" / "config.json"
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.cfg_home),
            "BDTOOLS_HOME": str(Path(self.tmp.name) / "bdtools"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, cfg):
        self.cfg.write_text(json.dumps(cfg), encoding="utf-8")

    def read(self):
        return json.loads(self.cfg.read_text(encoding="utf-8"))

    def test_top_level_key_is_emptied_not_deleted(self):
        """THE re-infection guard. Deleting the key hands it straight back to
        `cfg.setdefault(k, v)` on the tool's next start — and on a tool whose
        DEFAULTS still name the literal (the whole population this repair is
        for) that writes the foreign path back in. "" is what every tool already
        documents as "not configured", and setdefault leaves it alone."""
        self.write({"genoflu_db": FOREIGN_DB, "genoflu_pident": 98.0})
        found = HYG.sweep("irma_gui", str(ROOT))
        self.assertEqual([f["key"] for f in found], ["genoflu_db"])
        after = self.read()
        self.assertIn("genoflu_db", after, "the key must survive, emptied")
        self.assertEqual(after["genoflu_db"], "")
        self.assertEqual(after["genoflu_pident"], 98.0)

    def test_nothing_is_lost(self):
        self.write({"genoflu_db": FOREIGN_DB})
        HYG.sweep("irma_gui", str(ROOT))
        record = self.read()[HYG.QUARANTINE_KEY]
        self.assertEqual([r["value"] for r in record["removed"]], [FOREIGN_DB])

    def test_list_members_are_dropped(self):
        with tempfile.TemporaryDirectory() as real:
            self.write({"saved_project_roots": [real, "/srv/kapurlab/projects"]})
            HYG.sweep("irma_gui", str(ROOT))
            self.assertEqual(self.read()["saved_project_roots"], [real])

    def test_idempotent(self):
        self.write({"genoflu_db": FOREIGN_DB})
        self.assertEqual(len(HYG.sweep("irma_gui", str(ROOT))), 1)
        self.assertEqual(HYG.sweep("irma_gui", str(ROOT)), [],
                         "a swept config must have nothing left to find")

    def test_scan_changes_nothing(self):
        self.write({"genoflu_db": FOREIGN_DB})
        before = self.cfg.read_text(encoding="utf-8")
        self.assertEqual(len(HYG.scan("irma_gui", str(ROOT))), 1)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), before)

    def test_a_site_that_owns_the_path_keeps_it(self):
        self.write({"genoflu_db": FOREIGN_DB})
        with mock.patch.dict(os.environ, {"BDTOOLS_SITE_ROOT": "/srv/kapurlab"}):
            self.assertEqual(HYG.sweep("irma_gui", str(ROOT)), [])
        self.assertEqual(self.read()["genoflu_db"], FOREIGN_DB)

    def test_missing_config_is_not_an_error(self):
        self.assertEqual(HYG.sweep("no_such_gui", str(ROOT)), [])


class LaunchPathRepair(unittest.TestCase):
    """The sweep runs where every launch goes through, and reports every time."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg_home = Path(self.tmp.name) / "config"
        (cfg_home / "irma_gui").mkdir(parents=True)
        self.cfg = cfg_home / "irma_gui" / "config.json"
        self.cfg.write_text(json.dumps({"genoflu_db": FOREIGN_DB}), encoding="utf-8")
        patch = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(cfg_home)})
        patch.start()
        self.addCleanup(patch.stop)
        self.TL = load_module("bdtools_tool_launch_hyg", ROOT / "bin/lib/tool_launch.py")

    def test_it_survives_the_discovery_pass_that_precedes_the_launch(self):
        """The dashboard resolves each tool once to DISCOVER it and again to
        LAUNCH it, and only the launch shows a human anything. Caching just
        "already swept" let the discovery pass consume the single report of a
        config it had rewritten: the repair happened and nobody was told.

        So resolve() keeps replaying, and delivery is what ends it — see
        consume_notices() and RepairIsANoticeNotAnError below."""
        try:
            first = self.TL.resolve("irma_gui", 0)
            second = self.TL.resolve("irma_gui", 8765)
        except RuntimeError as exc:
            self.skipTest(f"irma_gui not installed here: {exc}")
        for label, plan in (("discovery", first), ("launch", second)):
            hits = [n for n in plan["notices"] if "another deployment" in n]
            self.assertEqual(len(hits), 1, f"{label} pass reported {plan['notices']}")
        self.assertEqual(json.loads(self.cfg.read_text())["genoflu_db"], "")


class RepairIsANoticeNotAnError(unittest.TestCase):
    """A finished repair must not be dressed as a live failure, or repeated.

    The first version put this line in the card's error slot — the same red text
    as "kSNP4 not found, nothing will run" — and replayed it on every launch for
    the life of the dashboard session. The first person to see it asked whether
    their working tool was broken. That is the reaction that teaches people to
    ignore the slot, and the slot has to work when something really is wrong."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg_home = Path(self.tmp.name) / "config"
        (cfg_home / "irma_gui").mkdir(parents=True)
        (cfg_home / "irma_gui" / "config.json").write_text(
            json.dumps({"genoflu_db": FOREIGN_DB}), encoding="utf-8")
        patch = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(cfg_home)})
        patch.start()
        self.addCleanup(patch.stop)
        self.TL = load_module("bdtools_tl_notice", ROOT / "bin/lib/tool_launch.py")

    def plan(self, port=0):
        try:
            return self.TL.resolve("irma_gui", port)
        except RuntimeError as exc:
            self.skipTest(f"irma_gui not installed here: {exc}")

    def test_a_completed_repair_is_a_notice_never_a_warning(self):
        plan = self.plan()
        self.assertTrue([n for n in plan["notices"] if "another deployment" in n])
        self.assertEqual([w for w in plan["warnings"] if "another deployment" in w], [],
                         "the repair must not occupy the failure channel")

    def test_it_is_delivered_once(self):
        self.plan()                                   # discovery
        self.plan()                                   # discovery again
        first = self.TL.consume_notices("irma_gui")   # the launch that shows a human
        self.assertEqual(len(first), 1, "the one telling was swallowed")
        self.assertEqual(self.TL.consume_notices("irma_gui"), [], "shown twice")
        self.assertEqual(self.plan()["notices"], [], "still replaying after delivery")

    def test_the_run_log_keeps_it_even_though_the_card_shows_it_once(self):
        """A run's log is the permanent record of what was true when it ran, and
        "your configuration was changed before this run" belongs in it. One run,
        one log, so "once" costs nothing there."""
        plan = self.plan(8765)
        header = self.TL.log_header(plan)
        self.assertIn("# NOTE:", header)
        self.assertNotIn("# WARNING: irma_gui: removed", header)


class NoSitePathsInTheUmbrella(unittest.TestCase):
    """bdtools' own code must not carry another deployment's layout either.

    Ratchets on the specific files that FEED A RUN or a diagnostic report. The
    OOD card template is deliberately excluded: it still spells the reference
    path once as the anchor install-server.sh:subst rewrites, and the test below
    pins the behaviour that makes that safe instead."""

    PY_FILES = ("bin/lib/requirements.py", "bin/lib/config_hygiene.py",
                "bin/lib/site_paths.py", "bin/lib/db_config.py")
    SH_FILES = ("bin/test.sh",)

    @staticmethod
    def _string_literals(path):
        """Every string VALUE in a python file, minus the docstrings.

        Prose is not the problem and never was — these modules exist because of
        this bug and have to be able to describe it. What must not survive is a
        site path in something the code can hand to the filesystem. Working from
        the AST rather than from lines is what draws that line accurately;
        grepping the source text cannot, and a guard that fires on its own
        explanation is a guard people delete."""
        import ast
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and \
                        isinstance(body[0].value, ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
        return [(n.lineno, n.value) for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docstrings]

    def test_no_site_literal_reaches_a_run_or_a_report(self):
        import re
        pattern = re.compile(r"/srv/[A-Za-z0-9_-]+")
        offenders = []
        for rel in self.PY_FILES:
            for lineno, value in self._string_literals(ROOT / rel):
                if pattern.search(value):
                    offenders.append(f"{rel}:{lineno}: {value!r}")
        for rel in self.SH_FILES:
            for i, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line.split("#")[0]):
                    offenders.append(f"{rel}:{i}: {line.strip()}")
        self.assertEqual(offenders, [],
                         "resolve these through lib/site_paths.py instead of "
                         "naming one deployment's filesystem:\n  " + "\n  ".join(offenders))

    def test_database_defaults_resolve_under_this_machines_root(self):
        """requirements.py names a ROOT plus a relative path, never a path. The
        old literals made doctor report a missing database on every deployment
        but one, and name somebody else's server as the place to go and look."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"BDTOOLS_DB_ROOT": tmp}):
                for db in REQ.for_tool("kraken_id_parse_gui")["databases"]:
                    got = CHECK._default_path(db)
                    self.assertTrue(got.startswith(tmp),
                                    f"{db['config_key']} defaulted to {got!r}, "
                                    f"outside this machine's db root {tmp}")

    def test_an_unresolvable_root_yields_no_default(self):
        """No root, no default — doctor then says the key is unset, which is
        true and actionable, rather than naming a foreign directory."""
        self.assertEqual(CHECK._default_path({"default_under": ("nonesuch", "x")}), "")

    def test_ood_card_never_searches_an_unverified_site_root(self):
        """The session script may still SPELL the reference path (subst's
        anchor), but it must prove the directory is here before deriving
        anything from it — the python search used to glob
        /srv/kapurlab/tools/*/env/bin/python on every deployment on earth."""
        src = (ROOT / "ood/apps/bdtools_dashboard/template/script.sh.erb").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        self.assertIn('[ -d "${SITE_TOOLS_ROOT}" ] ||', code,
                      "the site root must be proven to exist before it is used")
        self.assertEqual(code.count("/srv/"), 1,
                         "exactly one occurrence: the subst anchor, nothing else")


if __name__ == "__main__":
    unittest.main()
