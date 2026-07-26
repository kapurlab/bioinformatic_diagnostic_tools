#!/usr/bin/env python3
"""site_paths.py — resolve deployment-specific paths WITHOUT hard-coding any.

The suite runs on macOS, WSL2, plain Linux, a shared lab server, and OOD. A path
literal in code is only ever right on the machine it was written for, and it
fails *silently*: the directory simply isn't there, so the feature quietly does
nothing. Two real examples this module exists to prevent:

  * amr_plus_gui resolved its sibling MLST runner as /srv/kapurlab/tools/mlst_gui,
    so on every non-lab-server machine the organism cross-check was skipped with a
    log line that read like an expected state.
  * Several tools defaulted a database path to /srv/kapurlab/databases/..., which
    on a Mac shows up in Settings looking configured while pointing at a directory
    that can never exist.

So: **no site path appears in code.** Every value below is resolved from, in
order of authority:

  1. an environment variable — what a launcher or an OOD job script exports
  2. a recorded site file — written once per machine by the installer
     (`bdtools setup-databases` already does exactly this for the database root)
  3. a derivation from something we genuinely know, such as this checkout's own
     location or the XDG data dir
  4. nothing — return None and let the caller degrade honestly

There is deliberately no step 5 "…and otherwise assume the Kapur Lab layout".
A site supplies its own values via `sites/site.conf` (see site.conf.example,
which already declares itself "the ONE place site-specific values live") or via
`<BDTOOLS_HOME>/site.conf`, which `write_site_file()` records.

Dependency-free (stdlib only) so it runs under any tool env's python.
"""
import os
import shlex
from pathlib import Path

# Env vars the umbrella exports into every tool. Tools read THESE, never a path.
ENV_SHARED_PROJECTS_ROOT = "BDTOOLS_SHARED_PROJECTS_ROOT"
ENV_DB_ROOT = "BDTOOLS_DB_ROOT"
ENV_TOOLS_ROOT = "BDTOOLS_TOOLS_ROOT"
ENV_HOME = "BDTOOLS_HOME"

_SITE_FILE = "site.conf"
_DB_ROOT_FILE = "db-root"


def bdtools_home() -> Path:
    """Mirror common.sh: $BDTOOLS_HOME, else the XDG-friendly per-user default."""
    home = os.environ.get(ENV_HOME, "").strip()
    if home:
        return Path(home)
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "bdtools"


def _read_site_file(path: Path) -> dict:
    """Parse a `KEY=value` shell-style site file. Tolerant: a malformed line is
    skipped rather than taken as a reason to fall back to a guessed path."""
    out = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key.replace("_", "").isalnum():
            continue
        try:
            parts = shlex.split(val, comments=True)
        except ValueError:
            continue
        out[key] = parts[0] if parts else ""
    return out


def site_config(repo_dir=None) -> dict:
    """Site values, nearest-first: the per-machine record, then the repo's
    sites/site.conf (what an admin edits before `install --server`)."""
    merged = {}
    if repo_dir:
        merged.update(_read_site_file(Path(repo_dir) / "sites" / _SITE_FILE))
    merged.update(_read_site_file(bdtools_home() / _SITE_FILE))
    return merged


def shared_projects_root(repo_dir=None):
    """The multi-user projects root, or None when this deployment has none.

    Returns None — never Path("") — because Path("") is Path("."): an "unset"
    sentinel that silently means "the current working directory" would turn a
    missing shared root into project lookups against wherever uvicorn was started.

    A laptop legitimately has no shared root. That is not an error state.
    """
    env = os.environ.get(ENV_SHARED_PROJECTS_ROOT)
    if env is not None:                      # explicit, including "" = disabled
        env = env.strip()
        return Path(env) if env else None
    cfg = site_config(repo_dir)
    for key in ("SHARED_PROJECTS_ROOT", "PROJECTS_ROOT"):
        val = (cfg.get(key) or "").strip()
        if val:
            return Path(val)
    site_root = (cfg.get("SITE_ROOT") or "").strip()
    if site_root:
        return Path(site_root) / "projects"
    return None


def db_root(repo_dir=None) -> Path:
    """Where reference databases live on THIS machine.

    `bdtools setup-databases` asks once and records the answer in
    <BDTOOLS_HOME>/db-root. That recorded value is the authority; the fallback is
    a per-user directory, never a site layout."""
    env = os.environ.get(ENV_DB_ROOT, "").strip()
    if env:
        return Path(env)
    try:
        recorded = (bdtools_home() / _DB_ROOT_FILE).read_text(encoding="utf-8").strip()
        if recorded:
            return Path(recorded)
    except OSError:
        pass
    cfg = site_config(repo_dir)
    for key in ("DB_ROOT", "DATABASES_ROOT"):
        val = (cfg.get(key) or "").strip()
        if val:
            return Path(val)
    site_root = (cfg.get("SITE_ROOT") or "").strip()
    if site_root:
        return Path(site_root) / "databases"
    return Path.home() / "databases"


def tools_root(repo_dir=None) -> Path:
    """The directory that CONTAINS the tool checkouts, so a sibling is
    <tools_root>/<tool>. Lets one tool find another without assuming a layout."""
    env = os.environ.get(ENV_TOOLS_ROOT, "").strip()
    if env:
        return Path(env)
    cfg = site_config(repo_dir)
    val = (cfg.get("TOOLS_ROOT") or "").strip()
    if val:
        return Path(val)
    site_root = (cfg.get("SITE_ROOT") or "").strip()
    if site_root:
        return Path(site_root) / "tools"
    return bdtools_home() / "checkouts"


def write_site_file(values: dict, path=None) -> Path:
    """Record site values for this machine, so behaviour comes from configuration
    rather than from a literal someone has to remember to change."""
    target = Path(path) if path else (bdtools_home() / _SITE_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_site_file(target)
    existing.update({k: v for k, v in values.items() if v})
    body = ["# bdtools site paths — written by the installer.",
            "# Deployment-specific values live HERE, not in code, so the same",
            "# release runs unmodified on macOS, WSL, Linux and OOD.",
            ""]
    body += [f"{k}={shlex.quote(str(v))}" for k, v in sorted(existing.items())]
    target.write_text("\n".join(body) + "\n", encoding="utf-8")
    return target


def as_env(repo_dir=None) -> dict:
    """The env a tool should be launched with. Only set what we actually resolved;
    an unset variable means "not configured", which the tool handles."""
    env = {ENV_TOOLS_ROOT: str(tools_root(repo_dir)),
           ENV_DB_ROOT: str(db_root(repo_dir)),
           ENV_HOME: str(bdtools_home())}
    shared = shared_projects_root(repo_dir)
    if shared is not None:
        env[ENV_SHARED_PROJECTS_ROOT] = str(shared)
    return env


if __name__ == "__main__":
    import json
    import sys
    repo = sys.argv[1] if len(sys.argv) > 1 else None
    shared = shared_projects_root(repo)
    print(json.dumps({
        "bdtools_home": str(bdtools_home()),
        "tools_root": str(tools_root(repo)),
        "db_root": str(db_root(repo)),
        "shared_projects_root": str(shared) if shared else None,
        "site_config": site_config(repo),
    }, indent=2))
