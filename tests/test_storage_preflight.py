#!/usr/bin/env python3
"""An install asks for room first, and stops before writing anything when there is none.

bin/lib/storage.py measures every filesystem an install is about to write (the
env, conda's package cache, a database directory), and when one is short it
prints one block — how much is needed, where, how much is available — and
exits 28. What is pinned:

  * the block carries all three numbers, the mount point, each path that needs
    the space, and the command to run again;
  * needs on one filesystem are summed, not judged one at a time;
  * an env on the package cache's filesystem costs nothing extra (hard links);
  * the download total is read from both conda's and mamba's dry-run output;
  * the shell helpers exit 28, --dry-run reports without stopping, and the
    skip switch skips;
  * the dashboards lift the block out of the log and end on it.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import storage  # noqa: E402
import suite_common  # noqa: E402

BASH = shutil.which("bash")
GiB = storage.GiB

CONDA_DRY_RUN = """\
## Package Plan ##

  environment location: /project/shared/bdtools/tools/vsnp3

  added / updated specs:
    - vsnp3=3.37


The following packages will be downloaded:

    package                    |            build
    ---------------------------|-----------------
    vsnp3-3.37                 |       hdfd78af_0         2.6 MB  bioconda
    ------------------------------------------------------------
                                           Total:         2.6 MB

The following packages will be UPDATED:

  vsnp3                                     3.36-hdfd78af_0 --> 3.37-hdfd78af_0
"""

MAMBA_DRY_RUN = """\
  Summary:

  Upgrade: 1 packages

  Total download: 12MB
"""

CACHED_ONLY = """\
## Package Plan ##

The following packages will be UPDATED:

  openssl                                  3.6.4-h55eecbc_0 --> 3.6.5-h55eecbc_0
"""


def fake_fs(table):
    """storage.filesystem stand-in: path prefix -> (dev, mount, avail)."""
    def lookup(path):
        p = str(path)
        for prefix, fs in sorted(table.items(), key=lambda kv: -len(kv[0])):
            if p.startswith(prefix):
                return fs
        raise AssertionError(f"no fake filesystem for {p}")
    return lookup


class DownloadTotals(unittest.TestCase):
    def test_conda(self):
        self.assertEqual(storage.download_bytes(CONDA_DRY_RUN), int(2.6 * storage.MiB))

    def test_mamba(self):
        self.assertEqual(storage.download_bytes(MAMBA_DRY_RUN), 12 * storage.MiB)

    def test_everything_already_cached_downloads_nothing(self):
        self.assertEqual(storage.download_bytes(CACHED_ONLY), 0)

    def test_output_it_cannot_read_is_unknown_not_zero(self):
        self.assertIsNone(storage.download_bytes("Segmentation fault"))


class Verdict(unittest.TestCase):
    def report(self, needs, table, **kw):
        out = io.StringIO()
        with mock.patch.object(storage, "filesystem", fake_fs(table)):
            ok = storage.report(storage.plan(needs), "the vsnp_gui install (a new conda env)",
                                rerun="bin/bdtools install vsnp_gui", out=out, **kw)
        return ok, out.getvalue()

    def test_a_shortfall_says_how_much_where_and_what_is_there(self):
        ok, text = self.report(
            [(5 * GiB, "/project/shared/miniconda3/pkgs", "conda's package cache"),
             (0, "/project/shared/bdtools/tools/vsnp3", "the new conda env")],
            {"/project": (1, "/project", 2 * GiB)})
        self.assertFalse(ok)
        self.assertTrue(text.startswith(storage.STORAGE_MARKER))
        self.assertIn("the vsnp_gui install (a new conda env) was not started. Nothing was changed.", text)
        self.assertIn("mounted at /project", text)
        self.assertIn("needed:     5.0 GB", text)
        self.assertIn("available:  2.0 GB", text)
        self.assertIn("short by:   3.0 GB", text)
        self.assertIn("/project/shared/miniconda3/pkgs", text)
        self.assertIn("/project/shared/bdtools/tools/vsnp3", text)
        self.assertIn("      bin/bdtools install vsnp_gui", text)

    def test_needs_on_one_filesystem_add_up(self):
        # Each fits alone; together they do not.
        ok, _ = self.report([(3 * GiB, "/a/pkgs", "cache"), (3 * GiB, "/a/env", "env")],
                            {"/a": (1, "/a", 5 * GiB)})
        self.assertFalse(ok)

    def test_only_the_short_filesystem_is_blamed(self):
        ok, text = self.report([(1 * GiB, "/home/u/.conda/pkgs", "cache"),
                                (4 * GiB, "/project/env", "env")],
                               {"/home": (1, "/home", 900 * storage.MiB),
                                "/project": (2, "/project", 100 * GiB)})
        self.assertFalse(ok)
        self.assertIn("mounted at /home", text)
        self.assertIn("(/project has room", text)

    def test_room_is_one_quiet_line(self):
        ok, text = self.report([(1 * GiB, "/a/env", "env")], {"/a": (1, "/a", 10 * GiB)})
        self.assertTrue(ok)
        self.assertNotIn(storage.STORAGE_MARKER, text)
        self.assertIn("ok storage", text)


class EnvEstimate(unittest.TestCase):
    def needs(self, mode, same_fs, solve=None):
        table = {"/a": (1, "/a", 0), "/b": (2 if not same_fs else 1, "/b", 0)}
        with mock.patch.object(storage, "filesystem", fake_fs(table)):
            return storage.env_needs("/b/env", mode, "/a/pkgs", "2.5", solve)

    def test_a_new_env_on_the_caches_filesystem_is_hard_links(self):
        (cache, _, _), (env, _, _) = self.needs("new", same_fs=True)
        self.assertEqual(env, 0)
        self.assertGreater(cache, 2.5 * GiB)

    def test_a_new_env_elsewhere_is_a_full_copy(self):
        (_, _, _), (env, _, _) = self.needs("new", same_fs=False)
        self.assertGreaterEqual(env, 2.5 * GiB)

    def test_an_update_costs_what_its_solve_downloads(self):
        (cache, _, _), _ = self.needs("update", same_fs=True, solve=CONDA_DRY_RUN)
        self.assertLess(cache, 1 * GiB)   # 2.6 MB of vsnp3, not a whole env

    def test_an_unsolved_update_costs_half_an_env(self):
        (cache, _, _), _ = self.needs("update", same_fs=True)
        new_cache = self.needs("new", same_fs=True)[0][0]
        self.assertLess(cache, new_cache)


class Command(unittest.TestCase):
    def run_need(self, nbytes, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            return subprocess.run(
                [sys.executable, str(ROOT / "bin/lib/storage.py"), "need",
                 "--what", "the Kraken2 database download",
                 "--rerun", "bin/bdtools setup-databases kraken",
                 "--need", f"{nbytes}:{tmp}/kraken2/db:Kraken2 k2_standard_08gb", *extra],
                capture_output=True, text=True)

    def test_no_room_exits_28_with_the_block(self):
        out = self.run_need(1 << 62)
        self.assertEqual(out.returncode, storage.ENOSPC_EXIT)
        self.assertIn(storage.STORAGE_MARKER, out.stderr)
        self.assertIn("kraken2/db", out.stderr)

    def test_report_only_prints_it_and_carries_on(self):
        out = self.run_need(1 << 62, "--report-only")
        self.assertEqual(out.returncode, 0)
        self.assertIn("a real run would stop here", out.stderr)

    def test_room_exits_0(self):
        self.assertEqual(self.run_need(1).returncode, 0)


@unittest.skipUnless(BASH, "bash is required")
class ShellHelpers(unittest.TestCase):
    def sh(self, body, **env):
        e = {**os.environ, "DRY_RUN": "0", **env}
        e.pop("BDTOOLS_SKIP_STORAGE_CHECK", None)
        e.update(env)
        return subprocess.run(
            [BASH, "-c", f'set -euo pipefail\nsource "{ROOT}/bin/lib/common.sh"\n{body}\necho AFTER'],
            capture_output=True, text=True, env=e)

    def test_no_room_stops_the_script(self):
        out = self.sh(f'storage_preflight "x" "y" "{1 << 62}:/tmp/x:thing"')
        self.assertEqual(out.returncode, 28)
        self.assertNotIn("AFTER", out.stdout)

    def test_dry_run_reports_and_continues(self):
        out = self.sh(f'storage_preflight "x" "y" "{1 << 62}:/tmp/x:thing"', DRY_RUN="1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("AFTER", out.stdout)
        self.assertIn(storage.STORAGE_MARKER, out.stderr)

    def test_the_skip_switch_skips(self):
        out = self.sh(f'storage_preflight "x" "y" "{1 << 62}:/tmp/x:thing"',
                      BDTOOLS_SKIP_STORAGE_CHECK="1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("storage check skipped", out.stderr)

    def test_install_stops_the_loop_on_28(self):
        script = (ROOT / "bin/bdtools").read_text(encoding="utf-8")
        self.assertIn("rc=${STORAGE_SHORT_EXIT}; break", script)
        updates = (ROOT / "bin/check-updates.sh").read_text(encoding="utf-8")
        # Asked before the checkout moves, so a refusal leaves the tool as it was.
        self.assertLess(updates.index("--check-storage"),
                        updates.index('checkout -f -q "${target}"'))


class Dashboard(unittest.TestCase):
    LOG = ["$ bdtools update-packages vsnp_gui",
           "==> vsnp_gui: vsnp3 3.36 -> 3.37",
           f"{storage.STORAGE_MARKER} — the vsnp_gui package update (vsnp3=3.37) was not started. Nothing was changed.",
           "",
           "  On the filesystem mounted at /project:",
           "      needed:     540.0 MB",
           "      available:  12.0 MB",
           "      short by:   528.0 MB",
           "",
           "  Free at least 528.0 MB on /project, then run it again:",
           "      bin/bdtools update-packages vsnp_gui",
           "",
           "Something else"]

    def test_the_block_is_lifted_out_of_the_log(self):
        block = suite_common.storage_block(self.LOG)
        self.assertTrue(block[0].startswith(storage.STORAGE_MARKER))
        self.assertIn("      available:  12.0 MB", block)
        self.assertEqual(block[-1], "      bin/bdtools update-packages vsnp_gui")

    def test_the_marker_is_the_same_on_both_sides(self):
        self.assertEqual(suite_common.STORAGE_MARKER, storage.STORAGE_MARKER)

    def test_the_run_ends_on_it(self):
        self.assertIn("Not enough storage", suite_common.finish_line(False, ["x"]))
        self.assertIn("finished with errors", suite_common.finish_line(False, []))

    def test_the_update_job_reports_it(self):
        # The OOD dashboard's UpdateManager running a command that stops for space.
        with tempfile.TemporaryDirectory() as tmp:
            block = Path(tmp) / "block.txt"
            block.write_text("\n".join(self.LOG[2:-2]) + "\n")
            stub = Path(tmp) / "bdtools"
            stub.write_text(f'#!/bin/sh\necho "==> vsnp_gui: vsnp3 3.36 -> 3.37"\ncat "{block}" >&2\nexit 28\n')
            stub.chmod(0o755)
            um = suite_common.UpdateManager()
            um.job["running"] = True
            with mock.patch.object(suite_common, "BDTOOLS", str(stub)), \
                    mock.patch.object(um, "check_async"):
                um._run("packages:vsnp_gui")
        st = um.job_status()
        self.assertFalse(st["ok"])
        self.assertTrue(st["storage"][0].startswith(storage.STORAGE_MARKER))
        self.assertIn("      short by:   528.0 MB", st["storage"])
        self.assertIn("Not enough storage", st["log"][-1])

    def test_both_dashboards_use_it(self):
        src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
        self.assertIn('storage = storage_block(self.update_job["log"])', src)
        self.assertIn("finish_line(ok, storage)", src)

    def test_the_page_leads_with_it(self):
        sys.path.insert(0, str(ROOT / "bin"))
        import dashboard
        self.assertIn("Not enough storage — nothing was installed", dashboard.PAGE)
        self.assertIn("s.storage.join", dashboard.PAGE)


if __name__ == "__main__":
    unittest.main()
