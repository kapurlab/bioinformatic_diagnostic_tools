#!/usr/bin/env python3
"""`bdtools complete-env` adds what a spec declares and the env lacks — only that.

Run against a fake tool and a fake conda, so nothing here depends on the host:
the conda stub records every call and, on a real install, writes the conda-meta
record a real one would. What is pinned:

  * the install asks for exactly the missing declared specs, with
    --freeze-installed (nothing already present may move) and the spec's own
    channels, solved for the env's platform;
  * a refused solve changes nothing, records the refusal, and exits 2;
  * a complete env is a no-op; --dry-run installs nothing;
  * a report-only tool is left alone (`all` with a note; named, exit 5).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")

SPEC = """\
name: kraken_id_parse
channels:
  - conda-forge
  - bioconda
dependencies:
  - python=3.10
  - numpy
  - plotly <6          # interactive charts
  - pip:
      - PyVCF3>=1.0.0
"""

CONDA_STUB = """\
#!/usr/bin/env bash
# Records argv; refuses solves when asked to; writes conda-meta on a real install.
printf '%s\\n' "$*" >> "${FAKE_CONDA_LOG}"
if [[ "$1" == "list" ]]; then echo "@EXPLICIT"; echo "https://x/y-1.0-0.tar.bz2"; exit 0; fi
if [[ "$1" != "install" ]]; then exit 0; fi
prefix=""; dry=0; prev=""
for a in "$@"; do
  [[ "${prev}" == "-p" ]] && prefix="${a}"
  [[ "${a}" == "--dry-run" ]] && dry=1
  prev="${a}"
done
[[ "${FAKE_CONDA_REFUSE:-0}" == "1" ]] && { echo "UnsatisfiableError: plotly needs a newer tenacity than the frozen one"; exit 1; }
[[ ${dry} -eq 1 ]] && exit 0
for a in "$@"; do
  case "${a}" in
    -*|conda-forge|bioconda|"${prefix}") ;;
    *) name="${a%%[ =<>!~]*}"; touch "${prefix}/conda-meta/${name}-9.9-py_0.json";;
  esac
done
exit 0
"""


@unittest.skipUnless(BASH, "bash is required")
class CompleteEnv(unittest.TestCase):
    TOOL = "kraken_id_parse_gui"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        # A site-style tools dir named by BDTOOLS_TOOLSDIR, so both the shell and
        # tool_launch resolve the same fake checkout.
        self.tools = base / "tools"
        self.tool = self.tools / self.TOOL
        (self.tool / "backend").mkdir(parents=True)
        (self.tool / "conda_setup").mkdir()
        (self.tool / "conda_setup" / "environment.yml").write_text(SPEC)
        self.env = self.tool / "env"
        (self.env / "conda-meta").mkdir(parents=True)
        (self.env / "bin").mkdir()
        py = self.env / "bin" / "python"
        py.write_text("#!/bin/sh\nexit 0\n"); py.chmod(0o755)
        for name in ("python-3.10.14-h0_0", "numpy-1.26.4-py310_0"):
            (self.env / "conda-meta" / f"{name}.json").write_text(json.dumps({"subdir": "linux-64"}))
        # The fake conda base: detect_conda takes $CONDA_BASE/bin/conda first.
        self.conda_base = base / "conda"
        (self.conda_base / "bin").mkdir(parents=True)
        stub = self.conda_base / "bin" / "conda"
        stub.write_text(CONDA_STUB); stub.chmod(0o755)
        self.log = base / "conda.log"
        self.home = base / "bdhome"
        self.home.mkdir()

    def run_it(self, *args, refuse=False, extra_env=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BDTOOLS_", "CONDA_"))}
        env.update({
            "BDTOOLS_TOOLSDIR": str(self.tools),
            "BDTOOLS_HOME": str(self.home),
            "CONDA_BASE": str(self.conda_base),
            "FAKE_CONDA_LOG": str(self.log),
            "FAKE_CONDA_REFUSE": "1" if refuse else "0",
        })
        env.update(extra_env or {})
        return subprocess.run([BASH, str(ROOT / "bin/complete-env.sh"), *args],
                              capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=300)

    def conda_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def installed(self):
        return sorted(p.name.rsplit("-", 2)[0] for p in (self.env / "conda-meta").glob("*.json"))

    def test_it_adds_exactly_the_missing_spec_with_everything_else_frozen(self):
        r = self.run_it(self.TOOL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        installs = [c for c in self.conda_calls() if c.startswith("install")]
        self.assertEqual(len(installs), 2, installs)          # the dry-run solve, then the install
        self.assertIn("--dry-run", installs[0])
        self.assertNotIn("--dry-run", installs[1])
        for call in installs:
            self.assertIn("--freeze-installed", call)
            self.assertIn("-c conda-forge -c bioconda", call)
            self.assertTrue(call.endswith("plotly <6"), call)
            self.assertNotIn("numpy", call)                    # present → not asked for
        self.assertIn("plotly", self.installed())
        self.assertIn("added plotly", r.stdout)
        # The snapshot was taken before the install.
        self.assertTrue((self.home / "state" / f"{self.TOOL}.env-explicit.txt").exists())

    def test_a_refused_solve_changes_nothing_and_is_remembered(self):
        r = self.run_it(self.TOOL, refuse=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        installs = [c for c in self.conda_calls() if c.startswith("install")]
        self.assertEqual(len(installs), 1)                     # the dry run only
        self.assertNotIn("plotly", self.installed())
        self.assertIn("left exactly as it was", r.stderr + r.stdout)
        self.assertIn("install kraken_id_parse_gui --rebuild", r.stdout)
        rec = json.loads((self.home / "state" / f"{self.TOOL}.env-complete-blocked.json").read_text())
        self.assertEqual(rec["missing"], ["plotly"])
        # A later success forgets the refusal.
        r = self.run_it(self.TOOL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.home / "state" / f"{self.TOOL}.env-complete-blocked.json").exists())

    def test_a_complete_env_is_left_alone(self):
        (self.env / "conda-meta" / "plotly-5.24.1-py_0.json").write_text("{}")
        r = self.run_it(self.TOOL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([c for c in self.conda_calls() if c.startswith("install")], [])
        self.assertIn("has everything its spec declares", r.stdout)

    def test_dry_run_solves_and_installs_nothing(self):
        r = self.run_it(self.TOOL, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        installs = [c for c in self.conda_calls() if c.startswith("install")]
        self.assertEqual(len(installs), 1)
        self.assertIn("--dry-run", installs[0])
        self.assertNotIn("plotly", self.installed())
        self.assertIn("would add plotly", r.stdout)

    def test_a_report_only_tool_is_left_alone(self):
        manifest = Path(self.tmp.name) / "tools.yml"
        text = (ROOT / "tools.yml").read_text()
        head, sep, tail = text.partition(f"  - name: {self.TOOL}\n")
        self.assertTrue(sep)
        tail = tail.replace("    updates: install\n", "    updates: report\n", 1)
        manifest.write_text(head + sep + tail)
        r = self.run_it(self.TOOL, extra_env={"BDTOOLS_MANIFEST": str(manifest)})
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertEqual([c for c in self.conda_calls() if c.startswith("install")], [])
        self.assertIn("report-only", r.stderr + r.stdout)
        r = self.run_it(self.TOOL, "--allow-report-only", extra_env={"BDTOOLS_MANIFEST": str(manifest)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("plotly", self.installed())


if __name__ == "__main__":
    unittest.main()
