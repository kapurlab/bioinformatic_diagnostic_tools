#!/usr/bin/env python3
"""dashboard.py — a local landing page for the Kapur Lab tool suite.

Run via `bdtools dashboard [--port N] [--no-browser]`. There is no Open OnDemand
in local mode, so this is the equivalent home page: it lists the GUIs that are
installed on this machine and, when you pick one, starts that tool's own server
(`bdtools local <tool> --run-only --no-browser --port <free>`) and opens it.

Each tool runs as its own FastAPI/uvicorn app on its own localhost port (exactly
as `bdtools local` runs it); the dashboard is only a launcher + directory, so
there is no proxying and the tools behave identically to launching them by hand.

Dependency-free: standard library only (so it runs under any python3).
"""
import argparse
import html
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BIN_DIR = Path(__file__).resolve().parent
REPO_DIR = BIN_DIR.parent
BDTOOLS = str(BIN_DIR / "bdtools")

# Pretty display names; fall back to a derived label for anything not listed.
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
    "vsnp_gui": "SNP analysis & phylogeny (TB / Brucella)",
    "amr_plus_gui": "Antimicrobial resistance genes (AMRFinderPlus)",
    "irma_gui": "Influenza / SARS-CoV-2 assembly (CDC IRMA)",
    "genoflu_gui": "H5 2.3.4.4b influenza genotyping",
    "mlst_gui": "Multi-locus sequence typing",
    "kraken_id_parse_gui": "Taxonomic identification (Kraken2)",
    "ksnp_gui": "Reference-free SNP phylogeny (kSNP4)",
    "ncbi_submit_gui": "Prepare SRA / GenBank submissions",
    "mhc_gui": "Bovine MHC (BoLA) typing from Nanopore amplicons",
}
# Static per-tool development notices. Shown as a prominent banner on the card
# for tools that are not yet validated for diagnostic use — independent of the
# runtime readiness check (which reports missing deps/databases).
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
        out = subprocess.run([BDTOOLS, "list"], cwd=str(REPO_DIR),
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
                           cwd=str(REPO_DIR), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def readiness_map():
    """Run `bdtools doctor --json` and return {name: {"ok", "issues"}}.

    Used to badge tools that are installed but can't actually run yet (missing
    dependency or un-set-up database). Best-effort: on any failure we return {}
    and the UI simply shows no readiness badge."""
    try:
        r = subprocess.run([BDTOOLS, "doctor", "--json"], cwd=str(REPO_DIR),
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
    available = "available" in line  # "↑ <latest> available"
    return {
        "name": name,
        "label": pretty(name),
        "installed": installed or "—",
        "latest": latest or "—",
        "update_available": available,
    }


def check_tool_updates():
    """Run `bdtools check-updates all` and return a list of per-tool update
    records. Best-effort: returns [] on any failure (network, git, timeout)."""
    try:
        out = subprocess.run([BDTOOLS, "check-updates", "all"], cwd=str(REPO_DIR),
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
    """Is the umbrella (bdtools) checkout behind its upstream branch? Returns a
    record or None. Best-effort — no upstream / not a git checkout / offline
    all yield None (no cue shown)."""
    git = ["git", "-C", str(REPO_DIR)]
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


class Suite:
    """Tracks installed tools and any tool servers this dashboard has launched."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = {}  # name -> {"port": int, "proc": Popen, "url": str}
        self.tools = []    # [{"name","label","blurb","installed"}]
        # Update state: cached check + a single background apply job.
        self.updates_cache = None   # {"checked": bool, "items": [...], "any": bool}
        self.update_job = {"running": False, "done": False, "ok": None,
                           "target": None, "log": []}
        self.refresh()

    def refresh(self):
        ready = readiness_map()
        tools = []
        for name in list_tools():
            installed = tool_python(name) is not None
            r = ready.get(name)
            tools.append({
                "name": name,
                "label": pretty(name),
                "blurb": BLURB.get(name, ""),
                "caveat": CAVEAT.get(name, ""),
                "installed": installed,
                # ready is None when readiness is unknown (doctor unavailable or
                # tool has no spec); the UI only badges an explicit False.
                "ready": (r["ok"] if r else None) if installed else None,
                "issues": (r["issues"] if r else []) if installed else [],
                "notes": (r.get("notes", []) if r else []) if installed else [],
            })
        with self.lock:
            self.tools = tools

    def state(self):
        with self.lock:
            running = {n: v["url"] for n, v in self.running.items()
                       if v["proc"].poll() is None and port_open("127.0.0.1", v["port"])}
            # drop any that died
            self.running = {n: v for n, v in self.running.items() if n in running}
            return [dict(t, running=running.get(t["name"]), url=running.get(t["name"]))
                    for t in self.tools]

    def launch(self, name):
        with self.lock:
            cur = self.running.get(name)
            if cur and cur["proc"].poll() is None and port_open("127.0.0.1", cur["port"]):
                return cur["url"], None
            port = free_port()
            url = f"http://127.0.0.1:{port}/"
            logdir = Path(os.environ.get("BDTOOLS_HOME",
                          Path.home() / ".local/share/bdtools")) / "dashboard-logs"
            logdir.mkdir(parents=True, exist_ok=True)
            logf = open(logdir / f"{name}.log", "ab")
            try:
                proc = subprocess.Popen(
                    [BDTOOLS, "local", name, "--run-only", "--no-browser", "--port", str(port)],
                    cwd=str(REPO_DIR), stdout=logf, stderr=logf,
                    start_new_session=True, env=os.environ.copy())
            except OSError as exc:
                return None, str(exc)
            self.running[name] = {"port": port, "proc": proc, "url": url}
        # wait for the tool's uvicorn to come up
        for _ in range(120):  # up to ~60s
            if proc.poll() is not None:
                return None, f"the tool exited early — see {logf.name}"
            if port_open("127.0.0.1", port):
                return url, None
            time.sleep(0.5)
        return None, "timed out waiting for the tool to start (first launch may still be building)"

    # --- Updates -----------------------------------------------------------
    def check_updates(self):
        """Check bdtools + every tool for newer versions; cache and return it."""
        items = []
        bd = check_bdtools_update()
        if bd:
            items.append(bd)
        items.extend(check_tool_updates())
        cache = {
            "checked": True,
            "items": items,
            "any": any(i["update_available"] for i in items),
        }
        with self.lock:
            self.updates_cache = cache
        return cache

    def apply_updates(self, target):
        """Start a background update of `target` ('all', a tool name, or
        'bdtools'). Returns (started: bool, error: str|None)."""
        with self.lock:
            if self.update_job["running"]:
                return False, "an update is already running"
            self.update_job = {"running": True, "done": False, "ok": None,
                               "target": target, "log": []}
        threading.Thread(target=self._run_update, args=(target,), daemon=True).start()
        return True, None

    def _log(self, msg):
        with self.lock:
            self.update_job["log"].append(msg)
            # keep the tail bounded
            if len(self.update_job["log"]) > 2000:
                self.update_job["log"] = self.update_job["log"][-2000:]

    def _run_update(self, target):
        if target == "bdtools":
            cmd = ["git", "-C", str(REPO_DIR), "pull", "--ff-only"]
            self._log("$ git pull --ff-only  (updating bdtools)")
        else:
            cmd = [BDTOOLS, "update", target]
            self._log(f"$ bdtools update {target}")
            self._log("Rebuilding environments — this can take several minutes per tool…")
        ok = False
        try:
            proc = subprocess.Popen(cmd, cwd=str(REPO_DIR), stdout=subprocess.PIPE,
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
            self.update_job["running"] = False
            self.update_job["done"] = True
            self.update_job["ok"] = ok
        # Refresh the update cache so the banner reflects the new state.
        try:
            self.check_updates()
        except Exception:  # noqa: BLE001
            pass

    def update_status(self):
        with self.lock:
            j = self.update_job
            return {"running": j["running"], "done": j["done"], "ok": j["ok"],
                    "target": j["target"], "log": j["log"][-400:]}


SUITE = None


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kapur Lab Diagnostic Tools</title>
<style>
 :root{--bg:#f6f3ee;--card:#fff;--ink:#2c2a26;--muted:#7c756a;--accent:#a8553a;
       --accent2:#6b8f71;--line:#e6ded2;}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
   font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
 header{padding:28px 24px 8px}h1{margin:0;font-size:22px}
 p.sub{margin:4px 0 0;color:var(--muted)}
 .grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));
   padding:20px 24px 40px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
   padding:16px 16px 14px;display:flex;flex-direction:column;gap:8px;
   box-shadow:0 1px 2px rgba(0,0,0,.03)}
 .name{font-weight:650;font-size:16px}
 .blurb{color:var(--muted);font-size:13px;min-height:34px}
 .row{display:flex;align-items:center;justify-content:space-between;margin-top:4px}
 button{font:inherit;border:0;border-radius:8px;padding:8px 14px;cursor:pointer;
   background:var(--accent);color:#fff;font-weight:600}
 button:disabled{background:#cfc7ba;cursor:not-allowed}
 button.open{background:var(--accent2)}
 .pill{font-size:12px;padding:2px 9px;border-radius:999px;background:#efe9df;color:var(--muted)}
 .pill.on{background:#e2efe4;color:#3f6b48}
 .pill.warn{background:#fbedd6;color:#9a6212}
 .note{padding:0 24px;color:var(--muted);font-size:13px}
 a.foot{color:var(--accent)}
 .err{color:#b23b2e;font-size:12px;min-height:14px}
 .setup{background:#fbf5ea;border:1px solid #f0e2c8;border-radius:8px;padding:8px 10px;
   font-size:12px;color:#7a5a1e}
 .setup b{color:#6b4f1a}
 .setup code{background:#f3e7cf;padding:1px 5px;border-radius:4px;
   font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;
   user-select:all;word-break:break-all}
 .plat{font-size:12px;color:var(--muted);font-style:italic}
 .dev{background:#fbecec;border:1px solid #ecc9c4;border-radius:8px;padding:8px 10px;
   font-size:12px;color:#8a3324}
 .dev b{color:#7a2a1e}
 .recheck{padding:0 24px 12px;font-size:13px}
 .recheck button{background:transparent;color:var(--accent);padding:4px 0;font-weight:600}
 .updates{margin:8px 24px 0;border-radius:10px;padding:12px 14px;font-size:13.5px;
   border:1px solid var(--line);background:var(--card)}
 .updates.checking{color:var(--muted)}
 .updates.current{background:#e9f1ea;border-color:#cfe2d1;color:#3f6b48}
 .updates.avail{background:#fbf1dc;border-color:#f0dcae;color:#7a5a1e}
 .updates .uhead{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
 .updates .utitle{font-weight:650}
 .updates ul{margin:8px 0 0;padding-left:18px}
 .updates li{margin:2px 0}
 .updates .uactions{display:flex;gap:8px;flex-wrap:wrap}
 .updates button.u{background:var(--accent)}
 .updates button.link{background:transparent;color:var(--accent);padding:4px 6px;font-weight:600}
 .ulog{margin-top:10px;background:#2c2a26;color:#eee;border-radius:8px;padding:8px 10px;
   font:12px/1.45 ui-monospace,Menlo,Consolas,monospace;max-height:220px;overflow:auto;white-space:pre-wrap}
 .udone{margin-top:8px;font-weight:600}
</style></head><body>
<header><h1>Kapur Lab Diagnostic Tools</h1>
<p class="sub">Pick a tool to launch it on this machine. Each opens in a new tab.</p></header>
<div id="updates" class="updates checking">Checking for updates…</div>
<div id="grid" class="grid"></div>
<p class="recheck" id="recheck" style="display:none"><button onclick="recheck(this)">↻ Re-check readiness</button></p>
<p class="note" id="note"></p>
<script>
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function setupBlock(t){
  // Installed but not runnable yet: list what's missing + the fix commands.
  if(!t.installed || t.ready!==false || !(t.issues&&t.issues.length)) return '';
  const items = t.issues.map(i=>`<div>• ${esc(i.label)} — <code>${esc(i.fix)}</code></div>`).join('');
  return `<div class="setup"><b>Needs setup before it can run:</b>${items}</div>`;
}
function noteBlock(t){
  // Platform limitations (e.g. a step unavailable on macOS) — informational.
  if(!t.installed || !(t.notes&&t.notes.length)) return '';
  return `<div class="plat">${t.notes.map(n=>'⚠ '+esc(n)).join('<br>')}</div>`;
}
function devBlock(t){
  // Static development notice — the tool is not yet validated for diagnostic use.
  if(!t.caveat) return '';
  return `<div class="dev"><b>⚠ Development status:</b> ${esc(t.caveat)}</div>`;
}
async function load(){
  const r = await fetch('./api/tools'); const tools = await r.json();
  const g = document.getElementById('grid'); g.innerHTML='';
  let anyInstalled=false, anyIssues=false;
  for(const t of tools){
    if(t.installed) anyInstalled=true;
    const needs = t.installed && t.ready===false;
    if(needs) anyIssues=true;
    const c=document.createElement('div'); c.className='card';
    const pill = t.running ? `<span class="pill on">running</span>`
      : needs ? `<span class="pill warn">needs setup</span>`
      : `<span class="pill">${t.installed?'installed':'not installed'}</span>`;
    c.innerHTML = `<div class="name">${t.label}</div>
      <div class="blurb">${t.blurb||''}</div>
      ${devBlock(t)}
      ${setupBlock(t)}
      ${noteBlock(t)}
      <div class="row">
        ${pill}
        <button ${t.installed?'':'disabled'} data-tool="${t.name}" class="${t.running?'open':''}">
          ${t.running?'Open':'Launch'}</button>
      </div><div class="err" id="err-${t.name}"></div>`;
    const b=c.querySelector('button');
    b.onclick=()=>act(t.name,b);
    g.appendChild(c);
  }
  document.getElementById('recheck').style.display = anyIssues ? '' : 'none';
  document.getElementById('note').innerHTML = anyInstalled ? '' :
    'No tools are built yet. Install one first, e.g. <code>bin/bdtools install mlst_gui</code>.';
}
async function recheck(btn){
  btn.disabled=true; const was=btn.textContent; btn.textContent='Checking…';
  try{ await fetch('./api/recheck',{method:'POST'}); }catch(e){}
  await load(); btn.disabled=false; btn.textContent=was;
}
async function act(name,btn){
  const err=document.getElementById('err-'+name); err.textContent='';
  btn.disabled=true; const was=btn.textContent; btn.textContent='Starting…';
  try{
    const r=await fetch('./api/launch?tool='+encodeURIComponent(name),{method:'POST'});
    const j=await r.json();
    if(j.url){ window.open(j.url,'_blank'); }
    else { err.textContent = j.error || 'failed to launch'; }
  }catch(e){ err.textContent=String(e); }
  btn.disabled=false; btn.textContent=was; load();
}
// ---- Updates: check on open, show a cue, offer install buttons ----
let updatePolling = false;
function renderUpdates(d){
  const box = document.getElementById('updates');
  if(!d || !d.checked){ box.className='updates checking'; box.textContent='Checking for updates…'; return; }
  const items = d.items || [];
  const avail = items.filter(i=>i.update_available);
  if(!avail.length){
    box.className='updates current';
    box.innerHTML = `<div class="uhead"><span class="utitle">✓ Everything is up to date.</span>`
      + `<button class="link" onclick="checkUpdates(true)">Re-check</button></div>`;
    return;
  }
  box.className='updates avail';
  const bd = avail.find(i=>i.name==='bdtools');
  const toolUps = avail.filter(i=>i.name!=='bdtools');
  const li = avail.map(i=>`<li><b>${esc(i.label)}</b>: ${esc(i.installed)} → <b>${esc(i.latest)}</b></li>`).join('');
  let actions = '';
  if(toolUps.length) actions += `<button class="u" onclick="applyUpdates('all',this)">Install tool updates (${toolUps.length})</button>`;
  if(bd) actions += `<button class="u" onclick="applyUpdates('bdtools',this)">Update bdtools</button>`;
  actions += `<button class="link" onclick="checkUpdates(true)">Re-check</button>`;
  box.innerHTML = `<div class="uhead"><span class="utitle">↑ Updates available (${avail.length})</span>`
    + `<span class="uactions">${actions}</span></div>`
    + `<ul>${li}</ul>`
    + `<div class="udesc" style="margin-top:6px;color:#7a5a1e">Installing rebuilds the tool's environment and can take a few minutes. `
    + `When it finishes, <b>close this window and reopen the dashboard</b> to load the new version.</div>`
    + `<div id="ulog" class="ulog" style="display:none"></div>`
    + `<div id="udone" class="udone"></div>`;
}
async function checkUpdates(force){
  const box=document.getElementById('updates');
  if(force){ box.className='updates checking'; box.textContent='Checking for updates…'; }
  try{
    const r = await fetch('./api/check-updates',{method:'POST'});
    renderUpdates(await r.json());
  }catch(e){
    box.className='updates'; box.textContent='Could not check for updates right now.';
  }
}
async function applyUpdates(target,btn){
  if(!confirm(target==='bdtools'
      ? 'Update bdtools (the suite + this dashboard) now?'
      : 'Install tool updates now? This rebuilds environments and may take several minutes.')) return;
  document.querySelectorAll('.updates button').forEach(b=>b.disabled=true);
  const log=document.getElementById('ulog'); if(log){ log.style.display='block'; log.textContent='Starting…\n'; }
  try{
    const r=await fetch('./api/apply-updates?target='+encodeURIComponent(target),{method:'POST'});
    const j=await r.json();
    if(!j.started){ if(log) log.textContent += (j.error||'could not start')+'\n'; return; }
    pollUpdate();
  }catch(e){ if(log) log.textContent += String(e)+'\n'; }
}
async function pollUpdate(){
  if(updatePolling) return; updatePolling=true;
  const log=document.getElementById('ulog'); const done=document.getElementById('udone');
  const tick=async()=>{
    try{
      const r=await fetch('./api/update-status'); const s=await r.json();
      if(log){ log.textContent=(s.log||[]).join('\n'); log.scrollTop=log.scrollHeight; }
      if(s.done){
        updatePolling=false;
        if(done) done.innerHTML = s.ok
          ? '✅ Updates installed. <b>Close this window and reopen the dashboard</b> to load the new versions.'
          : '⚠ Update finished with errors — see the log above.';
        return;
      }
    }catch(e){}
    setTimeout(tick, 1500);
  };
  tick();
}
load(); setInterval(load, 5000);
checkUpdates(false);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/api/tools":
            self._send(200, json.dumps(SUITE.state()))
        elif path == "/api/updates":
            cache = SUITE.updates_cache or {"checked": False, "items": [], "any": False}
            self._send(200, json.dumps(cache))
        elif path == "/api/update-status":
            self._send(200, json.dumps(SUITE.update_status()))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/recheck":
            # Re-run readiness (after the user installs a database / fixes a dep).
            SUITE.refresh()
            self._send(200, json.dumps(SUITE.state()))
            return
        if parsed.path == "/api/check-updates":
            self._send(200, json.dumps(SUITE.check_updates()))
            return
        if parsed.path == "/api/apply-updates":
            target = (parse_qs(parsed.query).get("target") or ["all"])[0]
            valid = {"all", "bdtools"} | {t["name"] for t in SUITE.tools}
            if target not in valid:
                self._send(400, json.dumps({"error": f"unknown update target: {target}"}))
                return
            started, err = SUITE.apply_updates(target)
            code = 200 if started else 409
            self._send(code, json.dumps({"started": started, "error": err}))
            return
        if parsed.path == "/api/launch":
            tool = (parse_qs(parsed.query).get("tool") or [""])[0]
            names = {t["name"] for t in SUITE.tools}
            if tool not in names:
                self._send(400, json.dumps({"error": "unknown tool"}))
                return
            url, err = SUITE.launch(tool)
            if url:
                self._send(200, json.dumps({"url": url}))
            else:
                self._send(500, json.dumps({"error": err or "launch failed"}))
        else:
            self._send(404, json.dumps({"error": "not found"}))


def main():
    global SUITE
    ap = argparse.ArgumentParser(description="Kapur Lab local tool dashboard.")
    ap.add_argument("--port", type=int, default=None, help="dashboard port (default: 8080 or a free port)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    # If the dashboard is already running on the default port, don't start a
    # second copy — just reopen the browser to it (handles double-clicking twice).
    if args.port is None and _is_dashboard(args.host, 8080):
        url = f"http://{args.host}:8080/"
        print(f"The dashboard is already running — opening {url}")
        if not args.no_browser:
            _open(url)
        return

    port = args.port
    if port is None:
        port = 8080 if not port_open(args.host, 8080) else free_port()

    SUITE = Suite()
    httpd = ThreadingHTTPServer((args.host, port), Handler)
    url = f"http://{args.host}:{port}/"
    n_installed = sum(1 for t in SUITE.tools if t["installed"])
    bar = "=" * 64
    print(f"""
{bar}
  Kapur Lab Diagnostic Tools — your dashboard is running.

  Open this in your web browser:
      {url}

  {n_installed} of {len(SUITE.tools)} tools are installed and ready. Click a tool to launch it.

  Keep this window open while you work.
  To stop everything: close this window (or press Control-C).
  To start it again later, just re-open the dashboard (see the README).
{bar}
""", flush=True)
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1), _open(url)), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped. Re-open it any time with 'bin/bdtools dashboard'.")


def _is_dashboard(host, port):
    """True if our dashboard is already serving on host:port."""
    if not port_open(host, port):
        return False
    try:
        import urllib.request
        body = urllib.request.urlopen(f"http://{host}:{port}/", timeout=2).read(400).decode("utf-8", "replace")
        return "Kapur Lab Diagnostic Tools" in body
    except Exception:
        return False


def _open(url):
    for cmd in (["open", url], ["xdg-open", url], ["wslview", url]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue


if __name__ == "__main__":
    main()
