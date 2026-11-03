#!/usr/bin/env python3
"""A conda operation on a tool's env keeps the configuration that lives inside it.

vsnp3 reads its reference locations from <env>/dependencies/
reference_options_paths.txt, and the vsnp3 package ships its own copy of that
file. conda owns the path, so replacing the package replaces the file: on the
Ames HPC (2026-11) the dashboard's vsnp3 3.36 -> 3.37 update reset the site's
reference locations to the upstream author's one path, and Step 2 could no
longer find any project's reference folder.

The stub "transactions" here do what conda does to that file on an upgrade —
unlink it and link the package's copy in its place, a hardlink into a fake
package cache. What is pinned:

  * the machine's file comes back after the transaction as the SAME file — same
    inode, so the same owner, group, permissions and ACLs: on the HPC people
    deliberately have different access to it, and an update must not change
    that — and the package cache's copy is left untouched;
  * where the original cannot be kept (no hard link possible), a copy with the
    same contents and permissions goes back instead;
  * a file the transaction did not touch is left alone, and a legitimate edit
    between two transactions in one run survives (the local install registers
    its reference set between the create and the pin enforcement);
  * an attempt killed before it could put the file back does not let the retry
    save the package's copy over the good one;
  * --fresh carries it from the env set aside into the new one;
  * every conda call site in the suite is bracketed.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
REL = "dependencies/reference_options_paths.txt"
SITE = "/project/bacterial_identification/vsnp_dependencies\n/project/shared/refs/vsnp3\n"
SHIPPED = "/Users/todstuber/vsnp3_test_dataset/vsnp_dependencies\n"


def _fn(script, name):
    start = script.index(f"\n{name}() {{") + 1
    return script[start:script.index("\n}\n", start) + 3]


@unittest.skipUnless(BASH, "bash is required")
class EnvConfigHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.cache = base / "pkgs" / "vsnp3-3.37-0" / "dependencies"
        self.cache.mkdir(parents=True)
        (self.cache / "reference_options_paths.txt").write_text(SHIPPED)
        self.env = self._make_env(base / "env")

    def _make_env(self, env):
        (env / "bin").mkdir(parents=True)
        (env / "dependencies").mkdir()
        py = env / "bin" / "python"
        py.write_text("#!/bin/sh\nexit 0\n")
        py.chmod(0o755)
        f = env / REL
        f.write_text(SITE)
        f.chmod(0o664)
        return env

    def _replace_with_package_copy(self, env):
        """What conda does to the file when it replaces the package."""
        return (f'rm -f "{env}/{REL}"; '
                f'ln "{self.cache}/reference_options_paths.txt" "{env}/{REL}"\n')

    def sh(self, body, run_id="run-1", **env):
        prelude = f'set -euo pipefail\nsource "{ROOT}/bin/lib/common.sh"\n'
        e = {**os.environ, "BDTOOLS_HOME": str(self.home), "BDTOOLS_RUN_ID": run_id,
             "DRY_RUN": "0", **env}
        return subprocess.run([BASH, "-c", prelude + body], capture_output=True,
                              text=True, env=e)

    def stash(self):
        return self.home / "state" / "env-config" / "vsnp_gui" / REL

    def deps(self, env=None):
        return sorted(p.name for p in ((env or self.env) / "dependencies").iterdir())

    def test_a_package_update_gets_the_sites_file_back(self):
        original = (self.env / REL).stat()
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n'
                      + self._replace_with_package_copy(self.env)
                      + f'env_config_restore vsnp_gui "{self.env}"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        f = self.env / REL
        self.assertEqual(f.read_text(), SITE)
        self.assertIn("kept this machine's dependencies/reference_options_paths.txt", out.stdout)
        self.assertIn("same file, owner, group and permissions", out.stdout)
        # The original file itself: its inode carries owner, group, mode and ACLs.
        now = f.stat()
        self.assertEqual((now.st_ino, now.st_uid, now.st_gid), (original.st_ino, original.st_uid, original.st_gid))
        self.assertEqual(stat.S_IMODE(now.st_mode), 0o664)
        self.assertEqual(now.st_nlink, 1)
        # The package cache's copy is untouched, and nothing is left beside the file.
        self.assertEqual((self.cache / "reference_options_paths.txt").read_text(), SHIPPED)
        self.assertEqual(self.deps(), ["reference_options_paths.txt"])
        # The copy stays for a person to find; nothing is left pending.
        self.assertEqual(self.stash().read_text(), SITE)
        self.assertFalse(Path(str(self.stash()) + ".pending").exists())

    def test_without_the_original_a_copy_with_its_permissions_goes_back(self):
        # No hard link could be made (fs.protected_hardlinks on another
        # account's file): contents and mode still come back, on a new inode,
        # and the package cache is never written through.
        kept = self.env / "dependencies" / ".reference_options_paths.txt.bdtools-kept"
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n'
                      f'rm -f "{kept}"\n'
                      + self._replace_with_package_copy(self.env)
                      + f'env_config_restore vsnp_gui "{self.env}"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        f = self.env / REL
        self.assertEqual(f.read_text(), SITE)
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o664)
        self.assertEqual(f.stat().st_nlink, 1)
        self.assertEqual((self.cache / "reference_options_paths.txt").read_text(), SHIPPED)
        self.assertIn("with the same permissions", out.stdout)

    def test_an_untouched_file_is_left_alone(self):
        before = (self.env / REL).stat().st_ino
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n'
                      f'env_config_restore vsnp_gui "{self.env}"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((self.env / REL).stat().st_ino, before)
        self.assertNotIn("kept this machine's", out.stdout)
        self.assertEqual(self.deps(), ["reference_options_paths.txt"])

    def test_an_edit_between_two_transactions_survives(self):
        # install-local's local site block registers its reference set between
        # the env create and enforce_package_pins; the second bracket must save
        # that edit, not put the first one's copy back over it.
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n'
                      f'env_config_restore vsnp_gui "{self.env}"\n'
                      f'echo /added/by/the/installer >> "{self.env}/{REL}"\n'
                      f'env_config_save vsnp_gui "{self.env}"\n'
                      f'env_config_restore vsnp_gui "{self.env}"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((self.env / REL).read_text(), SITE + "/added/by/the/installer\n")

    def test_a_killed_attempt_does_not_overwrite_the_good_copy(self):
        # Attempt 1 replaced the file and was killed before its restore; the
        # retry's save must keep the copy from before attempt 1.
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n'
                      + self._replace_with_package_copy(self.env)
                      + f'env_config_save vsnp_gui "{self.env}"\n'
                      f'env_config_restore vsnp_gui "{self.env}"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((self.env / REL).read_text(), SITE)

    def test_another_runs_copy_is_named_never_put_back(self):
        first = self.sh(f'env_config_save vsnp_gui "{self.env}"\n', run_id="old-run")
        self.assertEqual(first.returncode, 0, first.stderr)
        (self.env / REL).write_text("/edited/since\n")
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n'
                      f'env_config_restore vsnp_gui "{self.env}"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((self.env / REL).read_text(), "/edited/since\n")
        self.assertIn("earlier run was stopped", out.stderr)

    def test_fresh_carries_it_into_the_new_env(self):
        # discard_env_for_fresh saves, then moves the env aside; the create
        # brackets itself; the new env gets the original, and the env set aside
        # keeps it too, in case the build fails and it is put back.
        original = (self.env / REL).stat().st_ino
        aside = Path(str(self.env) + ".bdtools-old-1")
        new = self._make_env(Path(self.tmp.name) / "new-env")
        (new / REL).unlink()
        os.link(self.cache / "reference_options_paths.txt", new / REL)
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n'
                      f'mv "{self.env}" "{aside}"\n'
                      f'FRESH_ORIG="{self.env}"; FRESH_ASIDE="{aside}"\n'
                      f'env_config_save vsnp_gui "{new}"\n'
                      f'env_config_restore vsnp_gui "{new}"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((new / REL).read_text(), SITE)
        self.assertEqual((new / REL).stat().st_ino, original)
        self.assertEqual((aside / REL).read_text(), SITE)

    def test_a_fresh_build_that_fails_leaves_nothing_behind(self):
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n'
                      'env_config_forget vsnp_gui\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.deps(), ["reference_options_paths.txt"])
        self.assertFalse(Path(str(self.stash()) + ".pending").exists())

    def test_a_failed_create_keeps_it_for_the_retry(self):
        partial = Path(self.tmp.name) / "partial"
        (partial / "conda-meta").mkdir(parents=True)
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n'
                      f'env_config_restore vsnp_gui "{partial}"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse((partial / REL).exists())
        self.assertTrue(Path(str(self.stash()) + ".pending").exists())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads anything")
    def test_an_unreadable_file_stops_the_change(self):
        (self.env / REL).chmod(0)
        self.addCleanup((self.env / REL).chmod, 0o644)
        out = self.sh(f'env_config_save vsnp_gui "{self.env}" || echo REFUSED\n')
        self.assertIn("REFUSED", out.stdout)
        self.assertIn("not changing the env without a copy", out.stderr)

    def test_a_tool_that_declares_nothing_is_untouched(self):
        out = self.sh(f'env_config_save mlst_gui "{self.env}"\n'
                      + self._replace_with_package_copy(self.env)
                      + f'env_config_restore mlst_gui "{self.env}"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((self.env / REL).read_text(), SHIPPED)

    def test_dry_run_changes_nothing(self):
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n', DRY_RUN="1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(self.stash().exists())

    def test_through_conda_step(self):
        # install-local.sh's one choke point for create / env update / pin
        # enforcement, run with a "conda" that replaces the file.
        script = (ROOT / "bin/install-local.sh").read_text(encoding="utf-8")
        start = script.index("\n_conda_step() {") + 1
        end_fn = script.index("\n_conda_placeholder() {")
        end = script.index("\n}\n", end_fn) + 3
        fake_base = Path(self.tmp.name) / "conda"
        (fake_base / "bin").mkdir(parents=True)
        (fake_base / "bin" / "conda").write_text("#!/bin/sh\necho @EXPLICIT\n")
        (fake_base / "bin" / "conda").chmod(0o755)
        tx = Path(self.tmp.name) / "tx.sh"
        tx.write_text("#!/bin/sh\n" + self._replace_with_package_copy(self.env))
        tx.chmod(0o755)
        out = self.sh(script[start:end] + f'TOOL=vsnp_gui\n_conda_step "{self.env}" "{tx}"\n',
                      CONDA_BASE=str(fake_base))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((self.env / REL).read_text(), SITE)
        self.assertEqual((self.env / REL).stat().st_nlink, 1)


class EveryCondaCallSiteIsBracketed(unittest.TestCase):
    """A save before the transaction and a restore after it, at every place the
    suite runs conda against an existing tool env."""

    def _between(self, path, before, call, after):
        s = (ROOT / path).read_text(encoding="utf-8")
        i = s.index(call)
        self.assertLess(s.rindex(before, 0, i), i, f"{path}: {before} must precede {call}")
        self.assertGreater(s.index(after, i), i, f"{path}: {after} must follow {call}")

    def test_update_packages(self):
        # The dashboard's "Update conda packages" — the Ames path.
        self._between("bin/update-packages.sh", "env_config_save",
                      '_conda_install_set "${conda}" "${envdir}" "${specs[@]}" || _irc',
                      "env_config_restore")

    def test_complete_env(self):
        self._between("bin/complete-env.sh", "env_config_save",
                      'run "${conda}" install -y -p "${envdir}" --freeze-installed',
                      "env_config_restore")

    def test_restore_env(self):
        self._between("bin/restore-env.sh", "env_config_save",
                      "_conda_restore || rc=$?", "env_config_restore")

    def test_install_local_conda_step(self):
        self._between("bin/install-local.sh", "env_config_save",
                      '"${pre[@]}" "$@" || rc=$?', "env_config_restore")

    def test_install_local_delegated_installer(self):
        self._between("bin/install-local.sh", "env_config_save",
                      '"${DIR}/deploy/install.sh" ${args[@]+"${args[@]}"} || die "${TOOL} deploy/install.sh failed"',
                      "env_config_restore")

    def test_server_env_update(self):
        self._between("bin/lib/common.sh", "env_config_save",
                      'run "${conda}" env update -p "${dir}/env" -f "${spec}" || _rc=$?',
                      "env_config_restore")

    def test_fresh_saves_before_setting_the_env_aside(self):
        self._between("bin/lib/common.sh", "env_config_save",
                      'mv "${envdir}" "${aside}"', "restore_env_from_fresh")

    def test_vsnp_gui_declares_its_reference_locations(self):
        out = subprocess.run(["python3", str(ROOT / "bin/lib/manifest.py"),
                              str(ROOT / "tools.yml"), "get", "vsnp_gui", "env_config"],
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.split(), [REL])


if __name__ == "__main__":
    unittest.main()
