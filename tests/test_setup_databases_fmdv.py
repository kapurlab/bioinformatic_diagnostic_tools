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


if __name__ == "__main__":
    unittest.main()
