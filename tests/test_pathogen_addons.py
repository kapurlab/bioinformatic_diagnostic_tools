#!/usr/bin/env python3
"""Pathogen add-ons (docs/BUILDING_AN_ADDON.md): installed, pinned, updated and
graded like any tool, but with no web app — vsnp_gui runs their hooks.

Pinned here:
  * the launcher resolves an add-on's env without a backend/ or a web stack,
    marks it not launchable, refuses `cmd`/`repro` for it, and still exports
    its env to the consumer as BDTOOLS_SIBLING_ENV_<TOOL>;
  * requirements: an add-on needs no web modules, its library is graded under
    <db root>/addons/<id>, and it is an optional sibling of its consumer;
  * dashboards never draw an add-on as a card, and updating one also stops its
    consumer (the consumer's jobs run the add-on's env);
  * DEFAULT_PATHOGEN from site.conf reaches tools as BDTOOLS_DEFAULT_PATHOGEN,
    and a malformed value is dropped;
  * the sandbox override file follows XDG_CONFIG_HOME;
  * `bdtools new-addon` fills the template (ids, names, paths) and copies
    binary fixtures untouched; the template's manifest names files that exist;
  * install-server.sh survives an add-on's empty card list under set -u.
"""
import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin/lib"))
import addon_tools  # noqa: E402
import requirements as REQ  # noqa: E402
import site_paths  # noqa: E402
import suite_common  # noqa: E402
import tool_launch  # noqa: E402

MANIFEST = """suite_version: "test"

tools:
  - name: vsnp_gui
    repo: https://example.invalid/vsnp_gui.git
    version: v1.0.0
    updates: install
    ood_apps: [vsnp_gui]
    env: vsnp3

  - name: fmdv_addon
    repo: https://example.invalid/fmdv_addon.git
    version: v0.1.0
    updates: install
    addon_for: [vsnp_gui]
    env: fmdv_addon
"""


class AddonResolutionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.manifest = t / "tools.yml"
        self.manifest.write_text(MANIFEST)
        self.tools = t / "tools"
        for tool in ("vsnp_gui", "fmdv_addon"):
            (self.tools / tool / "env" / "bin").mkdir(parents=True)
            (self.tools / tool / "env" / "bin" / "python").write_text("#!/bin/sh\n")
        (self.tools / "vsnp_gui" / "backend").mkdir()
        sp = self.tools / "vsnp_gui" / "env" / "lib" / "python3.12" / "site-packages"
        for mod in ("uvicorn", "fastapi"):
            (sp / mod).mkdir(parents=True)
        (self.tools / "fmdv_addon" / "addon.json").write_text(json.dumps({
            "contract": 1, "id": "fmdv", "name": "FMDV", "version": "0.1.0",
            "library": {"markers": ["lineages.json", "reference_options"]},
            "requires": {"binaries": ["minimap2", "samtools"], "modules": []}}))
        self.env = mock.patch.dict(os.environ, {
            "BDTOOLS_MANIFEST": str(self.manifest), "BDTOOLS_TOOLSDIR": str(self.tools),
            "BDTOOLS_HOME": str(t / "home"), "BDTOOLS_DB_ROOT": str(t / "db")})
        self.env.start()
        self.man = mock.patch.object(tool_launch, "_MANIFEST", str(self.manifest))
        self.man.start()

    def tearDown(self):
        self.man.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_addon_is_recognised(self):
        self.assertTrue(tool_launch.is_addon("fmdv_addon"))
        self.assertEqual(tool_launch.addon_for("fmdv_addon"), ["vsnp_gui"])
        self.assertFalse(tool_launch.is_addon("vsnp_gui"))

    def test_addon_resolves_without_backend_or_web_stack(self):
        plan = tool_launch.resolve("fmdv_addon", 0)
        self.assertEqual(plan["env_dir"], str(self.tools / "fmdv_addon" / "env"))
        self.assertFalse(plan["launchable"])
        self.assertEqual(plan["argv"], [], "an add-on has nothing to start")

    def test_consumer_gets_the_addon_env(self):
        plan = tool_launch.resolve("vsnp_gui", 8080)
        self.assertTrue(plan["launchable"])
        self.assertEqual(plan["env"].get("BDTOOLS_SIBLING_ENV_FMDV_ADDON"),
                         str(self.tools / "fmdv_addon" / "env"))

    def test_cli_refuses_to_launch_an_addon(self):
        with mock.patch.object(sys, "argv", ["tool_launch.py", "cmd", "fmdv_addon", "8080"]):
            with self.assertRaises(SystemExit) as ctx:
                tool_launch._cli()
        self.assertIn("add-on for vsnp_gui", str(ctx.exception.code))

    def test_requirements(self):
        spec = REQ.for_tool("fmdv_addon")
        self.assertEqual(spec["modules"], [], "an add-on needs no web layer")
        self.assertEqual(spec["binaries"], ["minimap2", "samtools"])
        db = spec["databases"][0]
        self.assertEqual(db["default_under"], ("db_root", "addons/fmdv"))
        self.assertEqual(db["markers"], ["lineages.json", "reference_options"])
        consumer = REQ.for_tool("vsnp_gui")
        self.assertIn("fmdv_addon", consumer["sibling_tools"])
        self.assertIn("fmdv_addon", consumer["optional_siblings"])
        self.assertIn("kraken_id_parse_gui", consumer["sibling_tools"], "existing siblings kept")

    def test_dashboards_and_update_scope(self):
        with mock.patch.object(suite_common, "REPO_DIR", str(ROOT)):
            self.assertEqual(suite_common.addons_of(), {"vsnp_gui": ["fmdv_addon"]})
            names, marks = suite_common.update_scope("fmdv_addon", running={"vsnp_gui"})
            self.assertEqual(names, {"fmdv_addon", "vsnp_gui"},
                             "updating an add-on stops its consumer, whose jobs run its env")
            listed = "TOOL VERSION\nvsnp_gui v1\nfmdv_addon v0.1.0\n"
            with mock.patch.object(suite_common.subprocess, "run",
                                   return_value=mock.Mock(stdout=listed)):
                self.assertEqual(suite_common.list_tools(), ["vsnp_gui"], "an add-on is not a card")


class SiteDefaultPathogenTests(unittest.TestCase):

    def test_env_wins_and_shape_is_checked(self):
        with mock.patch.dict(os.environ, {"BDTOOLS_DEFAULT_PATHOGEN": "fmdv"}):
            self.assertEqual(site_paths.default_pathogen(), "fmdv")
            self.assertEqual(site_paths.as_env().get("BDTOOLS_DEFAULT_PATHOGEN"), "fmdv")
        with mock.patch.dict(os.environ, {"BDTOOLS_DEFAULT_PATHOGEN": "../etc"}):
            self.assertIsNone(site_paths.default_pathogen())

    def test_site_conf_value(self):
        with tempfile.TemporaryDirectory() as t, \
                mock.patch.dict(os.environ, {"BDTOOLS_HOME": t}, clear=False):
            os.environ.pop("BDTOOLS_DEFAULT_PATHOGEN", None)
            Path(t, "site.conf").write_text("DEFAULT_PATHOGEN=hpai\n")
            self.assertEqual(site_paths.default_pathogen(), "hpai")


class SandboxEnvXdgTests(unittest.TestCase):

    def test_sandbox_env_follows_xdg_config_home(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": t}):
            Path(t, "vsnp_gui").mkdir()
            Path(t, "vsnp_gui", "sandbox.env").write_text("BDTOOLS_APP_DIR=/x\nBDTOOLS_APP_ENV=/y\n")
            self.assertEqual(tool_launch._sandbox_env("vsnp_gui"), ("/x", "/y"))


class NewAddonTests(unittest.TestCase):

    def test_template_fills_and_copies(self):
        with tempfile.TemporaryDirectory() as t:
            dest = Path(t) / "hpai_addon"
            with mock.patch("sys.stdout"):
                addon_tools.new_addon("hpai", "Highly pathogenic avian influenza", dest)
            man = json.loads((dest / "addon.json").read_text())
            self.assertEqual((man["id"], man["tool"], man["name"]),
                             ("hpai", "hpai_addon", "Highly pathogenic avian influenza"))
            for rel in list(man["hooks"].values()) + [a["command"] for a in man["actions"]]:
                self.assertTrue((dest / rel).is_file(), rel)
            self.assertTrue((dest / "tests/contract/fixture/step1/SAMPLE-01/alignment_hpai_example").is_dir(),
                            "placeholders in paths are filled")
            gz = dest / "tests/contract/fixture/step1/SAMPLE-01/SAMPLE-01_R1.fastq.gz"
            self.assertTrue(gzip.decompress(gz.read_bytes()).startswith(b"@r0"), "binary copied untouched")
            leftovers = [str(p) for p in dest.rglob("*") if p.is_file() and p.suffix != ".gz"
                         and "__ADDON_" in p.read_text(errors="ignore")]
            self.assertEqual(leftovers, [])
            self.assertTrue(os.access(dest / "deploy/build_library.sh", os.X_OK))

    def test_bad_id_refused(self):
        with tempfile.TemporaryDirectory() as t, self.assertRaises(SystemExit):
            addon_tools.new_addon("Bad-Id", "x", Path(t) / "x")


class InstallServerEmptyCardsTests(unittest.TestCase):

    def test_empty_card_list_under_set_u(self):
        """The pattern install-server.sh uses for an add-on's (empty) card list."""
        script = 'set -u; OOD_APPS=(); for a in ${OOD_APPS[@]+"${OOD_APPS[@]}"}; do echo "$a"; done; ' \
                 'echo "cards: ${OOD_APPS[*]+${OOD_APPS[*]}}"'
        out = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "cards:"))
        src = (ROOT / "bin/install-server.sh").read_text()
        self.assertNotIn('for app in "${OOD_APPS[@]}"', src, "every card loop is guarded")


if __name__ == "__main__":
    unittest.main()
