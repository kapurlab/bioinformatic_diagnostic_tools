#!/usr/bin/env python3
"""Regression tests for install/update robustness (bin/lib/common.sh, check-updates.sh).

Covers the three failure modes behind a real macOS update failure:
  1. a conda env updated for a platform other than the one it was built for,
  2. an upstream activation hook that reads CONDA_BACKUP_* under `set -u` and
     takes the whole conda transaction down with it,
  3. a build that failed half-way being reported as "already up to date" by the
     next `bdtools update` — and one failing tool ending an `update all` run.

No conda and no network: the conda parts are exercised against synthetic
conda-meta/ and hook files, and the update parts against local git repos.
"""
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "bin/lib/common.sh"


def sh(script, env=None, cwd=None):
    """Run a bash snippet with common.sh sourced. Returns CompletedProcess."""
    full = f'set -euo pipefail\nsource "{COMMON}"\n{script}\n'
    e = dict(os.environ)
    e.pop("DRY_RUN", None)
    if env:
        e.update(env)
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True,
                          env=e, cwd=cwd)


def write(path, text, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    if mode is not None:
        path.chmod(mode)
    return path


def meta(envdir, records):
    """Write synthetic conda-meta/*.json records: {name: subdir}."""
    (envdir / "conda-meta").mkdir(parents=True, exist_ok=True)
    for i, (name, subdir) in enumerate(records):
        (envdir / "conda-meta" / f"{name}-1.0-h0_{i}.json").write_text(
            '{\n  "build": "h0_%d",\n  "name": "%s",\n  "subdir": "%s"\n}\n' % (i, name, subdir))


class EnvPlatformTests(unittest.TestCase):
    """env_conda_subdir: an existing env's architecture, read from what it contains."""

    def subdir_of(self, records):
        with tempfile.TemporaryDirectory() as td:
            envdir = Path(td) / "env"
            meta(envdir, records)
            r = sh(f'env_conda_subdir "{envdir}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip()

    def test_single_platform_env(self):
        self.assertEqual(self.subdir_of([("python", "osx-64"), ("spades", "osx-64")]), "osx-64")

    def test_noarch_is_not_a_platform(self):
        # A noarch-only env has no architecture to pin; don't invent one.
        self.assertEqual(self.subdir_of([("pyyaml", "noarch")]), "")
        self.assertEqual(self.subdir_of([("pyyaml", "noarch"), ("python", "linux-64")]), "linux-64")

    def test_mixed_env_reports_the_majority(self):
        # The exact shape of the bug: an osx-64 env with a few arm64 packages
        # linked in. The env is still osx-64, and that is what must be pinned.
        recs = [("python", "osx-64"), ("openssl", "osx-64"), ("samtools", "osx-64"),
                ("spades", "osx-arm64")]
        self.assertEqual(self.subdir_of(recs), "osx-64")

    def test_foreign_packages_are_reported(self):
        # The real shape from a Mac: an osx-64 env with a handful of osx-arm64
        # packages linked in by an update that solved for the wrong platform.
        with tempfile.TemporaryDirectory() as td:
            envdir = Path(td) / "env"
            meta(envdir, [("python", "osx-64"), ("openssl", "osx-64"), ("samtools", "osx-64"),
                          ("spades", "osx-arm64"), ("picard", "osx-arm64"), ("zstd", "noarch")])
            r = sh(f'env_foreign_subdirs "{envdir}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "2 osx-arm64")

    def test_coherent_env_reports_no_foreign_packages(self):
        with tempfile.TemporaryDirectory() as td:
            envdir = Path(td) / "env"
            meta(envdir, [("python", "linux-64"), ("pyyaml", "noarch")])
            r = sh(f'env_foreign_subdirs "{envdir}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "")
            # and a missing env is not an error
            self.assertEqual(sh(f'env_foreign_subdirs "{td}/nope"').returncode, 0)

    def test_missing_env_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = sh(f'env_conda_subdir "{td}/nope"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "")
        r = sh("env_conda_subdir ''")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_host_subdir_is_known_for_this_machine(self):
        r = sh("host_conda_subdir")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(r.stdout.strip(),
                      {"linux-64", "linux-aarch64", "linux-ppc64le", "osx-64", "osx-arm64"})

    def test_install_local_pins_the_existing_env_over_a_preset(self):
        # ensure_conda_subdir must prefer what is on disk to CONDA_SUBDIR, on any
        # platform: solving for another subdir mixes architectures in one prefix.
        with tempfile.TemporaryDirectory() as td:
            envdir = Path(td) / "checkout" / "env"
            meta(envdir, [("python", "osx-64")])
            # A usable env, not just conda-meta: the resolver deliberately
            # ignores a python-less prefix (see the divergence test below).
            write(envdir / "bin/python", "#!/bin/sh\n", mode=0o755)
            script = f'''
              DIR="{Path(td) / 'checkout'}"; TOOL=faketool; ENV_NAME=fake
              # the functions under test, lifted from install-local.sh
              eval "$(sed -n '/^resolve_env_prefix()/,/^}}/p;/^ensure_conda_subdir()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              CONDA_SUBDIR=osx-arm64 ensure_conda_subdir
              echo "SUBDIR=${{CONDA_SUBDIR}}"
            '''
            r = sh(script)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("SUBDIR=osx-64", r.stdout)
            self.assertIn("was built for osx-64", r.stderr)

    def test_subdir_decision_and_mutation_target_cannot_diverge(self):
        # THE 2026-08 macOS incident, exactly: <checkout>/env is the corpse of a
        # dead osx-64 create (conda-meta, no python) while the tool actually
        # runs from a NAMED osx-arm64 env. The old code read CONDA_SUBDIR off
        # the corpse through one resolver and ran `conda install` on the named
        # env through another — one foreign openssl later, the env was mixed
        # and unrepairable. The invariant: the platform pinned for the build
        # equals the platform of the prefix the mutations will target.
        with tempfile.TemporaryDirectory() as td:
            checkout = Path(td) / "checkout"
            meta(checkout / "env", [("openssl", "osx-64")])       # corpse: no bin/python
            named = Path(td) / "base/envs/kraken_id_parse"
            meta(named, [("python", "osx-arm64"), ("openssl", "osx-arm64")])
            write(named / "bin/python", "#!/bin/sh\n", mode=0o755)
            conda_stub = write(Path(td) / "stub/conda", f'''\
                #!/bin/sh
                case "$1" in
                  env) echo "kraken_id_parse  {named}";;
                  run) echo "{named}";;
                esac
                ''', mode=0o755)
            script = f'''
              DIR="{checkout}"; TOOL=faketool; ENV_NAME=kraken_id_parse
              detect_conda() {{ printf '%s' "{conda_stub}"; }}
              eval "$(sed -n '/^resolve_env_prefix()/,/^}}/p;/^ensure_conda_subdir()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              ensure_conda_subdir
              target="$(resolve_env_prefix)"
              echo "TARGET=${{target}}"
              echo "SUBDIR=${{CONDA_SUBDIR:-unset}}"
              echo "TARGET_SUBDIR=$(env_conda_subdir "${{target}}")"
            '''
            r = sh(script)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(f"TARGET={named}", r.stdout,
                          "the mutations' resolver must find the named env")
            self.assertIn("SUBDIR=osx-arm64", r.stdout,
                          "the corpse's osx-64 conda-meta must get no vote")
            # the invariant itself:
            self.assertIn("TARGET_SUBDIR=osx-arm64", r.stdout)

    def test_a_pythonless_prefix_is_not_an_env(self):
        # conda-meta alone is what a dead `conda env create` leaves behind. If
        # the resolver called that an env, the corpse would anchor platform
        # decisions again (the divergence test above shows what that cost).
        with tempfile.TemporaryDirectory() as td:
            checkout = Path(td) / "checkout"
            meta(checkout / "env", [("openssl", "osx-64")])
            script = f'''
              DIR="{checkout}"; TOOL=faketool; ENV_NAME=""
              eval "$(sed -n '/^resolve_env_prefix()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              echo "PREFIX=[$(resolve_env_prefix)]"
            '''
            r = sh(script)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("PREFIX=[]", r.stdout)


class FreshRebuildTests(unittest.TestCase):
    """`install --fresh` — the one command for an env that is wrong, not incomplete.

    Its whole value is that it cannot lose a working env: the old prefix is moved
    aside, not deleted, and a build that fails puts it back. That promise is what
    these tests hold to, because the alternative recipe it replaces (`rm -rf
    <env> && bdtools install <tool>`) had already cost a working macOS install.
    """
    def _harness(self, envdir, body):
        return sh(f'''
          TOOL=faketool; FRESH=1; DRY_RUN=0
          snapshot_env() {{ :; }}
          tool_env_prefix() {{ printf '%s' "{envdir}"; }}
          # the functions under test, lifted from install-local.sh
          eval "$(sed -n '/^FRESH_ASIDE=""; FRESH_ORIG=""/,/^  FRESH_ASIDE=""$/p' \
                    "{ROOT}/bin/install-local.sh"; echo '}}')"
          {body}
        ''')

    def test_a_failed_build_puts_the_old_env_back_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / "env"
            write(env / "bin/python", "original\n")
            write(env / "lib/python3.10/site-packages/fastapi/__init__.py", "x\n")
            r = self._harness(env, f'''
              discard_env_for_fresh
              [[ -d "{env}" ]] && {{ echo "STILL-THERE"; exit 1; }}
              mkdir -p "{env}"; echo half > "{env}/junk"   # a half-built new env
              restore_env_from_fresh
            ''')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((env / "bin/python").read_text(), "original\n")
            self.assertTrue((env / "lib/python3.10/site-packages/fastapi/__init__.py").exists(),
                            "the pip layer must come back with the env")
            self.assertFalse((env / "junk").exists(),
                             "the half-built env must not survive the restore")
            self.assertEqual(list(Path(td).glob("env.bdtools-old-*")), [],
                             "the set-aside copy must not be left behind")

    def test_the_old_env_is_only_moved_never_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / "env"
            write(env / "bin/python", "original\n")
            r = self._harness(env, "discard_env_for_fresh")
            self.assertEqual(r.returncode, 0, r.stderr)
            aside = list(Path(td).glob("env.bdtools-old-*"))
            self.assertEqual(len(aside), 1, "the env should be set aside, not removed")
            self.assertEqual((aside[0] / "bin/python").read_text(), "original\n")

    def test_an_env_the_user_cannot_write_is_refused(self):
        # On a shared install the resolved env can be one somebody else owns.
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "shared"
            env = parent / "env"
            env.mkdir(parents=True)
            parent.chmod(0o555)
            try:
                r = self._harness(env, "discard_env_for_fresh")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("not yours to replace", r.stdout + r.stderr)
                self.assertTrue(env.exists())
            finally:
                parent.chmod(0o755)

    def test_no_existing_env_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._harness(Path(td) / "nope", "discard_env_for_fresh")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no env here yet", r.stdout)

    def test_dry_run_moves_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / "env"
            write(env / "bin/python", "original\n")
            r = self._harness(env, "DRY_RUN=1; discard_env_for_fresh")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((env / "bin/python").exists())
            self.assertEqual(list(Path(td).glob("env.bdtools-old-*")), [])

    def test_the_cli_accepts_the_flag(self):
        # It was rejected as an unknown option for --rebuild while the dashboard
        # told people to run it; a flag the UI advertises must reach the installer.
        for flag in ("--fresh", "--rebuild"):
            r = subprocess.run([str(ROOT / "bin/bdtools"), "install",
                                "kraken_id_parse_gui", flag, "--dry-run"],
                               capture_output=True, text=True)
            self.assertNotIn("unknown option", r.stdout + r.stderr, flag)


class LaunchPathTests(unittest.TestCase):
    """The env that provides a tool's python must provide its PATH.

    The 2026-08-21 macOS failure, third of the same family and the one that
    actually stopped analyses: `launch()` took its python from
    resolve_env_prefix (the checkout env) and its PATH from tool_launch's
    PATH_PREPEND, which had resolved a LEGACY personal conda env of the same
    manifest name. kraken2 — a perl script shebanged `#!/usr/bin/env perl` —
    was found correctly by absolute path and then executed by the legacy env's
    perl, which loaded that env's arm64 perl modules into an x86_64 interpreter:

        Can't load '.../envs/kraken_id_parse/.../Cwd.bundle': (mach-o file, but
        is an incompatible architecture (have 'arm64', need 'x86_64'))

    Every pre-flight check passed — they all name absolute paths — and doctor
    was green, because nothing graded the PATH the tool would actually launch
    with.
    """

    def _merge(self, envbin, prepend):
        r = sh(f'''
          eval "$(sed -n '/^merge_launch_path()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
          merge_launch_path "{envbin}" "{prepend}"
        ''')
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_the_launch_env_bin_always_comes_first(self):
        # tool_launch pointing at a different env must not displace it.
        got = self._merge("/checkouts/kraken_id_parse_gui/env/bin",
                          "/home/miniconda3/envs/kraken_id_parse/bin")
        self.assertEqual(
            got, "/checkouts/kraken_id_parse_gui/env/bin:"
                 "/home/miniconda3/envs/kraken_id_parse/bin")
        self.assertTrue(got.startswith("/checkouts/kraken_id_parse_gui/env/bin"),
                        "python and PATH must come from the same env")

    def test_vendored_asset_dirs_are_kept_after_the_env(self):
        # ksnp_gui's kSNP4 payload lives outside the env and must stay on PATH —
        # this is what PATH_PREPEND is legitimately for.
        got = self._merge("/checkouts/ksnp_gui/env/bin",
                          "/checkouts/ksnp_gui/env/bin:/vendor/kSNP4-bin")
        self.assertEqual(got, "/checkouts/ksnp_gui/env/bin:/vendor/kSNP4-bin")

    def test_the_env_bin_is_not_duplicated(self):
        got = self._merge("/e/bin", "/e/bin")
        self.assertEqual(got, "/e/bin")

    def test_no_prepend_is_just_the_env_bin(self):
        self.assertEqual(self._merge("/e/bin", ""), "/e/bin")

    def test_empty_segments_are_dropped(self):
        # An empty PATH entry means "the current directory" to the shell —
        # never something a tool launch should introduce.
        self.assertEqual(self._merge("/e/bin", "::/vendor/bin:"),
                         "/e/bin:/vendor/bin")


class ArchPinTests(unittest.TestCase):
    """A launch states the env's architecture, and says so even under Rosetta.

    macOS picks which slice of a UNIVERSAL binary to run from the launching
    process tree's inherited preference. An osx-arm64 env holding a universal
    perl with arm64-only XS modules therefore runs or dies depending on who
    started it — the 2026-08-22 failure.

    The subtle half, and why this test exists: the first version guarded the
    arm64 pin with `[[ "$(uname -m)" == arm64 ]]`. Inside a translated process
    `uname -m` reports x86_64, so the guard was FALSE exactly when the caller
    was the Rosetta process whose preference needed overriding. Observed
    directly: `bdtools local` from an arm64 shell pinned and the tool worked,
    while the dashboard did not pin and the same env failed the same way it had
    all week.
    """

    def _prefix(self, records, fake_uname=False):
        with tempfile.TemporaryDirectory() as td:
            envdir = Path(td) / "env"
            meta(envdir, records)
            fake = ('uname() { if [[ "${1:-}" == "-m" ]]; then echo x86_64; '
                    'else echo Darwin; fi; }\n') if fake_uname else ""
            r = sh(
                "eval \"$(sed -n '/^arch_prefix()/,/^}/p' "
                f'"{ROOT}/bin/install-local.sh")"\n'
                f"{fake}"
                f'arch_prefix "{envdir}"; echo')
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip()

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_an_arm64_env_is_pinned_to_arm64(self):
        self.assertEqual(self._prefix([("python", "osx-arm64")]),
                         "/usr/bin/arch -arm64")

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_the_pin_survives_a_translated_caller(self):
        # THE REGRESSION. A translated caller sees x86_64 from uname -m; the pin
        # must still be emitted, or it vanishes precisely when it is needed.
        self.assertEqual(self._prefix([("python", "osx-arm64")], fake_uname=True),
                         "/usr/bin/arch -arm64")

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_a_rosetta_env_is_pinned_to_x86_64(self):
        # The deliberate osx-64-under-Rosetta env (ensure_conda_subdir rule 2).
        self.assertEqual(self._prefix([("python", "osx-64")]),
                         "/usr/bin/arch -x86_64")

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_an_env_with_no_recorded_platform_is_not_pinned(self):
        # noarch-only: nothing to assert, so assert nothing.
        self.assertEqual(self._prefix([("pyyaml", "noarch")]), "")

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS-only behaviour")
    def test_the_two_launchers_agree(self):
        # bdtools local goes through install-local.sh; the proxy dashboard goes
        # through tool_launch. They must pin identically — two launchers
        # disagreeing about a tool's environment is a bug this suite has now
        # paid for three separate times.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "tool_launch", ROOT / "bin/lib/tool_launch.py")
        tl = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(tl)
        except Exception as exc:
            self.skipTest(f"tool_launch not importable here: {exc}")
        with tempfile.TemporaryDirectory() as td:
            for records, expected in (
                    ([("python", "osx-arm64")], "/usr/bin/arch -arm64"),
                    ([("python", "osx-64")], "/usr/bin/arch -x86_64")):
                envdir = Path(td) / ("env-" + records[0][1])
                meta(envdir, records)
                self.assertEqual(" ".join(tl._arch_prefix(str(envdir))), expected,
                                 "tool_launch and install-local must agree")
                self.assertEqual(self._prefix(records), expected)

    def test_sibling_arch_handoff_matches_each_siblings_own_env(self):
        # One backend runs binaries from ANOTHER tool's env via
        # BDTOOLS_SIBLING_ENV_<TOOL> — under the CALLER's inherited pin. When
        # the sibling env's subdir differs (real: mixed osx-64/osx-arm64 envs
        # on one Mac), that is the Cwd.bundle incident again, one cross-tool
        # call away and invisible to every file check. So each sibling env
        # export must be accompanied by BDTOOLS_SIBLING_ARCH_<TOOL>: a
        # ready-to-splice pin derived from the SIBLING's env, not the caller's.
        import importlib.util
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            # caller osx-arm64, sibling osx-64: the pins MUST differ, or the
            # test could pass with the caller's pin leaking onto the sibling.
            for name, subdir in (("toola", "osx-arm64"), ("toolb", "osx-64")):
                d = home / "checkouts" / name
                (d / "backend").mkdir(parents=True)
                meta(d / "env", [("python", subdir)])
                write(d / "env/bin/python", "#!/bin/sh\n", mode=0o755)
            manifest = write(Path(td) / "tools.yml", """\
                suite_version: test-1
                tools:
                  - name: toola
                    repo: file:///dev/null
                    version: v0.1.0
                    env: toola
                  - name: toolb
                    repo: file:///dev/null
                    version: v0.1.0
                    env: toolb
                """)
            keys = ("BDTOOLS_MANIFEST", "BDTOOLS_HOME", "BDTOOLS_TOOLSDIR")
            saved = {k: os.environ.get(k) for k in keys}
            os.environ["BDTOOLS_MANIFEST"] = str(manifest)
            os.environ["BDTOOLS_HOME"] = str(home)
            os.environ.pop("BDTOOLS_TOOLSDIR", None)
            try:
                spec = importlib.util.spec_from_file_location(
                    "tool_launch_sibling_fixture", ROOT / "bin/lib/tool_launch.py")
                tl = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(tl)
                except Exception as exc:
                    self.skipTest(f"tool_launch not importable here: {exc}")
                plan = tl.resolve("toola", 0)
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            ov = plan["env_overrides"]
            sib_env = str(home / "checkouts/toolb/env")
            self.assertEqual(ov.get("BDTOOLS_SIBLING_ENV_TOOLB"), sib_env)
            # present whenever the env export is (even when empty), and equal
            # to the pin the launcher itself would derive for that env — so a
            # consumer can splice it unconditionally.
            self.assertIn("BDTOOLS_SIBLING_ARCH_TOOLB", ov)
            self.assertEqual(ov["BDTOOLS_SIBLING_ARCH_TOOLB"],
                             " ".join(tl._arch_prefix(sib_env)))
            if os.uname().sysname == "Darwin":
                self.assertEqual(ov["BDTOOLS_SIBLING_ARCH_TOOLB"],
                                 "/usr/bin/arch -x86_64",
                                 "the sibling's pin comes from the SIBLING's subdir")
                self.assertEqual(plan["argv"][:2], ["/usr/bin/arch", "-arm64"],
                                 "the caller stays pinned to its OWN env")
            # A terminal reproduce that drops the handoff resolves the sibling
            # differently from the run it claims to copy — it must ride along.
            repro = tl.reproduce_command(plan)
            self.assertIn("BDTOOLS_SIBLING_ENV_TOOLB=", repro)
            self.assertIn("BDTOOLS_SIBLING_ARCH_TOOLB=", repro)

    def test_launch_pins_before_the_first_env_interpreter_run(self):
        # launch()'s vsnp config-repair heredoc runs the env python. It used to
        # run a hundred lines BEFORE the arch pin was computed, so a universal
        # python whose off-preference slice cannot start failed launch() with a
        # loader error the pinned path would never show. Asserted against the
        # source shape because the heredoc is unreachable under --dry-run and
        # the pinned path ends in an exec — there is no cheap behavioral probe.
        src = (ROOT / "bin/install-local.sh").read_text(encoding="utf-8")
        start = src.index("launch() {")
        body = src[start:src.index("\nensure_checkout", start)]
        pin_at = body.index('_archp="$(arch_prefix')
        heredoc_at = body.index("<<'PY'")
        self.assertLess(pin_at, heredoc_at,
                        "the pin must exist before the heredoc python runs")
        # both env-python runs carry the same splice, computed exactly once
        self.assertIn('${_arch_cmd[@]+"${_arch_cmd[@]}"} "${py}" - <<\'PY\'', body)
        self.assertIn('exec ${_arch_cmd[@]+"${_arch_cmd[@]}"} "${py}"', body)
        self.assertEqual(body.count('_archp="$(arch_prefix'), 1,
                         "one pin, computed once — two computations can drift")


class FreshGateTests(unittest.TestCase):
    """generic_build under --fresh must never no-op on an adoptable external env.

    --fresh means START OVER. When the tool's env lives outside the checkout and
    (for any reason) was not set aside, the adopt-the-external-env branch used to
    print "left as it is" and return 0 — a silent no-op that then reached
    build_state_ok and CLEARED the failed-build record, so `bdtools update`
    reported a build finished that had built nothing (2026-08, macOS).
    """

    def _build(self, fresh, external):
        with tempfile.TemporaryDirectory() as td:
            checkout = Path(td) / "checkout"
            write(checkout / "conda_setup/environment.yml", "name: fake\n")
            script = f'''
              DIR="{checkout}"; TOOL=faketool; ENV_NAME=fake
              REBUILD=0; FRESH={fresh}; FRESH_ORIG=""
              detect_conda() {{ printf 'fake-conda'; }}
              resolve_env_prefix() {{ printf '%s' "{external}"; }}
              with_progress() {{ echo "WOULD_BUILD: $*"; }}
              _conda_step() {{ :; }}
              harden_conda_hooks() {{ :; }}
              ensure_env_java() {{ :; }}
              _note_fresh_relocation() {{ :; }}
              _clear_partial_checkout_env() {{ :; }}
              eval "$(sed -n '/^generic_build()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              generic_build
            '''
            return sh(script)

    def test_without_fresh_an_external_env_is_adopted(self):
        r = self._build(fresh=0, external="/somewhere/envs/fake")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("using this machine's existing", r.stdout)
        self.assertNotIn("WOULD_BUILD", r.stdout)

    def test_with_fresh_the_env_is_actually_built(self):
        r = self._build(fresh=1, external="/somewhere/envs/fake")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WOULD_BUILD", r.stdout,
                      "--fresh must build, not adopt and report success")
        self.assertNotIn("left as it is", r.stdout)


class HookHardeningTests(unittest.TestCase):
    """harden_conda_hooks: an upstream hook must not be able to fail a transaction."""

    # Shaped like the real conda-forge compiler hook that failed on macOS: a
    # shebang, an early-exit branch, then a run of unguarded CONDA_BACKUP_*
    # restores (the failure was reported at line 63 of the real file).
    BROKEN = '''\
        #!/bin/bash
        if [ "${CONDA_BUILD_STATE:-}" = "BUILD" ]; then
          :
        fi
        export AR="${CONDA_BACKUP_AR}"
        unset CONDA_BACKUP_AR
        export AS="${CONDA_BACKUP_AS}"
        unset CONDA_BACKUP_AS
        export RANLIB="${CONDA_BACKUP_RANLIB}"
        unset CONDA_BACKUP_RANLIB
        '''

    def hook(self, td, body=None, name="deactivate.d/deactivate_cctools_osx-64.sh"):
        return write(Path(td) / "env/etc/conda" / name, body or self.BROKEN, mode=0o644)

    def source_under_set_u(self, hook):
        """Reproduce what conda does around a post-link script: source with -u on."""
        return subprocess.run(
            ["bash", "-c", f'set -u\nsource "{hook}"\ncase $- in *u*) echo SET_U_RESTORED;; esac\n'],
            capture_output=True, text=True)

    def test_broken_hook_fails_before_and_works_after(self):
        with tempfile.TemporaryDirectory() as td:
            hook = self.hook(td)
            before = self.source_under_set_u(hook)
            self.assertNotEqual(before.returncode, 0)
            self.assertIn("CONDA_BACKUP_AR: unbound variable", before.stderr)

            r = sh(f'harden_conda_hooks "{Path(td) / "env"}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("guarded 1 conda activation hook", r.stdout)

            after = self.source_under_set_u(hook)
            self.assertEqual(after.returncode, 0, after.stderr)
            # and the caller's `set -u` is put back, not silently dropped
            self.assertIn("SET_U_RESTORED", after.stdout)

    def test_shebang_stays_on_line_one(self):
        with tempfile.TemporaryDirectory() as td:
            hook = self.hook(td)
            sh(f'harden_conda_hooks "{Path(td) / "env"}"')
            lines = hook.read_text().splitlines()
            self.assertEqual(lines[0], "#!/bin/bash")
            self.assertIn("bdtools set-u guard", lines[1])
            self.assertEqual(hook.read_text().count("#!/bin/bash"), 1)

    def test_hook_that_returns_early_still_sources_cleanly(self):
        # Some upstream hooks bail out mid-file. The guard must not turn that into
        # a syntax error or an unbound-variable failure.
        with tempfile.TemporaryDirectory() as td:
            hook = self.hook(td, body='#!/bin/bash\nexport CC=cc\nreturn 0\nexport AR="${CONDA_BACKUP_AR}"\n')
            sh(f'harden_conda_hooks "{Path(td) / "env"}"')
            r = self.source_under_set_u(hook)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            hook = self.hook(td)
            sh(f'harden_conda_hooks "{Path(td) / "env"}"')
            once = hook.read_text()
            r = sh(f'harden_conda_hooks "{Path(td) / "env"}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("guarded", r.stdout)          # nothing left to do
            self.assertEqual(hook.read_text(), once)       # byte-identical

    def test_unaffected_hooks_are_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            body = '#!/bin/bash\nexport JAVA_HOME="${CONDA_PREFIX}/lib/jvm"\n'
            hook = self.hook(td, body=body, name="activate.d/openjdk_activate.sh")
            sh(f'harden_conda_hooks "{Path(td) / "env"}"')
            self.assertEqual(hook.read_text(), body)

    def test_file_mode_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            hook = self.hook(td)
            hook.chmod(0o755)
            sh(f'harden_conda_hooks "{Path(td) / "env"}"')
            self.assertEqual(hook.stat().st_mode & 0o777, 0o755)

    def test_missing_env_and_dry_run_are_no_ops(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(sh(f'harden_conda_hooks "{td}/nope"').returncode, 0)
            self.assertEqual(sh("harden_conda_hooks ''").returncode, 0)
            hook = self.hook(td)
            r = sh(f'harden_conda_hooks "{Path(td) / "env"}"', env={"DRY_RUN": "1"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("bdtools set-u guard", hook.read_text())


class BuildStateTests(unittest.TestCase):
    """build_state_*: a build that did not finish must be remembered."""

    def test_record_query_and_clear(self):
        with tempfile.TemporaryDirectory() as td:
            env = {"BDTOOLS_HOME": td}
            r = sh('build_state_fail toolx v1.2.3 "conda exit 1"', env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((Path(td) / "state/toolx.build-failed").exists())

            self.assertEqual(sh("build_failed_for toolx v1.2.3", env=env).returncode, 0)
            # a failure recorded at another ref is stale — the tool has moved on
            self.assertNotEqual(sh("build_failed_for toolx v1.3.0", env=env).returncode, 0)
            # an unrelated tool is unaffected
            self.assertNotEqual(sh("build_failed_for tooly v1.2.3", env=env).returncode, 0)

            self.assertEqual(sh("build_state_ok toolx", env=env).returncode, 0)
            self.assertNotEqual(sh("build_failed_for toolx v1.2.3", env=env).returncode, 0)

    def test_dry_run_records_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            sh("build_state_fail toolx v1 detail", env={"BDTOOLS_HOME": td, "DRY_RUN": "1"})
            self.assertFalse((Path(td) / "state/toolx.build-failed").exists())


class UpdateResilienceTests(unittest.TestCase):
    """check-updates.sh --apply: a failing tool must not be skipped or hide the rest."""

    @classmethod
    def git(cls, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True,
                       capture_output=True, text=True, env=cls.GIT_ENV)

    GIT_ENV = dict(os.environ,
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e",
                   GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.home = td / "home"
        self.home.mkdir()

        # toolfail: has a deploy/install.sh whose success is toggled by an
        # untracked FAIL sentinel. toolskip: nothing to build locally (exit 3).
        self.sources = {}
        for name, files in (
            ("toolfail", {"deploy/install.sh": '#!/bin/sh\n[ -f "$(dirname "$0")/../FAIL" ] && exit 1\nexit 0\n'}),
            ("toolskip", {"README.md": "no local build path\n"}),
        ):
            src = td / "src" / name
            for rel, body in files.items():
                write(src / rel, body, mode=0o755 if rel.endswith(".sh") else None)
            self.git("init", "-q", "-b", "main", cwd=src)
            self.git("add", "-A", cwd=src)
            self.git("commit", "-qm", "v1", cwd=src)
            self.git("tag", "v0.1.0", cwd=src)
            # A distinct commit for v0.2.0: two tags on one commit make
            # `git describe --tags` ambiguous, which is not what an update looks
            # like in practice and would mask whether the checkout really moved.
            write(src / "CHANGES", "v0.2.0\n")
            self.git("add", "-A", cwd=src)
            self.git("commit", "-qm", "v2", cwd=src)
            self.git("tag", "v0.2.0", cwd=src)
            self.sources[name] = src
            self.git("clone", "-q", f"file://{src}", str(self.home / "checkouts" / name),
                     cwd=td)
            self.git("checkout", "-q", "v0.1.0", cwd=self.home / "checkouts" / name)

        self.manifest = write(td / "tools.yml", f"""\
            suite_version: test-1
            tools:
              - name: toolfail
                repo: file://{self.sources['toolfail']}
                version: v0.1.0
                updates: install
                env: toolfail
              - name: toolskip
                repo: file://{self.sources['toolskip']}
                version: v0.1.0
                updates: install
                env: toolskip
            """)
        (self.home / "checkouts/toolfail/FAIL").touch()   # make its build fail

    def tearDown(self):
        self.td.cleanup()

    def update(self):
        env = dict(os.environ, BDTOOLS_HOME=str(self.home),
                   BDTOOLS_MANIFEST=str(self.manifest),
                   BDTOOLS_BUILD_TRIES="1", HOME=str(self.home))
        env.pop("DRY_RUN", None)
        p = subprocess.run([str(ROOT / "bin/check-updates.sh"), "--apply", "all"],
                           capture_output=True, text=True, env=env)
        return p, p.stdout + p.stderr

    def state_file(self, tool):
        return self.home / "state" / f"{tool}.build-failed"

    def test_failure_is_isolated_recorded_and_retried(self):
        p, out = self.update()
        # 1. the failing tool fails loudly and is named in a summary…
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAILED to update: toolfail", out)
        # 2. …but the run continued to every tool after it in the manifest
        self.assertIn("toolskip", out)
        self.assertNotIn("toolskip", out.split("FAILED to update:")[-1].splitlines()[0])
        self.assertIn("v0.2.0", subprocess.run(
            ["git", "-C", str(self.home / "checkouts/toolskip"), "describe", "--tags"],
            capture_output=True, text=True).stdout)
        # 3. the failure is recorded against the ref that failed
        self.assertTrue(self.state_file("toolfail").exists())
        self.assertIn("ref=v0.2.0", self.state_file("toolfail").read_text())

        # 4. Pretend the env survived (it does in the real case: conda rolls the
        #    transaction back, leaving the previous env and its python). The next
        #    update must NOT report this as already up to date.
        write(self.home / "checkouts/toolfail/env/bin/python", "#!/bin/sh\n", mode=0o755)
        p2, out2 = self.update()
        self.assertIn("did not finish", out2)
        self.assertNotIn("already at v0.2.0 with a built env", out2)
        self.assertEqual(p2.returncode, 1, out2)

        # 5. Once the build succeeds the record is cleared, and only then does the
        #    fast path skip the tool.
        (self.home / "checkouts/toolfail/FAIL").unlink()
        p3, out3 = self.update()
        self.assertEqual(p3.returncode, 0, out3)
        self.assertFalse(self.state_file("toolfail").exists())
        p4, out4 = self.update()
        self.assertEqual(p4.returncode, 0, out4)
        self.assertIn("already at v0.2.0 with a built env", out4)

    def test_tool_without_a_local_build_path_is_not_a_failure(self):
        (self.home / "checkouts/toolfail/FAIL").unlink()
        p, out = self.update()
        self.assertEqual(p.returncode, 0, out)
        self.assertNotIn("FAILED", out)
        # toolskip has no environment.yml / deploy/install.sh: install-local exits
        # with its "no local-build path" sentinel, which is a skip, not a failure.
        self.assertFalse(self.state_file("toolskip").exists())

    def test_uninstalled_tool_is_reported_without_failing_the_run(self):
        import shutil
        shutil.rmtree(self.home / "checkouts/toolskip")
        (self.home / "checkouts/toolfail/FAIL").unlink()
        p, out = self.update()
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("not installed (skipped): toolskip", out)


class DoctorReportsUnfinishedBuildTests(unittest.TestCase):
    """doctor must not call an env ready when the build that should have produced it failed."""

    def run_doctor(self, home, manifest, tool):
        env = dict(os.environ, BDTOOLS_HOME=str(home), BDTOOLS_MANIFEST=str(manifest))
        env.pop("DRY_RUN", None)
        p = subprocess.run([str(ROOT / "bin/doctor.sh"), tool],
                           capture_output=True, text=True, env=env)
        return p, p.stdout + p.stderr

    def test_recorded_failure_is_surfaced_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            write(home / "checkouts/toolx/env/bin/python", "#!/bin/sh\n", mode=0o755)
            manifest = write(Path(td) / "tools.yml", """\
                suite_version: test-1
                tools:
                  - name: toolx
                    repo: file:///dev/null
                    version: v0.1.0
                    env: toolx
                """)
            clean, out = self.run_doctor(home, manifest, "toolx")
            self.assertNotIn("did not finish", out)

            write(home / "state/toolx.build-failed", "ref=unknown\nwhen=now\ndetail=test\n")
            failed, out2 = self.run_doctor(home, manifest, "toolx")
            self.assertIn("the last environment build did not finish", out2)
            self.assertEqual(failed.returncode, 1, out2)

    def test_mixed_architecture_env_is_surfaced(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            envdir = home / "checkouts/toolx/env"
            write(envdir / "bin/python", "#!/bin/sh\n", mode=0o755)
            manifest = write(Path(td) / "tools.yml", """\
                suite_version: test-1
                tools:
                  - name: toolx
                    repo: file:///dev/null
                    version: v0.1.0
                    env: toolx
                """)
            meta(envdir, [("python", "osx-64"), ("samtools", "osx-64"), ("spades", "osx-arm64")])
            p, out = self.run_doctor(home, manifest, "toolx")
            self.assertIn("mixed-architecture env", out)
            self.assertIn("1 osx-arm64", out)
            self.assertEqual(p.returncode, 1, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class CondaStepGuardTests(unittest.TestCase):
    """conda transactions must survive a strict ambient shell and a failed attempt.

    The failure this pins, seen on macOS building kraken_id_parse_gui:

        deactivate_clangxx_osx-arm64.sh: CONDA_BACKUP_CLANGXX: unbound variable
        LinkError: post-link script failed for package spades-4.3.0
        ... giving up after 2 attempt(s)

    harden_conda_hooks cannot fix that one. The env was being CREATED, so no hook
    existed to patch; the transaction installed the hook, a post-link script sourced
    it and died, and the rollback deleted the hook again — so the retry began with
    nothing to harden and failed identically. The guard has to be in the environment
    conda is invoked with, not in files that may not exist yet.
    """

    def setUp(self):
        src = (ROOT / "bin/install-local.sh").read_text(encoding="utf-8")
        self.step = src[src.index("_conda_step() {"):src.index("generic_build() {")]
        self.progress = src[src.index("with_progress() {"):src.index("# _run_watched:")]

    def _run(self, snippet, env=None):
        script = (f'set -uo pipefail\nsource "{ROOT}/bin/lib/common.sh"\n'
                  f'{self.step}\n{snippet}\n')
        e = dict(os.environ)
        e.pop("DRY_RUN", None)
        if env:
            e.update(env)
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e)

    def test_an_unguarded_hook_survives_an_exported_shelloopts(self):
        with tempfile.TemporaryDirectory() as tmp:
            hook = Path(tmp) / "postlink.sh"
            hook.write_text('export CLANGXX="${CONDA_BACKUP_CLANGXX}"\necho POSTLINK_OK\n')
            # Without the guard this is the exact macOS failure.
            bare = subprocess.run(
                ["bash", "-c", f'set -u; export SHELLOPTS; bash "{hook}"'],
                capture_output=True, text=True)
            self.assertIn("unbound variable", bare.stderr)
            # Through _conda_step it runs.
            got = self._run(f'_conda_step /nonexistent-env bash "{hook}"',
                            env={"SHELLOPTS": "braceexpand:errexit:nounset:pipefail"})
            self.assertIn("POSTLINK_OK", got.stdout, got.stderr)

    def test_nounset_does_not_reach_the_child(self):
        got = self._run('_conda_step /nonexistent-env bash -c '
                        '\'echo "OPTS=${SHELLOPTS:-none}"\'',
                        env={"SHELLOPTS": "braceexpand:errexit:nounset:pipefail"})
        self.assertIn("OPTS=", got.stdout)
        self.assertNotIn("nounset", got.stdout.split("OPTS=")[1])

    def test_a_flag_value_with_spaces_stays_one_value(self):
        # Built as an array, not a command substitution: unquoted word-splitting on
        # CFLAGS/LDFLAGS/CMAKE_ARGS would turn the rest of the flags into the command.
        got = self._run('_conda_step /nonexistent-env bash -c '
                        '\'echo "[$CONDA_BACKUP_CFLAGS]"\'',
                        env={"CFLAGS": "-O2 -I/with space/inc"})
        self.assertIn("[-O2 -I/with space/inc]", got.stdout, got.stderr)

    def test_every_placeholder_is_defined_even_when_unset_locally(self):
        got = self._run('_conda_step /nonexistent-env bash -c '
                        '\'for v in CLANGXX AR CFLAGS GFORTRAN LDFLAGS; do '
                        'eval "echo $v=\\${CONDA_BACKUP_$v?MISSING}"; done\'')
        self.assertNotIn("MISSING", got.stdout + got.stderr, got.stderr)

    # ---- the platform invariant ---------------------------------------------
    # A conda operation must solve for the platform of the prefix it changes.
    # The 2026-08 macOS incident: CONDA_SUBDIR=osx-64, inherited from a subdir
    # decision made against a DIFFERENT prefix, linked an osx-64 openssl into an
    # osx-arm64 env — a mixed env nothing additive could repair.

    def test_a_mutation_that_contradicts_the_envs_platform_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "env"
            meta(env, [("python", "osx-arm64"), ("openssl", "osx-arm64")])
            got = self._run(f'_conda_step "{env}" echo SHOULD_NOT_RUN',
                            env={"CONDA_SUBDIR": "osx-64"})
            self.assertNotEqual(got.returncode, 0,
                                "a contradictory subdir must refuse to run")
            self.assertNotIn("SHOULD_NOT_RUN", got.stdout)
            # both values, so the message is actionable
            self.assertIn("osx-64", got.stdout + got.stderr)
            self.assertIn("osx-arm64", got.stdout + got.stderr)

    def test_a_matching_subdir_runs_and_an_absent_one_is_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "env"
            meta(env, [("python", "osx-arm64")])
            got = self._run(f'_conda_step "{env}" echo RAN',
                            env={"CONDA_SUBDIR": "osx-arm64"})
            self.assertEqual(got.returncode, 0, got.stderr)
            self.assertIn("RAN", got.stdout)
            # No ambient subdir: the prefix's own platform is pinned, so the
            # solve can never default to the host's.
            got = self._run(f'_conda_step "{env}" bash -c '
                            '\'echo "PINNED=${CONDA_SUBDIR:-none}"\'')
            self.assertEqual(got.returncode, 0, got.stderr)
            self.assertIn("PINNED=osx-arm64", got.stdout)

    def test_a_prefix_that_does_not_exist_yet_is_not_guarded(self):
        # `conda env create` runs before there is anything to contradict.
        got = self._run('_conda_step /nonexistent-env echo CREATED',
                        env={"CONDA_SUBDIR": "osx-64"})
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("CREATED", got.stdout)

    # ---- what a FAILED build may and may not delete -------------------------
    # Run for real rather than asserted against the source text: the old version
    # of this test checked that the guards were present, and they were — they were
    # just the wrong guards. "No working python" is exactly the state a rolled-back
    # update of an EXISTING env leaves behind, so the cleanup deleted the user's
    # env and every retry then failed with nothing to fall back on. That is what
    # cost a working kraken_id_parse_gui on macOS.
    def _retry_harness(self, envdir, extra="", tries=2):
        src = (ROOT / "bin/install-local.sh").read_text(encoding="utf-8")
        def chunk(start, end):
            return src[src.index(start):src.index(end)]
        body = (chunk("_tree_cpu_ticks() {", "_watched_bytes() {")
                + chunk("_watched_bytes() {", "with_progress() {")
                + chunk("with_progress() {", "_conda_step() {"))
        script = (f'set -uo pipefail\nsource "{ROOT}/bin/lib/common.sh"\n'
                  f'DIR="{envdir}/.."\nENV_NAME=test\nTOOL=testtool\n'
                  f'BDTOOLS_BUILD_TRIES={tries}\nBDTOOLS_HEARTBEAT_SECS=1\n'
                  f'{body}\n'
                  # Stand in for _conda_step: with_progress keys its cleanup on the
                  # payload being "_conda_step <envdir> ...", so the name matters.
                  f'_conda_step() {{ _ENV_PREEXISTING=0; '
                  f'[[ -x "$1/bin/python" ]] && _ENV_PREEXISTING=1; return 1; }}\n'
                  f'{extra}\n'
                  f'with_progress "build" _conda_step "{envdir}" || true\n')
        # BDTOOLS_HOME pinned inside the sandbox: with_progress writes the build
        # log under it, and a test must not touch the real one.
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              env={**os.environ, "DRY_RUN": "0",
                                   "BDTOOLS_HOME": str(Path(envdir).parent)},
                              timeout=120)

    def test_a_failed_build_never_deletes_an_env_that_was_already_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "env"
            (env / "bin").mkdir(parents=True)
            py = env / "bin/python"
            py.write_text("#!/bin/sh\n")
            py.chmod(0o755)
            (env / "conda-meta").mkdir()
            keep = env / "conda-meta/vsnp3-3.35-h0.json"
            keep.write_text("{}")
            self._retry_harness(str(env))
            self.assertTrue(py.exists(), "a failed build deleted a working env")
            self.assertTrue(keep.exists(), "a failed build deleted installed packages")

    def test_a_prefix_this_step_created_is_still_cleared_between_attempts(self):
        # The original reason the cleanup exists: conda's rollback leaves untracked
        # __pycache__ behind and attempt 2 dies in a ClobberError storm. A prefix
        # that had no python when the step began was never the user's working env.
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "env"
            (env / "lib").mkdir(parents=True)
            (env / "lib/__pycache__").mkdir()
            self._retry_harness(str(env))
            self.assertFalse(env.exists(),
                             "a partially-created prefix should be cleared before "
                             "the retry")

    def test_the_final_failure_leaves_no_partial_prefix_behind(self):
        # Between-attempt cleanup existed; give-up cleanup did not — so the last
        # failed attempt's corpse (conda-meta, no python) survived the run. That
        # corpse is what a later build's subdir decision mistook for an existing
        # env, seeding the 2026-08 mixed-architecture macOS incident.
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "env"
            r = self._retry_harness(str(env), extra=(
                '_conda_step() { _ENV_PREEXISTING=0; mkdir -p "$1/conda-meta"; '
                'return 1; }'))
            self.assertIn("giving up", r.stdout + r.stderr)
            self.assertFalse(env.exists(),
                             "the corpse of the final failed attempt must not "
                             "survive to vote on a later build's platform")

    def test_the_final_failure_still_never_deletes_a_preexisting_env(self):
        # Same protection at give-up as between attempts: an env that was here
        # before the step is never ours to delete, however broken it looks.
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "env"
            (env / "bin").mkdir(parents=True)
            py = env / "bin/python"
            py.write_text("#!/bin/sh\n")
            py.chmod(0o755)
            self._retry_harness(str(env), tries=1)
            self.assertTrue(py.exists(),
                            "a final failure deleted a pre-existing env")

    def test_a_failed_build_names_its_log(self):
        # Two failed rebuilds on a Mac once produced no diagnosable information
        # at all: with_progress showed only exit codes and the output lived in
        # scrollback. A failed step must leave its output at a stated path.
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "env"
            r = self._retry_harness(str(env), extra=(
                '_conda_step() { _ENV_PREEXISTING=0; echo "SOLVE DIED: fake"; '
                'return 1; }'), tries=1)
            out = r.stdout + r.stderr
            log = Path(tmp) / "state/build-logs/testtool.log"
            self.assertIn(str(log), out, "the give-up warning must name the log")
            self.assertTrue(log.exists())
            self.assertIn("SOLVE DIED: fake", log.read_text(),
                          "the payload's output must be captured, not discarded")


class PipArchPinTests(unittest.TestCase):
    """The pip layer is built under the env's arch pin, like the launch.

    pip keys wheel selection — and sdist compilation — off the RUNNING
    interpreter's architecture. A universal env python started from a
    translated ancestor (dashboard Update button -> bdtools update -> the pip
    step) therefore fills the env with wrong-arch extension modules while
    conda-meta stays clean, so the arch audit cannot see the damage and the
    loader smoke test (conda-declared binaries only) never exercises it.
    Exercised with a stubbed arch_prefix so the tests run identically on
    every platform; ArchPinTests already proves arch_prefix itself.
    """

    def _generic_build(self, pin):
        with tempfile.TemporaryDirectory() as td:
            checkout = Path(td) / "checkout"
            write(checkout / "conda_setup/environment.yml", "name: fake\n")
            write(checkout / "backend/requirements.txt", "fastapi\n")
            write(checkout / "env/bin/python", "#!/bin/sh\n", mode=0o755)
            r = sh(f'''
              DIR="{checkout}"; TOOL=faketool; ENV_NAME=fake; REBUILD=0; FRESH=0
              detect_conda() {{ printf 'fake-conda'; }}
              harden_conda_hooks() {{ :; }}
              ensure_env_java() {{ :; }}
              arch_prefix() {{ printf '%s' "{pin}"; }}
              run() {{ echo "RUN: $*"; }}
              eval "$(sed -n '/^generic_build()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              generic_build
            ''')
            self.assertEqual(r.returncode, 0, r.stderr)
            return checkout, r.stdout

    def test_backend_pip_install_carries_the_envs_pin(self):
        checkout, out = self._generic_build("/usr/bin/arch -x86_64")
        self.assertIn(
            f"RUN: /usr/bin/arch -x86_64 {checkout}/env/bin/python -m pip "
            f"install -r {checkout}/backend/requirements.txt", out,
            "pip must run under the env's pin, ahead of the interpreter")

    def test_no_pin_means_a_clean_unprefixed_pip_command(self):
        # The bash-3.2 ${arr[@]+...} guard: an empty pin must contribute NO
        # argv word — a stray empty first argument would become the command.
        checkout, out = self._generic_build("")
        self.assertIn(
            f"RUN: {checkout}/env/bin/python -m pip install -r "
            f"{checkout}/backend/requirements.txt", out)

    def test_vsnp_web_layer_pip_carries_the_envs_pin(self):
        # build_vsnp_local's pip targets ENVP (which can be an EXTERNAL env),
        # so its pin must come from ENVP — verified through --dry-run, which
        # prints the exact command without needing conda or the network.
        with tempfile.TemporaryDirectory() as td:
            checkout = Path(td) / "checkout"
            checkout.mkdir(parents=True)
            envp = Path(td) / "external-env"
            write(envp / "bin/pip", "#!/bin/sh\n", mode=0o755)
            r = sh(f'''
              DIR="{checkout}"; TOOL=vsnp_gui; ENV_NAME=vsnp3; FRESH=0
              BDTOOLS_HOME="{td}/bdhome"; DRY_RUN=1
              VSNP_REFS_REPO="https://example.invalid/refs.git"
              detect_conda() {{ printf 'fake-conda'; }}
              resolve_env_prefix() {{ printf '%s' "{envp}"; }}
              harden_conda_hooks() {{ :; }}
              arch_prefix() {{ [[ "$1" == "{envp}" ]] && printf '%s' "/usr/bin/arch -x86_64"; }}
              eval "$(sed -n '/^build_vsnp_local()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              build_vsnp_local
            ''')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(
                f"[dry-run] /usr/bin/arch -x86_64 {envp}/bin/pip install --upgrade",
                r.stdout, "the web-layer pip must run under ENVP's pin")
            # and the managed reference clone carries the line-ending pin —
            # as --config, which PERSISTS in the new checkout's .git/config so
            # every later fetch/checkout inherits it too (adversarial-review
            # upgrade from the transient -c form).
            self.assertIn("[dry-run] git clone --config core.autocrlf=false "
                          "--config core.eol=lf", r.stdout)


class DelegatedInstallerArchTests(unittest.TestCase):
    """build() hands a tool's own deploy/install.sh a ready-to-splice pin.

    CONDA_SUBDIR makes the delegated installer's SOLVES target the right
    platform, but anything it runs FROM the env — pip, version probes,
    post-install smoke, vendored-payload unpackers — executes under the
    caller's inherited arch, and the suite cannot see inside those scripts.
    BDTOOLS_ARCH_PREFIX is the suite-side half of the fix; the tool repos
    adopt it in their installers.
    """

    def _build(self, pin):
        with tempfile.TemporaryDirectory() as td:
            checkout = Path(td) / "checkout"
            write(checkout / "deploy/install.sh",
                  '#!/bin/sh\necho "ARCHPREFIX=[${BDTOOLS_ARCH_PREFIX-unset}]"\n',
                  mode=0o755)
            r = sh(f'''
              DIR="{checkout}"; TOOL=faketool; DRY_RUN=0
              discard_env_for_fresh() {{ :; }}
              ensure_conda_subdir() {{ :; }}
              harden_conda_hooks() {{ :; }}
              resolve_env_prefix() {{ printf '%s' "{checkout}/env"; }}
              arch_prefix() {{ printf '%s' "{pin}"; }}
              enforce_package_pins() {{ :; }}
              enforce_env_constraints() {{ :; }}
              with_progress() {{ shift; "$@"; }}
              eval "$(sed -n '/^build()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              build
            ''')
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout

    def test_the_installer_receives_the_resolved_envs_pin(self):
        self.assertIn("ARCHPREFIX=[/usr/bin/arch -x86_64]",
                      self._build("/usr/bin/arch -x86_64"))

    def test_no_pin_is_exported_as_empty_not_unset(self):
        # Empty-but-present is the contract: installers splice
        # ${BDTOOLS_ARCH_PREFIX} unconditionally, with no existence checks.
        self.assertIn("ARCHPREFIX=[]", self._build(""))


class NpmWslGuardTests(unittest.TestCase):
    """A Windows npm reached through WSL interop must count as 'npm not found'.

    /mnt/c/Program Files/nodejs is on PATH in a default WSL session and ships
    an extensionless `npm` wrapper, so `command -v npm` finds the WINDOWS npm.
    That npm runs Windows node.exe against a Linux-side checkout — it cannot
    resolve the working directory, breaks on CRLF wrappers, and writes
    Windows-format node_modules — and the resulting build failure used to be
    blamed on the Node VERSION, sending users down the wrong path. Exercised
    with fabricated kernel strings and npm paths, never a real WSL box.
    """

    WSL = "Linux version 5.15.167.4-microsoft-standard-WSL2 (gcc ...)"
    LINUX = "Linux version 6.5.0-41-generic (buildd@lcy02) ..."

    def _probe(self, kernel, npm_path):
        return sh(f'''
          eval "$(sed -n '/^_npm_path()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
          _npm_path() {{ printf '%s' "{npm_path}"; }}
          if have_usable_npm "{kernel}"; then echo USABLE; else echo NOT-USABLE; fi
        ''')

    def test_a_windows_npm_under_wsl_is_rejected_loudly(self):
        r = self._probe(self.WSL, "/mnt/c/Program Files/nodejs/npm")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NOT-USABLE", r.stdout)
        # loud, and actionable: name the npm it found and the fix
        self.assertIn("WINDOWS npm", r.stderr)
        self.assertIn("/mnt/c/Program Files/nodejs/npm", r.stderr)
        self.assertIn("install Linux node inside WSL", r.stderr)

    def test_a_linux_npm_under_wsl_is_accepted_silently(self):
        r = self._probe(self.WSL, "/usr/bin/npm")
        self.assertIn("USABLE", r.stdout)
        self.assertNotIn("WINDOWS npm", r.stderr)

    def test_a_mnt_path_off_wsl_is_not_windows(self):
        # A genuine Linux box can mount anything at /mnt — only the WSL kernel
        # makes a /mnt npm the Windows one.
        r = self._probe(self.LINUX, "/mnt/tools/node/bin/npm")
        self.assertIn("USABLE", r.stdout)
        self.assertNotIn("WINDOWS npm", r.stderr)

    def test_no_npm_at_all_is_quietly_not_found(self):
        # The callers own that message ("npm not found and no prebuilt dist");
        # the guard must not add a Windows warning about an npm that isn't there.
        r = self._probe(self.WSL, "")
        self.assertIn("NOT-USABLE", r.stdout)
        self.assertNotIn("WINDOWS npm", r.stderr)

    def test_macos_with_no_proc_version_is_untouched(self):
        # _wsl_kernel returns "" where /proc/version does not exist (macOS);
        # the guard must be inert there.
        r = self._probe("", "/usr/local/bin/npm")
        self.assertIn("USABLE", r.stdout)
        self.assertEqual(r.stderr.strip(), "", r.stderr)


class WslHomeGuardTests(unittest.TestCase):
    """BDTOOLS_HOME on a Windows drive under WSL is refused up front.

    drvfs (/mnt/*) has no hardlinks, unreliable symlink semantics, and 10-50x
    slower metadata, so a conda env cannot be built there — and without this
    guard the failure surfaced as an inscrutable transaction error hours into
    a solve. Fabricated kernel strings, no real WSL needed.
    """

    def _guard(self, kernel, home):
        return sh(f'''
          TOOL=faketool
          BDTOOLS_HOME="{home}"
          eval "$(sed -n '/^_require_linux_fs_home()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
          _require_linux_fs_home "{kernel}"
          echo GUARD-PASSED
        ''')

    def test_a_windows_drive_home_under_wsl_dies_with_the_remedy(self):
        r = self._guard(NpmWslGuardTests.WSL, "/mnt/c/bdtools")
        self.assertNotEqual(r.returncode, 0, "the install must not proceed")
        self.assertNotIn("GUARD-PASSED", r.stdout)
        out = r.stdout + r.stderr
        self.assertIn("Windows drive", out)
        self.assertIn("/mnt/c/bdtools", out)          # name what it found
        self.assertIn("--prefix", out)                # ...and the way out
        self.assertIn("~/.local/share/bdtools", out)

    def test_a_linux_fs_home_under_wsl_passes_silently(self):
        r = self._guard(NpmWslGuardTests.WSL, "/home/user/.local/share/bdtools")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("GUARD-PASSED", r.stdout)
        self.assertEqual(r.stderr.strip(), "")

    def test_a_mnt_home_off_wsl_is_not_a_windows_drive(self):
        # /mnt on a real Linux box (an HPC scratch mount, say) is fine.
        r = self._guard(NpmWslGuardTests.LINUX, "/mnt/scratch/bdtools")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("GUARD-PASSED", r.stdout)


class GitLineEndingTests(unittest.TestCase):
    """Managed git operations pin core.autocrlf=false core.eol=lf.

    A user's global autocrlf=true — routinely copied from a Windows gitconfig
    onto WSL — makes every clone/checkout materialize tracked scripts with
    CRLF, and the first shebanged one dies at exec with "/usr/bin/env:
    'bash\\r': No such file or directory". That breaks the INSTALLER itself,
    before any check can run. Managed checkouts are bdtools' own artifacts, so
    overriding the user's global preference inside them is correct. These
    tests run the real git operations under a hostile global config.
    """

    CRLF_CONFIG = "[core]\n\tautocrlf = true\n"

    def _git_env(self, gitconfig):
        return dict(os.environ,
                    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e",
                    GIT_CONFIG_GLOBAL=str(gitconfig),
                    GIT_CONFIG_SYSTEM="/dev/null")

    def _source_repo(self, td):
        """A tool repo with a shebanged script, tagged v0.1.0 and v0.2.0."""
        src = td / "src/toolln"
        write(src / "deploy/run.sh", "#!/usr/bin/env bash\necho v1\n", mode=0o755)
        env = self._git_env(write(td / "neutral-gitconfig", ""))

        def git(*args, cwd):
            subprocess.run(["git", *args], cwd=cwd, check=True,
                           capture_output=True, text=True, env=env)

        git("init", "-q", "-b", "main", cwd=src)
        git("add", "-A", cwd=src)
        git("commit", "-qm", "v1", cwd=src)
        git("tag", "v0.1.0", cwd=src)
        write(src / "deploy/run.sh", "#!/usr/bin/env bash\necho v2\n", mode=0o755)
        git("add", "-A", cwd=src)
        git("commit", "-qm", "v2", cwd=src)
        git("tag", "v0.2.0", cwd=src)
        return src

    def test_the_hostile_config_really_corrupts_an_unpinned_clone(self):
        # The control: prove this environment reproduces the corruption, so
        # the passing tests below are evidence and not vacuous.
        with tempfile.TemporaryDirectory() as tdname:
            td = Path(tdname)
            src = self._source_repo(td)
            crlf = write(td / "crlf-gitconfig", self.CRLF_CONFIG)
            dest = td / "plain-clone"
            subprocess.run(
                ["git", "clone", "-q", "--branch", "v0.1.0", "--depth", "1",
                 f"file://{src}", str(dest)],
                check=True, capture_output=True, env=self._git_env(crlf))
            self.assertIn(b"\r\n", (dest / "deploy/run.sh").read_bytes(),
                          "expected autocrlf=true to CRLF the working tree")

    def test_install_local_clone_is_immune_to_global_autocrlf(self):
        with tempfile.TemporaryDirectory() as tdname:
            td = Path(tdname)
            src = self._source_repo(td)
            crlf = write(td / "crlf-gitconfig", self.CRLF_CONFIG)
            dest = td / "checkout"
            r = sh(f'''
              DIR="{dest}"; TOOL=toolln; REPO="file://{src}"; VERSION=v0.1.0; RUN_ONLY=0
              eval "$(sed -n '/^ensure_checkout()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              ensure_checkout
            ''', env={"GIT_CONFIG_GLOBAL": str(crlf), "GIT_CONFIG_SYSTEM": "/dev/null"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            body = (dest / "deploy/run.sh").read_bytes()
            self.assertNotIn(b"\r", body,
                             "the managed clone must materialize LF endings")
            self.assertIn(b"echo v1", body)

    def test_pin_advance_checkout_is_immune_to_global_autocrlf(self):
        # The other write path: an existing checkout moved onto a new pinned
        # tag re-materializes changed files through `git checkout -f`.
        with tempfile.TemporaryDirectory() as tdname:
            td = Path(tdname)
            src = self._source_repo(td)
            crlf = write(td / "crlf-gitconfig", self.CRLF_CONFIG)
            dest = td / "checkout"
            subprocess.run(
                ["git", "clone", "-q", "--branch", "v0.1.0", "--depth", "1",
                 f"file://{src}", str(dest)],
                check=True, capture_output=True,
                env=self._git_env(write(td / "neutral2", "")))
            r = sh(f'''
              DIR="{dest}"; TOOL=toolln; REPO="file://{src}"; VERSION=v0.2.0; RUN_ONLY=0
              eval "$(sed -n '/^ensure_checkout()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              ensure_checkout
            ''', env={"GIT_CONFIG_GLOBAL": str(crlf), "GIT_CONFIG_SYSTEM": "/dev/null"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            body = (dest / "deploy/run.sh").read_bytes()
            self.assertIn(b"echo v2", body, "the pin must actually advance")
            self.assertNotIn(b"\r", body,
                             "the forced checkout must materialize LF endings")

    def test_common_sh_clone_is_immune_to_global_autocrlf(self):
        # common.sh has its own ensure_checkout (used by the sandbox/server
        # paths) with its own clone — pinned the same way.
        with tempfile.TemporaryDirectory() as tdname:
            td = Path(tdname)
            src = self._source_repo(td)
            crlf = write(td / "crlf-gitconfig", self.CRLF_CONFIG)
            home = td / "home"
            manifest = write(td / "tools.yml", f"""\
                suite_version: test-1
                tools:
                  - name: toolln
                    repo: file://{src}
                    version: v0.1.0
                    env: toolln
                """)
            r = sh("ensure_checkout toolln",
                   env={"BDTOOLS_HOME": str(home),
                        "BDTOOLS_MANIFEST": str(manifest),
                        "GIT_CONFIG_GLOBAL": str(crlf),
                        "GIT_CONFIG_SYSTEM": "/dev/null"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            body = (home / "checkouts/toolln/deploy/run.sh").read_bytes()
            self.assertNotIn(b"\r", body)


class PsPortabilityTests(unittest.TestCase):
    """_tree_cpu_ticks passes ONE comma-separated pid operand to ps.

    The space-separated form happened to work on macOS BSD ps and on procps,
    but neither behavior is documented; a busybox-class ps or a stricter parse
    silently returns nothing — and "nothing" here means the stall detector
    sees no CPU signal ever and stall-kills every long step, the exact macOS
    failure the surrounding comments in install-local.sh document.
    """

    def test_pid_list_is_one_comma_separated_operand(self):
        # Two real pids (this test's bash and its parent), discovered through
        # an overridden pgrep so no real process tree is required; the real ps
        # answers, proving the comma grammar works on THIS platform too. The
        # ps override records its argv to a file — the function discards ps's
        # stderr, so nothing asserted on may travel through it.
        with tempfile.TemporaryDirectory() as td:
            argfile = Path(td) / "ps-args"
            r = sh(f'''
              eval "$(sed -n '/^_tree_cpu_ticks()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              pgrep() {{ case "${{2:-}}" in "$$") echo "${{PPID}}";; esac; }}
              ps() {{ echo "PSARGS=$*" >> "{argfile}"; command ps "$@"; }}
              echo "TICKS=$(_tree_cpu_ticks $$)"
            ''')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertRegex(r.stdout, r"TICKS=\d+",
                             "the tick count must stay a plain integer")
            args = argfile.read_text()
            self.assertRegex(args, r"PSARGS=-o time= -p \d+,\d+",
                             "the pid list must be one comma-separated operand")
            self.assertNotRegex(args, r"-p \d+ \d+",
                                "no word-split bare pid operands")

    def test_a_single_process_still_reports_ticks(self):
        # The everyday healthy case, with the real pgrep and the real ps.
        r = sh(f'''
          eval "$(sed -n '/^_tree_cpu_ticks()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
          echo "TICKS=$(_tree_cpu_ticks $$)"
        ''')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"TICKS=\d+")


class CondaBaseListTests(unittest.TestCase):
    """detect_conda (common.sh) and _conda_bases (tool_launch.py) stay mirrored.

    The two resolvers answering "where is conda" differently is how the
    dashboard and doctor come to disagree about whether a tool is installed.
    Both lists gained the common non-interactive bases (/opt/conda in official
    Docker/WSL images and HPC site installs, /usr/local/*, and ~/opt/* — the
    macOS graphical installer's default); this pins them to each other so they
    cannot drift again.
    """

    OLD_ORDER = ["~/miniforge3", "~/miniconda3", "~/mambaforge", "~/anaconda3",
                 "/opt/miniforge3", "/opt/miniconda3",
                 "/opt/homebrew/Caskroom/miniforge/base"]
    NEW_BASES = ["/opt/conda", "/opt/anaconda3", "/opt/mambaforge",
                 "/usr/local/miniforge3", "/usr/local/miniconda3",
                 "~/opt/anaconda3", "~/opt/miniconda3"]

    def _bash_list(self):
        src = (ROOT / "bin/lib/common.sh").read_text(encoding="utf-8")
        body = src[src.index("detect_conda() {"):]
        loop = body[body.index("for b in"):body.index("; do")]
        return [e.replace("${HOME}", "~") for e in re.findall(r'"([^"]+)"', loop)]

    def _python_list(self):
        src = (ROOT / "bin/lib/tool_launch.py").read_text(encoding="utf-8")
        start = src.index("for b in (", src.index("def _conda_bases"))
        return re.findall(r'"([^"]+)"', src[start:src.index("):", start)])

    def test_the_two_probe_lists_are_identical_and_ordered(self):
        bash, py = self._bash_list(), self._python_list()
        self.assertEqual(bash, py, "common.sh and tool_launch.py must probe "
                                   "the same bases in the same order")
        # existing priority order first — deployments rely on it
        self.assertEqual(py[:len(self.OLD_ORDER)], self.OLD_ORDER)
        for b in self.NEW_BASES:
            self.assertIn(b, py)

    def test_conda_bases_finds_the_macos_gui_installer_default(self):
        # Behavioral, with a fabricated HOME: a conda at ~/opt/anaconda3 (the
        # Anaconda macOS graphical installer's default) must be probed even
        # with no CONDA_BASE/CONDA_EXE inherited — the dashboard/cron case.
        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td) / "home"
            (fake_home / "opt/anaconda3").mkdir(parents=True)
            code = textwrap.dedent(f'''
                import importlib.util, os
                os.environ["HOME"] = {str(fake_home)!r}
                os.environ.pop("CONDA_BASE", None)
                os.environ.pop("CONDA_EXE", None)
                spec = importlib.util.spec_from_file_location(
                    "tl", {str(ROOT / "bin/lib/tool_launch.py")!r})
                tl = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(tl)
                print({str(fake_home / "opt/anaconda3")!r} in tl._conda_bases())
            ''')
            r = subprocess.run([sys.executable, "-c", code],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("True", r.stdout)
