#!/usr/bin/env python3
"""The session's CPU/memory budget survives an OOD version that hides form locals.

The dashboard card exports BDTOOLS_SESSION_CORES/_MEM_GB so every tool launched
in the session caps its thread counts to what the user asked for, instead of
taking every core on a shared node.

Those values came from ERB tags reading the form attributes as bare locals. That
works on OOD 3.1.16 (the reference deployment) and NOT on OOD 4.2.3, where the
script template's binding does not expose them: `defined?(num_cores)` is false
and the tags render 0. Observed on a live Roar Collab session, 2026-09-11.

A Slurm site absorbs it — the tools try SLURM_CPUS_PER_TASK next — which is what
makes it dangerous: it is invisible where it does no harm, and silently removes
the cap on a linux_host site that has no such fallback, which is the single case
the budget exists for.

So the script reads OOD's own user_defined_context.json from the job workdir:
written by every adapter and every version, before the script runs, needing no
binding. These tests drive the real template with the tags rendered both ways.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "ood/apps/bdtools_dashboard/template/script.sh.erb"

CONTEXT = """{
  "cluster": "2e",
  "slurm_partition": "basic",
  "num_cores": "8",
  "mem_gb": "32",
  "bc_num_hours": "2"
}
"""


def budget_block(cores_tag, mem_tag):
    """The template's budget section, with the ERB tags rendered as given."""
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("<%= defined?(num_cores) ? num_cores.to_i : 0 %>", cores_tag)
    text = text.replace("<%= defined?(mem_gb) ? mem_gb.to_i : 0 %>", mem_tag)
    start = text.index("# ---- session resource intent")
    end = text.index("echo \"  session budget:", start)
    end = text.index("\n", end)
    return text[start:end]


def run(block, workdir, env=None):
    e = {"PATH": os.environ.get("PATH", "")}
    e.update(env or {})
    r = subprocess.run(["bash", "-c", block + "\nprintf '%s %s\\n' \"$BDTOOLS_SESSION_CORES\" \"$BDTOOLS_SESSION_MEM_GB\""],
                       capture_output=True, text=True, cwd=str(workdir), env=e)
    assert r.returncode == 0, r.stderr
    # The block echoes its own summary line first; the printf is what we assert on.
    return r.stdout.strip().splitlines()[-1].strip()


class SessionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _with_context(self):
        (self.wd / "user_defined_context.json").write_text(CONTEXT)

    def test_ood4_binding_hides_the_locals(self):
        """The tags render 0; the context file must supply the real numbers."""
        self._with_context()
        self.assertEqual(run(budget_block("0", "0"), self.wd), "8 32")

    def test_ood3_binding_still_works(self):
        """Where the binding does expose them, nothing changes."""
        self._with_context()
        self.assertEqual(run(budget_block("8", "32"), self.wd), "8 32")

    def test_context_file_does_not_override_a_real_erb_value(self):
        """A rendered value is the request itself; it wins over re-reading it."""
        (self.wd / "user_defined_context.json").write_text(
            CONTEXT.replace('"num_cores": "8"', '"num_cores": "99"'))
        self.assertEqual(run(budget_block("8", "32"), self.wd), "8 32")

    def test_slurm_is_the_last_resort(self):
        """No locals and no context file: the allocation is still authoritative."""
        self.assertEqual(
            run(budget_block("0", "0"), self.wd, {"SLURM_CPUS_PER_TASK": "12"}),
            "12 0")

    def test_nothing_to_read_degrades_to_zero_not_to_garbage(self):
        """0 means 'unset' to every consumer, which then keeps its own default.

        It must not become empty or non-numeric: the tools test isdigit() and a
        malformed value would be read as 'no budget declared' by luck, not design.
        """
        self.assertEqual(run(budget_block("0", "0"), self.wd), "0 0")

    def test_a_malformed_context_file_is_not_fatal(self):
        """A session that cannot parse its own context must still start."""
        (self.wd / "user_defined_context.json").write_text("{ not json at all")
        self.assertEqual(run(budget_block("0", "0"), self.wd), "0 0")


if __name__ == "__main__":
    unittest.main()
