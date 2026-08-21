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


def env_conda_subdir(envdir):
    """The conda platform this env was built for — majority of its packages.

    Same rule as common.sh:env_conda_subdir, in python because the remedy string
    is built here. A `conda install` that omits it re-solves for the HOST
    platform, so on Apple Silicon it links osx-arm64 packages into an osx-64
    env; the result runs until the analysis calls the binary and then dies with
    "incompatible architecture". A repair must not be able to cause that.
    """
    counts = {}
    for meta in Path(envdir, "conda-meta").glob("*.json"):
        try:
            sd = json.loads(meta.read_text(encoding="utf-8")).get("subdir", "")
        except Exception:
            continue
        if sd and sd != "noarch":
            counts[sd] = counts.get(sd, 0) + 1
    return max(counts, key=counts.get) if counts else ""


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
            capture_output=True, text=True, timeout=60).stdout.strip()
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
print(json.dumps(out))
'''


def check_modules(env_py, modules):
    """Which of `modules` fail to import in the tool's env, and WHY.

    Returns a dict, in spec order, {module: {"absent": bool, "error": str,
    "chain": [pkg, ...]}} — empty when every import works. Falsy-empty either
    way, so callers guard on it exactly as they did when this returned a list.

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
                             capture_output=True, text=True, timeout=120)
        # If the interpreter couldn't even start the script (nonzero exit with no
        # output), we can't say which imports failed — report all as missing.
        if out.returncode != 0 and not out.stdout.strip():
            return unknown
        data = json.loads(out.stdout.strip().splitlines()[-1])
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

    Covers the three shapes a dynamic-linker failure reaches python with:
    macOS `dlopen(...): Library not loaded: @rpath/libssl.3.dylib`, macOS
    `tried: '...' (mach-o file, but is an incompatible architecture ...)`, and
    Linux `libssl.so.3: cannot open shared object file`."""
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


# subdir -> the (os, arch) every native binary in such an env must target.
# Platforms with no entry (win-*, ppc) skip the audit rather than invent a rule.
_SUBDIR_TARGET = {"osx-64": ("macos", "x86_64"), "osx-arm64": ("macos", "arm64"),
                  "linux-64": ("linux", "x86_64"), "linux-aarch64": ("linux", "arm64")}

# A basename that names a platform is a package selecting per-platform payloads
# on purpose, not a broken install — see foreign_arch_files.
_ARCH_TAGGED_NAME = re.compile(
    r"(?:^|[-_.])(?:aarch64|arm64|x86[-_]?64|amd64|i[36]86|ppc64(?:le)?|s390x|"
    r"armv\d+|m1|win(?:32|64)|universal2?|manylinux\w*|musllinux\w*)(?=[-_.]|$)",
    re.IGNORECASE)


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
    lib/python* tree. Two reasons that tree is out of scope, both learned from
    the first machine this ran on: (1) it is already proven the strong way —
    check_modules imports it in the env's own interpreter, so a wrong-arch .so
    there fails with the exact loader error and shared_lib_fix names the owner;
    (2) it is where packages DELIBERATELY vendor one plugin per platform
    (ont-fast5-api ships _m1.dylib, _aarch64.so and _x86_64.so side by side and
    picks at run time) — flagging those is a false positive that costs doctor
    its credibility. Basenames carrying an explicit platform tag are skipped
    everywhere for the same vendoring reason; the incident class this audit
    exists for (perl, openssl, libdb) always wears neutral names. Scripts and
    data files are skipped by the magic-byte check itself; symlinks are skipped
    so a target is judged once, where it lives.
    """
    subdir = env_conda_subdir(envdir)
    want = _SUBDIR_TARGET.get(subdir)
    if not want:
        return []
    env = Path(envdir)
    candidates = []
    bindir = env / "bin"
    if bindir.is_dir():
        candidates += [p for p in bindir.iterdir()
                       if p.is_file() and not p.is_symlink()]
    libdir = env / "lib"
    if libdir.is_dir():
        for child in libdir.iterdir():
            if child.name.startswith("python"):
                continue                      # the import probe's jurisdiction
            if child.is_file() and not child.is_symlink():
                if child.suffix in (".so", ".dylib", ".bundle") or ".so." in child.name:
                    candidates.append(child)
                continue
            if not child.is_dir() or child.is_symlink():
                continue
            for pat in ("*.so", "*.so.*", "*.dylib", "*.bundle"):
                candidates += [p for p in child.rglob(pat)
                               if p.is_file() and not p.is_symlink()]
    bad = []
    for p in candidates:
        if _ARCH_TAGGED_NAME.search(p.name):
            continue                          # honest multi-platform vendoring
        target = _binary_target(str(p))
        if target is None or target[1] == "universal":
            continue
        if target != want:
            bad.append((str(p.relative_to(env)), "%s/%s" % target))
    return sorted(set(bad))


def _packages_owning(envdir, relpaths):
    """{relpath: (name, channel)} for the conda packages shipping these files."""
    want = set(relpaths)
    owners = {}
    for meta in Path(envdir, "conda-meta").glob("*.json"):
        try:
            rec = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        for f in want.intersection(rec.get("files") or []):
            owners[f] = (rec.get("name", ""), rec.get("channel", ""))
        if len(owners) == len(want):
            break
    return owners


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
    # Two different faults wear the same symptom, and they need opposite
    # remedies. ABSENT: the package isn't there, install it. BROKEN: it is
    # installed and raises on import — an install is a no-op ("All requested
    # packages already installed"), so report the actual error and move the
    # stale caller instead. Splitting them is what stops the pane printing a
    # command that cannot work.
    missing = [m for m, i in failures.items() if i.get("absent", True)]
    broken = [m for m, i in failures.items() if not i.get("absent", True)]
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
    if not failures and spec.get("modules"):
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
        pkg_chan = {}
        for name, chan in owners.values():
            if name:
                pkg_chan.setdefault(name, chan)
        shown = ", ".join(f"{rp} is {arch}" for rp, arch in foreign_files[:5])
        if len(foreign_files) > 5:
            shown += f", … ({len(foreign_files) - 5} more)"
        label = f"binaries on disk cannot run in this {env_subdir} env: {shown}"
        fix = None
        if pkg_chan:
            specs = " ".join(
                f'"{_channel_name(chan)}/{env_subdir}::{name}"'
                for name, chan in sorted(pkg_chan.items()))
            fix = (f'CONDA_SUBDIR={env_subdir} conda install -y -p "{envdir}" '
                   f'--no-deps --force-reinstall {specs}'
                   f'   # re-links every file of the named package(s) for '
                   f'{env_subdir}; a bare spec is already satisfied and re-links '
                   f'the same broken build')
        lines.append((BAD, label, fix))
        issues.append({"label": label, "fix": fix or ""})
        why = ("cause: an interrupted conda transaction left files from two "
               "extractions in one prefix — conda-meta still records "
               f"{env_subdir}, so record-level checks pass while the files "
               "above cannot load"
               + (f"; they belong to: {', '.join(sorted(pkg_chan))}" if pkg_chan
                  else "; no installed conda package claims them, so rebuild: "
                       + default_fix))
        lines.append((SKIP, why, None))
        notes.append(why)
    elif _SUBDIR_TARGET.get(env_subdir):
        lines.append((OK, f"on-disk binaries match the env platform "
                          f"({env_subdir})", None))

    optional_missing = [
        b for b in spec.get("optional_binaries", [])
        if not has_binary(b, env_bin, found_assets)
    ]
    if optional_missing:
        msg = (f"optional integrations unavailable: {', '.join(optional_missing)} "
               "(core analysis is still runnable)")
        lines.append((SKIP, msg, None))
        notes.append(msg)

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
        for db in spec.get("databases", []):
            ok, detail = check_db(tool, db, env_bin=env_bin)
            if ok:
                lines.append((OK, db["label"], None))
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
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    status, lines, issues, notes = run_checks(
        args.tool, args.python, args.scope, tool_dir=args.dir)
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
