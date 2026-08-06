#!/usr/bin/env python3
"""A package post-link script must not be able to kill a conda transaction.

This is the failure that cost a working kraken_id_parse_gui on macOS:

    LinkError: post-link script failed for package ...::spades-4.3.0...
    deactivate_cctools_osx-64.sh: line 63: CONDA_BACKUP_AR: unbound variable

The mechanism is in conda's own source (conda/utils.py, wrap_subprocess_call):
for a package that has a post-link script, conda writes a temp shell script

    conda activate <prefix>
    . "<pkg>-post-link.sh"                     <-- SOURCED, not executed
    . "<prefix>/etc/conda/deactivate.d/<x>.sh" <-- for each, in reverse order

and runs it as `bash <script>`. Because the post-link script is *sourced*, a
`set -u` inside it (bioconda's spades has one) is still in force when the
deactivate hooks are sourced two lines later — and the conda-forge toolchain
hooks read $CONDA_BACKUP_<TOOL> with no default.

So the `set -u` does not come from an ancestor shell, which is why scrubbing
SHELLOPTS from conda's environment did not fix it. What fixes it is making those
names bound before anything sources a hook. Two independent ways, both tested
here because the hooks come in more than one variant:

  * the plain toolchain variable is defined, so the ACTIVATE hook records a
    backup (`if [ ! -z "${AR+x}" ]; then export CONDA_BACKUP_AR="$AR"; fi`);
  * BASH_ENV names a prelude that defines every one of them, which bash sources
    at the top of the wrapper regardless of what any hook does.

These tests build the same shape conda builds and run it with bash.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")

# The conda-forge toolchain hook pair, in the variant that has the bug: activate
# saves a backup ONLY if the plain variable is defined, deactivate reads the
# backup with no default.
ACTIVATE_HOOK = """\
if [ ! -z "${AR+x}" ]; then
    export CONDA_BACKUP_AR="$AR"
fi
export AR="__PREFIX__/bin/ar"
"""
DEACTIVATE_HOOK = """\
export AR="$CONDA_BACKUP_AR"
unset CONDA_BACKUP_AR
"""
# bioconda's spades post-link script turns on strict mode and prints a notice.
POST_LINK = """\
set -eu
echo "Note: SPAdes installed through bioconda on MacOS may be somewhat slower"
"""


@unittest.skipUnless(BASH, "bash is required")
class PostLinkWrapperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        prefix = Path(self.tmp) / "env"
        (prefix / "etc/conda/activate.d").mkdir(parents=True)
        (prefix / "etc/conda/deactivate.d").mkdir(parents=True)
        (prefix / "etc/conda/activate.d/activate_cctools.sh").write_text(
            ACTIVATE_HOOK.replace("__PREFIX__", str(prefix)))
        (prefix / "etc/conda/deactivate.d/deactivate_cctools.sh").write_text(
            DEACTIVATE_HOOK)
        (prefix / "post-link.sh").write_text(POST_LINK)
        self.prefix = prefix

    def wrapper(self):
        """The script conda writes: activate, source post-link, source deactivate."""
        p = self.prefix
        return (f'. "{p}/etc/conda/activate.d/activate_cctools.sh"\n'
                f'. "{p}/post-link.sh"\n'
                f'. "{p}/etc/conda/deactivate.d/deactivate_cctools.sh"\n'
                'echo POST-LINK-OK\n')

    def run_wrapper(self, env):
        script = Path(self.tmp) / "wrapper.sh"
        script.write_text(self.wrapper())
        # conda runs it as `bash <script>` with env=os.environ.copy() —
        # see conda/core/link.py:run_script.
        return subprocess.run([BASH, str(script)], env=env,
                              capture_output=True, text=True, timeout=60)

    def clean_env(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("AR", "CONDA_BACKUP_", "BASH_ENV"))}
        env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
        return env

    def test_the_failure_reproduces_without_the_guard(self):
        # Guard rails for the test itself: if this ever stops failing, the two
        # tests below are proving nothing.
        proc = self.run_wrapper(self.clean_env())
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CONDA_BACKUP_AR", proc.stderr)
        self.assertIn("unbound variable", proc.stderr)

    def test_defining_the_plain_toolchain_variable_fixes_it(self):
        env = self.clean_env()
        env["AR"] = ""          # what _conda_step now exports
        proc = self.run_wrapper(env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("POST-LINK-OK", proc.stdout)

    def test_the_bash_env_prelude_fixes_it_on_its_own(self):
        # The belt that does not depend on the hook's shape: even a hook that
        # never records a backup cannot produce an unbound read.
        prelude = Path(self.tmp) / "prelude.sh"
        prelude.write_text(': "${AR:=}"; export AR\n'
                           ': "${CONDA_BACKUP_AR:=}"; export CONDA_BACKUP_AR\n')
        env = self.clean_env()
        env["BASH_ENV"] = str(prelude)
        proc = self.run_wrapper(env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("POST-LINK-OK", proc.stdout)

    def _real_prelude(self):
        """Run install-local.sh's OWN _conda_prelude_file and return what it wrote.

        The function and its variable list are extracted from the shipped script
        rather than restated here: a prelude that has drifted from the list of
        names the hooks read is the one way this guard can silently stop working.
        """
        src = (ROOT / "bin/install-local.sh").read_text()
        start = src.index("_conda_prelude_file()")
        end = src.index("generic_build()")
        home = Path(self.tmp) / "home"
        out = subprocess.run(
            [BASH, "-c", f'set -euo pipefail\nBDTOOLS_HOME="{home}"\n'
                         f'{src[start:end]}\n_conda_prelude_file'],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return Path(out.stdout.strip())

    def test_install_local_generates_a_prelude_that_binds_those_names(self):
        text = self._real_prelude().read_text()
        for name in ("AR", "CLANGXX", "CC", "LDFLAGS"):
            self.assertIn(f': "${{{name}:=}}"', text)
            self.assertIn(f': "${{CONDA_BACKUP_{name}:=}}"', text)

    def test_the_real_prelude_survives_the_real_wrapper(self):
        env = self.clean_env()
        env["BASH_ENV"] = str(self._real_prelude())
        proc = self.run_wrapper(env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("POST-LINK-OK", proc.stdout)

    def test_the_prelude_chains_to_a_users_own_bash_env(self):
        # Replacing someone's BASH_ENV would be a side effect of our own making.
        theirs = Path(self.tmp) / "theirs.sh"
        theirs.write_text("export BDTOOLS_TEST_THEIRS=yes\n")
        prelude = self._real_prelude()
        out = subprocess.run(
            [BASH, "-c", 'echo "${BDTOOLS_TEST_THEIRS:-no}"'],
            env={**self.clean_env(), "BASH_ENV": str(prelude),
                 "_BDTOOLS_PREV_BASH_ENV": str(theirs)},
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "yes", out.stderr)


if __name__ == "__main__":
    unittest.main()
