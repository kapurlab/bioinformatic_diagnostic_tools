#!/usr/bin/env python3
"""Which checkout is "the tool" — and the shell and python resolvers agreeing.

The 2026-08 incident this pins down: an umbrella installed at
<root>/tools/bioinformatic_diagnostic_tools with its tool checkouts as siblings.
tool_dir() honoured only $BDTOOLS_TOOLSDIR, so with that variable unset —
which is the normal state of an interactive shell — `bdtools check-updates` run
from inside that very tree reported the operator's PERSONAL copies, and
`bdtools install` built a second private one beside the shared tool everyone
actually runs. Two checkouts, every report about the wrong one, and a shipped
fix that "does not apply".

The rule must RECOGNISE a site tree, never assume one: a laptop has no sibling
checkout beside the umbrella and has to keep landing on the per-user path.

The 2026-09 incident (ICAR-NIVEDI) is the layout the sibling rule cannot see:
the umbrella cloned into the operator's home, the tools in /srv/icar/tools, and
sites/site.conf naming TOOLS_ROOT the whole time. sync.sh read that file;
tool_dir did not, so `status` and `doctor` called every deployed tool "(not
installed)" on a fully deployed box. A configured root now counts — as a claim
to be checked, never as a path to be assumed.
"""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ToolDirResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)

        # A site tree: <root>/tools/{bioinformatic_diagnostic_tools, irma_gui}
        self.tools = base / "site/tools"
        self.umbrella = self.tools / "bioinformatic_diagnostic_tools"
        (self.umbrella / "bin/lib").mkdir(parents=True)
        for rel in ("bin/lib/common.sh", "bin/lib/tool_launch.py",
                    "bin/lib/site_paths.py", "bin/lib/manifest.py",
                    "bin/lib/config_hygiene.py"):
            src = ROOT / rel
            if src.exists():
                (self.umbrella / rel).write_text(src.read_text(encoding="utf-8"),
                                                 encoding="utf-8")
        (ROOT / "tools.yml").exists() and \
            (self.umbrella / "tools.yml").write_text(
                (ROOT / "tools.yml").read_text(encoding="utf-8"), encoding="utf-8")

        self.shared_tool = self.tools / "irma_gui"
        (self.shared_tool / ".git").mkdir(parents=True)

        self.home = base / "bdhome"
        (self.home / "checkouts").mkdir(parents=True)

    def py_resolve(self, tool, **env):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("BDTOOLS_")}
        clean["BDTOOLS_HOME"] = str(self.home)
        clean.update(env)
        with mock.patch.dict(os.environ, clean, clear=True):
            tl = load_module("tl_probe", self.umbrella / "bin/lib/tool_launch.py")
            return tl.tool_dir(tool)

    def sh_resolve(self, tool, **env):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("BDTOOLS_")}
        clean["BDTOOLS_HOME"] = str(self.home)
        clean.update(env)
        script = (f'source "{self.umbrella}/bin/lib/common.sh"; tool_dir {tool}')
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env=clean, cwd=str(self.umbrella))
        return r.stdout.strip()

    # ---- the incident -------------------------------------------------------
    def test_a_sibling_checkout_beside_the_umbrella_is_the_tool(self):
        self.assertEqual(self.py_resolve("irma_gui"), str(self.shared_tool))

    def test_a_site_tree_is_recognised_not_assumed(self):
        """No sibling for this tool: fall through to the managed per-user copy.
        Without this, a laptop would resolve every tool to a directory that does
        not exist just because the umbrella has a parent — which everything has."""
        self.assertEqual(self.py_resolve("genoflu_gui"),
                         str(self.home / "checkouts/genoflu_gui"))

    def test_an_explicit_toolsdir_still_wins(self):
        other = Path(self.tmp.name) / "explicit"
        (other / "irma_gui").mkdir(parents=True)
        self.assertEqual(self.py_resolve("irma_gui", BDTOOLS_TOOLSDIR=str(other)),
                         str(other / "irma_gui"))

    def test_a_sibling_needs_to_be_a_real_checkout(self):
        """A stray directory named like a tool is not a checkout. Requiring .git
        keeps an unrelated neighbour — a data dir, a scratch folder — from
        capturing every command aimed at that tool."""
        (self.tools / "mlst_gui").mkdir()
        self.assertEqual(self.py_resolve("mlst_gui"),
                         str(self.home / "checkouts/mlst_gui"))

    # ---- the second incident: configured, not sibling -----------------------
    def write_site_conf(self, text):
        sites = self.umbrella / "sites"
        sites.mkdir(exist_ok=True)
        (sites / "site.conf").write_text(text, encoding="utf-8")

    def test_a_configured_tools_root_is_the_tool_when_there_is_no_sibling(self):
        """ICAR-NIVEDI: nothing is a sibling of anything; the written-down root
        has to count."""
        conf = Path(self.tmp.name) / "srv/icar/tools"
        (conf / "genoflu_gui/.git").mkdir(parents=True)
        self.write_site_conf(f"TOOLS_ROOT={conf}\n")
        self.assertEqual(self.py_resolve("genoflu_gui"), str(conf / "genoflu_gui"))
        self.assertEqual(self.sh_resolve("genoflu_gui"), str(conf / "genoflu_gui"))

    def test_the_site_file_is_read_the_way_its_author_wrote_it(self):
        """site.conf.example teaches TOOLS_ROOT=${SITE_ROOT}/tools. The shell
        sources the file and python expands it; a literal '${SITE_ROOT}/tools'
        from either resolver would be a path that cannot exist."""
        site = Path(self.tmp.name) / "srv/icar"
        (site / "tools/genoflu_gui/.git").mkdir(parents=True)
        self.write_site_conf(f"SITE_ROOT={site}\nTOOLS_ROOT=${{SITE_ROOT}}/tools\n")
        want = str(site / "tools/genoflu_gui")
        self.assertEqual(self.py_resolve("genoflu_gui"), want)
        self.assertEqual(self.sh_resolve("genoflu_gui"), want)

    def test_a_configured_root_is_a_claim_not_proof(self):
        """The root names a directory that does not hold this tool: fall through
        exactly as before — to the sibling if there is one, else per-user. A
        configured root must never make a tool resolve to a checkout that is
        not there."""
        conf = Path(self.tmp.name) / "srv/icar/tools"
        conf.mkdir(parents=True)
        self.write_site_conf(f"TOOLS_ROOT={conf}\n")
        self.assertEqual(self.py_resolve("irma_gui"), str(self.shared_tool))
        self.assertEqual(self.sh_resolve("irma_gui"), str(self.shared_tool))
        self.assertEqual(self.py_resolve("genoflu_gui"),
                         str(self.home / "checkouts/genoflu_gui"))

    def test_a_configured_root_outranks_a_sibling(self):
        """Both name a checkout for the same tool: the admin's statement wins over
        the layout inference. The sibling rule is a guess about what a parent
        directory means; TOOLS_ROOT is what someone wrote down on purpose."""
        conf = Path(self.tmp.name) / "srv/icar/tools"
        (conf / "irma_gui/.git").mkdir(parents=True)
        self.write_site_conf(f"TOOLS_ROOT={conf}\n")
        self.assertEqual(self.py_resolve("irma_gui"), str(conf / "irma_gui"))
        self.assertEqual(self.sh_resolve("irma_gui"), str(conf / "irma_gui"))

    def test_an_explicit_toolsdir_still_outranks_the_site_file(self):
        conf = Path(self.tmp.name) / "srv/icar/tools"
        (conf / "irma_gui/.git").mkdir(parents=True)
        self.write_site_conf(f"TOOLS_ROOT={conf}\n")
        other = Path(self.tmp.name) / "explicit"
        (other / "irma_gui").mkdir(parents=True)
        self.assertEqual(self.py_resolve("irma_gui", BDTOOLS_TOOLSDIR=str(other)),
                         str(other / "irma_gui"))
        self.assertEqual(self.sh_resolve("irma_gui", BDTOOLS_TOOLSDIR=str(other)),
                         str(other / "irma_gui"))

    def test_a_tools_root_merely_in_the_environment_is_not_configuration(self):
        """An exported TOOLS_ROOT is not a site file. Python's reader never sees
        the environment, so the shell must not either — or the two resolvers
        would disagree on exactly the machines where someone has sourced
        site.conf into their shell."""
        conf = Path(self.tmp.name) / "srv/icar/tools"
        (conf / "irma_gui/.git").mkdir(parents=True)
        self.assertEqual(self.sh_resolve("irma_gui", TOOLS_ROOT=str(conf)),
                         str(self.shared_tool))
        self.assertEqual(self.py_resolve("irma_gui", TOOLS_ROOT=str(conf)),
                         str(self.shared_tool))

    def test_shell_and_python_resolvers_agree_about_the_site_file(self):
        site = Path(self.tmp.name) / "srv/icar"
        (site / "tools/genoflu_gui/.git").mkdir(parents=True)
        (site / "tools/irma_gui/.git").mkdir(parents=True)
        self.write_site_conf(f"SITE_ROOT={site}\nTOOLS_ROOT=${{SITE_ROOT}}/tools\n")
        (self.tools / "mlst_gui/.git").mkdir(parents=True)
        for tool in ("genoflu_gui", "irma_gui", "mlst_gui", "ksnp_gui"):
            with self.subTest(tool=tool):
                self.assertEqual(self.py_resolve(tool), self.sh_resolve(tool))

    # ---- the two copies of the rule -----------------------------------------
    def test_shell_and_python_resolvers_agree(self):
        """common.sh:tool_dir and tool_launch.py:tool_dir are the same rule
        written twice. Them disagreeing about which copy is live IS the bug:
        the CLI graded one checkout while the dashboard launched another."""
        (self.tools / "mlst_gui/.git").mkdir(parents=True)
        explicit = Path(self.tmp.name) / "explicit"
        (explicit / "ksnp_gui").mkdir(parents=True)
        cases = [
            ("irma_gui", {}),                                       # site sibling
            ("genoflu_gui", {}),                                    # nothing -> per-user
            ("mlst_gui", {}),                                       # site sibling
            ("ksnp_gui", {"BDTOOLS_TOOLSDIR": str(explicit)}),      # explicit wins
            ("irma_gui", {"BDTOOLS_TOOLSDIR": str(explicit)}),      # explicit set, no match
        ]
        for tool, env in cases:
            with self.subTest(tool=tool, env=sorted(env)):
                self.assertEqual(self.py_resolve(tool, **env), self.sh_resolve(tool, **env))


if __name__ == "__main__":
    unittest.main()
