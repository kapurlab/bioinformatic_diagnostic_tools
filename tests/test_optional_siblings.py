#!/usr/bin/env python3
"""A sibling a tool degrades without is a note, not a "needs setup" finding.

vSNP's only sibling is the Kraken GUI, reached from Step 1 Results to clean the
reads of a low-mapping sample. Grading a missing Kraken env as a fault put a
"needs setup" badge on every vSNP-only install and told a first-time user to
install a second tool before a first one that runs fine could run. Required
siblings keep the finding: an amr_plus_gui without mlst_gui's env cannot type
an isolate at all.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin/lib"))
import check as CHECK  # noqa: E402
import requirements as REQ  # noqa: E402


class OptionalSiblingTests(unittest.TestCase):

    def _report(self, tool, present):
        """run_checks for a tool whose own env is healthy; `present` names the
        siblings that resolve to an env."""
        def handoff(name):
            return (f"/envs/{name}", f"/tools/{name}") if name in present else (None, None)

        with tempfile.TemporaryDirectory() as tmp:
            env_py = Path(tmp) / "env/bin/python"
            env_py.parent.mkdir(parents=True)
            env_py.touch()
            (Path(tmp) / f"tools/{tool}").mkdir(parents=True)
            with mock.patch.object(CHECK, "env_python_unrunnable", return_value=None), \
                    mock.patch.object(CHECK, "check_modules", return_value={}), \
                    mock.patch.object(CHECK, "has_binary", return_value=True), \
                    mock.patch.object(CHECK, "resolve_asset_dirs", return_value=([], [])), \
                    mock.patch.object(CHECK, "stale_sibling_packages", return_value=[]), \
                    mock.patch.object(CHECK, "sibling_handoff", side_effect=handoff), \
                    mock.patch("os.path.isdir", return_value=True):
                return CHECK.run_checks(tool, str(env_py), "env",
                                        tool_dir=str(Path(tmp) / f"tools/{tool}"))

    def test_vsnp_declares_kraken_as_optional(self):
        spec = REQ.for_tool("vsnp_gui")
        self.assertIn("kraken_id_parse_gui", spec["sibling_tools"])
        self.assertIn("kraken_id_parse_gui", spec["optional_siblings"])

    def test_missing_optional_sibling_leaves_the_tool_ready(self):
        status, lines, issues, notes = self._report("vsnp_gui", present=set())
        self.assertEqual(issues, [], "a missing optional sibling must not be a finding")
        self.assertEqual(status, "ok")
        note = " | ".join(notes)
        self.assertIn("optional kraken_id_parse_gui not installed", note)
        self.assertIn("Run Kraken", note, "the note must say what stops working")
        self.assertIn("bdtools install kraken_id_parse_gui", note)
        self.assertFalse(any(sym == CHECK.BAD for sym, _t, _f in lines))

    def test_present_optional_sibling_is_still_reported_ok(self):
        status, lines, issues, _notes = self._report(
            "vsnp_gui", present={"kraken_id_parse_gui"})
        self.assertEqual((status, issues), ("ok", []))
        self.assertTrue(any(sym == CHECK.OK and "sibling kraken_id_parse_gui env" in t
                            for sym, t, _f in lines))

    def test_required_sibling_is_still_a_finding(self):
        status, _lines, issues, _notes = self._report(
            "amr_plus_gui", present={"kraken_id_parse_gui"})
        self.assertEqual(status, "issues")
        self.assertIn("sibling mlst_gui env missing", [i["label"] for i in issues])


if __name__ == "__main__":
    unittest.main()
