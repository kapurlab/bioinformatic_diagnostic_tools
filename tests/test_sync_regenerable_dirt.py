#!/usr/bin/env python3
"""`bdtools sync` and the difference between a human's edit and derived output.

Live, 2026-09-15, the vxk1 site: `git pull` brought four pin bumps, and sync
deployed one of them. Six of nine checkouts were skipped as "local tracked
edits" — every one of them dirty with nothing but regenerated
`__pycache__/*.pyc` (the interpreter rewrites these on import; irma_gui and
genoflu_gui carry them committed) and `frontend/package-lock.json` (npm
rewrites it against the local Node). No human had touched any of it.

common.sh:tool_blocking_edits exists to be the one answer to "is this a user
edit?", and its own comment says it was centralized so the callers "cannot
drift apart again". sync.sh asked `git diff --quiet` directly and drifted
anyway: `bdtools update` had already been taught to tolerate regenerated
bytecode, so THE deploy step was the one command that could not deploy.

Tolerating the dirt is only half of it — a plain `git checkout <tag>` aborts on
exactly the files just forgiven, so the tolerance alone would buy a nicer error
and no deploy. The move has to force past derived output, and carry ood/apps/**
across, which is the contract the two updaters already keep.

Pinned here: derived output never blocks and never survives the move; a real
source edit always blocks and is NAMED; site-localized OOD cards ride across.
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
  - name: irma_gui
    repo: {repo}
    version: v2
    updates: install
"""


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True, env=GIT_ENV)


class SyncRegenerableDirt(unittest.TestCase):
    """A pinned (detached) checkout carrying derived output."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)

        # The upstream the site deploys from: v1 (deployed) then v2 (the pin).
        self.origin = base / "origin"
        self.origin.mkdir()
        git("init", "-q", "-b", "main", cwd=self.origin)
        (self.origin / "app.py").write_text("v1\n", encoding="utf-8")
        cache = self.origin / "backend/app/__pycache__"
        cache.mkdir(parents=True)
        (cache / "main.cpython-310.pyc").write_bytes(b"bytecode-v1")
        front = self.origin / "frontend"
        front.mkdir()
        (front / "package-lock.json").write_text('{"v":1}\n', encoding="utf-8")
        ood = self.origin / "ood/apps/irma"
        ood.mkdir(parents=True)
        (ood / "submit.yml.erb").write_text("cluster: UPSTREAM\n", encoding="utf-8")
        git("add", "-A", cwd=self.origin)
        git("commit", "-q", "-m", "v1", cwd=self.origin)
        git("tag", "v1", cwd=self.origin)
        (self.origin / "app.py").write_text("v2\n", encoding="utf-8")
        (cache / "main.cpython-310.pyc").write_bytes(b"bytecode-v2")
        (front / "package-lock.json").write_text('{"v":2}\n', encoding="utf-8")
        git("add", "-A", cwd=self.origin)
        git("commit", "-q", "-m", "v2", cwd=self.origin)
        git("tag", "v2", cwd=self.origin)

        self.tools = base / "tools"
        self.tools.mkdir()
        self.repo = self.tools / "irma_gui"
        git("clone", "-q", str(self.origin), str(self.repo), cwd=base)
        git("checkout", "-q", "v1", cwd=self.repo)

        self.manifest = base / "tools.yml"
        self.manifest.write_text(MANIFEST.format(repo=self.origin), encoding="utf-8")

    def sync(self, *extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BDTOOLS_")}
        env.update(GIT_ENV)
        env["BDTOOLS_HOME"] = str(Path(self.tmp.name) / "bdhome")
        env["BDTOOLS_MANIFEST"] = str(self.manifest)
        r = subprocess.run(["bash", str(SYNC), "irma_gui", "--toolsdir", str(self.tools), *extra],
                           capture_output=True, text=True, env=env, cwd=str(ROOT))
        return r.returncode, r.stdout + r.stderr

    def at(self):
        return git("describe", "--tags", "--always", cwd=self.repo).stdout.strip()

    def dirty_bytecode(self):
        (self.repo / "backend/app/__pycache__/main.cpython-310.pyc").write_bytes(b"rewritten-on-import")

    def dirty_lockfile(self):
        (self.repo / "frontend/package-lock.json").write_text('{"v":"local-node"}\n', encoding="utf-8")

    # --- derived output must not block -----------------------------------
    def test_regenerated_bytecode_does_not_block_the_pin_move(self):
        self.dirty_bytecode()
        rc, out = self.sync()
        self.assertEqual("v2", self.at(),
                         f"a .pyc the interpreter rewrote blocked the deploy:\n{out}")
        self.assertEqual(0, rc, out)

    def test_npm_rewritten_lockfile_does_not_block_the_pin_move(self):
        self.dirty_lockfile()
        rc, out = self.sync()
        self.assertEqual("v2", self.at(), f"package-lock.json blocked the deploy:\n{out}")
        self.assertEqual(0, rc, out)

    def test_dry_run_reports_the_move_it_would_make(self):
        self.dirty_bytecode()
        rc, out = self.sync("--dry-run")
        self.assertIn("WOULD move", out)
        self.assertEqual("v1", self.at(), "--dry-run moved the checkout")
        self.assertEqual(0, rc, out)

    # --- a human's edit still blocks, and is named ------------------------
    def test_a_source_edit_blocks_and_the_file_is_named(self):
        (self.repo / "app.py").write_text("local experiment\n", encoding="utf-8")
        rc, out = self.sync()
        self.assertEqual("v1", self.at(), f"sync moved over a real source edit:\n{out}")
        self.assertIn("app.py", out,
                      "the operator has to run `git status` themselves to learn which file")
        self.assertNotEqual(0, rc, "a skipped deploy must not exit 0")

    def test_a_staged_source_edit_blocks_too(self):
        (self.repo / "app.py").write_text("staged experiment\n", encoding="utf-8")
        git("add", "app.py", cwd=self.repo)
        rc, out = self.sync()
        self.assertEqual("v1", self.at(), f"sync moved over a staged edit:\n{out}")
        self.assertNotEqual(0, rc, out)

    # --- the site's own OOD cards ride across -----------------------------
    def test_site_localized_ood_card_survives_the_move(self):
        card = self.repo / "ood/apps/irma/submit.yml.erb"
        card.write_text("cluster: vxk1\naccount: open\n", encoding="utf-8")
        rc, out = self.sync()
        self.assertEqual("v2", self.at(), out)
        self.assertIn("cluster: vxk1", card.read_text(encoding="utf-8"),
                      "the force checkout ate this deployment's card config")
        self.assertEqual(0, rc, out)


if __name__ == "__main__":
    unittest.main()
