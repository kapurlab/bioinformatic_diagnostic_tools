#!/usr/bin/env python3
"""Regression tests for analysis-package versions (bin/lib/packages.py).

The suite tracked its GUI repos and nothing else, so a new bioconda release of the
software that actually produces results — vsnp3, AMRFinderPlus, kraken2, mlst,
IRMA, GenoFLU — was invisible: no version on the card, no notification, no button.
These tests pin the behaviour that fixes it, and in particular the ways it must
NOT go wrong:

  * a network failure must read as "unknown", never as "up to date",
  * only a strictly newer version counts as an update (channels renumber, and a
    locally patched build can sit ahead of the channel),
  * the version reported must come from the env that would actually run the tool.

No network: the channel side is exercised against a stubbed opener.
"""
import importlib.util
import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PKG = load("bdtools_packages", ROOT / "bin/lib/packages.py")
SC = load("bdtools_suite_common_pkg", ROOT / "bin/lib/suite_common.py")


class SpecTests(unittest.TestCase):
    def test_full_spec(self):
        self.assertEqual(PKG.parse_spec("bioconda::vsnp3=3.35"),
                         ("bioconda", "vsnp3", "3.35"))

    def test_partial_specs_degrade_instead_of_raising(self):
        # A half-written manifest entry should cost information, not a traceback in
        # the middle of a dashboard render.
        self.assertEqual(PKG.parse_spec("vsnp3"), ("bioconda", "vsnp3", ""))
        self.assertEqual(PKG.parse_spec("vsnp3=3.35"), ("bioconda", "vsnp3", "3.35"))
        self.assertEqual(PKG.parse_spec("conda-forge::snp-dists"),
                         ("conda-forge", "snp-dists", ""))


class ComparisonTests(unittest.TestCase):
    def test_numeric_runs_compare_numerically(self):
        self.assertTrue(PKG.is_newer("3.36", "3.35"))
        self.assertTrue(PKG.is_newer("2.10", "2.9"))      # not a string compare
        self.assertTrue(PKG.is_newer("4.2.7", "3.12.8"))
        self.assertFalse(PKG.is_newer("3.35", "3.36"))

    def test_equal_is_not_an_update(self):
        self.assertFalse(PKG.is_newer("3.35", "3.35"))

    def test_an_installed_version_ahead_of_the_channel_is_not_an_update(self):
        # A locally built or pre-release package must not be "downgraded" by a
        # badge that says an update is available.
        self.assertFalse(PKG.is_newer("1.07", "1.08"))

    def test_unknown_versions_are_never_an_update(self):
        self.assertFalse(PKG.is_newer("", "3.35"))
        self.assertFalse(PKG.is_newer("3.36", ""))


class InstalledVersionTests(unittest.TestCase):
    def test_reads_versions_from_conda_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "conda-meta"
            meta.mkdir()
            (meta / "vsnp3-3.35-hdfd78af_0.json").touch()
            (meta / "snp-dists-1.2.0-h577a1d6_0.json").touch()   # name has a dash
            (meta / "not-a-package.txt").touch()
            got = PKG.installed_versions(tmp)
        self.assertEqual(got["vsnp3"], "3.35")
        self.assertEqual(got["snp-dists"], "1.2.0")

    def test_missing_env_is_empty_not_an_error(self):
        self.assertEqual(PKG.installed_versions("/nonexistent/env"), {})
        self.assertEqual(PKG.installed_versions(""), {})


class ChannelLookupTests(unittest.TestCase):
    def _fake_urlopen(self, payload):
        class Resp(io.BytesIO):
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False
        return lambda req, timeout=None: Resp(json.dumps(payload).encode())

    def test_reads_latest_version(self):
        with mock.patch.object(PKG.urllib.request, "urlopen",
                               self._fake_urlopen({"latest_version": "3.36"})):
            cache = {}
            got = PKG.latest_version("bioconda", "vsnp3", cache=cache, now=1000)
        self.assertEqual(got, "3.36")
        self.assertEqual(cache["bioconda/vsnp3"]["latest"], "3.36")

    def test_network_failure_yields_unknown_not_up_to_date(self):
        def boom(req, timeout=None):
            raise OSError("no route to host")
        with mock.patch.object(PKG.urllib.request, "urlopen", boom):
            self.assertEqual(PKG.latest_version("bioconda", "vsnp3", cache={}), "")

    def test_a_failed_lookup_falls_back_to_the_cached_answer(self):
        cache = {"bioconda/vsnp3": {"latest": "3.35", "at": 0}}   # stale

        def boom(req, timeout=None):
            raise OSError("offline")
        with mock.patch.object(PKG.urllib.request, "urlopen", boom):
            got = PKG.latest_version("bioconda", "vsnp3", cache=cache, now=10 ** 9)
        self.assertEqual(got, "3.35")

    def test_fresh_cache_entry_skips_the_network_entirely(self):
        cache = {"bioconda/vsnp3": {"latest": "3.35", "at": 1000}}

        def boom(req, timeout=None):
            raise AssertionError("should not have called out")
        with mock.patch.object(PKG.urllib.request, "urlopen", boom):
            self.assertEqual(
                PKG.latest_version("bioconda", "vsnp3", cache=cache, now=1001), "3.35")

    def test_no_network_mode_never_calls_out(self):
        def boom(req, timeout=None):
            raise AssertionError("should not have called out")
        with mock.patch.object(PKG.urllib.request, "urlopen", boom):
            self.assertEqual(
                PKG.latest_version("bioconda", "vsnp3", cache={}, use_network=False), "")


class ReportTests(unittest.TestCase):
    def test_report_marks_updates_and_pin_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "conda-meta"
            meta.mkdir()
            (meta / "vsnp3-3.35-hdfd78af_0.json").touch()
            with mock.patch.object(PKG, "env_dir_for", return_value=tmp), \
                 mock.patch.object(PKG, "latest_version", return_value="3.36"), \
                 mock.patch.object(PKG, "_save_cache", lambda cache: None):
                records = PKG.report(["vsnp_gui"], use_network=True)
        vsnp = next(r for r in records if r["package"] == "vsnp3")
        self.assertEqual(vsnp["installed"], "3.35")
        self.assertEqual(vsnp["latest"], "3.36")
        self.assertTrue(vsnp["update_available"])
        self.assertIn("3.36", vsnp["status"])
        # tools.yml pins 3.35 and 3.35 is installed: no drift.
        self.assertFalse(vsnp["pin_drift"])

    def test_a_tool_with_no_env_is_reported_not_installed(self):
        with mock.patch.object(PKG, "env_dir_for", return_value=""), \
             mock.patch.object(PKG, "latest_version", return_value="3.36"), \
             mock.patch.object(PKG, "_save_cache", lambda cache: None):
            records = PKG.report(["vsnp_gui"], use_network=True)
        self.assertTrue(records)
        for rec in records:
            self.assertEqual(rec["status"], "not installed")
            # Nothing installed means nothing to update — offering an update for a
            # tool that is not there would be a dead button.
            self.assertFalse(rec["update_available"])

    def test_manifest_declares_packages_for_the_analysis_tools(self):
        # Guards against a tool losing its `packages:` line in a manifest edit: the
        # version panel would silently go blank for it.
        declared = PKG.declared()
        for tool in ("vsnp_gui", "amr_plus_gui", "mlst_gui", "kraken_id_parse_gui",
                     "irma_gui", "genoflu_gui"):
            self.assertTrue(declared.get(tool), f"{tool} declares no packages")
        for tool, specs in declared.items():
            for channel, name, version in specs:
                self.assertTrue(channel and name, f"{tool}: malformed spec")
                self.assertTrue(version, f"{tool}/{name} is not pinned to a version")


class UpdateScopeTests(unittest.TestCase):
    """A package update changes the env a running tool server executes from."""

    def test_a_package_target_stops_that_tools_server(self):
        names, marks = SC.update_scope("packages:vsnp_gui", {"vsnp_gui", "mlst_gui"})
        self.assertEqual(names, {"vsnp_gui"})
        self.assertEqual(marks, {"vsnp_gui"})

    def test_packages_all_covers_every_running_tool(self):
        running = {"vsnp_gui", "mlst_gui"}
        names, marks = SC.update_scope("packages:all", running)
        self.assertEqual(names, running)
        self.assertEqual(marks, {"*"})

    def test_plain_tool_targets_are_unchanged(self):
        self.assertEqual(SC.update_scope("mlst_gui", {"vsnp_gui"}),
                         ({"mlst_gui"}, {"mlst_gui"}))
        self.assertEqual(SC.update_scope("all", {"vsnp_gui"}),
                         ({"vsnp_gui"}, {"*"}))


class PackageRecordShapeTests(unittest.TestCase):
    def test_records_match_the_banner_contract(self):
        # The banner renderer is shared with tool updates; a missing key means an
        # "undefined" in the notification a user is meant to act on.
        with mock.patch.object(SC, "package_report", return_value=[{
                "tool": "vsnp_gui", "package": "vsnp3", "channel": "bioconda",
                "pinned": "3.35", "installed": "3.35", "latest": "3.36",
                "update_available": True, "env": "/x", "status": "↑ 3.36 available",
                "pin_drift": False}]):
            recs = SC.package_update_records()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        for key in ("name", "label", "installed", "latest", "update_available",
                    "kind", "tool", "package"):
            self.assertIn(key, rec)
        self.assertEqual(rec["kind"], "package")
        self.assertEqual(rec["name"], "vsnp_gui:vsnp3")

    def test_a_broken_lookup_degrades_to_no_panel(self):
        # The version panel is a nice-to-have; it must never take the dashboard
        # down. package_report imports `packages` lazily, so shadow that module
        # with one whose report() raises — the path a corrupt conda-meta or a
        # manifest edit would take.
        import sys
        import types
        broken = types.ModuleType("packages")

        def boom(*a, **k):
            raise RuntimeError("conda-meta unreadable")
        broken.report = boom
        with mock.patch.dict(sys.modules, {"packages": broken}):
            self.assertEqual(SC.package_report(), [])
            self.assertEqual(SC.package_map(use_network=False), {})
            self.assertEqual(SC.package_update_records(), [])


if __name__ == "__main__":
    unittest.main()
