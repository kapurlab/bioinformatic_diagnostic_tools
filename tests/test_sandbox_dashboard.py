#!/usr/bin/env python3
"""The consolidated dashboard installs per-user, and the render cannot lie.

The dashboard is the one card users are meant to launch: per-tool cards start a
scheduler job each and carry no application-level authentication. Path A renders
it with install-server.sh, which rewrites the reference site's literals from
sites/site.conf. The sandbox path had no equivalent, so the only card the CLI
could not install was the one it tells everyone to use, and rehearsing Path A
meant rehearsing the cards it replaced.

Two values decide whether a per-user render works, and both fail at *Launch*
rather than at install time, which is what makes them worth a test:

  * `cluster:` — form.yml ships the reference site's id. Nothing rewrites it on
    this path, so an un-rewritten card submits to a cluster the site has never
    heard of.
  * the umbrella path — the session script's per-user fallback is
    $HOME/bioinformatic_diagnostic_tools, and a sandbox user clones wherever
    they like.

So the installer verifies what it WROTE, not what it meant to write: a rewrite
that silently stops matching (the upstream text moves) must fail loudly here.

No conda, no network, no OOD: the render is driven against a throwaway HOME.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "bin/install-sandbox.sh"
CARD = ROOT / "ood/apps/bdtools_dashboard"


def render(home, *extra, bdtools_home=None):
    """Run the sandbox dashboard install against a throwaway HOME."""
    env = dict(os.environ, HOME=str(home))
    # An inherited BDTOOLS_HOME would point the install back at the real tree.
    env.pop("BDTOOLS_HOME", None)
    if bdtools_home is not None:
        env["BDTOOLS_HOME"] = str(bdtools_home)
    return subprocess.run(
        [str(INSTALLER), "--dashboard", *extra],
        capture_output=True, text=True, env=env, cwd=str(ROOT))


class SandboxDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp) / "home"
        self.home.mkdir()
        self.dst = self.home / "ondemand/dev/bdtools_dashboard"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_render_rewrites_the_cluster(self):
        """An un-rewritten cluster id is the failure this whole path exists to avoid."""
        r = render(self.home, "--cluster", "someclust")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        form = (self.dst / "form.yml").read_text()
        self.assertIn('cluster: "someclust"', form)
        # The reference site's id must not survive anywhere in the rendered card.
        self.assertNotIn('cluster: "wgs3"', form)

    def test_render_bakes_in_this_checkout(self):
        """A sandbox user clones the umbrella anywhere; $HOME/... is not a location."""
        r = render(self.home, "--cluster", "someclust")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        script = (self.dst / "template/script.sh.erb").read_text()
        self.assertIn(f"BDTOOLS_REPO:-{ROOT}", script)
        self.assertNotIn("BDTOOLS_REPO:-${HOME}/bioinformatic_diagnostic_tools", script)

    def test_render_bakes_in_where_tool_envs_live(self):
        """A batch job need not inherit BDTOOLS_HOME from a login profile.

        The session script's fallback is ~/.local/share/bdtools. On a deployment
        that sets BDTOOLS_HOME (a group tree, a --prefix install) the envs are
        somewhere else entirely, so an unbaked card finds no python and exits
        while every tool it lists has one.
        """
        elsewhere = Path(self.tmp) / "group/bdtools"
        r = render(self.home, "--cluster", "someclust", bdtools_home=elsewhere)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        script = (self.dst / "template/script.sh.erb").read_text()
        self.assertIn(f"BDTOOLS_HOME:-{elsewhere}", script)
        self.assertNotIn("${XDG_DATA_HOME:-${HOME}/.local/share}/bdtools", script)

    def test_refuses_to_guess_a_cluster(self):
        """Guessing produces a card that fails at Launch, long after the install 'passed'."""
        if Path("/etc/ood/config/clusters.d").is_dir():
            self.skipTest("host defines clusters.d, so detection has an answer here")
        r = render(self.home)
        self.assertNotEqual(r.returncode, 0, "a card with no cluster must not be written")
        self.assertFalse(self.dst.exists(), "nothing may be written when the cluster is unknown")
        self.assertIn("--cluster", r.stdout + r.stderr)

    def test_rendered_copy_is_outside_the_checkout(self):
        """A symlink puts the edit in a git tree that `bdtools update` force-checks-out."""
        r = render(self.home, "--cluster", "someclust")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.dst.is_symlink(), "the card must be a copy, not a symlink")
        self.assertTrue((self.dst / "form.yml").is_file())
        # Rendering must not have touched the source card.
        self.assertIn('cluster: "wgs3"', (CARD / "form.yml").read_text())

    def test_render_changes_nothing_else(self):
        """Only the two intended values differ; a stray sed would corrupt the card."""
        r = render(self.home, "--cluster", "someclust")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for src in sorted(p for p in CARD.rglob("*") if p.is_file()):
            rel = src.relative_to(CARD)
            out = self.dst / rel
            self.assertTrue(out.is_file(), f"{rel} was not rendered")
            if rel.name in ("form.yml", "script.sh.erb"):
                continue
            self.assertEqual(src.read_text(), out.read_text(),
                             f"{rel} changed but nothing should have rewritten it")

    def test_dry_run_writes_nothing(self):
        r = render(self.home, "--cluster", "someclust", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.dst.exists(), "--dry-run must not create the card")

    def test_tool_name_with_dashboard_is_refused(self):
        """--dashboard installs the one card; a tool name means the user wanted the other thing."""
        r = render(self.home, "--cluster", "someclust", "mlst_gui")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.dst.exists())

    def test_wrapper_refuses_a_tool_name_instead_of_dropping_it(self):
        """The CLI forwards no positionals, so an ignored tool name reads as installed.

        `bdtools install --sandbox --dashboard mlst_gui` used to render the card
        and say nothing about mlst_gui, which never got built.
        """
        env = dict(os.environ, HOME=str(self.home))
        env.pop("BDTOOLS_HOME", None)
        r = subprocess.run(
            [str(ROOT / "bin/bdtools"), "install", "--sandbox", "--dashboard",
             "--cluster", "someclust", "mlst_gui"],
            capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.dst.exists(), "nothing may be installed when the request is ambiguous")
        self.assertIn("mlst_gui", r.stdout + r.stderr, "the dropped name must be named back")

    def test_upgrading_from_the_symlink_route_does_not_touch_the_checkout(self):
        """The route this command replaces was a symlink AT THIS PATH into the checkout.

        Rendering "into" it followed it back to the source, where `sed f > out`
        with f and out the same file truncated every card file to zero bytes
        before sed could read it — the install command destroying the card.
        """
        dev = self.home / "ondemand/dev"
        dev.mkdir(parents=True)
        (dev / "bdtools_dashboard").symlink_to(CARD)
        before = {f.relative_to(CARD): f.read_bytes()
                  for f in CARD.rglob("*") if f.is_file()}

        r = render(self.home, "--cluster", "someclust")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        after = {f.relative_to(CARD): f.read_bytes()
                 for f in CARD.rglob("*") if f.is_file()}
        self.assertEqual(before, after, "the source card was modified")
        self.assertFalse(self.dst.is_symlink(), "the symlink must be replaced, not followed")
        self.assertIn('cluster: "someclust"', (self.dst / "form.yml").read_text())

    def test_rerender_prunes_a_file_a_release_removed(self):
        """An in-place render leaves a removed file behind, still part of the card."""
        self.assertEqual(render(self.home, "--cluster", "someclust").returncode, 0)
        stale = self.dst / "OLD_FILE.yml"
        stale.write_text("left over from an older release\n")
        self.assertEqual(render(self.home, "--cluster", "someclust").returncode, 0)
        self.assertFalse(stale.exists(), "a stale card file survived a re-render")

    def test_rerender_changes_the_cluster(self):
        """Re-running is the documented way to update; it must actually take."""
        self.assertEqual(render(self.home, "--cluster", "first").returncode, 0)
        self.assertEqual(render(self.home, "--cluster", "second").returncode, 0)
        self.assertIn('cluster: "second"', (self.dst / "form.yml").read_text())

    def test_a_directory_that_is_not_a_card_is_left_alone(self):
        """The installer deletes the destination, so it must be sure what it is."""
        self.dst.mkdir(parents=True)
        keep = self.dst / "notes.txt"
        keep.write_text("something the user put here\n")
        r = render(self.home, "--cluster", "someclust")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(keep.is_file(), "the installer removed a directory it did not own")
        self.assertEqual(keep.read_text(), "something the user put here\n")

    def test_wrapper_rejects_dashboard_for_local(self):
        """--local has no OOD card to register."""
        env = dict(os.environ, HOME=str(self.home))
        env.pop("BDTOOLS_HOME", None)
        r = subprocess.run(
            [str(ROOT / "bin/bdtools"), "install", "--local", "--dashboard"],
            capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.dst.exists())


if __name__ == "__main__":
    unittest.main()
