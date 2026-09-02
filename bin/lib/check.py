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
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements  # noqa: E402

try:
    import config_hygiene  # noqa: E402  (sibling module, stdlib-only)
except ImportError:        # pragma: no cover — doctor must never fail to load
    config_hygiene = None

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


# Import-name -> conda package for the ANALYSIS modules the specs probe. A
# module that does not import is a gap in ONE package, and naming that package
# is what lets this report offer a targeted install. Without this table any
# non-web gap fell through to the tool's `fix` string — a full env rebuild — and
# that remedy is both the largest available action (it re-solves every conda
# package, the operation that has already broken a working kraken env on macOS)
# and, for a report-only tool, one that refuses to run at all. So the card said
# "Needs setup before it can run" and named the single command guaranteed to do
# nothing. Reinstalling one package cannot re-solve the env, and it works on
# every tool regardless of update policy.
CONDA_MODULES = {
    "Bio": "biopython",
    "PIL": "pillow",
    "allel": "scikit-allel",
    "cairosvg": "cairosvg",
    "humanize": "humanize",
    "jinja2": "jinja2",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "pandas": "pandas",
    "plotly": "plotly",
    "pysam": "pysam",
    "svgwrite": "svgwrite",
    "yaml": "pyyaml",
}


# Variables that redirect an interpreter away from its own libraries, stripped
# from every probe subprocess. Doctor grades the TOOL's environment, not the
# shell doctor happens to run in: an HPC site module exporting PYTHONHOME makes
# the env's own python report the module's prefix as sys.prefix, and doctor
# then declared a healthy interpreter "another env's python" and prescribed a
# force-reinstall that could not change anything — the production launcher
# never sees that variable. PERL5LIB/PERLLIB/PERL5OPT/RUBYLIB are the same
# lever for the other interpreters, and LD_PRELOAD injects libraries into
# every child outright. LD_LIBRARY_PATH is deliberately KEPT: production
# children inherit it too, so stripping it would probe a configuration nobody
# runs — instead its presence is reported as a note (see loader_env_notes),
# because on HPC it redirects loading the way Rosetta redirected slices.
_PROBE_STRIP_VARS = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP",
                     "PERL5LIB", "PERLLIB", "PERL5OPT", "RUBYLIB", "LD_PRELOAD")


def _probe_env():
    """os.environ minus _PROBE_STRIP_VARS — what every probe child runs with."""
    env = dict(os.environ)
    for var in _PROBE_STRIP_VARS:
        env.pop(var, None)
    return env


# Memoized per real path for the life of the process. run_checks consults the
# env's subdir five-plus times per tool, and every un-cached call re-reads and
# re-parses every conda-meta/*.json — on an NFS/Lustre home directory that is
# a per-tool metadata storm doctor inflicts on the machine it is diagnosing.
# An env's platform changes only during a conda transaction, never during one
# doctor run, so one read per env is the honest amount of work.
_SUBDIR_CACHE = {}


def env_conda_subdir(envdir):
    """The conda platform this env was built for — majority of its packages.

    Same rule as common.sh:env_conda_subdir, in python because the remedy string
    is built here. A `conda install` that omits it re-solves for the HOST
    platform, so on Apple Silicon it links osx-arm64 packages into an osx-64
    env; the result runs until the analysis calls the binary and then dies with
    "incompatible architecture". A repair must not be able to cause that.
    """
    key = os.path.realpath(str(envdir))
    if key in _SUBDIR_CACHE:
        return _SUBDIR_CACHE[key]
    counts = {}
    for meta in Path(envdir, "conda-meta").glob("*.json"):
        try:
            sd = json.loads(meta.read_text(encoding="utf-8")).get("subdir", "")
        except Exception:
            continue
        if sd and sd != "noarch":
            counts[sd] = counts.get(sd, 0) + 1
    out = max(counts, key=counts.get) if counts else ""
    _SUBDIR_CACHE[key] = out
    return out


def _pip_cmd(env_py, tool_dir, web):
    """Bare pip command restoring the named web-layer packages."""
    pkgs = " ".join(sorted({PIP_WEB_MODULES[m] for m in web}))
    req = Path(tool_dir, "backend", "requirements.txt") if tool_dir else None
    if req and req.is_file():
        return f'"{env_py}" -m pip install -r "{req}" {pkgs}'
    return f'"{env_py}" -m pip install {pkgs}'


def _conda_cmd(env_py, conda_missing):
    """Bare conda command reinstalling the named analysis packages, pinned to
    the platform the env was actually built for."""
    envdir = str(Path(env_py).parent.parent)
    subdir = env_conda_subdir(envdir)
    pre = f"CONDA_SUBDIR={subdir} " if subdir else ""
    pkgs = " ".join(sorted({CONDA_MODULES[m] for m in conda_missing}))
    return (f'{pre}conda install -y -p "{envdir}" '
            f'-c conda-forge -c bioconda {pkgs}')


def web_layer_fix(env_py, tool_dir, missing):
    """The targeted remedy when every missing module is pip-owned web layer.

    Prefers the tool's own backend/requirements.txt (exactly what a healthy
    install would have pip-installed); the missing packages are also named
    explicitly so an older checkout whose requirements.txt predates a
    declaration (e.g. python-multipart) still ends up complete. Returns None
    when any missing module is an analysis package — those need conda, so the
    caller asks module_fix for the combined remedy."""
    if not missing or any(m not in PIP_WEB_MODULES for m in missing):
        return None
    return (_pip_cmd(env_py, tool_dir, missing)
            + "   # restores the web layer; analysis packages untouched")


def module_fix(env_py, tool_dir, missing):
    """Targeted remedy for missing python modules, or None if one is unknown.

    The web layer is pip-owned and the analysis modules are conda-owned, so a
    gap that spans both needs both commands — the real case this was written
    for reported fastapi, uvicorn AND pysam together, and answering only the
    pip half would have left the tool still unable to run. Returns None only
    when a missing module maps to neither table: then nobody here knows what to
    install and the caller's rebuild remedy is the honest answer.

    Kept to ONE line: `bdtools fix` carries remedies through a tab-separated
    plan, so a newline in a command truncates the plan silently.
    """
    if not missing:
        return None
    web = [m for m in missing if m in PIP_WEB_MODULES]
    conda_missing = [m for m in missing if m in CONDA_MODULES]
    if len(set(web)) + len(set(conda_missing)) != len(set(missing)):
        return None
    if not conda_missing:
        return web_layer_fix(env_py, tool_dir, web)
    cmds = []
    if web:
        cmds.append(_pip_cmd(env_py, tool_dir, web))
    cmds.append(_conda_cmd(env_py, conda_missing))
    return (" && ".join(cmds)
            + "   # installs only what is missing; the env is not re-solved")


def stale_python_trees(env_py):
    """(current, [stale]) lib/pythonX.Y trees in this env.

    A conda transaction that moves python's minor version relinks conda's own
    py-ABI packages for the new one, but conda does not own pip-installed files
    — those stay in the OLD tree, where nothing looks for them any more. The
    symptom is a handful of modules that imported yesterday and don't today,
    with every conda package present and correct, which reads exactly like a
    broken env and gets answered with a rebuild. It isn't one, and a rebuild is
    a far bigger risk than the pip install that actually fixes it, so say what
    happened.
    """
    envdir = Path(env_py).parent.parent
    # Symlinks excluded: conda ships lib/python3.1 -> python3.10 in every env,
    # and counting that as a second tree reported a python version change on
    # every healthy install.
    trees = sorted(p.name for p in envdir.glob("lib/python3.*")
                   if p.is_dir() and not p.is_symlink())
    if len(trees) < 2:
        return "", []
    try:
        cur = subprocess.run(
            [env_py, "-c",
             "import sys; print('python%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=60,
            env=_probe_env()).stdout.strip()
    except Exception:
        cur = ""
    if not cur:
        return "", []
    return cur, [t for t in trees if t != cur]


# The probe that runs INSIDE the tool's env interpreter. It reports why each
# import failed, not just that it did — see check_modules.
_IMPORT_PROBE = r'''
import json, sys
out = {}
for m in sys.argv[1:]:
    try:
        __import__(m)
    except BaseException as e:
        # Record the distinct top-level packages the import passed through. The
        # DEEPEST frame is not the culprit: when `allel` failed on macOS the
        # deepest frame was numpy raising AttributeError, while the package that
        # actually needed updating was dask, one frame above it. A chain shows
        # that relationship instead of guessing a single name.
        chain, tb = [], e.__traceback__
        while tb is not None:
            parts = tb.tb_frame.f_code.co_filename.replace("\\", "/").split("/")
            for i, part in enumerate(parts):
                if part in ("site-packages", "dist-packages") and i + 1 < len(parts):
                    top = parts[i + 1]
                    if top.endswith(".py"):
                        top = top[:-3]
                    if top and (not chain or chain[-1] != top):
                        chain.append(top)
                    break
            tb = tb.tb_next
        name = getattr(e, "name", "") or ""
        out[m] = {
            # "absent" = nothing to import. Anything else is INSTALLED and
            # broken, which needs a different remedy entirely.
            "absent": bool(isinstance(e, ModuleNotFoundError)
                           and name in (m, m.split(".")[0])),
            "error": "%s: %s" % (type(e).__name__, " ".join(str(e).split())[:300]),
            "chain": chain,
        }
# Bracketed by a sentinel so the parent can find the payload ANYWHERE in
# stdout. "Last line of stdout" broke the moment one probed module registered
# an atexit handler that prints (update notices, telemetry banners): that text
# lands AFTER this line, the parse failed, and every module in the spec was
# reported missing on a healthy env.
print("@@BDTOOLS@@" + json.dumps(out) + "@@BDTOOLS@@")
'''


# Returned by check_modules INSTEAD of a findings dict when the probe hit its
# time limit. A timeout is a fact about the filesystem, not the packages: on a
# cold NFS/Lustre cache the first-touch page-in of numpy/pandas/pysam shared
# objects can exceed any budget, and folding that into the generic "can't run
# the interpreter" answer printed "python modules missing: fastapi, uvicorn,
# ... pandas, pysam" — fifteen install commands — over a perfectly healthy env
# on the platform where doctor is trusted least. The caller must turn this
# into a note, never into a missing-modules finding.
MODULE_PROBE_TIMED_OUT = object()
_MODULE_PROBE_SECS = 120


def check_modules(env_py, modules, probe_env=None):
    """Which of `modules` fail to import in the tool's env, and WHY.

    Returns a dict, in spec order, {module: {"absent": bool, "error": str,
    "chain": [pkg, ...]}} — empty when every import works. Falsy-empty either
    way, so callers guard on it exactly as they did when this returned a list.
    Returns MODULE_PROBE_TIMED_OUT (a sentinel, not a dict) when the probe ran
    out of time — see that constant for why the distinction is load-bearing.

    Actually imports each module in the env interpreter (a real "does it work"
    test, not just "is it discoverable"). Each import is guarded independently so
    one failure doesn't hide the rest, and the script always completes and prints
    the failures. (An earlier version used `import importlib` + `importlib.util`,
    but `import importlib` does not expose the `util` submodule — it raised
    AttributeError, so the check silently passed while testing nothing, and could
    flip to "all missing" when the interpreter path wasn't runnable.)

    WHY THE REASON IS KEPT. This used to `except Exception` and append only the
    module name, so "not installed" and "installed but raises on import" arrived
    at the screen identically — and the remedy was chosen from the install
    tables, which answers only the first. The live case: scikit-allel was
    present and correct, `import allel` died inside dask on numpy 2's removal of
    `np.round_`, and the dashboard offered `conda install scikit-allel`, which
    conda answers with "All requested packages already installed". A dead end on
    every platform, with the one useful fact — the traceback — discarded here.
    """
    if not modules:
        return {}
    unknown = {m: {"absent": True, "error": "", "chain": []} for m in modules}
    try:
        out = subprocess.run([env_py, "-c", _IMPORT_PROBE, *modules],
                             capture_output=True, text=True,
                             timeout=_MODULE_PROBE_SECS,
                             env=probe_env or _probe_env())
        # If the interpreter couldn't even start the script (nonzero exit with no
        # output), we can't say which imports failed — report all as missing.
        if out.returncode != 0 and not out.stdout.strip():
            return unknown
        # Extract by sentinel, so a module that prints after the payload (an
        # atexit update notice) cannot corrupt the parse — see _IMPORT_PROBE.
        hit = re.search(r"@@BDTOOLS@@(.*?)@@BDTOOLS@@", out.stdout, re.DOTALL)
        data = json.loads(hit.group(1) if hit
                          else out.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        # NOT the all-missing answer: the interpreter was still working, the
        # filesystem was slow. Reporting fifteen missing packages here is the
        # highest-volume cry-wolf vector on HPC — see MODULE_PROBE_TIMED_OUT.
        return MODULE_PROBE_TIMED_OUT
    except Exception:
        return unknown  # can't even run the interpreter -> all "missing"
    # Preserve the spec's order, not the JSON's, so messages read the same way
    # every run.
    return {m: data[m] for m in modules if m in data}


def broken_import_fix(env_py, module, info):
    """Remedy for a module that is INSTALLED but raises on import, or None.

    An import that dies partway through a chain (allel -> dask -> numpy) is two
    installed packages disagreeing, not a missing one. The package to move is
    the CALLER in that chain — the stale one reaching for something its
    dependency has removed — never the module itself (present already) and never
    the deepest package (it is the one that dropped the API; downgrading it is
    how you get an env nobody can reproduce).

    Returns None when the chain says nothing useful; then the error text is the
    whole answer, which is still infinitely better than a no-op install.

    Kept to ONE line — `bdtools fix` carries remedies through a tab-separated
    plan, and a newline truncates the plan silently.
    """
    chain = [c for c in (info.get("chain") or []) if c]
    top = module.split(".")[0]
    # Drop the module's own package and the deepest frame; what's left is the
    # caller(s) between them.
    middle = [c for c in chain[:-1] if c != top]
    if not middle:
        return None
    envdir = str(Path(env_py).parent.parent)
    subdir = env_conda_subdir(envdir)
    pre = f"CONDA_SUBDIR={subdir} " if subdir else ""
    pkgs = " ".join(sorted(set(middle)))
    deepest = chain[-1] if chain else ""
    why = f" # {pkgs} is too old for the {deepest} in this env" if deepest else ""
    return (f'{pre}conda update -y -p "{envdir}" '
            f'-c conda-forge -c bioconda {pkgs}{why}')


def _shared_lib_from_error(err):
    """Basename of the native library a failed import could not load, or ''.

    Covers the shapes a dynamic-linker failure reaches python with. macOS:
    `dlopen(...): Library not loaded: @rpath/libssl.3.dylib` and
    `tried: '...' (mach-o file, but is an incompatible architecture ...)`.
    Linux/glibc: `libssl.so.3: cannot open shared object file`,
    `symbol lookup error: /path/libX.so: undefined symbol: foo`,
    ``version `GLIBC_2.34' not found (required by /path/libX.so)``, and the
    ImportError variant `/env/.../_ext.so: undefined symbol: PyFloat_...`.
    musl (Alpine): `Error loading shared library libz.so.1: ...`. The Linux
    shapes are checked AFTER the macOS ones so macOS behavior is unchanged —
    without them, shared_lib_fix returned (None, None) on every glibc failure
    and the report fell through to the import-chain heuristic, the exact
    "confidently wrong" remedy path this function exists to preempt."""
    m = re.search(r"Library not loaded:\s*'?(\S+?)'?(?:\s|$|\))", err)
    if m:
        return os.path.basename(m.group(1).strip("'\""))
    m = re.search(r"([A-Za-z0-9_.+-]+\.so(?:\.[0-9.]+)*)\s*:\s*cannot open shared object",
                  err)
    if m:
        return m.group(1)
    m = re.search(r"tried:\s*'([^']+)'[^']*incompatible architecture", err)
    if m:
        return os.path.basename(m.group(1))
    m = re.search(r"symbol lookup error:\s*(\S+?):", err)
    if m:
        return os.path.basename(m.group(1))
    m = re.search(r"(\S+\.so[\w.]*)\s*:\s*undefined symbol", err)
    if m:
        return os.path.basename(m.group(1))
    m = re.search(r"Error loading shared library\s+([^\s:]+)", err)
    if m:
        return os.path.basename(m.group(1))
    m = re.search(r"not found \(required by (\S+?)\)", err)
    if m:
        return os.path.basename(m.group(1))
    return ""


def _conda_pkg_owning(envdir, libname):
    """(name, version, subdir, channel) of the installed conda package that
    ships `libname`, read from conda-meta's per-package file lists."""
    needle = "/" + libname
    for meta in Path(envdir, "conda-meta").glob("*.json"):
        try:
            rec = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        for f in rec.get("files") or []:
            if f == libname or f.endswith(needle):
                return (rec.get("name", ""), rec.get("version", ""),
                        rec.get("subdir", ""), rec.get("channel", ""))
    return ("", "", "", "")


def _channel_name(channel):
    """'conda-forge' from either 'conda-forge' or a full channel URL with a
    trailing platform component."""
    parts = [p for p in (channel or "").split("/") if p and p not in ("https:", "http:")]
    if parts and re.fullmatch(r"noarch|linux-\w+|osx-\w+|win-\w+", parts[-1]):
        parts = parts[:-1]
    return parts[-1] if parts else "conda-forge"


def shared_lib_fix(env_py, module, info):
    """(fix, why) when an import died loading a NATIVE library, else (None, None).

    A dlopen failure is not a python-package conflict, and reasoning about it
    with the import-chain heuristic produced confidently wrong output: on the
    2026-08 macOS incident, ONE osx-64 openssl inside an osx-arm64 env broke
    `import ssl`, doctor reported fastapi, uvicorn and pysam as three separate
    package problems, blamed their import chains, and prescribed a full env
    rebuild — which failed twice without touching the actual defect. The
    accurate report is one line: name the library, name the conda package that
    owns it, and reinstall that package for the env's own platform.

    The remedy's spec is deliberately channel/subdir-QUALIFIED
    (`conda-forge/osx-arm64::openssl`): a bare `--force-reinstall openssl` is
    already satisfied by the foreign build, so conda re-links the same wrong
    package out of its cache and reports success — a convincing false negative,
    observed on the machine this was written for.

    Kept to ONE line — `bdtools fix` carries remedies through a tab-separated
    plan, and a newline truncates the plan silently.
    """
    err = info.get("error") or ""
    lib = _shared_lib_from_error(err)
    if not lib:
        return None, None
    envdir = str(Path(env_py).parent.parent)
    name, version, pkg_subdir, channel = _conda_pkg_owning(envdir, lib)
    if not name:
        # Some libraries were never conda's to ship, and the generic "only a
        # rebuild can restore it" is doubly wrong for them: the file was
        # expected from the operating system or a driver, and no number of env
        # rebuilds will produce it. The classic on CPU-only Linux nodes is a
        # GPU-optional package narrating "libcuda.so.1: cannot open shared
        # object file" — steering that user to the largest, least effective
        # action is how doctor loses its credibility.
        low = lib.lower()
        if low.startswith(("libcuda", "libnvidia")):
            return None, (f"cause: {lib} is a GPU driver library — it comes "
                          f"from the NVIDIA driver, not from this env. On a "
                          f"machine without that driver the GPU path is simply "
                          f"unavailable; the CPU path is unaffected, and no "
                          f"rebuild can produce a driver")
        if low.startswith(("libcrypt.so.1", "libxcrypt")):
            # The one system library with an in-env fix: modern distros
            # (RHEL9, Ubuntu 24.04) dropped libcrypt.so.1, and conda-forge
            # ships libxcrypt as exactly the shim older bioconda builds need.
            env_subdir = env_conda_subdir(envdir)
            pre = f"CONDA_SUBDIR={env_subdir} " if env_subdir else ""
            fix = (f'{pre}conda install -y -p "{envdir}" -c conda-forge '
                   f'libxcrypt   # restores libcrypt.so.1 inside the env, no '
                   f'root needed')
            why = (f"cause: this program was built against {lib}, which modern "
                   f"distros no longer ship — the host removed it, the env "
                   f"never had it, and a rebuild re-solves to the same builds; "
                   f"libxcrypt is the conda-installable shim that puts it back")
            return fix, why
        return None, (f"cause: the shared library {lib} failed to load — a "
                      f"native-library problem, not a conflict between the python "
                      f"packages in the import chain; no installed conda package "
                      f"ships that file, so only a rebuild can restore it")
    env_subdir = env_conda_subdir(envdir)
    spec_subdir = env_subdir or pkg_subdir
    spec = f"{_channel_name(channel)}/{spec_subdir}::{name}" if spec_subdir else name
    if env_subdir and pkg_subdir and pkg_subdir != env_subdir:
        why = (f"cause: {lib} belongs to {name} {version}, installed as {pkg_subdir} "
               f"inside an {env_subdir} env — a mixed-architecture install; "
               f"reinstalling {name} for {env_subdir} repairs it in place")
    else:
        why = (f"cause: {lib} belongs to {name} {version} — its files are broken "
               f"or unloadable, so reinstall that one package, not the modules "
               f"that stumbled over it")
    pre = f"CONDA_SUBDIR={env_subdir} " if env_subdir else ""
    fix = (f'{pre}conda install -y -p "{envdir}" --no-deps --force-reinstall "{spec}"'
           f'   # {lib} -> {name}; the qualified spec matters: a bare "{name}" is '
           f'already satisfied and re-links the same broken build')
    return fix, why


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


def _same_or_under(path, root):
    """Is `path` the same file/dir as `root`, or somewhere inside it?

    Path identity by string prefix alone carries two known lies. Symlinks:
    macOS parks /tmp and /var behind /private, so an env created under /tmp
    never string-matches its own realpath — both sides are resolved first.
    Case: the default macOS filesystem (APFS) folds case, so ONE directory
    reached as .../BDTools and .../bdtools fails every startswith() test —
    which classified a healthy in-env interpreter as belonging to "another
    conda env" (the same env, spelled differently) and, on the loader-smoke
    side, silently SKIPPED binaries it should have probed. Folding is applied
    only on macOS (with os.path.normcase for any platform where it acts):
    Linux filesystems are case-sensitive, and folding there would merge
    genuinely distinct paths into false ownership.
    """
    p = os.path.normcase(os.path.realpath(str(path)))
    r = os.path.normcase(os.path.realpath(str(root)))
    if platform.system() == "Darwin":
        p, r = p.lower(), r.lower()
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


# Executable-format magic bytes, plus the CPU field each format carries:
# ELF e_machine (offset 0x12, uint16 LE) and Mach-O cputype (offset 4, uint32 LE).
# The OS alone is not enough — see check_binary_format.
_ELF_MAGIC = b"\x7fELF"
_MACHO_THIN = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe")
_MACHO_FAT = b"\xca\xfe\xba\xbe"
# e_machine values, with the 64-bit machines host_conda_subdir claims to
# support: EM_PPC64 (0x15) reads "ppc64le" or "ppc64" by the file's own
# endianness, EM_S390 (0x16) is s390x, EM_RISCV (0xF3) is riscv64. Before
# these, every ELF on a ppc64le HPC host parsed to "unknown" — the suite
# claimed ppc support in common.sh and could not audit it here.
_ELF_MACHINES = {0x03: "i386", 0x3E: "x86_64", 0xB7: "arm64", 0x28: "arm",
                 0x15: "ppc64", 0x16: "s390x", 0xF3: "riscv64"}
_MACHO_CPUS = {0x00000007: "i386", 0x01000007: "x86_64", 0x0100000C: "arm64"}

# Memoized per path for the life of the process: the fat-or-thin question is
# now asked once per interpreter probe AND once per smoke launch (the pin
# decision hangs on it — see _pin_for_exec), and the answer for a given file
# cannot change mid-run.
_SLICE_CACHE = {}


def _macho_slices(path):
    """Architecture names inside a Mach-O file: [] for non-fat, one per slice
    for a universal binary. A fat file matches every host, which is exactly why
    it can hide a runtime mismatch — see interpreter_smoke_test."""
    if path in _SLICE_CACHE:
        return _SLICE_CACHE[path]
    names = _read_macho_slices(path)
    _SLICE_CACHE[path] = names
    return names


def _read_macho_slices(path):
    import struct
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
            if head[:4] != _MACHO_FAT:
                return []
            n = struct.unpack(">I", head[4:8])[0]
            if not 0 < n < 32:
                return []
            names = []
            for _ in range(n):
                rec = fh.read(20)
                if len(rec) < 20:
                    break
                cpu = struct.unpack(">i", rec[0:4])[0] & 0xFFFFFFFF
                names.append(_MACHO_CPUS.get(cpu, "0x%x" % cpu))
            return names
    except OSError:
        return []


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
        # EI_DATA (byte 5) declares the file's own byte order — reading
        # e_machine as little-endian unconditionally parsed every big-endian
        # ELF (s390x, ppc64 BE) into garbage machine numbers. Big-endian only
        # when the file SAYS so (2); anything else falls back to LE rather
        # than flipping on a malformed header.
        big = head[5] == 2
        m = struct.unpack(">H" if big else "<H", head[18:20])[0]
        arch = _ELF_MACHINES.get(m, "unknown")
        if m == 0x15:
            arch = "ppc64" if big else "ppc64le"
        # EI_CLASS (byte 4): 1 means a 32-bit file. A 32-bit build of a 64-bit
        # machine type must NOT wear the 64-bit name, or a wrong-ELF-class .so
        # in a linux-64 env passes the audit and dies at run time with
        # "wrong ELF class: ELFCLASS32" — give it a distinct arch instead.
        if head[4] == 1 and arch == "x86_64":
            arch = "x86"
        elif head[4] == 1 and arch in ("arm64", "ppc64", "ppc64le",
                                       "s390x", "riscv64"):
            arch += "-32"
        return "linux", arch
    if magic in _MACHO_THIN:
        c = struct.unpack("<I", head[4:8])[0]
        return "macos", _MACHO_CPUS.get(c, "unknown")
    if magic == _MACHO_FAT:
        # 0xCAFEBABE is ALSO the Java class-file magic, where bytes 4-8 hold
        # the class-file version (>= 45 in the low half, or big values with
        # preview bits set). A .class in <env>/bin was reported "built for
        # macOS (Mach-O) universal but this host is Linux" — a wrong-OS
        # verdict on a file that is neither. Real fat headers carry 2-3
        # slices, so validate the count exactly like _macho_slices does.
        n = struct.unpack(">I", head[4:8])[0]
        if 0 < n < 32:
            return "macos", "universal"
        return None
    return None


# One sysctl per process: _host_target is consulted per file in the arch
# audit, and the kernel's answer cannot change mid-run.
_DARWIN_ARCH = []


def _darwin_host_arch():
    """The CPU this Mac actually has — never what platform.machine() claims.

    platform.machine() reports the architecture of the CURRENT process, so a
    doctor run under Rosetta (an x86_64 base python — the suite's own default
    on Apple Silicon via ensure_conda_subdir rule 2, or an x86_64 terminal)
    answers "x86_64" on an arm64 machine. That is the exact lie the arch-pin
    work removed from every other guard: judging the host by it reported a
    native arm64 vendored binary as unrunnable on the machine that runs it
    natively. The kernel knows better — hw.optional.arm64 is 1 on Apple
    Silicon no matter which slice asks.
    """
    if _DARWIN_ARCH:
        return _DARWIN_ARCH[0]
    arch = ""
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip() == "1":
            arch = "arm64"
    except Exception:
        pass
    if not arch:
        m = platform.machine().lower()
        arch = {"x86_64": "x86_64", "amd64": "x86_64",
                "arm64": "arm64", "aarch64": "arm64"}.get(m, m)
    _DARWIN_ARCH.append(arch)
    return arch


def _host_target():
    os_name = {"Linux": "linux", "Darwin": "macos"}.get(
        platform.system(), platform.system().lower())
    if os_name == "macos":
        return os_name, _darwin_host_arch()
    # uname is truthful on Linux, WSL2 included — no translation layer lies.
    m = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64"}.get(m, m)
    return os_name, arch


_ROSETTA = []                       # memoized: one exec per process


def _rosetta_available():
    """Can this Apple Silicon host run x86_64 binaries?"""
    if _ROSETTA:
        return _ROSETTA[0]
    try:
        ok = subprocess.run(["/usr/bin/arch", "-x86_64", "/usr/bin/true"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=10).returncode == 0
    except Exception:
        ok = False
    _ROSETTA.append(ok)
    return ok


def _host_can_run(target):
    """Can THIS machine exec a binary targeting (os, arch)?

    The same runnability rules check_binary_format applies, factored out so
    other checks can ask the question without inheriting its messages: an
    osx-64 file on Apple Silicon with Rosetta is runnable, x86_64 on aarch64
    Linux is not. "Foreign to the env" and "unrunnable on the host" are
    different verdicts with different remedies, and conflating them is how a
    deliberately-installed osx-64 package got reported as corruption.
    """
    if not target:
        return False
    bin_os, bin_arch = target
    host_os, host_arch = _host_target()
    if bin_os != host_os:
        return False
    if bin_arch in ("universal", host_arch):
        return True
    if host_os == "macos" and host_arch == "arm64" and bin_arch == "x86_64":
        return _rosetta_available()
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


# subdir -> the (os, arch) every native binary in such an env must target.
# Platforms with no entry (win-*, ppc) skip the audit rather than invent a rule.
_SUBDIR_TARGET = {"osx-64": ("macos", "x86_64"), "osx-arm64": ("macos", "arm64"),
                  "linux-64": ("linux", "x86_64"), "linux-aarch64": ("linux", "arm64")}

# A path component that names a platform is a package selecting per-platform
# payloads on purpose, not a broken install — see foreign_arch_files. Applied
# to EVERY component of the env-relative path, not the basename alone: node
# trees ship '@img/sharp-linux-x64/libvips-cpp.so' and conda cross-toolchains
# ship 'lib/gcc/aarch64-conda-linux-gnu/.../libgcc_s.so.1' — neutral basenames
# inside platform-tagged directories, the exact ont-fast5-api vendoring class
# one directory level up, and flagging those healthy files is the credibility
# cost this exclusion exists to avoid.
_ARCH_TAGGED_NAME = re.compile(
    r"(?:^|[-_.])(?:aarch64|arm64|x86[-_]?64|amd64|i[36]86|ppc64(?:le)?|s390x|"
    r"armv\d+|m1|win(?:32|64)|universal2?|manylinux\w*|musllinux\w*)(?=[-_.]|$)",
    re.IGNORECASE)

# One compiled "is this a native-library filename" test for the lib/ walk:
# *.so, *.so.N..., *.dylib, *.bundle. A single pattern over a single os.walk
# replaced four rglob passes that each re-traversed the same subtrees — on an
# env carrying R or Qt (10k-100k files under lib/) that was a stat-and-open
# storm repeated for all nine tools on every routine doctor run.
_NATIVE_LIB_NAME = re.compile(r"\.(?:so|dylib|bundle)(?:$|\.)")


def foreign_arch_files(envdir):
    """[(relpath, "os/arch")] of on-disk binaries that cannot run in this env.

    Judged against the ENV's platform (conda-meta majority), never the host's:
    an all-osx-64 env on Apple Silicon is fine — Rosetta runs the whole prefix —
    but a single x86_64 file inside an osx-arm64 prefix is dead on arrival,
    because everything it links against is arm64.

    WHY THIS EXISTS when the record-level check (env_foreign_subdirs) already
    reads conda-meta: records can lie about the disk. An interrupted conda
    transaction leaves files from TWO extractions in one prefix while the
    rollback restores the RECORDS — so every record says the right platform and
    doctor shows green. The live case (2026-08-21, the same Mac as the openssl
    incident): bin/perl on disk was x86_64 under an arm64 perl record. Every
    import check passed (python is not perl), every existence check passed (the
    kraken2 script was there), doctor said "all 9 tools ready" — and kraken2, a
    perl script, died at run time loading an arm64 Cwd.bundle into an x86_64
    perl. The disk is the authority; read it.

    Scope: <env>/bin plus native libraries under <env>/lib, EXCEPT the
    lib/python* tree and any node_modules tree. Two reasons lib/python* is out
    of scope, both learned from the first machine this ran on: (1) it is
    already proven the strong way — check_modules imports it in the env's own
    interpreter, so a wrong-arch .so there fails with the exact loader error
    and shared_lib_fix names the owner; (2) it is where packages DELIBERATELY
    vendor one plugin per platform (ont-fast5-api ships _m1.dylib,
    _aarch64.so and _x86_64.so side by side and picks at run time) — flagging
    those is a false positive that costs doctor its credibility. node_modules
    is the same vendoring pattern with the tag one level up (@img/
    sharp-linux-x64/...), so it is pruned without descending. Path components
    carrying an explicit platform tag are skipped for the same reason; the
    incident class this audit exists for (perl, openssl, libdb) always wears
    neutral names in neutral directories. Scripts and data files are skipped
    by the magic-byte check itself; symlinks are skipped so a target is
    judged once, where it lives. One os.walk, one compiled name pattern — the
    previous four rglob passes re-traversed every subtree four times.
    """
    subdir = env_conda_subdir(envdir)
    want = _SUBDIR_TARGET.get(subdir)
    if not want:
        return []
    env = str(envdir)
    candidates = []
    bindir = os.path.join(env, "bin")
    if os.path.isdir(bindir):
        try:
            with os.scandir(bindir) as it:
                candidates += [e.path for e in it
                               if e.is_file(follow_symlinks=False)]
        except OSError:
            pass
    libdir = os.path.join(env, "lib")
    if os.path.isdir(libdir):
        for dirpath, dirnames, filenames in os.walk(libdir):
            if dirpath == libdir:
                # the import probe's jurisdiction — pruned, never descended
                dirnames[:] = [d for d in dirnames
                               if not d.startswith("python")]
            dirnames[:] = [d for d in dirnames if d != "node_modules"]
            for fn in filenames:
                if _NATIVE_LIB_NAME.search(fn):
                    candidates.append(os.path.join(dirpath, fn))
    bad = []
    for p in candidates:
        rel = os.path.relpath(p, env)
        if any(_ARCH_TAGGED_NAME.search(part) for part in rel.split(os.sep)):
            continue                          # honest multi-platform vendoring
        if os.path.islink(p):
            continue
        target = _binary_target(p)
        if target is None or target[1] == "universal":
            continue
        if target != want:
            bad.append((rel, "%s/%s" % target))
    return sorted(set(bad))


def _packages_owning(envdir, relpaths):
    """{relpath: owner} for the conda packages shipping these files, where owner
    carries name, channel, the package's own recorded subdir (which platform
    THAT record claims — the fact that separates an interrupted transaction
    from a deliberate CONDA_SUBDIR install), the dist string
    (name-version-build, which is the conda-meta filename and the pkgs-cache
    directory name), and the cache paths conda recorded at install time
    (extracted_package_dir, tarball)."""
    want = set(relpaths)
    owners = {}
    for meta in Path(envdir, "conda-meta").glob("*.json"):
        try:
            rec = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        hits = want.intersection(rec.get("files") or [])
        if not hits:
            continue
        owner = {
            "name": rec.get("name", ""),
            "channel": rec.get("channel", ""),
            "subdir": rec.get("subdir", ""),
            "dist": meta.name[:-5],
            "extracted": rec.get("extracted_package_dir", "") or "",
            "tarball": rec.get("package_tarball_full_path", "") or "",
        }
        for f in hits:
            owners[f] = owner
        if len(owners) == len(want):
            break
    return owners


# Packages whose PUBLISHED build for a platform is itself wrong — files inside
# the official package do not match the platform it is published under. No
# local action can fix these: purging the cache re-downloads the same wrong
# bytes (verified for libdb by downloading BOTH conda-forge osx-arm64 builds
# straight from anaconda.org on 2026-08-22 — every one ships x86_64 binaries).
# Reporting them as failures forever would teach people to ignore doctor, and
# the remedy doctor would print (force-reinstall) provably does nothing — it
# was run, twice, with the cache purged in between, and the files never
# changed. So they are downgraded to a note WHEN the suite does not execute
# them, with the evidence stated. Keyed by (package name, env subdir).
KNOWN_UPSTREAM_FOREIGN = {
    ("libdb", "osx-arm64"):
        "every published conda-forge osx-arm64 build of libdb ships x86_64 "
        "binaries (upstream packaging defect, verified by direct download "
        "2026-08-22). Harmless here: only perl's DB_File links libdb and no "
        "declared tool uses it — but anything that ever does will fail with "
        "'incompatible architecture', and no reinstall can fix it.",
}


def _cache_copy_foreign(owner, relpath, want):
    """Is the pkgs-cache copy of this file ALSO the wrong architecture?

    If it is, the force-reinstall remedy is a no-op: conda re-links the same
    wrong bytes out of the cache and reports a successful transaction. Observed
    live (libdb, 2026-08-22): the env record said osx-arm64, the cache directory
    NAMED as the arm64 package held x86_64 files, and two qualified
    force-reinstalls "succeeded" without changing a byte. The remedy must purge
    the poisoned cache entry first, so conda re-downloads and re-extracts.
    Returns (is_foreign, cache_dir) — (False, "") when the cache copy is absent
    or healthy.
    """
    extracted = owner.get("extracted", "")
    if not extracted or not os.path.isdir(extracted):
        return False, ""
    cached = os.path.join(extracted, relpath)
    target = _binary_target(cached)
    if target is None or target[1] == "universal":
        return False, ""
    return (target != want), extracted


def script_interpreter(path):
    """Interpreter a script's shebang names: (name, is_env_form), or None.

    `#!/usr/bin/env perl` -> ("perl", True)  — resolved through PATH at run time
    `#!/abs/path/perl -w` -> ("/abs/path/perl", False)
    """
    try:
        with open(path, "rb") as fh:
            first = fh.readline(512)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    try:
        parts = first[2:].decode("utf-8", "replace").strip().split()
    except Exception:
        return None
    if not parts:
        return None
    if os.path.basename(parts[0]) == "env":
        rest = [p for p in parts[1:] if not p.startswith("-") and "=" not in p]
        return (rest[0], True) if rest else None
    return (parts[0], False)


# How to ask an interpreter where IT thinks its library root is. An interpreter
# can sit inside the env and still be another env's interpreter — see
# interpreter_library_root.
_LIBROOT_PROBE = {
    "perl":    ['-e', 'print join("\n", grep { $_ ne "." } @INC)'],
    "python":  ['-c', 'import sys; print(sys.prefix)'],
    "python3": ['-c', 'import sys; print(sys.prefix)'],
    "ruby":    ['-e', 'puts $LOAD_PATH'],
}


def interpreter_library_root(path, name, pin=()):
    """Directories an interpreter will load its libraries from, or [].

    WHY ASKING IS NECESSARY. That an interpreter FILE lives inside the env says
    nothing about which libraries it loads. A conda interpreter carries its
    library root baked into the binary, written at install time; if the file was
    hardlinked in from another prefix (or its prefix rewrite did not happen),
    the binary is that other env's interpreter wearing this env's path. PATH is
    powerless over it, and so is every check that only looks at where files are.

    The live case (2026-08-22): <checkout>/env/bin/perl existed, was executable,
    was first on PATH, and passed every check here — and `env perl` running it
    printed the LEGACY env's perl as $^X and loaded that env's @INC. kraken2, a
    perl script, therefore ran an x86_64 interpreter against arm64 modules and
    died at launch, while doctor reported the tool ready.

    Runs under the caller's `pin` (fat interpreters only — see _pin_for_exec)
    and the sanitized probe environment: a site module exporting PYTHONHOME
    makes the answer here point at the module's prefix, which read exactly
    like the hardlinked-interpreter incident on a healthy env.
    """
    probe = _LIBROOT_PROBE.get(name)
    if not probe:
        return []
    try:
        out = subprocess.run([*pin, path, *probe], capture_output=True,
                             text=True, timeout=30, env=_probe_env())
    except Exception:
        return []
    if out.returncode != 0:
        return []
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


# A smoke test per interpreter: load a NATIVE (compiled) module that ships with
# it. Not a formality — see interpreter_smoke_test.
_SMOKE_TEST = {
    "perl": (["-MCwd", "-e", "1"], "Cwd"),
    "python": (["-c", "import ssl, zlib"], "ssl/zlib"),
    "python3": (["-c", "import ssl, zlib"], "ssl/zlib"),
}


def interpreter_smoke_test(path, name, pin=()):
    """(error, module) if the interpreter cannot load its own native module.

    WHY EXECUTION IS THE ONLY HONEST TEST. Every check above reasons about
    files: where they are, what architecture the bytes claim, what the records
    say. A universal binary defeats all of it — it contains BOTH architectures,
    so it matches any host and any env, and macOS decides which slice actually
    runs from the process tree's inherited preference. That decision is invisible
    on disk.

    The 2026-08-22 case: an osx-arm64 env whose perl was universal (x86_64 +
    arm64) while its XS bundles were arm64-only. Run from an arm64 shell it was
    perfect; run under a tree preferring x86_64 it died loading its own Cwd. The
    file checks could not see it, because nothing about the FILES was wrong.
    Loading a native module through the interpreter reproduces it in
    milliseconds.
    """
    probe = _SMOKE_TEST.get(name)
    if not probe:
        return None, None
    args, module = probe
    try:
        out = subprocess.run([*pin, path, *args], capture_output=True, text=True,
                             timeout=60, env=_probe_env())
    except Exception:
        return None, None
    if out.returncode == 0:
        return None, None
    err = " ".join((out.stderr or out.stdout or "").split())[:400]
    return (err or "exited %d with no message" % out.returncode), module


def interpreter_findings(envdir, env_bin, binaries, extra_dirs=()):
    """[(label, fix, note)] for script interpreters that will not resolve inside
    this env.

    WHY THIS EXISTS. Existence checks pass on the SCRIPT; what runs it is a
    different file entirely. bioconda ships kraken2 as a perl script whose
    shebang is `#!/usr/bin/env perl`, so the perl that executes it is decided by
    PATH at run time — it is not necessarily the env's own perl, and doctor had
    no opinion about it. The live case (2026-08-21): every check was green,
    every program "found", and kraken2 died at launch because `env perl` landed
    in a DIFFERENT conda env whose perl binary and perl module tree disagreed
    about architecture:

        Can't load '.../envs/kraken_id_parse/lib/perl5/.../Cwd.bundle':
        (mach-o file, but is an incompatible architecture
         (have 'arm64', need 'x86_64'))

    An env whose scripts borrow an interpreter from outside is fragile even when
    it happens to work: it depends on PATH order and on the health of an
    environment nobody is grading. So report it, name what it resolved to, and
    prefer the remedy that makes the env self-contained.
    """
    subdir = env_conda_subdir(envdir)
    want = _SUBDIR_TARGET.get(subdir)
    # Group by interpreter so kraken2 and ktImportText report once, not twice.
    users = {}
    owned = [envdir] + [d for d in extra_dirs if d]
    for name in binaries:
        path = find_binary(name, env_bin, extra_dirs)
        if not path:
            continue                      # absence is the existence check's job
        # Judge only scripts this deployment OWNS — inside the env, or in a
        # vendored asset dir. A program found on the ambient PATH (a system
        # samtools, a module-loaded tool) brings its own interpreter with it by
        # definition, and flagging that would fire on every healthy machine
        # whose tools are not all conda-installed. Ownership is identity, not
        # string prefix — see _same_or_under for the case-folding lie.
        if not any(_same_or_under(path, o) for o in owned):
            continue
        got = script_interpreter(path)
        if not got:
            continue                      # a compiled binary, not a script
        interp, env_form = got
        # An absolute shebang pointing inside this env is self-contained by
        # construction — conda writes those and PATH cannot divert them.
        if not env_form and _same_or_under(interp, envdir):
            continue
        users.setdefault((interp, env_form), []).append(name)

    out = []
    for (interp, env_form), scripts in sorted(users.items()):
        who = ", ".join(sorted(set(scripts)))
        name = os.path.basename(interp)
        install = (f'CONDA_SUBDIR={subdir} conda install -y -p "{envdir}" '
                   f'-c conda-forge {name}' if subdir else
                   f'conda install -y -p "{envdir}" -c conda-forge {name}')
        if env_form:
            # `#!/usr/bin/env X`: PATH decides, so resolve it the way the
            # launcher's PATH would (env bin first, then vendored dirs, then
            # the ambient PATH).
            resolved = find_binary(name, env_bin, extra_dirs)
        else:
            resolved = interp if os.path.exists(interp) else None
        if not resolved:
            out.append((
                f"{who} run under '{interp}', which is not in this env or on PATH",
                install, None))
            continue
        real = os.path.realpath(resolved)
        if _same_or_under(resolved, envdir):
            # Inside the env by PATH — but is it this env's interpreter? Ask it
            # where its libraries are; a mis-prefixed or hardlinked-in binary
            # answers with another prefix and no PATH fix can reach it.
            # Probe under the SAME architecture pin the launcher applies —
            # but ONLY when the interpreter is a FAT binary. Doctor certifies
            # production, and production launches pinned: an unpinned probe
            # run from a Rosetta-translated dashboard took the x86_64 slice
            # of a universal perl, failed, and flagged "needs setup" over a
            # tool that was running analyses at that moment (2026-08-22, the
            # false-positive twin of the original bug). The fat gate is the
            # other half of the same lesson: /usr/bin/arch ENFORCES an
            # architecture while the launcher's inherited preference only
            # SELECTS among slices, so pinning a THIN foreign-arch
            # interpreter kills a binary Rosetta runs every day — see
            # _pin_for_exec.
            pin = _arch_pin(envdir) if len(_macho_slices(real)) > 1 else []
            roots = interpreter_library_root(real, name, pin=pin)
            # Compare roots by identity, not string prefix: an interpreter
            # reports the paths it was configured with, and on macOS an env
            # under /tmp or /var reports /var/... against a realpath of
            # /private/var/... . Comparing those raw flags every healthy env
            # behind a symlinked parent — the same cry-wolf failure the
            # /bin/bash rule above exists to avoid.
            smoke_err, smoke_mod = interpreter_smoke_test(real, name, pin=pin)
            if smoke_err:
                arches = _macho_slices(real)
                extra = ""
                if len(arches) > 1:
                    extra = (f" It is a universal binary ({', '.join(arches)}), so "
                             f"which slice runs is decided by the process it is "
                             f"launched from — not by anything on disk.")
                out.append((
                    f"{who} cannot run: {resolved} fails to load its own "
                    f"{smoke_mod} — {smoke_err}",
                    f"bin/bdtools install {os.path.basename(os.path.dirname(envdir))} --fresh",
                    f"cause: the interpreter executes but cannot load a native "
                    f"module that ships with it, so every script above dies at "
                    f"startup.{extra}"))
                continue
            if roots and not any(_same_or_under(r, envdir) for r in roots):
                _own = _packages_owning(envdir, [f"bin/{name}"]).get(
                    f"bin/{name}", {})
                owner = _own.get("name") or name
                chan = _own.get("channel", "")
                spec = (f'"{_channel_name(chan)}/{subdir}::{owner}"'
                        if subdir else owner)
                out.append((
                    f"{who} run under {resolved}, which is inside this env but "
                    f"loads its libraries from {roots[0]} — it is another env's "
                    f"{name}",
                    f'CONDA_SUBDIR={subdir} conda install -y -p "{envdir}" '
                    f'--no-deps --force-reinstall {spec}   # give this env a '
                    f'real {name} of its own' if subdir else
                    f'conda install -y -p "{envdir}" --no-deps '
                    f'--force-reinstall {spec}',
                    f"cause: the file is in this env, so every path check passes, "
                    f"but the binary carries another prefix baked in (hardlinked "
                    f"in, or its prefix rewrite never happened). PATH cannot "
                    f"redirect it: scripts shebanged '#!/usr/bin/env {name}' load "
                    f"{roots[0]} whatever PATH says."))
            continue

        target = _binary_target(real)
        arch_bad = bool(want and target and target[1] != "universal"
                        and target != want)
        # "Foreign to the env" is not "cannot run": the env's subdir constrains
        # the env's OWN libraries, not a self-contained outside interpreter's.
        # An Intel Mac migrated to Apple Silicon keeps an x86_64 Homebrew perl
        # that runs its own x86_64 module tree under Rosetta every day —
        # calling that "cannot run in this env" was factually wrong. The hard
        # verdict is reserved for combos this HOST genuinely cannot exec
        # (x86_64 on aarch64 Linux, wrong-OS binaries).
        runnable = _host_can_run(target) if arch_bad else True
        foreign_env = os.path.dirname(os.path.dirname(real))
        borrowed = os.path.isdir(os.path.join(foreign_env, "conda-meta"))
        # An OS interpreter (/bin/bash, /usr/bin/perl) is not a defect: it is
        # present on every machine, it is universal on macOS, and conda's own
        # wrapper scripts name it deliberately. Only three things are worth a
        # finding — an interpreter that is MISSING (handled above), one BORROWED
        # from another conda env (the 2026-08 incident: PATH order and that
        # env's health silently decide whether this tool runs), or one this
        # host cannot execute at all. Anything else would fire on every
        # healthy machine and teach people to ignore doctor.
        if not borrowed and not (arch_bad and not runnable):
            continue

        detail = f"{who} run under {resolved}, which is OUTSIDE this env"
        note = None
        if borrowed:
            other_subdir = env_conda_subdir(foreign_env)
            detail += f" (it belongs to another conda env: {foreign_env})"
            if target and target[1] != "universal" and other_subdir:
                other_want = _SUBDIR_TARGET.get(other_subdir)
                if other_want and target != other_want:
                    note = (f"cause: {resolved} is {target[0]}/{target[1]} while the "
                            f"env it belongs to records {other_subdir} — that env is "
                            f"itself split between its records and its files, so the "
                            f"interpreter and the modules it loads disagree about "
                            f"architecture. This is the failure that reaches the user "
                            f"as an 'incompatible architecture' error at run time.")
            if note is None:
                note = (f"cause: this tool's scripts depend on PATH order and on an "
                        f"environment nothing here grades; giving this env its own "
                        f"{name} makes the tool self-contained.")
        if arch_bad and not runnable:
            host_os, host_arch = _host_target()
            detail += (f" and is {target[0]}/{target[1]}, which this "
                       f"{host_os} {host_arch} host cannot execute")
        fix = (f"{install}   # give this env its own {name} so the shebang "
               f"stops resolving outside it")
        out.append((detail, fix, note))
    return out


# A dynamic-loader failure, in the words each platform uses. These are the
# messages that mean "the file is right there and still cannot execute" —
# distinct from a program that ran and disliked its arguments. Consulted ONLY
# when the process actually failed (nonzero exit or a signal): output alone
# is not a verdict, because GPU-probing tools on CPU-only nodes print "Could
# not load dynamic library libcudart.so..." as a WARNING and exit 0 — a
# process that exits 0 has proven it can start, whatever it printed.
_LOADER_ERRORS = re.compile(
    r"incompatible architecture"          # macOS: wrong slice / wrong arch
    r"|Library not loaded"                # macOS: missing dylib
    r"|image not found"                   # macOS: missing dylib (older wording)
    r"|Bad CPU type"                      # macOS: no slice this host can run
    r"|Symbol not found"                  # macOS: lib loaded, symbol absent
    r"|cannot open shared object file"    # Linux: missing .so
    r"|symbol lookup error"               # glibc: run-time relocation failure
    r"|undefined symbol"                  # glibc/ImportError variant of the same
    r"|(?:GLIBC|GLIBCXX|CXXABI)_[0-9.]+'? not found"  # host libc/libstdc++ too old
    r"|Error loading shared library"      # musl: missing .so
    r"|Error relocating"                  # musl: symbol not found
    r"|bad ELF interpreter"               # missing/foreign ld.so
    r"|wrong ELF class"                   # Linux: 32/64 mismatch
    r"|Exec format error"                 # Linux/WSL: wrong-arch or wrong-OS binary
    r"|cannot execute binary file"        # the shell's version of the same
    r"|cannot execute: required file not found",  # bash>=5.1: missing/CRLF interpreter
    re.IGNORECASE)

# Arguments that make a program start up and exit immediately. Tried in order;
# the FIRST one that produces output or a clean-ish exit is enough, because we
# are not testing the program — only that the process can get off the ground.
_SMOKE_ARGS = (["--version"], ["-v"], ["--help"], [])


def _arch_pin(envdir):
    """['/usr/bin/arch', '-arm64'] etc, matching what the launcher will use.

    Doctor must probe the way production runs, or it certifies a configuration
    nobody uses. This is the whole lesson of 2026-08-22: from a terminal every
    binary in that env ran perfectly, while the same binaries launched by the
    dashboard could not start, because the launching process's architecture
    decides which slice of a universal binary runs. A check that does not pin
    the same way the launcher pins is testing a different program.
    """
    if platform.system() != "Darwin" or not os.path.exists("/usr/bin/arch"):
        return []
    sub = env_conda_subdir(envdir)
    if sub == "osx-arm64":
        return ["/usr/bin/arch", "-arm64"]
    if sub == "osx-64":
        return ["/usr/bin/arch", "-x86_64"]
    return []


def _pin_for_exec(envdir, path, env_bin="", extra_dirs=()):
    """The arch pin to launch `path` with — _arch_pin(envdir), or [].

    Pin only what the pin means. /usr/bin/arch ENFORCES an architecture;
    production's inherited preference only SELECTS among the slices of a FAT
    binary. The two agree only when the exec'd file IS fat: a thin x86_64
    binary under an arm64-pinned parent still runs — Rosetta translates it,
    which is exactly how production runs ksnp_gui's pre-Apple-Silicon
    SourceForge payload every day — while `arch -arm64 <thin x86_64>` dies
    with "Bad CPU type in executable". The unconditional pin therefore flagged
    "installed but cannot start" on the SAME report whose format check had
    just blessed those binaries as runnable: doctor contradicting itself on a
    working machine, at every install. For a script, the file exec actually
    loads is its shebang interpreter, so the fat-or-thin question is asked of
    THAT binary.
    """
    exec_path = path
    got = script_interpreter(path)
    if got:
        interp, env_form = got
        resolved = (find_binary(os.path.basename(interp), env_bin, extra_dirs)
                    if env_form else (interp if os.path.exists(interp) else None))
        if not resolved:
            return []      # nothing will exec; the interpreter check owns that
        exec_path = os.path.realpath(resolved)
    return _arch_pin(envdir) if len(_macho_slices(exec_path)) > 1 else []


def loader_smoke_findings(envdir, env_bin, binaries, extra_dirs=()):
    """([(label, fix, note)], [note]) for declared programs that cannot START.

    WHY EXISTENCE IS NOT ENOUGH. Every check that came before this one asked
    where a file is, what its bytes claim, or what the package records say. A
    program can satisfy all three and still fail the instant it is executed —
    a missing shared library, a wrong-architecture payload, or (the case this
    was written for) a universal binary whose companion modules exist in only
    one architecture. On the machine that motivated it, kraken2 was present,
    executable, correctly resolved, in a coherent env, and could not run.

    Deliberately narrow, two ways. The program is launched with a harmless
    flag and the result is inspected ONLY for dynamic-loader errors — and
    only when the process actually FAILED (nonzero exit, or a signal). A
    program that starts and complains about its arguments has passed, and so
    has one that exits 0 while narrating a missed optional library (medaka's
    CPU-only "Could not load dynamic library libcudart" warning wears loader
    words and means nothing). This is not a test of the tool, it is a test
    that the process can exist — that narrowness is what makes it safe to run
    against tools nobody here has argument knowledge of.

    Every launch runs in a throwaway working directory: check_binary_format
    refuses to exec vendored payloads precisely because a bare invocation may
    scaffold output into the cwd, and this probe launches those same binaries
    with zero args as its last resort — it must not litter whatever directory
    the user happened to run doctor from, nor inherit a read-only one.

    The second return value carries notes, not findings: a soft wall-clock
    budget (BDTOOLS_SMOKE_BUDGET_SECS, default 120s) stops LAUNCHING once
    spent — 33 declared binaries at 60s per attempt on a wedged NFS mount is
    an hour of silent serial hanging for the one report a user in a failure
    state is waiting on — and the note names every binary not probed, because
    a check that silently skips is indistinguishable from a check that passed.
    """
    try:
        budget = float(os.environ.get("BDTOOLS_SMOKE_BUDGET_SECS", "") or 120)
    except ValueError:
        budget = 120.0
    start = time.monotonic()
    out, skipped, budget_notes = [], [], []
    with tempfile.TemporaryDirectory(prefix="bdtools-smoke.") as scratch:
        for name in binaries:
            path = find_binary(name, env_bin, extra_dirs)
            if not path:
                continue                  # the existence check owns absence
            owned = _same_or_under(path, envdir) or any(
                _same_or_under(path, d) for d in extra_dirs if d)
            if not owned:
                continue                  # a system tool brings its own runtime
            if time.monotonic() - start > budget:
                skipped.append(name)
                continue
            pin = _pin_for_exec(envdir, os.path.realpath(path),
                                env_bin, extra_dirs)
            detail = ""
            for args in _SMOKE_ARGS:
                try:
                    res = subprocess.run(pin + [path, *args],
                                         capture_output=True, text=True,
                                         timeout=60, stdin=subprocess.DEVNULL,
                                         cwd=scratch, env=_probe_env())
                except subprocess.TimeoutExpired:
                    break                 # it started; a hang is not a loader fault
                except Exception:
                    break
                blob = (res.stderr or "") + (res.stdout or "")
                # Both conditions, in this order: the process failed AND the
                # text is the loader's. A returncode of 0 wins uncondition-
                # ally — the process exists, whatever it printed.
                if res.returncode != 0 and _LOADER_ERRORS.search(blob):
                    detail = " ".join(blob.split())[:300]
                    break
                if res.returncode == 0 or blob.strip():
                    detail = ""           # it started: nothing more to prove
                    break
            if detail:
                pinned = (" (launched as %s, the way the tool is launched)"
                          % " ".join(pin)) if pin else ""
                out.append((
                    f"{name} is installed but cannot start{pinned} — {detail}",
                    f'CONDA_SUBDIR={env_conda_subdir(envdir)} conda install -y -p '
                    f'"{envdir}" --no-deps --force-reinstall {name}'
                    if env_conda_subdir(envdir) else "",
                    "cause: the file exists and resolves correctly; the failure is in "
                    "loading it, so no amount of reinstalling paths or rebuilding the "
                    "env layout changes it. Reinstall the package that owns this "
                    "program for this env's platform."))
    if skipped:
        budget_notes.append(
            f"smoke test stopped after its {budget:.0f}s budget "
            f"(BDTOOLS_SMOKE_BUDGET_SECS) — not probed: {', '.join(skipped)}. "
            f"A slow filesystem, not a verdict on those programs; re-run "
            f"--deep when it settles")
    return out, budget_notes


def crlf_findings(envdir, env_bin, binaries, extra_dirs=(), tool_dir=None):
    """[(label, fix, note)] for owned scripts whose shebang line ends in CRLF.

    git core.autocrlf=true on WSL (or a Windows editor touching a checkout)
    rewrites every text file with \\r\\n, and execve then looks for an
    interpreter literally named "perl\\r" — the kernel answers
    "/usr/bin/env: 'perl\\r': No such file or directory", exit 127. NOTHING
    else in this file can see it: script_interpreter .strip()s the decoded
    shebang, which deletes the very \\r that kills the exec, so the
    interpreter check resolves 'perl' cleanly and passes; and the smoke
    test's exec raises FileNotFoundError inside subprocess, which the probe
    loop treats as unjudgeable. Only reading the raw first-line BYTES catches
    it before a user does. Judged with the same ownership rule as the loader
    smoke — plus the tool checkout's own bin/*.py entry scripts, which arrive
    by git clone and are the likeliest CRLF victims. Healthy files are
    silent: no OK line, no note.
    """
    paths = []
    for name in binaries:
        path = find_binary(name, env_bin, extra_dirs)
        if not path:
            continue
        if not (_same_or_under(path, envdir) or any(
                _same_or_under(path, d) for d in extra_dirs if d)):
            continue
        paths.append(path)
    if tool_dir:
        bindir = Path(tool_dir) / "bin"
        if bindir.is_dir():
            paths.extend(str(p) for p in sorted(bindir.glob("*.py")))
    out, seen = [], set()
    for path in paths:
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        try:
            with open(path, "rb") as fh:
                first = fh.readline(512)
        except OSError:
            continue
        if not first.startswith(b"#!") or not first.rstrip(b"\n").endswith(b"\r"):
            continue
        tokens = first[2:].rstrip(b"\r\n").split()
        shown = (tokens[-1].decode("utf-8", "replace") if tokens else "") + "\\r"
        out.append((
            f"{os.path.basename(path)} has Windows line endings (CRLF) — the "
            f"shebang names '{shown}', an interpreter that does not exist "
            f"({path})",
            f"perl -pi -e 's/\\r$//' \"{path}\"",   # not sed -i: BSD sed takes the script as the -i backup suffix and dies (adversarial-review catch, reproduced live)
            "cause: the file was saved with \\r\\n line endings (git "
            "core.autocrlf=true on this machine, or a Windows editor), and "
            "execve keeps the \\r as part of the interpreter's name. Fix the "
            "cause too — `git config core.autocrlf false` (or 'input') in the "
            "checkout — or the next pull writes it right back."))
    return out


# Patched to fixture files in tests; absent on macOS, which is the silence.
_PROC_MOUNTS = "/proc/mounts"
_PROC_VERSION = "/proc/version"


def _mount_fstype(path):
    """Filesystem type of the mount holding `path` (longest-prefix match over
    /proc/mounts, octal-escape-aware), or "" when the table is unreadable.
    The discriminator wsl_drvfs_findings needs: a path under /mnt/ can be 9p
    (a real Windows drive) or ext4 (a `wsl --mount` disk), and only the mount
    table can tell them apart."""
    try:
        lines = Path(_PROC_MOUNTS).read_text(encoding="utf-8",
                                             errors="replace").splitlines()
    except OSError:
        return ""
    best, best_type = "", ""
    for ln in lines:
        parts = ln.split()
        if len(parts) < 3:
            continue
        mnt = parts[1].replace("\\040", " ").replace("\\011", "\t")
        if (path == mnt or path.startswith(mnt.rstrip("/") + "/")
                or mnt == "/") and len(mnt) > len(best):
            best, best_type = mnt, parts[2]
    return best_type


def noexec_findings(envdir):
    """[(label, fix, note)] when the env's bin sits on a noexec mount (Linux).

    HPC scratch trees and some /home mounts carry noexec. Every file there
    shows rwxr-xr-x, os.access() says executable (access() ignores mount
    flags), and exec still fails EACCES — "Permission denied" on a file whose
    permission bits are perfect, which reads exactly like corruption and gets
    answered with chmods and reinstalls that cannot help. The mount table is
    the authority: longest-prefix match, then the noexec flag. Silent when
    /proc/mounts does not exist (macOS) — this is a Linux failure mode.
    """
    try:
        text = Path(_PROC_MOUNTS).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    bindir = os.path.realpath(os.path.join(str(envdir), "bin"))
    best = ("", "")
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        # /proc/mounts octal-escapes spaces in mountpoints (\040).
        mp = re.sub(r"\\(0[0-7]{2})",
                    lambda m: chr(int(m.group(1), 8)), fields[1])
        if ((bindir == mp or bindir.startswith(mp.rstrip("/") + "/"))
                and len(mp) > len(best[0])):
            best = (mp, fields[3])
    mp, opts = best
    if not mp or "noexec" not in opts.split(","):
        return []
    return [(
        f"env is on a noexec filesystem — {mp} is mounted noexec, so nothing "
        f"under it can ever execute, whatever its permission bits say",
        "export BDTOOLS_HOME=<a filesystem mounted exec> && "
        "bin/bdtools install <tool> --fresh",
        f"cause: the mount at {mp} carries the noexec option; exec fails with "
        f"'Permission denied' on files whose permissions are perfect, so no "
        f"chmod and no reinstall-in-place can fix it — the install has to "
        f"move to a filesystem that allows execution.")]


def wsl_drvfs_findings(envdir):
    """[(label, fix, note)] when a WSL install keeps its env on a Windows drive.

    /mnt/c and friends are drvfs (9p) mounts of the Windows filesystem: no
    hardlinks (conda's link phase falls back to copies or dies mid-
    transaction), foreign symlink semantics (this suite's own sibling-symlink
    remedy fails outright there), no POSIX locking, and 10-50x the latency of
    the Linux filesystem — an env build that takes minutes on ext4 takes
    hours on drvfs and breaks in ways that read as corruption.

    Decided by FSTYPE, not by path: /mnt/ is not synonymous with drvfs.
    `wsl --mount` attaches ext4 disks under /mnt/wsl/<name> (and /mnt/wslg is
    WSLg's tmpfs) — native Linux filesystems with everything drvfs lacks, and
    exactly where a careful user puts a big BDTOOLS_HOME. The adversarial
    review caught the first version flagging those. The mount table names the
    truth (9p on WSL2, drvfs on WSL1, virtiofs on newer builds); the /mnt/
    prefix survives only as the fallback when /proc/mounts is unreadable,
    with /mnt/wsl and /mnt/wslg exempted.
    """
    try:
        version = Path(_PROC_VERSION).read_text(encoding="utf-8",
                                                errors="replace")
    except OSError:
        return []
    if "microsoft" not in version.lower():
        return []
    real = os.path.realpath(str(envdir))
    fstype = _mount_fstype(real)
    if fstype:
        if fstype not in ("9p", "drvfs", "virtiofs"):
            return []
    else:
        # No readable mount table: fall back to the path shape, exempting the
        # wsl-mount locations that are Linux filesystems by construction.
        if not (real == "/mnt" or real.startswith("/mnt/")):
            return []
        if real.startswith("/mnt/wsl/") or real.startswith("/mnt/wslg/"):
            return []
    return [(
        f"conda env on a Windows drive (drvfs): {real} lives under /mnt/, "
        f"the 9p-mounted Windows filesystem",
        "export BDTOOLS_HOME=$HOME/.local/share/bdtools && "
        "bin/bdtools install <tool> --fresh",
        "cause: drvfs has no hardlinks, foreign symlink semantics and 10-50x "
        "the latency of the Linux filesystem, so conda transactions crawl or "
        "break there. Move BDTOOLS_HOME into the Linux filesystem (the "
        "default, ~/.local/share/bdtools) and reach results from Windows via "
        "\\\\wsl$\\<distro>\\home\\... instead of installing on C:.")]


def loader_env_notes():
    """[str] when doctor's own environment redirects the dynamic loader (Linux).

    The Linux twin of the Rosetta slice redirect: LD_LIBRARY_PATH and
    LD_PRELOAD silently decide what every child process loads, and HPC module
    systems export them wholesale — a `module load gcc` has broken conda
    binaries with 'symbol lookup error' pointing into /apps. Setting them is
    not itself a defect (production may inherit the same values and run), so
    this is a NOTE and never a failure: it names the variable so that when a
    tool DOES die loading a library, the report already contains the
    likeliest cause instead of sending the reader to a rebuild.
    """
    if platform.system() != "Linux":
        return []
    notes = []
    for var in ("LD_PRELOAD", "LD_LIBRARY_PATH"):
        val = (os.environ.get(var) or "").strip()
        if val:
            notes.append(
                f"{var} is set ({val}) — it overrides this env's own libraries "
                f"for every program launched, the way Rosetta's inherited "
                f"slice preference redirected a universal perl. If a tool "
                f"fails with 'symbol lookup error' or a version-not-found, "
                f"retry after: module purge; unset LD_PRELOAD LD_LIBRARY_PATH")
    return notes


def _manifest_packages():
    """{tool: [analysis package names]} from tools.yml's `packages:` pins.

    Read from the manifest rather than restated here, so a re-pin (mlst ->
    mlst2, a krakentools addition) is picked up without touching this file.
    Located the way packages.py locates it: $BDTOOLS_MANIFEST, else the
    umbrella repo's tools.yml. A spec 'bioconda::mlst=2.33.1' contributes the
    bare name 'mlst'. Empty dict when the manifest cannot be read — the
    stale-sibling check then stays silent rather than guessing.
    """
    try:
        import manifest  # sibling module, stdlib-only
    except Exception:
        return {}
    repo = Path(__file__).resolve().parents[2]
    path = os.environ.get("BDTOOLS_MANIFEST", str(repo / "tools.yml"))
    try:
        _ver, tools = manifest.parse(path)
    except Exception:
        return {}
    out = {}
    for rec in tools:
        pkgs = rec.get("packages") or []
        if isinstance(pkgs, str):
            pkgs = [pkgs]
        names = []
        for spec in pkgs:
            name = re.split(r"[=<>]", spec.split("::", 1)[-1], maxsplit=1)[0].strip()
            if name:
                names.append(name)
        out[rec.get("name", "")] = names
    return out


def stale_sibling_packages(tool, envdir, sibling_tools):
    """Sibling tools' analysis packages found inside THIS tool's own env.

    WHY THIS EXISTS. Before the sibling split, some envs carried other tools'
    analysis packages directly (amr_plus once held mlst and kraken2). Those
    stale packages are not dead weight — they are solver constraints: on a
    lab Mac, the stale mlst's perl closure made the manifest's
    ncbi-amrfinderplus=4.2.7 pin unsatisfiable, so every `bdtools update`
    "finished" successfully and changed nothing, forever, with no line of
    output naming why. Nothing else here can see it: the modules import, the
    binaries resolve, the arch audit is clean — the env is HEALTHY, it just
    cannot move. Detection is a conda-meta filename scan (name-version-build
    .json, prefix match on 'name-'), no JSON parse, so it is free on every
    doctor run. Gated on the tool's own pins: kraken2 inside
    kraken_id_parse_gui's env is that tool's OWN package, never stale.
    """
    if not sibling_tools:
        return []
    pkgs_by_tool = _manifest_packages()
    own = set(pkgs_by_tool.get(tool, []))
    wanted = []
    for sib in sibling_tools:
        for name in pkgs_by_tool.get(sib, []):
            if name and name not in own and name not in wanted:
                wanted.append(name)
    if not wanted:
        return []
    try:
        files = [p.name for p in Path(envdir, "conda-meta").iterdir()
                 if p.name.endswith(".json")]
    except OSError:
        return []
    # EXACT dist-name parse, never a prefix match: dist filenames are
    # name-version-build.json with hyphens forbidden in version and build, so
    # dropping the last two hyphen-separated fields recovers the name
    # precisely. A prefix match on 'name-' claimed kraken2-server as a stale
    # kraken2 and genoflu-multi as a stale genoflu (adversarial-review catch),
    # while this still matches hyphenated names like ncbi-amrfinderplus.
    installed = set()
    for f in files:
        fields = f[:-5].split("-")          # strip ".json"
        if len(fields) >= 3:
            installed.add("-".join(fields[:-2]))
    return [name for name in wanted if name in installed]


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


def sibling_handoff(name):
    """(env_dir, tool_dir) the launcher would hand a consumer for sibling `name`.

    Asked of tool_launch.resolve — the same answer the sibling-env map exports —
    never guessed from <root>/<name>/env. The guess is exactly what made doctor
    pass while a hand-off failed: a sibling built as a NAMED conda env has no
    <checkout>/env, so every per-tool check was green and the consumer still
    found nothing at the path it probed."""
    try:
        import tool_launch  # sibling module, stdlib-only
    except Exception:
        return "", ""
    outer = getattr(tool_launch, "_SCANNING_SIBLINGS", False)
    tool_launch._SCANNING_SIBLINGS = True
    try:
        plan = tool_launch.resolve(name, 0)
    except Exception:
        return "", ""
    finally:
        tool_launch._SCANNING_SIBLINGS = outer
    env = plan.get("env_dir") or ""
    if env == "(base)":
        env = ""
    return env, plan.get("dir") or ""


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


# --- AMRFinderPlus database ------------------------------------------------
#
# Graded by EXECUTION, not by looking at files: `amrfinder --database_version`
# resolves its database exactly the way a run does (the configured -d, else the
# copy inside its own env), so doctor and the pipeline cannot disagree about
# whether there is a usable one. This is here because nothing graded it at all.
# The bioconda ncbi-amrfinderplus package ships 15 files and NO database — the
# README said the opposite — so a machine where nobody ever ran `amrfinder -u`
# had amrfinder on PATH, a green doctor report, and every AMR run exiting 1 with
# "No valid AMRFinder database is found" *after* writing a report and an xlsx.
# That is the live 2026-08-23 training run, and it is the third instance of this
# suite's recurring shape: a failed run reported as success.
AMRFINDER_PROBE_TIMEOUT = 90


def _amrfinder_env_db(env_bin):
    """Where amrfinder looks when given no -d: the DB inside its own env."""
    if not env_bin:
        return ""
    return str(Path(env_bin).parent / "share" / "amrfinderplus" / "data" / "latest")


def _amrfinder_error(out):
    """The line amrfinder printed after its own `*** ERROR ***` banner."""
    lines = [ln.strip() for ln in (out or "").splitlines()]
    for i, ln in enumerate(lines):
        if ln.startswith("*** ERROR"):
            for rest in lines[i + 1:]:
                if rest:
                    return rest
    for ln in reversed(lines):
        if ln:
            return ln
    return "no output"


def _run_amrfinder(cmd):
    """(returncode, combined output); (None, "") if it could not be run."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=AMRFINDER_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return proc.returncode, (proc.stdout or "") + "\n" + (proc.stderr or "")


def _amrfinder_major(amr, pin):
    """Major version of the amrfinder binary, or None.

    Scans every line rather than the first: 3.x prints a "Running: <path>"
    banner before "Software version: 3.12.8", so line 0 is a path carrying no
    version at all (the trap amr_plus_gui's run_amrfinder.py documents)."""
    rc, out = _run_amrfinder(pin + [amr, "--version"])
    if rc is None:
        return None
    for line in out.splitlines():
        for pattern in (r"^\s*(\d+)\.\d+", r"version[:\s]+(\d+)\.\d+"):
            m = re.search(pattern, line.strip(), re.IGNORECASE)
            if m:
                return int(m.group(1))
    return None


def _db_format_major(path):
    """Major of the DB's database_format_version.txt, or None if absent."""
    try:
        raw = (Path(path) / "database_format_version.txt").read_text(
            encoding="utf-8").strip()
    except OSError:
        return None
    m = re.match(r"(\d+)\.", raw + ".")
    return int(m.group(1)) if m else None


def check_amrfinder_db(configured, env_bin):
    """(ok, detail) for the AMRFinderPlus DB an AMR run here would actually use.

    ok is None when the question does not apply (no amrfinder in this env — the
    binary check owns that finding and saying it twice reads as two faults).

    The format comparison is kept even when the probe passes: 4.x renamed
    AMRProt to AMRProt.fa and bumped database_format_version, and a 3.x binary
    handed a 4.x database aborts with "The BLAST database for AMRProt was not
    found. Use amrfinder -u", which sends someone to re-download a database that
    is already there and already correct. Same rule as the pipeline's
    _is_valid_amrfinder_db, so both refuse the same pairing."""
    amr = find_binary("amrfinder", env_bin)
    if not amr:
        return None, "(no amrfinder in this env)"
    envdir = str(Path(env_bin).parent) if env_bin else ""
    pin = _pin_for_exec(envdir, amr, env_bin) if envdir else []
    used = configured or _amrfinder_env_db(env_bin)
    cmd = pin + [amr, "--database_version"] + (["-d", configured] if configured else [])
    rc, out = _run_amrfinder(cmd)
    if rc is None:
        return None, f"(could not run {amr})"
    # amrfinder names the directory it settled on ("Database directory: '<p>'").
    # Prefer it over our reconstruction: with no -d the choice depends on
    # $CONDA_PREFIX, which is set per launch, so the path we would compute can
    # be right about the env and wrong about the run.
    for line in out.splitlines():
        m = re.search(r"database directory:\s*'([^']+)'", line, re.IGNORECASE)
        if m:
            used = m.group(1)
    if rc != 0 and "is not a valid option" in out:
        # An amrfinder too old for --database_version (3.x). Fall back to the
        # layout its own reader requires, then to the format check below — which
        # is what actually breaks these installs.
        d = Path(used) if used else None
        ok = bool(d) and (d / "version.txt").is_file() and (
            any(d.glob("AMRProt*")) or any(d.glob("AMR_CDS*")))
        if not ok:
            return False, f"{used or '(none)'} — no AMRFinderPlus database there"
    elif rc != 0:
        return False, f"{used or '(none)'} — amrfinder says: {_amrfinder_error(out)}"
    db_major, bin_major = _db_format_major(used), _amrfinder_major(amr, pin)
    if db_major and bin_major and db_major != bin_major:
        return False, (f"{used} is format {db_major}.x but this amrfinder is "
                       f"{bin_major}.x — nothing is corrupt and re-downloading "
                       f"will not help; one side has to move")
    ver = ""
    for line in out.splitlines():
        m = re.search(r"database version[:\s]+(\S+)", line, re.IGNORECASE)
        if m:
            ver = m.group(1)
    if ver and ver != os.path.basename(used.rstrip("/")):
        return True, f"{used} ({ver})"
    return True, used


def _foreign_config_paths(tool):
    """Configured paths that belong to another deployment ([] when clean).

    Never raises and never writes: a hygiene module that failed to import, or a
    config that cannot be read, must cost a missing line in the report rather
    than the whole report."""
    if config_hygiene is None:
        return []
    try:
        return config_hygiene.scan(tool, str(Path(__file__).resolve().parents[2]))
    except Exception:      # noqa: BLE001
        return []


def _resolve_root(name):
    """One of the roots requirements.py's `default_under` may name, or "".

    Resolved, never assumed. site_paths reads an env var, then this machine's
    recorded site file, then a defensible derivation; VSNP_GUI_SITE_ROOT is
    asked of the LAUNCHER, so doctor grades the tree vsnp_gui will actually be
    started with rather than a second guess at it."""
    try:
        import site_paths  # sibling module, stdlib-only
    except Exception:
        return ""
    repo = str(Path(__file__).resolve().parents[2])
    if name == "db_root":
        return str(site_paths.db_root(repo) or "")
    if name == "vsnp_site_root":
        env = os.environ.get("VSNP_GUI_SITE_ROOT", "").strip()
        if env:
            return env
        try:
            import tool_launch  # sibling module, stdlib-only
            outer = getattr(tool_launch, "_SCANNING_SIBLINGS", False)
            tool_launch._SCANNING_SIBLINGS = True
            try:
                plan = tool_launch.resolve("vsnp_gui", 0)
            finally:
                tool_launch._SCANNING_SIBLINGS = outer
            resolved = (plan.get("env_overrides") or {}).get("VSNP_GUI_SITE_ROOT", "")
            if resolved:
                return resolved
        except Exception:      # noqa: BLE001
            pass
        return str(site_paths.site_root(repo) or "")
    return ""


_QUARANTINE_KEY = getattr(config_hygiene, "QUARANTINE_KEY", "_bdtools_foreign_paths")


def _config_file(tool):
    """The tool's config.json, honouring XDG_CONFIG_HOME as the tools do."""
    if config_hygiene is not None:
        try:
            return config_hygiene.config_path(tool)
        except Exception:      # noqa: BLE001
            pass
    return Path.home() / ".config" / tool / "config.json"


def _quarantined_config_paths(tool):
    """(when, entries) a previous sweep removed for this tool. Never raises."""
    if config_hygiene is None:
        return "", []
    try:
        return config_hygiene.quarantined(tool)
    except Exception:      # noqa: BLE001
        return "", []


def _default_path(db):
    """Where this database lives when its config key was never written, or "".

    An empty answer is a real answer: it means nothing on this machine declares
    a home for it, so doctor reports the key as unset instead of naming a
    directory from another deployment as the thing to go and look at."""
    under = db.get("default_under")
    if under:
        root, rel = under
        base = _resolve_root(root)
        return os.path.join(base, rel) if base else ""
    return _expand(db["default"]) if db.get("default") else ""


def _check_db_inner(tool, db, env_bin=""):
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
    # A saved value can belong to ANOTHER deployment: a literal a tool's old
    # DEFAULTS wrote into this user's config.json on first run, or a value carried
    # over from a machine with a different layout. ICAR-NIVEDI, 2026-09-02: doctor
    # reported BLAST "missing /srv/kapurlab/databases/blast/ref_prok_rep_genomes"
    # on a site whose databases live under /srv/icar — grading a path that was
    # never this machine's, and proposing a download to fix it. config_hygiene's
    # three-clause rule is the one judgement of "foreign" the suite has (the
    # launcher sweeps exactly these at start-up); apply it here, grade what the
    # tool would use once the value is evicted, and name the repair.
    if val and config_hygiene is not None:
        try:
            if config_hygiene.is_foreign(val, config_hygiene.local_roots(
                    str(Path(__file__).resolve().parents[2]))):
                val = ""          # grade what the tool uses once this is evicted
        except Exception:      # noqa: BLE001 — hygiene must never break doctor
            pass
    if not val:
        val = _default_path(db)
    kind = db["kind"]
    # Asked before the "nothing configured" bail-out below, because this one
    # resolves either way: amrfinder falls back to the database inside its own
    # env, so an empty config key is a perfectly normal working install.
    if kind == "amrfinder_db":
        return check_amrfinder_db(val, env_bin)
    if not val:
        # `optional` marks a database the tool DEGRADES without rather than
        # fails: amr_plus_gui skips read-based organism detection when it has no
        # Kraken2 DB and still calls acquired genes. Unset is then a note, not a
        # fault — but a path that IS set and cannot be read stays a finding,
        # because that run will log a failure and silently drop the feature.
        if db.get("optional"):
            return None, f"(not configured under '{db['config_key']}')"
        return False, f"(no path set under '{db['config_key']}')"
    p = Path(val)
    if kind == "dir":
        # Same OSError guard as the paths_file loop above: a traversable but
        # unreadable directory (mode 330 on a shared site root) makes iterdir
        # raise PermissionError, and doctor must report a finding, never
        # traceback (adversarial-review catch, reproduced live).
        try:
            ok = p.is_dir() and any(p.iterdir())
        except OSError as e:
            return False, f"{val} (cannot list: {e})"
    elif kind == "dir_marker":
        # Every marker, not one. kraken2's own wrapper requires taxo.k2d,
        # opts.k2d AND hash.k2d, and checks them in that order — so a directory
        # holding only hash.k2d (an interrupted extraction; a DB built by hand)
        # passed this check and then died at run time with `does not contain
        # necessary file taxo.k2d`, which is exactly what the AMR pipeline's
        # organism-detection step hit on 2026-08-23.
        markers = db.get("markers") or ([db["marker"]] if db.get("marker") else [])
        missing = [m for m in markers if not (p / m).exists()]
        if missing:
            need = ", ".join(markers)
            return False, f"{val} — no {missing[0]} (kraken2 needs {need})"
        ok = bool(markers)
    elif kind == "file_prefix":
        ok = bool(list(p.parent.glob(p.name + ".*"))) if p.parent.is_dir() else False
    else:
        ok = p.exists()
    return ok, val



def check_db(tool, db, env_bin=""):
    """(ok, detail) for one database requirement — see _check_db_inner. When the
    saved config value was judged foreign, the detail says so and names the
    repair, whatever the graded default turned out to be."""
    ok, detail = _check_db_inner(tool, db, env_bin)
    val = config_value(tool, db["config_key"])
    if val and config_hygiene is not None:
        try:
            if config_hygiene.is_foreign(val, config_hygiene.local_roots(
                    str(Path(__file__).resolve().parents[2]))):
                detail = (f"{detail} [saved {db['config_key']}={val} belongs to another "
                          f"deployment — bin/bdtools check-paths {tool} --apply]")
        except Exception:      # noqa: BLE001
            pass
    return ok, detail

def run_checks(tool, env_py, scope, tool_dir=None, deep=False):
    """Return (status, lines, issues, notes). status: 'ok'|'issues'|'skip'.
    lines: pretty (symbol, text, fix-or-None) tuples. issues: [{label, fix}]
    (fixable problems). notes: [str] (platform limits, and context explaining a
    finding — reported, never part of the pass/fail)."""
    spec = requirements.for_tool(tool)
    lines, issues, notes = [], [], []
    default_fix = spec.get("fix", f"bin/bdtools install {tool} --fresh")

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

    failures = check_modules(env_py, spec.get("modules", []))
    probe_timed_out = failures is MODULE_PROBE_TIMED_OUT
    if probe_timed_out:
        # A timeout is a fact about the FILESYSTEM, not the packages. Folding
        # it into "python modules missing: <all fifteen>" — with a pip+conda
        # remedy — was the highest-volume cry-wolf on cold NFS/Lustre caches,
        # the platform where doctor is trusted least. Nothing was graded, so
        # nothing is green and nothing is failed.
        msg = (f"module probe timed out after {_MODULE_PROBE_SECS}s — slow "
               f"filesystem, not missing packages; nothing was graded. Re-run "
               f"when the filesystem settles (a warm cache usually passes)")
        lines.append((SKIP, msg, None))
        notes.append(msg)
        failures = {}
    # Two different faults wear the same symptom, and they need opposite
    # remedies. ABSENT: the package isn't there, install it. BROKEN: it is
    # installed and raises on import — an install is a no-op ("All requested
    # packages already installed"), so report the actual error and move the
    # stale caller instead. Splitting them is what stops the pane printing a
    # command that cannot work.
    missing = [m for m, i in failures.items() if i.get("absent", True)]
    broken = [m for m, i in failures.items() if not i.get("absent", True)]
    if missing and (os.environ.get("PYTHONPATH") or os.environ.get("PERL5LIB")):
        # The probes run with PYTHONPATH/PERL5LIB stripped (an HPC module
        # exporting PYTHONHOME once made a healthy env misreport sys.prefix),
        # but production children INHERIT the ambient value — tool_launch
        # prepends to it, never replaces it. So a module supplied only via the
        # user's PYTHONPATH imports fine in production and reads "absent" to
        # the sanitized probe (adversarial-review catch, reproduced live).
        # Re-probe once with the ambient values restored: what imports cleanly
        # there is a note about environment reliance, not a missing package.
        re_fail = check_modules(env_py, missing, probe_env=dict(os.environ))
        if re_fail is MODULE_PROBE_TIMED_OUT:
            re_fail = {m: failures[m] for m in missing}
        rescued = [m for m in missing if m not in re_fail]
        if rescued:
            msg = (f"modules {', '.join(rescued)} import only via the ambient "
                   f"PYTHONPATH/PERL5LIB — production inherits it so the tool "
                   f"runs, but the env does not own these modules; a cron job "
                   f"or another user without that variable will not have them")
            lines.append((SKIP, msg, None))
            notes.append(msg)
            missing = [m for m in missing if m not in rescued]
    if missing:
        # Install what is missing, in the env that is already here. The rebuild
        # remedy is the fallback for modules nothing here can name — not the
        # first answer to every import error.
        mod_fix = module_fix(env_py, tool_dir, missing) or default_fix
        lines.append((BAD, f"python modules missing: {', '.join(missing)}", mod_fix))
        issues.append({"label": f"missing modules: {', '.join(missing)}", "fix": mod_fix})
        cur_py, stale = stale_python_trees(env_py)
        if stale:
            why = (f"cause: this env's python is now {cur_py}, but "
                   f"{', '.join(stale)} is still on disk — the python version was "
                   "replaced, and anything pip-installed under the old one is "
                   "stranded there. The env is not broken; reinstall the "
                   "modules above.")
            lines.append((SKIP, why, None))
            notes.append(why)
    for mod in broken:
        info = failures[mod]
        # The error text is the payload: it is the one thing that told a human
        # what to do when this happened for real, and it used to be discarded.
        label = (f"python module {mod} is installed but fails to import — "
                 f"{info.get('error') or 'no error reported'}")
        chain = [c for c in (info.get("chain") or []) if c]
        # A native-library failure first: it is the more specific diagnosis, and
        # the chain heuristic reads it exactly wrong (the chain names the python
        # packages that STUMBLED over the broken dylib, not the conda package
        # that ships it — see shared_lib_fix).
        sfix, swhy = shared_lib_fix(env_py, mod, info)
        bfix = sfix or broken_import_fix(env_py, mod, info)
        lines.append((BAD, label, bfix))
        issues.append({"label": label, "fix": bfix or ""})
        if swhy:
            lines.append((SKIP, swhy, None))
            notes.append(swhy)
        elif len(chain) > 1:
            why = ("cause: import chain " + " → ".join(chain)
                   + f" — {mod} is present, so installing it changes nothing; "
                     "the incompatibility is between the packages named above.")
            lines.append((SKIP, why, None))
            notes.append(why)
        if not bfix:
            why = ("no remedy can be derived from that traceback — rebuild only "
                   f"if the error is unclear: {default_fix}")
            lines.append((SKIP, why, None))
            notes.append(why)
    if not failures and spec.get("modules") and not probe_timed_out:
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

    # On-disk architecture audit — the disk, not the records (see
    # foreign_arch_files). This is the check that was missing when doctor said
    # "all 9 tools ready" over an env whose bin/perl could not run.
    envdir = str(Path(env_py).parent.parent)
    env_subdir = env_conda_subdir(envdir)
    foreign_files = foreign_arch_files(envdir)
    if foreign_files:
        owners = _packages_owning(envdir, [rp for rp, _ in foreign_files])
        want = _SUBDIR_TARGET.get(env_subdir)
        # Split FOUR ways, because "foreign bytes" has four different truths
        # behind it. KNOWN upstream mispackages: no local action exists.
        # DELIBERATE foreign-subdir installs: the owning record's own subdir
        # MATCHES the bytes, so records and disk AGREE — someone ran
        # CONDA_SUBDIR=osx-64 on purpose (bioconda's osx-arm64 coverage is
        # still partial), and calling that "an interrupted transaction" is a
        # fabricated incident on a machine where conda-meta and the disk
        # agree; whether it is even a problem depends on whether THIS HOST
        # can execute it (osx-64 on Apple Silicon runs whole under Rosetta).
        # ACTIONABLE splits: the owner's record CONTRADICTS the bytes — the
        # actual incident signature, where the interrupted-transaction story
        # is earned. And UNOWNED files, which no record claims — not conda's
        # doing, so not conda corruption either.
        known, actionable, deliberate, unowned = [], [], [], []
        purge_dirs, pkg_chan = {}, {}
        foreign_bad = False
        for rp, arch in foreign_files:
            owner = owners.get(rp, {})
            oname = owner.get("name", "")
            if (oname, env_subdir) in KNOWN_UPSTREAM_FOREIGN:
                known.append((rp, arch, oname))
                continue
            target = tuple(arch.split("/", 1))
            if oname and _SUBDIR_TARGET.get(owner.get("subdir", "")) == target:
                deliberate.append((rp, arch, oname, owner.get("subdir", ""),
                                   _host_can_run(target)))
                continue
            actionable.append((rp, arch))
            if oname:
                pkg_chan.setdefault(oname, owner.get("channel", ""))
                poisoned, cache_dir = _cache_copy_foreign(owner, rp, want)
                if poisoned:
                    purge_dirs[cache_dir] = owner.get("tarball", "")
            else:
                unowned.append(rp)
        if actionable:
            foreign_bad = True
            shown = ", ".join(f"{rp} is {arch}" for rp, arch in actionable[:5])
            if len(actionable) > 5:
                shown += f", … ({len(actionable) - 5} more)"
            label = (f"binaries on disk cannot run in this {env_subdir} env: "
                     f"{shown}")
            fix = None
            if pkg_chan:
                specs = " ".join(
                    f'"{_channel_name(chan)}/{env_subdir}::{name}"'
                    for name, chan in sorted(pkg_chan.items()))
                purge = ""
                if purge_dirs:
                    doomed = " ".join(
                        f'"{d}"' + (f' "{t}"' if t else "")
                        for d, t in sorted(purge_dirs.items()))
                    purge = f"rm -rf {doomed} && "
                fix = (f'{purge}CONDA_SUBDIR={env_subdir} conda install -y -p '
                       f'"{envdir}" --no-deps --force-reinstall {specs}'
                       f'   # re-links every file of the named package(s) for '
                       f'{env_subdir}; a bare spec is already satisfied and '
                       f're-links the same broken build')
            lines.append((BAD, label, fix))
            issues.append({"label": label, "fix": fix or ""})
            # The interrupted-transaction story is told ONLY about files whose
            # owner's record contradicts their bytes — that is its signature.
            why_bits = []
            if pkg_chan:
                why_bits.append(
                    "cause: an interrupted conda transaction left files from "
                    "two extractions in one prefix — conda-meta still records "
                    f"{env_subdir}, so record-level checks pass while the "
                    f"files above cannot load; they belong to: "
                    f"{', '.join(sorted(pkg_chan))}")
            if unowned:
                why_bits.append(
                    f"{len(unowned)} file(s) ({', '.join(unowned[:3])}"
                    + ("…" if len(unowned) > 3 else "")
                    + ") are not conda-managed — no installed package claims "
                      "them (dropped in by hand, or by a non-conda "
                      "installer), so remove or replace them by hand; a "
                      "rebuild helps only if whatever put them there stops: "
                    + default_fix)
            why = "; ".join(why_bits)
            if purge_dirs:
                why += (". The pkgs-cache copy is ALSO wrong, so a plain "
                        "force-reinstall re-links the same bytes and reports "
                        "success — the remedy purges the poisoned cache entry "
                        "first (observed live: two 'successful' reinstalls of "
                        "libdb that changed nothing)")
            lines.append((SKIP, why, None))
            notes.append(why)
        for rp, arch, oname, osub, runnable in deliberate:
            if runnable:
                msg = (f"note: {rp} is {arch} because {oname} was installed "
                       f"for {osub} on purpose (its own record and its bytes "
                       f"agree — a deliberate foreign-platform install, not "
                       f"an interrupted transaction); this host can execute "
                       f"it, so it is reported, not failed")
                lines.append((SKIP, msg, None))
                notes.append(msg)
                continue
            foreign_bad = True
            label = (f"{rp} is {arch}: {oname} was deliberately installed for "
                     f"{osub}, but this host cannot execute {arch} binaries")
            fix = (f'CONDA_SUBDIR={env_subdir} conda install -y -p "{envdir}" '
                   f'--no-deps --force-reinstall '
                   f'"{_channel_name(owners.get(rp, {}).get("channel", ""))}/'
                   f'{env_subdir}::{oname}"'
                   if env_subdir else default_fix)
            lines.append((BAD, label, fix))
            issues.append({"label": label, "fix": fix})
            why = (f"cause: not corruption — the {oname} record says {osub} "
                   f"and the bytes agree, so this package was installed for a "
                   f"foreign platform on purpose; it simply cannot execute on "
                   f"this host, so install the build that can")
            lines.append((SKIP, why, None))
            notes.append(why)
        for _rp, _arch, _oname in known[:1]:
            why = (f"note: {len(known)} file(s) from {_oname} are {known[0][1]} "
                   f"in this {env_subdir} env — "
                   + KNOWN_UPSTREAM_FOREIGN[(_oname, env_subdir)])
            lines.append((SKIP, why, None))
            notes.append(why)
        if not foreign_bad and _SUBDIR_TARGET.get(env_subdir):
            lines.append((OK, f"on-disk binaries match the env platform "
                              f"({env_subdir}; noted exceptions are deliberate "
                              f"or upstream)", None))
    elif _SUBDIR_TARGET.get(env_subdir):
        lines.append((OK, f"on-disk binaries match the env platform "
                          f"({env_subdir})", None))

    # Script interpreters: what RUNS the tool's scripts, not just that the
    # scripts are there. A `#!/usr/bin/env perl` shebang is resolved by PATH at
    # run time, so a green existence check says nothing about it — see
    # interpreter_findings for the run-time failure this catches.
    interp_bad = interpreter_findings(
        envdir, env_bin, spec.get("binaries", []), found_assets)
    for label, fix, why in interp_bad:
        lines.append((BAD, label, fix))
        issues.append({"label": label, "fix": fix or ""})
        if why:
            lines.append((SKIP, why, None))
            notes.append(why)
    if not interp_bad and spec.get("binaries"):
        lines.append((OK, "script interpreters resolve inside this env", None))

    # Windows line endings in shebangs (WSL/Windows-editor damage), mount-level
    # Linux failure modes (noexec, drvfs) and loader-redirecting environment
    # variables. All three are byte- or table-reads — free enough for every
    # doctor run — and all three stay silent on a healthy machine.
    platform_bad = (crlf_findings(envdir, env_bin, spec.get("binaries", []),
                                  found_assets, tool_dir=tool_dir)
                    + noexec_findings(envdir)
                    + wsl_drvfs_findings(envdir))
    for label, fix, why in platform_bad:
        lines.append((BAD, label, fix))
        issues.append({"label": label, "fix": fix or ""})
        if why:
            lines.append((SKIP, why, None))
            notes.append(why)
    for msg in loader_env_notes():
        lines.append((SKIP, msg, None))
        notes.append(msg)

    # Can each declared program actually START? Runs at install time (scope
    # "env") and on demand (--deep), not on every routine doctor: it launches
    # every binary, which costs seconds. Install time is the right default —
    # that is where a fresh machine should discover it cannot run, instead of
    # discovering it mid-analysis weeks later.
    if deep or scope == "env":
        smoke_bad, smoke_notes = loader_smoke_findings(
            envdir, env_bin, spec.get("binaries", []), found_assets)
        for label, fix, why in smoke_bad:
            lines.append((BAD, label, fix or default_fix))
            issues.append({"label": label, "fix": fix or default_fix})
            lines.append((SKIP, why, None))
            notes.append(why)
        for msg in smoke_notes:
            lines.append((SKIP, msg, None))
            notes.append(msg)

    optional_missing = [
        b for b in spec.get("optional_binaries", [])
        if not has_binary(b, env_bin, found_assets)
    ]
    if optional_missing:
        msg = (f"optional integrations unavailable: {', '.join(optional_missing)} "
               "(core analysis is still runnable)")
        lines.append((SKIP, msg, None))
        notes.append(msg)

    # The inverse of the sibling hand-off below: sibling tools' packages left
    # INSIDE this env from before the split. They are not dead weight, they
    # are solver constraints — on a lab Mac whose amr_plus env predated the
    # mlst/kraken2 split, the stale mlst's perl closure made the manifest pin
    # ncbi-amrfinderplus=4.2.7 unsatisfiable, so every update "finished" and
    # changed nothing, forever, with no output naming the cause. `--rebuild`
    # is additive and cannot remove a package; only --fresh can.
    stale = stale_sibling_packages(tool, envdir, spec.get("sibling_tools", []))
    if stale:
        label = (f"stale copies of sibling tools' packages are inside this "
                 f"env: {', '.join(stale)} — they cap this env's solve (the "
                 f"documented case: mlst's perl closure held "
                 f"ncbi-amrfinderplus at 3.12.8)")
        fix = (f"bin/bdtools install {tool} --fresh   # --rebuild is additive "
               f"and cannot remove them")
        lines.append((BAD, label, fix))
        issues.append({"label": label, "fix": fix})
        why = ("cause: these packages predate the sibling split and now live "
               "in their own envs; left here, their dependency closures pin "
               "this env's solve, so updates succeed without moving anything "
               "— an update loop that never converges until the env is "
               "rebuilt fresh")
        lines.append((SKIP, why, None))
        notes.append(why)

    # Sibling hand-offs: software this tool runs from ANOTHER tool's env
    # (amr_plus -> mlst/kraken2, irma -> genoflu, vsnp -> the Kraken GUI).
    # Everything above grades the env THIS tool launches with, which says
    # nothing about the path a hand-off resolves: an install whose
    # kraken_id_parse_gui ran from a named conda env passed every per-tool
    # check while vsnp_gui's Kraken hand-off, probing <checkout>/env, found
    # nothing. Ask the launcher's resolver, so the two can never disagree.
    for sib in spec.get("sibling_tools", []):
        sib_env, sib_dir = sibling_handoff(sib)
        if not sib_env:
            fix = f"bin/bdtools install {sib}"
            label = (f"sibling {sib}: no runnable environment — "
                     f"the {sib} hand-off will fail or be skipped at runtime")
            lines.append((BAD, label, fix))
            issues.append({"label": f"sibling {sib} env missing", "fix": fix})
            continue
        checkout_env = os.path.join(sib_dir, "env") if sib_dir else ""
        if checkout_env and not os.path.isdir(checkout_env):
            # The launcher (and current releases) will find sib_env; releases
            # from before the sibling-env map, and backends started outside
            # the launcher, probe <checkout>/env and will miss it.
            remedy = f"ln -sfn {sib_env} {checkout_env}"
            msg = (f"sibling {sib} runs from {sib_env}, but {checkout_env} "
                   f"does not exist — consumers that probe the checkout "
                   f"(older releases, backends started outside the launcher) "
                   f"will miss it")
            lines.append((SKIP, msg, remedy))
            notes.append(f"{msg}; remedy: {remedy}")
        else:
            lines.append((OK, f"sibling {sib} env: {sib_env}", None))

    if scope == "all":
        # Paths in this user's config for this tool that belong to a DIFFERENT
        # deployment. Reported here as well as repaired at launch, because
        # doctor is where someone looks when a run has already gone wrong, and
        # because a config nobody has launched since the repair shipped still
        # holds the value. Read-only: doctor diagnoses, the launcher and
        # `bdtools fix --apply` are what change anything.
        for f in _foreign_config_paths(tool):
            msg = (f"config key '{f['key']}' points at another deployment: "
                   f"{f['value']} — it does not exist here and is under none of "
                   f"this machine's roots, so every run is handed a path that "
                   f"cannot resolve")
            fix = f"bin/bdtools check-paths {tool} --apply"
            lines.append((BAD, msg, fix))
            issues.append({"label": f"foreign path configured ({f['key']})", "fix": fix})

        # ...and what a previous sweep already took out. Reported as a NOTE, not
        # a finding: nothing is wrong now. It is here because the repair happens
        # at launch, so by the time anyone reads a doctor report the finding
        # above has usually already been cured — and "we changed your
        # configuration" is not something to do silently. Names the tool's own
        # fallback as the consequence, so a site that really owns the path knows
        # to put it back.
        removed_at, removed = _quarantined_config_paths(tool)
        if removed:
            keys = ", ".join(f"{r.get('key')}={r.get('value')}" for r in removed)
            lines.append((SKIP,
                          f"removed {len(removed)} configured path(s) belonging to "
                          f"another deployment ({keys})"
                          + (f" on {removed_at}" if removed_at else "")
                          + f"; the tool now uses its own default. Kept under "
                            f"'{_QUARANTINE_KEY}' in {_config_file(tool)}",
                          None))

        for db in spec.get("databases", []):
            ok, detail = check_db(tool, db, env_bin=env_bin)
            if ok:
                # Say WHICH database answered: the recurring confusion on these
                # reports is two configs naming different copies (amr_plus_gui
                # carries its own kraken_db key, separate from the Kraken GUI's),
                # and a bare "✓ Kraken2 DB" cannot tell them apart.
                lines.append((OK, f"{db['label']}: {detail}" if detail else db["label"], None))
            elif ok is None:
                msg = f"{db['label']} {detail}"
                if db.get("degrades"):
                    msg += f" — {db['degrades']}"
                lines.append((SKIP, msg, db.get("fix")))
                notes.append(msg + (f"; remedy: {db['fix']}" if db.get("fix") else ""))
            else:
                lines.append((BAD, f"{db['label']} missing {detail}", db["fix"]))
                issues.append({"label": f"{db['label']} missing", "fix": db["fix"]})

    return ("issues" if issues else "ok"), lines, issues, notes


BDTOOLS = str(Path(__file__).resolve().parents[1] / "bdtools")


def absolute_fix(cmd):
    """Rewrite a leading `bin/bdtools` to its absolute path.

    Every fix string is written relative to the umbrella checkout root, which is
    not where anyone is standing when they read one: these commands are copied
    off a dashboard card or out of doctor's report into whatever directory the
    terminal is in, and from bin/ — the likeliest place, since that is where
    these scripts live — the answer is "zsh: no such file or directory:
    bin/bdtools". An absolute path cannot be pasted wrong, and still contains
    "bdtools update", so fix.sh's classifier reads it the same as before.
    """
    if not cmd:
        return cmd
    return re.sub(r"(?<![\w./])bin/bdtools(?=\s|$)", BDTOOLS, cmd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--python", default="")
    ap.add_argument("--scope", choices=["env", "all"], default="all")
    ap.add_argument("--deep", action="store_true",
                    help="also launch each program to prove it can start")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    status, lines, issues, notes = run_checks(
        args.tool, args.python, args.scope, tool_dir=args.dir,
        deep=args.deep)
    lines = [(sym, text, absolute_fix(fix)) for sym, text, fix in lines]
    for iss in issues:
        iss["fix"] = absolute_fix(iss.get("fix", ""))

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
