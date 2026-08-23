#!/usr/bin/env python3
"""Regression tests for the peripheral launch and portability guards.

The August macOS incident: launch architecture (which slice of a universal
binary runs) is inherited from the process tree, so the same healthy env worked
from one launcher and died from another. Production launchers now pin with
/usr/bin/arch derived from the env's conda-meta. These tests cover the
peripheral spawn sites that were still unpinned — the golden-test pipeline run
(bin/test.sh), the sra-tools fetch (tests/lib/fetch.sh), update_blastdb.pl
(bin/setup-databases.sh), and the dashboard launch (bin/bdtools) — plus the
md5sum/md5 portability shim in bin/check-shared-frontend.sh and the repair
order printed by bare `bdtools`.

No conda and no network: conda envs are fabricated as conda-meta/*.json, and
the shell functions are sed-extracted from the shipped scripts so the tests
exercise the exact text that runs in production.
"""
import hashlib
import os
import subprocess
import tempfile
import textwrap
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
COMMON = ROOT / "bin/lib/common.sh"

# Every file this suite owns; bash -n on each is the cheapest possible guard
# against shipping a launcher that cannot even parse.
OWNED = [
    ROOT / "bin/test.sh",
    ROOT / "bin/setup-databases.sh",
    ROOT / "bin/check-shared-frontend.sh",
    ROOT / "bin/doctor.sh",
    ROOT / "bin/bdtools",
    ROOT / "tests/lib/fetch.sh",
]

# Where each pin-mapping function lives. They are deliberate per-script copies
# (the scripts are owned separately from common.sh), which is exactly why the
# test drives ALL of them through the same fixtures: copies that drift apart
# are two launchers disagreeing about an env — a bug this suite has paid for
# more than once.
PIN_FUNCS = [
    (ROOT / "bin/test.sh", "arch_prefix"),
    (ROOT / "bin/setup-databases.sh", "arch_prefix"),
    (ROOT / "bin/bdtools", "arch_prefix"),
    (ROOT / "tests/lib/fetch.sh", "_sra_arch_prefix"),
]

# THE regression this suite already shipped once: a guard that consulted
# `uname -m`. Inside a translated process uname -m reports x86_64 — false
# exactly when the caller is the Rosetta process whose preference needs
# overriding. The shim makes uname -m lie the way Rosetta does; the mapping
# must not care.
FAKE_UNAME = ('uname() { if [[ "${1:-}" == "-m" ]]; then echo x86_64; '
              'else echo Darwin; fi; }\n')


def sh(script, env=None, cwd=None):
    """Run a bash snippet with common.sh sourced. Returns CompletedProcess."""
    full = f'set -euo pipefail\nsource "{COMMON}"\n{script}\n'
    e = dict(os.environ)
    e.pop("DRY_RUN", None)
    if env:
        e.update(env)
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True,
                          env=e, cwd=cwd)


def meta(envdir, records):
    """Write synthetic conda-meta/*.json records: [(name, subdir), ...]."""
    (envdir / "conda-meta").mkdir(parents=True, exist_ok=True)
    for i, (name, subdir) in enumerate(records):
        (envdir / "conda-meta" / f"{name}-1.0-h0_{i}.json").write_text(
            '{\n  "build": "h0_%d",\n  "name": "%s",\n  "subdir": "%s"\n}\n'
            % (i, name, subdir))


def extract(path, func):
    """The function's shipped source text, sed-extracted like ArchPinTests does."""
    r = subprocess.run(["sed", "-n", f"/^{func}()/,/^}}/p", str(path)],
                       capture_output=True, text=True)
    if not r.stdout.strip():
        raise AssertionError(f"{func}() not found in {path}")
    return r.stdout


class PinMappingTests(unittest.TestCase):
    """conda-meta -> /usr/bin/arch pin, identically at every peripheral spawn site."""

    def _pin(self, path, func, records, fake_uname=False):
        with tempfile.TemporaryDirectory() as td:
            envdir = Path(td) / "env"
            meta(envdir, records)
            shim = FAKE_UNAME if fake_uname else ""
            r = sh(extract(path, func) + shim + f'\n{func} "{envdir}"; echo')
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip()

    def _assert_all(self, records, expected, fake_uname=False):
        for path, func in PIN_FUNCS:
            with self.subTest(script=path.name, func=func):
                got = self._pin(path, func, records, fake_uname=fake_uname)
                self.assertEqual(got, expected)

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_an_arm64_env_is_pinned_to_arm64(self):
        self._assert_all([("python", "osx-arm64"), ("perl", "osx-arm64")],
                         "/usr/bin/arch -arm64")

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_a_rosetta_env_is_pinned_to_x86_64(self):
        self._assert_all([("python", "osx-64"), ("blast", "osx-64")],
                         "/usr/bin/arch -x86_64")

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_a_noarch_only_env_is_not_pinned(self):
        # No recorded platform means nothing to assert — assert nothing.
        self._assert_all([("pyyaml", "noarch")], "")

    def test_a_missing_env_is_not_pinned_anywhere(self):
        # Holds on every platform: no conda-meta (or no dir at all) -> no pin.
        with tempfile.TemporaryDirectory() as td:
            for path, func in PIN_FUNCS:
                with self.subTest(script=path.name, func=func):
                    r = sh(extract(path, func) + f'\n{func} "{td}/nope"; echo')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stdout.strip(), "")

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_the_mapping_never_consults_uname_m(self):
        # THE REGRESSION. With uname -m lying like a translated process does,
        # the pin must still come from conda-meta — otherwise it vanishes
        # precisely in the case that needed it (the 2026-08 dashboard failure).
        self._assert_all([("python", "osx-arm64")], "/usr/bin/arch -arm64",
                         fake_uname=True)

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_every_copy_agrees_with_the_production_launcher(self):
        # install-local.sh:arch_prefix is the production original; the
        # peripheral copies must never drift from it.
        prod = ROOT / "bin/install-local.sh"
        for records, expected in (
                ([("python", "osx-arm64")], "/usr/bin/arch -arm64"),
                ([("python", "osx-64")], "/usr/bin/arch -x86_64"),
                ([("pyyaml", "noarch")], "")):
            want = self._pin(prod, "arch_prefix", records)
            self.assertEqual(want, expected)
            self._assert_all(records, want)


class DashboardLaunchPinTests(unittest.TestCase):
    """bdtools must pin the dashboard itself to its python's env platform.

    _dashboard_proxy_python picks the first tool-checkout env python that
    imports the proxy deps, and on Apple Silicon those envs are deliberately
    osx-64 — so an unpinned dashboard ran under Rosetta BY CONSTRUCTION and
    became the hidden x86_64 ancestor of everything it launched. The pin block
    is sed-extracted from cmd_dashboard so this exercises the shipped lines,
    not a re-implementation.
    """

    BDTOOLS = ROOT / "bin/bdtools"

    def _launch_argv(self, python_path):
        block = subprocess.run(
            ["sed", "-n", "/# Pin the dashboard to its python/,/^  fi$/p",
             str(self.BDTOOLS)], capture_output=True, text=True).stdout
        self.assertIn("arch_prefix", block, "pin block missing from cmd_dashboard")
        fn = extract(self.BDTOOLS, "arch_prefix")
        r = sh(fn +
               '\n_launch() {\n'
               f'  local runcmd=("{python_path}" -m uvicorn app:app)\n'
               f'{block}\n'
               '  printf "%s\\n" "${runcmd[@]}"\n'
               '}\n_launch')
        self.assertEqual(r.returncode, 0, r.stderr)
        # Drop the human "arch: ..." notice; keep the argv lines.
        return [l for l in r.stdout.splitlines() if not l.startswith("  arch:")]

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_an_osx64_env_python_gets_the_pin_prepended(self):
        with tempfile.TemporaryDirectory() as td:
            envdir = Path(td) / "env"
            meta(envdir, [("python", "osx-64"), ("uvicorn", "osx-64")])
            argv = self._launch_argv(f"{envdir}/bin/python")
            self.assertEqual(argv[:2], ["/usr/bin/arch", "-x86_64"])
            self.assertEqual(argv[2], f"{envdir}/bin/python")

    def test_a_python_outside_any_env_launches_unpinned(self):
        # No conda-meta -> no recorded platform -> the argv is untouched.
        with tempfile.TemporaryDirectory() as td:
            argv = self._launch_argv(f"{td}/bin/python")
            self.assertEqual(argv[0], f"{td}/bin/python")
            self.assertNotIn("/usr/bin/arch", argv)


class SraArchDetectionTests(unittest.TestCase):
    """fetch.sh pins env-resolved sra-tools, and only env-resolved ones."""

    FETCH = ROOT / "tests/lib/fetch.sh"

    def _detect(self, bindir):
        # _sra_arch_from_resolved needs its mapping helper too.
        fns = extract(self.FETCH, "_sra_arch_prefix") + \
              extract(self.FETCH, "_sra_arch_from_resolved")
        r = sh(f'{fns}\nPATH="{bindir}:${{PATH}}"\n'
               '_sra_arch_from_resolved\necho "${_SRA_ARCH_PREFIX}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def _env_with_prefetch(self, td, records):
        envdir = Path(td) / "env"
        meta(envdir, records)
        bindir = envdir / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        exe = bindir / "prefetch"
        exe.write_text("#!/usr/bin/env bash\nexit 0\n")
        exe.chmod(0o755)
        return bindir

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_tools_inside_a_conda_env_get_that_envs_pin(self):
        with tempfile.TemporaryDirectory() as td:
            bindir = self._env_with_prefetch(td, [("sra-tools", "osx-64")])
            self.assertEqual(self._detect(bindir), "/usr/bin/arch -x86_64")

    def test_tools_outside_any_env_run_unpinned(self):
        # A plain bin dir with no conda-meta beside it: no recorded platform,
        # no pin — on every OS.
        with tempfile.TemporaryDirectory() as td:
            bindir = Path(td) / "bin"
            bindir.mkdir()
            exe = bindir / "prefetch"
            exe.write_text("#!/usr/bin/env bash\nexit 0\n")
            exe.chmod(0o755)
            self.assertEqual(self._detect(bindir), "")


class Md5ResolutionTests(unittest.TestCase):
    """check-shared-frontend.sh must hash on pre-Ventura macOS (no md5sum) too."""

    SCRIPT = ROOT / "bin/check-shared-frontend.sh"
    DIGEST = "d41d8cd98f00b204e9800998ecf8427e"

    def _shim(self, td, tool, output):
        exe = Path(td) / tool
        exe.write_text("#!/usr/bin/env bash\n" + output)
        exe.chmod(0o755)
        return Path(td)

    def _resolve(self, shimdir):
        # Resolution runs against the shim dir ALONE, so the host's real tools
        # cannot leak into the branch choice; execution then puts the shims
        # first so the chosen name resolves to a fake with a known output.
        fn = extract(self.SCRIPT, "resolve_md5")
        r = sh(f'{fn}\n_old="${{PATH}}"; PATH="{shimdir}"; resolve_md5; PATH="${{_old}}"\n'
               f'echo "${{MD5[*]}}"\n'
               f'PATH="{shimdir}:${{PATH}}"\n'
               f'printf "" | "${{MD5[@]}}" | awk \'{{print $1}}\'')
        self.assertEqual(r.returncode, 0, r.stderr)
        chosen, digest = r.stdout.strip().splitlines()
        return chosen, digest

    def test_md5sum_branch_and_its_two_column_output_parse(self):
        with tempfile.TemporaryDirectory() as td:
            # GNU format: "digest  filename" — awk must strip the second column.
            shim = self._shim(td, "md5sum", f'echo "{self.DIGEST}  -"\n')
            chosen, digest = self._resolve(shim)
            self.assertEqual(chosen, "md5sum")
            self.assertEqual(digest, self.DIGEST)

    def test_bsd_md5_branch_and_its_bare_output_parse(self):
        with tempfile.TemporaryDirectory() as td:
            # BSD md5 -q prints the bare digest; the shim refuses to run
            # without -q, proving the resolved command really carries the flag.
            shim = self._shim(
                td, "md5",
                'if [[ "${1:-}" != "-q" ]]; then echo "md5: expected -q" >&2; exit 64; fi\n'
                f'echo "{self.DIGEST}"\n')
            chosen, digest = self._resolve(shim)
            self.assertEqual(chosen, "md5 -q")
            self.assertEqual(digest, self.DIGEST)

    def test_the_real_tool_on_this_machine_yields_a_real_md5(self):
        # Whichever branch this host takes, the parsed value must equal a real
        # MD5 of the input — the drift check depends on the digest, not just on
        # "some 32 characters".
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "sample"
            f.write_bytes(b"shared frontend drift\n")
            fn = extract(self.SCRIPT, "resolve_md5")
            r = sh(f'{fn}\nresolve_md5\n"${{MD5[@]}}" < "{f}" | awk \'{{print $1}}\'')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(),
                             hashlib.md5(b"shared frontend drift\n").hexdigest())


class RepairOrderTests(unittest.TestCase):
    """Bare `bdtools` must hand out the canonical 6-step repair order."""

    def test_repair_order_names_diagnose_between_fix_and_test(self):
        r = subprocess.run([str(ROOT / "bin/bdtools")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Something broken?", r.stdout)
        steps = r.stdout.split("Something broken?", 1)[1]
        for n in range(1, 7):
            self.assertIn(f"{n}.", steps, f"repair order lost step {n}")
        self.assertIn("bdtools diagnose", steps)
        # doctor -> fix -> diagnose -> test: diagnose is the escalation for
        # "doctor is green but the tool still fails", so it must sit after the
        # fixes and before the final proof.
        self.assertLess(steps.index("bdtools doctor"), steps.index("bdtools fix"))
        self.assertLess(steps.index("bdtools fix"), steps.index("bdtools diagnose"))
        self.assertLess(steps.index("bdtools diagnose"), steps.index("bdtools test"))

    def test_doctor_help_documents_deep(self):
        r = subprocess.run(["bash", str(ROOT / "bin/doctor.sh"), "--help"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--deep", r.stdout)


class SyntaxTests(unittest.TestCase):
    """bash -n on every file this change touches — cheap, and it has caught
    real quoting mistakes in argv-prefix code exactly like this before."""

    def test_every_owned_script_parses(self):
        for p in OWNED:
            with self.subTest(script=str(p.relative_to(ROOT))):
                r = subprocess.run(["bash", "-n", str(p)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


class ReviewRegressionTests(unittest.TestCase):
    """Defects the adversarial review confirmed in this hardening pass itself.

    Each shipped in a first draft and was caught only by trying to refute the
    code: a feature that computed its pin and never applied it, and a
    build-time guard that locked launch-only modes out.
    """

    @unittest.skipUnless(BASH, "bash required")
    def test_the_sra_pin_is_actually_applied_not_just_computed(self):
        # The first draft set _SRA_ARCH_PREFIX, tested the DETECTION, and never
        # spliced it into the prefetch/fasterq-dump invocations — the whole
        # feature inert while its tests passed. Stub the tools and a recording
        # arch, and assert the pin reaches the argv the tools actually see.
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            bindir = tdp / "bin"
            bindir.mkdir()
            arglog = tdp / "argv.log"
            for tool in ("prefetch", "fasterq-dump"):
                stub = bindir / tool
                stub.write_text("#!/bin/sh\necho \"%s ran\" >> \"%s\"\nexit 0\n"
                                % (tool, arglog))
                stub.chmod(0o755)
            pinstub = tdp / "fakearch"
            pinstub.write_text("#!/bin/sh\nshift\necho \"PINNED $*\" >> \"%s\"\nexec \"$@\"\n"
                               % arglog)
            pinstub.chmod(0o755)
            script = (
                'source "%s/tests/lib/fetch.sh"\n' % ROOT
                + '_ensure_sra_tools() { _SRA_ARCH_PREFIX="%s -arm64"; return 0; }\n' % pinstub
                + 'PATH="%s:$PATH" fetch_sra FAKE1 "%s/out" || true\n' % (bindir, tdp))
            r = subprocess.run([BASH, "-c", script], capture_output=True,
                               text=True, timeout=120)
            logged = arglog.read_text() if arglog.exists() else ""
            self.assertIn("PINNED", logged,
                          "the computed pin must reach the actual invocation; "
                          "stderr: " + r.stderr[:300])
            self.assertIn("prefetch ran", logged)

    def _guard_harness(self, home, kernel, run_only):
        src = (ROOT / "bin/install-local.sh").read_text()
        i = src.index("_require_linux_fs_home() {")
        fn = src[i:src.index("\n}", i) + 2]
        gi = src.index("# Launch-only and query modes clone and solve nothing")
        gate = src[gi:src.index('DIR="$(tool_dir', gi)]
        script = "\n".join([
            "set -uo pipefail",
            'warn() { echo "WARN $*"; }',
            'die()  { echo "DIE $*"; exit 1; }',
            '_wsl_kernel() { echo "%s"; }' % kernel,
            'BDTOOLS_HOME=%s' % home,
            "TOOL=faketool",
            "RUN_ONLY=%d" % run_only,
            "PRINT_PYTHON=0",
            fn,
            gate,
            'echo "SURVIVED"',
        ])
        return subprocess.run([BASH, "-c", script], capture_output=True,
                              text=True, timeout=60)

    @unittest.skipUnless(BASH, "bash required")
    def test_wsl_home_guard_spares_launch_only_modes(self):
        # The first draft died unconditionally: a legacy-but-working /mnt
        # install could not even LAUNCH, and --print-python (how check-updates
        # and the dashboard detect built tools) misreported every built env as
        # unbuilt. Launch/query modes warn; builds die.
        r = self._guard_harness("/mnt/c/bdtools", "5.15.0-microsoft-standard", 1)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARN", r.stdout)
        self.assertIn("SURVIVED", r.stdout)
        r = self._guard_harness("/mnt/c/bdtools", "5.15.0-microsoft-standard", 0)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("DIE", r.stdout)

    @unittest.skipUnless(BASH, "bash required")
    def test_wsl_home_guard_exempts_wsl_mounted_linux_disks(self):
        # `wsl --mount` puts ext4 disks under /mnt/wsl/<name> — native Linux
        # filesystems, exactly where a careful user parks a big BDTOOLS_HOME.
        # The first draft equated /mnt/* with drvfs and refused them.
        r = self._guard_harness("/mnt/wsl/bigdisk/bdtools",
                                "5.15.0-microsoft-standard", 0)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SURVIVED", r.stdout)
        self.assertNotIn("WARN", r.stdout)

    @unittest.skipUnless(BASH, "bash required")
    def test_off_wsl_the_guard_is_inert(self):
        r = self._guard_harness("/mnt/c/bdtools", "Darwin 25.6.0", 0)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SURVIVED", r.stdout)
        self.assertNotIn("WARN", r.stdout)


if __name__ == "__main__":
    unittest.main()
