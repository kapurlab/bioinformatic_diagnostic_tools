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
        self._binary(self.env, "perl", self.X86)
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

    def test_a_system_interpreter_of_the_wrong_arch_is_still_a_defect(self):
        # The exception to the rule above: "outside the env" is tolerable,
        # "cannot execute here" never is.
        self._pkg(self.env, "python", "osx-arm64")
        self._script(self.env, "tool", "#!/usr/bin/env oddterp")
        sysdir = self.tmp / "usr"
        self._binary(sysdir, "oddterp", self.X86)      # x86_64 under arm64 env
        findings = CHECK.interpreter_findings(
            str(self.env), str(self.env / "bin"), ["tool"],
            extra_dirs=[str(sysdir / "bin")])
        self.assertEqual(len(findings), 1)
        self.assertIn("cannot run in this osx-arm64 env", findings[0][0])

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
