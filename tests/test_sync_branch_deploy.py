#!/usr/bin/env python3
"""`bdtools sync` and a checkout deployed on a branch.

Two ways the branch path told the operator the opposite of the truth.

1. NO UPSTREAM. sync measured "how far behind?" with
   `rev-list --count HEAD..origin/<branch>`, and swallowed the failure with
   `|| echo 0`. When origin/<branch> does not resolve — the branch was never
   pushed, or was deleted upstream after deploy — the count failed, the
   fallback said 0, and sync printed "already current on <branch>", in green,
   at every deploy. The failure was shaped exactly like success, so there was
   nothing to investigate: a checkout running code no one else can fetch, and
   the one command whose job is to notice reporting it as deployed and current.

2. DIVERGED vs. DIRTY. `merge --ff-only` refuses two different things: a
   history that cannot fast-forward, and a working tree whose tracked files the
   merge would overwrite. sync reported both as "has DIVERGED ... Reconcile by
   hand" — so a checkout that was a plain fast-forward with a regenerated .pyc
   in it sent the operator off to reconcile a history that was never in
   conflict. Divergence is a fact about commits; decide it from the commit
   counts and let the tree be a tree.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "bin/sync.sh"
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

MANIFEST = """\
suite_version: "0.0.0"
tools:
  - name: ksnp_gui
    repo: {repo}
    version: v1
    updates: install
"""


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True, env=GIT_ENV)


class SyncBranchDeploy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)

        self.origin = base / "origin"
        self.origin.mkdir()
        git("init", "-q", "-b", "main", cwd=self.origin)
        (self.origin / "app.py").write_text("v1\n", encoding="utf-8")
        cache = self.origin / "backend/app/__pycache__"
        cache.mkdir(parents=True)
        (cache / "main.cpython-310.pyc").write_bytes(b"bytecode-v1")
        git("add", "-A", cwd=self.origin)
        git("commit", "-q", "-m", "v1", cwd=self.origin)
        git("tag", "v1", cwd=self.origin)

        self.tools = base / "tools"
        self.tools.mkdir()
        self.repo = self.tools / "ksnp_gui"
        git("clone", "-q", str(self.origin), str(self.repo), cwd=base)

        self.manifest = base / "tools.yml"
        self.manifest.write_text(MANIFEST.format(repo=self.origin), encoding="utf-8")

    def advance_origin(self):
        """One more commit on origin/main, touching a committed .pyc as well."""
        (self.origin / "app.py").write_text("v2\n", encoding="utf-8")
        (self.origin / "backend/app/__pycache__/main.cpython-310.pyc").write_bytes(b"bytecode-v2")
        git("add", "-A", cwd=self.origin)
        git("commit", "-q", "-m", "v2", cwd=self.origin)

    def sync(self, *extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BDTOOLS_")}
        env.update(GIT_ENV)
        env["BDTOOLS_HOME"] = str(Path(self.tmp.name) / "bdhome")
        env["BDTOOLS_MANIFEST"] = str(self.manifest)
        r = subprocess.run(["bash", str(SYNC), "ksnp_gui", "--toolsdir", str(self.tools), *extra],
                           capture_output=True, text=True, env=env, cwd=str(ROOT))
        return r.returncode, r.stdout + r.stderr

    def head_subject(self):
        return git("log", "-1", "--format=%s", cwd=self.repo).stdout.strip()

    # --- 1. a branch with nothing upstream --------------------------------
    def test_local_only_branch_is_not_reported_as_current(self):
        git("checkout", "-q", "-b", "fix/report-out-of-space-writes", cwd=self.repo)
        (self.repo / "app.py").write_text("unpushed fix\n", encoding="utf-8")
        git("commit", "-q", "-am", "unpushed fix", cwd=self.repo)
        rc, out = self.sync()
        self.assertNotIn("already current", out,
                         "an unpushed branch was reported as deployed-and-current")
        self.assertIn("local-only branch", out)
        self.assertIn("fix/report-out-of-space-writes", out, "say WHICH branch")
        self.assertNotEqual(0, rc, "an unresolved deployment must not exit 0")

    def test_the_fix_is_named_so_the_operator_can_act(self):
        git("checkout", "-q", "-b", "wip", cwd=self.repo)
        rc, out = self.sync()
        self.assertIn("push -u origin wip", out,
                      "tell the operator how to resolve it, not just that it is wrong")

    # --- 2. dirty tree is not a diverged history --------------------------
    def test_regenerated_bytecode_does_not_look_like_divergence(self):
        self.advance_origin()
        (self.repo / "backend/app/__pycache__/main.cpython-310.pyc").write_bytes(b"rewritten-on-import")
        rc, out = self.sync()
        self.assertNotIn("DIVERGED", out,
                         "a plain fast-forward with a regenerated .pyc was called a divergence")
        self.assertEqual("v2", self.head_subject(), f"the fast-forward did not happen:\n{out}")
        self.assertEqual(0, rc, out)

    def test_a_real_divergence_is_still_refused(self):
        self.advance_origin()
        (self.repo / "app.py").write_text("local commit\n", encoding="utf-8")
        git("commit", "-q", "-am", "local commit", cwd=self.repo)
        rc, out = self.sync()
        self.assertIn("DIVERGED", out)
        self.assertEqual("local commit", self.head_subject(),
                         "sync moved a branch that had commits of its own")
        self.assertNotEqual(0, rc, out)

    def test_a_source_edit_still_blocks_a_branch_checkout(self):
        self.advance_origin()
        (self.repo / "app.py").write_text("local experiment\n", encoding="utf-8")
        rc, out = self.sync()
        self.assertEqual("v1", self.head_subject(), f"sync moved over a real edit:\n{out}")
        self.assertIn("app.py", out)
        self.assertNotEqual(0, rc, out)

    def test_an_up_to_date_branch_is_quiet_and_clean(self):
        rc, out = self.sync()
        self.assertIn("already current", out)
        self.assertEqual(0, rc, out)


if __name__ == "__main__":
    unittest.main()
