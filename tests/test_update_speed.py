#!/usr/bin/env python3
"""A package update does no solve it does not need, and says how long each one took.

On the Ames HPC (2026-11-03) the dashboard's two-package update ran for over half
an hour; the same kraken2 move takes 40 s on the suite's build Mac. Each tool's
update was five conda solves — one in its env, three to prove the set installs on
every platform the lab deploys to, one more for the install — and the log said
nothing about which was slow. What is pinned here:

  * moving to exactly the version tools.yml pins skips the three-platform gate (a
    pin is checked there before it is set); moving past the pin still runs it;
  * the install reuses the channel index its dry run just fetched (conda only);
  * the log names the conda and its solver, flags the classic solver, and times
    each step;
  * a --dry-run ends by saying nothing was installed, not "updates complete".
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
SCRIPT = (ROOT / "bin/update-packages.sh").read_text(encoding="utf-8")


def _fn(name, text=SCRIPT):
    start = text.index(f"\n{name}() {{") + 1
    return text[start:text.index("\n}\n", start) + 3]


def sh(body, **env):
    e = {**os.environ, "DRY_RUN": "0", **env}
    return subprocess.run([BASH, "-c", f'set -euo pipefail\nsource "{ROOT}/bin/lib/common.sh"\n{body}'],
                          capture_output=True, text=True, env=e)


@unittest.skipUnless(BASH, "bash is required")
class Helpers(unittest.TestCase):
    def test_elapsed_reads_as_seconds_then_minutes(self):
        out = sh('SECONDS=14; elapsed_since 0; echo; SECONDS=185; elapsed_since 0')
        self.assertEqual(out.stdout.split("\n"), ["14s", "3m 05s"], out.stderr)

    def fake_conda(self, tmp, name="conda", solver="libmamba"):
        c = Path(tmp) / name
        show = f'echo "solver: {solver}"' if solver else "exit 1"
        c.write_text('#!/bin/sh\n'
                     'case "$1" in\n'
                     '  --version) echo "conda 22.9.0";;\n'
                     f'  config) {show};;\n'
                     '  *) printf "%s\\n" "$*" >> "$(dirname "$0")/calls";;\n'
                     'esac\n')
        c.chmod(0o755)
        return c

    def test_the_conda_and_its_solver_are_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.fake_conda(tmp)
            out = sh(f'say_conda "{c}"')
        self.assertIn("conda 22.9.0, libmamba solver", out.stdout)
        self.assertNotIn("classic", out.stderr)

    def test_the_classic_solver_is_flagged_with_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.fake_conda(tmp, solver="")   # an old conda has no `solver` key
            out = sh(f'say_conda "{c}"; say_conda "{c}"')
        self.assertIn("classic solver", out.stdout)
        self.assertEqual(out.stderr.count("uses the classic solver"), 1, "once per run")
        self.assertIn("config --system --set solver libmamba", out.stdout)

    def test_the_install_reuses_the_dry_runs_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("conda", "mamba"):
                c = self.fake_conda(tmp, name=name)
                out = sh(_fn("_pin_env_subdir") + _fn("_conda_install_set")
                         + f'_conda_install_set "{c}" "{tmp}/env" kraken2=2.17.2')
                self.assertEqual(out.returncode, 0, out.stderr)
            calls = (Path(tmp) / "calls").read_text().splitlines()
        self.assertIn("--use-index-cache", calls[0], "conda reuses the index")
        self.assertNotIn("--use-index-cache", calls[1], "mamba's flags differ across versions")


@unittest.skipUnless(BASH, "bash is required")
class PinnedSetSkipsTheGate(unittest.TestCase):
    """update_one_tool, run as a dry run with its collaborators stubbed."""

    def run_update(self, pinned):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "env"
            (env / "conda-meta").mkdir(parents=True)
            conda = Path(tmp) / "conda"
            conda.write_text("#!/bin/sh\necho 'Total: 2.6 MB'\nexit 0\n")
            conda.chmod(0o755)
            gate = Path(tmp) / "gate"
            body = (_fn("_pin_env_subdir") + _fn("update_one_tool") + f'''
records_for() {{ printf 'kraken2\\tbioconda\\t2.17.1\\t2.17.2\\t{pinned}\\t{env}\\n'; }}
require_updatable() {{ return 0; }}
tool_dir() {{ echo "{tmp}"; }}
detect_conda() {{ echo "{conda}"; }}
say_conda() {{ :; }}
_tool_pyver() {{ echo python=3.10; }}
_unavailable_platforms() {{ echo called >> "{gate}"; }}
storage_preflight_env() {{ :; }}
TO=""; LOCAL_ONLY=0; PLATFORMS=linux-64,osx-64,osx-arm64; TARGET=kraken_id_parse_gui
FAILED=(); BLOCKED=(); REPORT_ONLY=(); CHANGED=0; ALLOW_REPORT_ONLY=0
update_one_tool kraken_id_parse_gui
''')
            out = sh(body, DRY_RUN="1", BDTOOLS_HOME=tmp)
            return out, gate.exists()

    def test_moving_to_the_pin_does_not_re_solve_three_platforms(self):
        out, gated = self.run_update(pinned="2.17.2")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(gated)
        self.assertIn("checked on linux-64,osx-64,osx-arm64 when pinned", out.stdout)
        self.assertRegex(out.stdout, r"the set solves here \(\d+s\)")

    def test_moving_past_the_pin_still_runs_the_gate(self):
        out, gated = self.run_update(pinned="2.17.1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(gated)
        self.assertRegex(out.stdout, r"installable on linux-64,osx-64,osx-arm64 \(\d+s\)")


class DryRunSaysSo(unittest.TestCase):
    def test_the_summary_does_not_claim_an_install(self):
        tail = SCRIPT[SCRIPT.index("if [[ ${#FAILED[@]} -gt 0 ]]; then\n  echo"):]
        dry = tail.index('dry run: nothing was installed and tools.yml is unchanged')
        real = tail.index("package updates complete")
        self.assertLess(dry, real, "the dry-run branch is decided first")
        self.assertIn('if [[ ${CHANGED} -eq 1 && "${DRY_RUN}" -eq 1 ]]', tail)

    def test_complete_env_says_would_add(self):
        text = (ROOT / "bin/complete-env.sh").read_text(encoding="utf-8")
        self.assertIn("would add (dry run — nothing was installed):", text)


if __name__ == "__main__":
    unittest.main()
