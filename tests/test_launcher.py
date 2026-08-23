#!/usr/bin/env python3
"""Regression tests for `bdtools make-launcher` (bin/make-launcher.sh).

The launcher is the one code path in the suite that a user reaches with a
double-click and no terminal, so its failure mode is silence. These tests build
real bundles into a throwaway HOME and assert the properties that make it work
where "Open Dashboard.command" did not:

  * the install path is absolute inside the generated script, so the launcher
    keeps working when it is copied to the Desktop or the Dock,
  * the macOS bundle is structurally something Finder will launch and draw an
    icon for (parseable Info.plist, executable stub, valid .icns),
  * the dashboard is started detached, so quitting the launcher — or the app —
    cannot take a running analysis with it,
  * a failure to come up is reported to the user, not just dropped.

OpenUrlWslTests covers the same silent-failure family one step later: the
launch worked, but the browser the user is waiting for never opens.

No macOS needed: a .app is a directory, and BDTOOLS_LAUNCHER_PLATFORM selects the
target explicitly for exactly this reason.
"""
import os
import plistlib
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BDTOOLS = ROOT / "bin/bdtools"
COMMON = ROOT / "bin/lib/common.sh"


def make_launcher(home, platform, *args):
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "BDTOOLS_HOME": str(Path(home) / ".local/share/bdtools"),
        "BDTOOLS_LAUNCHER_PLATFORM": platform,
    })
    env.pop("DRY_RUN", None)
    return subprocess.run([str(BDTOOLS), "make-launcher", *args],
                          capture_output=True, text=True, env=env, cwd=str(ROOT))


class MacBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "Desktop").mkdir()
        result = make_launcher(self.home, "macos")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.app = self.home / "Desktop/Kapur Lab Dashboard.app"
        self.launch = self.app / "Contents/MacOS/launch"

    def tearDown(self):
        self.tmp.cleanup()

    def test_bundle_is_launchable_and_has_an_icon(self):
        plist = plistlib.loads((self.app / "Contents/Info.plist").read_bytes())
        self.assertEqual(plist["CFBundleExecutable"], "launch")
        self.assertEqual(plist["CFBundlePackageType"], "APPL")
        # The icon is named WITHOUT its extension in the plist and the file must
        # exist under Resources; either half missing yields a generic icon.
        self.assertEqual(plist["CFBundleIconFile"], "bdtools-dashboard")
        icns = self.app / "Contents/Resources/bdtools-dashboard.icns"
        self.assertTrue(icns.exists())
        self.assertTrue(os.access(self.launch, os.X_OK))
        self.assertEqual((self.app / "Contents/PkgInfo").read_text(), "APPL????")

    def test_icns_declares_the_sizes_finder_asks_for(self):
        data = (self.app / "Contents/Resources/bdtools-dashboard.icns").read_bytes()
        self.assertEqual(data[:4], b"icns")
        # The length in the header must match the file, or macOS rejects the lot.
        self.assertEqual(struct.unpack(">I", data[4:8])[0], len(data))
        kinds, off = set(), 8
        while off < len(data):
            kind = data[off:off + 4]
            length = struct.unpack(">I", data[off + 4:off + 8])[0]
            self.assertGreater(length, 8, "zero-length icns chunk")
            self.assertEqual(data[off + 8:off + 12], b"\x89PNG"[:4],
                             f"{kind!r} payload is not a PNG")
            kinds.add(kind)
            off += length
        # 16px (Finder list view) through 1024px (Retina Get Info) — leaving the
        # small slots out makes macOS downsample 512px art, which looks muddy.
        for required in (b"icp4", b"ic08", b"ic10"):
            self.assertIn(required, kinds)

    def test_install_path_is_absolute_so_the_app_can_be_moved(self):
        body = self.launch.read_text()
        self.assertIn(f'REPO="{ROOT}"', body)
        # The bug this replaces: locating the suite relative to the launcher.
        self.assertNotIn('dirname "$0"', body)

    def test_dashboard_is_started_detached(self):
        body = self.launch.read_text()
        self.assertRegex(body, r"(setsid nohup|trap '' HUP;\s*nohup)")
        # An analysis outliving the launcher is the whole point; a foreground
        # start would tie the dashboard's life to a Dock icon.
        self.assertIn("dashboard --port", body)

    def test_failure_reaches_the_user(self):
        body = self.launch.read_text()
        self.assertIn("osascript", body)          # a dialog, not a silent exit
        self.assertIn("Library/Logs/bdtools", body)
        self.assertIn("did not start", body)

    def test_regenerating_over_our_own_bundle_succeeds(self):
        # Re-running after moving the install is the documented fix, so it must
        # not need --force.
        again = make_launcher(self.home, "macos")
        self.assertEqual(again.returncode, 0, again.stderr)

    def test_a_foreign_app_of_the_same_name_is_not_clobbered(self):
        stranger = self.home / "Desktop/Kapur Lab Dashboard.app"
        for path in sorted(stranger.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        (stranger / "Contents").mkdir(parents=True)
        (stranger / "Contents/Info.plist").write_text("not ours")
        refused = make_launcher(self.home, "macos")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--force", refused.stderr + refused.stdout)
        self.assertEqual((stranger / "Contents/Info.plist").read_text(), "not ours")


class LinuxEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        result = make_launcher(self.home, "linux")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.entry = self.home / ".local/share/applications/bdtools-dashboard.desktop"
        self.script = self.home / ".local/share/bdtools/bin/bdtools-dashboard-launch"

    def tearDown(self):
        self.tmp.cleanup()

    def test_desktop_entry_points_at_an_executable_launcher(self):
        fields = dict(
            line.split("=", 1) for line in self.entry.read_text().splitlines()
            if "=" in line and not line.startswith("[")
        )
        self.assertEqual(fields["Type"], "Application")
        self.assertEqual(fields["Terminal"], "false")
        self.assertEqual(fields["Exec"], str(self.script))
        self.assertTrue(os.access(self.script, os.X_OK))
        # Icon by theme NAME (not a path): that is what makes the icon survive in
        # menus, docks and window lists.
        self.assertEqual(fields["Icon"], "bdtools-dashboard")

    def test_icons_are_installed_into_the_hicolor_theme(self):
        theme = self.home / ".local/share/icons/hicolor"
        for size in (16, 32, 48, 128, 256, 512):
            self.assertTrue((theme / f"{size}x{size}/apps/bdtools-dashboard.png").exists(),
                            f"missing {size}px icon")

    def test_desktop_copy_is_marked_trusted(self):
        desktop = self.home / "Desktop"
        desktop.mkdir()
        result = make_launcher(self.home, "linux", "--dest", str(desktop))
        self.assertEqual(result.returncode, 0, result.stderr)
        copy = desktop / "Kapur Lab Dashboard.desktop"
        self.assertTrue(copy.exists())
        self.assertTrue(os.access(copy, os.X_OK))


class OpenUrlWslTests(unittest.TestCase):
    """open_url (common.sh) on WSL prefers the Windows-side opener over xdg-open.

    The launcher's whole journey ends with a browser opening; on WSL it often
    didn't, silently. xdg-utils is routinely installed as a dependency there
    while no Linux browser is, so xdg-open exists, gets picked, is backgrounded
    with output discarded — and the URL never opens, with no hint why, while
    wslview (or the Windows shell) one line down would have worked. The old
    order even labeled wslview "# WSL" while making it unreachable whenever
    xdg-open existed.

    Exercised with a fabricated kernel string and PATH shims that record which
    opener ran — no real WSL, and no real browser, involved.
    """

    WSL = "Linux version 5.15.167.4-microsoft-standard-WSL2 (gcc ...)"
    LINUX = "Linux version 6.5.0-41-generic (buildd@lcy02) ..."

    def _open(self, kernel, shims):
        """Run open_url with only `shims` resolvable; return what got called."""
        with tempfile.TemporaryDirectory() as td:
            bindir = Path(td) / "bin"
            outfile = Path(td) / "called"
            bindir.mkdir()
            for name in shims:
                shim = bindir / name
                shim.write_text(f'#!/bin/sh\necho "{name} $1" >> "{outfile}"\n')
                shim.chmod(0o755)
            script = (
                f'set -euo pipefail\n'
                f'source "{COMMON}"\n'
                f'_wsl_kernel() {{ printf \'%s\' "{kernel}"; }}\n'
                f'PATH="{bindir}:${{PATH}}"\n'
                f'open_url "http://127.0.0.1:8123/"\n'
                f'wait\n'   # the opener is backgrounded; let it finish writing
            )
            env = dict(os.environ)
            env.pop("DRY_RUN", None)
            r = subprocess.run(["bash", "-c", script],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            try:
                return outfile.read_text()
            except OSError:
                return ""

    def test_wsl_uses_wslview_even_when_xdg_open_exists(self):
        called = self._open(self.WSL, ["wslview", "xdg-open"])
        self.assertIn("wslview http://127.0.0.1:8123/", called)
        self.assertNotIn("xdg-open", called,
                         "xdg-open on WSL is commonly browserless — never first")

    def test_wsl_without_wslview_falls_back_to_the_windows_shell(self):
        called = self._open(self.WSL, ["powershell.exe", "xdg-open"])
        self.assertIn("powershell.exe", called)
        self.assertNotIn("xdg-open", called)

    def test_off_wsl_the_existing_chain_is_untouched(self):
        # A plain Linux box with both installed keeps its native opener: the
        # WSL preference must never leak onto real Linux desktops.
        called = self._open(self.LINUX, ["wslview", "xdg-open"])
        self.assertIn("xdg-open http://127.0.0.1:8123/", called)
        self.assertNotIn("wslview", called)


class ArgumentTests(unittest.TestCase):
    def test_port_must_be_numeric(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = make_launcher(Path(tmp), "linux", "--port", "eight-thousand")
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("--port must be a number", bad.stderr)

    def test_help_does_not_build_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            out = make_launcher(home, "linux", "--help")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("make-launcher", out.stdout)
            self.assertFalse((home / ".local/share/applications").exists())


if __name__ == "__main__":
    unittest.main()
