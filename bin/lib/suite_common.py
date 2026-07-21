#!/usr/bin/env python3
"""suite_common.py — shared, dependency-free helpers for the tool dashboards.

Single source of truth for the bits both dashboards need:
  * bin/dashboard.py        (legacy stdlib fallback, no proxy)
  * bin/ood_dashboard/app.py (Starlette reverse-proxy — used for OOD *and* local)

Everything here is stdlib-only (subprocess/threading), so it imports cleanly
under any python3 — the fallback dashboard's system python as well as the
proxy dashboard's conda-env python.
"""
import json
import os
import socket
import subprocess
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(os.path.dirname(_HERE))
BDTOOLS = os.path.join(REPO_DIR, "bin", "bdtools")

# Pretty display names + one-line blurbs. Fall back to a derived label.
PRETTY = {
    "vsnp_gui": "vSNP3",
    "amr_plus_gui": "AMRFinderPlus",
    "irma_gui": "IRMA",
    "genoflu_gui": "GenoFLU",
    "mlst_gui": "MLST",
    "kraken_id_parse_gui": "Kraken ID / Parse",
    "ksnp_gui": "kSNP",
    "ncbi_submit_gui": "NCBI Submit",
    "mhc_gui": "Bovine MHC Typer",
}
BLURB = {
    "vsnp_gui": "SNP analysis & phylogeny (High resolution genotyping)",
    "amr_plus_gui": "Antimicrobial resistance genes (AMRFinderPlus)",
    "irma_gui": "Influenza / SARS-CoV-2 assembly (CDC IRMA)",
    "genoflu_gui": "H5 2.3.4.4b influenza genotyping",
    "mlst_gui": "Multi-locus sequence typing",
    "kraken_id_parse_gui": "Taxonomic identification (Kraken2)",
    "ksnp_gui": "Reference-free SNP phylogeny (kSNP4)",
    "ncbi_submit_gui": "Prepare SRA / GenBank submissions",
    "mhc_gui": "Bovine MHC (BoLA) typing from Nanopore amplicons",
}
# Static per-tool development notices — shown as a prominent banner for tools
# not yet validated for diagnostic use (independent of the runtime readiness check).
CAVEAT = {
    "mhc_gui": ("This tool is under active development. Results are preliminary, "
                "have not been fully validated, and should not be treated as "
                "definitive; interpret with caution and confirm by orthogonal methods."),
    "ncbi_submit_gui": ("This tool is under active development. Output is preliminary "
                        "and has not been fully validated; review all generated "
                        "submission files carefully before submitting to NCBI."),
}


def pretty(name):
    return PRETTY.get(name, name.replace("_gui", "").replace("_", " ").upper())


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def port_open(host, port, timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def list_tools():
    """Tool names from `bdtools list` (first column, skipping header/footer)."""
    try:
        out = subprocess.run([BDTOOLS, "list"], cwd=REPO_DIR,
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.splitlines():
        line = line.rstrip()
        if not line or line.startswith("TOOL") or line.startswith("suite_version"):
            continue
        tok = line.split()[0]
        if tok and not tok.endswith(":"):
            names.append(tok)
    return names


def tool_python(name):
    """Return the tool's env python path if built, else None (no build/launch)."""
    try:
        r = subprocess.run([BDTOOLS, "local", name, "--print-python"],
                           cwd=REPO_DIR, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def readiness_map():
    """Run `bdtools doctor --json` -> {name: {"ok","issues","notes",...}}.

    Best-effort: on any failure return {} and callers simply show no badge."""
    try:
        r = subprocess.run([BDTOOLS, "doctor", "--json"], cwd=REPO_DIR,
                           capture_output=True, text=True, timeout=180)
        return {t["tool"]: t for t in json.loads(r.stdout or "[]")}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def _parse_update_line(line):
    """Parse one `check-updates` report line into an update record, or None.

    Line shape (from check-updates.sh report_one):
      <name> pinned=<v> installed=<v> latest=<v> <status text>
    """
    line = line.rstrip()
    if not line or "pinned=" not in line or "installed=" not in line:
        return None
    name = line.split()[0]

    def field(key):
        for tok in line.split():
            if tok.startswith(key + "="):
                return tok[len(key) + 1:]
        return ""
    installed = field("installed")
    latest = field("latest")
    # An update is available when what's INSTALLED here is behind the newest
    # released tag — not when the manifest pin matches the tag. `git describe`
    # may add "-N-g<hash>" past a tag; strip it so a checkout on/ahead of the
    # tag isn't flagged.
    inst_tag = installed.split("-")[0] if installed and installed != "—" else ""
    available = bool(latest and latest != "—" and inst_tag and inst_tag != latest)
    return {
        "name": name,
        "label": pretty(name),
        "installed": installed or "—",
        "latest": latest or "—",
        "update_available": available,
    }


def check_tool_updates():
    """Run `bdtools check-updates all`; return per-tool update records ([] on error)."""
    try:
        out = subprocess.run([BDTOOLS, "check-updates", "all"], cwd=REPO_DIR,
                             capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    recs = []
    for line in out.splitlines():
        rec = _parse_update_line(line)
        if rec:
            recs.append(rec)
    return recs


def check_bdtools_update():
    """Is the umbrella (bdtools) checkout behind upstream? Record or None."""
    git = ["git", "-C", REPO_DIR]
    try:
        if subprocess.run(git + ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                          capture_output=True, text=True, timeout=15).returncode != 0:
            return None  # no upstream tracking branch
        subprocess.run(git + ["fetch", "--quiet"], capture_output=True, text=True, timeout=60)
        behind = subprocess.run(git + ["rev-list", "--count", "HEAD..@{u}"],
                               capture_output=True, text=True, timeout=15).stdout.strip()
        n = int(behind or "0")
        current = subprocess.run(git + ["describe", "--tags", "--always"],
                                capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return {
        "name": "bdtools",
        "label": "bdtools (suite + dashboard)",
        "installed": current or "—",
        "latest": f"{n} new commit(s)" if n else current or "—",
        "update_available": n > 0,
    }


class UpdateManager:
    """Background update checking + a single background apply job.

    Thread-based and stdlib-only, so it is safe to drive from either the
    stdlib HTTP dashboard or the asyncio (Starlette) dashboard — the async
    handlers just read the snapshot methods, which take the lock briefly.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.updates_cache = None   # {"checked","items","any"}
        self.updates_checking = False
        self.job = {"running": False, "done": False, "ok": None, "target": None, "log": []}

    # --- checking ----------------------------------------------------------
    def _check(self):
        try:
            items = []
            bd = check_bdtools_update()
            if bd:
                items.append(bd)
            items.extend(check_tool_updates())
            cache = {"checked": True, "items": items,
                     "any": any(i["update_available"] for i in items)}
        finally:
            with self.lock:
                self.updates_checking = False
        with self.lock:
            self.updates_cache = cache

    def check_async(self, force=False):
        with self.lock:
            if self.updates_checking:
                return
            if self.updates_cache and self.updates_cache.get("checked") and not force:
                return
            self.updates_checking = True
        threading.Thread(target=self._check, daemon=True).start()

    def state(self):
        with self.lock:
            cache = self.updates_cache or {"checked": False, "items": [], "any": False}
            return dict(cache, checking=self.updates_checking)

    # --- applying ----------------------------------------------------------
    def apply(self, target, valid_targets):
        """Start a background update of `target` ('all', 'bdtools', or a tool)."""
        if target not in valid_targets:
            return False, f"unknown update target: {target}"
        with self.lock:
            if self.job["running"]:
                return False, "an update is already running"
            self.job = {"running": True, "done": False, "ok": None, "target": target, "log": []}
        threading.Thread(target=self._run, args=(target,), daemon=True).start()
        return True, None

    def _log(self, msg):
        with self.lock:
            self.job["log"].append(msg)
            if len(self.job["log"]) > 2000:
                self.job["log"] = self.job["log"][-2000:]

    def _reconcile_manifest(self):
        """Discard a leftover local pin auto-bump so `pull --ff-only` can run.

        No-op unless tools.yml is the only dirty tracked file. Best-effort:
        any git error is logged and swallowed so the pull still proceeds.
        """
        try:
            out = subprocess.run(
                ["git", "-C", REPO_DIR, "status", "--porcelain", "--untracked-files=no"],
                capture_output=True, text=True, check=True).stdout
            dirty = [ln[3:] for ln in out.splitlines() if ln.strip()]
            if dirty == ["tools.yml"]:
                self._log("$ git checkout -- tools.yml  (discarding local pin auto-bump)")
                subprocess.run(["git", "-C", REPO_DIR, "checkout", "--", "tools.yml"],
                               check=True, capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError) as exc:
            self._log(f"note: could not reconcile tools.yml before pull ({exc})")

    def _run(self, target):
        if target == "bdtools":
            # `bdtools update <tool>` auto-bumps the pin in tools.yml in place
            # (check-updates.sh) and never commits it — by design, so a box can
            # track newer tool releases even when the committed pin lags. But on
            # a checkout that also pulls an authoritative manifest, that leftover
            # dirty tools.yml makes `pull --ff-only` abort. Origin is the source
            # of truth for the pin and the tool checkout stays at its newer tag
            # regardless, so discard the local auto-bump before pulling — but
            # only when tools.yml is the *sole* dirty tracked file, to avoid
            # clobbering any other in-progress edit.
            self._reconcile_manifest()
            cmd = ["git", "-C", REPO_DIR, "pull", "--ff-only"]
            self._log("$ git pull --ff-only  (updating bdtools)")
        else:
            cmd = [BDTOOLS, "update", target]
            self._log(f"$ bdtools update {target}")
            self._log("Rebuilding environments — this can take several minutes per tool…")
        ok = False
        try:
            proc = subprocess.Popen(cmd, cwd=REPO_DIR, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in proc.stdout:
                self._log(line.rstrip())
            ok = proc.wait() == 0
        except (OSError, subprocess.SubprocessError) as exc:
            self._log(f"ERROR: {exc}")
            ok = False
        self._log("")
        self._log("✅ Done." if ok else "⚠ Update finished with errors — see the log above.")
        with self.lock:
            self.job["running"] = False
            self.job["done"] = True
            self.job["ok"] = ok
        self.check_async(force=True)  # refresh the banner after applying

    def job_status(self):
        with self.lock:
            j = self.job
            return {"running": j["running"], "done": j["done"], "ok": j["ok"],
                    "target": j["target"], "log": j["log"][-400:]}
