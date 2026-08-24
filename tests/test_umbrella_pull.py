#!/usr/bin/env python3
"""`bdtools pull` — update the umbrella without losing this site's card edits.

The bug: ood/apps/** is tracked (a fresh clone needs a working OOD card) AND is
the one area a site is expected to edit (cluster, account, CPU/memory/walltime
floors). The tool updaters already know that — common.sh:tool_blocking_edits
exempts ood/apps/* and the updater carries those files across a tag checkout —
but the umbrella is pulled by hand, so nothing applied the rule there. A site
that had set a 16 GB floor on the dashboard card met this on every release:

    error: Your local changes to the following files would be overwritten by
    merge:  ood/apps/bdtools_dashboard/form.yml

Each test builds a real two-repo git setup rather than mocking git, because
every interesting case here IS git's behaviour: whether a stash pops cleanly,
what a non-fast-forward looks like, what happens to an unrelated dirty file.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PULL = ROOT / "bin/pull.sh"

CARD = "ood/apps/bdtools_dashboard/form.yml"

# Shaped like the real card, not minimised. git merges by CONTEXT, so a
# three-line fixture makes every edit look like a conflict and the test would
# pass or fail for a reason that has nothing to do with this command. The real
# file separates `min:` from `help:` with `max:`, which is exactly why a site
# floor and a release's help-text edit merge cleanly in the field.
def _card(mem_min="4", mem_help="Shared across all tools in the session.",
          hours_min="1"):
    return (
        "---\n"
        "cluster: \"example\"\n"
        "attributes:\n"
        "  num_cores:\n"
        "    widget: \"number_field\"\n"
        "    label: \"CPU cores\"\n"
        "    value: 16\n"
        "    min: 1\n"
        "    max: 128\n"
        "    help: \"Shared by every tool you open this session.\"\n"
        "\n"
        "  mem_gb:\n"
        "    widget: \"number_field\"\n"
        "    label: \"Memory (GB)\"\n"
        "    value: 64\n"
        f"    min: {mem_min}\n"
        "    max: 1024\n"
        f"    help: \"{mem_help}\"\n"
        "\n"
        "  bc_num_hours:\n"
        "    widget: \"number_field\"\n"
        "    label: \"Session duration (hours)\"\n"
        "    value: 8\n"
        f"    min: {hours_min}\n"
        "    max: 48\n"
    )


CARD_V1 = _card()
CARD_V1_SITE = _card(mem_min="16", hours_min="2")            # the site's floors
CARD_V2 = _card(mem_help="Shared across all tools in the session. "
                         "Enforced on Slurm clusters only.")  # a release's help edit
CARD_V2_CLASH = _card(mem_min="8")                            # a release moving min: itself


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=check)


class UmbrellaPull(unittest.TestCase):
    def setUp(self):
        if not PULL.exists():
            self.skipTest("bin/pull.sh not present")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)

        # An "origin" everyone can push to, and a clone standing in for the site.
        self.origin = base / "origin.git"
        seed = base / "seed"
        seed.mkdir()
        git(seed, "init", "-q", "-b", "main")
        git(seed, "config", "user.email", "t@e.st")
        git(seed, "config", "user.name", "t")
        (seed / CARD).parent.mkdir(parents=True)
        (seed / CARD).write_text(CARD_V1, encoding="utf-8")
        (seed / "bin").mkdir(exist_ok=True)
        (seed / "bin/doctor.sh").write_text("# suite code\n", encoding="utf-8")
        git(seed, "add", "-A")
        git(seed, "commit", "-qm", "seed")
        subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(self.origin)], check=True)
        self.seed = seed
        git(seed, "remote", "add", "origin", str(self.origin))

        self.site = base / "site"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.site)], check=True)
        git(self.site, "config", "user.email", "t@e.st")
        git(self.site, "config", "user.name", "t")
        # pull.sh resolves its repo from its own location, so it has to live here.
        (self.site / "bin").mkdir(exist_ok=True)
        (self.site / "bin/pull.sh").write_text(PULL.read_text(encoding="utf-8"), encoding="utf-8")
        for name in ("lib",):
            src = ROOT / "bin" / name
            dst = self.site / "bin" / name
            if src.is_dir() and not dst.exists():
                subprocess.run(["cp", "-R", str(src), str(dst)], check=True)

    def release(self, content):
        """Publish a new upstream version of the card."""
        (self.seed / CARD).write_text(content, encoding="utf-8")
        git(self.seed, "commit", "-qam", "release")
        git(self.seed, "push", "-q", "origin", "main")

    def run_pull(self, *args):
        return subprocess.run(["bash", str(self.site / "bin/pull.sh"), *args],
                              capture_output=True, text=True,
                              cwd=str(self.site), env={**os.environ, "HOME": self.tmp.name})

    # ---- the case from the field -------------------------------------------
    def test_site_card_edit_survives_a_release_that_touches_the_same_file(self):
        (self.site / CARD).write_text(CARD_V1_SITE, encoding="utf-8")
        self.release(CARD_V2)
        r = self.run_pull()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = (self.site / CARD).read_text(encoding="utf-8")
        self.assertIn("min: 16", got, "the site's memory floor was lost")
        self.assertIn("min: 2", got, "the site's walltime floor was lost")
        self.assertIn("Slurm clusters only", got, "the release's change never arrived")

    def test_a_dirty_file_outside_ood_apps_is_refused_by_name(self):
        """Suite code is not site config. A pull silently overwriting it is how a
        hand-patch disappears — so name it and change nothing."""
        (self.site / "bin/doctor.sh").write_text("# someone's experiment\n", encoding="utf-8")
        before = git(self.site, "rev-parse", "HEAD").stdout
        self.release(CARD_V2)
        r = self.run_pull()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("bin/doctor.sh", r.stderr)
        self.assertEqual(git(self.site, "rev-parse", "HEAD").stdout, before,
                         "nothing may move when the pull is refused")
        self.assertIn("experiment", (self.site / "bin/doctor.sh").read_text(encoding="utf-8"))

    def test_a_same_line_clash_keeps_the_site_version_and_fails_loudly(self):
        """Whose value wins is a human decision. Nothing may be lost, and the
        command must not print a success line over the top of it."""
        (self.site / CARD).write_text(CARD_V1_SITE, encoding="utf-8")
        self.release(CARD_V2_CLASH)
        r = self.run_pull()
        self.assertNotEqual(r.returncode, 0, "a conflict must not report success")
        self.assertIn(CARD, r.stderr)
        self.assertTrue(git(self.site, "stash", "list").stdout.strip(),
                        "the site's version must still be recoverable from the stash")

    def test_dry_run_changes_nothing(self):
        (self.site / CARD).write_text(CARD_V1_SITE, encoding="utf-8")
        before = git(self.site, "rev-parse", "HEAD").stdout
        self.release(CARD_V2)
        r = self.run_pull("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(git(self.site, "rev-parse", "HEAD").stdout, before)
        self.assertIn("min: 16", (self.site / CARD).read_text(encoding="utf-8"))

    def test_clean_tree_just_fast_forwards(self):
        self.release(CARD_V2)
        r = self.run_pull()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Slurm clusters only", (self.site / CARD).read_text(encoding="utf-8"))

    def test_a_diverged_checkout_is_named_not_guessed_at(self):
        """An earlier draft reported every failed pull as divergence, including a
        plain network error. The verdict has to be checked before it is stated."""
        (self.site / "bin/doctor.sh").write_text("# local commit\n", encoding="utf-8")
        git(self.site, "commit", "-qam", "a commit origin does not have")
        self.release(CARD_V2)
        r = self.run_pull()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("1 commit(s)", r.stderr)
        self.assertIn("not a", r.stderr)


if __name__ == "__main__":
    unittest.main()
