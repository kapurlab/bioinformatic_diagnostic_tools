#!/usr/bin/env python3
"""A site tool launches from site state alone — the same way for every user.

THE 2026-10-29 USDA REPORT. On a SciComp OOD site, vSNP opened from the dashboard
for tstuber, who installed the suite, and for nobody else. The site's vsnp3 env
had never been built; vSNP resolved through tstuber's personal state instead
(his sandbox.env, his env-prefix record, the conda his login profile put on
CONDA_EXE) — a world-readable miniconda, so it ran for him and resolved nothing
for anyone else. These tests pin the rule that replaced it:

  * a site tool resolves only from the site's own envs and declared conda base,
  * an env counts only if it can serve the tool (uvicorn + fastapi in its OWN
    site-packages),
  * with no such env every user gets the same SiteEnvMissing, naming what was
    checked and the command that fixes it,
  * per-user and local installs resolve exactly as before.

No conda, no network: envs are directories with a bin/python and the packages
the check looks for.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin/lib"))


def load():
    spec = importlib.util.spec_from_file_location("tool_launch_site", ROOT / "bin/lib/tool_launch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TL = load()


def make_env(prefix, web=True, py="python3.12"):
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    (prefix / "bin/python").write_text("#!/bin/sh\n")
    (prefix / "bin/python").chmod(0o755)
    if web:
        for mod in ("uvicorn", "fastapi"):
            (prefix / "lib" / py / "site-packages" / mod).mkdir(parents=True, exist_ok=True)
    return prefix


class SiteResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.tools = t / "site/tools"
        self.umbrella = self.tools / "bioinformatic_diagnostic_tools"
        (self.umbrella / "sites").mkdir(parents=True)
        (self.tools / "vsnp_gui/backend").mkdir(parents=True)
        # tstuber: a sandbox.env, an env-prefix record, and a personal conda whose
        # vsnp3 env is complete — every per-user route the USDA session took.
        self.personal_base = t / "software/miniconda"
        self.personal = make_env(self.personal_base / "envs/vsnp3")
        self.users = {}
        for user in ("tstuber", "oaosipenko"):
            home, bdhome = t / "home" / user, t / "home" / user / ".local/share/bdtools"
            bdhome.mkdir(parents=True)
            self.users[user] = (home, bdhome)
        home, bdhome = self.users["tstuber"]
        (bdhome / "env-prefix").mkdir()
        (bdhome / "env-prefix/vsnp_gui").write_text(f"{self.personal}\n")
        sandbox_dir = t / "home/tstuber/vsnp_gui"
        (sandbox_dir / "backend").mkdir(parents=True)
        (home / ".config/vsnp_gui").mkdir(parents=True)
        (home / ".config/vsnp_gui/sandbox.env").write_text(
            f"BDTOOLS_APP_DIR={sandbox_dir}\nBDTOOLS_APP_ENV={self.personal}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, user, **extra):
        home, bdhome = self.users[user]
        env = {"HOME": str(home), "BDTOOLS_HOME": str(bdhome),
               "BDTOOLS_TOOLSDIR": str(self.tools)}
        if user == "tstuber":   # conda init in his login profile reaches the job
            env.update(CONDA_BASE=str(self.personal_base),
                       CONDA_EXE=str(self.personal_base / "bin/conda"))
        env.update(extra)
        clear = {k: "" for k in ("CONDA_BASE", "CONDA_EXE", "BDTOOLS_SITE_CONDA_BASE",
                                 "XDG_DATA_HOME", "BDTOOLS_SITE_ROOT")}
        with mock.patch.dict(os.environ, {**clear, **env}), \
             mock.patch.object(TL, "_REPO_DIR", str(self.umbrella)), \
             mock.patch.object(TL, "_WEB_READY", set()):
            return TL.resolve("vsnp_gui", 8123)

    def test_every_user_gets_the_site_env(self):
        site = make_env(self.tools / "vsnp3")
        plans = {u: self.resolve(u) for u in self.users}
        for user, plan in plans.items():
            self.assertEqual(plan["dir"], str(self.tools / "vsnp_gui"), user)
            self.assertEqual(plan["env_dir"], str(site), user)
            self.assertEqual(plan["env_source"], "site shared env", user)
            self.assertEqual(plan["site_root"], str(self.tools), user)
            self.assertEqual(plan["env_overrides"].get("PYTHONNOUSERSITE"), "1", user)
        self.assertEqual(plans["tstuber"]["python"], plans["oaosipenko"]["python"])

    def test_with_no_site_env_every_user_gets_the_same_error(self):
        # The USDA state: <root>/vsnp3 never built. Before, tstuber launched from
        # his personal env and oaosipenko got nothing.
        errors = {}
        for user in self.users:
            with self.assertRaises(TL.SiteEnvMissing, msg=user) as cm:
                self.resolve(user)
            errors[user] = str(cm.exception)
        core = [e.split(" Your account also reaches")[0].split(" Otherwise ")[0]
                for e in errors.values()]
        self.assertEqual(core[0], core[1], "the finding itself is identical for both")
        for msg in errors.values():
            self.assertIn("will not start for any user", msg)
            self.assertIn(str(self.tools / "vsnp3"), msg)
            self.assertIn("install_ood.sh --site-conf deploy/site.conf toolchain", msg)
            self.assertIn("Step 3b", msg)
        # The installer is told what used to make it work for him, and how to
        # make that env everyone's if it is meant to be.
        self.assertIn(str(self.personal), errors["tstuber"])
        self.assertIn(f"CONDA_BASE={self.personal_base}", errors["tstuber"])
        self.assertNotIn(str(self.personal), errors["oaosipenko"])

    def test_an_env_without_the_web_stack_is_not_a_candidate(self):
        # A bare bioconda vsnp3 env has a python and no uvicorn: it would "resolve"
        # and then die at launch, or run only for whoever has uvicorn in ~/.local.
        make_env(self.tools / "vsnp3", web=False)
        with self.assertRaises(TL.SiteEnvMissing) as cm:
            self.resolve("oaosipenko")
        self.assertIn("no uvicorn/fastapi", str(cm.exception))

    def test_the_site_conda_base_is_declared_not_inherited(self):
        site_base = Path(self.tmp.name) / "site/conda"
        declared = make_env(site_base / "envs/vsnp3")
        (self.umbrella / "sites/site.conf").write_text(f"CONDA_BASE={site_base}\n")
        for user in self.users:   # tstuber's CONDA_BASE points at his own conda
            plan = self.resolve(user)
            self.assertEqual(plan["env_dir"], str(declared), user)
            self.assertEqual(plan["env_source"], "site conda base", user)

    def test_the_cards_rendered_base_outranks_site_conf(self):
        (self.umbrella / "sites/site.conf").write_text("CONDA_BASE=/nowhere\n")
        card_base = Path(self.tmp.name) / "site/card-conda"
        rendered = make_env(card_base / "envs/vsnp3")
        plan = self.resolve("oaosipenko", BDTOOLS_SITE_CONDA_BASE=str(card_base))
        self.assertEqual(plan["env_dir"], str(rendered))

    def test_a_personal_site_conf_does_not_declare_the_base(self):
        # <BDTOOLS_HOME>/site.conf is per-user (install-server writes the
        # installer's); only sites/site.conf speaks for everyone.
        _home, bdhome = self.users["tstuber"]
        (bdhome / "site.conf").write_text(f"CONDA_BASE={self.personal_base}\n")
        with self.assertRaises(TL.SiteEnvMissing):
            self.resolve("tstuber")


class PersonalResolutionUnchangedTests(unittest.TestCase):
    """--local and --sandbox installs resolve exactly as they did."""

    def test_a_managed_checkout_still_uses_its_record_and_a_bare_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bdhome = t / "bdtools"
            (bdhome / "checkouts/vsnp_gui/backend").mkdir(parents=True)
            recorded = make_env(t / "conda/envs/vsnp3", web=False)  # no web check here
            (bdhome / "env-prefix").mkdir()
            (bdhome / "env-prefix/vsnp_gui").write_text(f"{recorded}\n")
            clear = {k: "" for k in ("CONDA_BASE", "CONDA_EXE", "BDTOOLS_TOOLSDIR",
                                     "XDG_DATA_HOME")}
            with mock.patch.dict(os.environ, {**clear, "HOME": str(t / "home"),
                                              "BDTOOLS_HOME": str(bdhome)}):
                plan = TL.resolve("vsnp_gui", 8124)
        self.assertEqual(plan["env_dir"], str(recorded))
        self.assertEqual(plan["site_root"], "")
        self.assertIn("env-prefix record", plan["env_source"])
        self.assertNotIn("PYTHONNOUSERSITE", plan["env_overrides"])

    def test_a_sandbox_env_still_wins_off_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            app = t / "sandbox/vsnp_gui"
            (app / "backend").mkdir(parents=True)
            env = make_env(t / "sandbox/env", web=False)
            home = t / "home"
            (home / ".config/vsnp_gui").mkdir(parents=True)
            (home / ".config/vsnp_gui/sandbox.env").write_text(
                f"BDTOOLS_APP_DIR={app}\nBDTOOLS_APP_ENV={env}\n")
            clear = {k: "" for k in ("CONDA_BASE", "CONDA_EXE", "BDTOOLS_TOOLSDIR")}
            with mock.patch.dict(os.environ, {**clear, "HOME": str(home),
                                              "BDTOOLS_HOME": str(t / "bdtools")}):
                plan = TL.resolve("vsnp_gui", 8125)
        self.assertEqual((plan["dir"], plan["env_dir"]), (str(app), str(env)))
        self.assertEqual(plan["site_root"], "")


class ShellAgreesTests(unittest.TestCase):
    """doctor and every bash caller ask the launcher; for a site tool with no site
    env they must not fall back to <dir>/env or this user's `conda run -n`."""

    def test_tool_env_python_does_not_fall_back_for_a_site_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            tools = t / "tools"
            (tools / "vsnp_gui/backend").mkdir(parents=True)
            (tools / "vsnp_gui/.git").mkdir()
            make_env(tools / "vsnp_gui/env", web=False)   # the old fallback's answer
            bdhome = t / "bdtools"
            bdhome.mkdir()
            # Site recognised through the declared TOOLS_ROOT (the umbrella under
            # test is not a sibling of this fixture).
            (bdhome / "site.conf").write_text(f"TOOLS_ROOT={tools}\n")
            env = {**os.environ, "HOME": str(t), "BDTOOLS_HOME": str(bdhome),
                   "BDTOOLS_TOOLSDIR": str(tools), "CONDA_BASE": "", "CONDA_EXE": "",
                   "PATH": "/usr/bin:/bin"}
            script = (f'set -euo pipefail\nsource "{ROOT}/bin/lib/common.sh"\n'
                      f'echo "py=[$(tool_env_python "{tools}/vsnp_gui" vsnp3 vsnp_gui)]"\n'
                      f'tool_env_problem vsnp_gui\n')
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "py=[]", "no personal fallback for a site tool")
        self.assertIn("will not start for any user", lines[1])
        self.assertIn("install_ood.sh", lines[2], "line 2 is the fix command")


class CardTests(unittest.TestCase):
    """The dashboard card: a site session never searches the user's own conda."""

    SCRIPT = ROOT / "ood/apps/bdtools_dashboard/template/script.sh.erb"

    def run_search(self, site_tools, home, site_conda_base="", inherited_base=""):
        text = self.SCRIPT.read_text()
        start = text.index("SITE_TOOLS_ROOT=/srv/")
        end = text.index('echo "  python:   ${PYTHON}"')
        snippet = text[start:end]
        # skip the ERB-only budget block between the checkout lookup and conda
        snippet = (snippet[:snippet.index("# ---- authentication material")]
                   + snippet[snippet.index("# ---- the conda base"):])
        snippet = snippet.replace("/srv/kapurlab/tools", str(site_tools))
        snippet = snippet.replace("SITE_CONDA_BASE=\n", f"SITE_CONDA_BASE={site_conda_base}\n")
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
        if inherited_base:
            env["CONDA_BASE"] = inherited_base
        return subprocess.run(["bash", "-c", snippet + '\necho "PYTHON=${PYTHON}"'],
                              capture_output=True, text=True, env=env)

    def fake_python(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")   # "imports" anything
        path.chmod(0o755)
        return path

    def test_a_site_session_ignores_the_users_conda(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            tools = t / "tools"
            (tools / "bioinformatic_diagnostic_tools/bin/ood_dashboard").mkdir(parents=True)
            self.fake_python(t / "home/miniconda3/bin/python")
            r = self.run_search(tools, t / "home", inherited_base=str(t / "home/miniconda3"))
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("never one user's own conda", r.stderr)
            site_py = self.fake_python(tools / "mlst_gui/env/bin/python")
            r = self.run_search(tools, t / "home", inherited_base=str(t / "home/miniconda3"))
            self.assertIn(f"PYTHON={site_py}", r.stdout)

    def test_the_rendered_site_base_is_searched(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            tools = t / "tools"
            (tools / "bioinformatic_diagnostic_tools/bin/ood_dashboard").mkdir(parents=True)
            base_py = self.fake_python(t / "sitebase/envs/vsnp3/bin/python")
            r = self.run_search(tools, t / "home", site_conda_base=str(t / "sitebase"))
            self.assertIn(f"PYTHON={base_py}", r.stdout)

    def test_install_server_renders_the_site_base(self):
        script = (ROOT / "bin/install-server.sh").read_text()
        self.assertIn('s|^SITE_CONDA_BASE=\\$|SITE_CONDA_BASE=${CONDA_BASE}|', script)
        self.assertIn("\nSITE_CONDA_BASE=\n", self.SCRIPT.read_text(),
                      "the anchor install-server.sh renders into")


if __name__ == "__main__":
    unittest.main()
