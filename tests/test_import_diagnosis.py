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
import platform
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


class SharedLibraryDiagnosisTests(unittest.TestCase):
    """A dlopen failure names the conda package that owns the library.

    The 2026-08 macOS incident: ONE osx-64 openssl linked into an osx-arm64 env
    broke `import ssl`, and doctor reported fastapi, uvicorn and pysam as three
    separate python-package problems — blaming their import chains ("the
    incompatibility is between the packages named above") and prescribing a full
    env rebuild, which failed twice without touching the defect. The accurate
    report is one line: libssl.3.dylib belongs to openssl, reinstall openssl for
    the env's own platform.

    The remedy must carry a channel/subdir-QUALIFIED spec: on the affected
    machine a bare `--force-reinstall openssl` was already satisfied by the
    foreign build, so conda re-linked the same x86_64 package from its cache and
    reported a successful transaction — a convincing false negative.
    """

    # The real error, squashed to one line the way the import probe reports it.
    MAC_ERR = ("ImportError: dlopen(/e/lib/python3.10/lib-dynload/"
               "_ssl.cpython-310-darwin.so, 0x0002): Library not loaded: "
               "@rpath/libssl.3.dylib Referenced from: <UUID> /e/lib/python3.10/"
               "lib-dynload/_ssl.cpython-310-darwin.so Reason: tried: "
               "'/e/lib/libssl.3.dylib' (mach-o file, but is an incompatible "
               "architecture (have 'x86_64', need 'arm64'))")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        envbin = self.tmp / "env/bin"
        envbin.mkdir(parents=True)
        self.env_py = envbin / "python"
        self.env_py.write_text("#!/bin/sh\n")
        self.meta = self.tmp / "env/conda-meta"
        self.meta.mkdir()

    def _pkg(self, name, version, subdir, files=(), channel=None):
        import json
        (self.meta / f"{name}-{version}-h0_0.json").write_text(json.dumps({
            "name": name, "version": version, "subdir": subdir,
            "channel": channel or f"https://conda.anaconda.org/conda-forge/{subdir}",
            "files": list(files),
        }))

    def _mixed_env(self):
        # Majority osx-arm64, one foreign osx-64 openssl — the incident's shape.
        self._pkg("python", "3.10.14", "osx-arm64")
        self._pkg("samtools", "1.20", "osx-arm64")
        self._pkg("pysam", "0.22", "osx-arm64")
        self._pkg("openssl", "3.6.3", "osx-64",
                  files=["lib/libssl.3.dylib", "lib/libcrypto.3.dylib"])

    def test_the_owner_is_named_and_the_spec_is_qualified(self):
        self._mixed_env()
        info = {"absent": False, "error": self.MAC_ERR,
                "chain": ["fastapi", "anyio"]}
        fix, why = CHECK.shared_lib_fix(str(self.env_py), "fastapi", info)
        self.assertIsNotNone(fix)
        self.assertIn("CONDA_SUBDIR=osx-arm64", fix)
        self.assertIn("--force-reinstall", fix)
        self.assertIn("--no-deps", fix)
        self.assertIn('"conda-forge/osx-arm64::openssl"', fix,
                      "an unqualified spec is already satisfied by the foreign "
                      "build and re-links it — the observed false negative")
        self.assertIn("libssl.3.dylib", why)
        self.assertIn("openssl", why)
        self.assertIn("mixed-architecture", why)
        self.assertNotIn("\n", fix)
        self.assertNotIn("\t", fix)

    def test_a_linux_loader_error_is_recognised_too(self):
        self._pkg("python", "3.10.14", "linux-64")
        self._pkg("openssl", "3.6.3", "linux-64", files=["lib/libssl.so.3"])
        info = {"absent": False, "error":
                "ImportError: libssl.so.3: cannot open shared object file: "
                "No such file or directory", "chain": ["pysam"]}
        fix, why = CHECK.shared_lib_fix(str(self.env_py), "pysam", info)
        self.assertIsNotNone(fix)
        self.assertIn('"conda-forge/linux-64::openssl"', fix)
        self.assertIn("openssl", why)

    def test_a_plain_python_error_is_left_to_the_chain_heuristic(self):
        self._mixed_env()
        info = {"absent": False, "chain": ["allel", "dask", "numpy"],
                "error": "AttributeError: `np.round_` was removed in NumPy 2.0"}
        fix, why = CHECK.shared_lib_fix(str(self.env_py), "allel", info)
        self.assertIsNone(fix)
        self.assertIsNone(why)

    def test_an_unowned_library_gets_no_invented_remedy(self):
        self._pkg("python", "3.10.14", "osx-arm64")   # nobody ships libssl here
        info = {"absent": False, "error": self.MAC_ERR, "chain": ["fastapi"]}
        fix, why = CHECK.shared_lib_fix(str(self.env_py), "fastapi", info)
        self.assertIsNone(fix)
        self.assertIn("libssl.3.dylib", why,
                      "even with no remedy, the note must say what failed to "
                      "load rather than blaming the import chain")

    def test_run_checks_reports_the_library_not_the_import_chain(self):
        self._mixed_env()
        from unittest import mock
        failures = {"fastapi": {"absent": False, "error": self.MAC_ERR,
                                "chain": ["fastapi", "anyio"]}}
        with mock.patch.object(CHECK, "check_modules", return_value=failures):
            with mock.patch.object(CHECK, "has_binary", return_value=True):
                with mock.patch.object(CHECK, "resolve_asset_dirs",
                                       return_value=([], [])):
                    status, _lines, issues, notes = CHECK.run_checks(
                        "kraken_id_parse_gui", str(self.env_py), "env",
                        tool_dir=str(self.tmp))
        self.assertEqual(status, "issues")
        joined = " ".join(notes)
        self.assertIn("openssl", joined)
        self.assertNotIn("incompatibility is between the packages named above",
                         joined,
                         "the chain note is the confidently wrong diagnosis "
                         "this replaces for loader failures")
        fixes = " | ".join(i.get("fix", "") for i in issues)
        self.assertIn("conda-forge/osx-arm64::openssl", fixes)
        self.assertNotIn("--fresh", fixes,
                         "the rebuild failed twice on the real incident; the "
                         "one-package reinstall is the fix")


class BinaryArchAuditTests(unittest.TestCase):
    """doctor reads binary architectures off the DISK, never just conda-meta.

    The failure this pins (2026-08-21, the same Mac as the openssl incident,
    hours later): bin/perl on disk was x86_64 while its conda-meta record — and
    every other record — said osx-arm64. An interrupted transaction had left
    files from two extractions in one prefix and the rollback restored only the
    records. Every import check passed (python is not perl), every existence
    check passed (the kraken2 script was there), doctor printed "all 9 installed
    tool(s) ready" — and kraken2, a perl script, died at run time loading an
    arm64 Cwd.bundle into an x86_64 perl:

        Can't load '.../auto/Cwd/Cwd.bundle' for module Cwd: ...
        (mach-o file, but is an incompatible architecture
         (have 'arm64', need 'x86_64'))

    Nothing record-level can see that. The audit reads magic bytes.
    """

    # Minimal Mach-O headers: magic + cputype (offset 4, uint32 LE), padded past
    # the 20-byte floor _binary_target requires.
    X86 = b"\xcf\xfa\xed\xfe" + (0x01000007).to_bytes(4, "little") + b"\x00" * 16
    ARM = b"\xcf\xfa\xed\xfe" + (0x0100000C).to_bytes(4, "little") + b"\x00" * 16
    FAT = b"\xca\xfe\xba\xbe" + b"\x00" * 20                    # universal

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "conda-meta").mkdir()
        # env_py exists so run_checks-level tests get past the presence gate.
        (self.env / "bin/python").write_bytes(b"#!/bin/sh\n" + b"\x00" * 16)

    def _pkg(self, name, subdir, files=()):
        import json
        (self.env / "conda-meta" / f"{name}-1.0-h0_0.json").write_text(json.dumps({
            "name": name, "version": "1.0", "subdir": subdir,
            "channel": f"https://conda.anaconda.org/conda-forge/{subdir}",
            "files": list(files)}))

    def _write(self, rel, payload):
        p = self.env / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)
        return p

    def _arm64_records(self):
        self._pkg("python", "osx-arm64")
        self._pkg("samtools", "osx-arm64")
        self._pkg("perl", "osx-arm64", files=["bin/perl"])

    def test_a_foreign_binary_is_found_where_records_look_clean(self):
        self._arm64_records()                       # records: coherent arm64
        self._write("bin/perl", self.X86)           # disk: the x86_64 leftover
        self._write("bin/samtools", self.ARM)
        got = CHECK.foreign_arch_files(str(self.env))
        self.assertEqual(got, [("bin/perl", "macos/x86_64")])

    def test_native_libraries_under_lib_are_audited_too(self):
        self._arm64_records()
        self._write("lib/perl5/5.32/vendor_perl/auto/Cwd/Cwd.bundle", self.ARM)
        self._write("lib/libssl.3.dylib", self.X86)     # the openssl shape
        got = CHECK.foreign_arch_files(str(self.env))
        self.assertEqual(got, [("lib/libssl.3.dylib", "macos/x86_64")])

    def test_scripts_and_universal_binaries_are_not_flagged(self):
        self._arm64_records()
        self._write("bin/kraken2", b"#!/usr/bin/env perl\nuse Cwd;\n" + b" " * 8)
        self._write("bin/universal-tool", self.FAT)
        self.assertEqual(CHECK.foreign_arch_files(str(self.env)), [])

    def test_a_coherent_rosetta_env_is_not_flagged(self):
        # The rule is env-relative, never host-relative: an all-osx-64 env on
        # Apple Silicon runs whole under Rosetta and is healthy.
        self._pkg("python", "osx-64")
        self._pkg("perl", "osx-64", files=["bin/perl"])
        self._write("bin/perl", self.X86)
        self.assertEqual(CHECK.foreign_arch_files(str(self.env)), [])

    def test_deliberate_per_platform_payloads_are_not_flagged(self):
        # ont-fast5-api ships one plugin per platform side by side and picks at
        # run time. The first live run of this audit flagged all three — a
        # false positive that would have cost doctor its credibility.
        elf_arm = b"\x7fELF" + b"\x00" * 14 + (0xB7).to_bytes(2, "little") + b"\x00" * 4
        elf_x86 = b"\x7fELF" + b"\x00" * 14 + (0x3E).to_bytes(2, "little") + b"\x00" * 4
        self._pkg("python", "osx-64")
        base = "lib/python3.10/site-packages/ont_fast5_api/vbz_plugin"
        self._write(f"{base}/libvbz_hdf_plugin_m1.dylib", self.ARM)
        self._write(f"{base}/libvbz_hdf_plugin_aarch64.so", elf_arm)
        self._write(f"{base}/libvbz_hdf_plugin_x86_64.so", elf_x86)
        # ...and an arch-tagged vendored file OUTSIDE the python tree.
        self._write("lib/vendor/libplugin_arm64.dylib", self.ARM)
        self.assertEqual(CHECK.foreign_arch_files(str(self.env)), [])

    def test_the_python_tree_is_the_import_probes_jurisdiction(self):
        # A wrong-arch .so under lib/python* is real breakage, but the import
        # probe already catches it with the loader's own error (and
        # shared_lib_fix names the owner) — the audit must not double-report
        # the tree where deliberate multi-platform vendoring also lives.
        self._arm64_records()
        self._write("lib/python3.10/lib-dynload/_ssl.cpython-310-darwin.so",
                    self.X86)
        self.assertEqual(CHECK.foreign_arch_files(str(self.env)), [])

    def test_run_checks_names_the_file_the_package_and_the_qualified_fix(self):
        self._arm64_records()
        self._write("bin/perl", self.X86)
        from unittest import mock
        with mock.patch.object(CHECK, "check_modules", return_value={}):
            with mock.patch.object(CHECK, "has_binary", return_value=True):
                with mock.patch.object(CHECK, "resolve_asset_dirs",
                                       return_value=([], [])):
                    status, _lines, issues, notes = CHECK.run_checks(
                        "kraken_id_parse_gui", str(self.env / "bin/python"),
                        "env", tool_dir=str(self.tmp))
        self.assertEqual(status, "issues",
                         "a tool whose analysis binary cannot exec is not ready")
        labels = " | ".join(i["label"] for i in issues)
        self.assertIn("bin/perl is macos/x86_64", labels)
        fixes = " | ".join(i.get("fix", "") for i in issues)
        self.assertIn("CONDA_SUBDIR=osx-arm64", fixes)
        self.assertIn("--force-reinstall", fixes)
        self.assertIn('"conda-forge/osx-arm64::perl"', fixes)
        joined = " ".join(notes)
        self.assertIn("conda-meta still records", joined)
        self.assertIn("perl", joined)

    def test_a_clean_env_reports_the_audit_green(self):
        self._arm64_records()
        self._write("bin/perl", self.ARM)
        from unittest import mock
        with mock.patch.object(CHECK, "check_modules", return_value={}):
            with mock.patch.object(CHECK, "has_binary", return_value=True):
                with mock.patch.object(CHECK, "resolve_asset_dirs",
                                       return_value=([], [])):
                    status, lines, issues, _notes = CHECK.run_checks(
                        "kraken_id_parse_gui", str(self.env / "bin/python"),
                        "env", tool_dir=str(self.tmp))
        self.assertEqual(status, "ok", issues)
        self.assertIn("on-disk binaries match the env platform (osx-arm64)",
                      " | ".join(t for _s, t, _f in lines))


class ScriptInterpreterTests(unittest.TestCase):
    """What RUNS a script matters as much as the script being there.

    bioconda ships kraken2 as a perl script with `#!/usr/bin/env perl`, so PATH
    decides which perl executes it. On 2026-08-21 doctor reported "all 9
    installed tool(s) ready" — kraken2 present, every module importing, the
    on-disk audit green — and the run died instantly because `env perl` landed
    in a DIFFERENT conda env whose perl binary was x86_64 while its perl module
    tree was arm64:

        Can't load '.../envs/kraken_id_parse/lib/perl5/.../Cwd.bundle':
        (mach-o file, but is an incompatible architecture
         (have 'arm64', need 'x86_64'))

    Nothing doctor had could see it: the existence check passes on the script,
    the import probe covers python and not perl, and the arch audit grades the
    env the tool launches from — not the one its shebang wanders into.
    """

    X86 = b"\xcf\xfa\xed\xfe" + (0x01000007).to_bytes(4, "little") + b"\x00" * 16
    ARM = b"\xcf\xfa\xed\xfe" + (0x0100000C).to_bytes(4, "little") + b"\x00" * 16

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "conda-meta").mkdir()

    def _pkg(self, envdir, name, subdir):
        import json
        (envdir / "conda-meta" / f"{name}-1.0-h0_0.json").write_text(json.dumps(
            {"name": name, "version": "1.0", "subdir": subdir,
             "channel": f"https://conda.anaconda.org/conda-forge/{subdir}"}))

    def _script(self, envdir, name, shebang):
        p = envdir / "bin" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{shebang}\nprint 1;\n")
        p.chmod(0o755)
        return p

    def _binary(self, envdir, name, payload):
        p = envdir / "bin" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)
        p.chmod(0o755)
        return p

    def _find(self, findings, needle):
        return [f for f in findings if needle in f[0]]

    def test_an_interpreter_inside_the_env_is_silent(self):
        self._pkg(self.env, "python", "osx-64")
        self._script(self.env, "kraken2", "#!/usr/bin/env perl")
        # A runnable stand-in perl that owns its libraries and loads Cwd. (An
        # earlier version planted raw Mach-O header bytes here; once the smoke
        # probe ran under the production arch pin, "cannot exec at all" became
        # a correct finding — the fixture was broken, not the check.)
        good = self.env / "bin/perl"
        good.write_text("#!/bin/sh\n"
                        'if [ "$1" = "-MCwd" ]; then exit 0; fi\n'
                        f'echo "{self.env}/lib/perl5/5.32/site_perl"\n')
        good.chmod(0o755)
        self.assertEqual(
            CHECK.interpreter_findings(str(self.env), str(self.env / "bin"),
                                       ["kraken2"]), [])

    def test_the_other_macs_failure_is_named_end_to_end(self):
        # Exact layout: an osx-64 checkout env with NO perl of its own, and a
        # legacy env earlier on PATH whose perl is x86_64 under osx-arm64
        # records — the records/disk split, in the env doctor does not grade.
        self._pkg(self.env, "python", "osx-64")
        self._script(self.env, "kraken2", "#!/usr/bin/env perl")
        legacy = self.tmp / "miniconda3/envs/kraken_id_parse"
        (legacy / "conda-meta").mkdir(parents=True)
        self._pkg(legacy, "perl", "osx-arm64")
        self._binary(legacy, "perl", self.X86)
        findings = CHECK.interpreter_findings(
            str(self.env), str(self.env / "bin"), ["kraken2"],
            extra_dirs=[str(legacy / "bin")])
        self.assertEqual(len(findings), 1, findings)
        label, fix, note = findings[0]
        self.assertIn("kraken2", label)
        self.assertIn("OUTSIDE this env", label)
        self.assertIn(str(legacy / "bin/perl"), label)
        # the remedy makes the tool's own env self-contained
        self.assertIn("CONDA_SUBDIR=osx-64", fix)
        self.assertIn("perl", fix)
        # and the note explains the foreign env's own records/disk split
        self.assertIsNotNone(note)
        self.assertIn("osx-arm64", note)
        self.assertIn("incompatible architecture", note)

    def test_an_interpreter_that_is_another_envs_is_caught(self):
        # The 2026-08-22 failure, exactly. <checkout>/env/bin/perl EXISTS, is
        # executable, is first on PATH, and every path-based check passes — but
        # running it prints the LEGACY env as $^X and loads that env's @INC,
        # because the binary carries another prefix baked in. PATH cannot reach
        # that, which is why three PATH fixes changed nothing. Only asking the
        # interpreter where its libraries are finds it.
        self._pkg(self.env, "python", "osx-arm64")
        self._pkg(self.env, "perl", "osx-arm64")
        self._script(self.env, "kraken2", "#!/usr/bin/env perl")
        legacy = self.tmp / "miniconda3/envs/kraken_id_parse"
        # A perl that answers with the legacy env's @INC, like the real one did.
        fake = self.env / "bin/perl"
        fake.write_text("#!/bin/sh\n"
                        f'echo "{legacy}/lib/perl5/5.32/vendor_perl"\n'
                        f'echo "{legacy}/lib/perl5/core_perl"\n')
        fake.chmod(0o755)
        findings = CHECK.interpreter_findings(
            str(self.env), str(self.env / "bin"), ["kraken2"])
        self.assertEqual(len(findings), 1, findings)
        label, fix, note = findings[0]
        self.assertIn("inside this env but loads its libraries from", label)
        self.assertIn(str(legacy), label)
        self.assertIn("--force-reinstall", fix)
        self.assertIn("perl", fix)
        self.assertIn("CONDA_SUBDIR=osx-arm64", fix)
        self.assertIn("PATH cannot redirect it", note)

    def test_an_interpreter_that_owns_its_libraries_is_silent(self):
        # The healthy shape, verified against a real conda env: @INC inside the
        # env. This must stay quiet or the check is noise.
        self._pkg(self.env, "python", "osx-arm64")
        self._script(self.env, "kraken2", "#!/usr/bin/env perl")
        good = self.env / "bin/perl"
        good.write_text("#!/bin/sh\n"
                        f'echo "{self.env}/lib/perl5/5.32/vendor_perl"\n')
        good.chmod(0o755)
        self.assertEqual(
            CHECK.interpreter_findings(str(self.env), str(self.env / "bin"),
                                       ["kraken2"]), [])

    def test_an_interpreter_that_cannot_be_probed_is_not_accused(self):
        # No probe for this name, or the probe fails: absence of evidence is not
        # evidence of breakage.
        self._pkg(self.env, "python", "osx-64")
        self._script(self.env, "tool", "#!/usr/bin/env oddterp")
        broken = self.env / "bin/oddterp"
        broken.write_text("#!/bin/sh\nexit 3\n")
        broken.chmod(0o755)
        self.assertEqual(
            CHECK.interpreter_findings(str(self.env), str(self.env / "bin"),
                                       ["tool"]), [])

    def test_an_interpreter_that_cannot_load_its_own_module_is_caught(self):
        # The 2026-08-22 root cause. Nothing about the FILES was wrong: the env
        # was one healthy osx-arm64 env, every path resolved into it, and its
        # perl was a universal binary — which matches every host and every env
        # by construction. Its XS bundles were arm64-only, so when the x86_64
        # slice ran (macOS picks the slice from the launching process tree, not
        # from disk) perl died loading its own Cwd. Only executing it can see
        # that.
        self._pkg(self.env, "python", "osx-arm64")
        self._pkg(self.env, "perl", "osx-arm64")
        self._script(self.env, "kraken2", "#!/usr/bin/env perl")
        broken = self.env / "bin/perl"
        broken.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-MCwd" ]; then\n'
            "  echo \"Can't load '.../auto/Cwd/Cwd.bundle' for module Cwd: \"\n"
            '  echo "(mach-o file, but is an incompatible architecture '
            '(have arm64, need x86_64))" >&2\n'
            "  exit 2\n"
            "fi\n"
            f'echo "{self.env}/lib/perl5/5.32/vendor_perl"\n')
        broken.chmod(0o755)
        findings = CHECK.interpreter_findings(
            str(self.env), str(self.env / "bin"), ["kraken2"])
        self.assertEqual(len(findings), 1, findings)
        label, fix, note = findings[0]
        self.assertIn("cannot run", label)
        self.assertIn("Cwd", label)
        self.assertIn("incompatible architecture", label)
        self.assertIn("dies at startup", note)

    def test_a_universal_interpreter_is_named_as_such(self):
        # The slice that runs is not visible on disk, so when the smoke test
        # fails on a fat binary the report must say so — otherwise the reader
        # goes hunting for a wrong file, which is what cost four rounds.
        fat = self.env / "bin/perl"
        # A fat header declaring x86_64 + arm64, enough for _macho_slices.
        import struct
        hdr = struct.pack(">II", 0xcafebabe, 2)
        for cpu in (0x01000007, 0x0100000C):
            hdr += struct.pack(">iiIII", cpu, 3, 0, 0, 0)
        fat.write_bytes(hdr)
        self.assertEqual(CHECK._macho_slices(str(fat)), ["x86_64", "arm64"])

    def test_a_healthy_interpreter_passes_its_smoke_test(self):
        # perl that loads Cwd fine must stay silent, or the check is noise.
        self._pkg(self.env, "python", "osx-arm64")
        self._script(self.env, "kraken2", "#!/usr/bin/env perl")
        good = self.env / "bin/perl"
        good.write_text("#!/bin/sh\n"
                        'if [ "$1" = "-MCwd" ]; then exit 0; fi\n'
                        f'echo "{self.env}/lib/perl5/5.32/vendor_perl"\n')
        good.chmod(0o755)
        self.assertEqual(
            CHECK.interpreter_findings(str(self.env), str(self.env / "bin"),
                                       ["kraken2"]), [])

    def test_a_missing_interpreter_is_reported_with_an_install(self):
        self._pkg(self.env, "python", "linux-64")
        self._script(self.env, "kraken2", "#!/usr/bin/env nosuchinterp")
        findings = CHECK.interpreter_findings(
            str(self.env), str(self.env / "bin"), ["kraken2"])
        self.assertEqual(len(findings), 1)
        self.assertIn("not in this env or on PATH", findings[0][0])
        self.assertIn("nosuchinterp", findings[0][1])

    def test_scripts_sharing_an_interpreter_report_once(self):
        self._pkg(self.env, "python", "osx-64")
        self._script(self.env, "kraken2", "#!/usr/bin/env perl")
        self._script(self.env, "ktImportText", "#!/usr/bin/env perl")
        legacy = self.tmp / "other"
        (legacy / "conda-meta").mkdir(parents=True)
        self._pkg(legacy, "perl", "osx-64")
        self._binary(legacy, "perl", self.X86)
        findings = CHECK.interpreter_findings(
            str(self.env), str(self.env / "bin"), ["kraken2", "ktImportText"],
            extra_dirs=[str(legacy / "bin")])
        self.assertEqual(len(findings), 1, "one interpreter, one finding")
        self.assertIn("kraken2, ktImportText", findings[0][0])

    def test_a_system_interpreter_is_not_a_defect(self):
        # /bin/bash is on every machine, universal on macOS, and named
        # deliberately by conda's own wrapper scripts. The first live run of
        # this check flagged IRMA, picard, kSNP4 and medaka_consensus for it —
        # four false positives whose remedy would have been `conda install
        # bash`. A check that cries wolf is a check people learn to ignore.
        self._pkg(self.env, "python", "osx-64")
        for name, shebang in (("IRMA", "#!/bin/bash"), ("picard", "#!/bin/sh"),
                              ("kSNP4", "#!/usr/bin/env bash")):
            self._script(self.env, name, shebang)
        self.assertEqual(
            CHECK.interpreter_findings(str(self.env), str(self.env / "bin"),
                                       ["IRMA", "picard", "kSNP4"]), [])

    def test_a_host_unexecutable_interpreter_is_still_a_defect(self):
        # The exception to the rule above: "outside the env" is tolerable,
        # "this host cannot exec it" never is. Host pinned to arm64-without-
        # Rosetta so the verdict is deterministic on every CI platform.
        from unittest import mock
        self._pkg(self.env, "python", "osx-arm64")
        self._script(self.env, "tool", "#!/usr/bin/env oddterp")
        sysdir = self.tmp / "usr"
        self._binary(sysdir, "oddterp", self.X86)      # x86_64 under arm64 env
        with mock.patch.object(CHECK, "_host_target",
                               return_value=("macos", "arm64")):
            with mock.patch.object(CHECK, "_rosetta_available",
                                   return_value=False):
                findings = CHECK.interpreter_findings(
                    str(self.env), str(self.env / "bin"), ["tool"],
                    extra_dirs=[str(sysdir / "bin")])
        self.assertEqual(len(findings), 1)
        self.assertIn("cannot execute", findings[0][0])

    def test_a_rosetta_runnable_host_interpreter_is_not_a_finding(self):
        # The audited false positive: an Apple Silicon Mac migrated from Intel
        # keeps an x86_64 Homebrew perl that runs its OWN self-consistent
        # x86_64 module tree under Rosetta every day. The env's subdir
        # constrains the env's own libraries, not a self-contained outside
        # interpreter's — "cannot run in this osx-arm64 env" was factually
        # wrong, and kraken2-class scripts using core modules work fine.
        from unittest import mock
        self._pkg(self.env, "python", "osx-arm64")
        self._script(self.env, "tool", "#!/usr/bin/env oddterp")
        sysdir = self.tmp / "usr"
        self._binary(sysdir, "oddterp", self.X86)      # thin x86_64, Rosetta-ok
        with mock.patch.object(CHECK, "_host_target",
                               return_value=("macos", "arm64")):
            with mock.patch.object(CHECK, "_rosetta_available",
                                   return_value=True):
                findings = CHECK.interpreter_findings(
                    str(self.env), str(self.env / "bin"), ["tool"],
                    extra_dirs=[str(sysdir / "bin")])
        self.assertEqual(findings, [])

    def test_an_absolute_in_env_shebang_and_compiled_binaries_are_silent(self):
        self._pkg(self.env, "python", "osx-64")
        self._binary(self.env, "perl", self.X86)
        self._script(self.env, "picard", f"#!{self.env}/bin/perl")
        self._binary(self.env, "samtools", self.X86)     # not a script at all
        self.assertEqual(
            CHECK.interpreter_findings(str(self.env), str(self.env / "bin"),
                                       ["picard", "samtools"]), [])

    def test_shebang_parsing_handles_flags_and_absolutes(self):
        p = self._script(self.env, "a", "#!/usr/bin/env -S perl -w")
        self.assertEqual(CHECK.script_interpreter(str(p)), ("perl", True))
        p = self._script(self.env, "b", "#!/opt/bin/perl -w")
        self.assertEqual(CHECK.script_interpreter(str(p)), ("/opt/bin/perl", False))
        b = self._binary(self.env, "c", self.X86)
        self.assertIsNone(CHECK.script_interpreter(str(b)))

    def test_run_checks_fails_the_tool_and_prints_the_remedy(self):
        self._pkg(self.env, "python", "osx-64")
        self._binary(self.env, "python", self.X86)
        self._script(self.env, "kraken2", "#!/usr/bin/env perl")
        legacy = self.tmp / "miniconda3/envs/kraken_id_parse"
        (legacy / "conda-meta").mkdir(parents=True)
        self._pkg(legacy, "perl", "osx-arm64")
        self._binary(legacy, "perl", self.X86)
        from unittest import mock
        with mock.patch.object(CHECK, "check_modules", return_value={}):
            with mock.patch.object(
                    CHECK, "resolve_asset_dirs",
                    return_value=([str(legacy / "bin")], [])):
                status, _lines, issues, notes = CHECK.run_checks(
                    "kraken_id_parse_gui", str(self.env / "bin/python"), "env",
                    tool_dir=str(self.tmp))
        self.assertEqual(status, "issues",
                         "a tool whose scripts borrow a broken interpreter is "
                         "not ready, however green everything else looks")
        labels = " | ".join(i["label"] for i in issues)
        self.assertIn("OUTSIDE this env", labels)
        self.assertIn("perl", " | ".join(i.get("fix", "") for i in issues))


class LoaderSmokeTests(unittest.TestCase):
    """A declared program must be able to START, not merely exist.

    Everything before this check asked where a file is, what its bytes claim,
    or what conda recorded. On 2026-08-22 a tool passed all of that and could
    not run: kraken2 was present, executable, correctly resolved, in a coherent
    env, and died the instant it launched because its interpreter loaded
    modules of another architecture. The only check that can see that is
    launching the thing.
    """

    MAC_ARCH = ("dyld[123]: Library not loaded: @rpath/libssl.3.dylib\n"
                "Reason: tried: '/e/lib/libssl.3.dylib' (mach-o file, but is an "
                "incompatible architecture (have 'arm64', need 'x86_64'))")
    LINUX_SO = "blastn: error while loading shared libraries: libidn.so.11: cannot open shared object file: No such file or directory"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "conda-meta").mkdir()

    def _prog(self, name, body):
        p = self.env / "bin" / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)
        return p

    def _find(self, names):
        # loader_smoke_findings returns (findings, notes): the notes channel
        # exists so a budget-truncated run can NAME what it skipped instead of
        # silently passing it — see SmokeBudgetTests.
        found, _notes = CHECK.loader_smoke_findings(
            str(self.env), str(self.env / "bin"), names)
        return found

    def test_a_loader_failure_is_caught_and_quoted(self):
        self._prog("kraken2", f'echo "{self.MAC_ARCH}" >&2\nexit 2\n')
        found = self._find(["kraken2"])
        self.assertEqual(len(found), 1, found)
        label, _fix, note = found[0]
        self.assertIn("cannot start", label)
        self.assertIn("incompatible architecture", label)
        self.assertIn("the failure is in loading it", note)

    def test_a_linux_missing_library_is_caught_too(self):
        # Nothing about this class is macOS-specific: the same check must hold
        # on Linux and WSL, where the wording differs and the fault does not.
        self._prog("blastn", f'echo "{self.LINUX_SO}" >&2\nexit 127\n')
        self.assertEqual(len(self._find(["blastn"])), 1)

    def test_a_program_that_merely_dislikes_its_arguments_passes(self):
        # The narrowness that makes this safe to run against tools whose
        # arguments nobody here knows: only loader errors fail.
        self._prog("picard", 'echo "USAGE: picard <command>" >&2\nexit 1\n')
        self.assertEqual(self._find(["picard"]), [])

    def test_a_healthy_program_passes(self):
        self._prog("samtools", 'echo "samtools 1.20"\n')
        self.assertEqual(self._find(["samtools"]), [])

    def test_a_program_outside_the_env_is_not_judged(self):
        # A system tool brings its own runtime and is not this env's business.
        self.assertEqual(self._find(["sh"]), [])

    def test_an_absent_program_is_left_to_the_existence_check(self):
        self.assertEqual(self._find(["nosuchprogram"]), [])

    def test_the_probe_uses_the_launchers_architecture_pin(self):
        # Doctor must probe the way production launches, or it certifies a
        # configuration nobody uses — that mismatch is exactly why a terminal
        # said healthy while the dashboard could not start the same binary.
        import json as _json
        (self.env / "conda-meta" / "python-1.0-h0.json").write_text(_json.dumps(
            {"name": "python", "version": "1.0", "subdir": "osx-arm64"}))
        pin = CHECK._arch_pin(str(self.env))
        if platform.system() == "Darwin":
            self.assertEqual(pin, ["/usr/bin/arch", "-arm64"])
        else:
            self.assertEqual(pin, [], "no arch pinning off macOS")


class ProbeHygieneTests(unittest.TestCase):
    """Probe children run clean, and their answers are parsed by marker.

    Three cry-wolf vectors, all of which end at the same wrong screen —
    "python modules missing: <everything>" over a healthy env. (1) A site
    module exporting PYTHONHOME makes the env's own python unable to start,
    so doctor blamed the packages for the shell's exports. (2) A probed module
    with an atexit print (update notices, telemetry banners) lands text after
    the JSON payload, and "last line of stdout" stopped being JSON. (3) A cold
    NFS/Lustre cache pushes fifteen heavy imports past any timeout, and the
    timeout collapsed into the same all-missing answer with a fifteen-package
    install remedy — on the platform where doctor is trusted least.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        envbin = self.tmp / "env/bin"
        envbin.mkdir(parents=True)
        self.env_py = envbin / "python"
        self.env_py.write_text("#!/bin/sh\n"
                               f'exec "{sys.executable}" "$@"\n')
        self.env_py.chmod(0o755)

    @unittest.skipUnless(BASH, "a POSIX shell is required for the env stub")
    def test_a_hostile_pythonhome_cannot_fail_the_probe(self):
        # Without the strip, PYTHONHOME=/nonexistent kills the interpreter at
        # startup and every module in the spec is reported missing.
        from unittest import mock
        with mock.patch.dict(os.environ, {"PYTHONHOME": "/nonexistent"}):
            self.assertEqual(CHECK.check_modules(str(self.env_py), ["json"]),
                             {})

    def test_probe_env_strips_the_saboteurs_and_keeps_ld_library_path(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {
                "PYTHONHOME": "/x", "PYTHONPATH": "/x", "PYTHONSTARTUP": "/x",
                "PERL5LIB": "/x", "PERLLIB": "/x", "PERL5OPT": "-Mx",
                "RUBYLIB": "/x", "LD_PRELOAD": "/x/inject.so",
                "LD_LIBRARY_PATH": "/apps/lib"}):
            env = CHECK._probe_env()
        for var in CHECK._PROBE_STRIP_VARS:
            self.assertNotIn(var, env, var)
        # LD_LIBRARY_PATH stays: production children inherit it too, so
        # stripping it would probe a configuration nobody runs. It is
        # REPORTED instead — see LoaderEnvNoteTests.
        self.assertEqual(env.get("LD_LIBRARY_PATH"), "/apps/lib")

    @unittest.skipUnless(BASH, "a POSIX shell is required for the env stub")
    def test_atexit_noise_after_the_payload_is_tolerated(self):
        # One badly behaved dependency printing after the JSON used to turn
        # every module in the spec "missing"; the sentinel makes the payload
        # findable anywhere in stdout.
        noisy = self.tmp / "env/bin/noisy-python"
        noisy.write_text(
            "#!/bin/sh\n"
            f'"{sys.executable}" "$@"\n'
            "rc=$?\n"
            'echo "somepackage 9.9 is available! run pip install -U somepackage"\n'
            "exit $rc\n")
        noisy.chmod(0o755)
        out = CHECK.check_modules(str(noisy), ["json", "nosuchmodule"])
        self.assertEqual(sorted(out), ["nosuchmodule"],
                         "the banner after the payload corrupted the parse")
        self.assertTrue(out["nosuchmodule"]["absent"])

    def test_a_timeout_is_its_own_verdict_not_all_missing(self):
        from unittest import mock
        with mock.patch.object(
                CHECK.subprocess, "run",
                side_effect=subprocess.TimeoutExpired("probe", 120)):
            out = CHECK.check_modules(str(self.env_py), ["numpy", "pandas"])
        self.assertIs(out, CHECK.MODULE_PROBE_TIMED_OUT,
                      "a slow filesystem is not fifteen missing packages")

    def test_run_checks_turns_a_timeout_into_a_note(self):
        from unittest import mock
        (self.tmp / "env/conda-meta").mkdir(parents=True, exist_ok=True)
        with mock.patch.object(CHECK, "check_modules",
                               return_value=CHECK.MODULE_PROBE_TIMED_OUT):
            with mock.patch.object(CHECK, "has_binary", return_value=True):
                with mock.patch.object(CHECK, "resolve_asset_dirs",
                                       return_value=([], [])):
                    status, _lines, issues, notes = CHECK.run_checks(
                        "kraken_id_parse_gui", str(self.env_py), "env",
                        tool_dir=str(self.tmp))
        labels = " | ".join(i["label"] for i in issues)
        self.assertNotIn("modules missing", labels,
                         "the timeout collapsing to all-missing is the bug "
                         "this pins")
        self.assertIn("timed out", " ".join(notes))
        self.assertEqual(status, "ok", issues)

    def test_a_genuinely_unrunnable_interpreter_still_reports_missing(self):
        # The other half must keep working: can't-exec is NOT a timeout, and
        # its all-missing answer is the honest one.
        out = CHECK.check_modules(str(self.tmp / "no-such-python"), ["a"])
        self.assertEqual(sorted(out), ["a"])


class LoaderGateTests(unittest.TestCase):
    """Loader words in OUTPUT are not a verdict; the process FAILING is.

    The audited false positive: TensorFlow-backed stacks (medaka behind
    mhc_gui is the canonical emitter) print "Could not load dynamic library
    libcudart.so..." as a WARNING on CPU-only nodes and exit 0 — a fully
    working install that the old output-only match flagged "installed but
    cannot start" with a --force-reinstall remedy that changes nothing. The
    gate: a process that exits 0 has proven it can start, full stop. The
    same class also extends the loader vocabulary to the Linux/WSL wordings
    the smoke test was blind to — old-glibc HPC hosts fail with
    "version `GLIBC_2.34' not found" and module-system hijacks with "symbol
    lookup error", and both sailed through as PASSED.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "conda-meta").mkdir()

    def _prog(self, name, body):
        p = self.env / "bin" / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)
        return p

    def _find(self, names):
        found, _notes = CHECK.loader_smoke_findings(
            str(self.env), str(self.env / "bin"), names)
        return found

    def test_exit_zero_with_loader_words_passes(self):
        # The mandated fixture: prints "Library not loaded", exits 0. It
        # started; whatever it narrated about optional libraries is its own
        # business.
        self._prog("kraken2",
                   'echo "dyld[9]: Library not loaded: @rpath/libomp.dylib '
                   '(optional probe)" >&2\n'
                   'echo "kraken2 version 2.1.6"\n'
                   "exit 0\n")
        self.assertEqual(self._find(["kraken2"]), [])

    def test_a_cpu_only_gpu_warning_passes(self):
        self._prog("medaka_consensus",
                   'echo "W tensorflow: Could not load dynamic library '
                   "libcudart.so.11: cannot open shared object file: No such "
                   'file or directory" >&2\n'
                   'echo "medaka 1.11"\n')
        self.assertEqual(self._find(["medaka_consensus"]), [])

    def test_glibc_version_not_found_is_caught(self):
        # CentOS-7-era HPC: file present, arch right, records right — only
        # execution shows the host libc is older than the build.
        self._prog("prog",
                   'echo "/lib64/libc.so.6: version \\`GLIBC_2.34\' not found '
                   '(required by /e/env/bin/prog)" >&2\nexit 1\n')
        found = self._find(["prog"])
        self.assertEqual(len(found), 1, found)
        self.assertIn("GLIBC_2.34", found[0][0])

    def test_symbol_lookup_error_is_caught(self):
        self._prog("samtools",
                   'echo "samtools: symbol lookup error: '
                   "/apps/gcc/12.2/lib64/libstdc++.so.6: undefined symbol: "
                   '_ZSt28__throw" >&2\nexit 127\n')
        self.assertEqual(len(self._find(["samtools"])), 1)

    def test_the_new_loader_vocabulary_matches(self):
        # Each literal wording from the platform projection, straight from
        # the loaders that emit them. Subject to the returncode gate above.
        for msg in (
            "dyld[7]: Symbol not found: _EVP_KDF_ctrl",
            "Error loading shared library libz.so.1: No such file or directory",
            "Error relocating /e/bin/prog: hts_open: symbol not found",
            "/lib/ld-linux.so.2: bad ELF interpreter: No such file or directory",
            "ImportError: /e/lib/foo.so: wrong ELF class: ELFCLASS32",
            "bash: line 1: /e/bin/prog: cannot execute: required file not found",
            "version `GLIBCXX_3.4.30' not found",
            "version `CXXABI_1.3.13' not found",
        ):
            self.assertTrue(CHECK._LOADER_ERRORS.search(msg), msg)
        self.assertFalse(
            CHECK._LOADER_ERRORS.search("usage: prog [options] <input>"),
            "plain usage text must never look like a loader failure")


class ThinFatPinTests(unittest.TestCase):
    """Pin only what the pin means: fat exec targets, never thin ones.

    /usr/bin/arch ENFORCES an architecture; production's inherited preference
    only SELECTS among the slices of a FAT binary. ksnp_gui on Apple Silicon
    is HEALTHY by the suite's own rules — its SourceForge payload is
    x86_64-thin and check_binary_format blesses it as Rosetta-runnable — yet
    `arch -arm64 MakeKSNP4infile` dies with "Bad CPU type in executable",
    which the old unconditional pin turned into "installed but cannot start"
    at EVERY install, on the same report that said "vendored binaries match
    this host". Doctor contradicting itself on a working machine.
    """

    X86 = b"\xcf\xfa\xed\xfe" + (0x01000007).to_bytes(4, "little") + b"\x00" * 16
    PIN = ["/usr/bin/arch", "-arm64"]

    @staticmethod
    def _fat():
        import struct
        hdr = struct.pack(">II", 0xcafebabe, 2)
        for cpu in (0x01000007, 0x0100000C):
            hdr += struct.pack(">iiIII", cpu, 3, 0, 0, 0)
        return hdr

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "conda-meta").mkdir()

    def _bin(self, name, payload):
        p = self.env / "bin" / name
        p.write_bytes(payload)
        p.chmod(0o755)
        return p

    def _pin_for(self, path):
        from unittest import mock
        with mock.patch.object(CHECK, "_arch_pin", return_value=list(self.PIN)):
            return CHECK._pin_for_exec(str(self.env), str(path),
                                       str(self.env / "bin"))

    def test_a_thin_binary_is_never_pinned(self):
        # A thin binary has exactly one slice; the launcher's preference
        # cannot change what runs, so the probe must not enforce anything.
        p = self._bin("MakeKSNP4infile", self.X86)
        self.assertEqual(self._pin_for(p), [])

    def test_a_fat_binary_gets_the_launchers_pin(self):
        p = self._bin("perl", self._fat())
        self.assertEqual(self._pin_for(p), self.PIN)

    def test_a_script_is_pinned_by_its_interpreters_fatness(self):
        # exec loads the INTERPRETER, so the fat-or-thin question is asked of
        # that binary, not of the script's own bytes.
        self._bin("fatperl", self._fat())
        fat_script = self.env / "bin/kraken2"
        fat_script.write_text("#!/usr/bin/env fatperl\nprint 1;\n")
        fat_script.chmod(0o755)
        self.assertEqual(self._pin_for(fat_script), self.PIN)
        self._bin("thinperl", self.X86)
        thin_script = self.env / "bin/ktImportText"
        thin_script.write_text("#!/usr/bin/env thinperl\nprint 1;\n")
        thin_script.chmod(0o755)
        self.assertEqual(self._pin_for(thin_script), [])

    def _smoke_argv(self, name):
        from unittest import mock
        calls = []

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="v1.0", stderr="")

        with mock.patch.object(CHECK, "_arch_pin", return_value=list(self.PIN)):
            with mock.patch.object(CHECK.subprocess, "run",
                                   side_effect=fake_run):
                found, _ = CHECK.loader_smoke_findings(
                    str(self.env), str(self.env / "bin"), [name])
        self.assertEqual(found, [])
        return calls[0]

    def test_the_smoke_test_launches_thin_binaries_unpinned(self):
        p = self._bin("kSNP4", self.X86)
        argv = self._smoke_argv("kSNP4")
        self.assertEqual(argv[0], str(p),
                         "arch(1) enforces on thin binaries too — the exact "
                         "'Bad CPU type' false positive")

    def test_the_smoke_test_launches_fat_binaries_pinned(self):
        p = self._bin("perl", self._fat())
        argv = self._smoke_argv("perl")
        self.assertEqual(argv[:2], self.PIN)
        self.assertEqual(argv[2], str(p))


class BinaryTargetTests(unittest.TestCase):
    """Header parsing: shared magics, foreign byte orders, and ELF class.

    Three audited blind spots. Java .class files share 0xCAFEBABE with fat
    Mach-O, so a vendored .class was "built for macOS but this host is Linux".
    Big-endian ELF (s390x, ppc64) had e_machine read little-endian into
    garbage, so the suite claimed ppc support in common.sh and could not
    audit it here. And EI_CLASS was never read, so a 32-bit .so wearing a
    64-bit machine type passed the audit and died at run time with
    "wrong ELF class: ELFCLASS32".
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _file(self, payload, name="probe"):
        p = self.tmp / name
        p.write_bytes(payload)
        return str(p)

    @staticmethod
    def _elf(ei_class=2, ei_data=1, machine=0x3E):
        endian = "little" if ei_data == 1 else "big"
        head = b"\x7fELF" + bytes([ei_class, ei_data, 1, 0]) + b"\x00" * 8
        head += (2).to_bytes(2, endian)          # e_type
        head += machine.to_bytes(2, endian)      # e_machine
        return head + b"\x00" * 8

    def test_a_java_class_file_is_not_a_universal_macho(self):
        # The task's exact fixture: nfat=0x01000000 reads as 16 million
        # slices — no real fat header has more than a handful.
        for version in (0x01000000, 0x00000041):
            p = self._file(b"\xca\xfe\xba\xbe"
                           + version.to_bytes(4, "big") + b"\x00" * 16)
            self.assertIsNone(CHECK._binary_target(p), hex(version))

    def test_a_real_fat_header_is_still_universal(self):
        import struct
        p = self._file(struct.pack(">II", 0xcafebabe, 2) + b"\x00" * 20)
        self.assertEqual(CHECK._binary_target(p), ("macos", "universal"))

    def test_big_endian_elf_reads_in_its_own_byte_order(self):
        self.assertEqual(CHECK._binary_target(
            self._file(self._elf(ei_data=2, machine=0x15))),
            ("linux", "ppc64"))
        self.assertEqual(CHECK._binary_target(
            self._file(self._elf(ei_data=2, machine=0x16))),
            ("linux", "s390x"))

    def test_the_new_64_bit_machines_are_named(self):
        self.assertEqual(CHECK._binary_target(
            self._file(self._elf(machine=0x15))), ("linux", "ppc64le"))
        self.assertEqual(CHECK._binary_target(
            self._file(self._elf(machine=0xF3))), ("linux", "riscv64"))

    def test_a_32_bit_elf_is_its_own_architecture(self):
        # ELFCLASS32 with the x86_64 machine type must NOT wear the 64-bit
        # name, or the audit blesses a file the loader will reject.
        self.assertEqual(CHECK._binary_target(
            self._file(self._elf(ei_class=1, machine=0x3E))), ("linux", "x86"))
        self.assertEqual(CHECK._binary_target(
            self._file(self._elf(ei_class=1, machine=0x03))), ("linux", "i386"))

    def test_the_audit_catches_a_wrong_class_library(self):
        env = self.tmp / "env"
        (env / "conda-meta").mkdir(parents=True)
        (env / "lib").mkdir()
        for name in ("python", "samtools"):
            (env / "conda-meta" / f"{name}-1.0-h0_0.json").write_text(
                '{"name": "%s", "subdir": "linux-64"}' % name)
        (env / "lib/libold.so").write_bytes(self._elf(ei_class=1, machine=0x3E))
        (env / "lib/libnew.so").write_bytes(self._elf())      # healthy 64-bit
        self.assertEqual(CHECK.foreign_arch_files(str(env)),
                         [("lib/libold.so", "linux/x86")])


class HostArchTests(unittest.TestCase):
    """The host's CPU is the kernel's fact, not the current process's.

    platform.machine() reports the architecture of the PROCESS, so doctor run
    under Rosetta — the suite's own default on Apple Silicon, where the conda
    BASE python is osx-64 — judged the host as x86_64 and reported a native
    arm64 vendored binary as unrunnable on the machine that runs it natively.
    sysctl hw.optional.arm64 answers 1 on Apple Silicon no matter which slice
    asks.
    """

    def setUp(self):
        self._saved = list(CHECK._DARWIN_ARCH)
        CHECK._DARWIN_ARCH[:] = []
        self.addCleanup(self._restore)

    def _restore(self):
        CHECK._DARWIN_ARCH[:] = self._saved

    def test_rosetta_cannot_lie_about_the_host(self):
        from unittest import mock
        fake = subprocess.CompletedProcess(["sysctl"], 0, stdout="1\n",
                                           stderr="")
        with mock.patch.object(CHECK.platform, "system",
                               return_value="Darwin"):
            with mock.patch.object(CHECK.platform, "machine",
                                   return_value="x86_64"):
                with mock.patch.object(CHECK.subprocess, "run",
                                       return_value=fake):
                    self.assertEqual(CHECK._host_target(), ("macos", "arm64"))

    def test_an_intel_mac_still_reads_intel(self):
        # hw.optional.arm64 is an unknown oid on Intel; the fallback must not
        # invent Apple Silicon where there is none.
        from unittest import mock
        fake = subprocess.CompletedProcess(["sysctl"], 1, stdout="",
                                           stderr="unknown oid")
        with mock.patch.object(CHECK.platform, "system",
                               return_value="Darwin"):
            with mock.patch.object(CHECK.platform, "machine",
                                   return_value="x86_64"):
                with mock.patch.object(CHECK.subprocess, "run",
                                       return_value=fake):
                    self.assertEqual(CHECK._host_target(), ("macos", "x86_64"))


class ForeignSubdirIntentTests(unittest.TestCase):
    """Records AGREEING with the disk is intent, never corruption.

    The audited misfire: a healthy osx-arm64 env holding a deliberately
    CONDA_SUBDIR=osx-64-installed package (bioconda's osx-arm64 coverage is
    still partial — the suite's own spec documents bracken as osx-64-only).
    The package's record says osx-64 and its bytes are x86_64: conda-meta and
    the disk AGREE, yet the audit note asserted "an interrupted conda
    transaction left files from two extractions" — a fabricated incident —
    and macOS runs the thin binary under Rosetta anyway. Whether it is even
    a problem is the HOST's runnability question, not the env's.
    """

    X86 = b"\xcf\xfa\xed\xfe" + (0x01000007).to_bytes(4, "little") + b"\x00" * 16

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "conda-meta").mkdir()
        (self.env / "bin/python").write_bytes(b"#!/bin/sh\n" + b"\x00" * 16)

    def _pkg(self, name, subdir, files=()):
        import json
        (self.env / "conda-meta" / f"{name}-1.0-h0_0.json").write_text(
            json.dumps({"name": name, "version": "1.0", "subdir": subdir,
                        "channel": "https://conda.anaconda.org/bioconda/"
                                   + subdir,
                        "files": list(files)}))

    def _fixture(self):
        self._pkg("python", "osx-arm64")
        self._pkg("samtools", "osx-arm64")
        self._pkg("bracken", "osx-64", files=["bin/bracken-bin"])
        (self.env / "bin/bracken-bin").write_bytes(self.X86)

    def _run(self):
        from unittest import mock
        with mock.patch.object(CHECK, "check_modules", return_value={}):
            with mock.patch.object(CHECK, "has_binary", return_value=True):
                with mock.patch.object(CHECK, "resolve_asset_dirs",
                                       return_value=([], [])):
                    return CHECK.run_checks(
                        "kraken_id_parse_gui", str(self.env / "bin/python"),
                        "env", tool_dir=str(self.tmp))

    def test_a_runnable_deliberate_install_is_a_note_not_a_failure(self):
        from unittest import mock
        self._fixture()
        with mock.patch.object(CHECK, "_host_can_run", return_value=True):
            status, _lines, issues, notes = self._run()
        labels = " | ".join(i["label"] for i in issues)
        self.assertNotIn("bracken", labels,
                         "records and disk agree and the host can run it — "
                         "failing this is the fabricated-incident bug")
        joined = " ".join(notes)
        self.assertIn("on purpose", joined)
        self.assertNotIn("interrupted conda transaction left", joined,
                         "the interrupted-transaction accusation belongs only "
                         "to records that CONTRADICT the bytes")
        self.assertEqual(status, "ok", issues)

    def test_an_unrunnable_deliberate_install_is_bad_with_the_true_cause(self):
        from unittest import mock
        self._fixture()
        with mock.patch.object(CHECK, "_host_can_run", return_value=False):
            status, _lines, issues, notes = self._run()
        self.assertEqual(status, "issues")
        labels = " | ".join(i["label"] for i in issues)
        self.assertIn("deliberately installed for osx-64", labels)
        joined = " ".join(notes)
        self.assertIn("not corruption", joined)
        self.assertNotIn("interrupted conda transaction left", joined)


class ArchTagComponentTests(unittest.TestCase):
    """Platform tags in DIRECTORY names shield their payloads too.

    The exclusion was built for ont-fast5-api's per-platform basenames; node
    trees and conda cross-toolchains vendor the same way one directory level
    up — '@img/sharp-linux-x64/libvips-cpp.so', 'lib/gcc/aarch64-conda-
    linux-gnu/.../libgcc_s.so.1' — neutral basenames inside platform-tagged
    dirs, flagged as corruption on every healthy env that carries them.
    """

    X86 = b"\xcf\xfa\xed\xfe" + (0x01000007).to_bytes(4, "little") + b"\x00" * 16
    ELF_ARM = (b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
               + b"\x02\x00" + (0xB7).to_bytes(2, "little") + b"\x00" * 8)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "conda-meta").mkdir(parents=True)

    def _pkg(self, name, subdir):
        (self.env / "conda-meta" / f"{name}-1.0-h0_0.json").write_text(
            '{"name": "%s", "subdir": "%s"}' % (name, subdir))

    def _write(self, rel, payload):
        p = self.env / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)

    def test_a_platform_tagged_directory_shields_its_payload(self):
        # A linux-64 env with a cross-toolchain's target-arch runtime lib —
        # link-time payload, never executed by the host.
        self._pkg("python", "linux-64")
        self._pkg("gcc", "linux-64")
        self._write("lib/gcc/aarch64-conda-linux-gnu/12/libgcc_s.so.1",
                    self.ELF_ARM)
        self.assertEqual(CHECK.foreign_arch_files(str(self.env)), [])

    def test_node_modules_is_pruned_not_descended(self):
        self._pkg("python", "osx-arm64")
        self._pkg("nodejs", "osx-arm64")
        self._write("lib/node_modules/somepkg/build/libquery_engine.dylib",
                    self.X86)
        self.assertEqual(CHECK.foreign_arch_files(str(self.env)), [])

    def test_neutral_wrong_arch_files_still_fire(self):
        # The guard must not swallow the incident class it exists for:
        # neutral names in neutral directories are still judged.
        self._pkg("python", "osx-arm64")
        self._pkg("openssl", "osx-arm64")
        self._write("lib/vendor/libneutral.dylib", self.X86)
        self.assertEqual(CHECK.foreign_arch_files(str(self.env)),
                         [("lib/vendor/libneutral.dylib", "macos/x86_64")])


class PathIdentityTests(unittest.TestCase):
    """Path identity survives symlinks everywhere and case folds on macOS.

    APFS folds case by default, so ONE directory reached as .../BDTools and
    .../bdtools failed every startswith() test: a healthy in-env interpreter
    was classified as another env's ("the same env, spelled differently"),
    and the loader smoke silently SKIPPED binaries it should have probed —
    a false positive and a false negative from the same root.
    """

    def test_case_flips_do_not_break_identity_on_macos(self):
        from unittest import mock
        with mock.patch.object(CHECK.platform, "system",
                               return_value="Darwin"):
            self.assertTrue(CHECK._same_or_under(
                "/x/BDTools/env/bin/perl", "/x/bdtools/env"))

    def test_case_stays_significant_on_linux(self):
        # ext4 is case-sensitive: two casings ARE two directories, and folding
        # them would fabricate ownership.
        from unittest import mock
        with mock.patch.object(CHECK.platform, "system",
                               return_value="Linux"):
            self.assertFalse(CHECK._same_or_under(
                "/x/BDTools/env/bin/perl", "/x/bdtools/env"))

    def test_symlinked_prefixes_compare_equal(self):
        # macOS parks /tmp behind /private — the reason realpath runs on BOTH
        # sides before comparing.
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        real = tmp / "real"
        (real / "bin").mkdir(parents=True)
        link = tmp / "link"
        os.symlink(real, link)
        self.assertTrue(CHECK._same_or_under(str(link / "bin"), str(real)))


class SharedLibVocabularyTests(unittest.TestCase):
    """shared_lib_fix speaks Linux, and knows which libraries are not conda's.

    Without the Linux shapes, every glibc failure fell through to the
    import-chain heuristic — the exact "confidently wrong" path the function
    exists to preempt, preempted only on macOS. And the no-owner branch
    prescribed "only a rebuild can restore it" for libraries that were never
    conda's to ship: no number of rebuilds produces an NVIDIA driver, while
    libcrypt.so.1 has a real one-package fix (libxcrypt) that the rebuild
    advice hid.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        envbin = self.tmp / "env/bin"
        envbin.mkdir(parents=True)
        self.env_py = envbin / "python"
        self.env_py.write_text("#!/bin/sh\n")
        (self.tmp / "env/conda-meta").mkdir()
        (self.tmp / "env/conda-meta/python-3.10-h0_0.json").write_text(
            '{"name": "python", "subdir": "linux-64"}')

    def test_linux_error_shapes_yield_the_library(self):
        cases = {
            "samtools: symbol lookup error: /apps/gcc/12.2/lib64/"
            "libstdc++.so.6: undefined symbol: _ZSt28__throw":
                "libstdc++.so.6",
            "ImportError: /e/lib/python3.10/site-packages/pkg/_ext.so: "
            "undefined symbol: PyFloat_AsDouble": "_ext.so",
            "version `GLIBC_2.34' not found (required by /e/lib/libhts.so.3)":
                "libhts.so.3",
            "Error loading shared library libz.so.1: No such file or "
            "directory (needed by /e/bin/prog)": "libz.so.1",
        }
        for err, lib in cases.items():
            self.assertEqual(CHECK._shared_lib_from_error(err), lib, err)

    def test_the_macos_shapes_are_unchanged(self):
        self.assertEqual(CHECK._shared_lib_from_error(
            "dlopen(...): Library not loaded: @rpath/libssl.3.dylib"),
            "libssl.3.dylib")

    def test_libcrypt_offers_the_libxcrypt_shim_not_a_rebuild(self):
        info = {"absent": False, "chain": [], "error":
                "prog: error while loading shared libraries: libcrypt.so.1: "
                "cannot open shared object file: No such file or directory"}
        fix, why = CHECK.shared_lib_fix(str(self.env_py), "pysam", info)
        self.assertIsNotNone(fix)
        self.assertIn("libxcrypt", fix)
        self.assertIn("CONDA_SUBDIR=linux-64", fix)
        self.assertIn("no longer ship", why)
        self.assertNotIn("only a rebuild", why,
                         "libcrypt IS conda-installable; the rebuild advice "
                         "hid the one-package fix")

    def test_libcuda_names_the_driver_and_prescribes_nothing(self):
        info = {"absent": False, "chain": [], "error":
                "ImportError: libcuda.so.1: cannot open shared object file: "
                "No such file or directory"}
        fix, why = CHECK.shared_lib_fix(str(self.env_py), "torch", info)
        self.assertIsNone(fix)
        self.assertIn("GPU driver", why)
        self.assertNotIn("only a rebuild can restore it", why,
                         "no number of rebuilds produces an NVIDIA driver")


class CrlfTests(unittest.TestCase):
    """A CRLF shebang is invisible to every other check; read the bytes.

    git core.autocrlf=true on WSL rewrites a checkout with \\r\\n and execve
    then hunts for an interpreter literally named "perl\\r" — exit 127,
    "/usr/bin/env: 'perl\\r': No such file or directory". script_interpreter
    .strip()s the decoded shebang, deleting the very \\r that kills the exec,
    so the interpreter check resolves 'perl' cleanly and passes; the smoke
    test's exec raises FileNotFoundError, which the probe loop treats as
    unjudgeable. Only the raw first-line bytes tell the truth.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)

    def _script(self, name, payload):
        p = self.env / "bin" / name
        p.write_bytes(payload)
        p.chmod(0o755)
        return p

    def test_a_crlf_shebang_is_named_with_its_carriage_return(self):
        p = self._script("kraken2", b"#!/usr/bin/env perl\r\nprint 1;\r\n")
        found = CHECK.crlf_findings(str(self.env), str(self.env / "bin"),
                                    ["kraken2"])
        self.assertEqual(len(found), 1, found)
        label, fix, note = found[0]
        self.assertIn("Windows line endings (CRLF)", label)
        self.assertIn("perl\\r", label,
                      "the report must show the literal name the kernel is "
                      "hunting for")
        # perl -pi, not sed -i: BSD sed parses 's/\r$//' as the -i backup
        # suffix and dies "command c expects \ followed by text" — reproduced
        # live on macOS during adversarial review. perl is present on macOS,
        # Linux, and WSL alike.
        self.assertIn("perl -pi", fix)
        self.assertIn(str(p), fix)
        self.assertIn("core.autocrlf", note)

    def test_clean_scripts_and_binaries_stay_silent(self):
        self._script("kraken2", b"#!/usr/bin/env perl\nprint 1;\n")
        self._script("samtools", b"\xcf\xfa\xed\xfe" + b"\x00" * 20)
        self.assertEqual(
            CHECK.crlf_findings(str(self.env), str(self.env / "bin"),
                                ["kraken2", "samtools"]), [])

    def test_the_checkouts_own_entry_scripts_are_read_too(self):
        # Entry scripts arrive by git clone — the likeliest CRLF victims.
        checkout = self.tmp / "checkout"
        (checkout / "bin").mkdir(parents=True)
        (checkout / "bin/app.py").write_bytes(
            b"#!/usr/bin/env python3\r\nimport sys\r\n")
        found = CHECK.crlf_findings(str(self.env), str(self.env / "bin"), [],
                                    tool_dir=str(checkout))
        self.assertEqual(len(found), 1)
        self.assertIn("python3\\r", found[0][0])


class MountAndWslTests(unittest.TestCase):
    """noexec mounts and Windows-drive (drvfs) envs are named, not chased.

    noexec: files show rwxr-xr-x, os.access() approves (it ignores mount
    flags), and exec fails EACCES anyway — "Permission denied" on perfect
    permissions reads like corruption and gets answered with chmods and
    reinstalls that cannot help. drvfs: a conda env under /mnt/c has no
    hardlinks, foreign symlinks and 10-50x the latency — builds take hours
    and break in ways that read as corruption. Both are stated facts about
    the filesystem; both were invisible.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)

    def test_a_noexec_mount_is_named(self):
        from unittest import mock
        root = os.path.realpath(str(self.tmp))
        mounts = self.tmp / "mounts"
        mounts.write_text(
            "/dev/root / ext4 rw,relatime 0 0\n"
            f"tmpfs {root} tmpfs rw,nosuid,nodev,noexec,relatime 0 0\n")
        with mock.patch.object(CHECK, "_PROC_MOUNTS", str(mounts)):
            found = CHECK.noexec_findings(str(self.env))
        self.assertEqual(len(found), 1, found)
        label, _fix, note = found[0]
        self.assertIn("noexec", label)
        self.assertIn(root, label)
        self.assertIn("Permission denied", note)

    def test_an_exec_mount_is_silent(self):
        from unittest import mock
        root = os.path.realpath(str(self.tmp))
        mounts = self.tmp / "mounts"
        mounts.write_text(
            "/dev/root / ext4 rw,relatime 0 0\n"
            f"tmpfs {root} tmpfs rw,nosuid,nodev,relatime 0 0\n")
        with mock.patch.object(CHECK, "_PROC_MOUNTS", str(mounts)):
            self.assertEqual(CHECK.noexec_findings(str(self.env)), [])

    def test_no_proc_mounts_is_macos_silence(self):
        from unittest import mock
        with mock.patch.object(CHECK, "_PROC_MOUNTS",
                               str(self.tmp / "no-such-file")):
            self.assertEqual(CHECK.noexec_findings(str(self.env)), [])

    def test_a_wsl_mounted_ext4_disk_under_mnt_is_not_a_windows_drive(self):
        # Adversarial-review catch: `wsl --mount` attaches ext4 disks under
        # /mnt/wsl/<name> — native Linux filesystems, exactly where a careful
        # user parks a big BDTOOLS_HOME. The first draft equated /mnt/* with
        # drvfs by path shape; the mount table's FSTYPE is the truth.
        from unittest import mock
        version = self.tmp / "version"
        version.write_text("Linux version 5.15.167.4-microsoft-standard-WSL2\n")
        mounts = self.tmp / "mounts"
        mounts.write_text(
            "/dev/root / ext4 rw,relatime 0 0\n"
            "none /mnt/wsl tmpfs rw,relatime 0 0\n"
            "/dev/sdd /mnt/wsl/bigdisk ext4 rw,relatime 0 0\n")
        with mock.patch.object(CHECK, "_PROC_VERSION", str(version)), \
             mock.patch.object(CHECK, "_PROC_MOUNTS", str(mounts)):
            self.assertEqual(
                CHECK.wsl_drvfs_findings("/mnt/wsl/bigdisk/bdtools/env"), [])

    def test_fstype_outranks_the_path_even_off_mnt_wsl(self):
        # A wsl --mount at a custom point (/mnt/data) is still ext4 — silent;
        # while a real Windows drive is 9p — flagged. Same paths, different
        # mount tables, opposite verdicts: the decision is the fstype's.
        from unittest import mock
        version = self.tmp / "version"
        version.write_text("Linux version 5.15.167.4-microsoft-standard-WSL2\n")
        mounts = self.tmp / "mounts"
        mounts.write_text("/dev/root / ext4 rw 0 0\n"
                          "/dev/sdd /mnt/data ext4 rw 0 0\n")
        with mock.patch.object(CHECK, "_PROC_VERSION", str(version)), \
             mock.patch.object(CHECK, "_PROC_MOUNTS", str(mounts)):
            self.assertEqual(
                CHECK.wsl_drvfs_findings("/mnt/data/bdtools/env"), [])
        mounts.write_text("/dev/root / ext4 rw 0 0\n"
                          "C:\\134 /mnt/data 9p rw,aname=drvfs 0 0\n")
        with mock.patch.object(CHECK, "_PROC_VERSION", str(version)), \
             mock.patch.object(CHECK, "_PROC_MOUNTS", str(mounts)):
            found = CHECK.wsl_drvfs_findings("/mnt/data/bdtools/env")
        self.assertEqual(len(found), 1, found)

    def test_without_a_mount_table_the_fallback_exempts_mnt_wsl(self):
        from unittest import mock
        version = self.tmp / "version"
        version.write_text("Linux version 5.15.167.4-microsoft-standard-WSL2\n")
        with mock.patch.object(CHECK, "_PROC_VERSION", str(version)), \
             mock.patch.object(CHECK, "_PROC_MOUNTS",
                               str(self.tmp / "no-mounts")):
            self.assertEqual(
                CHECK.wsl_drvfs_findings("/mnt/wsl/bigdisk/env"), [])
            self.assertEqual(
                len(CHECK.wsl_drvfs_findings("/mnt/c/bdtools/env")), 1)

    def test_an_env_on_a_windows_drive_is_flagged(self):
        # The mount table is mocked for the same reason every sibling here
        # mocks it, and this test was the one that forgot. Unmocked it reads
        # the HOST's /proc/mounts, where _mount_fstype falls back to "/" for a
        # path no mount covers: on macOS there is no table at all, so the
        # path-shape fallback flagged /mnt/c and this passed, while on Linux
        # "/" answers ext4, the fstype check declines, and it failed — green on
        # one platform and red on the other for a reason that had nothing to do
        # with the code under test. It is why every ubuntu CI run failed from
        # the day CI landed (2026-08-24). A real WSL2 table names /mnt/c 9p.
        from unittest import mock
        version = self.tmp / "version"
        version.write_text("Linux version 5.15.167.4-microsoft-standard-WSL2 "
                           "(gcc ...) #1 SMP\n")
        mounts = self.tmp / "mounts"
        mounts.write_text("/dev/root / ext4 rw 0 0\n"
                          "C:\\134 /mnt/c 9p rw,aname=drvfs 0 0\n")
        with mock.patch.object(CHECK, "_PROC_VERSION", str(version)), \
             mock.patch.object(CHECK, "_PROC_MOUNTS", str(mounts)):
            found = CHECK.wsl_drvfs_findings(
                "/mnt/c/Users/lab/bdtools/checkouts/x/env")
        self.assertEqual(len(found), 1, found)
        label, fix, _note = found[0]
        self.assertIn("drvfs", label)
        self.assertIn(".local/share/bdtools", fix)

    def test_wsl_on_the_linux_filesystem_and_non_wsl_are_silent(self):
        from unittest import mock
        version = self.tmp / "version"
        version.write_text("Linux version 5.15.167.4-microsoft-standard-WSL2\n")
        with mock.patch.object(CHECK, "_PROC_VERSION", str(version)):
            self.assertEqual(CHECK.wsl_drvfs_findings(
                "/home/lab/.local/share/bdtools/checkouts/x/env"), [])
        version.write_text("Linux version 6.8.0-45-generic (buildd@lcy02)\n")
        with mock.patch.object(CHECK, "_PROC_VERSION", str(version)):
            self.assertEqual(CHECK.wsl_drvfs_findings(
                "/mnt/data/bdtools/env"), [],
                "a plain Linux /mnt is a real mount, not drvfs")


class LoaderEnvNoteTests(unittest.TestCase):
    """LD_PRELOAD / LD_LIBRARY_PATH are reported, never failed.

    The Linux twin of the Rosetta slice redirect: HPC module systems export
    them wholesale, and they silently decide what every child loads. Setting
    them is not itself a defect — production may inherit the same values and
    run — so this is a note that puts the likeliest cause of a future
    'symbol lookup error' on the report before it happens.
    """

    def test_ld_preload_is_named_on_linux(self):
        from unittest import mock
        with mock.patch.object(CHECK.platform, "system",
                               return_value="Linux"):
            with mock.patch.dict(os.environ,
                                 {"LD_PRELOAD": "/apps/inject.so",
                                  "LD_LIBRARY_PATH": ""}):
                notes = CHECK.loader_env_notes()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("LD_PRELOAD", notes[0])
        self.assertIn("/apps/inject.so", notes[0])

    def test_clean_environments_and_macs_are_silent(self):
        from unittest import mock
        with mock.patch.object(CHECK.platform, "system",
                               return_value="Linux"):
            with mock.patch.dict(os.environ, {"LD_PRELOAD": "",
                                              "LD_LIBRARY_PATH": ""}):
                self.assertEqual(CHECK.loader_env_notes(), [])
        with mock.patch.object(CHECK.platform, "system",
                               return_value="Darwin"):
            with mock.patch.dict(os.environ,
                                 {"LD_LIBRARY_PATH": "/opt/lib"}):
                self.assertEqual(CHECK.loader_env_notes(), [],
                                 "macOS loading is dyld's business; these "
                                 "notes are Linux facts")


class SmokeBudgetTests(unittest.TestCase):
    """The smoke test may stop early, but never silently.

    33 declared binaries at up to 60s per attempt on a wedged NFS mount is
    the better part of an hour of serial hanging inside the one report a
    user already in a failure state is waiting on. The budget stops the
    LAUNCHING; the note names every binary not probed, because a check that
    silently skips is indistinguishable from a check that passed.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "conda-meta").mkdir()
        for name in ("alpha", "beta"):
            p = self.env / "bin" / name
            p.write_text(f'#!/bin/sh\necho "{name} 1.0"\n')
            p.chmod(0o755)

    def test_an_exhausted_budget_names_the_unprobed(self):
        from unittest import mock
        with mock.patch.dict(os.environ,
                             {"BDTOOLS_SMOKE_BUDGET_SECS": "0"}):
            found, notes = CHECK.loader_smoke_findings(
                str(self.env), str(self.env / "bin"), ["alpha", "beta"])
        self.assertEqual(found, [])
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("alpha", notes[0])
        self.assertIn("beta", notes[0])
        self.assertIn("not probed", notes[0])

    def test_the_default_budget_probes_everything(self):
        from unittest import mock
        with mock.patch.dict(os.environ):
            os.environ.pop("BDTOOLS_SMOKE_BUDGET_SECS", None)
            found, notes = CHECK.loader_smoke_findings(
                str(self.env), str(self.env / "bin"), ["alpha", "beta"])
        self.assertEqual((found, notes), ([], []))


class MemoTests(unittest.TestCase):
    """Per-process caches: the same question is not re-asked of the disk.

    env_conda_subdir is consulted five-plus times per tool and used to
    re-read every conda-meta/*.json each time; _macho_slices is now asked
    once per probe launch. On NFS/Lustre those repeats were doctor's own
    metadata storm. An env cannot change platform mid-run, so one read is
    the honest amount of work.
    """

    def test_env_conda_subdir_reads_the_metadata_once(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = tmp / "env"
        (env / "conda-meta").mkdir(parents=True)
        (env / "conda-meta/python-1.0-h0_0.json").write_text(
            '{"name": "python", "subdir": "osx-arm64"}')
        self.assertEqual(CHECK.env_conda_subdir(str(env)), "osx-arm64")
        shutil.rmtree(env / "conda-meta")
        self.assertEqual(CHECK.env_conda_subdir(str(env)), "osx-arm64",
                         "the cache answers within one process lifetime")

    def test_macho_slices_are_read_once_per_path(self):
        import struct
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        fat = tmp / "fatbin"
        hdr = struct.pack(">II", 0xcafebabe, 2)
        for cpu in (0x01000007, 0x0100000C):
            hdr += struct.pack(">iiIII", cpu, 3, 0, 0, 0)
        fat.write_bytes(hdr)
        self.assertEqual(CHECK._macho_slices(str(fat)), ["x86_64", "arm64"])
        fat.write_bytes(b"")
        self.assertEqual(CHECK._macho_slices(str(fat)), ["x86_64", "arm64"])


class StaleSiblingTests(unittest.TestCase):
    """A sibling tool's package left inside this env caps every future solve.

    The lab-Mac case: amr_plus's env predates the mlst/kraken2 sibling split,
    so a stale mlst package still sits in it — and mlst's perl closure makes
    the manifest pin ncbi-amrfinderplus=4.2.7 unsatisfiable. Every `bdtools
    update` "finishes" successfully and changes nothing, forever, and no line
    of output names why: the modules import, the binaries resolve, the arch
    audit is green. The env is healthy; it just cannot move. Detection is a
    conda-meta FILENAME scan against the manifest's own `packages:` pins —
    no JSON parse, free on every doctor run.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = self.tmp / "env"
        (self.env / "bin").mkdir(parents=True)
        (self.env / "conda-meta").mkdir()
        (self.env / "bin/python").write_bytes(b"#!/bin/sh\n" + b"\x00" * 16)

    def _meta(self, dist):
        # Filename-only records: the check reads names, never the JSON.
        (self.env / "conda-meta" / f"{dist}.json").write_text("{}")

    def test_a_stale_mlst_inside_an_amr_env_is_caught(self):
        self._meta("mlst-2.33.1-hdfd78af_1")
        self._meta("ncbi-amrfinderplus-3.12.8-h283d18e_0")
        got = CHECK.stale_sibling_packages(
            "amr_plus_gui", str(self.env),
            ["mlst_gui", "kraken_id_parse_gui"])
        self.assertEqual(got, ["mlst"])

    def test_a_clean_env_is_silent(self):
        self._meta("ncbi-amrfinderplus-4.2.7-h283d18e_0")
        self.assertEqual(CHECK.stale_sibling_packages(
            "amr_plus_gui", str(self.env),
            ["mlst_gui", "kraken_id_parse_gui"]), [])

    def test_a_name_that_merely_extends_a_sibling_pin_is_not_stale(self):
        # Adversarial-review catch: the first draft prefix-matched 'name-'
        # against dist filenames, so kraken2-server read as a stale kraken2 and
        # genoflu-multi as a stale genoflu. Dist names are name-version-build
        # with hyphens forbidden in version and build; the exact parse drops
        # the last two fields and must NOT match these.
        self._meta("kraken2-server-1.0-h0_0")
        self._meta("genoflu-multi-1.2-pyhdfd78af_0")
        self.assertEqual(CHECK.stale_sibling_packages(
            "amr_plus_gui", str(self.env),
            ["mlst_gui", "kraken_id_parse_gui", "genoflu_gui"]), [])

    def test_a_hyphenated_sibling_package_still_matches_exactly(self):
        # ...while the same parse keeps matching genuinely hyphenated names.
        self._meta("kraken2-2.17.1-pl5321h0_0")
        got = CHECK.stale_sibling_packages(
            "amr_plus_gui", str(self.env),
            ["mlst_gui", "kraken_id_parse_gui"])
        self.assertEqual(got, ["kraken2"])

    def test_a_tools_own_package_is_never_stale(self):
        # kraken2 inside kraken_id_parse_gui's env — or mlst inside
        # mlst_gui's — is that tool's OWN analysis package, whatever the
        # sibling list says.
        self._meta("mlst-2.33.1-hdfd78af_1")
        self.assertEqual(CHECK.stale_sibling_packages(
            "mlst_gui", str(self.env), ["mlst_gui"]), [])

    def test_run_checks_fails_the_env_and_names_the_loop(self):
        from unittest import mock
        self._meta("mlst-2.33.1-hdfd78af_1")
        self._meta("ncbi-amrfinderplus-3.12.8-h283d18e_0")
        with mock.patch.object(CHECK, "check_modules", return_value={}):
            with mock.patch.object(CHECK, "has_binary", return_value=True):
                with mock.patch.object(CHECK, "resolve_asset_dirs",
                                       return_value=([], [])):
                    with mock.patch.object(CHECK, "sibling_handoff",
                                           return_value=(str(self.env), "")):
                        status, _lines, issues, notes = CHECK.run_checks(
                            "amr_plus_gui", str(self.env / "bin/python"),
                            "env", tool_dir=str(self.tmp))
        self.assertEqual(status, "issues")
        labels = " | ".join(i["label"] for i in issues)
        self.assertIn("stale copies of sibling", labels)
        self.assertIn("mlst", labels)
        fixes = " | ".join(i.get("fix", "") for i in issues)
        self.assertIn("--fresh", fixes)
        self.assertIn("cannot remove", fixes,
                      "--rebuild is additive; the fix must say why --fresh")
        self.assertIn("never converges", " ".join(notes))


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

    def test_the_shared_library_reinstall_is_never_automatic(self):
        # Small and targeted, but still a conda transaction on a live env:
        # proposed with an accurate explanation, never run unattended.
        self.assertEqual(
            self._fix_class('CONDA_SUBDIR=osx-arm64 conda install -y -p "/x/env" '
                            '--no-deps --force-reinstall '
                            '"conda-forge/osx-arm64::openssl"'),
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
