#!/usr/bin/env python3
"""A tool's conda_setup/environment.yml against the environment that runs it.

`bdtools sync` moves a site's code and nothing else, and `bdtools update` only
rebuilds the managed checkouts it owns, so a release that adds a package to a
tool's spec reaches the CODE on every deployment and the ENVIRONMENT on none of
them until someone runs an installer by hand. The live case (2026-10-02, Ames
HPC): kraken_id_parse_gui's spec gained `plotly <6` in September, the env
predated it, and a Full identification ran its hour of Kraken2, SPAdes, BLAST
and alignment before dying at the report with "No module named 'plotly'" —
while the dashboard said "✓ Up to date", because every check it made compared
versions, and the versions were right.

This module answers one question — what does the spec declare that the env
lacks? — for both the dashboard (suite_common) and the CLI (complete-env.sh),
so the two cannot disagree about it. The spec is read from the checkout that
RUNS (tool_launch.tool_dir) and the env is the one a launch would pick
(packages.env_dir_for asks tool_launch.resolve, so a sandbox override, a shared
sibling env or a symlinked site env each count as what they are).

Stdlib only, like everything the dashboard imports: PyYAML is nothing its
interpreter can be assumed to have, so the spec reader covers the one shape
every tool's spec uses — `channels:` and `dependencies:` lists of `- item`
lines, with an optional `- pip:` sub-list. pip entries are skipped: conda-meta
cannot see them, and the installers' own pip step owns them.

    env_spec.py <tool> [--spec PATH] [--env PATH] [--field FIELD]
                [--record-blocked REASON] [--clear-blocked]

Without --field, one JSON object; with it, that field alone, lists one per line
(`missing` and `declared` print the spec strings, e.g. "plotly <6", which is
what conda wants to hear).
"""
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Where a package name ends and its constraint begins: "plotly <6", "dask>=2024.8",
# "python=3.10", "numpy~=1.26", "pysam[build=…]".
_NAME_END = re.compile(r"[=<>!~ \t\[]")


def _bdtools_home():
    """Mirror common.sh / tool_launch: $BDTOOLS_HOME, else the XDG default."""
    home = os.environ.get("BDTOOLS_HOME", "").strip()
    if not home:
        base = os.environ.get("XDG_DATA_HOME", "").strip() or os.path.expanduser("~/.local/share")
        home = os.path.join(base, "bdtools")
    return home


def read_spec(spec_path):
    """{"channels": [...], "declared": [(name, spec), ...]} from an environment.yml.

    `name` is the conda package name as conda-meta spells it (lowercase, no
    channel, no constraint); `spec` is the entry as written, minus quotes, which
    is what an install should ask for. Both lists are empty for an unreadable
    file.
    """
    try:
        with open(spec_path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return {"channels": [], "declared": []}
    channels, declared = [], []
    section, pip_indent = None, None
    for raw in lines:
        if raw.lstrip().startswith("#"):
            continue
        line = re.sub(r"\s+#.*$", "", raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0 and not stripped.startswith("- "):
            key = stripped.split(":", 1)[0].strip()
            section = key if key in ("channels", "dependencies") else None
            pip_indent = None
            continue
        if section is None or not stripped.startswith("- "):
            continue
        if pip_indent is not None and indent > pip_indent:
            continue                     # an entry of the pip: sub-list
        pip_indent = None
        item = stripped[2:].strip()
        if section == "channels":
            channel = item.strip("'\"")
            if channel:
                channels.append(channel)
            continue
        if item.endswith(":") and item.rstrip(":").strip().strip("'\"") == "pip":
            pip_indent = indent
            continue
        item = item.strip("'\"")
        bare = item.split("::", 1)[1] if "::" in item else item
        name = _NAME_END.split(bare, 1)[0].strip().lower()
        if name:
            declared.append((name, item))
    return {"channels": channels, "declared": declared}


def spec_path_for(tool):
    """<the checkout that runs>/conda_setup/environment.yml, or ''."""
    try:
        import tool_launch
        return os.path.join(tool_launch.tool_dir(tool), "conda_setup", "environment.yml")
    except Exception:
        return ""


def env_dir_for(tool):
    """The env a launch of `tool` would use, or '' (packages.env_dir_for)."""
    try:
        import packages
        return packages.env_dir_for(tool) or ""
    except Exception:
        return ""


def drift(tool, spec_path=None, env_dir=None):
    """What `tool`'s spec declares that its environment lacks.

    {"tool", "spec", "env_dir", "channels", "declared": [{name, spec}],
    "missing": [{name, spec}]}, or None when the question does not apply: no
    environment.yml (vsnp_gui builds its env from a bioconda package), no env
    resolves for the tool (nothing is installed — `install`, not a completion,
    is the remedy), or the env has no conda-meta to read.
    """
    spec_path = spec_path if spec_path is not None else spec_path_for(tool)
    if not spec_path or not os.path.isfile(spec_path):
        return None
    env_dir = env_dir if env_dir is not None else env_dir_for(tool)
    if not env_dir:
        return None
    try:
        import packages
        installed = {n.lower() for n in packages.installed_versions(env_dir)}
    except Exception:
        installed = set()
    if not installed:
        return None
    spec = read_spec(spec_path)
    declared = [{"name": n, "spec": s} for n, s in spec["declared"]]
    return {
        "tool": tool, "spec": spec_path, "env_dir": env_dir,
        "channels": spec["channels"], "declared": declared,
        "missing": [d for d in declared if d["name"] not in installed],
    }


# ----- what this machine has shown it cannot add -----------------------------
# A declared package that cannot be added without moving packages already
# installed is a property of the env, not a transient failure; offering it on
# every dashboard visit would be nagging about what the machine has already
# refused. The refusal is recorded against the exact set of missing names, so a
# spec that changes (a new package, or the old one dropped) is offered again,
# and `bdtools complete-env <tool>` from a terminal always retries.

def _blocked_path(tool):
    return os.path.join(_bdtools_home(), "state", f"{tool}.env-complete-blocked.json")


def record_blocked(tool, missing_names, reason):
    path = _blocked_path(tool)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"missing": sorted(missing_names), "reason": reason,
                       "when": time.strftime("%Y-%m-%d %H:%M")}, fh)
    except OSError:
        pass


def clear_blocked(tool):
    try:
        os.unlink(_blocked_path(tool))
    except OSError:
        pass


def blocked(tool, missing_names):
    """The recorded refusal for exactly this set of missing names, or None."""
    try:
        with open(_blocked_path(tool), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if sorted(rec.get("missing") or []) != sorted(missing_names):
        return None
    return rec


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tool")
    ap.add_argument("--spec", default=None, help="environment.yml to read (default: the tool's)")
    ap.add_argument("--env", default=None, help="env prefix to read (default: the one a launch uses)")
    ap.add_argument("--field", choices=["applies", "env_dir", "spec", "channels", "missing", "declared"])
    ap.add_argument("--record-blocked", metavar="REASON",
                    help="record that the missing set could not be added here")
    ap.add_argument("--clear-blocked", action="store_true",
                    help="forget a recorded refusal (after a successful completion)")
    a = ap.parse_args(argv)
    d = drift(a.tool, spec_path=a.spec, env_dir=a.env)
    if a.clear_blocked:
        clear_blocked(a.tool)
    if a.record_blocked is not None:
        record_blocked(a.tool, [m["name"] for m in (d or {}).get("missing", [])], a.record_blocked)
    if a.field == "applies":
        print("true" if d is not None else "false")
        return 0
    if d is None:
        if a.field:
            return 0                     # an empty answer, not an error
        print(json.dumps({"tool": a.tool, "applies": False}))
        return 0
    if a.field in ("missing", "declared"):
        for item in d[a.field]:
            print(item["spec"])
    elif a.field == "channels":
        for channel in d["channels"]:
            print(channel)
    elif a.field:
        print(d[a.field])
    else:
        print(json.dumps(dict(d, applies=True), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
