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


def check_modules(env_py, modules):
    """Return the subset of modules that fail to import in the tool's env."""
    if not modules:
        return []
    code = (
        "import importlib,sys\n"
        "bad=[m for m in sys.argv[1:] "
        "if importlib.util.find_spec(m) is None]\n"
        "print('\\n'.join(bad))\n"
    )
    try:
        out = subprocess.run([env_py, "-c", code, *modules],
                             capture_output=True, text=True, timeout=60)
        return [m for m in out.stdout.split() if m]
    except Exception:
        return list(modules)  # can't even run the interpreter -> all "missing"


def has_binary(name, env_bin):
    if env_bin:
        cand = Path(env_bin) / name
        if cand.exists() and os.access(cand, os.X_OK):
            return True
        path = f"{env_bin}{os.pathsep}{os.environ.get('PATH', '')}"
    else:
        path = os.environ.get("PATH", "")
    return shutil.which(name, path=path) is not None


def _expand(s):
    """Expand $VAR, ${VAR}, and ${VAR:-fallback} against the environment."""
    import re
    def repl(m):
        var, fb = m.group(1), m.group(2)
        return os.environ.get(var) or (fb if fb is not None else "")
    s = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", repl, s)
    return os.path.expandvars(s)


def check_db(tool, db):
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


def run_checks(tool, env_py, scope):
    """Return (status, lines, issues). status: 'ok'|'issues'|'skip'.
    lines: pretty (symbol, text, fix-or-None) tuples. issues: [{label, fix}]."""
    spec = requirements.for_tool(tool)
    lines, issues = [], []
    default_fix = spec.get("fix", f"bin/bdtools update {tool}")

    want_os = spec.get("os")
    sysname = "linux" if platform.system() == "Linux" else (
        "macos" if platform.system() == "Darwin" else platform.system().lower())
    if want_os and want_os != sysname:
        return "skip", [(SKIP, f"not supported on {sysname} (requires {want_os}); skipping", None)], []

    env_py = (env_py or "").strip()
    if not env_py or not Path(env_py).exists():
        fix = f"bin/bdtools install {tool}"
        issues.append({"label": "environment not built", "fix": fix})
        return "issues", [(BAD, "environment not built", fix)], issues
    env_bin = str(Path(env_py).parent)
    lines.append((OK, "environment present", None))

    missing = check_modules(env_py, spec.get("modules", []))
    if missing:
        lines.append((BAD, f"python modules missing: {', '.join(missing)}", default_fix))
        issues.append({"label": f"missing modules: {', '.join(missing)}", "fix": default_fix})
    elif spec.get("modules"):
        lines.append((OK, f"python modules ({len(spec['modules'])}) import", None))

    missing_bin = [b for b in spec.get("binaries", []) if not has_binary(b, env_bin)]
    if missing_bin:
        lines.append((BAD, f"programs not found: {', '.join(missing_bin)}", default_fix))
        issues.append({"label": f"missing programs: {', '.join(missing_bin)}", "fix": default_fix})
    elif spec.get("binaries"):
        lines.append((OK, f"programs ({len(spec['binaries'])}) on PATH", None))

    if scope == "all":
        for db in spec.get("databases", []):
            ok, detail = check_db(tool, db)
            if ok:
                lines.append((OK, db["label"], None))
            else:
                lines.append((BAD, f"{db['label']} missing {detail}", db["fix"]))
                issues.append({"label": f"{db['label']} missing", "fix": db["fix"]})

    return ("issues" if issues else "ok"), lines, issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--python", default="")
    ap.add_argument("--scope", choices=["env", "all"], default="all")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    status, lines, issues = run_checks(args.tool, args.python, args.scope)

    if args.json:
        print(json.dumps({"tool": args.tool, "status": status,
                          "ok": status != "issues", "issues": issues}))
        return 1 if status == "issues" else 0

    print(f"{B}{args.tool}{X}")
    for sym, text, fix in lines:
        print(f"  {sym} {text}")
        if fix:
            print(f"      fix: {fix}")
    return 1 if status == "issues" else 0


if __name__ == "__main__":
    sys.exit(main())
