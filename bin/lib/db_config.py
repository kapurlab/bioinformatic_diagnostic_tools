#!/usr/bin/env python3
"""db_config.py — point the GUIs at locally-installed reference databases.

setup-databases.sh downloads the shared reference databases to a chosen root
(home or shared) and calls this helper to write the matching path into each
tool's per-user config.json. JSON is merged in place so existing user prefs are
preserved; only the database keys are touched.

Config locations mirror each tool's own config.py (XDG_CONFIG_HOME, else
~/.config/<tool>/config.json).

Usage:
  db_config.py kraken  --kraken-db PATH --blast-db PATH
  db_config.py vsnp    --refs-root PATH        # only when there is NO local
                                               # vsnp3 site (setup-databases
                                               # re-points the site symlink
                                               # otherwise, which survives the
                                               # launcher's self-heal).
Every flag is optional; only keys whose flag is given are written.
"""
import argparse
import json
import os
from pathlib import Path


def config_path(tool: str) -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / tool / "config.json"


def update(tool: str, updates: dict) -> None:
    updates = {k: v for k, v in updates.items() if v}
    if not updates:
        return
    p = config_path(tool)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    changed = {k: v for k, v in updates.items() if cfg.get(k) != v}
    if not changed:
        print(f"  {tool}: config already current")
        return
    cfg.update(changed)
    p.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
    print(f"  {tool}: set {', '.join(sorted(changed))} -> {p}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="tool", required=True)

    k = sub.add_parser("kraken")
    k.add_argument("--kraken-db")
    k.add_argument("--blast-db")

    v = sub.add_parser("vsnp")
    v.add_argument("--refs-root")

    args = ap.parse_args()
    if args.tool == "kraken":
        update("kraken_id_parse_gui", {
            "kraken_db": args.kraken_db,
            "blast_db": args.blast_db,
        })
    elif args.tool == "vsnp":
        update("vsnp_gui", {
            "vsnp3_reference_options_root": args.refs_root,
        })


if __name__ == "__main__":
    main()
