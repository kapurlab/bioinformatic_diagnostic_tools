#!/usr/bin/env python3
"""Focused regression tests for dashboard process and control-plane safety."""
import asyncio
import importlib.util
import json
import os
import stat
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
