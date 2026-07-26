#!/usr/bin/env python3
"""Focused regression tests for dashboard process and control-plane safety."""
import asyncio
import importlib.util
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SC = load_module("bdtools_suite_common", ROOT / "bin/lib/suite_common.py")
TL = load_module("bdtools_tool_launch", ROOT / "bin/lib/tool_launch.py")
REQ = load_module("bdtools_requirements", ROOT / "bin/lib/requirements.py")
CHECK = load_module("bdtools_check", ROOT / "bin/lib/check.py")
MANIFEST = load_module("bdtools_manifest", ROOT / "bin/lib/manifest.py")
SITE = load_module("bdtools_site_paths", ROOT / "bin/lib/site_paths.py")
try:
    APP = load_module("bdtools_dashboard_app", ROOT / "bin/ood_dashboard/app.py")
    HAS_PROXY_DEPS = True
except ModuleNotFoundError:
    APP = None
    HAS_PROXY_DEPS = False


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = 0
        self.killed = 0

    def terminate(self):
        self.terminated += 1
        self.returncode = 0

    def kill(self):
        self.killed += 1
        self.returncode = -9

    async def wait(self):
        return self.returncode


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    async def get(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.response


@unittest.skipUnless(HAS_PROXY_DEPS, "proxy dashboard dependencies are not installed")
class DashboardSafetyTests(unittest.IsolatedAsyncioTestCase):
    def make_suite(self):
        with mock.patch.object(APP.Suite, "_discover", return_value=[]):
            return APP.Suite()

    async def test_concurrent_launches_share_one_startup_task(self):
        suite = self.make_suite()
        calls = 0

        async def start(name):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return f"t/{name}/", None

        suite._start_tool = start
        results = await asyncio.gather(*(suite.launch("mlst_gui") for _ in range(12)))
        self.assertEqual(calls, 1)
        self.assertEqual(results, [("t/mlst_gui/", None)] * 12)
        self.assertEqual(suite.starting, {})

    async def test_active_job_blocks_quiesce_and_resets_gate(self):
        suite = self.make_suite()
        suite.running["mlst_gui"] = {
            "port": 12345,
            "proc": FakeProcess(),
            "log": None,
        }
        previous = APP.CLIENT
        APP.CLIENT = FakeClient(FakeResponse([
            {"id": "abc", "name": "sample-1", "status": "running"}
        ]))
        try:
            snapshot = await suite.begin_quiesce()
        finally:
            APP.CLIENT = previous
        self.assertFalse(snapshot["safe"])
        self.assertEqual(snapshot["active"][0]["tool"], "mlst_gui")
        self.assertFalse(suite.quiescing)

    async def test_unverifiable_tool_blocks_lifecycle(self):
        suite = self.make_suite()
        suite.running["irma_gui"] = {
            "port": 12346,
            "proc": FakeProcess(),
            "log": None,
        }
        previous = APP.CLIENT
        APP.CLIENT = FakeClient(error=TimeoutError("not responding"))
        try:
            snapshot = await suite.activity()
        finally:
            APP.CLIENT = previous
        self.assertFalse(snapshot["safe"])
        self.assertEqual(snapshot["errors"][0]["tool"], "irma_gui")

    async def test_quiesce_waits_for_inflight_startup_before_job_check(self):
        suite = self.make_suite()

        async def start():
            await asyncio.sleep(0.02)
            suite.running["mlst_gui"] = {
                "port": 12348,
                "proc": FakeProcess(),
                "log": None,
            }
            return "t/mlst_gui/", None

        task = asyncio.create_task(start())
        suite.starting["mlst_gui"] = task
        previous = APP.CLIENT
        APP.CLIENT = FakeClient(FakeResponse([
            {"id": "job-after-start", "name": "sample-2", "status": "running"}
        ]))
        try:
            snapshot = await suite.begin_quiesce()
        finally:
            APP.CLIENT = previous
        self.assertFalse(snapshot["safe"])
        self.assertEqual(snapshot["active"][0]["id"], "job-after-start")
        self.assertFalse(suite.quiescing)

    async def test_idle_backend_is_terminated_and_awaited(self):
        suite = self.make_suite()
        process = FakeProcess()
        suite.running["ksnp_gui"] = {"port": 12347, "proc": process, "log": None}
        await suite.stop_backends()
        self.assertEqual(process.terminated, 1)
        self.assertNotIn("ksnp_gui", suite.running)

    async def test_local_control_and_proxy_mutations_reject_cross_site_requests(self):
        import httpx
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def mutate(request):
            return JSONResponse({"ok": True})

        guarded = Starlette(routes=[
            Route("/api/mutate", mutate, methods=["POST"]),
            Route("/t/tool/api/mutate", mutate, methods=["POST"]),
        ])
        guarded.add_middleware(APP.AuthMiddleware)
        previous_local = APP.LOCAL
        APP.LOCAL = True
        try:
            transport = httpx.ASGITransport(app=guarded)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1:8080"
            ) as client:
                missing = await client.post("/api/mutate")
                allowed = await client.post(
                    "/api/mutate",
                    headers={"X-Bdtools-Control": APP.CONTROL_TOKEN},
                )
                cross_site = await client.post(
                    "/t/tool/api/mutate",
                    headers={
                        "Origin": "https://attacker.example",
                        "Sec-Fetch-Site": "cross-site",
                    },
                )
        finally:
            APP.LOCAL = previous_local
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(cross_site.status_code, 403)


class StateFileTests(unittest.TestCase):
    def test_source_override_reuses_installed_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "feature-tools"
            source_tool = source_root / "mlst_gui"
            (source_tool / "backend").mkdir(parents=True)
            bdtools_home = root / "bdtools-home"
            installed_env = bdtools_home / "checkouts/mlst_gui/env"
            (installed_env / "bin").mkdir(parents=True)
            (installed_env / "bin/python").touch()
            with mock.patch.dict(os.environ, {
                "BDTOOLS_TOOLSDIR": str(source_root),
                "BDTOOLS_HOME": str(bdtools_home),
            }, clear=False):
                with mock.patch.object(TL, "_conda_bases", return_value=[]):
                    plan = TL.resolve("mlst_gui", 8124)
        self.assertEqual(plan["dir"], str(source_tool))
        self.assertEqual(plan["env_dir"], str(installed_env))
        self.assertEqual(plan["python"], str(installed_env / "bin/python"))

    def _ksnp_worktree(self, root, *, with_vendor_in_source, with_vendor_installed):
        """A source-override tree + installed checkout, as `--tools-dir` produces.

        Vendored payloads are gitignored, so a feature worktree normally has an
        EMPTY vendor/ while the installed checkout holds the real 545 MB package."""
        source_root = root / "feature-tools"
        source_tool = source_root / "ksnp_gui"
        (source_tool / "backend").mkdir(parents=True)
        bdtools_home = root / "bdtools-home"
        installed = bdtools_home / "checkouts/ksnp_gui"
        (installed / "env/bin").mkdir(parents=True)
        (installed / "env/bin/python").touch()
        if with_vendor_in_source:
            (source_tool / "vendor/kSNP4-bin").mkdir(parents=True)
        if with_vendor_installed:
            (installed / "vendor/kSNP4-bin").mkdir(parents=True)
        return source_root, source_tool, bdtools_home, installed

    def test_missing_vendor_dir_falls_back_to_installed_checkout(self):
        """A feature worktree must borrow kSNP4 rather than emit a dead PATH entry.

        Regression: an empty worktree vendor/ was prepended to PATH unchecked, so a
        complete kSNP4 install surfaced as `command not found: kSNP4` (rc 127)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root, source_tool, bdtools_home, installed = self._ksnp_worktree(
                root, with_vendor_in_source=False, with_vendor_installed=True)
            with mock.patch.dict(os.environ, {
                "BDTOOLS_TOOLSDIR": str(source_root),
                "BDTOOLS_HOME": str(bdtools_home),
            }, clear=False):
                with mock.patch.object(TL, "_conda_bases", return_value=[]):
                    plan = TL.resolve("ksnp_gui", 8125)
        prepend = plan["env_overrides"]["PATH_PREPEND"]
        self.assertIn(str(installed / "vendor/kSNP4-bin"), prepend)
        self.assertNotIn(str(source_tool / "vendor/kSNP4-bin"), prepend)
        self.assertEqual(plan["warnings"], [])
        self.assertNotIn("BDTOOLS_MISSING_ASSETS", plan["env_overrides"])

    def test_absent_vendor_dir_warns_and_never_enters_path(self):
        """With no candidate anywhere, warn loudly and keep PATH clean."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root, source_tool, bdtools_home, _installed = self._ksnp_worktree(
                root, with_vendor_in_source=False, with_vendor_installed=False)
            with mock.patch.dict(os.environ, {
                "BDTOOLS_TOOLSDIR": str(source_root),
                "BDTOOLS_HOME": str(bdtools_home),
                "BDTOOLS_VENDOR_ROOT": str(root / "no-such-vendor-root"),
            }, clear=False):
                with mock.patch.object(TL, "_conda_bases", return_value=[]):
                    plan = TL.resolve("ksnp_gui", 8126)
        self.assertNotIn("kSNP4-bin", plan["env_overrides"]["PATH_PREPEND"])
        self.assertEqual(
            plan["env_overrides"]["BDTOOLS_MISSING_ASSETS"], "vendor/kSNP4-bin")
        self.assertEqual(len(plan["warnings"]), 1)
        self.assertIn("vendor/kSNP4-bin not found", plan["warnings"][0])
        # The launch log is where anyone will actually look for this.
        self.assertIn("WARNING:", TL.log_header(plan))

    def test_source_tree_vendor_wins_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root, source_tool, bdtools_home, installed = self._ksnp_worktree(
                root, with_vendor_in_source=True, with_vendor_installed=True)
            with mock.patch.dict(os.environ, {
                "BDTOOLS_TOOLSDIR": str(source_root),
                "BDTOOLS_HOME": str(bdtools_home),
            }, clear=False):
                with mock.patch.object(TL, "_conda_bases", return_value=[]):
                    plan = TL.resolve("ksnp_gui", 8127)
        prepend = plan["env_overrides"]["PATH_PREPEND"]
        self.assertIn(str(source_tool / "vendor/kSNP4-bin"), prepend)
        self.assertNotIn(str(installed / "vendor/kSNP4-bin"), prepend)

    def test_dangling_vendor_symlink_is_rejected(self):
        """vendor/kSNP4-bin IS a symlink in a real install; a broken one is not usable."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root, source_tool, bdtools_home, installed = self._ksnp_worktree(
                root, with_vendor_in_source=False, with_vendor_installed=True)
            (source_tool / "vendor").mkdir(parents=True)
            (source_tool / "vendor/kSNP4-bin").symlink_to(root / "gone")
            with mock.patch.dict(os.environ, {
                "BDTOOLS_TOOLSDIR": str(source_root),
                "BDTOOLS_HOME": str(bdtools_home),
            }, clear=False):
                with mock.patch.object(TL, "_conda_bases", return_value=[]):
                    plan = TL.resolve("ksnp_gui", 8128)
        self.assertIn(str(installed / "vendor/kSNP4-bin"),
                      plan["env_overrides"]["PATH_PREPEND"])

    def test_doctor_reports_missing_ksnp_binaries(self):
        """Doctor used to report ksnp_gui green while the pipeline could not run:
        kSNP4 is not a conda package, so an <env>/bin + PATH search never saw it."""
        spec = REQ.for_tool("ksnp_gui")
        self.assertIn("kSNP4", spec["binaries"])
        self.assertIn("Kchooser4", spec["binaries"])
        self.assertIn("MakeKSNP4infile", spec["binaries"])
        self.assertEqual(spec["asset_dirs"], ["vendor/kSNP4-bin"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_py = root / "env/bin/python"
            env_py.parent.mkdir(parents=True)
            env_py.touch()
            tool_dir = root / "ksnp_gui"
            tool_dir.mkdir()
            with mock.patch.dict(os.environ, {
                "BDTOOLS_HOME": str(root / "bdtools-home"),
                "BDTOOLS_VENDOR_ROOT": str(root / "no-such-vendor-root"),
            }, clear=False):
                with mock.patch.object(CHECK, "check_modules", return_value=[]):
                    status, _lines, issues, _notes = CHECK.run_checks(
                        "ksnp_gui", str(env_py), "env", tool_dir=str(tool_dir))
        self.assertEqual(status, "issues")
        labels = " | ".join(i["label"] for i in issues)
        self.assertIn("vendored files missing: vendor/kSNP4-bin", labels)
        self.assertIn("kSNP4", labels)

    # ---- the appearance contract, checked against SHIPPED artifacts ----
    #
    # The suite claimed for a while that "all nine GUI headers offer Light, Dark and
    # System modes" while every pinned tag shipped an unthemed bundle: the tool-side
    # work sat on an unmerged branch, and the only test asserted strings in the two
    # umbrella page templates — nothing tool-side. These check what a user actually
    # receives.

    _FIRST_THEMED_TAG = {
        "vsnp_gui": (0, 4, 34), "amr_plus_gui": (0, 2, 7), "irma_gui": (0, 2, 7),
        "genoflu_gui": (0, 2, 7), "mlst_gui": (0, 2, 6), "kraken_id_parse_gui": (0, 1, 10),
        "ksnp_gui": (0, 2, 6), "ncbi_submit_gui": (0, 1, 8), "mhc_gui": (0, 1, 6),
    }

    @staticmethod
    def _tag_tuple(v):
        m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", str(v or ""))
        return tuple(int(g) for g in m.groups()) if m else None

    def test_manifest_pins_are_themed(self):
        """A pin rollback below the first themed tag would silently un-theme a tool."""
        _suite, tools = MANIFEST.parse(str(ROOT / "tools.yml"))
        seen = 0
        for t in tools:
            floor = self._FIRST_THEMED_TAG.get(t.get("name"))
            if floor is None:
                continue
            pinned = self._tag_tuple(t.get("version"))
            self.assertIsNotNone(
                pinned, f"{t['name']} is pinned to {t.get('version')!r}, not a release tag")
            self.assertGreaterEqual(
                pinned, floor,
                f"{t['name']} is pinned below its first themed release "
                f"— that ships a light-only GUI")
            seen += 1
        self.assertEqual(seen, len(self._FIRST_THEMED_TAG), "a tool went missing from tools.yml")

    def test_installed_tool_dists_carry_theme_bootstrap(self):
        """What's on disk must actually contain the theme, not just the source tree.

        Backends serve the PREBUILT frontend/dist, so a themed src with a stale dist
        is invisible. Skipped when no checkouts exist so the suite still runs bare."""
        checkouts = Path(
            os.environ.get("BDTOOLS_HOME") or Path.home() / ".local/share/bdtools"
        ) / "checkouts"
        if not checkouts.is_dir():
            self.skipTest("no installed checkouts on this machine")
        checked = []
        for name in sorted(self._FIRST_THEMED_TAG):
            index = checkouts / name / "frontend/dist/index.html"
            if not index.is_file():
                continue
            html = index.read_text(encoding="utf-8", errors="replace")
            self.assertIn("bdtools-theme", html,
                          f"{name}: dist/index.html has no pre-paint theme bootstrap "
                          f"(the GUI will flash light, or stay light)")
            self.assertIn("dataset.theme", html,
                          f"{name}: dist/index.html never sets data-theme on the root")
            bundles = list((checkouts / name / "frontend/dist/assets").glob("index-*.js"))
            self.assertTrue(bundles, f"{name}: dist has no entry bundle")
            self.assertTrue(
                any("bdtools-theme" in b.read_text(encoding="utf-8", errors="replace")
                    for b in bundles),
                f"{name}: no entry bundle references the bdtools-theme key, so its "
                f"header control cannot persist a choice")
            checked.append(name)
        if not checked:
            self.skipTest("no built frontends found in the installed checkouts")

    def test_tool_backends_send_no_store_for_the_entry_document(self):
        """Hashed assets may be cached forever; index.html must not be.

        Otherwise an updated bundle never loads — Safari in particular will keep an
        open GUI tab on the previous entry document indefinitely."""
        tools_root = Path(
            os.environ.get("BDTOOLS_HOME") or Path.home() / ".local/share/bdtools"
        ) / "checkouts"
        if not tools_root.is_dir():
            self.skipTest("no installed checkouts on this machine")
        checked = []
        for name in sorted(self._FIRST_THEMED_TAG):
            main_py = tools_root / name / "backend/app/main.py"
            if not main_py.is_file():
                continue
            src = main_py.read_text(encoding="utf-8", errors="replace")
            self.assertIn("no-store", src,
                          f"{name}: backend never sends no-store, so an updated "
                          f"frontend bundle may never be picked up")
            checked.append(name)
        if not checked:
            self.skipTest("no tool backends found in the installed checkouts")

    def test_installed_tool_backends_import(self):
        """Every installed backend must import — uvicorn does nothing else first.

        A route decorated above `app = FastAPI(...)` raises NameError at import, so
        the tool exits the instant it launches and the dashboard can only report
        "the tool exited early". Nothing else in the suite catches that: the module
        compiles fine, and the failure only appears at run time. Imports each
        backend in its OWN env python, since deps differ per tool."""
        checkouts = Path(
            os.environ.get("BDTOOLS_HOME") or Path.home() / ".local/share/bdtools"
        ) / "checkouts"
        if not checkouts.is_dir():
            self.skipTest("no installed checkouts on this machine")
        failures, checked = [], []
        for name in sorted(self._FIRST_THEMED_TAG):
            backend = checkouts / name / "backend"
            if not (backend / "app/main.py").is_file():
                continue
            try:
                plan = TL.resolve(name, 0)
            except Exception:
                continue  # not installed / no env — other tests cover that
            proc = subprocess.run(
                [plan["python"], "-c", "import app.main"],
                cwd=str(backend), env={**plan["env"], "PYTHONPATH": plan["env"].get("PYTHONPATH", "")},
                capture_output=True, text=True, timeout=180)
            checked.append(name)
            if proc.returncode != 0:
                last = (proc.stderr or "").strip().splitlines()[-1:] or ["(no output)"]
                failures.append(f"{name}: {last[0]}")
        if not checked:
            self.skipTest("no importable tool backends found")
        self.assertEqual(failures, [], "tool backends failed to import:\n  " + "\n  ".join(failures))

    # ---- hard-coded site paths: a ratchet, not a clean-room rule ----
    #
    # The suite must run identically on macOS, WSL, plain Linux and OOD, so a
    # literal "/srv/kapurlab/..." or "/home/<someone>/..." in code is a latent
    # portability bug. Two have already bitten: amr_plus_gui resolved its sibling
    # MLST runner as /srv/kapurlab/tools/mlst_gui, so the organism cross-check
    # silently never ran off the lab server; and mhc_gui probed /home/vxk1 paths at
    # import time and crashed for every other account.
    #
    # There is existing debt, so these are ceilings that may only go DOWN. The
    # legitimate remaining uses are last-resort fallbacks *after* an env var, the
    # tool's own location, and user config have been tried.
    _SITE_PATH_BUDGET = {
        # amr_plus_gui is the reference implementation and is at ZERO: it reads
        # BDTOOLS_SHARED_PROJECTS_ROOT / BDTOOLS_DB_ROOT / BDTOOLS_TOOLS_ROOT,
        # which the launcher resolves from the machine's recorded site config
        # (bin/lib/site_paths.py). Every other tool should land here too.
        "amr_plus_gui": 0,
        "vsnp_gui": 24, "mlst_gui": 2, "kraken_id_parse_gui": 4,
        "ksnp_gui": 2, "genoflu_gui": 3, "irma_gui": 3, "ncbi_submit_gui": 2,
        "mhc_gui": 11,
    }
    # Paths under a *named person's* home are never a defensible fallback — that
    # account may not exist, and its home is usually unreadable to everyone else.
    _OTHER_HOME_BUDGET = {"mhc_gui": 6}

    @staticmethod
    def _count_site_paths(tool_dir):
        import itertools
        site = re.compile(r'"(?:/srv|/home)/[A-Za-z0-9_./-]+"')
        other_home = re.compile(r'"/home/[A-Za-z0-9_./-]+"')
        n_site = n_home = 0
        files = itertools.chain((tool_dir / "backend/app").glob("*.py"),
                               (tool_dir / "bin").glob("*.py"))
        for f in files:
            try:
                src = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            n_site += len(site.findall(src))
            n_home += len(other_home.findall(src))
        return n_site, n_home

    def test_hardcoded_site_paths_do_not_grow(self):
        checkouts = Path(
            os.environ.get("BDTOOLS_HOME") or Path.home() / ".local/share/bdtools"
        ) / "checkouts"
        if not checkouts.is_dir():
            self.skipTest("no installed checkouts on this machine")
        over, checked = [], []
        for name, budget in sorted(self._SITE_PATH_BUDGET.items()):
            tool_dir = checkouts / name
            if not (tool_dir / "backend/app").is_dir():
                continue
            checked.append(name)
            n_site, n_home = self._count_site_paths(tool_dir)
            if n_site > budget:
                over.append(f"{name}: {n_site} literal site paths > budget {budget} "
                            f"— resolve it (env var / own location / user config) "
                            f"instead of hard-coding, or lower the budget knowingly")
            home_budget = self._OTHER_HOME_BUDGET.get(name, 0)
            if n_home > home_budget:
                over.append(f"{name}: {n_home} paths under a named person's home > "
                            f"budget {home_budget} — that account may not exist and "
                            f"its home is usually unreadable to everyone else")
        if not checked:
            self.skipTest("no tool checkouts to scan")
        self.assertEqual(over, [], "hard-coded path budgets exceeded:\n  " + "\n  ".join(over))

    def test_site_paths_module_contains_no_site_paths(self):
        """The resolver itself must not smuggle in the layout it exists to avoid."""
        src = (ROOT / "bin/lib/site_paths.py").read_text(encoding="utf-8")
        code = "\n".join(
            l for l in src.splitlines()
            if not l.lstrip().startswith("#") and "/srv/kapurlab" not in l or "=" in l.split("#")[0]
        )
        offenders = [l.strip() for l in src.splitlines()
                     if re.search(r'["\'](?:/srv|/home)/[A-Za-z0-9_.-]+', l)]
        self.assertEqual(offenders, [],
                         "site_paths.py must resolve paths, never contain them")

    def test_site_paths_degrade_honestly_when_unconfigured(self):
        """An unconfigured machine (a fresh Mac/WSL) must get None, not a guess."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = {k: v for k, v in os.environ.items()
                     if not k.startswith(("BDTOOLS_", "XDG_"))}
            clean["BDTOOLS_HOME"] = str(root / "bdtools")
            with mock.patch.dict(os.environ, clean, clear=True):
                self.assertIsNone(
                    SITE.shared_projects_root(str(root)),
                    "an unconfigured machine must report NO shared root, not a guessed one")
                # never Path("") — that is Path("."), the working directory
                self.assertNotEqual(str(SITE.shared_projects_root(str(root)) or "x"), ".")
                self.assertEqual(SITE.db_root(str(root)), Path.home() / "databases")
                env = SITE.as_env(str(root))
                self.assertNotIn(SITE.ENV_SHARED_PROJECTS_ROOT, env,
                                 "an unresolved value must be left unset, not set to a guess")

    def test_site_paths_honour_configuration(self):
        """A site supplies values through config; code supplies none."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "bdtools"
            SITE.write_site_file(
                {"SHARED_PROJECTS_ROOT": "/mnt/lab/projects", "DB_ROOT": "/mnt/lab/db"},
                path=home / "site.conf")
            with mock.patch.dict(os.environ, {"BDTOOLS_HOME": str(home)}, clear=False):
                for var in (SITE.ENV_SHARED_PROJECTS_ROOT, SITE.ENV_DB_ROOT):
                    os.environ.pop(var, None)
                self.assertEqual(str(SITE.shared_projects_root()), "/mnt/lab/projects")
                self.assertEqual(str(SITE.db_root()), "/mnt/lab/db")
            # an explicitly empty env value DISABLES the shared root
            with mock.patch.dict(os.environ, {
                "BDTOOLS_HOME": str(home), SITE.ENV_SHARED_PROJECTS_ROOT: ""
            }, clear=False):
                self.assertIsNone(SITE.shared_projects_root())

    def test_launcher_tells_tools_where_the_suite_lives(self):
        """Tools must be able to find a sibling without assuming a site layout."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools_root = root / "checkouts"
            (tools_root / "amr_plus_gui/backend").mkdir(parents=True)
            env_bin = tools_root / "amr_plus_gui/env/bin"
            env_bin.mkdir(parents=True)
            (env_bin / "python").touch()
            with mock.patch.dict(os.environ, {"BDTOOLS_HOME": str(root)}, clear=False):
                with mock.patch.object(TL, "_conda_bases", return_value=[]):
                    plan = TL.resolve("amr_plus_gui", 8129)
        self.assertEqual(plan["env"]["BDTOOLS_TOOLS_ROOT"], str(tools_root))
        self.assertEqual(plan["env"]["BDTOOLS_HOME"], str(root))
        # and it must survive into the reproduce command, or a terminal re-run
        # resolves siblings differently from the dashboard run it claims to copy
        self.assertIn("BDTOOLS_TOOLS_ROOT=", TL.reproduce_command(plan))

    def test_state_file_is_private_and_pid_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dashboard-state.json"
            SC.write_dashboard_state(str(path), 8123, "secret-token")
            payload = json.loads(path.read_text())
            self.assertEqual(payload["pid"], os.getpid())
            self.assertEqual(payload["port"], 8123)
            self.assertEqual(payload["control_token"], "secret-token")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            SC.remove_dashboard_state(str(path))
            self.assertFalse(path.exists())

    def test_page_uses_custom_control_header(self):
        dashboard = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
        launcher = (ROOT / "bin/bdtools").read_text(encoding="utf-8")
        self.assertIn("X-Bdtools-Control", dashboard)
        self.assertIn("controlFetch('./api/restart'", dashboard)
        self.assertIn("--tools-dir", launcher)
        self.assertIn("export BDTOOLS_TOOLSDIR", launcher)
        self.assertIn(".bdtools-tools-dir", launcher)
        self.assertNotIn('pkill -f "uvicorn app.main:app"', (
            ROOT / "bin/bdtools"
        ).read_text(encoding="utf-8"))

    @unittest.skipUnless(HAS_PROXY_DEPS, "proxy dashboard dependencies are not installed")
    def test_dashboard_pages_expose_persisted_accessible_themes(self):
        dashboard = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
        self.assertIn("bdtools-theme", dashboard)
        self.assertIn('data-theme-choice="dark"', dashboard)
        self.assertIn('aria-label="Appearance"', dashboard)
        rendered_ood = APP.SIMPLE_PAGE.format(who="Signed in.", host="compute-1")
        self.assertIn("bdtools-theme", rendered_ood)
        self.assertIn('data-theme-choice="light"', rendered_ood)
        self.assertIn('html[data-theme="dark"]', rendered_ood)

    def test_suite_self_update_refuses_dirty_checkout(self):
        manager = SC.UpdateManager()
        manager.job = {
            "running": True, "done": False, "ok": None,
            "target": "bdtools", "log": [],
        }
        with mock.patch.object(
            SC.subprocess, "run",
            return_value=SimpleNamespace(stdout=" M tools.yml\n"),
        ):
            with mock.patch.object(SC.subprocess, "Popen") as popen:
                with mock.patch.object(manager, "check_async"):
                    manager._run("bdtools")
        popen.assert_not_called()
        status = manager.job_status()
        self.assertTrue(status["done"])
        self.assertFalse(status["ok"])
        self.assertTrue(any("refusing to pull" in line for line in status["log"]))


if __name__ == "__main__":
    unittest.main()
