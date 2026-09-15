#!/usr/bin/env python3
"""An env this host cannot EXECUTE must not be reported as an env with missing
packages.

The incident (2026-09-15, macOS 27 on an M3 Max). A macOS upgrade removed
Rosetta 2. Every osx-64 conda env in the suite — the suite's own default on
Apple Silicon, see install-local.sh's ensure_conda_subdir rule 2 — stopped being
able to start a single binary:

    bad CPU type in executable: .../envs/amr_plus/bin/python

Five dashboard cards then read "Needs setup before it can run" and listed
fastapi, uvicorn, pydantic, multipart, aiofiles as missing modules, offering a
pip install for each. Every one of those packages was installed and correct, and
every printed remedy re-execs the same interpreter that cannot start — so each
card told the user to run a command that could only fail the same way.

The mechanism: check_modules answers "which imports failed" by running the env's
python, and when that process cannot start it reports every module absent — the
only thing its contract allows, since it genuinely cannot tell which import
failed (see its docstring). run_checks trusted that as a package verdict.

The rule this encodes is the one MODULE_PROBE_TIMED_OUT already encodes for the
other way the probe can fail to run: a fact about the HOST is not a fact about
the packages, and a check that could not run must not grade what it never
probed.

The host is pinned with mocks throughout. These verdicts depend on the machine
the tests run on, and the suite deploys on Apple Silicon, Intel macOS, Linux and
ARM Linux — an unpinned assertion here would pass on one runner and fail on the
next, which is exactly the class of bug the file is about.
"""
import os
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin/lib"))
import check as CHECK  # noqa: E402

COMMON = ROOT / "bin/lib/common.sh"

# Mach-O headers, thin. Same construction ScriptInterpreterTests uses.
X86 = b"\xcf\xfa\xed\xfe" + (0x01000007).to_bytes(4, "little") + b"\x00" * 16
ARM = b"\xcf\xfa\xed\xfe" + (0x0100000C).to_bytes(4, "little") + b"\x00" * 16
# ELF64, little-endian: e_machine 0x3E = x86_64, 0xB7 = aarch64.
def _elf(machine):
    return (b"\x7fELF\x02\x01\x01" + b"\x00" * 9
            + (2).to_bytes(2, "little") + machine.to_bytes(2, "little")
            + b"\x00" * 4)
ELF_X86 = _elf(0x3E)
ELF_ARM = _elf(0xB7)


class EnvPythonRunnableTests(unittest.TestCase):
    """env_python_unrunnable: the header decides, and the host decides."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "env/bin"
        self.bin.mkdir(parents=True)

    def _py(self, payload):
        p = self.bin / "python"
        p.write_bytes(payload)
        p.chmod(0o755)
        return str(p)

    def test_the_live_incident_is_diagnosed_as_an_arch_fault(self):
        py = self._py(X86)
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "arm64")), \
             mock.patch.object(CHECK, "_rosetta_available", return_value=False):
            out = CHECK.env_python_unrunnable(py, "amr_plus_gui")
        self.assertIsNotNone(out, "an x86_64 env on an arm64 Mac without Rosetta "
                                 "cannot run — that is the whole finding")
        label, fix, why = out
        self.assertIn("cannot run on this machine", label)
        self.assertIn("x86_64", label)
        self.assertIn("arm64", label)
        self.assertNotIn("missing", label.lower(),
                         "nothing is missing; the packages are installed")

    def test_the_suites_normal_apple_silicon_state_is_not_a_finding(self):
        # osx-64 under Rosetta is what ensure_conda_subdir rule 2 builds on
        # purpose. While Rosetta is installed it runs, and calling it a defect
        # would fire on every healthy Mac in the lab.
        py = self._py(X86)
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "arm64")), \
             mock.patch.object(CHECK, "_rosetta_available", return_value=True):
            self.assertIsNone(CHECK.env_python_unrunnable(py, "amr_plus_gui"))

    def test_a_native_env_is_not_a_finding(self):
        py = self._py(ARM)
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "arm64")), \
             mock.patch.object(CHECK, "_rosetta_available", return_value=False):
            self.assertIsNone(CHECK.env_python_unrunnable(py, "genoflu_gui"))

    def test_an_intel_mac_running_its_own_osx64_env_is_not_a_finding(self):
        py = self._py(X86)
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "x86_64")):
            self.assertIsNone(CHECK.env_python_unrunnable(py, "amr_plus_gui"))

    def test_aarch64_linux_is_told_there_is_nothing_to_install(self):
        # The distinct case: Linux has no translation layer, so "install the
        # translator" is not a wrong answer, it is not an answer. Saying so
        # stops the search instead of sending someone to look for Rosetta.
        py = self._py(ELF_X86)
        with mock.patch.object(CHECK, "_host_target", return_value=("linux", "arm64")):
            out = CHECK.env_python_unrunnable(py, "kraken_id_parse_gui")
        self.assertIsNotNone(out)
        _label, fix, why = out
        self.assertIn("no x86 translation layer", why)
        self.assertNotIn("rosetta", fix.lower())
        self.assertNotIn("rosetta", why.lower())

    def test_an_env_from_another_os_is_a_rebuild(self):
        py = self._py(ELF_X86)          # a Linux env on a Mac
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "arm64")), \
             mock.patch.object(CHECK, "_rosetta_available", return_value=True):
            out = CHECK.env_python_unrunnable(py, "vsnp_gui")
        self.assertIsNotNone(out, "Rosetta translates x86_64 macOS binaries, "
                                  "not Linux ones")
        label, fix, _why = out
        self.assertIn("Linux", label)
        self.assertIn("rebuild-native", fix)

    def test_a_script_interpreter_is_not_judged(self):
        # A shell-script stand-in for python (what the other test files build)
        # has no arch to read. Inventing a verdict from an unreadable header is
        # how a healthy env gets failed.
        p = self.bin / "python"
        p.write_text("#!/bin/sh\nexec /usr/bin/python3 \"$@\"\n")
        p.chmod(0o755)
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "arm64")), \
             mock.patch.object(CHECK, "_rosetta_available", return_value=False):
            self.assertIsNone(CHECK.env_python_unrunnable(str(p), "vsnp_gui"))

    def test_a_missing_interpreter_is_left_to_the_presence_check(self):
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "arm64")):
            self.assertIsNone(
                CHECK.env_python_unrunnable(str(self.bin / "nope"), "vsnp_gui"))

    def test_the_symlink_is_followed_to_the_real_interpreter(self):
        # A conda env's bin/python is a symlink to bin/pythonX.Y; reading the
        # link itself finds no Mach-O header and would silently pass.
        real = self.bin / "python3.12"
        real.write_bytes(X86)
        real.chmod(0o755)
        link = self.bin / "python"
        link.symlink_to(real)
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "arm64")), \
             mock.patch.object(CHECK, "_rosetta_available", return_value=False):
            self.assertIsNotNone(CHECK.env_python_unrunnable(str(link), "vsnp_gui"))


class RemedyHonestyTests(unittest.TestCase):
    """The printed fix must be runnable. That is the entire defect."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        b = self.tmp / "env/bin"
        b.mkdir(parents=True)
        self.py = b / "python"
        self.py.write_bytes(X86)
        self.py.chmod(0o755)

    def _out(self):
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "arm64")), \
             mock.patch.object(CHECK, "_rosetta_available", return_value=False):
            return CHECK.env_python_unrunnable(str(self.py), "amr_plus_gui")

    def test_the_fix_never_re_execs_the_interpreter_that_cannot_start(self):
        _label, fix, _why = self._out()
        self.assertNotIn("-m pip", fix,
                         "a pip install runs the dead interpreter — this is the "
                         "command five cards printed and nobody could run")
        self.assertNotIn(str(self.py), fix)

    def test_both_real_answers_are_offered_in_order(self):
        _label, fix, why = self._out()
        self.assertIn("--install-rosetta", fix,
                      "the smaller change is offered first while it still exists")
        self.assertIn("rebuild-native", fix,
                      "and the one that survives Apple withdrawing Rosetta")
        self.assertIn("withdrawn", why)

    def test_the_fix_is_one_line(self):
        # bdtools fix carries remedies through a tab-separated plan; a newline
        # truncates the plan silently (same constraint as broken_import_fix).
        _label, fix, _why = self._out()
        self.assertNotIn("\n", fix)

    def test_the_note_says_nothing_was_graded(self):
        _label, _fix, why = self._out()
        self.assertIn("graded", why,
                      "silence about what was NOT checked is how a host fault "
                      "reads as a package verdict")


class RunChecksTests(unittest.TestCase):
    """End to end: the card must not say 'missing modules'."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "conda-meta").mkdir()
        self.py = self.env / "bin/python"
        self.py.write_bytes(X86)
        self.py.chmod(0o755)

    def _run(self):
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "arm64")), \
             mock.patch.object(CHECK, "_rosetta_available", return_value=False):
            return CHECK.run_checks("amr_plus_gui", str(self.py), "env",
                                    tool_dir=str(self.tmp))

    def test_the_tool_fails_for_the_right_reason(self):
        status, _lines, issues, _notes = self._run()
        self.assertEqual(status, "issues")
        labels = " | ".join(i["label"] for i in issues)
        self.assertIn("cannot run on this machine", labels)
        self.assertNotIn("missing modules", labels,
                         "the packages are installed; the CPU cannot run them")

    def test_no_other_check_gets_to_speak(self):
        # Every remaining check runs something out of this prefix, so each would
        # fail for the one reason already established and report it as its own
        # kind of damage. One fault, one finding.
        _status, _lines, issues, _notes = self._run()
        self.assertEqual(len(issues), 1,
                         f"expected exactly one finding, got: "
                         f"{[i['label'] for i in issues]}")

    def test_the_module_probe_is_never_run(self):
        # Not merely "its answer is discarded": running it costs a subprocess
        # per tool that can only fail, and on a slow filesystem that is the
        # timeout path on top of this one.
        with mock.patch.object(CHECK, "check_modules") as probe:
            self._run()
        probe.assert_not_called()


class HostSubdirAgreementTests(unittest.TestCase):
    """check.py and common.sh must name this machine's platform identically.

    Two answers to "what does this host run natively" is how an env gets built
    for one platform and graded against another — the same class of split the
    suite already fixed for env_conda_subdir.
    """

    def test_native_subdir_matches_common_sh(self):
        out = subprocess.run(
            ["bash", "-c", f'set -euo pipefail\nsource "{COMMON}"\nhost_conda_subdir'],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(CHECK.native_subdir(), out.stdout.strip())

    def test_this_machine_maps_to_something(self):
        if platform.system() not in ("Darwin", "Linux"):
            self.skipTest("no mapping claimed for this platform")
        self.assertTrue(CHECK.native_subdir())


class RebuildNativeScriptTests(unittest.TestCase):
    """The remedy check.py prints has to exist and be wired up."""

    SCRIPT = ROOT / "bin/rebuild-native.sh"

    def test_the_script_parses(self):
        out = subprocess.run(["bash", "-n", str(self.SCRIPT)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_the_cli_dispatches_it(self):
        text = (ROOT / "bin/bdtools").read_text()
        self.assertIn("rebuild-native)", text)
        self.assertIn("rebuild-native.sh", text)

    def test_it_is_documented_in_the_usage_block(self):
        out = subprocess.run([str(ROOT / "bin/bdtools"), "--help"],
                             capture_output=True, text=True)
        self.assertIn("rebuild-native", out.stdout)

    def test_it_reports_by_default(self):
        # A command that rebuilds envs must not do so because someone typed it
        # to see what it does. --apply is the only mutating form.
        text = self.SCRIPT.read_text()
        self.assertIn("APPLY=0", text)
        self.assertIn("REPORT ONLY", text)

    def test_the_remedy_check_py_prints_names_this_script(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        b = tmp / "env/bin"
        b.mkdir(parents=True)
        py = b / "python"
        py.write_bytes(ELF_X86)
        py.chmod(0o755)
        with mock.patch.object(CHECK, "_host_target", return_value=("linux", "arm64")):
            _label, fix, _why = CHECK.env_python_unrunnable(str(py), "vsnp_gui")
        self.assertIn("rebuild-native", fix)
        self.assertIn("vsnp_gui", fix)


class VendoredBinaryTests(unittest.TestCase):
    """A hand-downloaded payload's ARCHITECTURE is not its OS.

    ksnp_gui on Apple Silicon without Rosetta (live, 2026-09-15): its conda env
    is osx-arm64 and healthy, but kSNP4 has no conda package — deploy/install.sh
    downloads it from SourceForge, and upstream publishes exactly one "kSNP4.1
    Mac package", x86_64 only. Doctor labelled that "wrong-OS binaries" (on
    macOS binaries, on macOS) and offered `bdtools install ksnp_gui`, which
    re-fetches the identical bytes. A confident label over a remedy that cannot
    work — the same defect as the missing-modules misdiagnosis, in the one check
    that looks outside the env.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "conda-meta").mkdir()
        py = self.env / "bin/python"
        py.write_bytes(ARM)                  # env itself is fine; only the payload is not
        py.chmod(0o755)
        self.py = py
        self.vendor = self.tmp / "vendor/kSNP4-bin"
        self.vendor.mkdir(parents=True)

    def _payload(self, name, blob):
        p = self.vendor / name
        p.write_bytes(blob)
        p.chmod(0o755)
        return p

    def _run(self, host=("macos", "arm64"), rosetta=False):
        spec = {"binary_format_probes": ["MakeKSNP4infile"],
                "asset_dirs": ["vendor/kSNP4-bin"],
                "fix": "bin/bdtools install ksnp_gui"}
        with mock.patch.object(CHECK.requirements, "for_tool", return_value=spec), \
             mock.patch.object(CHECK, "resolve_asset_dirs",
                               return_value=([str(self.vendor)], [])), \
             mock.patch.object(CHECK, "check_modules", return_value={}), \
             mock.patch.object(CHECK, "_host_target", return_value=host), \
             mock.patch.object(CHECK, "_rosetta_available", return_value=rosetta):
            return CHECK.run_checks("ksnp_gui", str(self.py), "env",
                                    tool_dir=str(self.tmp))

    def test_an_intel_payload_is_not_called_wrong_os(self):
        self._payload("MakeKSNP4infile", X86)
        _status, _lines, issues, _notes = self._run()
        labels = " | ".join(i["label"] for i in issues)
        self.assertNotIn("wrong-OS", labels,
                         "the OS is macOS and so is the binary; only the CPU differs")
        self.assertIn("Intel-only", labels)

    def test_the_remedy_is_not_a_re_download(self):
        self._payload("MakeKSNP4infile", X86)
        _status, _lines, issues, _notes = self._run()
        fixes = " | ".join(i.get("fix", "") for i in issues)
        self.assertIn("--install-rosetta", fixes)
        self.assertNotIn("bdtools install", fixes,
                         "re-fetching an x86_64-only payload returns the same bytes")

    def test_the_note_says_a_native_env_does_not_help(self):
        self._payload("MakeKSNP4infile", X86)
        _status, _lines, _issues, notes = self._run()
        joined = " ".join(notes)
        self.assertIn("native arm64 env does not help", joined)

    def test_a_genuine_wrong_os_payload_still_offers_the_re_download(self):
        # The case the old label was written for, and the one it answered
        # correctly: a macOS host that got the Linux archive.
        self._payload("MakeKSNP4infile", ELF_X86)
        _status, _lines, issues, _notes = self._run()
        labels = " | ".join(i["label"] for i in issues)
        fixes = " | ".join(i.get("fix", "") for i in issues)
        self.assertIn("wrong-OS", labels)
        self.assertIn("bdtools install ksnp_gui", fixes)

    def test_a_runnable_payload_is_no_finding(self):
        self._payload("MakeKSNP4infile", X86)
        _status, _lines, issues, _notes = self._run(rosetta=True)
        self.assertEqual(
            [i for i in issues if "binaries" in i["label"].lower()], [],
            "with Rosetta present the Intel payload runs, as it has for years")


class FixAutomationTests(unittest.TestCase):
    """`bdtools fix --apply` must never run this remedy unattended.

    The remedy for an unrunnable env is a macOS system install that accepts a
    licence, or a command that REPLACES a conda env. Both are exactly what
    fix.sh's auto class excludes. fix_class is extracted from the shipped script
    rather than restated, so a reordering of its cases is caught here instead of
    in production — the same construction FixAutomationTests in
    test_import_diagnosis.py uses.
    """

    def _fix_class(self, cmd):
        src = (ROOT / "bin/fix.sh").read_text()
        start = src.index("fix_class()")
        end = src.index("DOCTOR_ARGS=()")
        out = subprocess.run(
            ["bash", "-c", f"{src[start:end]}\nfix_class {cmd!r}"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_the_rosetta_install_is_never_automatic(self):
        self.assertEqual(
            self._fix_class("softwareupdate --install-rosetta --agree-to-license"),
            "manual")

    def test_a_native_rebuild_is_never_automatic(self):
        self.assertEqual(self._fix_class("bin/bdtools rebuild-native vsnp_gui"),
                         "manual")

    def test_the_real_remedy_string_is_manual(self):
        # The exact string doctor emits: both commands in one line, joined by a
        # comment. It must not be classed by whichever half is matched first.
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        b = tmp / "env/bin"
        b.mkdir(parents=True)
        py = b / "python"
        py.write_bytes(X86)
        py.chmod(0o755)
        with mock.patch.object(CHECK, "_host_target", return_value=("macos", "arm64")), \
             mock.patch.object(CHECK, "_rosetta_available", return_value=False):
            _label, fix, _why = CHECK.env_python_unrunnable(str(py), "vsnp_gui")
        self.assertEqual(self._fix_class(fix), "manual")

    def test_the_existing_classifications_still_hold(self):
        # The two new cases were inserted above `update-packages`; prove the
        # auto ones below them were not shadowed.
        self.assertEqual(self._fix_class('"/x/env/bin/python" -m pip install fastapi'),
                         "auto")
        self.assertEqual(self._fix_class("bin/bdtools setup-databases --home"),
                         "auto")


if __name__ == "__main__":
    unittest.main()
