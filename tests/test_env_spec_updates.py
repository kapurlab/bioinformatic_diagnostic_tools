#!/usr/bin/env python3
"""A tool's environment is checked against its own spec, and offered a refresh.

The 2026-10-02 case: kraken_id_parse_gui's conda_setup/environment.yml gained
`plotly <6` in September. On the Ames HPC the code moved (`bdtools sync`) and
the env did not, so a Full identification ran for an hour and died at the
report with "No module named 'plotly'" — while the dashboard said "✓ Up to
date", because every check it made compared versions, and the versions were
right. Here the spec is read against the env's conda-meta, the gap becomes a
fourth kind of update, and the remedy is the installer's own additive
--rebuild in the form that owns the tree.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin/lib"))


def load_suite_common():
    spec = importlib.util.spec_from_file_location(
        "sc_env_probe", ROOT / "bin/lib/suite_common.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SPEC = """\
# Environment for a tool
name: demo_env
channels:
  - conda-forge
  - bioconda

dependencies:
  # Core
  - python=3.10
  - pip
  - numpy
  - fastapi>=0.115.0
  - uvicorn-standard
  - dask >=2024.8        # a floor, see tools.yml
  - conda-forge::weasyprint
  - plotly <6            # interactive charts (guarded)
  - "pyyaml"
  - pip:
      - PyVCF3>=1.0.0
      - pydantic>=2.0.0
  - humanize
"""

DECLARED = ["python", "pip", "numpy", "fastapi", "uvicorn-standard", "dask",
            "weasyprint", "plotly", "pyyaml", "humanize"]


def fake_env(root, *packages):
    """A prefix with just enough of a conda env for the installed-side reader."""
    env = Path(root) / "env"
    (env / "conda-meta").mkdir(parents=True)
    (env / "bin").mkdir()
    (env / "bin" / "python").write_text("")
    for name in packages:
        (env / "conda-meta" / f"{name}-1.0-h0_0.json").write_text("{}")
    return str(env)


class SpecReader(unittest.TestCase):
    def setUp(self):
        self.sc = load_suite_common()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spec = Path(self.tmp.name) / "environment.yml"
        self.spec.write_text(SPEC, encoding="utf-8")

    def test_it_names_conda_packages_only(self):
        """Comments, channel prefixes, constraints and quotes are stripped; the
        pip sub-list is skipped (conda-meta cannot see pip installs); the entry
        after the pip block is still read."""
        self.assertEqual(self.sc.env_spec_declared(str(self.spec)), DECLARED)

    def test_items_at_column_zero_are_still_the_dependencies(self):
        flat = "name: x\ndependencies:\n- python\n- plotly <6\n- pip:\n  - foo\n- numpy\nprefix: /x\n"
        self.spec.write_text(flat, encoding="utf-8")
        self.assertEqual(self.sc.env_spec_declared(str(self.spec)), ["python", "plotly", "numpy"])

    def test_a_missing_file_declares_nothing(self):
        self.assertEqual(self.sc.env_spec_declared(str(self.spec) + ".nope"), [])


class SpecModule(unittest.TestCase):
    """lib/env_spec.py — shared by the dashboard and complete-env.sh."""

    def setUp(self):
        import env_spec
        self.es = env_spec
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spec = Path(self.tmp.name) / "environment.yml"
        self.spec.write_text(SPEC, encoding="utf-8")

    def test_the_reader_keeps_channels_and_the_specs_as_written(self):
        out = self.es.read_spec(str(self.spec))
        self.assertEqual(out["channels"], ["conda-forge", "bioconda"])
        self.assertEqual([n for n, _ in out["declared"]], DECLARED)
        specs = dict(out["declared"])
        self.assertEqual(specs["plotly"], "plotly <6")
        self.assertEqual(specs["weasyprint"], "conda-forge::weasyprint")
        self.assertEqual(specs["dask"], "dask >=2024.8")
        self.assertEqual(specs["pyyaml"], "pyyaml")

    def test_the_cli_prints_what_the_shell_asks_for(self):
        import subprocess
        env = fake_env(self.tmp.name, *(n for n in DECLARED if n != "plotly"))
        base = [sys.executable, str(ROOT / "bin/lib/env_spec.py"), "kraken_id_parse_gui",
                "--spec", str(self.spec), "--env", env]
        field = lambda f: subprocess.run(base + ["--field", f], capture_output=True, text=True, check=True).stdout
        self.assertEqual(field("applies").strip(), "true")
        self.assertEqual(field("env_dir").strip(), env)
        self.assertEqual(field("missing").split(), ["plotly", "<6"])
        self.assertEqual(field("channels").split(), ["conda-forge", "bioconda"])
        whole = json.loads(subprocess.run(base, capture_output=True, text=True, check=True).stdout)
        self.assertTrue(whole["applies"])
        self.assertEqual([m["name"] for m in whole["missing"]], ["plotly"])
        gone = subprocess.run(base[:3] + ["--spec", str(self.spec) + ".nope", "--field", "applies"],
                              capture_output=True, text=True, check=True).stdout
        self.assertEqual(gone.strip(), "false")

    def test_a_refusal_is_remembered_for_exactly_that_missing_set(self):
        with mock.patch.dict(os.environ, {"BDTOOLS_HOME": self.tmp.name}):
            self.es.record_blocked("kraken_id_parse_gui", ["plotly"], "cannot be added")
            self.assertEqual(self.es.blocked("kraken_id_parse_gui", ["plotly"])["reason"], "cannot be added")
            # A changed spec is a new question.
            self.assertIsNone(self.es.blocked("kraken_id_parse_gui", ["plotly", "pysam"]))
            self.assertIsNone(self.es.blocked("irma_gui", ["plotly"]))
            self.es.clear_blocked("kraken_id_parse_gui")
            self.assertIsNone(self.es.blocked("kraken_id_parse_gui", ["plotly"]))


class Drift(unittest.TestCase):
    def setUp(self):
        self.sc = load_suite_common()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spec = Path(self.tmp.name) / "environment.yml"
        self.spec.write_text(SPEC, encoding="utf-8")

    def test_it_names_what_the_env_lacks(self):
        env = fake_env(self.tmp.name, *(n for n in DECLARED if n != "plotly"))
        drift = self.sc.env_spec_drift("kraken_id_parse_gui", spec_path=str(self.spec), env_dir=env)
        self.assertEqual(drift["missing"], ["plotly"])
        self.assertEqual(drift["env_dir"], env)
        self.assertEqual(drift["declared"], DECLARED)

    def test_a_complete_env_has_no_gap(self):
        env = fake_env(self.tmp.name, *DECLARED)
        drift = self.sc.env_spec_drift("kraken_id_parse_gui", spec_path=str(self.spec), env_dir=env)
        self.assertEqual(drift["missing"], [])

    def test_conda_meta_case_does_not_matter(self):
        env = fake_env(self.tmp.name, *(n for n in DECLARED if n != "plotly"), "Plotly")
        drift = self.sc.env_spec_drift("kraken_id_parse_gui", spec_path=str(self.spec), env_dir=env)
        self.assertEqual(drift["missing"], [])

    def test_no_spec_no_env_or_no_conda_meta_is_not_a_finding(self):
        """vsnp_gui has no environment.yml; a tool with no env needs `install`,
        not a refresh; a prefix without conda-meta cannot be read. None of these
        may surface as "your environment is behind"."""
        env = fake_env(self.tmp.name, *DECLARED)
        self.assertIsNone(self.sc.env_spec_drift("vsnp_gui", spec_path=str(self.spec) + ".nope", env_dir=env))
        self.assertIsNone(self.sc.env_spec_drift("x", spec_path=str(self.spec), env_dir=""))
        bare = Path(self.tmp.name) / "bare"
        bare.mkdir()
        self.assertIsNone(self.sc.env_spec_drift("x", spec_path=str(self.spec), env_dir=str(bare)))


class RecordsAndCommands(unittest.TestCase):
    def setUp(self):
        self.sc = load_suite_common()
        self.sc.list_tools = lambda: ["kraken_id_parse_gui", "irma_gui", "vsnp_gui"]
        self.drifts = {
            "kraken_id_parse_gui": {"spec": "s", "env_dir": "/e/k", "declared": ["plotly"], "missing": ["plotly"]},
            "irma_gui": {"spec": "s", "env_dir": "/e/i", "declared": ["plotly"], "missing": []},
            "vsnp_gui": None,
        }
        self.sc.env_spec_drift = lambda name, spec_path=None, env_dir=None: self.drifts.get(name)

    def test_only_a_gap_makes_a_record_and_it_is_offered(self):
        with mock.patch.object(self.sc, "tool_is_updatable", lambda n: True):
            recs = self.sc.env_update_records()
        self.assertEqual([r["name"] for r in recs], ["kraken_id_parse_gui:env"])
        rec = recs[0]
        self.assertEqual(rec["kind"], "env")
        self.assertEqual(rec["tool"], "kraken_id_parse_gui")
        self.assertEqual(rec["missing"], ["plotly"])
        self.assertEqual(rec["latest"], "plotly")
        self.assertIn("1 declared package missing", rec["installed"])
        self.assertTrue(rec["update_available"])
        self.assertTrue(rec["newer_exists"])
        self.assertFalse(rec["report_only"])

    def test_a_report_only_tool_is_named_but_not_offered(self):
        with mock.patch.object(self.sc, "tool_is_updatable", lambda n: False):
            rec = self.sc.env_update_records()[0]
        self.assertFalse(rec["update_available"])
        self.assertTrue(rec["newer_exists"])
        self.assertTrue(rec["report_only"])

    def verbs(self, cmds):
        return [tuple(c[1:]) for c in cmds]

    def test_the_same_narrow_command_serves_every_kind_of_tree(self):
        """complete-env resolves the env the way a launch does and moves no
        checkout, so a site deployment and a managed checkout get the same
        command — neither installer's force-checkout nor a full re-solve."""
        kinds = {"kraken_id_parse_gui": "site", "irma_gui": "managed"}
        with mock.patch.object(self.sc, "tool_source_kind", lambda n: kinds[n]):
            self.assertEqual(self.verbs(self.sc.env_update_commands("kraken_id_parse_gui")),
                             [("complete-env", "kraken_id_parse_gui")])
            self.assertEqual(self.verbs(self.sc.env_update_commands("irma_gui")),
                             [("complete-env", "irma_gui")])

    def test_a_recorded_refusal_is_named_but_not_offered(self):
        """What this machine has shown it cannot add without moving installed
        packages is held — named, with the reason and the fuller remedy — until
        the spec's missing set changes. Otherwise the banner nags forever about
        a button whose run was always going to be refused."""
        with mock.patch.object(self.sc, "tool_is_updatable", lambda n: True):
            import env_spec
            with mock.patch.object(env_spec, "blocked",
                                   lambda name, missing: {"reason": "plotly conflicts", "missing": missing}
                                   if name == "kraken_id_parse_gui" else None):
                rec = self.sc.env_update_records()[0]
        self.assertFalse(rec["update_available"])
        self.assertTrue(rec["held"])
        self.assertEqual(rec["held_reason"], "plotly conflicts")
        self.assertIn("install kraken_id_parse_gui --rebuild", rec["held_fix"])

    def test_all_refreshes_only_the_environments_that_are_behind(self):
        log = []
        with mock.patch.object(self.sc, "tool_source_kind", lambda n: "managed"), \
             mock.patch.object(self.sc, "tool_is_updatable", lambda n: True):
            cmds = self.sc.env_update_commands("all", log.append)
        self.assertEqual(self.verbs(cmds), [("complete-env", "kraken_id_parse_gui")])
        text = "\n".join(log)
        self.assertIn("$ bdtools complete-env kraken_id_parse_gui", text)
        self.assertIn("plotly", text)
        self.assertIn("restore-env kraken_id_parse_gui", text)

    def test_all_skips_a_report_only_tool_with_a_note(self):
        log = []
        with mock.patch.object(self.sc, "tool_source_kind", lambda n: "managed"), \
             mock.patch.object(self.sc, "tool_is_updatable", lambda n: False):
            cmds = self.sc.env_update_commands("all", log.append)
        self.assertEqual(cmds, [])
        self.assertTrue(any("report-only" in l for l in log), log)
        self.assertTrue(any("Nothing to run" in l for l in log), log)

    def test_the_tool_whose_env_moves_is_stopped_first(self):
        running = {"kraken_id_parse_gui", "irma_gui"}
        self.assertEqual(self.sc.update_scope("env:kraken_id_parse_gui", running),
                         ({"kraken_id_parse_gui"}, {"kraken_id_parse_gui"}))
        self.assertEqual(self.sc.update_scope("env:all", running), (running, {"*"}))


if __name__ == "__main__":
    unittest.main()
