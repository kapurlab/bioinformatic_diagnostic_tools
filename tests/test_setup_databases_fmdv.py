#!/usr/bin/env python3
"""`bdtools setup-databases fmdv` builds vsnp_gui's FMDV reference library and
links it where the GUI looks.

The library is built by vsnp_gui's own bin/fmdv_library_build.sh with the tools
in vsnp_gui's env, then linked as <site>/refs/fmdv. What is pinned here:

  * it is opt-in: a plain `setup-databases` never builds it (a TB lab has no
    use for it, and the build needs GitHub and NCBI);
  * the builder runs with the env's bin first on PATH and is handed the
    library folder under the database root;
  * only a finished build is linked: a builder that stops leaves no link, so
    the GUI is never pointed at half a library;
  * a vsnp_gui older than FMDV mode, or an env without BLAST+, is named with
    the command that fixes it, and nothing is built or linked.

The builder, the env and its tools are stand-ins: no network, no conda.
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/setup-databases.sh"

BUILDER_OK = """#!/usr/bin/env bash
set -euo pipefail
lib=""
while [[ $# -gt 0 ]]; do case "$1" in --lib) lib="$2"; shift 2;; *) shift;; esac; done
mkdir -p "$lib/vp1_context"
command -v blastn > "$lib/blastn_seen_at"
echo '{}' > "$lib/lineages.json"
echo '{}' > "$lib/vp1_context/SOURCE.json"
echo "[fmdv library] ready"
"""
BUILDER_FAILS = """#!/usr/bin/env bash
lib=""
while [[ $# -gt 0 ]]; do case "$1" in --lib) lib="$2"; shift 2;; *) shift;; esac; done
mkdir -p "$lib"; echo '{}' > "$lib/lineages.json"
echo "[fmdv library] stopped" >&2
exit 3
"""


def _exe(path: Path, text: str = "#!/bin/sh\nexit 0\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class SetupDatabasesFmdv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.home = t / "bdhome"          # BDTOOLS_HOME, with a local vsnp site
        (self.home / "vsnp3-site").mkdir(parents=True)
        self.tools = t / "tools"          # BDTOOLS_TOOLSDIR
        self.gui = self.tools / "vsnp_gui"
        self.root = t / "databases"
        self.lib = self.root / "fmdv" / "fmdv_refs"
        self.link = self.home / "vsnp3-site" / "refs" / "fmdv"

    def tearDown(self):
        self.tmp.cleanup()

    def gui_with(self, builder=BUILDER_OK, tools=("blastn", "makeblastdb", "sourmash")):
        if builder is not None:
            _exe(self.gui / "bin" / "fmdv_library_build.sh", builder)
        else:
            (self.gui / "bin").mkdir(parents=True, exist_ok=True)
        env_bin = self.gui / "env" / "bin"
        env_bin.mkdir(parents=True, exist_ok=True)
        _exe(env_bin / "python")
        for name in tools:
            _exe(env_bin / name)

    def run_setup(self, *args):
        env = dict(os.environ, BDTOOLS_HOME=str(self.home), BDTOOLS_TOOLSDIR=str(self.tools))
        env.pop("DRY_RUN", None)
        return subprocess.run(["bash", str(SCRIPT), "--root", str(self.root), *args],
                              capture_output=True, text=True, env=env, timeout=120)

    def test_builds_then_links_the_library_for_the_gui(self):
        self.gui_with()
        r = self.run_setup("fmdv")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue((self.lib / "lineages.json").is_file(), out)
        self.assertTrue(self.link.is_symlink(), out)
        self.assertEqual(self.link.resolve(), self.lib.resolve())
        # The env's tools, not whatever the caller's PATH happens to hold.
        self.assertEqual((self.lib / "blastn_seen_at").read_text().strip(),
                         str(self.gui / "env" / "bin" / "blastn"))

    def test_a_run_that_names_no_location_goes_where_the_databases_are(self):
        """`setup-databases fmdv`, exactly as the GUI words it, on a machine
        whose databases are not in ~/databases. It used to build the library
        under the caller's home and then record THAT as the machine's database
        root, repointing every tool."""
        self.gui_with()
        self.assertEqual(self.run_setup("--dry-run").returncode, 0)       # nothing recorded by a dry run
        self.assertFalse((self.home / "db-root").exists())
        self.assertEqual(self.run_setup("fmdv").returncode, 0)            # records self.root
        self.assertEqual((self.home / "db-root").read_text().strip(), str(self.root))
        (self.lib / "lineages.json").unlink()
        fake_home = Path(self.tmp.name) / "userhome"
        fake_home.mkdir()
        env = dict(os.environ, BDTOOLS_HOME=str(self.home), BDTOOLS_TOOLSDIR=str(self.tools),
                   HOME=str(fake_home))
        env.pop("DRY_RUN", None)
        r = subprocess.run(["bash", str(SCRIPT), "fmdv"], capture_output=True, text=True,
                           env=env, timeout=120, stdin=subprocess.DEVNULL)
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("recorded on this machine", out)
        self.assertTrue((self.lib / "lineages.json").is_file(), out)       # built where the databases are
        self.assertFalse((fake_home / "databases").exists(), out)           # not under the caller's home
        self.assertEqual((self.home / "db-root").read_text().strip(), str(self.root))   # and the record stands

    def test_a_named_location_still_wins_over_the_recorded_one(self):
        self.gui_with()
        self.assertEqual(self.run_setup("fmdv").returncode, 0)
        other = Path(self.tmp.name) / "other_databases"
        env = dict(os.environ, BDTOOLS_HOME=str(self.home), BDTOOLS_TOOLSDIR=str(self.tools))
        env.pop("DRY_RUN", None)
        r = subprocess.run(["bash", str(SCRIPT), "--root", str(other), "fmdv"], capture_output=True,
                           text=True, env=env, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((other / "fmdv" / "fmdv_refs" / "lineages.json").is_file())
        self.assertEqual((self.home / "db-root").read_text().strip(), str(other))

    def test_a_second_run_keeps_the_link(self):
        self.gui_with()
        self.assertEqual(self.run_setup("fmdv").returncode, 0)
        r = self.run_setup("fmdv")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.link.resolve(), self.lib.resolve())

    def test_a_build_that_stops_is_not_linked(self):
        self.gui_with(builder=BUILDER_FAILS)
        r = self.run_setup("fmdv")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)       # the other databases are not abandoned
        self.assertIn("stopped before it finished", out)
        self.assertFalse(self.link.exists(), out)

    def test_plain_setup_never_builds_it(self):
        self.gui_with()
        r = self.run_setup("--dry-run")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("fmdv_library_build.sh", out)
        self.assertFalse(self.lib.exists())

    def test_an_older_vsnp_gui_is_named_with_the_fix(self):
        self.gui_with(builder=None)
        r = self.run_setup("fmdv")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("no bin/fmdv_library_build.sh", out)
        self.assertIn("bdtools update vsnp_gui", out)
        self.assertFalse(self.link.exists())

    def test_an_env_without_blast_is_named_with_the_fix(self):
        self.gui_with(tools=("sourmash",))
        r = self.run_setup("fmdv")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("lacks blastn makeblastdb", out)
        self.assertIn("bdtools install vsnp_gui", out)
        self.assertFalse(self.lib.exists())
        self.assertFalse(self.link.exists())

    def test_dry_run_shows_the_build_and_the_link_and_writes_neither(self):
        self.gui_with()
        r = self.run_setup("--dry-run", "fmdv")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("fmdv_library_build.sh --lib " + str(self.lib), out)
        self.assertIn("ln -sfn " + str(self.lib), out)
        self.assertFalse(self.lib.exists())
        self.assertFalse(self.link.exists())


class SetupDatabasesFmdvOnASharedSite(unittest.TestCase):
    """The same command on a shared site, run by someone whose home still holds
    a personal vsnp site from an older install.

    Ames HPC, 2026-11-20. `setup-databases fmdv --shared` built the library
    under the site's database root and then linked it into the running user's
    own ~/.local/share/bdtools/vsnp3-site/refs, because "a personal site folder
    exists" was taken to mean "this is a personal install". It ended "ok". The
    GUI, deployed in the site and reading <site>/refs, went on reporting that
    no library had been built.

    A site is recognised the way the launcher recognises one (the tool is a
    sibling of the umbrella under the site's tools folder), so the umbrella's
    bin/ is copied into a site layout here rather than run from the repo.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.site = t / "site"
        self.umbrella = self.site / "tools" / "bioinformatic_diagnostic_tools"
        self.umbrella.mkdir(parents=True)
        subprocess.run(["cp", "-R", str(ROOT / "bin"), str(self.umbrella / "bin")], check=True)
        subprocess.run(["cp", str(ROOT / "tools.yml"), str(self.umbrella / "tools.yml")], check=True)
        (self.umbrella / "sites").mkdir()
        (self.umbrella / "sites" / "site.conf").write_text(f"SITE_ROOT={self.site}\n")
        self.gui = self.site / "tools" / "vsnp_gui"
        (self.gui / ".git").mkdir(parents=True)
        _exe(self.gui / "bin" / "fmdv_library_build.sh", BUILDER_OK)
        _exe(self.gui / "env" / "bin" / "python")
        for name in ("blastn", "makeblastdb", "sourmash"):
            _exe(self.gui / "env" / "bin" / name)
        self.root = self.site / "databases"
        self.lib = self.root / "fmdv" / "fmdv_refs"
        self.site_link = self.site / "refs" / "fmdv"
        # The leftover: this user's own site tree, from a personal install.
        self.home = t / "home" / ".local" / "share" / "bdtools"
        (self.home / "vsnp3-site" / "refs").mkdir(parents=True)
        self.personal_link = self.home / "vsnp3-site" / "refs" / "fmdv"

    def tearDown(self):
        self.tmp.cleanup()

    def run_setup(self, *args):
        env = dict(os.environ, BDTOOLS_HOME=str(self.home), HOME=str(Path(self.tmp.name) / "home"))
        for k in ("DRY_RUN", "BDTOOLS_TOOLSDIR", "VSNP_GUI_SITE_ROOT", "BDTOOLS_SITE_ROOT", "BDTOOLS_DB_ROOT"):
            env.pop(k, None)
        return subprocess.run(["bash", str(self.umbrella / "bin" / "setup-databases.sh"),
                               "--root", str(self.root), *args],
                              capture_output=True, text=True, env=env, timeout=180)

    def test_the_library_is_linked_into_the_site_not_the_runners_home(self):
        r = self.run_setup("fmdv")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue(self.site_link.is_symlink(), out)
        self.assertEqual(self.site_link.resolve(), self.lib.resolve())
        self.assertFalse(self.personal_link.exists(), out)
        self.assertIn("the vSNP GUI finds the library", out)
        self.assertIn("Database setup complete", out)

    def test_a_second_run_says_the_same_and_changes_nothing(self):
        self.assertEqual(self.run_setup("fmdv").returncode, 0)
        r = self.run_setup("fmdv")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertEqual(self.site_link.resolve(), self.lib.resolve())
        self.assertIn("the vSNP GUI finds the library", out)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes anywhere")
    def test_a_site_the_runner_cannot_write_ends_on_the_problem_not_on_ok(self):
        # Built, but not where the GUI can see it, and this account cannot put
        # it there: the run must not end "Database setup complete".
        refs = self.site / "refs"
        refs.mkdir()
        refs.chmod(0o555)
        try:
            r = self.run_setup("fmdv")
        finally:
            refs.chmod(0o755)
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, out)
        self.assertTrue((self.lib / "lineages.json").is_file(), out)
        self.assertIn("CANNOT SEE IT", out)
        self.assertIn(f"ln -sfn {self.lib} {self.site_link}", out)
        self.assertIn("finished with problems", out)
        self.assertNotIn("Database setup complete", out)
        self.assertFalse(self.personal_link.exists(), out)

    def findings(self):
        """What `doctor` (and so the dashboard card) reports about FMDV mode."""
        env = dict(os.environ, BDTOOLS_HOME=str(self.home), HOME=str(Path(self.tmp.name) / "home"))
        for k in ("BDTOOLS_TOOLSDIR", "VSNP_GUI_SITE_ROOT", "BDTOOLS_SITE_ROOT", "BDTOOLS_DB_ROOT"):
            env.pop(k, None)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import tool_launch\n"
                "for label, fix in tool_launch.vsnp_gui_fmdv_findings(): print(label + ' ## ' + fix)\n")
        r = subprocess.run([sys.executable, "-c", code, str(self.umbrella / "bin" / "lib")],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_doctor_is_silent_until_a_library_is_built_then_grades_it(self):
        # Opt-in: with nothing built there is nothing to report.
        self.assertEqual(self.findings(), "")
        self.assertEqual(self.run_setup("fmdv").returncode, 0)
        self.assertEqual(self.findings(), "")            # built and visible
        # Built, and then not where the GUI looks: the Ames state, reported
        # before anyone presses Import, with the command that repairs it.
        self.site_link.unlink()
        out = self.findings()
        self.assertIn("cannot see it", out)
        self.assertIn(str(self.site_link), out)
        self.assertIn(f"setup-databases fmdv --root {self.root}", out)

    def test_dry_run_names_the_site_link(self):
        r = self.run_setup("fmdv", "--dry-run")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn(f"ln -sfn {self.lib} {self.site_link}", out)
        self.assertFalse(self.site_link.exists())
        self.assertFalse(self.lib.exists())


if __name__ == "__main__":
    unittest.main()
