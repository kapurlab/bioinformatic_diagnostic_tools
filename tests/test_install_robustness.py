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
import subprocess
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
