#!/usr/bin/env python3
"""The manifest's `constraints:` floors must hold on every platform.

An unpinned library pair is a latent break, not a free upgrade. The case this
guards (2026-08-21, macOS, but nothing about it is macOS-specific):
kraken_id_parse_gui's own environment.yml declares bare `numpy` and bare `dask`,
a --fresh build solved to numpy 2.2.6 with dask 2023.3.0, and dask that old
decorates with `np.round_` — which NumPy 2.0 removed. `import allel` therefore
died three packages deep, every version in `packages:` was present and correct,
and because a solve is deterministic per spec+channel the rebuild remedy
reproduced the failure instead of repairing it.

`constraints:` is the suite's answer: state the floor the tool's own spec omits,
enforce it after the env is built, on Linux, WSL, macOS and OOD alike.

The functions under test are EXTRACTED from the shipped install-local.sh rather
than restated here — a floor check that has drifted from the script that runs it
is the one way this guard silently stops working.
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
SCRIPT = ROOT / "bin/install-local.sh"
MANIFEST = ROOT / "tools.yml"


def _extract(*names):
    """Pull named shell functions verbatim out of install-local.sh."""
    src = SCRIPT.read_text()
    out = []
    for name in names:
        start = src.index(f"{name}() {{")
        depth, i = 0, start
        while i < len(src):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append(src[start:i + 1])
    return "\n".join(out)


@unittest.skipUnless(BASH, "bash is required")
class VersionCompareTests(unittest.TestCase):
    """`2024.10 >= 2024.8` is true, and string comparison gets it wrong.

    sort -V is GNU-only (absent on macOS and on minimal OOD images), which is
    why this delegates to python rather than the shell.
    """

    def _ge(self, a, b):
        body = _extract("_version_ge")
        out = subprocess.run(
            [BASH, "-c", f'PYBIN=python3\n{body}\n_version_ge "{a}" "{b}"'],
            capture_output=True, text=True, timeout=60)
        return out.returncode == 0

    def test_calendar_versions_compare_numerically(self):
        self.assertTrue(self._ge("2024.10", "2024.8"),
                        "string comparison says '10' < '8' and would reinstall "
                        "a dask that is already new enough")
        self.assertTrue(self._ge("2025.5.1", "2024.8"))
        self.assertFalse(self._ge("2023.3.0", "2024.8"),
                         "this is the version that could not import")

    def test_equal_satisfies_a_floor(self):
        self.assertTrue(self._ge("2024.8", "2024.8"))
        self.assertTrue(self._ge("2024.8.0", "2024.8"))

    def test_a_lower_minor_does_not_satisfy(self):
        self.assertFalse(self._ge("1.3.13", "1.4"))

    def test_a_non_numeric_suffix_does_not_crash(self):
        # Whatever the answer, it must be an answer — an exception here would
        # abort an install under `set -euo pipefail`.
        for a, b in (("2025.5.1rc1", "2024.8"), ("abc", "2024.8"),
                     ("2024.8", "abc"), ("", "2024.8")):
            self._ge(a, b)


@unittest.skipUnless(BASH, "bash is required")
class EnforceConstraintsTests(unittest.TestCase):
    """enforce_env_constraints, run against a synthetic conda-meta."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "conda-meta").mkdir(parents=True)
        (self.env / "bin").mkdir(parents=True)
        (self.env / "bin/python").write_text("#!/bin/sh\nexit 0\n")
        (self.env / "bin/python").chmod(0o755)

    def _install(self, pkg, version):
        (self.env / f"conda-meta/{pkg}-{version}-py310h0.json").write_text("{}")

    def _run(self, constraints="dask>=2024.8"):
        """Run the real function with conda and the manifest stubbed out.

        A stub `detect_conda` + `_conda_step` records whether a transaction
        would have run, which is the behaviour that matters: a satisfied floor
        must cost nothing, because a `conda install` that changes nothing still
        pays for a full solve, and minutes on every build is how a safety check
        gets deleted.
        """
        body = _extract("_version_ge", "enforce_env_constraints")
        harness = f'''
set -uo pipefail
PYBIN=python3
TOOL=faketool
manifest_get() {{ printf '%s' "{constraints}"; }}
resolve_env_prefix() {{ printf '%s' "{self.env}"; }}
ok()   {{ echo "OK $*"; }}
info() {{ echo "INFO $*"; }}
warn() {{ echo "WARN $*"; }}
detect_conda() {{ printf 'fake-conda'; }}
with_progress() {{ shift; "$@"; }}
_conda_step() {{ echo "CONDA_RAN $*"; }}
{body}
enforce_env_constraints
'''
        out = subprocess.run([BASH, "-c", harness],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0,
                         f"stdout={out.stdout} stderr={out.stderr}")
        return out.stdout

    def test_a_satisfied_floor_runs_no_conda_transaction(self):
        self._install("dask", "2025.5.1")
        out = self._run()
        self.assertIn("OK faketool: dask 2025.5.1 (>= 2024.8)", out)
        self.assertNotIn("CONDA_RAN", out)

    def test_a_violated_floor_raises_it(self):
        self._install("dask", "2023.3.0")
        out = self._run()
        self.assertIn("below the 2024.8 floor", out)
        self.assertIn("CONDA_RAN", out)
        self.assertIn("dask>=2024.8", out)
        # The floor must be passed to conda as a floor, not as an exact pin:
        # pinning would move machines that are already fine.
        self.assertNotIn("dask=2024.8 ", out)

    def test_a_package_the_env_does_not_have_is_left_alone(self):
        # No dask installed: this env does not use it. Adding packages a tool
        # never asked for is not what a floor is for.
        out = self._run()
        self.assertNotIn("CONDA_RAN", out)

    def test_no_constraints_is_a_silent_no_op(self):
        self._install("dask", "2023.3.0")
        out = self._run(constraints="")
        self.assertNotIn("CONDA_RAN", out)
        self.assertNotIn("floor", out)

    def test_an_unsupported_constraint_shape_warns_and_is_skipped(self):
        self._install("dask", "2023.3.0")
        out = self._run(constraints="dask==2024.8")
        self.assertIn("WARN", out)
        self.assertIn("only 'package>=version' floors are supported", out)
        self.assertNotIn("CONDA_RAN", out)

    def test_several_floors_are_evaluated_independently(self):
        self._install("dask", "2025.5.1")     # satisfied
        self._install("bokeh", "2.4.3")       # violated
        out = self._run(constraints="dask>=2024.8 bokeh>=3.0")
        self.assertIn("OK faketool: dask 2025.5.1", out)
        self.assertIn("CONDA_RAN", out)
        self.assertIn("bokeh>=3.0", out)
        # dask is already fine; re-solving it would be the cost this avoids.
        conda_line = [l for l in out.splitlines() if "CONDA_RAN" in l][0]
        self.assertNotIn("dask", conda_line)


class ManifestConstraintTests(unittest.TestCase):
    """The floor that this whole mechanism exists for must actually be declared."""

    def _get(self, tool, field):
        out = subprocess.run(
            ["python3", str(ROOT / "bin/lib/manifest.py"), str(MANIFEST),
             "get", tool, field],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_kraken_declares_the_dask_floor(self):
        self.assertIn("dask>=", self._get("kraken_id_parse_gui", "constraints"))

    def test_the_floor_is_above_the_version_that_could_not_import(self):
        # dask 2023.3.0 is the one that decorates with the removed np.round_.
        floor = self._get("kraken_id_parse_gui", "constraints").split(">=")[1]
        self.assertGreater(tuple(int(p) for p in floor.split(".") if p.isdigit()),
                           (2023, 3, 0))

    def test_constraints_are_floors_everywhere_they_appear(self):
        # An exact pin in this field would silently move machines that are fine;
        # the parser and the enforcement both assume ">=".
        names = subprocess.run(
            ["python3", str(ROOT / "bin/lib/manifest.py"), str(MANIFEST), "names"],
            capture_output=True, text=True, timeout=60).stdout.split()
        for tool in names:
            for spec in self._get(tool, "constraints").split():
                self.assertRegex(spec, r"^[A-Za-z0-9_.-]+>=[0-9][A-Za-z0-9_.]*$",
                                 f"{tool}: '{spec}' is not a floor")

    def test_the_field_is_documented(self):
        # The next person to add a floor reads tools.yml, not this test.
        text = MANIFEST.read_text()
        self.assertIn("constraints", text)
        self.assertRegex(text, r"#.*constraints\b")


if __name__ == "__main__":
    unittest.main()
