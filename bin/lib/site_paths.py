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
import re
import shlex
from pathlib import Path

# Env vars the umbrella exports into every tool. Tools read THESE, never a path.
ENV_SHARED_PROJECTS_ROOT = "BDTOOLS_SHARED_PROJECTS_ROOT"
ENV_DB_ROOT = "BDTOOLS_DB_ROOT"
ENV_TOOLS_ROOT = "BDTOOLS_TOOLS_ROOT"
ENV_SITE_ROOT = "BDTOOLS_SITE_ROOT"
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
        out[key] = _expand(parts[0] if parts else "", out)
    return out


# site.conf is *sourced as bash* by the installer, and site.conf.example teaches
# that idiom in its own defaults (SITE_ROOT=/srv/${SITE_NAME},
# TOOLS_ROOT=${SITE_ROOT}/tools, DB_ROOT=${SITE_ROOT}/databases). This module
# reads the same file as text, so without expanding those references a site that
# followed the example verbatim would hand tools the literal string
# "${SITE_ROOT}/tools" — a path that cannot exist, arrived at by doing exactly
# what the documentation showed.
_VAR_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def _expand(value: str, seen: dict) -> str:
    """Expand ${VAR}/$VAR against earlier keys in this file, then the environment.

    Bash-like in the ways that matter here: definitions resolve against what came
    before them, and an unknown name expands to empty. Substitution is applied to
    the replacement text too (bounded), so TOOLS_ROOT=${SITE_ROOT}/tools works when
    SITE_ROOT itself was written as /srv/${SITE_NAME}.
    """
    if "$" not in value:
        return value

    def sub(m):
        name = m.group(1) or m.group(2)
        if name in seen:
            return seen[name]
        return os.environ.get(name, "")

    for _ in range(8):                     # cheap cycle guard; nesting is shallow
        expanded = _VAR_RE.sub(sub, value)
        if expanded == value:
            break
        value = expanded
    return value


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


def site_root(repo_dir=None):
    """The deployment's top-level shared root, or None if this isn't a site install.

    Some tools organise several shared trees under one root rather than reading a
    path per feature — vsnp_gui is the example: references, the shared VCF-db
    folders, its sibling analysis env and the shared projects dir all hang off
    <site_root>. Those tools take a single env var, so the launcher needs one
    authoritative answer.

    Returns None rather than guessing. In particular it does NOT derive the root
    from tools_root's parent: TOOLS_ROOT is independently configurable, so
    `dirname(TOOLS_ROOT)` is a coincidence of the default layout, and a wrong root
    here is exactly the silent failure this module exists to prevent — the tool
    would look confidently in a directory that simply has nothing in it.
    """
    env = os.environ.get(ENV_SITE_ROOT, "").strip()
    if env:
        return Path(env)
    cfg = site_config(repo_dir)
    val = (cfg.get("SITE_ROOT") or "").strip()
    return Path(val) if val else None


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
    site = site_root(repo_dir)
    if site is not None:
        env[ENV_SITE_ROOT] = str(site)
    return env


if __name__ == "__main__":
    import json
    import sys
    repo = sys.argv[1] if len(sys.argv) > 1 else None
    shared = shared_projects_root(repo)
    site = site_root(repo)
    print(json.dumps({
        "bdtools_home": str(bdtools_home()),
        "site_root": str(site) if site else None,
        "tools_root": str(tools_root(repo)),
        "db_root": str(db_root(repo)),
        "shared_projects_root": str(shared) if shared else None,
        "site_config": site_config(repo),
    }, indent=2))
