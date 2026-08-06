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
                 mock.patch.object(PKG, "unsatisfiable_here", return_value={}), \
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
             mock.patch.object(PKG, "unsatisfiable_here", return_value={}), \
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

    def test_held_packages_are_declared_for_a_tool_that_declares_them(self):
        # Every held name must also be a declared package, or the hold is a typo
        # that silently does nothing — and the update it was meant to suppress
        # comes back.
        declared = PKG.declared()
        for tool, names in PKG.held().items():
            for name in names:
                self.assertIn(name, {n for _c, n, _v in declared.get(tool, [])},
                              f"{tool}: held package {name!r} is not declared")

    def test_a_held_package_is_shown_but_not_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "conda-meta"
            meta.mkdir()
            (meta / "vsnp3-3.35-hdfd78af_0.json").touch()
            with mock.patch.object(PKG, "env_dir_for", return_value=tmp), \
                 mock.patch.object(PKG, "latest_version", return_value="3.36"), \
                 mock.patch.object(PKG, "unsatisfiable_here", return_value={}), \
                 mock.patch.object(PKG, "held",
                                   return_value={"vsnp_gui": {"vsnp3"}}), \
                 mock.patch.object(PKG, "_save_cache", lambda cache: None):
                rec = next(r for r in PKG.report(["vsnp_gui"], use_network=True)
                           if r["package"] == "vsnp3")
        # The newer version is still reported — held is not hidden.
        self.assertEqual(rec["latest"], "3.36")
        self.assertTrue(rec["held"])
        # ...but not offered, or the banner nags forever and the update always fails.
        self.assertFalse(rec["update_available"])
        self.assertIn("held", rec["status"])
        # The reason has to travel on the record: once nothing offers the package,
        # the card badge and the CLI line are the only places left to explain it.
        self.assertIn("3.36", rec["held_reason"])
        self.assertIn("tools.yml", rec["held_reason"])

    def test_a_hold_expires_when_the_env_catches_up(self):
        # A manifest hold is by NAME and forever; the env is not. Once the installed
        # version has reached the channel's newest, nothing is being held back — and
        # reporting "held" there badged a card and padded the dashboard's "N held"
        # summary with a package that had nothing newer behind it.
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "conda-meta"
            meta.mkdir()
            (meta / "vsnp3-3.36-hdfd78af_0.json").touch()
            with mock.patch.object(PKG, "env_dir_for", return_value=tmp), \
                 mock.patch.object(PKG, "latest_version", return_value="3.36"), \
                 mock.patch.object(PKG, "unsatisfiable_here", return_value={}), \
                 mock.patch.object(PKG, "held",
                                   return_value={"vsnp_gui": {"vsnp3"}}), \
                 mock.patch.object(PKG, "_save_cache", lambda cache: None):
                rec = next(r for r in PKG.report(["vsnp_gui"], use_network=True)
                           if r["package"] == "vsnp3")
        self.assertFalse(rec["held"])
        self.assertFalse(rec["update_available"])
        self.assertEqual(rec["status"], "up to date")


class UnsatisfiableRecordTests(unittest.TestCase):
    """A version this machine has PROVEN it cannot install must stop being offered.

    The live case: mlst 2.34+ is noarch, so it looks installable everywhere, but it
    depends on libxcrypt1 — which has no macOS build. A Mac therefore sees "2.35.0
    available", fails the solve, and is offered it again on every check. The record
    is per platform because a shared manifest cannot be: the same version installs
    fine on Linux.
    """

    def test_a_recorded_version_is_held_not_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "conda-meta"
            meta.mkdir()
            (meta / "mlst-2.33.1-hdfd78af_0.json").touch()
            with mock.patch.object(PKG, "env_dir_for", return_value=tmp), \
                 mock.patch.object(PKG, "latest_version", return_value="2.35.0"), \
                 mock.patch.object(PKG, "_save_cache", lambda cache: None), \
                 mock.patch.object(PKG, "held", return_value={}), \
                 mock.patch.object(PKG, "unsatisfiable_here", return_value={
                     "mlst_gui/mlst": {"version": "2.35.0",
                                       "reason": "nothing provides libxcrypt1"}}):
                rec = next(r for r in PKG.report(["mlst_gui"], use_network=True)
                           if r["package"] == "mlst")
        self.assertEqual(rec["installed"], "2.33.1")
        self.assertEqual(rec["latest"], "2.35.0")   # still reported, not hidden
        self.assertTrue(rec["held"])
        self.assertFalse(rec["update_available"])
        self.assertIn("cannot be installed", rec["status"])
        # The recorded solver reason, not a pointer to tools.yml — nothing was ever
        # written there about this one, so sending anyone to look was a dead end.
        self.assertIn("libxcrypt1", rec["held_reason"])

    def test_a_hold_from_an_env_conflict_names_the_rebuild_that_would_lift_it(self):
        # The amr_plus case: the pinned version is right and the EXISTING env cannot
        # take it, so rebuilding from the spec is the way out and belongs on the
        # record. A version that does not exist for this OS must NOT get that advice.
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "conda-meta"
            meta.mkdir()
            (meta / "ncbi-amrfinderplus-3.12.8-h1234_0.json").touch()
            def rec_for(reason):
                with mock.patch.object(PKG, "env_dir_for", return_value=tmp), \
                     mock.patch.object(PKG, "latest_version", return_value="4.2.7"), \
                     mock.patch.object(PKG, "_save_cache", lambda cache: None), \
                     mock.patch.object(PKG, "unsatisfiable_here", return_value={
                         "amr_plus_gui/ncbi-amrfinderplus": {
                             "version": "4.2.7", "reason": reason}}):
                    return next(r for r in PKG.report(["amr_plus_gui"],
                                                      use_network=True)
                                if r["package"] == "ncbi-amrfinderplus")
            conflict = rec_for("requires perl >=5.32.1, but none of the providers "
                               "can be installed")
            elsewhere = rec_for("not installable on osx-64 osx-arm64")
        self.assertEqual(conflict["held_fix"],
                         "bdtools install amr_plus_gui --rebuild")
        self.assertEqual(elsewhere["held_fix"], "",
                         "a rebuild cannot conjure a build that does not exist")

    def test_a_record_for_a_different_version_does_not_hold_the_new_one(self):
        # Keyed by version so a NEWER release is tried again — the record suppresses
        # one known-bad answer, it does not abandon the package.
        # `held` is stubbed out because this is about the RECORD: mlst also carries a
        # manifest hold (tools.yml: mlst 2.34+ can never install on macOS), which
        # would suppress the new version for a different and permanent reason.
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "conda-meta"
            meta.mkdir()
            (meta / "mlst-2.33.1-hdfd78af_0.json").touch()
            with mock.patch.object(PKG, "env_dir_for", return_value=tmp), \
                 mock.patch.object(PKG, "latest_version", return_value="2.36.0"), \
                 mock.patch.object(PKG, "_save_cache", lambda cache: None), \
                 mock.patch.object(PKG, "held", return_value={}), \
                 mock.patch.object(PKG, "unsatisfiable_here", return_value={
                     "mlst_gui/mlst": {"version": "2.35.0", "reason": "x"}}):
                rec = next(r for r in PKG.report(["mlst_gui"], use_network=True)
                           if r["package"] == "mlst")
        self.assertTrue(rec["update_available"])
        self.assertFalse(rec["held"])

    def test_record_round_trips_and_is_keyed_by_platform(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"BDTOOLS_HOME": home}, clear=False):
                PKG.record_unsatisfiable("mlst_gui", "mlst", "2.35.0", "no libxcrypt1")
                got = PKG.unsatisfiable_here()
                self.assertEqual(got["mlst_gui/mlst"]["version"], "2.35.0")
                self.assertIn("libxcrypt1", got["mlst_gui/mlst"]["reason"])
                raw = json.load(open(Path(home) / "cache/unsatisfiable.json"))
                self.assertEqual(list(raw), [PKG._platform_key()])
                # Another platform's record must not leak into this one.
                raw["some-other-arch"] = {"mlst_gui/mlst": {"version": "9.9"}}
                json.dump(raw, open(Path(home) / "cache/unsatisfiable.json", "w"))
                self.assertEqual(PKG.unsatisfiable_here()["mlst_gui/mlst"]["version"],
                                 "2.35.0")

    def test_a_missing_or_corrupt_record_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"BDTOOLS_HOME": home}, clear=False):
                self.assertEqual(PKG.unsatisfiable_here(), {})
                cache = Path(home) / "cache"
                cache.mkdir(parents=True)
                (cache / "unsatisfiable.json").write_text("{not json")
                self.assertEqual(PKG.unsatisfiable_here(), {})


class PinStabilityTests(unittest.TestCase):
    def test_update_packages_does_not_mirror_local_versions_into_the_pin(self):
        # A pin is a cross-platform decision. Rewriting it to match whatever is
        # installed here made `update-packages` overwrite it on every run — and since
        # the right answer differs per platform (mlst 2.35.0 installs on Linux and
        # cannot exist on macOS), two machines would fight over tools.yml forever.
        script = (ROOT / "bin/update-packages.sh").read_text(encoding="utf-8")
        already = script.split("is already ${installed}", 1)[1].split("continue", 1)[0]
        self.assertNotIn("_bump_pin", already,
                         "the 'already at this version' path must not rewrite the pin")
        # A pin still moves when a package is genuinely installed by this command.
        self.assertIn("_bump_pin", script)


class CrossPlatformGateTests(unittest.TestCase):
    """Cross-platform consistency outranks being current.

    A version that installs on Linux and cannot install on macOS must not be applied
    anywhere: it does not make the lab more current, it makes two machines disagree
    about what produced a result. A lab running one older version everywhere is in a
    better position than one running the newest version in half the building.
    """

    def setUp(self):
        self.script = (ROOT / "bin/update-packages.sh").read_text(encoding="utf-8")

    def test_the_gate_runs_before_any_install(self):
        gate = self.script.index("_unavailable_platforms")
        install = self.script.index("_conda_install_set")
        self.assertLess(gate, install, "the platform gate must precede the install")

    def test_every_deployed_platform_is_checked_by_default(self):
        default = self.script.split('PLATFORMS="', 1)[1].split('"', 1)[0]
        for plat in ("linux-64", "osx-64", "osx-arm64"):
            self.assertIn(plat, default)

    def test_a_blocked_platform_is_not_an_error(self):
        body = self.script[self.script.index("badplats"):
                           self.script.index("installable on ${PLATFORMS}")]
        self.assertIn("BLOCKED+=", body)
        self.assertNotIn("FAILED+=", body)

    def test_the_override_exists_and_is_off_by_default(self):
        self.assertIn("--local-only", self.script)
        self.assertIn("LOCAL_ONLY=0", self.script)
        # Gated unless explicitly overridden — not the other way round.
        self.assertIn("if [[ ${LOCAL_ONLY} -eq 0 ]]", self.script)

    def test_foreign_platform_solves_set_the_osx_override(self):
        # Without CONDA_OVERRIDE_OSX a foreign solve fails on a missing __osx virtual
        # package, which would make every macOS check a false negative and block
        # every update.
        helper = self.script[self.script.index("_unavailable_platforms() {"):
                             self.script.index("_tool_pyver()")]
        self.assertIn("CONDA_OVERRIDE_OSX", helper)


class PinPlatformTests(unittest.TestCase):
    """Versions verified NOT to exist on every deployed platform.

    Offline guard against re-pinning a known-bad version. It rejects only versions
    that have been checked and found impossible somewhere, so a legitimate future
    bump (say an mlst that drops the libxcrypt1 dependency) still passes. The real
    gate is a solve per platform, which needs the network and minutes:

        bin/bdtools update-packages all --check-pins

    Extend this map whenever that gate finds another one.
    """

    KNOWN_UNAVAILABLE = {
        # noarch, but depends on libxcrypt1 — which has no macOS build at all.
        "mlst": {"2.34.0", "2.35.0"},
    }

    def test_no_pin_uses_a_version_known_to_be_unavailable_somewhere(self):
        for tool, specs in PKG.declared().items():
            for _channel, name, version in specs:
                bad = self.KNOWN_UNAVAILABLE.get(name, set())
                self.assertNotIn(
                    version, bad,
                    f"{tool}: {name} {version} cannot be installed on every "
                    f"platform the lab deploys to (run --check-pins)")

    def test_the_pin_gate_exists_and_is_documented(self):
        script = (ROOT / "bin/update-packages.sh").read_text(encoding="utf-8")
        self.assertIn("--check-pins", script)
        self.assertIn("CONDA_OVERRIDE_OSX", script,
                      "foreign-platform solves need this or they fail on __osx")
        self.assertIn("osx-arm64", script, "Apple Silicon must be a checked target")


class SeverityTests(unittest.TestCase):
    """"Cannot be done" is not "went wrong"."""

    def setUp(self):
        self.script = (ROOT / "bin/update-packages.sh").read_text(encoding="utf-8")

    def test_an_unsolvable_set_is_blocked_not_failed(self):
        block = self.script[self.script.index("local _blockwhy="):
                            self.script.index("the set solves")]
        self.assertIn("BLOCKED+=", block)
        self.assertNotIn("FAILED+=", block,
                         "an unsatisfiable solve must not be reported as a failure")

    def test_real_breakage_still_fails(self):
        # An install that dies after its solve succeeded, or patches that do not
        # re-apply, must still exit non-zero — that is a human's problem.
        for marker in ("conda install failed after a successful solve",
                       "PATCHES DID NOT RE-APPLY"):
            idx = self.script.index(marker)
            self.assertIn("FAILED+=", self.script[max(0, idx - 200):idx + 50])

    def test_blocked_alone_exits_zero(self):
        tail = self.script[self.script.index("if [[ ${#BLOCKED[@]} -gt 0 ]]"):]
        self.assertIn("This is not a failure", tail)
        self.assertTrue(tail.rstrip().endswith("exit 0"))


class BannerOrderTests(unittest.TestCase):
    """The three update kinds must be offered in the order they have to be run.

    bdtools first: `bdtools update <tool>` rewrites the pins in tools.yml, and the
    later bdtools `git pull --ff-only` discards that drift to get a clean tree, so
    tools-first silently loses the pin record — and a tool rebuild should run under
    the new install scripts anyway. Conda packages last: a bdtools pull can change
    the pins they work from. The banner encodes that order left to right, so a
    reordering here is a behaviour change, not a cosmetic one.
    """

    def setUp(self):
        page = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
        self.render = page.split("function renderUpdates", 1)[1].split(
            "// Poll the cached result", 1)[0]

    def test_groups_are_declared_suite_then_tool_then_package(self):
        order = [self.render.index(f"key: '{k}'") for k in ("suite", "tool", "package")]
        self.assertEqual(order, sorted(order),
                         "update groups are no longer declared in run order")

    def test_each_group_maps_to_its_own_apply_target(self):
        for target in ("'bdtools'", "'all'", "'packages:all'"):
            self.assertIn(f"target: {target}", self.render)

    def test_steps_are_numbered_over_the_groups_present(self):
        # Numbered 1..N over what is on screen, so left-to-right always reads as the
        # run order with no gaps when only some kinds have updates.
        self.assertIn("groups = [", self.render)
        self.assertIn(".filter(g=>g.items.length)", self.render)
        self.assertIn("const n = idx + 1", self.render)

    def test_the_conda_button_says_conda(self):
        # The question this answers: which button updates vsnp3 3.35 -> 3.36?
        self.assertIn("'Update conda packages'", self.render)


class PackageLabelTests(unittest.TestCase):
    def test_a_package_item_names_its_tool_first(self):
        with mock.patch.object(SC, "package_report", return_value=[{
                "tool": "vsnp_gui", "package": "vsnp3", "channel": "bioconda",
                "pinned": "3.35", "installed": "3.35", "latest": "3.36",
                "update_available": True, "env": "/x", "status": "", "held": False}]):
            rec = SC.package_update_records()[0]
        # "vSNP3 — vsnp3", not "vsnp3 (in vSNP3)": which tool comes first, and the
        # tool name is not repeated as though it were the package's container.
        self.assertEqual(rec["label"], "vSNP3 — vsnp3")
        self.assertEqual(rec["tool_label"], "vSNP3")
        # "conda" belongs to the UI's kind tag, not the label — it was in both.
        self.assertNotIn("conda", rec["label"])


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
