#!/usr/bin/env python3
"""config_hygiene.py — evict ANOTHER deployment's paths from this machine's
per-tool config.json.

WHY THIS EXISTS, AND WHY REMOVING THE LITERAL FROM THE CODE WAS NOT ENOUGH.

Every GUI keeps a per-user `~/.config/<tool>/config.json`, seeded from its own
`DEFAULTS` the first time it starts. Several tools used to compute a default by
naming a site path ("/srv/kapurlab/databases/genoflu/dependencies"). On the
machine that path was written for it is correct; anywhere else the tool wrote a
foreign literal into the user's config and then handed it to every analysis run.

Those defaults have been removed one tool at a time. That fixes the NEXT config
to be created and nothing else: `load_config()` only `setdefault`s missing keys,
so a config.json that already holds the foreign value keeps it forever. A real
run on 2026-08-24 shows exactly that — irma_gui updated, and its pipeline still
invoked with `--genoflu-db /srv/kapurlab/databases/genoflu/dependencies`,
because the value was persisted before the default was fixed:

    WARNING: configured genoflu_db has no fastas/ + genotype_key.xlsx
    (/srv/kapurlab/databases/genoflu/dependencies); falling back to ...

A stale path is not merely untidy. It is a run whose provenance names a
directory that does not exist here, and a warning nobody can act on. When the
tool does NOT degrade gracefully it is worse: the feature silently does nothing.

So the umbrella repairs it, at launch, for every tool at once. That matters
more than fixing each tool's DEFAULTS again: bdtools reaches a machine on the
next `git pull`, while a per-tool fix needs a release, a pin bump and an env
rebuild on every install before it helps anyone.

THE RULE. A configured value is foreign when all three hold:

  1. it is an absolute path,
  2. nothing exists at it on this machine, AND its whole site-specific branch is
     absent too — the deepest ancestor that does exist is a bare top-level
     directory such as /srv, /home or /mnt, and
  3. it is not under any root this machine legitimately owns — home,
     BDTOOLS_HOME, and the resolved tools/db/site/shared-projects roots
     (site_paths.py).

Clauses 2 and 3 are the safety valves, and they are the reason this is not just
an `os.path.exists` check. Plenty of configured directories do not exist yet and
are perfectly correct: a projects root the tool creates on first use, a scratch
tree on a filesystem the login node has not touched. What separates those from
another site's layout is how much of the path is missing. `/work/tstuber/projects`
on a machine where `/work/tstuber` exists is a directory waiting to be created;
`/srv/kapurlab/databases/genoflu/dependencies` on a machine where even
`/srv/kapurlab` does not exist is somebody else's filesystem.

Clause 3 then covers the case clause 2 cannot see. On the lab server SITE_ROOT
really is /srv/kapurlab, so that same path is under a local root and is left
alone even while an NFS mount is briefly away — precisely when a naive "it isn't
there, delete it" would destroy a correct configuration.

NOTHING IS DELETED. An offending value is moved to the `_bdtools_foreign_paths`
key, which records what was removed and when. The live key is emptied, which
every tool already treats as "not configured" (its documented meaning), so the
tool falls back to its own default instead of a path that cannot resolve. A site
that really does own the path can put it back by hand, and the record says what
it was.

Dependency-free (stdlib only) so it runs under any tool env's python.
"""
import json
import os
import tempfile
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import site_paths
except ImportError:                       # pragma: no cover — direct execution
    import sys
    sys.path.insert(0, _HERE)
    import site_paths

# Where the removed values are parked. Read by doctor, never by a tool.
QUARANTINE_KEY = "_bdtools_foreign_paths"

# Directories that are ephemeral by design: something configured under one is
# expected to vanish, and its absence says nothing about which site wrote it.
# `tempfile.gettempdir()` is in here because the platform temp dir is not always
# /tmp — macOS hands each user a private /var/folders/<hash>/T, and a tool that
# had parked a scratch path there was reported as a foreign-site path on the
# first run of this check.
_EPHEMERAL_FIXED = ("/tmp", "/var/tmp", "/private/tmp", "/dev/shm", "/var/folders")


def _ephemeral_roots():
    roots = list(_EPHEMERAL_FIXED)
    for candidate in (tempfile.gettempdir(), os.environ.get("TMPDIR", "")):
        candidate = (candidate or "").strip()
        if candidate.startswith("/"):
            roots.append(candidate)
    return tuple(sorted(set(roots)))


def config_path(tool):
    """Mirror every tool's own config.py: $XDG_CONFIG_HOME, else ~/.config."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / tool / "config.json"


def local_roots(repo_dir=None):
    """Absolute prefixes this machine legitimately owns.

    Resolved, never assumed — site_paths reads an env var, then the per-machine
    site file, then a defensible derivation. A site that declares SITE_ROOT gets
    its own tree back here, which is what keeps clause 3 from evicting a correct
    configuration during a mount blip.
    """
    roots = []

    def add(value):
        if not value:
            return
        try:
            p = Path(str(value))
        except (TypeError, ValueError):
            return
        if p.is_absolute():
            roots.append(os.path.normpath(str(p)))

    add(Path.home())
    add(site_paths.bdtools_home())
    add(site_paths.tools_root(repo_dir))
    add(site_paths.db_root(repo_dir))
    add(site_paths.site_root(repo_dir))
    add(site_paths.shared_projects_root(repo_dir))
    # An explicit BDTOOLS_TOOLSDIR is a root by definition: it is the tree the
    # launcher was told to run tools from.
    add(os.environ.get("BDTOOLS_TOOLSDIR", "").strip())
    return sorted(set(roots))


def _under(value, root):
    """True when `value` is `root` or lives beneath it. Pure string containment
    on normalised paths — no resolve(), which would touch a filesystem we have
    just established may not answer."""
    value = os.path.normpath(value)
    root = os.path.normpath(root)
    return value == root or value.startswith(root.rstrip(os.sep) + os.sep)


def _present_branch_depth(value):
    """Path depth of the deepest ancestor of `value` that exists.

    0 means nothing above it exists at all; 1 means only a bare top-level
    directory does (/srv, /home, /mnt — present on almost every Linux box and
    saying nothing about this site). An unreadable ancestor counts as present:
    a permission error is not evidence that a path belongs to another machine.
    """
    parts = [p for p in os.path.normpath(value).split(os.sep) if p]
    for i in range(len(parts) - 1, -1, -1):
        candidate = os.sep + os.sep.join(parts[:i + 1])
        try:
            if os.path.exists(candidate):
                return i + 1
        except OSError:
            return i + 1
    return 0


def is_foreign(value, roots):
    """Apply the three-clause rule to one configured value."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    # Clause 1. Relative paths, names, URLs and plain settings are not ours to
    # judge; only an absolute path can name another machine's layout.
    if not value.startswith("/"):
        return False
    if any(_under(value, e) for e in _ephemeral_roots()):
        return False
    # Clause 2. Something is there — whatever wrote it, it resolves here.
    try:
        if os.path.exists(value):
            return False
    except OSError:
        # An unreadable parent is not evidence of a foreign path.
        return False
    # Clause 2 (continued): how much of the path is missing. A value whose
    # parent tree is present is a directory that has not been created yet, which
    # is a normal and correct state for a projects/scratch root — evicting those
    # would make this check a nuisance rather than a repair.
    if _present_branch_depth(value) > 1:
        return False
    # Clause 3.
    return not any(_under(value, r) for r in roots)


def _sweep_value(node, roots, trail, found):
    """Walk a decoded config, returning the cleaned node and appending findings.

    A TOP-LEVEL key is emptied (""), never deleted. That distinction is the
    whole point: every tool's `load_config()` ends with `cfg.setdefault(k, v)`
    over its own DEFAULTS, so deleting the key invites the tool to write the
    same foreign literal straight back on the next start — on any tool whose
    DEFAULTS have not been fixed yet, which is exactly the population this
    repair exists for. "" is what every tool already documents as "not
    configured", and setdefault leaves it alone.

    List members and nested mapping entries are dropped instead: nothing
    re-seeds them, and a stale element in a list of saved roots has no empty
    form that means anything.
    """
    if isinstance(node, str):
        if is_foreign(node, roots):
            found.append({"key": ".".join(trail), "value": node})
            return ""
        return node
    if isinstance(node, list):
        out = []
        for i, item in enumerate(node):
            if isinstance(item, str):
                if is_foreign(item, roots):
                    found.append({"key": "%s[%d]" % (".".join(trail), i), "value": item})
                    continue
                out.append(item)
            else:
                out.append(_sweep_value(item, roots, trail + ["[%d]" % i], found))
        return out
    if isinstance(node, dict):
        top_level = not trail
        out = {}
        for k, v in node.items():
            if isinstance(v, str):
                if is_foreign(v, roots):
                    found.append({"key": ".".join(trail + [str(k)]), "value": v})
                    if top_level:
                        out[k] = ""
                    continue
                out[k] = v
            else:
                out[k] = _sweep_value(v, roots, trail + [str(k)], found)
        return out
    return node


def scan(tool, repo_dir=None, roots=None):
    """Findings for one tool, changing nothing. [] when the config is clean or
    absent. Every value is walked, at any depth: a per-key allowlist would go
    stale the first time a tool adds a setting, and this class of bug is exactly
    the one nobody remembers to re-declare."""
    path = config_path(tool)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(cfg, dict):
        return []
    cfg.pop(QUARANTINE_KEY, None)      # already removed; not a fresh finding
    found = []
    _sweep_value(cfg, local_roots(repo_dir) if roots is None else roots, [], found)
    return found


def quarantined(tool):
    """What a previous sweep removed for this tool: (removed_at, [entries]).

    Doctor reports this rather than only reporting values still present. By the
    time anyone runs doctor the launcher has usually already repaired the config
    — the finding is gone and the fact that a run's configuration was silently
    changed would go unmentioned. This is what keeps it on the report."""
    try:
        cfg = json.loads(config_path(tool).read_text(encoding="utf-8"))
        record = cfg.get(QUARANTINE_KEY) or {}
        return record.get("removed_at", ""), list(record.get("removed") or [])
    except (OSError, ValueError, AttributeError, TypeError):
        return "", []


def sweep(tool, repo_dir=None, apply=True, roots=None):
    """Scan and (by default) repair one tool's config. Returns the findings.

    Idempotent: after a repair the values are gone from the live keys, so the
    next call reads a clean config and returns []. Failure to write is not fatal
    — a read-only config is a reason to warn, never a reason to block a launch.
    """
    path = config_path(tool)
    try:
        raw = path.read_text(encoding="utf-8")
        cfg = json.loads(raw)
    except (OSError, ValueError):
        return []
    if not isinstance(cfg, dict):
        return []
    previous = cfg.pop(QUARANTINE_KEY, None)
    found = []
    cleaned = _sweep_value(cfg, local_roots(repo_dir) if roots is None else roots, [], found)
    if not found:
        if previous is not None:       # keep the record; nothing new to do
            cleaned[QUARANTINE_KEY] = previous
            _write(path, cleaned)
        return []
    record = previous if isinstance(previous, dict) else {}
    record.update({
        "removed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": ("Absolute paths that do not exist on this machine and are "
                 "under none of its roots: written by another deployment's "
                 "defaults. Removed by bdtools so runs stop being handed them. "
                 "Restore by hand if this machine really does own them."),
        "removed": (record.get("removed") or []) + found,
    })
    cleaned[QUARANTINE_KEY] = record
    if apply:
        _write(path, cleaned)
    return found


def _write(path, cfg):
    """Write the config back the way the tools do (indent=2, sorted), via a
    temp file in the same directory so an interrupted write cannot truncate a
    working config."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".bdtools-tmp")
        tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        return False
    return True


def describe(tool, found):
    """One launch-time warning line per tool, naming the keys and the remedy."""
    if not found:
        return ""
    keys = ", ".join("%s=%s" % (f["key"], f["value"]) for f in found)
    return ("%s: removed %d configured path(s) belonging to another deployment "
            "(%s). Nothing here resolves them, so runs were being handed a "
            "directory that does not exist. The tool now falls back to its own "
            "default; the old values are kept under '%s' in %s."
            % (tool, len(found), keys, QUARANTINE_KEY, config_path(tool)))


def _cli():
    import argparse
    ap = argparse.ArgumentParser(
        prog="bdtools check-paths",
        description="Report (--apply: remove) paths in the GUIs' per-user "
                    "config that belong to another deployment and resolve to "
                    "nothing on this machine.")
    ap.add_argument("tools", nargs="*", help="tool names (default: every config found)")
    ap.add_argument("--apply", action="store_true", help="repair (default: report only)")
    ap.add_argument("--repo-dir", default=os.path.dirname(os.path.dirname(_HERE)))
    args = ap.parse_args()

    tools = args.tools
    if not tools:
        base = config_path("x").parent.parent
        tools = sorted(p.parent.name for p in base.glob("*/config.json")) if base.is_dir() else []

    roots = local_roots(args.repo_dir)
    print("this machine's roots: " + (", ".join(roots) or "(none resolved)"))
    total = 0
    for tool in tools:
        found = sweep(tool, args.repo_dir, apply=args.apply, roots=roots)
        total += len(found)
        for f in found:
            print("  %-22s %-28s %s" % (tool, f["key"], f["value"]))
    if not total:
        print("no foreign paths configured.")
    elif not args.apply:
        print("\nreport only — rerun with --apply to remove them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
