#!/usr/bin/env python3
"""A module that is INSTALLED but fails to import must diagnose itself.

This is the failure that left a kraken_id_parse_gui unusable with no way out
except someone reading the traceback by hand:

    doctor:    ✗ python modules missing: allel
    remedy:    conda install ... scikit-allel
    conda:     All requested packages already installed.

scikit-allel WAS installed. `import allel` reaches `allel/model/dask.py`, which
imports dask, and dask 2023.3.0 decorates with `np.round_` — removed in NumPy
2.0. The env had numpy 2.2.6. So the import died three packages deep while every
package the remedy could name was present and correct.

The old check collapsed both faults into one message: it caught `Exception` and
appended the module name, discarding the reason. "Not installed" and "installed
but raises" then reached the dashboard identically, and the remedy was chosen
from the install tables — which answers only the first. The fallback remedy
(`bdtools install <tool> --fresh`) is worse than useless for this fault: the
same spec and channels re-solve to the same incompatible pair, so the rebuild
reproduces the failure it was supposed to repair.

What fixes it is keeping the traceback: report the real error, say the module is
present, and name the package in the middle of the chain — the stale caller —
rather than the module (already there) or the deepest package (the one that
removed the API; downgrading that is how you get an env nobody can reproduce).

These tests build a real three-package chain on disk and run the real probe
through a real interpreter.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin/lib"))
import check as CHECK  # noqa: E402

BASH = shutil.which("bash")

# The shape of the real failure: topmod -> midpkg -> deeppkg, where deeppkg has
# removed what midpkg reaches for. Mirrors allel -> dask -> numpy.
TOPMOD = "from midpkg import thing\n"
MIDPKG = "import deeppkg\nthing = deeppkg.round_\n"
DEEPPKG = (
    "def __getattr__(name):\n"
    "    raise AttributeError(\"`dp.round_` was removed in the DeepPkg 2.0 \"\n"
    "                         \"release. Use dp.round instead.\")\n"
)


class ImportDiagnosisTests(unittest.TestCase):
    """check_modules must report WHY, not just WHICH."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        sp = self.tmp / "site-packages"
        for name, body in (("topmod", TOPMOD), ("midpkg", MIDPKG),
                           ("deeppkg", DEEPPKG)):
            (sp / name).mkdir(parents=True)
            (sp / name / "__init__.py").write_text(body)
        self.site_packages = sp
        # A stand-in for <env>/bin/python: the real interpreter, with the fake
        # site-packages on its path. check_modules only ever execs this path, so
        # this exercises the true subprocess route rather than mocking it.
        envbin = self.tmp / "env/bin"
        envbin.mkdir(parents=True)
        self.env_py = envbin / "python"
        self.env_py.write_text(
            "#!/bin/sh\n"
            f'PYTHONPATH="{sp}" exec "{sys.executable}" "$@"\n')
        self.env_py.chmod(0o755)

    # ---- the probe ----------------------------------------------------------

    @unittest.skipUnless(BASH, "a POSIX shell is required for the env stub")
    def test_a_broken_import_is_not_reported_as_missing(self):
        out = CHECK.check_modules(str(self.env_py), ["topmod"])
        self.assertIn("topmod", out)
        info = out["topmod"]
        self.assertFalse(info["absent"],
                         "an installed module that raises is NOT absent — "
                         "calling it missing is what produced a no-op install")
        self.assertIn("round_", info["error"])
        self.assertIn("AttributeError", info["error"])

    @unittest.skipUnless(BASH, "a POSIX shell is required for the env stub")
    def test_the_chain_names_the_intermediate_package(self):
        info = CHECK.check_modules(str(self.env_py), ["topmod"])["topmod"]
        self.assertEqual(info["chain"], ["topmod", "midpkg", "deeppkg"],
                         "the chain is the diagnosis: the deepest frame raises, "
                         "the one above it is stale")

    @unittest.skipUnless(BASH, "a POSIX shell is required for the env stub")
    def test_a_genuinely_absent_module_is_still_absent(self):
        # The other half of the split must keep working, or every real missing
        # package stops being offered its install.
        info = CHECK.check_modules(str(self.env_py), ["nosuchmodule"])["nosuchmodule"]
        self.assertTrue(info["absent"])
        self.assertIn("ModuleNotFoundError", info["error"])

    @unittest.skipUnless(BASH, "a POSIX shell is required for the env stub")
    def test_a_healthy_module_is_not_reported_at_all(self):
        self.assertEqual(CHECK.check_modules(str(self.env_py), ["json"]), {})

    @unittest.skipUnless(BASH, "a POSIX shell is required for the env stub")
    def test_one_failure_does_not_hide_the_others(self):
        out = CHECK.check_modules(str(self.env_py),
                                  ["topmod", "json", "nosuchmodule"])
        self.assertEqual(sorted(out), ["nosuchmodule", "topmod"])

    def test_an_unrunnable_interpreter_reports_every_module(self):
        # Preserved from the old contract: if we cannot run the interpreter we
        # cannot say which import failed, so nothing is silently green.
        out = CHECK.check_modules(str(self.tmp / "no-such-python"), ["a", "b"])
        self.assertEqual(sorted(out), ["a", "b"])
        self.assertTrue(all(i["absent"] for i in out.values()))

    def test_no_modules_requested_is_empty_not_an_error(self):
        self.assertEqual(CHECK.check_modules(str(self.env_py), []), {})

    # ---- the remedy ---------------------------------------------------------

    def test_the_remedy_moves_the_caller_not_the_module_or_the_deepest(self):
        info = {"absent": False, "error": "AttributeError: ...",
                "chain": ["allel", "dask", "numpy"]}
        fix = CHECK.broken_import_fix(str(self.env_py), "allel", info)
        self.assertIsNotNone(fix)
        self.assertIn("conda update", fix)
        self.assertIn("dask", fix)
        # Only the operands matter here; the trailing "# ..." explains the
        # diagnosis and naturally names the deepest package.
        operands = fix.split("#")[0]
        # scikit-allel is present — naming it is the no-op that started all this.
        self.assertNotIn("scikit-allel", operands)
        # numpy dropped the API; pulling it backwards is not the fix.
        self.assertNotRegex(operands, r"(?<![-\w])numpy(?![-\w])")

    def test_the_remedy_is_one_line(self):
        # bdtools fix carries remedies through a tab-separated plan; a newline
        # truncates the plan silently.
        info = {"absent": False, "error": "x", "chain": ["allel", "dask", "numpy"]}
        fix = CHECK.broken_import_fix(str(self.env_py), "allel", info)
        self.assertNotIn("\n", fix)
        self.assertNotIn("\t", fix)

    def test_no_remedy_is_invented_when_the_chain_says_nothing(self):
        for chain in ([], ["allel"], ["allel", "allel"]):
            info = {"absent": False, "error": "x", "chain": chain}
            self.assertIsNone(
                CHECK.broken_import_fix(str(self.env_py), "allel", info),
                f"chain {chain} identifies no caller to move")

    # ---- what reaches the screen -------------------------------------------

    def _broken_report(self, module, chain, error):
        """run_checks' output for one tool whose only fault is a broken import."""
        tool = "kraken_id_parse_gui"
        failures = {module: {"absent": False, "error": error, "chain": chain}}
        from unittest import mock
        env_py = self.tmp / "env/bin/python"
        with mock.patch.object(CHECK, "check_modules", return_value=failures):
            with mock.patch.object(CHECK, "has_binary", return_value=True):
                with mock.patch.object(CHECK, "resolve_asset_dirs",
                                       return_value=([], [])):
                    return CHECK.run_checks(tool, str(env_py), "env",
                                            tool_dir=str(self.tmp))

    def test_the_real_error_reaches_the_issue_label(self):
        # The dashboard renders issue labels and fixes verbatim
        # (bin/dashboard.py), so whatever a remote Mac is told to do, it is told
        # here. The traceback is the payload.
        err = ("AttributeError: `np.round_` was removed in the NumPy 2.0 "
               "release. Use `np.round` instead.")
        _status, _lines, issues, _notes = self._broken_report(
            "allel", ["allel", "dask", "numpy"], err)
        labels = " | ".join(i["label"] for i in issues)
        self.assertIn("np.round_", labels)
        self.assertIn("installed but fails to import", labels)
        self.assertNotIn("modules missing", labels,
                         "reporting it as missing is the bug this guards")

    def test_the_offered_fix_is_never_a_no_op_install(self):
        _status, _lines, issues, _notes = self._broken_report(
            "allel", ["allel", "dask", "numpy"], "AttributeError: gone")
        fixes = " | ".join(i.get("fix", "") for i in issues)
        self.assertIn("conda update", fixes)
        self.assertNotIn("install -y", fixes)
        self.assertNotIn("--fresh", fixes,
                         "a rebuild re-solves to the same incompatible pair")

    def test_the_import_chain_is_explained_in_the_notes(self):
        _status, _lines, issues, notes = self._broken_report(
            "allel", ["allel", "dask", "numpy"], "AttributeError: gone")
        joined = " ".join(notes)
        self.assertIn("allel → dask → numpy", joined)
        self.assertIn("installing it changes nothing", joined)

    def test_a_broken_import_still_fails_the_check(self):
        status, _lines, _issues, _notes = self._broken_report(
            "allel", ["allel", "dask", "numpy"], "AttributeError: gone")
        self.assertEqual(status, "issues",
                         "a tool that cannot import its analysis module is not ready")


@unittest.skipUnless(BASH, "bash is required")
class FixAutomationTests(unittest.TestCase):
    """`bdtools fix` must not run the new remedy unattended.

    fix_class is extracted from the shipped script rather than restated, so a
    reordering of its cases is caught here instead of in production.
    """

    def _fix_class(self, cmd):
        src = (ROOT / "bin/fix.sh").read_text()
        start = src.index("fix_class()")
        end = src.index("DOCTOR_ARGS=()")
        out = subprocess.run(
            [BASH, "-c", f"{src[start:end]}\nfix_class {cmd!r}"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_conda_update_is_never_automatic(self):
        self.assertEqual(
            self._fix_class('CONDA_SUBDIR=osx-64 conda update -y -p "/x/env" '
                            '-c conda-forge -c bioconda dask'),
            "manual")

    def test_the_existing_classifications_are_unchanged(self):
        self.assertEqual(self._fix_class("conda install -y -p /x/env pysam"),
                         "manual")
        self.assertEqual(self._fix_class("bin/bdtools setup-databases --home"),
                         "auto")
        self.assertEqual(self._fix_class('"/x/env/bin/python" -m pip install fastapi'),
                         "auto")


if __name__ == "__main__":
    unittest.main()
