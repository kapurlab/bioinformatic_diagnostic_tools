#!/usr/bin/env python3
"""addon_tools.py — `bdtools new-addon` and `bdtools addon-check`.

  new-addon <id> --name "Display name" [--dest DIR]
      A new pathogen add-on repository from templates/pathogen_addon: the
      manifest, env, library builder, the four hooks, an example action, the
      vendored addon_sdk.py, a fixture project and a CI workflow, with the id
      and name filled in. Every add-on starts from the same files, so every
      add-on is checked, installed and run the same way.

  addon-check <id | tool | path> [--no-fixture] [--known-answer] [--json]
      Run the consumer's own contract check (vsnp_gui app.addon_check) on an
      add-on, with the consumer's env and the environment its launches get. The
      rules live in the code that runs add-ons, never in a second copy here.

docs/BUILDING_AN_ADDON.md is the contract in prose. Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import tool_launch  # noqa: E402

REPO_DIR = _HERE.parent.parent
TEMPLATE = REPO_DIR / "templates" / "pathogen_addon"
ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
CONSUMER = "vsnp_gui"


def new_addon(addon_id: str, name: str, dest: Path) -> int:
    if not ID_RE.fullmatch(addon_id):
        sys.exit(f"new-addon: id {addon_id!r} must be lower-case letters, digits and _ "
                 "(start with a letter, at most 32), e.g. fmdv, hpai, asf")
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"new-addon: {dest} exists and is not empty")
    tool = f"{addon_id}_addon"
    subs = {"__ADDON_ID__": addon_id, "__ADDON_TOOL__": tool, "__ADDON_NAME__": name}

    def fill(text: str) -> str:
        for k, v in subs.items():
            text = text.replace(k, v)
        return text

    for src in sorted(TEMPLATE.rglob("*")):
        rel = src.relative_to(TEMPLATE)
        if any(part in ("env", "library", "__pycache__") for part in rel.parts) or rel.name == ".library_path":
            continue
        out = dest / fill(str(rel))
        if src.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        try:
            out.write_text(fill(data.decode("utf-8")), encoding="utf-8")
        except UnicodeDecodeError:
            out.write_bytes(data)          # reads, gzip: copied as they are
        shutil.copymode(src, out)
    print(f"new add-on '{addon_id}' ({name}) at {dest}\n")
    print("Next:")
    print(f"  1. cd {dest} && git init && git add -A && git commit -m 'Start the {name} add-on from the bdtools template'")
    print("  2. Replace the TODOs: deploy/build_library.sh, hooks/choose_reference.py, hooks/after_sample.py")
    print(f"  3. List it in tools.yml with addon_for: [{CONSUMER}] (see docs/BUILDING_AN_ADDON.md), then")
    print(f"     bin/bdtools install {tool} && bin/bdtools addon-check {addon_id}")
    return 0


def _find_addon(target: str) -> Path:
    p = Path(target).expanduser()
    if (p / "addon.json").is_file():
        return p.resolve()
    for name in (target, f"{target}_addon"):
        d = Path(tool_launch.tool_dir(name))
        if (d / "addon.json").is_file():
            return d
    sys.exit(f"addon-check: no add-on found for {target!r} (a path holding addon.json, a tools.yml "
             f"add-on name, or an id whose tool is <id>_addon)")


def addon_check(target: str, extra: list) -> int:
    addon_dir = _find_addon(target)
    try:
        plan = tool_launch.resolve(CONSUMER, 0)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"addon-check: {CONSUMER}, which runs add-ons and holds the contract check, "
                 f"is not runnable here: {e}")
    backend = Path(plan["dir"]) / "backend"
    if not (backend / "app" / "addon_check.py").is_file():
        sys.exit(f"addon-check: {plan['dir']} has no app/addon_check.py: that {CONSUMER} predates "
                 "pathogen add-ons. Update it first.")
    env = dict(plan["env"])
    env["PYTHONPATH"] = str(backend)
    argv = tool_launch._arch_prefix(plan["env_dir"]) + [plan["python"], "-m", "app.addon_check",
                                                       str(addon_dir), *extra]
    print(f"addon-check: {addon_dir} against {CONSUMER} at {plan['dir']}\n", flush=True)
    return subprocess.run(argv, cwd=str(backend), env=env).returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bdtools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new-addon", help="start a pathogen add-on from the template")
    n.add_argument("id")
    n.add_argument("--name", required=True)
    n.add_argument("--dest", default="")
    c = sub.add_parser("addon-check", help="check an add-on against the contract")
    c.add_argument("target")
    c.add_argument("--no-fixture", action="store_true")
    c.add_argument("--known-answer", action="store_true")
    c.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "new-addon":
        dest = Path(a.dest or f"{a.id}_addon").expanduser().resolve()
        return new_addon(a.id, a.name, dest)
    extra = [f for f, on in (("--no-fixture", a.no_fixture), ("--known-answer", a.known_answer),
                             ("--json", a.json)) if on]
    return addon_check(a.target, extra)


if __name__ == "__main__":
    raise SystemExit(main())
