#!/usr/bin/env python3
"""lint.py — catch dependency drift in one tool before a user does.

Static check (no env build): scan the tool's python for imports and the external
programs it shells out to, then flag anything the tool's declared dependencies
(conda_setup/environment.yml, backend/requirements.txt, and the requirements.py
contract) don't account for. This is the guardrail for the class of bug where
the code grows a dependency but the env spec doesn't — e.g. `import humanize`
with no `humanize` in environment.yml, which "installs fine" then crashes at run
time on a fresh machine.

  lint.py --tool NAME --dir DIR

Heuristic by nature (import name != package name); an alias map and a stdlib /
first-party filter keep the noise down. Exit 1 if anything looks undeclared.
"""
import argparse
import ast
import re
import sys
import warnings
from pathlib import Path

warnings.simplefilter("ignore")  # tool sources contain regex/latex escapes

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements  # noqa: E402

# import-name -> the token you'd expect in environment.yml / requirements.txt
ALIASES = {
    "Bio": "biopython", "allel": "scikit-allel", "sklearn": "scikit-learn",
    "cv2": "opencv", "yaml": "pyyaml", "PIL": "pillow", "dateutil": "python-dateutil",
    "attr": "attrs", "OpenSSL": "pyopenssl", "dotenv": "python-dotenv",
    "uvicorn": "uvicorn", "fastapi": "fastapi", "pydantic": "pydantic",
    "vcf": "pyvcf3", "Bio.Seq": "biopython",
    "websockets": "uvicorn",  # shipped by uvicorn-standard / uvicorn[standard]
}
# Shell builtins / coreutils / OS + scheduler tools that aren't conda deps, plus
# sub-commands that ship inside an already-declared package under a different
# name (krakentools/krona/freebayes/vcflib/tectonic) — flagging those is noise.
BINARY_IGNORE = {
    # coreutils / shell
    "cp", "mv", "rm", "mkdir", "rmdir", "ln", "cat", "echo", "printf", "ls",
    "awk", "sed", "grep", "cut", "sort", "uniq", "head", "tail", "tee", "wc",
    "tar", "gzip", "gunzip", "zip", "unzip", "find", "xargs", "chmod", "touch",
    "true", "false", "env", "bash", "sh", "which", "date", "sleep", "kill",
    "python", "python3", "pip", "git", "cd", "test", "dirname", "basename",
    "realpath", "readlink", "mktemp", "df", "du", "ps", "source", "eval",
    # OS / scheduler / package managers (not part of any tool's conda env)
    "conda", "mamba", "open", "xdg-open", "wslview", "osascript", "pgrep",
    "pkill", "sbatch", "squeue", "scancel", "sudo", "curl", "wget", "ssh",
    "scp", "dpkg-query", "brew", "apt", "apt-get", "yum", "softwareupdate",
    "uname", "hostname", "whoami",
    # sub-commands provided by a declared package
    "kreport2krona.py", "ktImportText", "ktImportTaxonomy", "kraken-report",
    "freebayes-parallel", "vcffilter", "vcfallelicprimitives", "pdflatex",
}
G, R, Y, B, X = (("\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m")
                 if sys.stdout.isatty() else ("", "", "", "", ""))

BIN_CALL = re.compile(
    r"""subprocess\.(?:run|Popen|call|check_output|check_call)\(\s*\[?\s*['"]([A-Za-z0-9_.\-]+)['"]"""
    r"""|os\.system\(\s*f?['"]([A-Za-z0-9_.\-]+)"""
    r"""|cmd\s*=\s*f?['"]([A-Za-z0-9_.\-]+)""")


_SKIP_DIRS = {"env", "node_modules", ".git", "frontend", "dist", "build",
              "__pycache__", ".venv", "venv", "site-packages"}
# The actual application code. Deliberately NOT the repo root — root-level dev
# helpers (e.g. create_icons_from_images.py) and docs/deploy scripts aren't
# runtime dependencies and would be false positives.
_APP_DIRS = ("bin", "backend", "app", "src")


def py_files(root):
    for sub in _APP_DIRS:
        d = root / sub
        if d.is_dir():
            for p in d.rglob("*.py"):
                if not (_SKIP_DIRS & set(p.parts)):
                    yield p


def scan(root):
    """Return (imports, binaries, local) used by the code. `local` covers
    first-party modules AND packages: file basenames, their parent directory
    names (so `from app import x` / `import reporting` aren't flagged), and the
    basenames of bundled files (so a vendored `foo.sh` invoked via subprocess
    isn't flagged as an external program)."""
    imports, binaries, local = set(), set(), set()
    files = list(py_files(root))
    for p in files:
        local.add(p.stem)
        for parent in p.relative_to(root).parents:
            if parent.name:
                local.add(parent.name)
    repo_files = set()
    for sub in _APP_DIRS:
        d = root / sub
        if d.is_dir():
            repo_files |= {f.name for f in d.rglob("*") if f.is_file()
                           and not (_SKIP_DIRS & set(f.parts))}
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        imports.add(a.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports.add(node.module.split(".")[0])
        for m in BIN_CALL.finditer(text):
            tok = next(g for g in m.groups() if g)
            if tok not in repo_files:        # bundled script -> first-party
                binaries.add(tok)
    return imports, binaries, local


def declared_blob(root):
    """Lowercased text of every dependency declaration we can find."""
    parts = []
    for rel in ("conda_setup/environment.yml", "environment.yml",
                "backend/requirements.txt", "requirements.txt"):
        f = root / rel
        if f.is_file():
            parts.append(f.read_text(encoding="utf-8", errors="replace").lower())
    return "\n".join(parts)


def token_in(blob, name):
    return re.search(r"(?<![A-Za-z0-9_-])" + re.escape(name.lower()) + r"(?![A-Za-z0-9_])", blob) is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True)
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    root = Path(args.dir)
    spec = requirements.for_tool(args.tool)
    blob = declared_blob(root)
    # requirements.py is also a declaration source (the curated contract).
    spec_tokens = " ".join(
        spec.get("modules", [])
        + spec.get("binaries", [])
        + spec.get("optional_binaries", [])
    ).lower()
    blob_all = blob + "\n" + spec_tokens

    imports, binaries, local = scan(root)
    stdlib = set(getattr(sys, "stdlib_module_names", set()))

    optional = set(spec.get("optional_imports", []))
    undeclared_imports = []
    for imp in sorted(imports):
        if imp in stdlib or imp in local or imp in optional or imp.startswith("_"):
            continue
        pkg = ALIASES.get(imp, imp)
        if not (token_in(blob_all, pkg) or token_in(blob_all, imp)):
            undeclared_imports.append(imp if pkg == imp else f"{imp} (pkg: {pkg})")

    undeclared_bins = []
    for b in sorted(binaries):
        base = b[:-3] if b.endswith(".py") else b
        if base in BINARY_IGNORE or b in BINARY_IGNORE:
            continue
        if not (token_in(blob_all, b) or token_in(blob_all, base)):
            undeclared_bins.append(b)

    # Spec-vs-env: modules in the requirements.py contract that the env file
    # doesn't ship. Modules only — binary names rarely match their conda package
    # (blast->blastn, krakentools->kreport2krona), which would be false drift.
    # Skip entirely when the tool has no env file (e.g. vsnp_gui builds from a
    # bioconda package, not an environment.yml).
    spec_missing = []
    if blob:
        for item in spec.get("modules", []):
            pkg = ALIASES.get(item, item)
            if not (token_in(blob, pkg) or token_in(blob, item)):
                spec_missing.append(item)

    print(f"{B}{args.tool}{X}")
    hard = soft = 0
    if undeclared_imports:
        hard += 1
        print(f"  {R}✗{X} imports not in any dependency file: {', '.join(undeclared_imports)}")
    if spec_missing:
        hard += 1
        print(f"  {R}✗{X} in requirements.py but not in environment.yml: {', '.join(spec_missing)}")
    if undeclared_bins:
        soft += 1
        print(f"  {Y}!{X} programs invoked but not declared: {', '.join(undeclared_bins)}")
    if not (hard or soft):
        print(f"  {G}✓{X} dependencies declared")
    # Only definite (✗) drift fails the gate; advisory (!) binary guesses don't.
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
