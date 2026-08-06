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
import re
import socket
import subprocess
import threading
import time

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


def write_dashboard_state(path, port, control_token, session_token=""):
    """Atomically write the private local-dashboard control record.

    `session_token` is recorded so a second launch can rebuild the ?t=… URL and
    just open the dashboard that is already running. Only the process that printed
    that URL used to know it, so double-clicking the launcher again on a host with
    session auth had nothing to open — it reported "already running" to a terminal
    the desktop launcher does not show. The file is 0600, and it already carries
    the strictly more powerful control token.
    """
    if not path:
        return
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    payload = {
        "pid": os.getpid(),
        "port": int(port),
        "control_token": control_token,
        "session_token": session_token,
        "started_at": int(time.time()),
    }
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.write("\n")
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def remove_dashboard_state(path):
    """Remove our state record, but never delete a newer process's record."""
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if int(payload.get("pid", -1)) != os.getpid():
            return
        os.unlink(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass


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


def tool_is_updatable(name):
    """May bdtools change this tool? tools.yml `updates:`, default no.

    One reader for the dashboard, so the buttons on screen and the gate in
    common.sh:require_updatable cannot disagree about what is allowed. Defaults to
    False on any error: the safe answer to "may I rebuild this?" is no.
    """
    try:
        import manifest
        _, tools = manifest.parse(
            os.environ.get("BDTOOLS_MANIFEST", os.path.join(REPO_DIR, "tools.yml")))
    except Exception:
        return False
    for rec in tools:
        if rec.get("name") == name:
            return rec.get("updates") == "install"
    return False


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
    # tools.yml decides whether bdtools may CHANGE this tool. A report-only tool
    # keeps `latest` — seeing that a release exists is the point — but is never
    # offered, because the CLI would refuse it anyway and a button that leads to a
    # refusal is worse than no button. It is also the only reliable guard: the
    # dashboard's "Install tool updates" targets `all`, so without this one click
    # reaches every tool in the manifest.
    report_only = not tool_is_updatable(name)
    return {
        "name": name,
        "label": pretty(name),
        "installed": installed or "—",
        "latest": latest or "—",
        "update_available": available and not report_only,
        "newer_exists": available,
        "report_only": report_only,
        # "tool" = this GUI's own release (a git tag + an env rebuild), as opposed
        # to "package" (conda software inside its env) or "suite" (bdtools itself).
        # The banner groups by this and orders the buttons by it.
        "kind": "tool",
    }


def tool_checkout_version(name):
    """`git describe` of a tool's checkout, or '' — the GUI version in use."""
    try:
        import tool_launch
        directory = tool_launch.tool_dir(name)
    except Exception:
        return ""
    if not os.path.isdir(os.path.join(directory, ".git")):
        return ""
    try:
        out = subprocess.run(["git", "-C", directory, "describe", "--tags", "--always"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def package_report(use_network=True, tools=None):
    """Analysis-package versions per tool ([] if anything goes wrong).

    Imported lazily and defensively: the version panel is nice to have, and a
    dashboard that will not render because a version lookup raised would be a
    strictly worse dashboard.
    """
    try:
        import packages
        return packages.report(tools=tools, use_network=use_network)
    except Exception:
        return []


def package_map(use_network=True):
    """{tool: [package record, ...]} for the card display."""
    out = {}
    for rec in package_report(use_network=use_network):
        out.setdefault(rec["tool"], []).append(rec)
    return out


def package_update_records():
    """Package updates in the same shape as the tool-update records.

    The banner already knows how to render {name,label,installed,latest,
    update_available}; giving package updates the same shape means one list, one
    renderer, and no second notification mechanism to keep in step. `kind` and
    `tool` let the apply path tell them apart.
    """
    recs = []
    for rec in package_report():
        # Same gate as the tool records: `update-packages all` reaches every tool,
        # and conda installing into a live env is still a change to the software
        # that produces results.
        report_only = not tool_is_updatable(rec["tool"])
        recs.append({
            "name": f"{rec['tool']}:{rec['package']}",
            # Tool first, then the package inside it: "vSNP3 — vsnp3". The old
            # "vsnp3 (in vSNP3)" stuttered for tools named after their own package.
            # The kind ("conda package") is a separate tag in the UI, so it is not
            # repeated here.
            "label": f"{pretty(rec['tool'])} — {rec['package']}",
            "tool_label": pretty(rec["tool"]),
            "installed": rec["installed"] or "—",
            "latest": rec["latest"] or "—",
            "update_available": rec["update_available"] and not report_only,
            "newer_exists": rec["update_available"],
            "report_only": report_only,
            # Carried even though the banner never offers a held package: the
            # "up to date" line counts them, so a run that could correctly install
            # nothing still leaves visible evidence of why. Without it the banner
            # can only choose between nagging forever and saying nothing.
            "held": rec.get("held", False),
            "held_reason": rec.get("held_reason", ""),
            "held_fix": rec.get("held_fix", ""),
            "kind": "package",
            "tool": rec["tool"],
            "package": rec["package"],
        })
    return recs


def update_scope(target, running):
    """Which tool names an update target touches, and the label for the cards.

    "packages:<tool>" changes the conda env a running tool server is executing
    from, so that server must be stopped exactly like a tool update stops it —
    treating the target as an unknown tool name (which is what a naive
    `{target}` does) would leave it running against a half-swapped env.
    """
    if target.startswith("packages:"):
        scope = target.split(":", 1)[1]
    else:
        scope = target
    if scope == "all":
        return set(running), {"*"}
    return {scope}, {scope}


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
        "kind": "suite",
    }


def _pin_only_manifest_drift():
    """True when tools.yml differs from HEAD ONLY in pin values.

    `bdtools update <tool>` records the version it moved to by rewriting
    `version:` in tools.yml (check-updates.sh apply_one -> manifest_set). tools.yml
    is git-tracked, so updating any tool dirties the umbrella checkout — and the
    umbrella's own self-update is `git pull --ff-only`, which refuses a dirty tree.
    The result was a standing deadlock: update your tools and you can no longer
    update bdtools until you manually `git restore tools.yml`. It recurred on every
    release, on every platform.

    Those pin lines are *derived* state — `bdtools update` wrote them and can write
    them again, and the manifest coming from origin is the release of record. So
    pin-only drift is safe to drop for the pull. Anything else (a comment, a repo
    URL, a new tool, an edit to another file) still refuses, which is the point of
    the original check.
    """
    try:
        diff = subprocess.run(
            ["git", "-C", REPO_DIR, "diff", "--unified=0", "--", "tools.yml"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    if not diff.strip():
        return False
    changed = [
        ln for ln in diff.splitlines()
        # Skip diff furniture; keep only real +/- content lines.
        if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
    ]
    if not changed:
        return False
    return all(
        re.match(r"^[+-]\s*(version|suite_version):\s*\S+\s*$", ln)
        for ln in changed
    )


def _dirty_paths(porcelain):
    """Paths in a `git status --porcelain` block.

    Parsed rather than sliced at a fixed offset: the caller strips the whole
    block for display, which eats the leading space of the first line (" M f"
    becomes "M f") and silently shifts every column. Slicing [3:] then yielded
    "ools.yml" and the pin-drift check never matched.
    """
    paths = set()
    for line in porcelain.splitlines():
        line = line.rstrip()
        if not line:
            continue
        rest = line[2:].strip() if len(line) > 2 else ""
        if not rest:
            continue
        # "R  old -> new" reports both sides; either one makes the tree dirty.
        for part in rest.split(" -> "):
            paths.add(part.strip().strip('"'))
    return paths


def suite_update_command(log):
    """The `bdtools` self-update command, or None when it must be refused.

    Never merge an update into a locally edited suite checkout. A clean tree makes
    the exact scope of `pull --ff-only` reviewable and reproducible.

    The one exception is tools.yml pin drift that `bdtools update` wrote itself —
    see _pin_only_manifest_drift. Refusing on that turned "update your tools" into
    "you can no longer update bdtools", which is not a safety property, just a
    deadlock.

    Shared by BOTH dashboards. It lived only in UpdateManager, so the legacy
    stdlib dashboard would happily `git pull --ff-only` over a dirty checkout —
    the two update paths must not disagree about a safety rule. `log` is the
    caller's line-logger.
    """
    try:
        dirty = subprocess.run(
            ["git", "-C", REPO_DIR, "status", "--porcelain"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        dirty = f"(could not inspect checkout: {exc})"

    # Only tools.yml is dirty, and only in pin values -> restore it and carry on.
    if dirty and _dirty_paths(dirty) == {"tools.yml"}:
        if _pin_only_manifest_drift():
            log("tools.yml differs from HEAD only in version pins — that is "
                "`bdtools update`'s own bookkeeping, not an edit of yours.")
            log("Restoring it so the pull can proceed; the manifest from origin "
                "is authoritative, and re-running a tool update re-applies any "
                "newer tags.")
            try:
                subprocess.run(
                    ["git", "-C", REPO_DIR, "checkout", "--", "tools.yml"],
                    capture_output=True, text=True, check=True, timeout=30,
                )
                dirty = ""
            except (OSError, subprocess.SubprocessError) as exc:
                log(f"ERROR: could not restore tools.yml: {exc}")
                return None

    if dirty:
        log("ERROR: bdtools checkout has local changes; refusing to pull.")
        log("Commit/stash them, or update from a separate clean checkout.")
        for line in dirty.splitlines()[:20]:
            log(f"  {line}")
        return None
    log("$ git pull --ff-only  (updating bdtools)")
    return ["git", "-C", REPO_DIR, "pull", "--ff-only"]


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
        cache = None
        try:
            items = []
            bd = check_bdtools_update()
            if bd:
                items.append(bd)
            items.extend(check_tool_updates())
            # Analysis packages (vsnp3, AMRFinderPlus, kraken2, …) are checked in
            # the same pass and reported in the same list. A new bioconda release
            # used to be invisible here: the tool checks compare git tags, and a
            # tool tag does not move when the science underneath it does.
            items.extend(package_update_records())
            cache = {"checked": True, "items": items,
                     "any": any(i["update_available"] for i in items)}
        except Exception as exc:
            cache = {"checked": True, "items": [], "any": False, "error": str(exc)}
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
        """Start a background update of `target` ('all', 'bdtools', a tool, or
        'packages:<tool|all>')."""
        if target not in valid_targets:
            return False, f"unknown update target: {target}"
        with self.lock:
            if self.job["running"]:
                return False, "an update is already running"
            self.job = {"running": True, "done": False, "ok": None, "target": target, "log": []}
        # Non-daemon by design: if the user presses Ctrl-C during an update,
        # Python waits for the checkout/environment operation instead of
        # orphaning it halfway through.
        threading.Thread(target=self._run, args=(target,), daemon=False).start()
        return True, None

    def _log(self, msg):
        with self.lock:
            self.job["log"].append(msg)
            if len(self.job["log"]) > 2000:
                self.job["log"] = self.job["log"][-2000:]

    def _run(self, target):
        cmd = None
        if target == "bdtools":
            cmd = suite_update_command(self._log)
        elif target.startswith("packages:"):
            # "packages:<tool>" or "packages:all" — update the analysis packages in
            # a tool's env to the newest on their channel. A separate command, not
            # `update <tool>`: that one moves the GUI checkout to a new tag and
            # rebuilds, which is a different act with different risks.
            scope = target.split(":", 1)[1]
            cmd = [BDTOOLS, "update-packages", scope]
            self._log(f"$ bdtools update-packages {scope}")
            self._log("Installing into the tool's conda env — the solve can take "
                      "several minutes…")
        else:
            cmd = [BDTOOLS, "update", target]
            self._log(f"$ bdtools update {target}")
            self._log("Rebuilding environments — this can take several minutes per tool…")
        ok = False
        if cmd is not None:
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
