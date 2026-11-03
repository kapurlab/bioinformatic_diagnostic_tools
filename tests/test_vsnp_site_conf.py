#!/usr/bin/env python3
"""bin/vsnp-site-conf.sh writes vsnp_gui's deploy/site.conf from sites/site.conf.

Step 3b runs vsnp_gui's own installer, which reads its OWN site.conf, and the
guide used to say "edit it so these match". Each trap in that hand edit is pinned
here: VSNP3_PREFIX must be exactly <TOOLS_ROOT>/vsnp3 (the only place a site
session looks for vSNP, 2026-10-29), ADMIN_USER/SERVERNAME must not stay empty
(install_ood.sh refuses to run), CONDA_BASE must hold a conda (otherwise the
installer skips the env with a warning), NPM_BIN must not trigger a root rebuild
of the committed frontend, and a site's existing file keeps its own lines.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/vsnp-site-conf.sh"

# The shape of vsnp_gui's deploy/site.conf.example: order-dependent ${...}
# references, empty required keys, a trailing comment, an npm path.
EXAMPLE = """\
# vsnp_gui site.conf example
SITE_NAME=yoursite
SITE_DISPLAY="Your Site"
SITE_ROOT=/srv/${SITE_NAME}
MEMBERS_GROUP=${SITE_NAME}-members
ADMINS_GROUP=${SITE_NAME}-admins
ADMIN_USER=
CLUSTER_NAME=${SITE_NAME}
SERVERNAME=
SINGULARITY_BINDPATH=/etc,/home,${SITE_ROOT}
CONDA_BASE=${SITE_ROOT}/tools/miniforge3
VSNP3_PREFIX=${SITE_ROOT}/tools/vsnp3
VSNP3_VERSION=3.36
VSNP_GUI_DIR=${SITE_ROOT}/tools/vsnp_gui
NODE_BIN=/usr/bin/node
NPM_BIN=/usr/bin/npm
KRAKEN_RSYNC_SOURCE=        # e.g. someone@host:/path
"""


def source(path, *names):
    script = 'set -a; . "$1" >/dev/null 2>&1; shift; for v in "$@"; do printf "%s=%s\\n" "$v" "${!v-<unset>}"; done'
    out = subprocess.run(["env", "-i", "PATH=/usr/bin:/bin", "bash", "-c", script, "_", str(path), *names],
                         capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines())


class VsnpSiteConfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.site_root = t / "project/bdtools"
        self.tools = self.site_root / "tools"
        self.gui = self.tools / "vsnp_gui"
        (self.gui / "deploy").mkdir(parents=True)
        (self.gui / "deploy/site.conf.example").write_text(EXAMPLE)
        self.conda = t / "software/miniforge3"
        (self.conda / "bin").mkdir(parents=True)
        (self.conda / "bin/conda").write_text("#!/bin/sh\n")
        (self.conda / "bin/conda").chmod(0o755)
        self.conf = t / "site.conf"
        self.write_conf()
        self.pin = next(line.split("vsnp3=")[1].split(",")[0].split("]")[0].strip()
                        for line in (ROOT / "tools.yml").read_text().splitlines()
                        if "bioconda::vsnp3=" in line)

    def tearDown(self):
        self.tmp.cleanup()

    def write_conf(self, conda=True, tools_root=None):
        lines = ["SITE_NAME=usda", 'SITE_DISPLAY="USDA SciComp"',
                 f"SITE_ROOT={self.site_root}", "CLUSTER_NAME=ceres",
                 f"TOOLS_ROOT={tools_root or '${SITE_ROOT}/tools'}"]
        if conda:
            lines.append(f"CONDA_BASE={self.conda}")
        self.conf.write_text("\n".join(lines) + "\n")

    def run_script(self, *args, **env):
        e = {**os.environ, **env}
        return subprocess.run(["bash", str(SCRIPT), "--site-conf", str(self.conf), *args],
                              capture_output=True, text=True, env=e)

    def test_a_fresh_file_has_every_value_step_3b_needs(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = source(self.gui / "deploy/site.conf", "SITE_ROOT", "CLUSTER_NAME", "CONDA_BASE",
                     "VSNP3_PREFIX", "VSNP_GUI_DIR", "VSNP3_VERSION", "ADMIN_USER",
                     "SERVERNAME", "NPM_BIN", "SITE_DISPLAY", "SINGULARITY_BINDPATH",
                     "ADMINS_GROUP")
        self.assertEqual(got["VSNP3_PREFIX"], f"{self.tools}/vsnp3")
        self.assertEqual(got["VSNP_GUI_DIR"], str(self.gui))
        self.assertEqual(got["CONDA_BASE"], str(self.conda))
        self.assertEqual(got["CLUSTER_NAME"], "ceres")
        self.assertEqual(got["SITE_DISPLAY"], "USDA SciComp")
        self.assertEqual(got["VSNP3_VERSION"], self.pin, "the tools.yml pin, not the example's")
        self.assertNotIn(got["ADMIN_USER"], ("", "<unset>"))
        self.assertNotIn(got["SERVERNAME"], ("", "<unset>"))
        self.assertEqual(got["NPM_BIN"], "", "no root rebuild of the committed frontend")
        # Untouched lines still expand from the new values, in order.
        self.assertEqual(got["SINGULARITY_BINDPATH"], f"/etc,/home,{self.site_root}")
        self.assertEqual(got["ADMINS_GROUP"], "usda-admins")
        self.assertIn("install_ood.sh --site-conf deploy/site.conf", r.stdout + r.stderr)

    def test_the_prefix_follows_tools_root_not_site_root(self):
        elsewhere = Path(self.tmp.name) / "apps/tools"
        (elsewhere / "vsnp_gui/deploy").mkdir(parents=True)
        (elsewhere / "vsnp_gui/deploy/site.conf.example").write_text(EXAMPLE)
        self.write_conf(tools_root=elsewhere)
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = source(elsewhere / "vsnp_gui/deploy/site.conf", "VSNP3_PREFIX")
        self.assertEqual(got["VSNP3_PREFIX"], f"{elsewhere}/vsnp3")

    def test_an_existing_file_is_edited_in_place_with_a_backup(self):
        dest = self.gui / "deploy/site.conf"
        dest.write_text("# our cluster notes — keep\n" + EXAMPLE + "QUOTA_SOFT=9t\n")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = dest.read_text()
        self.assertIn("# our cluster notes — keep", text)
        self.assertIn("QUOTA_SOFT=9t", text)
        self.assertEqual(source(dest, "VSNP3_PREFIX")["VSNP3_PREFIX"], f"{self.tools}/vsnp3")
        self.assertEqual(len(list((self.gui / "deploy").glob("site.conf.bak.*"))), 1)

    def test_a_conda_from_the_shell_does_not_stand_in_for_the_sites(self):
        # tstuber's login profile can export CONDA_BASE; it is not the site's.
        self.write_conf(conda=False)
        r = self.run_script(CONDA_BASE=str(self.conda))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("has no bin/conda", r.stdout + r.stderr)
        self.assertFalse((self.gui / "deploy/site.conf").exists(), "nothing written")

    def test_a_dry_run_writes_nothing(self):
        r = self.run_script("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("dry run", r.stdout + r.stderr)
        self.assertFalse((self.gui / "deploy/site.conf").exists())


if __name__ == "__main__":
    unittest.main()
