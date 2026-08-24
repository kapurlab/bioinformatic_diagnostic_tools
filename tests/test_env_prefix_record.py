#!/usr/bin/env python3
"""The env a tool was BUILT into beats the env a search happens to find first.

The 2026-08-24 Ames HPC case. `bdtools install irma_gui` succeeded and put a
complete env (plotly, weasyprint) at /project/shared/miniconda3/envs/irma_gui.
The launcher then resolved /home/tstuber/miniforge3/envs/irma_gui — an older env
by the same name in a different conda base, missing plotly — because the build
asks conda for the named env under whichever conda detect_conda found, while the
launcher takes the first base on its own probe list that has one. Those agree
only while the machine has exactly one such base.

Nothing errored. Doctor graded the env nobody would run, and IRMA would have
written its HTML report with every Coverage & Variants chart silently absent:
html_report.py wraps the plotly import in a broad except on purpose, so a run
never dies over a picture.
"""
import importlib.util
import os
import subprocess
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


TL = load_module("bdtools_tl_envrec", ROOT / "bin/lib/tool_launch.py")


class EnvPrefixRecord(unittest.TestCase):
    TOOL = "irma_gui"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "bdtools"
        (self.home / "checkouts" / self.TOOL / "backend").mkdir(parents=True)

        # Two conda bases, each with an env of the same name — the HPC's shape.
        self.built = base / "shared/envs" / self.TOOL          # what install produced
        self.other = base / "personal/envs" / self.TOOL        # what the probe finds first
        for env in (self.built, self.other):
            (env / "bin").mkdir(parents=True)
            (env / "bin/python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (env / "bin/python").chmod(0o755)

    def resolve(self):
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("BDTOOLS_", "CONDA_"))}
        clean.update({
            "BDTOOLS_HOME": str(self.home),
            "BDTOOLS_MANIFEST": str(ROOT / "tools.yml"),
            "CONDA_BASE": str(Path(self.tmp.name) / "personal"),   # the wrong one, first
        })
        with mock.patch.dict(os.environ, clean, clear=True):
            return TL.resolve(self.TOOL, 0)

    def record(self, prefix):
        d = self.home / "env-prefix"
        d.mkdir(parents=True, exist_ok=True)
        (d / self.TOOL).write_text(str(prefix) + "\n", encoding="utf-8")

    def test_without_a_record_the_probe_order_decides(self):
        """The bug, pinned so the fix below is measuring something real."""
        self.assertEqual(self.resolve()["env_dir"], str(self.other))

    def test_the_recorded_env_wins_over_the_probe_order(self):
        self.record(self.built)
        plan = self.resolve()
        self.assertEqual(plan["env_dir"], str(self.built))
        self.assertEqual([w for w in plan["warnings"] if "recorded" in w], [])

    def test_a_record_pointing_at_a_deleted_env_falls_back_and_says_so(self):
        """Advisory, never binding — a stale record must not wedge the tool. But
        an env that was built and is now gone means the tool is about to run from
        somewhere nobody chose, which is worth saying out loud."""
        self.record(Path(self.tmp.name) / "deleted/envs" / self.TOOL)
        plan = self.resolve()
        self.assertEqual(plan["env_dir"], str(self.other), "must fall back, not fail")
        self.assertTrue([w for w in plan["warnings"] if "recorded at install time" in w],
                        f"no warning about the dead record: {plan['warnings']}")

    def test_an_empty_or_absent_record_is_silent(self):
        for content in (None, "", "   \n"):
            with self.subTest(content=content):
                d = self.home / "env-prefix"
                d.mkdir(parents=True, exist_ok=True)
                f = d / self.TOOL
                f.unlink(missing_ok=True)
                if content is not None:
                    f.write_text(content, encoding="utf-8")
                plan = self.resolve()
                self.assertEqual(plan["env_dir"], str(self.other))
                self.assertEqual([w for w in plan["warnings"] if "recorded" in w], [])


class ShellSideRecord(unittest.TestCase):
    """common.sh writes the record install-local.sh calls it with; the launcher
    above reads it. Two files, one convention — worth asserting they meet."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "bdtools"
        self.env = Path(self.tmp.name) / "envs/irma_gui"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "bin/python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.env / "bin/python").chmod(0o755)

    def sh(self, script):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("BDTOOLS_")}
        clean["BDTOOLS_HOME"] = str(self.home)
        return subprocess.run(
            ["bash", "-c", f'source "{ROOT}/bin/lib/common.sh"\n{script}'],
            capture_output=True, text=True, env=clean, cwd=str(ROOT))

    def test_writes_where_the_launcher_reads(self):
        self.sh(f'record_env_prefix irma_gui "{self.env}"')
        written = self.home / "env-prefix/irma_gui"
        self.assertTrue(written.is_file(), "record not written")
        self.assertEqual(written.read_text(encoding="utf-8").strip(), str(self.env))
        # the exact path tool_launch derives
        self.assertEqual(written, Path(self.tmp.name) / "bdtools/env-prefix/irma_gui")

    def test_a_prefix_with_no_python_is_not_recorded(self):
        """Recording a directory that cannot run the tool would just move the
        wrong-env problem into the file meant to settle it."""
        self.sh(f'record_env_prefix irma_gui "{Path(self.tmp.name) / "nope"}"')
        self.assertFalse((self.home / "env-prefix/irma_gui").exists())

    def test_read_back_validates_the_prefix_still_works(self):
        self.sh(f'record_env_prefix irma_gui "{self.env}"')
        self.assertEqual(self.sh("recorded_env_prefix irma_gui").stdout.strip(), str(self.env))
        (self.env / "bin/python").unlink()
        self.assertEqual(self.sh("recorded_env_prefix irma_gui").stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
