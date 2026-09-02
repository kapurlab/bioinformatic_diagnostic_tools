#!/usr/bin/env python3
"""doctor grades the database a tool would actually use, not a path another
deployment left in this user's config.

ICAR-NIVEDI, 2026-09-02: doctor reported the Kraken GUI's BLAST database as
"missing /srv/kapurlab/databases/blast/ref_prok_rep_genomes" and proposed a
download — on a site whose databases live under /srv/icar. The saved value was
a literal the tool's old DEFAULTS wrote into root's config.json on first run,
and doctor graded it at face value. config_hygiene's three-clause rule is the
suite's one judgement of "foreign" (the launcher sweeps exactly these values at
start-up); doctor now applies it, grades the site default in its place, and
names the repair.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_check():
    spec = importlib.util.spec_from_file_location("check_probe", ROOT / "bin/lib/check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


KRAKEN_DB = {"label": "Kraken2 DB", "config_key": "kraken_db", "kind": "dir_marker",
             "markers": ["hash.k2d", "opts.k2d", "taxo.k2d"],
             "default_under": ("db_root", "kraken2/k2_standard_08gb")}


class ForeignSavedDbPath(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        base = Path(self.td.name)
        self.db_root = base / "databases"
        site_db = self.db_root / "kraken2/k2_standard_08gb"
        site_db.mkdir(parents=True)
        for m in KRAKEN_DB["markers"]:
            (site_db / m).write_text("x", encoding="utf-8")
        self.site_db = site_db
        self.cfg_dir = base / "xdg/kraken_id_parse_gui"
        self.cfg_dir.mkdir(parents=True)
        self.env = {"XDG_CONFIG_HOME": str(base / "xdg"), "BDTOOLS_DB_ROOT": str(self.db_root),
                    "BDTOOLS_HOME": str(base / "bdhome"), "HOME": str(base / "home")}

    def saved(self, value):
        (self.cfg_dir / "config.json").write_text(json.dumps({"kraken_db": value}), encoding="utf-8")

    def check(self):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("BDTOOLS_")}
        clean.update(self.env)
        with mock.patch.dict(os.environ, clean, clear=True):
            return load_check().check_db("kraken_id_parse_gui", KRAKEN_DB)

    def test_a_foreign_saved_path_is_graded_at_the_site_default_and_named(self):
        self.saved("/srv/elsewhere/databases/kraken2/k2_standard_08gb")
        ok, detail = self.check()
        self.assertTrue(ok, detail)
        self.assertIn(str(self.site_db), detail)
        self.assertIn("/srv/elsewhere", detail, "the foreign value must be named")
        self.assertIn("check-paths kraken_id_parse_gui --apply", detail, "and the repair with it")

    def test_a_saved_path_under_this_sites_root_is_graded_as_saved(self):
        """A value under a root this machine owns is ours to grade even when it is
        missing: clause 3 keeps a not-yet-staged database from being called foreign."""
        self.saved(str(self.db_root / "kraken2/not_staged_yet"))
        ok, detail = self.check()
        self.assertFalse(ok)
        self.assertIn("not_staged_yet", detail)
        self.assertNotIn("check-paths", detail)

    def test_no_saved_value_is_the_default_with_no_note(self):
        ok, detail = self.check()
        self.assertTrue(ok, detail)
        self.assertNotIn("check-paths", detail)


if __name__ == "__main__":
    unittest.main()
