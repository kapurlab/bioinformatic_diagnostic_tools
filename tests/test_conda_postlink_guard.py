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
SHELLOPTS from conda's environment did not fix it — and it is also why nothing
done at shell startup can fix it. BASH_ENV, a prelude, `set +u`: all of them run
BEFORE the sourced post-link script turns nounset back on. The variables are the
only lever that survives that line.

Which variables, and with what values, depends on the hook, and the hooks come in
two shapes. Both are exercised here because the guard has to hold for both:

  * `if [ ! -z "${AR+x}" ]; then export CONDA_BACKUP_AR="$AR"; fi` — records a
    backup whenever the plain variable is DEFINED. An empty value is enough.
  * conda-forge's real `_tc_activation` (cctools_osx-64, clang_osx-64, …) —
    `if [ -n "${oldval}" ]` … `else eval unset '${to}${thing}'`. This one tests
    for NON-EMPTY, so an empty plain variable makes it UNSET the backup that was
    just defined, and the deactivate hook dies on exactly the line it always
    died on. That is the shape that was in the field on 2026-08-23, with the
    guard already in place, and it is why the values are placeholders now
    (install-local.sh:_conda_placeholder) rather than empty strings.

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

# The pair as conda-forge actually ships it: `_tc_activation` reads the plain
# variable with an unguarded eval, records a backup only when it is NON-EMPTY,
# and unsets the backup otherwise; the deactivate side reads the backup with no
# default. Transcribed from the installed cctools_osx-64 hooks (2026-08-23) —
# an empty AR is what turned this into the field failure above.
REAL_ACTIVATE_HOOK = """\
eval oldval="\\$AR"
if [ -n "${oldval}" ]; then
    eval export "CONDA_BACKUP_'AR'=\\"${oldval}\\""
else
    eval unset 'CONDA_BACKUP_AR'
fi
export AR="${AR:-__PREFIX__/bin/x86_64-apple-darwin13.4.0-ar}"
"""
REAL_DEACTIVATE_HOOK = """\
eval oldval="\\$CONDA_BACKUP_AR"
export AR="${oldval}"
unset CONDA_BACKUP_AR
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
        env["AR"] = ""          # enough for THIS hook variant; see below
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
        # A toolchain PROGRAM name is bound to a placeholder, not to "": the real
        # conda-forge hook unsets the backup for an empty one (see
        # RealToolchainHookTests). A flag stays empty.
        for name, default in (("AR", "ar"), ("CLANGXX", "clang++"), ("CC", "cc")):
            self.assertIn(f': "${{{name}:={default}}}"', text)
            self.assertIn(f': "${{CONDA_BACKUP_{name}:=${{{name}}}}}"', text)
        self.assertIn(': "${LDFLAGS:=}"', text)
        self.assertIn(': "${CONDA_BACKUP_LDFLAGS:=${LDFLAGS}}"', text)

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


@unittest.skipUnless(BASH, "bash is required")
class RealToolchainHookTests(unittest.TestCase):
    """The hook shape that was actually in the field on 2026-08-23.

    kraken_id_parse_gui would not build on a lab Mac even with the guard in
    place: `conda env create` reached spades' post-link script, and

        deactivate_cctools_osx-64.sh: line 63: CONDA_BACKUP_AR: unbound variable

    because AR="" made the activate hook UNSET the backup rather than record it.
    Reproduced against the installed hooks, then fixed by giving each toolchain
    variable a placeholder value.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        prefix = Path(self.tmp) / "env"
        (prefix / "etc/conda/activate.d").mkdir(parents=True)
        (prefix / "etc/conda/deactivate.d").mkdir(parents=True)
        (prefix / "etc/conda/activate.d/activate_cctools.sh").write_text(
            REAL_ACTIVATE_HOOK.replace("__PREFIX__", str(prefix)))
        (prefix / "etc/conda/deactivate.d/deactivate_cctools.sh").write_text(
            REAL_DEACTIVATE_HOOK)
        (prefix / "post-link.sh").write_text(POST_LINK)
        self.prefix = prefix

    def run_wrapper(self, env):
        """conda's own shape: activate, SOURCE the post-link, deactivate."""
        p = self.prefix
        script = Path(self.tmp) / "wrapper.sh"
        script.write_text(f'. "{p}/etc/conda/activate.d/activate_cctools.sh"\n'
                          f'. "{p}/post-link.sh"\n'
                          f'. "{p}/etc/conda/deactivate.d/deactivate_cctools.sh"\n'
                          'echo POST-LINK-OK\n')
        return subprocess.run([BASH, str(script)], env=env,
                              capture_output=True, text=True, timeout=60)

    def clean_env(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("AR", "CONDA_BACKUP_", "BASH_ENV"))}
        env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
        return env

    def _shell_value(self, expr):
        """Evaluate a snippet of install-local.sh's own guard code."""
        src = (ROOT / "bin/install-local.sh").read_text()
        lists = src[src.index("_CONDA_TOOL_VARS="):src.index("_CONDA_BACKUP_VARS=")]
        tail = src[src.index("_CONDA_BACKUP_VARS="):]
        lists += tail[:tail.index("\n")]
        func = src[src.index("_conda_placeholder()"):]
        func = func[:func.index("\n}\n") + 3]
        out = subprocess.run(
            [BASH, "-c", f"set -uo pipefail\n{lists}\n_conda_placeholder() "
                         f"{func[func.index('{'):]}\n{expr}"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_an_empty_value_reproduces_the_field_failure(self):
        """The guard as it shipped: every name defined, all of them empty."""
        env = self.clean_env()
        env["AR"] = ""
        proc = self.run_wrapper(env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CONDA_BACKUP_AR", proc.stderr)
        self.assertIn("unbound variable", proc.stderr)

    def test_a_placeholder_value_survives_it(self):
        env = self.clean_env()
        env["AR"] = "ar"        # what _conda_placeholder returns
        proc = self.run_wrapper(env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("POST-LINK-OK", proc.stdout)

    def test_a_real_value_the_caller_had_is_kept(self):
        env = self.clean_env()
        env["AR"] = "/usr/bin/ar"
        proc = self.run_wrapper(env)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_placeholder_is_the_programs_ordinary_name(self):
        for var, want in (("AR", "ar"), ("CXX", "c++"), ("CLANGXX", "clang++"),
                          ("F77", "gfortran"), ("LD_GOLD", "ld.gold"),
                          ("INSTALL_NAME_TOOL", "install_name_tool"),
                          # Both of these end a line of the list, which a
                          # `case " ${list} "` membership test silently missed —
                          # they kept the empty value the placeholder replaces.
                          ("REDO_PREBINDING", "redo_prebinding"),
                          ("ADDR2LINE", "addr2line")):
            self.assertEqual(self._shell_value(f'_conda_placeholder {var}'), want,
                             f"placeholder for {var}")

    def test_flag_variables_stay_empty(self):
        """A placeholder in CFLAGS or HOST would be a lie handed to whatever
        reads it; no hook in the wild manages those through the unset-ing loop."""
        for var in ("CFLAGS", "LDFLAGS", "HOST", "CONDA_BUILD_SYSROOT"):
            self.assertEqual(self._shell_value(f'_conda_placeholder {var}'), "",
                             f"{var} must stay empty")

    def test_every_program_the_cctools_hook_manages_is_declared(self):
        """The 19 names cctools_osx-64's hook enumerates. Ours listed 8; under
        `set -u` the hook dies on the first one nothing defined."""
        managed = ("AR AS CHECKSYMS INSTALL_NAME_TOOL LD LIBTOOL LIPO NM NMEDIT "
                   "OTOOL PAGESTUFF RANLIB REDO_PREBINDING SEGEDIT SEG_ADDR_TABLE "
                   "SEG_HACK SIZE STRINGS STRIP").split()
        declared = self._shell_value('echo ${_CONDA_TOOL_VARS}').split()
        for name in managed:
            self.assertIn(name, declared)

    def test_the_prelude_gives_tool_names_a_placeholder_and_flags_nothing(self):
        src = (ROOT / "bin/install-local.sh").read_text()
        self.assertIn('printf \': "${%s:=%s}"; export %s\\n\'', src)
        self.assertIn('"$(_conda_placeholder "${v}")"', src)


if __name__ == "__main__":
    unittest.main()
