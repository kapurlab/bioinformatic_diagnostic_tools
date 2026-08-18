#!/usr/bin/env python3
"""check.py — verify one installed tool against its requirements spec.

Run by `bdtools doctor` (scope=all) and by the build-time self-check in
install-local.sh (scope=env). Prints a plain-language report — what's wrong and
the exact command to fix it — for users who don't read tracebacks.

  check.py --tool NAME --dir DIR [--python ENV_PY] [--scope env|all]

--python is the tool's env interpreter (so module imports run in the env, not
base). If omitted/empty the env is treated as not built. Exit code: 0 if nothing
in scope failed (skips/notes don't count), 1 otherwise.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements  # noqa: E402

G, Y, R, B, X = (
    ("\033[32m", "\033[33m", "\033[31m", "\033[1m", "\033[0m")
    if sys.stdout.isatty() else ("", "", "", "", "")
)
OK, BAD, SKIP = f"{G}✓{X}", f"{R}✗{X}", f"{Y}–{X}"


def config_value(tool, key):
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    p = base / tool / "config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get(key, "")
    except Exception:
        return ""


# Import-name → pip-name for the web layer the installers pip-install into each
# tool env (backend/requirements.txt). These are NOT analysis packages: when
# ONLY these are missing — the classic remnant of an env rebuild that died
# before its pip step — the cure is a targeted pip install into the EXISTING
# env, not a rebuild. A rebuild re-solves every conda package (the operation
# that broke a working kraken env on macOS once already) to close a 20-second
# gap, and `bdtools update` refuses it for report-only tools anyway, which
# left the card recommending a command that could not run.
PIP_WEB_MODULES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "aiofiles": "aiofiles",
    "multipart": "python-multipart",
}


def web_layer_fix(env_py, tool_dir, missing):
    """The targeted remedy when every missing module is pip-owned web layer.

    Prefers the tool's own backend/requirements.txt (exactly what a healthy
    install would have pip-installed); the missing packages are also named
    explicitly so an older checkout whose requirements.txt predates a
    declaration (e.g. python-multipart) still ends up complete. Returns None
    when any missing module is an analysis package — that is a real env
    problem and keeps the rebuild remedy."""
    if not missing or any(m not in PIP_WEB_MODULES for m in missing):
        return None
    pkgs = " ".join(sorted({PIP_WEB_MODULES[m] for m in missing}))
    req = Path(tool_dir, "backend", "requirements.txt") if tool_dir else None
    if req and req.is_file():
        return (f'"{env_py}" -m pip install -r "{req}" {pkgs}'
                "   # restores the web layer; analysis packages untouched")
    return (f'"{env_py}" -m pip install {pkgs}'
            "   # restores the web layer; analysis packages untouched")


def check_modules(env_py, modules):
    """Return the subset of modules that fail to import in the tool's env.

    Actually imports each module in the env interpreter (a real "does it work"
    test, not just "is it discoverable"). Each import is guarded independently so
    one failure doesn't hide the rest, and the script always completes and prints
    the failures. (An earlier version used `import importlib` + `importlib.util`,
    but `import importlib` does not expose the `util` submodule — it raised
    AttributeError, so the check silently passed while testing nothing, and could
    flip to "all missing" when the interpreter path wasn't runnable.)
    """
    if not modules:
        return []
    code = (
        "import sys\n"
        "bad=[]\n"
        "for m in sys.argv[1:]:\n"
        "    try:\n"
        "        __import__(m)\n"
        "    except Exception:\n"
        "        bad.append(m)\n"
        "print('\\n'.join(bad))\n"
    )
    try:
        out = subprocess.run([env_py, "-c", code, *modules],
                             capture_output=True, text=True, timeout=120)
        # If the interpreter couldn't even start the script (nonzero exit with no
        # output), we can't say which imports failed — report all as missing.
        if out.returncode != 0 and not out.stdout.strip():
            return list(modules)
        return [m for m in out.stdout.split() if m]
    except Exception:
        return list(modules)  # can't even run the interpreter -> all "missing"


def has_binary(name, env_bin, extra_dirs=()):
    """Is `name` runnable? Searches <env>/bin, any vendored asset dirs, then PATH.

    extra_dirs carries the resolved `asset_dirs` from the tool's spec — vendored
    payloads like ksnp_gui's kSNP4 that conda never installs. Without them the
    check searched only places those binaries are guaranteed NOT to be, and
    reported the tool healthy while it could not run at all."""
    dirs = ([env_bin] if env_bin else []) + [d for d in extra_dirs if d]
    for d in dirs:
        cand = Path(d) / name
        if cand.exists() and os.access(cand, os.X_OK):
            return True
    search = dirs + [os.environ.get("PATH", "")]
    return shutil.which(name, path=os.pathsep.join(p for p in search if p)) is not None


def find_binary(name, env_bin, extra_dirs=()):
    """Absolute path `name` would resolve to, searched exactly like has_binary."""
    dirs = ([env_bin] if env_bin else []) + [d for d in extra_dirs if d]
    for d in dirs:
        cand = Path(d) / name
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    search = dirs + [os.environ.get("PATH", "")]
    return shutil.which(name, path=os.pathsep.join(p for p in search if p))


# Executable-format magic bytes, plus the CPU field each format carries:
# ELF e_machine (offset 0x12, uint16 LE) and Mach-O cputype (offset 4, uint32 LE).
# The OS alone is not enough — see check_binary_format.
_ELF_MAGIC = b"\x7fELF"
_MACHO_THIN = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe")
_MACHO_FAT = b"\xca\xfe\xba\xbe"
_ELF_MACHINES = {0x03: "i386", 0x3E: "x86_64", 0xB7: "arm64", 0x28: "arm"}
_MACHO_CPUS = {0x00000007: "i386", 0x01000007: "x86_64", 0x0100000C: "arm64"}


def _binary_target(path):
    """(os, arch) a binary targets, or None if not a recognised executable."""
    import struct
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 20 or head[:2] == b"#!":
        return None              # unreadable, tiny, or a script (portable)
    magic = head[:4]
    if magic == _ELF_MAGIC:
        m = struct.unpack("<H", head[18:20])[0]
        return "linux", _ELF_MACHINES.get(m, "unknown")
    if magic in _MACHO_THIN:
        c = struct.unpack("<I", head[4:8])[0]
        return "macos", _MACHO_CPUS.get(c, "unknown")
    if magic == _MACHO_FAT:
        # Universal binary: assume the loader finds a usable slice rather than
        # parsing the fat header. Permissive on purpose — this guards against the
        # obvious mistake, it is not a loader.
        return "macos", "universal"
    return None


def _host_target():
    os_name = {"Linux": "linux", "Darwin": "macos"}.get(
        platform.system(), platform.system().lower())
    m = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64"}.get(m, m)
    return os_name, arch


def _rosetta_available():
    """Can this Apple Silicon host run x86_64 binaries?"""
    try:
        return subprocess.run(["/usr/bin/arch", "-x86_64", "/usr/bin/true"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10).returncode == 0
    except Exception:
        return False


def check_binary_format(name, env_bin, extra_dirs=()):
    """Can this host actually exec `name`? Returns (verdict, detail).

    verdict: True (runnable) | False (not runnable) | None (nothing to judge).

    A binary resolving on PATH does not mean it runs. ksnp_gui's kSNP4 payload is
    downloaded by hand from SourceForge, and a macOS host that got the Linux
    archive satisfied every existence check here, was then SKIPped by the old
    `os: linux` gate, and finally failed inside a real analysis with
    "OSError: [Errno 8] Exec format error" — with a run directory already on disk.

    OS *and* CPU are both checked, because the OS alone would have let the same
    class of bug through on ARM: kSNP4 ships x86_64 only, so an aarch64 Linux host
    (ARM WSL, Graviton) fails identically with no translation layer to save it,
    while Apple Silicon succeeds via Rosetta 2 — a distinct state with a distinct
    remedy, so it is reported separately rather than as a wrong-OS payload.

    Read from the header rather than by exec'ing: these binaries take positional
    arguments and would block on stdin or write into the cwd.
    """
    path = find_binary(name, env_bin, extra_dirs)
    if not path:
        return None, ""          # absence is the existence check's job, not ours
    target = _binary_target(path)
    if target is None:
        return None, ""
    bin_os, bin_arch = target
    host_os, host_arch = _host_target()
    if host_os not in ("linux", "macos"):
        return None, ""          # no rules for this platform; don't invent one

    pretty = ("Linux (ELF)" if bin_os == "linux" else "macOS (Mach-O)") + f" {bin_arch}"
    if bin_os != host_os:
        # Host named from the same derivation the decision used, so the message
        # can never contradict its own verdict.
        host_pretty = {"linux": "Linux", "macos": "macOS"}[host_os]
        return False, (f"{name} was built for {pretty} but this host is "
                       f"{host_pretty} {host_arch} ({path})")
    if bin_arch in ("universal", host_arch):
        return True, path
    if host_os == "macos" and host_arch == "arm64" and bin_arch == "x86_64":
        if _rosetta_available():
            return True, path
        return False, (f"{name} is an Intel (x86_64) binary and Rosetta 2 is not "
                       f"installed — fix: softwareupdate --install-rosetta "
                       f"--agree-to-license ({path})")
    if host_os == "linux" and host_arch == "arm64" and bin_arch == "x86_64":
        return False, (f"{name} is x86_64 and this is ARM (aarch64) Linux, which "
                       f"has no x86 translation layer — no build of this tool can "
                       f"run here ({path})")
    return False, (f"{name} is {bin_arch} but this host is {host_arch} ({path})")


def resolve_asset_dirs(tool, tool_dir, asset_dirs):
    """Resolve a spec's `asset_dirs` the same way the launcher builds PATH.

    Delegates to tool_launch._resolve_asset_dir so there is exactly one resolution
    rule (tool tree -> installed checkout -> machine-wide vendor cache) and doctor
    can never report a state the launcher wouldn't produce. Returns
    (found_dirs, missing_rel_paths)."""
    if not asset_dirs or not tool_dir:
        return [], list(asset_dirs or [])
    try:
        import tool_launch  # sibling module, stdlib-only
    except Exception:
        # Never let a doctor run die over this — fall back to a plain lookup.
        found = [os.path.join(tool_dir, r) for r in asset_dirs
                 if os.path.isdir(os.path.join(tool_dir, r))]
        missing = [r for r in asset_dirs if not os.path.isdir(os.path.join(tool_dir, r))]
        return found, missing
    found, missing = [], []
    for rel in asset_dirs:
        path, _tried = tool_launch._resolve_asset_dir(tool, rel, tool_dir)
        (found.append(path) if path else missing.append(rel))
    return found, missing


def _expand(s):
    """Expand $VAR, ${VAR}, and ${VAR:-fallback} against the environment."""
    import re
    def repl(m):
        var, fb = m.group(1), m.group(2)
        return os.environ.get(var) or (fb if fb is not None else "")
    s = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", repl, s)
    return os.path.expandvars(s)


def _paths_file_roots(env_bin, rel):
    """Directories listed in a tool's runtime paths file, if it has one.

    Some references are located by a FILE the analysis package reads at run time
    (vsnp3: <env>/dependencies/reference_options_paths.txt), not by a config key.
    That file is the authority — it is what vsnp3 and the GUI both consult — so a
    config key pointing somewhere else does not make the references missing. A
    stale key from a previous local install had doctor reporting "vSNP reference
    options missing" on a site with 28 reference sets loaded and visible in the
    GUI, and offering to download a second copy into the wrong place.
    """
    if not rel or not env_bin:
        return []
    path = Path(env_bin).parent / rel
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def check_db(tool, db, env_bin=""):
    # What the tool actually reads at run time wins over what a config key says.
    for root in _paths_file_roots(env_bin, db.get("paths_file", "")):
        p = Path(root)
        try:
            if p.is_dir() and any(p.iterdir()):
                return True, str(p)
        except OSError:
            continue
    # The tool uses the configured path if set, else a computed default (which
    # is what config.py falls back to when the key was never written). Check
    # whichever the tool would actually use.
    val = config_value(tool, db["config_key"])
    if not val and db.get("default"):
        val = _expand(db["default"])
    if not val:
        return False, f"(no path set under '{db['config_key']}')"
    p = Path(val)
    kind = db["kind"]
    if kind == "dir":
        ok = p.is_dir() and any(p.iterdir()) if p.is_dir() else False
    elif kind == "dir_marker":
        ok = (p / db["marker"]).exists()
    elif kind == "file_prefix":
        ok = bool(list(p.parent.glob(p.name + ".*"))) if p.parent.is_dir() else False
    else:
        ok = p.exists()
    return ok, val


def run_checks(tool, env_py, scope, tool_dir=None):
    """Return (status, lines, issues, notes). status: 'ok'|'issues'|'skip'.
    lines: pretty (symbol, text, fix-or-None) tuples. issues: [{label, fix}]
    (fixable problems). notes: [str] (platform limitations — not fixable here)."""
    spec = requirements.for_tool(tool)
    lines, issues, notes = [], [], []
    default_fix = spec.get("fix", f"bin/bdtools update {tool}")

    want_os = spec.get("os")
    sysname = "linux" if platform.system() == "Linux" else (
        "macos" if platform.system() == "Darwin" else platform.system().lower())
    if want_os and want_os != sysname:
        return "skip", [(SKIP, f"not supported on {sysname} (requires {want_os}); skipping", None)], [], []

    env_py = (env_py or "").strip()
    if not env_py or not Path(env_py).exists():
        fix = f"bin/bdtools install {tool}"
        issues.append({"label": "environment not built", "fix": fix})
        return "issues", [(BAD, "environment not built", fix)], issues, []
    env_bin = str(Path(env_py).parent)
    lines.append((OK, "environment present", None))

    missing = check_modules(env_py, spec.get("modules", []))
    if missing:
        # Web-layer-only gaps get a targeted pip install into the existing env;
        # a missing ANALYSIS module keeps the full-rebuild remedy.
        mod_fix = web_layer_fix(env_py, tool_dir, missing) or default_fix
        lines.append((BAD, f"python modules missing: {', '.join(missing)}", mod_fix))
        issues.append({"label": f"missing modules: {', '.join(missing)}", "fix": mod_fix})
    elif spec.get("modules"):
        lines.append((OK, f"python modules ({len(spec['modules'])}) import", None))

    # Vendored payloads (kSNP4's SourceForge package) live outside the conda env
    # and are gitignored, so a fresh clone or feature worktree has none. Resolve
    # them first — the binary search below depends on the result, and a missing
    # asset dir is itself a reportable failure with its own fix line.
    asset_dirs = spec.get("asset_dirs", [])
    found_assets, missing_assets = resolve_asset_dirs(tool, tool_dir, asset_dirs)
    if missing_assets:
        label = f"vendored files missing: {', '.join(missing_assets)}"
        lines.append((BAD, label, default_fix))
        issues.append({"label": label, "fix": default_fix})
    elif asset_dirs:
        lines.append((OK, f"vendored files present ({', '.join(asset_dirs)})", None))

    # Binaries unavailable on this OS (e.g. bracken on macOS) are a known
    # limitation, not a rebuild-fixable error — report as a note, don't fail.
    unavailable = set(spec.get("platform_unavailable", {}).get(sysname, []))
    missing_bin = [b for b in spec.get("binaries", [])
                   if not has_binary(b, env_bin, found_assets)]
    real_missing = [b for b in missing_bin if b not in unavailable]
    note_missing = [b for b in missing_bin if b in unavailable]
    if real_missing:
        lines.append((BAD, f"programs not found: {', '.join(real_missing)}", default_fix))
        issues.append({"label": f"missing programs: {', '.join(real_missing)}", "fix": default_fix})
    elif spec.get("binaries"):
        # If some binaries are platform-unavailable (reported as a note below),
        # say "other" so the OK line doesn't read as "everything is present".
        lines.append((OK, "other programs on PATH" if note_missing else "programs on PATH", None))
    if note_missing:
        msg = (f"{', '.join(note_missing)} not available on {sysname} "
               f"(that step won't run; use a Linux/OOD deployment for full output)")
        lines.append((SKIP, msg, None))
        notes.append(msg)

    # ...and for hand-downloaded payloads, that the binaries found above are the
    # right KIND of executable for this host. Only probes that resolve are judged,
    # so this stays quiet on a machine where the payload is simply absent.
    fmt_bad = []
    for probe in spec.get("binary_format_probes", []):
        verdict, detail = check_binary_format(probe, env_bin, found_assets)
        if verdict is False:
            fmt_bad.append(detail)
    if fmt_bad:
        for detail in fmt_bad:
            lines.append((BAD, detail, default_fix))
        label = "wrong-OS binaries: " + ", ".join(
            d.split(" ", 1)[0] for d in fmt_bad)
        issues.append({"label": label, "fix": default_fix})
    elif spec.get("binary_format_probes") and not real_missing:
        lines.append((OK, f"vendored binaries match this host "
                          f"({platform.system()}/{platform.machine()})", None))

    optional_missing = [
        b for b in spec.get("optional_binaries", [])
        if not has_binary(b, env_bin, found_assets)
    ]
    if optional_missing:
        msg = (f"optional integrations unavailable: {', '.join(optional_missing)} "
               "(core analysis is still runnable)")
        lines.append((SKIP, msg, None))
        notes.append(msg)

    if scope == "all":
        for db in spec.get("databases", []):
            ok, detail = check_db(tool, db, env_bin=env_bin)
            if ok:
                lines.append((OK, db["label"], None))
            else:
                lines.append((BAD, f"{db['label']} missing {detail}", db["fix"]))
                issues.append({"label": f"{db['label']} missing", "fix": db["fix"]})

    return ("issues" if issues else "ok"), lines, issues, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--python", default="")
    ap.add_argument("--scope", choices=["env", "all"], default="all")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    status, lines, issues, notes = run_checks(
        args.tool, args.python, args.scope, tool_dir=args.dir)

    if args.json:
        print(json.dumps({"tool": args.tool, "status": status,
                          "ok": status != "issues", "issues": issues, "notes": notes}))
        return 1 if status == "issues" else 0

    print(f"{B}{args.tool}{X}")
    for sym, text, fix in lines:
        print(f"  {sym} {text}")
        if fix:
            print(f"      fix: {fix}")
    return 1 if status == "issues" else 0


if __name__ == "__main__":
    sys.exit(main())
