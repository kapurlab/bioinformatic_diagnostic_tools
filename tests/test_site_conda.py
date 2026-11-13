"""Update scripts find a SITE's conda the way launches do.

2026-11-12, USDA SciComp: the dashboard's "Update tool environments" ran
`bdtools complete-env kraken_id_parse_gui` and failed "conda/mamba not found".
Launches had read the site's conda from sites/site.conf since 2c5430b (the
vSNP fix), but common.sh:detect_conda — which every update script uses — only
knew CONDA_BASE/CONDA_EXE in its own environment, a fixed list of install
folders and PATH, and a dashboard session has none of them.

Pinned here: the site's declared conda (the card's BDTOOLS_SITE_CONDA_BASE,
else sites/site.conf CONDA_BASE) is found first, a per-user site.conf and a
personal CONDA_BASE never outrank it, and an env's own history names the conda
that built it when nothing is declared. bash and python agree on the base.
"""
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def fake_conda(base):
    exe = Path(base) / "bin" / "conda"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    return str(exe)


class SiteConda(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.umbrella = base / "site/tools/bioinformatic_diagnostic_tools"
        (self.umbrella / "bin/lib").mkdir(parents=True)
        (self.umbrella / "sites").mkdir()
        for rel in ("bin/lib/common.sh", "bin/lib/site_paths.py"):
            shutil.copy(ROOT / rel, self.umbrella / rel)
        shutil.copy(ROOT / "tools.yml", self.umbrella / "tools.yml")
        self.home = base / "home"            # no ~/miniconda3 etc. to probe
        self.home.mkdir()
        self.bdhome = base / "bdhome"
        self.bdhome.mkdir()
        self.site_conda = fake_conda(base / "apps/miniconda_site")
        self.personal = fake_conda(base / "personal/miniconda3")

    def sh(self, call, **env):
        clean = {"PATH": "/usr/bin:/bin", "HOME": str(self.home),
                 "BDTOOLS_HOME": str(self.bdhome)}
        clean.update(env)
        r = subprocess.run(["bash", "-c", f'source "{self.umbrella}/bin/lib/common.sh"; {call}'],
                           capture_output=True, text=True, env=clean, cwd=str(self.umbrella))
        return r.returncode, r.stdout.strip()

    def declare(self, path):
        site_base = str(Path(path).parent.parent)
        (self.umbrella / "sites/site.conf").write_text(
            f"SITE_ROOT=/nowhere\nCONDA_BASE={site_base}\n")
        return site_base

    def test_nothing_declared_means_no_site_conda(self):
        # The machine may have a conda of its own (GitHub's runner does), so
        # assert what the site lookup decides, not that no conda exists.
        self.assertEqual(self.sh("site_conda_base")[1], "")
        self.assertNotEqual(self.sh("detect_conda")[1], self.site_conda)

    def test_the_sites_declared_conda_is_found_with_nothing_on_path(self):
        self.declare(self.site_conda)
        self.assertEqual(self.sh("detect_conda"), (0, self.site_conda))

    def test_the_cards_value_wins_over_site_conf(self):
        self.declare(self.personal)
        base = str(Path(self.site_conda).parent.parent)
        self.assertEqual(self.sh("detect_conda", BDTOOLS_SITE_CONDA_BASE=base), (0, self.site_conda))

    def test_a_personal_conda_base_never_outranks_the_sites(self):
        self.declare(self.site_conda)
        personal_base = str(Path(self.personal).parent.parent)
        self.assertEqual(self.sh("detect_conda", CONDA_BASE=personal_base), (0, self.site_conda))
        self.assertEqual(self.sh("site_conda_base", CONDA_BASE=personal_base)[1],
                         str(Path(self.site_conda).parent.parent),
                         "an exported CONDA_BASE must not count as site configuration")

    def test_a_per_user_site_conf_is_not_the_site(self):
        (self.bdhome / "site.conf").write_text(
            f"CONDA_BASE={Path(self.personal).parent.parent}\n")
        self.assertEqual(self.sh("site_conda_base")[1], "")
        self.assertNotEqual(self.sh("detect_conda")[1], self.personal)

    def test_an_envs_history_names_the_conda_that_built_it(self):
        env = Path(self.tmp.name) / "tools/kraken_id_parse_gui/env"
        (env / "conda-meta").mkdir(parents=True)
        (env / "conda-meta/history").write_text(
            "==> 2026-08-24 06:10:11 <==\n"
            f"# cmd: {self.site_conda} env create -p {env} -f conda_setup/environment.yml\n"
            "# conda version: 25.9.1\n")
        self.assertEqual(self.sh(f'env_creator_conda "{env}"'), (0, self.site_conda))
        self.assertEqual(self.sh(f'conda_for_env "{env}"'), (0, self.site_conda))
        self.assertEqual(self.sh(f'env_creator_conda "{env.parent}"')[0], 1,
                         "no history, no answer")

    def test_bash_and_python_name_the_same_site_base(self):
        base = self.declare(self.site_conda)
        spec = importlib.util.spec_from_file_location("sp_probe", self.umbrella / "bin/lib/site_paths.py")
        sp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sp)
        with mock.patch.dict(os.environ, {"BDTOOLS_HOME": str(self.bdhome)}, clear=False):
            os.environ.pop("BDTOOLS_SITE_CONDA_BASE", None)
            self.assertEqual(str(sp.site_conda_base(self.umbrella)), base)
        self.assertEqual(self.sh("site_conda_base")[1], base)


if __name__ == "__main__":
    unittest.main()
