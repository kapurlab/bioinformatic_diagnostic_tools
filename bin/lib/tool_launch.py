#!/usr/bin/env python3
"""tool_launch.py — resolve how to launch a suite tool on ONE allocated node.

The single source of truth the consolidated OOD dashboard uses to start each
tool. It reproduces exactly what every tool's own
``ood/apps/<tool>/template/script.sh.erb`` does (env resolution, PATH/PYTHONPATH,
per-tool extras) EXCEPT that it always binds uvicorn to **127.0.0.1** — the tool
is reachable only through the dashboard's reverse proxy on the same node, never
directly via /rnode. That loopback bind is the session-confinement fix.

The 7 non-vsnp tools share one pattern (shared env at ``<dir>/env``,
``PYTHONPATH=<dir>/bin``, ``cd backend``, uvicorn ``app.main:app``); vsnp_gui uses
the sibling ``vsnp3`` env and no PYTHONPATH; ksnp adds its vendored kSNP4 bin dir
to PATH; amr sets CONDA_PREFIX. All of that is captured in SPEC below.

Dependency-free (stdlib only) so it runs under any tool env's python.

CLI (used by tests / a bash shim):
  tool_launch.py cmd   <tool> <port> [--host H]   -> prints argv (one per line)
  tool_launch.py show  <tool> <port> [--host H]   -> prints resolved plan as JSON
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import manifest  # noqa: E402  (sibling module, stdlib-only)

_REPO_DIR = os.path.dirname(os.path.dirname(_HERE))
_MANIFEST = os.environ.get("BDTOOLS_MANIFEST", os.path.join(_REPO_DIR, "tools.yml"))

# Per-tool deviations from the common pattern. Everything not listed here uses
# DEFAULTS. Keeping this a small table (not 8 near-identical shell scripts) is the
# consolidation the OOD admins asked for: env knowledge lives in the umbrella.
DEFAULTS = {
    "shared_env_sibling": None,   # e.g. "vsnp3" -> <tools_root>/vsnp3 instead of <dir>/env
    "workdir": "backend",
    "app": "app.main:app",
    "pythonpath": ["bin"],        # dirs (relative to <dir>) prepended to PYTHONPATH
    "path_prepend": [],           # extra dirs (relative to <dir>) after <env>/bin on PATH
    "set_conda_prefix": False,    # export CONDA_PREFIX=<chosen env> (amr needs it)
}
SPEC = {
    "vsnp_gui": {"shared_env_sibling": "vsnp3", "pythonpath": []},
    "ksnp_gui": {"path_prepend": ["vendor/kSNP4-bin"]},
    "amr_plus_gui": {"set_conda_prefix": True},
}


def _spec(tool):
    s = dict(DEFAULTS)
    s.update(SPEC.get(tool, {}))
    return s


def tool_dir(name):
    """Mirror common.sh:tool_dir — explicit BDTOOLS_TOOLSDIR wins, else per-user."""
    td = os.environ.get("BDTOOLS_TOOLSDIR", "").strip()
    if td and os.path.isdir(os.path.join(td, name)):
        return os.path.join(td, name)
    home = os.environ.get("BDTOOLS_HOME", "").strip()
    if not home:
        base = os.environ.get("XDG_DATA_HOME", "").strip() or os.path.expanduser("~/.local/share")
        home = os.path.join(base, "bdtools")
    return os.path.join(home, "checkouts", name)


def _manifest_env_name(tool):
    """The conda env name for the personal-install fallback (tools.yml `env`)."""
    _, tools = manifest.parse(_MANIFEST)
    for t in tools:
        if t.get("name") == tool:
            return t.get("env") or tool
    raise KeyError("unknown tool: %s" % tool)


def _sandbox_env(tool):
    """Per-user sandbox overrides from ~/.config/<tool>/sandbox.env (BDTOOLS_APP_*).

    Written by install-sandbox.sh so a $HOME checkout/env can live anywhere.
    Returns (app_dir, app_env) with empty strings when unset/absent."""
    path = os.path.expanduser("~/.config/%s/sandbox.env" % tool)
    app_dir = app_env = ""
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("BDTOOLS_APP_DIR="):
                    app_dir = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("BDTOOLS_APP_ENV="):
                    app_env = line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return app_dir, app_env


def resolve(tool, port, host="127.0.0.1"):
    """Return a launch plan dict: argv, cwd, env (full environ + overrides), python, env_dir.

    Raises RuntimeError with an actionable message if nothing runnable is found.
    """
    spec = _spec(tool)
    sb_dir, sb_env = _sandbox_env(tool)
    d = sb_dir or tool_dir(tool)
    if not os.path.isdir(os.path.join(d, spec["workdir"])):
        raise RuntimeError("%s: no %s/ at %s (tool not installed here?)" % (tool, spec["workdir"], d))

    # ---- pick the env + python (shared -> own -> personal -> base), mirroring
    # script.sh.erb but with one extra fallback for local per-user installs.
    tools_root = os.path.dirname(d)
    own_env = os.path.join(d, "env")   # the tool's OWN built env (<dir>/env)
    if spec["shared_env_sibling"]:
        shared_env = os.path.join(tools_root, spec["shared_env_sibling"])
    else:
        shared_env = own_env
    personal_env = os.path.expanduser(os.path.join("~/miniforge3/envs", _manifest_env_name(tool)))
    base_python = os.path.expanduser("~/miniforge3/bin/python")

    def _has_python(p):
        return bool(p) and os.path.isfile(os.path.join(p, "bin", "python"))

    # sandbox override -> shared/sibling env -> the tool's own <dir>/env -> personal
    # conda env. The own-env step matters for a *local* install of a sibling-env
    # tool (e.g. vsnp_gui): there is no sibling <tools_root>/vsnp3 checkout, and the
    # GUI's server deps (uvicorn/fastapi) live in <dir>/env — NOT in the bare vsnp3
    # analysis conda env, which would otherwise be picked and fail to start uvicorn.
    env_dir = None
    for cand in (sb_env, shared_env, own_env):
        if _has_python(cand):
            env_dir = cand
            break
    if env_dir is None and os.path.isdir(personal_env):
        env_dir = personal_env

    if env_dir:
        python = os.path.join(env_dir, "bin", "python")
    elif os.path.isfile(base_python):
        python = base_python
    else:
        raise RuntimeError(
            "%s: no python found (looked for %s, %s, %s)" % (tool, shared_env, personal_env, base_python))

    # ---- build the environment overrides
    env = dict(os.environ)
    path_parts = []
    if env_dir:
        path_parts.append(os.path.join(env_dir, "bin"))
    path_parts += [os.path.join(d, p) for p in spec["path_prepend"]]
    if path_parts:
        env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH", "")])
    if spec["pythonpath"]:
        pp = [os.path.join(d, p) for p in spec["pythonpath"]]
        env["PYTHONPATH"] = os.pathsep.join(pp + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    if spec["set_conda_prefix"] and env_dir:
        env["CONDA_PREFIX"] = env_dir
    # vsnp_gui: on a --server node its backend reads shared paths from defaults
    # (/srv/... or SITE_ROOT) — same as its own script.sh.erb, which sets nothing
    # extra. VSNP_GUI_SITE_ROOT is only needed for local/sandbox and is passed
    # through from the environment if already set (dict(os.environ) above).

    argv = [python, "-m", "uvicorn", spec["app"],
            "--host", host, "--port", str(port), "--log-level", "info"]
    return {
        "tool": tool,
        "argv": argv,
        "cwd": os.path.join(d, spec["workdir"]),
        "env": env,
        "python": python,
        "env_dir": env_dir or "(base)",
        "dir": d,
    }


def _cli():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    action, tool, port = sys.argv[1], sys.argv[2], sys.argv[3]
    host = "127.0.0.1"
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    plan = resolve(tool, int(port), host=host)
    if action == "cmd":
        print("\n".join(plan["argv"]))
    elif action == "show":
        out = dict(plan)
        out.pop("env")  # too big / secret-bearing
        print(json.dumps(out, indent=2))
    else:
        sys.exit("unknown action: %s" % action)


if __name__ == "__main__":
    _cli()
