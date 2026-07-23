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
  tool_launch.py repro <tool> <port> [--host H]   -> prints a copy/paste shell command
  tool_launch.py show  <tool> <port> [--host H]   -> prints resolved plan as JSON
"""
import json
import os
import shlex
import sys
import time

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


def _bdtools_home():
    """Mirror common.sh: $BDTOOLS_HOME, else the XDG-friendly per-user default.

    Single source of truth so tool_dir() and the vsnp_gui site-root resolution
    in resolve() agree with install-local.sh, which builds its site tree at
    <BDTOOLS_HOME>/vsnp3-site. Works identically on Mac/WSL/Linux — pure path
    logic, no assumption about where the install lives."""
    home = os.environ.get("BDTOOLS_HOME", "").strip()
    if not home:
        base = os.environ.get("XDG_DATA_HOME", "").strip() or os.path.expanduser("~/.local/share")
        home = os.path.join(base, "bdtools")
    return home


def tool_dir(name):
    """Mirror common.sh:tool_dir — explicit BDTOOLS_TOOLSDIR wins, else per-user."""
    td = os.environ.get("BDTOOLS_TOOLSDIR", "").strip()
    if td and os.path.isdir(os.path.join(td, name)):
        return os.path.join(td, name)
    return os.path.join(_bdtools_home(), "checkouts", name)


def _manifest_env_name(tool):
    """The conda env name for the personal-install fallback (tools.yml `env`)."""
    _, tools = manifest.parse(_MANIFEST)
    for t in tools:
        if t.get("name") == tool:
            return t.get("env") or tool
    raise KeyError("unknown tool: %s" % tool)


def _conda_bases():
    """Candidate conda base dirs, mirroring common.sh:detect_conda.

    The personal-env fallback must find whatever conda the user actually has —
    NOT just miniforge3. common.sh already probes this list; tool_launch has to
    agree, or a tool installed into (say) ~/miniconda3/envs/<env> shows up as
    "not installed" in the dashboard even though `bdtools doctor` sees it.
    Order matters: honor explicit overrides first, then the common install bases.
    """
    seen, bases = set(), []

    def add(b):
        if b and b not in seen:
            seen.add(b)
            bases.append(b)

    add(os.environ.get("CONDA_BASE", "").strip())
    exe = os.environ.get("CONDA_EXE", "").strip()
    if exe:
        add(os.path.dirname(os.path.dirname(exe)))  # <base>/bin/conda -> <base>
    for b in ("~/miniforge3", "~/miniconda3", "~/mambaforge", "~/anaconda3",
              "/opt/miniforge3", "/opt/miniconda3",
              "/opt/homebrew/Caskroom/miniforge/base"):
        add(os.path.expanduser(b))
    return [b for b in bases if os.path.isdir(b)]


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
    # Personal-install fallback: <conda base>/envs/<manifest env> and, last of
    # all, the conda base python. Probe every conda base the user might have
    # (common.sh does the same) rather than assuming miniforge3.
    env_name = _manifest_env_name(tool)
    conda_bases = _conda_bases()
    personal_envs = [os.path.join(b, "envs", env_name) for b in conda_bases]
    base_pythons = [os.path.join(b, "bin", "python") for b in conda_bases]

    def _has_python(p):
        return bool(p) and os.path.isfile(os.path.join(p, "bin", "python"))

    # A source-tree override is useful for testing a feature branch without
    # rebuilding large analysis environments. In that case, reuse the matching
    # installed checkout's env after checking the override tree itself. This
    # changes code only: databases, conda packages, and user data remain in the
    # normal local installation.
    source_override_env = ""
    if os.environ.get("BDTOOLS_TOOLSDIR", "").strip() and not sb_dir:
        installed_dir = os.path.join(_bdtools_home(), "checkouts", tool)
        if os.path.abspath(d) != os.path.abspath(installed_dir):
            source_override_env = os.path.join(installed_dir, "env")

    # sandbox override -> shared/sibling env -> the tool's own <dir>/env ->
    # installed env for an explicit source override -> personal conda env.
    # The own-env step matters for a *local* install of a sibling-env
    # tool (e.g. vsnp_gui): there is no sibling <tools_root>/vsnp3 checkout, and the
    # GUI's server deps (uvicorn/fastapi) live in <dir>/env — NOT in the bare vsnp3
    # analysis conda env, which would otherwise be picked and fail to start uvicorn.
    env_dir = None
    for cand in (sb_env, shared_env, own_env, source_override_env):
        if _has_python(cand):
            env_dir = cand
            break
    if env_dir is None:
        env_dir = next((p for p in personal_envs if _has_python(p)), None)

    if env_dir:
        python = os.path.join(env_dir, "bin", "python")
    else:
        python = next((p for p in base_pythons if os.path.isfile(p)), None)
    if not python:
        looked = ", ".join([shared_env] + personal_envs + base_pythons)
        raise RuntimeError("%s: no python found (looked for %s)" % (tool, looked))

    # ---- build the environment overrides
    # env_overrides records ONLY the variables this function sets on top of the
    # ambient environment, so reproduce_command() can emit a runnable command that
    # doesn't leak the caller's whole environment (tokens etc). PATH is tracked as
    # the prepended prefix alone (":$PATH" is re-appended at render time).
    env = dict(os.environ)
    env_overrides = {}
    path_parts = []
    if env_dir:
        path_parts.append(os.path.join(env_dir, "bin"))
    path_parts += [os.path.join(d, p) for p in spec["path_prepend"]]
    if path_parts:
        env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH", "")])
        env_overrides["PATH_PREPEND"] = os.pathsep.join(path_parts)
    if spec["pythonpath"]:
        pp = [os.path.join(d, p) for p in spec["pythonpath"]]
        env["PYTHONPATH"] = os.pathsep.join(pp + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        env_overrides["PYTHONPATH"] = os.pathsep.join(pp)
    if spec["set_conda_prefix"] and env_dir:
        env["CONDA_PREFIX"] = env_dir
        env_overrides["CONDA_PREFIX"] = env_dir
    # vsnp_gui resolves its shared paths — references, VCF-db root, the vsnp3 env,
    # and the SIBLING Kraken install — from VSNP_GUI_SITE_ROOT, read ONCE at process
    # start (backend config.py). The Kraken path (_KRAKEN_GUI_ROOT) is derived from
    # that env var, NOT a config.json key, so a correct config.json can't repair it.
    # On a SERVER deployment those paths live under /srv/... (the backend default)
    # and script.sh.erb sets nothing extra. On a LOCAL/group install, install-local.sh
    # builds a self-contained site tree at <BDTOOLS_HOME>/vsnp3-site and points the
    # GUI there; this launch path must do the SAME, or the backend falls back to
    # /srv/kapurlab and e.g. "Run Kraken" 503s ("Kraken ID Parse is not installed at
    # /srv/kapurlab/tools/kraken_id_parse_gui"). Discriminator (server vs local) is
    # simply whether that site tree exists — install-local.sh creates it only for
    # local installs. setdefault() never overrides a value the caller already set, so
    # an explicit --server / script.sh.erb export of VSNP_GUI_SITE_ROOT still wins.
    if tool == "vsnp_gui":
        _site = os.path.join(_bdtools_home(), "vsnp3-site")
        if os.path.isdir(_site):
            env.setdefault("VSNP_GUI_SITE_ROOT", _site)
            # Single-user local install: one Projects root. "" is authoritative-empty
            # in the backend (disables the multi-user shared root); mirrors install-local.sh.
            env.setdefault("VSNP_GUI_SHARED_PROJECTS_ROOT", "")
        # Record the effective values (whatever won: caller's export or our default)
        # so the reproduce command carries them — the backend reads them once at start.
        for _k in ("VSNP_GUI_SITE_ROOT", "VSNP_GUI_SHARED_PROJECTS_ROOT"):
            if _k in env:
                env_overrides[_k] = env[_k]

    argv = [python, "-m", "uvicorn", spec["app"],
            "--host", host, "--port", str(port), "--log-level", "info"]
    return {
        "tool": tool,
        "argv": argv,
        "cwd": os.path.join(d, spec["workdir"]),
        "env": env,
        "env_overrides": env_overrides,
        "python": python,
        "env_dir": env_dir or "(base)",
        "dir": d,
    }


def reproduce_command(plan):
    """A single, copy-pasteable shell command that reproduces this launch from a
    fresh terminal: cd into the tool's backend, set ONLY the env vars we override,
    then run the same uvicorn line. The ambient environment (and any secrets in it)
    is intentionally excluded — only tool_launch's own overrides are emitted."""
    ov = plan.get("env_overrides", {})
    assigns = []
    prepend = ov.get("PATH_PREPEND")
    if prepend:
        # ":$PATH" stays outside the quotes so the shell still expands it.
        assigns.append("PATH=%s:$PATH" % shlex.quote(prepend))
    for k in ("PYTHONPATH", "CONDA_PREFIX",
              "VSNP_GUI_SITE_ROOT", "VSNP_GUI_SHARED_PROJECTS_ROOT"):
        if k in ov:
            assigns.append("%s=%s" % (k, shlex.quote(ov[k])))
    argv = " ".join(shlex.quote(a) for a in plan["argv"])
    prefix = (" ".join(assigns) + " ") if assigns else ""
    return "cd %s && %s%s" % (shlex.quote(plan["cwd"]), prefix, argv)


def log_header(plan, when=None):
    """A commented banner + the reproduce command, prepended to a tool's log file
    at launch so every run records the exact terminal command that produced it."""
    when = when or time.strftime("%Y-%m-%d %H:%M:%S %z")
    bar = "# " + "=" * 68
    return (
        "\n%s\n"
        "# bdtools tool launch — %s\n"
        "# started: %s\n"
        "# python env: %s\n"
        "# Reproduce this exact run from a terminal (copy/paste the line below):\n"
        "#\n"
        "%s\n"
        "#\n"
        "%s\n"
    ) % (bar, plan.get("tool", "?"), when, plan.get("env_dir", "?"),
         reproduce_command(plan), bar)


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
    elif action == "repro":
        print(reproduce_command(plan))
    elif action == "show":
        out = dict(plan)
        out.pop("env")  # too big / secret-bearing
        print(json.dumps(out, indent=2))
    else:
        sys.exit("unknown action: %s" % action)


if __name__ == "__main__":
    _cli()
