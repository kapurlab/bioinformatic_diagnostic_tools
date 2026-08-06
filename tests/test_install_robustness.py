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
            script = f'''
              DIR="{Path(td) / 'checkout'}"; TOOL=faketool; ENV_NAME=fake
              # the function under test, lifted from install-local.sh
              eval "$(sed -n '/^_env_prefix_for_subdir()/,/^}}/p;/^ensure_conda_subdir()/,/^}}/p' "{ROOT}/bin/install-local.sh")"
              CONDA_SUBDIR=osx-arm64 ensure_conda_subdir
              echo "SUBDIR=${{CONDA_SUBDIR}}"
            '''
            r = sh(script)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("SUBDIR=osx-64", r.stdout)
            self.assertIn("was built for osx-64", r.stderr)


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
                env: toolfail
              - name: toolskip
                repo: file://{self.sources['toolskip']}
                version: v0.1.0
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
