#!/usr/bin/env python3
"""Every update path reads the same per-tool policy, and they cannot disagree.

`updates: install` is the suite-wide state (2026-08-20): the dashboard shows
each tool's newest release, so an update that then refuses to apply would
contradict the screen — the version shown and the version an update produces
must be the same thing. `updates: report` remains a supported per-tool freeze
(mid-validation, an install being debugged): read and displayed, never changed,
`--allow-report-only` the deliberate override.

History: from 2026-08-06 to 2026-08-20 report-only was the default, after an
update-all rebuilt kraken_id_parse_gui on macOS, hit an upstream spades
post-link bug, and left a working install broken. The protections that make
all-in acceptable are per-tool subshells, recorded build failures, and env
snapshots (restore-env) — see the tools.yml header.

The gate has to hold in three places that could otherwise disagree: the
manifest, the shell gate every update path calls, and the dashboard that
decides which buttons to draw. The report-only side is exercised against a
synthetic manifest (BDTOOLS_MANIFEST) because the real one no longer carries a
frozen tool.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
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

# A manifest with one frozen tool, so the report-only machinery stays tested
# now that the real tools.yml opts everything in.
FIXTURE = """\
suite_version: "0.0.0"

tools:
  - name: alpha_gui
    repo: https://example.invalid/alpha_gui.git
    version: v0.1.0
    updates: install

  - name: frozen_gui
    repo: https://example.invalid/frozen_gui.git
    version: v0.1.0
    updates: report
"""


def _write_fixture():
    fd, path = tempfile.mkstemp(prefix="tools_policy_", suffix=".yml")
    with os.fdopen(fd, "w") as fh:
        fh.write(FIXTURE)
    return path


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

    def test_every_tool_is_opted_in(self):
        frozen = [t["name"] for t in self.tools if t.get("updates") != "install"]
        self.assertEqual(frozen, [],
                         "freezing a tool back to report-only is a deliberate "
                         "decision — update this test with it, and say why in "
                         "tools.yml")


class ShellGateTests(unittest.TestCase):
    """common.sh:require_updatable — the one gate every update path calls."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = _write_fixture()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.fixture)

    def gate(self, tool, allow="0", explain="1", manifest=None):
        env = dict(os.environ)
        env.pop("BDTOOLS_MANIFEST", None)
        if manifest:
            env["BDTOOLS_MANIFEST"] = manifest
        return subprocess.run(
            ["bash", "-c",
             f'source "{ROOT}/bin/lib/common.sh" >/dev/null 2>&1; '
             f'require_updatable {tool} {allow} update {explain}'],
            capture_output=True, text=True, timeout=60, env=env)

    def test_every_real_tool_passes(self):
        _, tools = MANIFEST.parse(str(ROOT / "tools.yml"))
        for rec in tools:
            with self.subTest(tool=rec["name"]):
                self.assertEqual(self.gate(rec["name"]).returncode, 0)

    def test_a_report_only_tool_is_refused(self):
        proc = self.gate("frozen_gui", manifest=self.fixture)
        self.assertNotEqual(proc.returncode, 0)
        out = proc.stdout + proc.stderr
        self.assertIn("report-only", out)
        # Refusing without saying how to proceed deliberately is just an obstacle.
        self.assertIn("--allow-report-only", out)

    def test_the_explicit_override_passes(self):
        self.assertEqual(
            self.gate("frozen_gui", allow="1", manifest=self.fixture).returncode, 0)

    def test_a_sweep_refuses_quietly(self):
        # explain=0 is what `all` uses: a five-line explanation per skipped tool is
        # a wall of warnings for a run in which nothing went wrong, and that is how
        # people learn to skip warnings. The caller summarises instead.
        proc = self.gate("frozen_gui", explain="0", manifest=self.fixture)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual((proc.stdout + proc.stderr).strip(), "")

    def test_an_unknown_tool_is_refused_rather_than_defaulting_open(self):
        self.assertNotEqual(self.gate("no_such_tool").returncode, 0)


class DashboardAgreesWithTheGateTests(unittest.TestCase):
    """The buttons on screen must match what the CLI will actually do."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = _write_fixture()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.fixture)

    def setUp(self):
        self._saved = os.environ.pop("BDTOOLS_MANIFEST", None)
        self.sc = _load("bdtools_suite_common", ROOT / "bin/lib/suite_common.py")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("BDTOOLS_MANIFEST", None)
        else:
            os.environ["BDTOOLS_MANIFEST"] = self._saved

    def test_the_dashboard_reads_the_same_policy(self):
        self.assertTrue(self.sc.tool_is_updatable("vsnp_gui"))
        self.assertTrue(self.sc.tool_is_updatable("kraken_id_parse_gui"))
        os.environ["BDTOOLS_MANIFEST"] = self.fixture
        self.assertFalse(self.sc.tool_is_updatable("frozen_gui"))

    def test_an_unknown_tool_is_not_updatable(self):
        self.assertFalse(self.sc.tool_is_updatable("no_such_tool"))

    def test_a_report_only_tool_is_reported_but_not_offered(self):
        os.environ["BDTOOLS_MANIFEST"] = self.fixture
        rec = self.sc._parse_update_line(
            "frozen_gui     pinned=v0.1.0   installed=v0.1.0   "
            "latest=v0.2.0   ↑ v0.2.0 available")
        self.assertTrue(rec["newer_exists"], "the newer release must still be seen")
        self.assertEqual(rec["latest"], "v0.2.0")
        self.assertFalse(rec["update_available"], "...and must never be offered")
        self.assertTrue(rec["report_only"])

    def test_an_opted_in_tool_is_still_offered(self):
        rec = self.sc._parse_update_line(
            "kraken_id_parse_gui     pinned=v0.2.3   installed=v0.2.3   "
            "latest=v0.2.4   ↑ v0.2.4 available")
        self.assertTrue(rec["update_available"])
        self.assertFalse(rec["report_only"])


if __name__ == "__main__":
    unittest.main()
