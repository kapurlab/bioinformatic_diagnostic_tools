#!/usr/bin/env python3
"""`bdtools sync` and a checkout git will not open.

ICAR-NIVEDI, 2026-09-01: the site's tool checkouts are root-owned and the
operator runs sync as oodadmin with no safe.directory entries. sync.sh asked
`git diff --quiet` first; git refused to open the repo — exit 129 and the full
`git diff --no-index` usage text on stderr — and the dirty check read any
non-zero exit as "has local edits". Eight tools skipped as (dirty), 58 KB of
usage text burying the one line per tool that mattered, and the safe.directory
hint never printed, because only the fetch further down knew to look for
"dubious ownership" and the fetch was never reached.

Harmless there only because those checkouts really were dirty. On a site with
root-owned CLEAN checkouts the same code skips everything, forever, and calls
it a guard. The rule pinned here: "cannot open" is its own outcome, told apart
from "has edits" and reported with what to do, and neither path dumps git's
usage text into the report.
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "bin/sync.sh"
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True, env=GIT_ENV)


class SyncUnreadableCheckout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tools = Path(self.tmp.name) / "tools"
        self.repo = self.tools / "irma_gui"
        self.repo.mkdir(parents=True)
        git("init", "-q", cwd=self.repo)
        (self.repo / "tracked.txt").write_text("v1\n", encoding="utf-8")
        git("add", "tracked.txt", cwd=self.repo)
        git("commit", "-q", "-m", "tracked", cwd=self.repo)

    def sync(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BDTOOLS_")}
        env["BDTOOLS_HOME"] = str(Path(self.tmp.name) / "bdhome")
        r = subprocess.run(["bash", str(SYNC), "irma_gui", "--toolsdir", str(self.tools)],
                           capture_output=True, text=True, env=env, cwd=str(ROOT))
        return r.stdout + r.stderr

    def test_a_checkout_git_cannot_open_is_not_called_dirty(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes; the refusal cannot be staged")
        gitdir = self.repo / ".git"
        mode = stat.S_IMODE(gitdir.stat().st_mode)
        gitdir.chmod(0)
        self.addCleanup(gitdir.chmod, mode)
        out = self.sync()
        self.assertIn("(unreadable)", out)
        self.assertIn("cannot open", out)
        self.assertNotIn("(dirty)", out, "a refusal is not an edit")
        self.assertNotIn("usage: git diff", out, "git's usage text is not a report")

    def test_a_checkout_with_edits_is_still_dirty(self):
        (self.repo / "tracked.txt").write_text("v2\n", encoding="utf-8")
        out = self.sync()
        self.assertIn("(dirty)", out)
        self.assertNotIn("(unreadable)", out)
        self.assertNotIn("usage: git diff", out)

    def test_a_clean_checkout_reaches_the_fetch(self):
        """No origin here, so the fetch fails — which is the point: a clean repo
        must get PAST both the probe and the dirty check, not be turned back by
        either of them."""
        out = self.sync()
        self.assertNotIn("(dirty)", out)
        self.assertNotIn("(unreadable)", out)
        self.assertIn("(fetch)", out)


if __name__ == "__main__":
    unittest.main()
