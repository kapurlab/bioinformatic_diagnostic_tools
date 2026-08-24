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
import platform
import shlex
import sys
import time
try:
    import resource        # POSIX only; absent on native Windows
except ImportError:        # pragma: no cover — WSL/macOS/Linux all have it
    resource = None

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import config_hygiene  # noqa: E402  (sibling module, stdlib-only)
import manifest  # noqa: E402  (sibling module, stdlib-only)
import site_paths  # noqa: E402  (sibling module, stdlib-only)

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


# Human-readable note per vendored asset dir, used in the "not found" warning so
# the message says what actually breaks rather than just naming a missing path.
ASSET_NOTES = {
    "vendor/kSNP4-bin": "kSNP4, Kchooser4 and MakeKSNP4infile will NOT be on PATH",
}


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
    """Mirror common.sh:tool_dir, nearest-first:

      1. $BDTOOLS_TOOLSDIR/<tool>  — what a launcher or OOD job script exported
      2. <this checkout's parent>/<tool> — a SITE TREE. An umbrella installed at
         <root>/tools/bioinformatic_diagnostic_tools has its siblings there, so
         where this file lives already says which deployment it is talking about.
      3. the managed per-user checkout

    Step 2 exists because step 1 is a variable someone has to remember to
    export, and forgetting it is silent: on a site whose umbrella lives at
    /project/shared/bdtools/tools/bioinformatic_diagnostic_tools, commands run
    from inside that tree resolved the OPERATOR's personal copies instead of the
    shared tools everyone runs — two checkouts, every report about the wrong
    one, and a shipped fix that "does not apply".

    Each step requires the directory to exist, so this RECOGNISES a site tree
    rather than assuming one; a laptop has no sibling beside the umbrella and
    lands on step 3 exactly as before. Kept in lock-step with common.sh:tool_dir
    — the two disagreeing about which copy is live is the bug this fixes."""
    td = os.environ.get("BDTOOLS_TOOLSDIR", "").strip()
    if td and os.path.isdir(os.path.join(td, name)):
        return os.path.join(td, name)
    site = os.path.join(os.path.dirname(_REPO_DIR), name)
    if os.path.isdir(os.path.join(site, ".git")):
        return site
    return os.path.join(_bdtools_home(), "checkouts", name)


def _vendor_root():
    """Machine-wide cache for large vendored third-party payloads (kSNP4 etc).

    Mirrors how setup-databases.sh records its chosen database root: an explicit
    env var wins, else the path written at install time, else a per-user default
    under BDTOOLS_HOME. Kept separate from `db-root` — vendored binaries and
    reference databases have different owners and lifecycles."""
    root = os.environ.get("BDTOOLS_VENDOR_ROOT", "").strip()
    if root:
        return root
    home = str(site_paths.bdtools_home())
    try:
        with open(os.path.join(home, "vendor-root")) as fh:
            recorded = fh.readline().strip()
        if recorded:
            return recorded
    except OSError:
        pass
    return os.path.join(home, "vendor")


def _recorded_env_dir(tool):
    """(prefix, warning) — the env this tool was BUILT into, as recorded at
    install time by common.sh:record_env_prefix.

    Read before searching, because searching is what got this wrong. The build
    asks conda for the env named in the manifest, under whichever conda
    detect_conda found; the launcher took the first base on its own probe list
    holding an env by that name. Identical answers only while the machine has
    one such base. On the 2026-08-24 Ames HPC it had two, and the tool ran from
    the one the install had NOT touched — complete env built, doctor grading the
    other one, and IRMA's HTML report would have come out with every chart
    missing, because plotly's import is wrapped in a broad except so a run never
    dies over a picture.

    Advisory, never binding: a record whose prefix no longer holds a python is
    ignored and the normal search runs. That case is worth a warning though —
    the env a build produced has been moved or deleted, so the tool is about to
    run from somewhere nobody chose."""
    path = os.path.join(_bdtools_home(), "env-prefix", tool)
    try:
        with open(path, encoding="utf-8") as fh:
            prefix = fh.readline().strip()
    except OSError:
        return "", ""
    if not prefix:
        return "", ""
    if os.path.isfile(os.path.join(prefix, "bin", "python")):
        return prefix, ""
    return "", (
        "%s: the env recorded at install time (%s) no longer has a python — it "
        "was moved or deleted. Falling back to searching this machine's conda "
        "bases, which may not be the env this tool was built with. Rebuild with "
        "`bdtools install %s` to settle it." % (tool, prefix, tool))


def _resolve_asset_dir(tool, rel, d, sb_dir=""):
    """Locate a tool's vendored asset dir, tolerating a source-tree override.

    Large third-party payloads are gitignored (ksnp_gui/vendor/.gitignore ignores
    everything), so a fresh clone — or a feature worktree created by
    `bdtools dashboard --tools-dir` — has an EMPTY vendor/. Prepending that
    nonexistent path to PATH is how a complete kSNP4 install becomes
    "command not found: kSNP4" at runtime.

    Same reasoning as source_override_env in resolve(): a 545 MB vendored binary
    bundle is not code, so borrow it from the normal installation instead of
    failing. Candidate order: the resolved tree, the installed checkout, the
    machine-wide vendor cache.

    Returns (path, tried). `path` is "" when no candidate exists. isdir() follows
    symlinks, so a DANGLING vendor/kSNP4-bin symlink is correctly rejected — that
    is the exact shape the kSNP4 install uses.
    """
    tried = []

    def _try(p):
        tried.append(p)
        return p if os.path.isdir(p) else ""

    hit = _try(os.path.join(d, rel))
    if hit:
        return hit, tried
    if os.environ.get("BDTOOLS_TOOLSDIR", "").strip() and not sb_dir:
        installed_dir = os.path.join(_bdtools_home(), "checkouts", tool)
        if os.path.abspath(d) != os.path.abspath(installed_dir):
            hit = _try(os.path.join(installed_dir, rel))
            if hit:
                return hit, tried
    hit = _try(os.path.join(_vendor_root(), tool, os.path.basename(rel)))
    if hit:
        return hit, tried
    return "", tried


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
    # This literal list is PINNED to common.sh:detect_conda() — same entries,
    # same order (~ here is spelled ${HOME} there). Drift between the two is
    # how the dashboard and doctor come to disagree about "installed". The
    # second block covers the common non-interactive install bases the first
    # missed: /opt/conda (official Anaconda/Miniconda Docker/WSL images, many
    # HPC site installs), /opt/anaconda3, /opt/mambaforge,
    # /usr/local/{miniforge3,miniconda3}, and ~/opt/{anaconda3,miniconda3}
    # (Anaconda's macOS graphical installer default). Change both or neither.
    for b in ("~/miniforge3", "~/miniconda3", "~/mambaforge", "~/anaconda3",
              "/opt/miniforge3", "/opt/miniconda3",
              "/opt/homebrew/Caskroom/miniforge/base",
              "/opt/conda", "/opt/anaconda3", "/opt/mambaforge",
              "/usr/local/miniforge3", "/usr/local/miniconda3",
              "~/opt/anaconda3", "~/opt/miniconda3"):
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


# resolve() builds the sibling map by asking resolve() about each sibling, so the
# nested calls must NOT build it again — that recurses until the stack blows, and
# resolve() is on the dashboard's hot path (every discovery pass, every launch).
_SCANNING_SIBLINGS = False

# tool -> the notice lines its sweep produced. Caches the RESULT, not merely the
# fact that it ran: the dashboard resolves each tool once during discovery and
# again at launch, and only the launch shows anything to anyone. A boolean
# "already done" made the discovery pass consume the one report of a config it
# had just rewritten, so the repair happened and nobody was told.
#
# Replayed until something DELIVERS it — see consume_notices(). Before that, the
# line rendered on every launch for the life of the dashboard, in the same red
# slot as "kSNP4 not found, nothing will run". A finished repair styled as a
# live failure is how a report stops being read: the first person to see it
# asked whether their working tool was broken.
_CONFIG_SWEPT = {}


def consume_notices(tool):
    """Return this tool's pending notices and clear them — deliver once.

    A notice describes something that ALREADY HAPPENED and needs no action; a
    warning describes something still wrong. Both are worth saying, but only one
    is worth saying twice. The caller that actually shows a human (the
    dashboards' launch handler) calls this; resolve() keeps replaying until then,
    so a discovery pass cannot swallow the only telling."""
    return _CONFIG_SWEPT.pop(tool, [])


def _sweep_foreign_config(tool):
    """Strip another deployment's paths out of this tool's user config, and say
    so. Returns NOTICE lines for the launch plan — the repair is already done
    and there is nothing for the reader to fix.

    Done HERE, at the single point every launch goes through, because the bug it
    fixes is invisible at every other one. A GUI seeds
    ~/.config/<tool>/config.json from its own DEFAULTS on first start, and
    several tools used to default a database path to a site literal; the tool
    then hands that path to every analysis run, on a machine where it cannot
    exist. Removing the literal from a tool's DEFAULTS fixes the next config
    created and no existing one — `load_config()` only setdefaults MISSING keys
    — so the stale value outlives the fix, the tool update, and the env rebuild.
    A real 2026-08-24 run: irma_gui invoked with
    `--genoflu-db /srv/kapurlab/databases/genoflu/dependencies` on a cluster
    that has no /srv/kapurlab, and a GenoFLU warning nobody there could act on.

    See config_hygiene.py for the (deliberately conservative) rule and for why
    nothing is deleted."""
    if tool in _CONFIG_SWEPT:
        return list(_CONFIG_SWEPT[tool])
    try:
        found = config_hygiene.sweep(tool, _REPO_DIR)
    except Exception:      # noqa: BLE001 — never block a launch over hygiene
        _CONFIG_SWEPT[tool] = []
        return []
    line = config_hygiene.describe(tool, found)
    _CONFIG_SWEPT[tool] = [line] if line else []
    return list(_CONFIG_SWEPT[tool])


def _manifest_tool_names():
    try:
        _v, tools = manifest.parse(_MANIFEST)
        return [t.get("name", "") for t in tools if t.get("name")]
    except Exception:
        return []


def _env_dir_for_tool(name):
    """The env that would run `name`, or "" — asked of resolve(), not guessed.

    Guessing <root>/<name>/env would miss a personal conda env or a shared sibling
    env, and would then hand a caller a path that does not exist.
    """
    global _SCANNING_SIBLINGS
    outer = _SCANNING_SIBLINGS
    _SCANNING_SIBLINGS = True
    try:
        plan = resolve(name, 0)
    except Exception:
        return ""
    finally:
        _SCANNING_SIBLINGS = outer
    d = plan.get("env_dir") or ""
    return "" if d in ("", "(base)") else d


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

    # sandbox override -> the env this tool was BUILT into -> shared/sibling env
    # -> the tool's own <dir>/env -> installed env for an explicit source
    # override -> personal conda env.
    # The own-env step matters for a *local* install of a sibling-env
    # tool (e.g. vsnp_gui): there is no sibling <tools_root>/vsnp3 checkout, and the
    # GUI's server deps (uvicorn/fastapi) live in <dir>/env — NOT in the bare vsnp3
    # analysis conda env, which would otherwise be picked and fail to start uvicorn.
    #
    # The recorded prefix sits second, under only the sandbox override: an
    # explicit sandbox is someone saying "run this one", while the record is this
    # machine reporting what it built. Everything below it is a search, and a
    # search is what picked the wrong env of two same-named ones (see
    # _recorded_env_dir).
    recorded_env, recorded_warning = _recorded_env_dir(tool)
    env_dir = None
    for cand in (sb_env, recorded_env, shared_env, own_env, source_override_env):
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
    warnings = []          # something is WRONG and the reader may need to act
    notices = []           # something HAPPENED; recorded, no action needed
    if recorded_warning:
        warnings.append(recorded_warning)
    path_parts = []
    if env_dir:
        path_parts.append(os.path.join(env_dir, "bin"))
    # Vendored asset dirs are resolved, not assumed: a missing one must produce a
    # loud warning at LAUNCH rather than a bare "command not found" mid-analysis.
    missing_assets = []
    for rel in spec["path_prepend"]:
        asset, tried = _resolve_asset_dir(tool, rel, d, sb_dir=sb_dir)
        if asset:
            path_parts.append(asset)
            continue
        missing_assets.append(rel)
        note = ASSET_NOTES.get(rel, "tools shipped in %s will NOT be on PATH" % rel)
        warnings.append(
            "%s: %s not found — %s. Looked in: %s. Fix: run %s/deploy/install.sh "
            "(or `bdtools install %s`)." % (tool, rel, note, ", ".join(tried), d, tool))
    if missing_assets:
        # Lets the tool's own backend refuse to start an analysis it cannot run,
        # without re-deriving any of this resolution logic.
        env["BDTOOLS_MISSING_ASSETS"] = os.pathsep.join(missing_assets)
        env_overrides["BDTOOLS_MISSING_ASSETS"] = env["BDTOOLS_MISSING_ASSETS"]
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
    # Hand every tool the deployment's resolved paths, so no tool has to contain
    # one. A literal like "/srv/kapurlab/tools/<tool>" or
    # "/srv/kapurlab/databases/..." is correct only on the machine it was written
    # for, and it fails SILENTLY elsewhere — the directory simply isn't there, so
    # the feature quietly does nothing (amr's MLST cross-check did exactly this on
    # every non-lab-server machine). site_paths resolves each value from an env
    # var, then a recorded site file, then a defensible derivation — never a
    # baked-in path. BDTOOLS_TOOLS_ROOT is the dir CONTAINING the checkouts, so a
    # sibling tool is <root>/<tool>; we pass the tree we actually resolved above,
    # which also keeps a feature worktree self-consistent.
    for _k, _v in site_paths.as_env(_REPO_DIR).items():
        env[_k] = _v
        env_overrides[_k] = _v
    # ...and take away the ones a PREVIOUS deployment left in this user's config
    # for the same tool, which the env vars above cannot override because the
    # tool reads its own config.json first. Skipped while scanning siblings:
    # that pass is a cheap env lookup, not a launch.
    if not _SCANNING_SIBLINGS:
        notices.extend(_sweep_foreign_config(tool))
    # Which conda env provides each OTHER tool's software.
    #
    # BDTOOLS_TOOLS_ROOT above says where the sibling checkouts are, which is enough
    # to find a sibling's SCRIPT but not the environment built for it — and running a
    # sibling's script under this tool's python defeats the point, because the
    # binaries it shells out to still resolve from this env. That is the whole reason
    # amr_plus had to carry its own copy of mlst, which then held its own
    # AMRFinderPlus two major versions back: one shared env can only ever install the
    # lowest common denominator of everything in it.
    #
    # With this map a tool can invoke a sibling's software from the env built for it
    # (<env>/bin/<prog>, with <env>/bin first on PATH so the callee's own perl/blast
    # resolve there too), and each analysis package is then free to move at its own
    # pace. Exported as "tool=envdir" pairs plus one variable per tool, since a shell
    # script consuming this should not have to parse anything.
    _sibs = []
    for _name in ([] if _SCANNING_SIBLINGS else _manifest_tool_names()):
        if _name == tool:
            continue
        _senv = _env_dir_for_tool(_name)
        if not _senv:
            continue
        _sibs.append(f"{_name}={_senv}")
        _var = "BDTOOLS_SIBLING_ENV_" + _name.upper()
        env[_var] = _senv
        env_overrides[_var] = _senv
        # The sibling's ARCH pin rides along with its env path. The launched
        # backend runs under a pin derived from ITS OWN env (argv below), and
        # every subprocess inherits that preference — including a sibling's
        # binaries invoked via BDTOOLS_SIBLING_ENV_<TOOL>. When the sibling env
        # was built for a DIFFERENT subdir (real today: mixed osx-64/osx-arm64
        # envs on one Mac — a genoflu env built osx-arm64 beside osx-64
        # kraken/mlst/vsnp envs), a universal interpreter in it picks the
        # CALLER's slice and dies loading its own native modules — the exact
        # Cwd.bundle failure of 2026-08-22, reintroduced through cross-tool
        # invocation, invisible to every file check. So export a ready-to-splice
        # prefix ("/usr/bin/arch -arm64", "/usr/bin/arch -x86_64", or "" when
        # there is nothing to pin — no spaces beyond the one separator, so it is
        # safe in shell strings and argv alike) for consumers to prepend.
        # SPLICE IT ONLY ONTO A WRAPPER OR INTERPRETER — the env's python/perl/
        # bash that then spawns the tools — never directly onto an individual
        # tool binary: /usr/bin/arch ENFORCES its architecture, so a thin
        # foreign-arch binary that would run happily via Rosetta dies under it
        # with "Bad CPU type" (adversarial-review catch; the same rule
        # check.py's _pin_for_exec applies by only pinning fat exec targets).
        # Interpreters and wrappers are universal-or-matching by construction,
        # and the preference they PASS DOWN to children only selects among a
        # fat binary's slices — it never breaks a thin one. Exported
        # unconditionally alongside each sibling env so consumers can splice
        # without existence checks.
        _avar = "BDTOOLS_SIBLING_ARCH_" + _name.upper()
        _sarch = " ".join(_arch_prefix(_senv))
        env[_avar] = _sarch
        env_overrides[_avar] = _sarch
    if _sibs:
        env["BDTOOLS_SIBLING_ENVS"] = os.pathsep.join(_sibs)
        env_overrides["BDTOOLS_SIBLING_ENVS"] = env["BDTOOLS_SIBLING_ENVS"]
    env["BDTOOLS_TOOLS_ROOT"] = tools_root
    env_overrides["BDTOOLS_TOOLS_ROOT"] = tools_root
    # vsnp_gui resolves its shared paths — references, VCF-db root, the vsnp3 env,
    # and the SIBLING Kraken install — from VSNP_GUI_SITE_ROOT, read ONCE at process
    # start (backend config.py). The Kraken path (_KRAKEN_GUI_ROOT) is derived from
    # that env var, NOT a config.json key, so a correct config.json can't repair it.
    # On a LOCAL/group install, install-local.sh builds a self-contained site tree at
    # <BDTOOLS_HOME>/vsnp3-site and points the GUI there; this launch path must do the
    # SAME, or the backend falls back to its built-in default and e.g. "Run Kraken"
    # 503s ("Kraken ID Parse is not installed at <default>/tools/kraken_id_parse_gui").
    # Discriminator (server vs local) is simply whether that site tree exists —
    # install-local.sh creates it only for local installs.
    #
    # On a SERVER deployment there is no such tree, so take the root the deployment
    # declares (site.conf SITE_ROOT, via site_paths). The backend's own fallback is a
    # literal that is only correct on the reference install, so leaving it unset would
    # send vSNP looking for a multi-GB reference set in a directory this site has
    # never heard of — and finding nothing there looks identical to having no
    # references configured. Resolving it here keeps every site path in site_paths,
    # which is the one place allowed to know them.
    #
    # setdefault() never overrides a value the caller already set, so an explicit
    # export by a card or a wrapper still wins.
    if tool == "vsnp_gui":
        _site = os.path.join(_bdtools_home(), "vsnp3-site")
        # "The personal site tree exists" is NOT the same as "this is a personal
        # install". A box can have both: an old local install left
        # <BDTOOLS_HOME>/vsnp3-site behind, and the tool now runs from a shared
        # site checkout. Taking the personal tree there pointed vsnp_gui (and
        # doctor, which grades what this resolver returns) at an empty refs
        # directory, so a site with 28 reference sets was reported as having
        # none — with a fix that would have downloaded a second copy into the
        # wrong place. Only prefer the personal tree when the tool really is the
        # managed personal checkout; a site install takes the root its
        # deployment declares.
        _managed = os.path.join(_bdtools_home(), "checkouts", tool)
        _is_personal = os.path.realpath(d) == os.path.realpath(_managed)
        if os.path.isdir(_site) and (_is_personal or not env.get(site_paths.ENV_SITE_ROOT)):
            env.setdefault("VSNP_GUI_SITE_ROOT", _site)
            # Single-user local install: one Projects root. "" is authoritative-empty
            # in the backend (disables the multi-user shared root); mirrors install-local.sh.
            env.setdefault("VSNP_GUI_SHARED_PROJECTS_ROOT", "")
        elif env.get(site_paths.ENV_SITE_ROOT):
            env.setdefault("VSNP_GUI_SITE_ROOT", env[site_paths.ENV_SITE_ROOT])
        # Record the effective values (whatever won: caller's export or our default)
        # so the reproduce command carries them — the backend reads them once at start.
        for _k in ("VSNP_GUI_SITE_ROOT", "VSNP_GUI_SHARED_PROJECTS_ROOT"):
            if _k in env:
                env_overrides[_k] = env[_k]

    argv = _arch_prefix(env_dir) + [python, "-m", "uvicorn", spec["app"],
                                    "--host", host, "--port", str(port),
                                    "--log-level", "info"]
    return {
        "tool": tool,
        "argv": argv,
        "cwd": os.path.join(d, spec["workdir"]),
        "env": env,
        "env_overrides": env_overrides,
        "python": python,
        "env_dir": env_dir or "(base)",
        "dir": d,
        "warnings": warnings,
        "notices": notices,
    }


# How many open files a tool gets. Every analysis subprocess inherits this
# launcher's RLIMIT_NOFILE, and macOS hands a GUI-launched process a soft limit
# of 256 — which the assembly stack runs straight through. Live case
# (2026-08-23): shovill's KMC opens one temp file per k-mer bin and died on
# `Cannot open temporary file .../kmc_00253.bin` — file 253 of 256, minus stdio
# — so the AMR pipeline silently fell back to plain SPAdes, which announced its
# own ceiling in the same log ("Open file limit set to 256") and survived only
# because it needs 80. Nothing about that failure names a limit, and no file
# check can see it: it is a property of the process that spawned the tool.
#
# 8192 with a stepped retreat: the soft limit may be raised up to the hard limit
# (effectively unbounded on macOS and Linux), but a host with a real ceiling must
# still get the highest value it will accept rather than an exception.
WANT_NOFILE = 8192


def raise_file_limit(want=WANT_NOFILE):
    """Raise this process's open-file soft limit. Returns (before, after).

    Children inherit it, so calling this once in the launcher covers every
    program the pipeline spawns. Never lowers a limit, and never throws: a host
    that refuses the raise is reported through the return value (and the launch
    header), not by failing a launch that would otherwise have worked."""
    if resource is None:
        return (0, 0)
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (OSError, ValueError):
        return (0, 0)
    if soft >= want:
        return (soft, soft)
    capped = want if hard == resource.RLIM_INFINITY else min(want, hard)
    for cand in (capped, 4096, 2048, 1024):
        if cand <= soft:
            break
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (cand, hard))
            return (soft, cand)
        except (OSError, ValueError):
            continue
    return (soft, soft)


def file_limit():
    """This process's open-file soft limit (0 when it cannot be read)."""
    if resource is None:
        return 0
    try:
        return resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    except (OSError, ValueError):
        return 0


def _env_subdir(envdir):
    """The conda platform an env was built for (majority of its packages)."""
    counts = {}
    try:
        metas = os.listdir(os.path.join(envdir, "conda-meta"))
    except OSError:
        return ""
    for m in metas:
        if not m.endswith(".json"):
            continue
        try:
            with open(os.path.join(envdir, "conda-meta", m)) as fh:
                sd = json.load(fh).get("subdir", "")
        except Exception:
            continue
        if sd and sd != "noarch":
            counts[sd] = counts.get(sd, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def _arch_prefix(env_dir):
    """['/usr/bin/arch', '-arm64'] etc — pin this launch to the env's platform.

    macOS decides which slice of a UNIVERSAL binary to run from the launching
    process tree's inherited preference, not from anything on disk. A conda env
    can hold a universal interpreter next to single-architecture extension
    modules, and then that inherited preference decides whether the tool runs at
    all. Live case (2026-08-22): an osx-arm64 kraken env whose perl was
    universal (x86_64 + arm64) with arm64-only XS bundles — launched from a tree
    preferring x86_64, perl took its x86_64 slice and died loading its own Cwd,
    while the identical command from an arm64 shell worked.

    Kept in lock-step with install-local.sh's arch_prefix: the proxy dashboard
    launches through here and `bdtools local` launches through there, and two
    launchers disagreeing about the environment is a failure mode this suite has
    already paid for more than once.
    """
    if platform.system() != "Darwin" or not env_dir or env_dir == "(base)":
        return []
    if not os.path.exists("/usr/bin/arch"):
        return []
    sub = _env_subdir(env_dir)
    # No host check: platform.machine() returns "x86_64" inside a translated
    # process, so gating on it switches the pin OFF in the very case it exists
    # for — a Rosetta-translated dashboard launching an arm64 env's tool. An
    # osx-arm64 env implies an arm64 host; nothing else needs asserting.
    if sub == "osx-arm64":
        return ["/usr/bin/arch", "-arm64"]
    if sub == "osx-64":
        # The deliberate Rosetta case; pin it too, so a universal binary there
        # cannot pick an arm64 slice its neighbours are not built for.
        return ["/usr/bin/arch", "-x86_64"]
    return []


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
    # The BDTOOLS_* site paths must ride along, or a terminal re-run resolves its
    # databases and shared projects differently from the dashboard run it claims
    # to reproduce.
    for k in ("PYTHONPATH", "CONDA_PREFIX",
              "BDTOOLS_HOME", "BDTOOLS_TOOLS_ROOT", "BDTOOLS_DB_ROOT",
              "BDTOOLS_SHARED_PROJECTS_ROOT",
              "VSNP_GUI_SITE_ROOT", "VSNP_GUI_SHARED_PROJECTS_ROOT"):
        if k in ov:
            assigns.append("%s=%s" % (k, shlex.quote(ov[k])))
    # The sibling handoff (BDTOOLS_SIBLING_ENV_*/BDTOOLS_SIBLING_ARCH_*/
    # BDTOOLS_SIBLING_ENVS) must ride along too: a terminal re-run that drops
    # them resolves a sibling's binaries from a different env — or runs them
    # under the wrong architecture slice — than the dashboard run it claims to
    # reproduce, which is exactly the cross-env drift those exports prevent.
    for k in sorted(ov):
        if k.startswith("BDTOOLS_SIBLING_"):
            assigns.append("%s=%s" % (k, shlex.quote(ov[k])))
    argv = " ".join(shlex.quote(a) for a in plan["argv"])
    prefix = (" ".join(assigns) + " ") if assigns else ""
    return "cd %s && %s%s" % (shlex.quote(plan["cwd"]), prefix, argv)


def log_header(plan, when=None):
    """A commented banner + the reproduce command, prepended to a tool's log file
    at launch so every run records the exact terminal command that produced it."""
    when = when or time.strftime("%Y-%m-%d %H:%M:%S %z")
    bar = "# " + "=" * 68
    # Missing vendored assets are recorded in the run log too: the symptom shows up
    # much later (a pipeline exiting 127), and the log is where anyone will look.
    warn_block = "".join("# WARNING: %s\n" % w for w in plan.get("warnings") or [])
    # Notices go in the log too — a run's log is the permanent record of what
    # was true when it ran, and "your configuration was changed before this run"
    # belongs in it. One run, one log, so "once" is satisfied by construction.
    warn_block += "".join("# NOTE: %s\n" % n for n in plan.get("notices") or [])
    return (
        "\n%s\n"
        "# bdtools tool launch — %s\n"
        "# started: %s\n"
        "# python env: %s\n"
        "# open files: %s (soft RLIMIT_NOFILE, inherited by every analysis subprocess)\n"
        "%s"
        "# Reproduce this exact run from a terminal (copy/paste the line below):\n"
        "#\n"
        "%s\n"
        "#\n"
        "%s\n"
    ) % (bar, plan.get("tool", "?"), when, plan.get("env_dir", "?"),
         file_limit() or "unknown", warn_block, reproduce_command(plan), bar)


def _cli():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    action, tool, port = sys.argv[1], sys.argv[2], sys.argv[3]
    host = "127.0.0.1"
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    plan = resolve(tool, int(port), host=host)
    # stderr, so `cmd`/`repro` output stays machine-consumable by the bash shim.
    for w in plan.get("warnings") or []:
        sys.stderr.write("WARNING: %s\n" % w)
    for n in plan.get("notices") or []:
        sys.stderr.write("NOTE: %s\n" % n)
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
