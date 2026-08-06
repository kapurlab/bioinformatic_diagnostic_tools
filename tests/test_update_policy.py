#!/usr/bin/env python3
"""Only a tool tools.yml opts in may be changed by bdtools.

An env rebuild re-solves every dependency, and a conda transaction that dies
part-way rolls back into an env that may no longer run the tool. That happened:
a dashboard "Install tool updates" run — which targets `all` — rebuilt
kraken_id_parse_gui on macOS, hit an upstream spades post-link bug, and left a
working install broken. Being a release behind cannot do that.

So changing a tool is opt-in per tool (`updates: install`), the default is
report-only, and the gate has to hold in three places that could otherwise
disagree: the manifest, the shell gate every update path calls, and the dashboard
that decides which buttons to draw.
"""
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin/lib"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MANIFEST = _load("bdtools_manifest", ROOT / "bin/lib/manifest.py")


class ManifestPolicyTests(unittest.TestCase):
    def setUp(self):
        _, self.tools = MANIFEST.parse(str(ROOT / "tools.yml"))

    def test_every_tool_declares_the_policy_explicitly(self):
        # The default is safe, but a field that is usually absent is a field nobody
        # reads. Stating it on every tool is what makes "why was this rebuilt?"
        # answerable from the manifest alone.
        for rec in self.tools:
            with self.subTest(tool=rec.get("name")):
                self.assertIn(rec.get("updates"), ("install", "report"),
                              f"{rec.get('name')}: `updates:` must be install|report")

    def test_only_vsnp_is_opted_in(self):
        opted = [t["name"] for t in self.tools if t.get("updates") == "install"]
        self.assertEqual(opted, ["vsnp_gui"],
                         "opting another tool in is a deliberate decision — update "
                         "this test with it, and say why in tools.yml")


class ShellGateTests(unittest.TestCase):
    """common.sh:require_updatable — the one gate every update path calls."""

    def gate(self, tool, allow="0", explain="1"):
        return subprocess.run(
            ["bash", "-c",
             f'source "{ROOT}/bin/lib/common.sh" >/dev/null 2>&1; '
             f'require_updatable {tool} {allow} update {explain}'],
            capture_output=True, text=True, timeout=60)

    def test_an_opted_in_tool_passes(self):
        self.assertEqual(self.gate("vsnp_gui").returncode, 0)

    def test_a_report_only_tool_is_refused(self):
        proc = self.gate("kraken_id_parse_gui")
        self.assertNotEqual(proc.returncode, 0)
        out = proc.stdout + proc.stderr
        self.assertIn("report-only", out)
        # Refusing without saying how to proceed deliberately is just an obstacle.
        self.assertIn("--allow-report-only", out)

    def test_the_explicit_override_passes(self):
        self.assertEqual(self.gate("kraken_id_parse_gui", allow="1").returncode, 0)

    def test_a_sweep_refuses_quietly(self):
        # explain=0 is what `all` uses: eight tools times a five-line explanation is
        # a wall of warnings for a run in which nothing went wrong, and that is how
        # people learn to skip warnings. The caller summarises instead.
        proc = self.gate("kraken_id_parse_gui", explain="0")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual((proc.stdout + proc.stderr).strip(), "")

    def test_an_unknown_tool_is_refused_rather_than_defaulting_open(self):
        self.assertNotEqual(self.gate("no_such_tool").returncode, 0)


class DashboardAgreesWithTheGateTests(unittest.TestCase):
    """The buttons on screen must match what the CLI will actually do."""

    def setUp(self):
        os.environ.pop("BDTOOLS_MANIFEST", None)
        self.sc = _load("bdtools_suite_common", ROOT / "bin/lib/suite_common.py")

    def test_the_dashboard_reads_the_same_policy(self):
        self.assertTrue(self.sc.tool_is_updatable("vsnp_gui"))
        self.assertFalse(self.sc.tool_is_updatable("kraken_id_parse_gui"))

    def test_an_unknown_tool_is_not_updatable(self):
        self.assertFalse(self.sc.tool_is_updatable("no_such_tool"))

    def test_a_report_only_tool_is_reported_but_not_offered(self):
        rec = self.sc._parse_update_line(
            "kraken_id_parse_gui     pinned=v0.2.3   installed=v0.2.3   "
            "latest=v0.2.4   ↑ v0.2.4 available")
        self.assertTrue(rec["newer_exists"], "the newer release must still be seen")
        self.assertEqual(rec["latest"], "v0.2.4")
        self.assertFalse(rec["update_available"], "...and must never be offered")
        self.assertTrue(rec["report_only"])

    def test_an_opted_in_tool_is_still_offered(self):
        rec = self.sc._parse_update_line(
            "vsnp_gui     pinned=v0.4.36   installed=v0.4.36   "
            "latest=v0.4.37   ↑ v0.4.37 available")
        self.assertTrue(rec["update_available"])
        self.assertFalse(rec["report_only"])


if __name__ == "__main__":
    unittest.main()
