#!/usr/bin/env python3
"""packages.py — which analysis package version each tool is actually running,
and whether the channel has a newer one.

The suite already tracks its GUI repos: `bdtools check-updates` compares each
checkout against the newest release tag, and the dashboard offers a button. The
science underneath was invisible. vsnp3, AMRFinderPlus, kraken2, mlst, IRMA and
GenoFLU are conda packages inside each tool's env, and nothing recorded, showed or
checked their versions — so "which vsnp3 produced this report?" had no answer
short of listing conda-meta by hand, and a new bioconda release could sit there
unnoticed indefinitely.

Two halves, both cheap:

  installed — read from <env>/conda-meta/<name>-<version>-<build>.json filenames.
              No conda invocation, so no solve: it is a directory listing.
  latest    — one GET to api.anaconda.org per package (public, unauthenticated),
              cached on disk. Also no solve, and it degrades to "unknown" offline
              rather than failing the caller.

The manifest pins each package exactly (`packages: [bioconda::vsnp3=3.35]`), which
is what makes two machines built a month apart agree. Before that, every tool
floor-pinned or left the version open (`mlst>=2.23`, plain `vsnp3`), so the version
you got depended on the day you built — visible right now as mlst 2.33.1 on one
install and 2.35.0 on another.

CLI:
  packages.py                 human-readable report for every tool
  packages.py --json          machine-readable records
  packages.py --no-network    installed versions only (skip the channel check)
  packages.py <tool> [...]    limit to these tools
"""
import json
import os
import platform
import re
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import manifest  # noqa: E402
import tool_launch  # noqa: E402

REPO_DIR = os.path.dirname(os.path.dirname(_HERE))
MANIFEST = os.environ.get("BDTOOLS_MANIFEST", os.path.join(REPO_DIR, "tools.yml"))
API = "https://api.anaconda.org/package/{channel}/{name}"
CACHE_TTL = 6 * 3600        # a bioconda release is not an hourly event
NET_TIMEOUT = 6.0


def _bdtools_home():
    return os.environ.get("BDTOOLS_HOME") or os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
        "bdtools")


def _cache_path():
    return os.path.join(_bdtools_home(), "cache", "package-versions.json")


def _unsat_path():
    return os.path.join(_bdtools_home(), "cache", "unsatisfiable.json")


def _platform_key():
    """This machine's identity for the unsatisfiable record.

    Keyed per platform because BDTOOLS_HOME can be shared (a group install on a
    cluster), and "this cannot be installed" is very often a platform fact rather
    than a universal one: mlst 2.34+ is noarch but depends on libxcrypt1, which has
    no macOS build, so it installs on Linux and can never install on a Mac.
    """
    return f"{platform.system()}-{platform.machine()}".lower()


def record_unsatisfiable(tool, package, version, reason=""):
    """Remember that <package>=<version> could not be installed for <tool> here.

    Written after a solve says no, so the dashboard stops offering an update that
    cannot succeed on this machine. Keyed by the exact version: when a newer one is
    released the key no longer matches and it is tried again — the record suppresses
    a known-bad answer, it does not give up on the package.
    """
    path = _unsat_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data.setdefault(_platform_key(), {})[f"{tool}/{package}"] = {
        "version": version, "reason": reason, "at": int(time.time()),
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def unsatisfiable_here():
    """{"tool/package": {"version","reason","at"}} recorded on this platform."""
    try:
        with open(_unsat_path(), encoding="utf-8") as fh:
            return json.load(fh).get(_platform_key(), {})
    except (OSError, ValueError, AttributeError):
        return {}


# ----- the manifest side ---------------------------------------------------
def parse_spec(spec):
    """'bioconda::vsnp3=3.35' -> ('bioconda', 'vsnp3', '3.35').

    Channel and version are both optional: 'vsnp3' and 'vsnp3=3.35' and
    'bioconda::vsnp3' all parse, so a half-written manifest entry degrades to
    less information rather than an exception.
    """
    spec = spec.strip()
    channel, _, rest = spec.rpartition("::")
    name, _, version = rest.partition("=")
    return channel or "bioconda", name.strip(), version.strip()


def declared(tool_records=None):
    """{tool: [(channel, name, pinned_version), ...]} from the manifest."""
    if tool_records is None:
        _, tool_records = manifest.parse(MANIFEST)
    out = {}
    for rec in tool_records:
        name = rec.get("name", "")
        specs = rec.get("packages") or []
        if isinstance(specs, str):
            specs = [specs] if specs else []
        out[name] = [parse_spec(s) for s in specs if s]
    return out


def held(tool_records=None):
    """{tool: {package, ...}} that must NOT be offered as updates.

    A package is held when a newer release exists but THIS env cannot take it —
    typically because another package in the same env pins an older
    perl/zlib/libcurl. amr_plus is the live example: `mlst` transitively holds
    perl and zlib down, which caps ncbi-amrfinderplus at 3.12.8 and kraken2 at
    2.1.3 in that env. Offering those upgrades produced a banner that could never
    be satisfied and an update that always failed, which trains people to ignore
    both. The version is still read and displayed — being held is not being hidden.
    """
    if tool_records is None:
        _, tool_records = manifest.parse(MANIFEST)
    out = {}
    for rec in tool_records:
        names = rec.get("packages_held") or []
        if isinstance(names, str):
            names = [names] if names else []
        out[rec.get("name", "")] = {n.strip() for n in names if n.strip()}
    return out


# ----- the installed side -------------------------------------------------
def env_dir_for(tool):
    """The env whose packages would ACTUALLY run this tool, or ''.

    Deliberately asks tool_launch.resolve rather than assuming <checkout>/env: a
    sandbox override, a shared sibling env or a personal conda env can each win,
    and reporting a version from an env the tool does not use would be worse than
    reporting nothing.
    """
    try:
        plan = tool_launch.resolve(tool, 0)
    except (RuntimeError, OSError):
        return ""
    env = plan.get("env_dir") or ""
    return "" if env in ("", "(base)") else env


def installed_versions(env_dir):
    """{package: version} from conda-meta filenames. {} if there is no env."""
    meta = os.path.join(env_dir or "", "conda-meta")
    try:
        entries = os.listdir(meta)
    except OSError:
        return {}
    out = {}
    for entry in entries:
        if not entry.endswith(".json"):
            continue
        stem = entry[:-5]
        parts = stem.rsplit("-", 2)      # name may contain '-' (snp-dists)
        if len(parts) != 3:
            continue
        name, version, _build = parts
        out[name] = version
    return out


# ----- the channel side ---------------------------------------------------
def _load_cache():
    try:
        with open(_cache_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, path)
    except OSError:
        pass                              # a cache we cannot write is not an error


def latest_version(channel, name, cache=None, use_network=True, now=None):
    """Newest version on the channel, or '' when unknown.

    '' means "could not find out" — offline, a typo'd package name, a channel
    outage. Callers must render that as unknown, never as "up to date": claiming a
    package is current because a network call failed is the failure mode worth
    avoiding here.
    """
    now = now if now is not None else time.time()
    key = f"{channel}/{name}"
    cache = cache if cache is not None else _load_cache()
    hit = cache.get(key)
    if hit and now - hit.get("at", 0) < CACHE_TTL:
        return hit.get("latest", "")
    if not use_network:
        return hit.get("latest", "") if hit else ""
    try:
        req = urllib.request.Request(
            API.format(channel=channel, name=name),
            headers={"Accept": "application/json",
                     "User-Agent": "bdtools-check-updates"},
        )
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
            payload = json.load(resp)
        latest = str(payload.get("latest_version") or "")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        # Keep serving a stale answer if we have one; it beats blanking the
        # version column every time the network hiccups.
        return hit.get("latest", "") if hit else ""
    cache[key] = {"latest": latest, "at": now}
    return latest


# ----- comparison ---------------------------------------------------------
def version_key(version):
    """Sortable key for a conda version string.

    Numeric runs compare numerically, so 3.35 < 3.36 and 2.9 < 2.10. Non-numeric
    runs compare as text, which is enough to keep 1.0rc1 below 1.0.
    """
    parts = []
    for chunk in re.findall(r"\d+|[A-Za-z]+", version or ""):
        parts.append((1, int(chunk), "") if chunk.isdigit() else (0, 0, chunk))
    return parts


def is_newer(candidate, current):
    """True only when `candidate` sorts strictly above `current`.

    A version that merely DIFFERS is not an update. Channels renumber (GenoFLU's
    1.03/1.07 line), and a locally patched or pre-release build can sit ahead of
    the channel; flagging those as "update available" would train people to ignore
    the badge.
    """
    if not candidate or not current:
        return False
    return version_key(candidate) > version_key(current)


# ----- the report ---------------------------------------------------------
def report(tools=None, use_network=True):
    """One record per declared package.

    Fields: tool, package, channel, pinned, installed, latest, update_available,
    status. `status` is the one-phrase summary the dashboards and the CLI share,
    so both cannot disagree about what a given combination means.
    """
    _, tool_records = manifest.parse(MANIFEST)
    wanted = set(tools) if tools else None
    specs = declared(tool_records)
    holds = held(tool_records)
    unsat = unsatisfiable_here()
    cache = _load_cache()
    out = []
    for rec in tool_records:
        tool = rec.get("name", "")
        if wanted and tool not in wanted:
            continue
        if not specs.get(tool):
            continue
        env = env_dir_for(tool)
        have = installed_versions(env)
        for channel, name, pinned in specs[tool]:
            inst = have.get(name, "")
            latest = latest_version(channel, name, cache=cache,
                                    use_network=use_network)
            is_held = name in holds.get(tool, set())
            # A version this machine has already PROVEN it cannot install counts as
            # held too, without needing a manifest edit — that record is per
            # platform, which a shared manifest cannot be.
            tried = unsat.get(f"{tool}/{name}") or {}
            blocked_version = tried.get("version", "")
            newer = is_newer(latest, inst) if inst else False
            if not env:
                status = "not installed"
            elif not inst:
                status = "declared but missing from the env"
            elif newer and latest and latest == blocked_version:
                status = (f"held at {inst} (newer: {latest} was tried here and "
                          f"cannot be installed)")
                newer = False
                is_held = True
            elif newer and is_held:
                status = f"held at {inst} (newer: {latest}; this env cannot take it)"
                newer = False
            elif newer:
                status = f"↑ {latest} available"
            elif not latest:
                status = "up to date (channel not checked)"
            else:
                status = "up to date"
            out.append({
                "tool": tool,
                "package": name,
                "channel": channel,
                "pinned": pinned,
                "installed": inst,
                "latest": latest,
                "update_available": bool(newer),
                "held": is_held,
                "env": env,
                "status": status,
                # A pin that does not match what is installed means this env was
                # built before the pin, or by something that ignored it. Worth
                # surfacing: it is the difference between a reproducible install
                # and a coincidence.
                "pin_drift": bool(pinned and inst and pinned != inst),
            })
    _save_cache(cache)
    return out


def by_tool(records):
    out = {}
    for rec in records:
        out.setdefault(rec["tool"], []).append(rec)
    return out


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    as_json = "--json" in argv
    use_network = "--no-network" not in argv
    records = report(args or None, use_network=use_network)
    if as_json:
        json.dump(records, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if not records:
        print("no analysis packages are declared in tools.yml "
              "(add e.g. `packages: [bioconda::vsnp3=3.35]` to a tool)")
        return 0
    for tool, recs in by_tool(records).items():
        print(tool)
        for rec in recs:
            drift = "  (pinned %s)" % rec["pinned"] if rec["pin_drift"] else ""
            print("    %-22s installed=%-12s latest=%-12s %s%s" % (
                "%s (%s)" % (rec["package"], rec["channel"]),
                rec["installed"] or "—", rec["latest"] or "?",
                rec["status"], drift))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
