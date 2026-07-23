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
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen

BIN_DIR = Path(__file__).resolve().parent
REPO_DIR = BIN_DIR.parent
BDTOOLS = str(BIN_DIR / "bdtools")
CONTROL_TOKEN = os.environ.get("BDTOOLS_CONTROL_TOKEN", "").strip() or secrets.token_urlsafe(32)
STATE_FILE = os.environ.get("BDTOOLS_DASHBOARD_STATE_FILE", "").strip()
ACTIVE_JOB_STATES = {"queued", "running", "stopping", "cancelling"}

# Display tables + subprocess/update helpers are shared with the proxy dashboard
# (bin/ood_dashboard/app.py) so the two can never drift apart.
sys.path.insert(0, str(BIN_DIR / "lib"))
from suite_common import (  # noqa: E402
    BLURB, CAVEAT, pretty, free_port, port_open, list_tools,
    tool_python, readiness_map, check_tool_updates, check_bdtools_update,
    write_dashboard_state, remove_dashboard_state,
)


class Suite:
    """Tracks installed tools and any tool servers this dashboard has launched."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = {}  # name -> {"port": int, "proc": Popen, "url": str}
        self.starting = {}  # name -> {"event": Event, "result": (url, error)}
        self.quiescing = False
        self.updating = set()
        self.tools = []    # [{"name","label","blurb","installed"}]
        # Update state: cached check + a single background apply job.
        self.updates_cache = None   # {"checked": bool, "items": [...], "any": bool}
        self.updates_checking = False
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
            alive = {n: v for n, v in self.running.items() if v["proc"].poll() is None}
            running = {n: v["url"] for n, v in alive.items()
                       if port_open("127.0.0.1", v["port"])}
            # Drop only exited processes. A live process with a closed port is
            # still in startup and must remain tracked.
            for n, v in self.running.items():
                if n not in alive and v.get("log"):
                    v["log"].close()
            self.running = alive
            return [dict(
                        t,
                        running=running.get(t["name"]),
                        url=running.get(t["name"]),
                        starting=t["name"] in self.starting,
                        updating=(t["name"] in self.updating or "*" in self.updating),
                    )
                    for t in self.tools]

    def launch(self, name):
        with self.lock:
            if self.quiescing:
                return None, "the dashboard is shutting down or restarting"
            if name in self.updating or "*" in self.updating:
                return None, f"{name} is being updated; wait for the update to finish"
            cur = self.running.get(name)
            if cur and cur["proc"].poll() is None and port_open("127.0.0.1", cur["port"]):
                return cur["url"], None
            pending = self.starting.get(name)
            creator = pending is None
            if creator:
                pending = {"event": threading.Event(), "result": (None, "startup interrupted")}
                self.starting[name] = pending
        if not creator:
            if not pending["event"].wait(65):
                return None, "timed out waiting for the existing startup request"
            return pending["result"]

        result = (None, "startup interrupted")
        logf = None
        try:
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
                result = (None, str(exc))
                return result
            with self.lock:
                self.running[name] = {
                    "port": port, "proc": proc, "url": url, "log": logf,
                }
            for _ in range(120):  # up to ~60s
                if proc.poll() is not None:
                    result = (None, f"the tool exited early — see {logf.name}")
                    return result
                if port_open("127.0.0.1", port):
                    result = (url, None)
                    return result
                time.sleep(0.5)
            result = (None, "timed out waiting for the tool to start (first launch may still be building)")
            return result
        finally:
            with self.lock:
                pending["result"] = result
                pending["event"].set()
                self.starting.pop(name, None)
                failed = self.running.get(name) if result[0] is None else None
                if failed and failed.get("log") is logf:
                    self.running.pop(name, None)
            if failed:
                proc = failed["proc"]
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
            if result[0] is None and logf is not None:
                logf.close()

    def activity(self, names=None):
        active, errors = [], []
        with self.lock:
            entries = list(self.running.items())
        selected = set(names or dict(entries))
        for name, v in entries:
            if name not in selected:
                continue
            if v["proc"].poll() is not None:
                continue
            try:
                with urlopen(f"http://127.0.0.1:{v['port']}/api/jobs", timeout=5) as response:
                    jobs = json.loads(response.read().decode("utf-8"))
                if not isinstance(jobs, list):
                    raise ValueError("expected a JSON list")
                for job in jobs:
                    if str(job.get("status", "")).lower() in ACTIVE_JOB_STATES:
                        active.append({
                            "tool": name,
                            "id": str(job.get("id", "")),
                            "name": str(job.get("name", "")),
                            "status": str(job.get("status", "")),
                        })
            except Exception as exc:
                errors.append({"tool": name, "error": str(exc)})
        return {"safe": not active and not errors, "active": active, "errors": errors}

    def begin_quiesce(self, names=None):
        with self.lock:
            self.quiescing = True
            starting = list(self.starting.items())
        selected = set(names) if names is not None else None
        deadline = time.monotonic() + 65
        startup_errors = []
        for name, pending in starting:
            if selected is not None and name not in selected:
                continue
            remaining = max(0, deadline - time.monotonic())
            if not pending["event"].wait(remaining):
                startup_errors.append({
                    "tool": name, "error": "tool startup is still in progress",
                })
        if startup_errors:
            with self.lock:
                self.quiescing = False
            return {"safe": False, "active": [], "errors": startup_errors}
        snapshot = self.activity(names)
        if not snapshot["safe"]:
            with self.lock:
                self.quiescing = False
        return snapshot

    def stop_backends(self, names=None):
        with self.lock:
            selected = set(names or self.running)
            entries = [(n, v) for n, v in self.running.items() if n in selected]
        for _, v in entries:
            if v["proc"].poll() is None:
                v["proc"].terminate()
        deadline = time.monotonic() + 10
        for _, v in entries:
            remaining = max(0, deadline - time.monotonic())
            try:
                v["proc"].wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                v["proc"].kill()
                v["proc"].wait(timeout=5)
        with self.lock:
            for name, v in entries:
                self.running.pop(name, None)
                if v.get("log"):
                    v["log"].close()

    def prepare_update(self, target):
        if target == "bdtools":
            return {"safe": True, "active": [], "errors": []}
        names = set(self.running) if target == "all" else {target}
        snapshot = self.begin_quiesce(names)
        if not snapshot["safe"]:
            return snapshot
        self.stop_backends(names)
        with self.lock:
            self.updating = {"*"} if target == "all" else {target}
            self.quiescing = False
        return snapshot

    def finish_update(self):
        with self.lock:
            self.updating.clear()
        self.refresh()

    # --- Updates -----------------------------------------------------------
    def check_updates(self):
        """Check bdtools + every tool for newer versions; cache and return it.
        Network-heavy (git ls-remote per tool) — always run in the background
        via check_updates_async so it never blocks the dashboard or launches."""
        try:
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
        except Exception as exc:
            cache = {"checked": True, "items": [], "any": False, "error": str(exc)}
        finally:
            with self.lock:
                self.updates_checking = False
        with self.lock:
            self.updates_cache = cache
        return cache

    def check_updates_async(self, force=False):
        """Start the update check on a background thread (no-op if one is already
        running, or if we already have a result and force is False)."""
        with self.lock:
            if self.updates_checking:
                return
            if self.updates_cache and self.updates_cache.get("checked") and not force:
                return
            self.updates_checking = True
        threading.Thread(target=self.check_updates, daemon=True).start()

    def updates_state(self):
        with self.lock:
            cache = self.updates_cache or {"checked": False, "items": [], "any": False}
            return dict(cache, checking=self.updates_checking)

    def apply_updates(self, target):
        """Start a background update of `target` ('all', a tool name, or
        'bdtools'). Returns (started: bool, error: str|None)."""
        with self.lock:
            if self.update_job["running"]:
                return False, "an update is already running"
            self.update_job = {"running": True, "done": False, "ok": None,
                               "target": target, "log": []}
        # Keep the interpreter alive through Ctrl-C until the update finishes;
        # abandoning a checkout/environment rebuild mid-flight is unsafe.
        threading.Thread(target=self._run_update, args=(target,), daemon=False).start()
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
        self.check_updates_async(force=True)

    def update_status(self):
        with self.lock:
            j = self.update_job
            status = {"running": j["running"], "done": j["done"], "ok": j["ok"],
                      "target": j["target"], "log": j["log"][-400:]}
        if status["done"] and self.updating:
            self.finish_update()
        return status


SUITE = None


def _stop_tools():
    """Terminate every tool server this dashboard launched (best effort)."""
    if SUITE is None:
        return
    SUITE.stop_backends()


def _schedule_exit(code):
    """Stop the tools, then exit the process with `code` after the HTTP response
    has flushed. The `bdtools dashboard` supervisor loop reads the code: 42 =>
    relaunch (Restart), anything else => stop for good (Shut down)."""
    def run():
        time.sleep(0.4)
        _stop_tools()
        remove_dashboard_state(STATE_FILE)
        os._exit(code)
    threading.Thread(target=run, daemon=True).start()


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
 /* Subtle by default (checking / up-to-date are just a small muted line, so the
    dashboard is usable immediately); only 'updates available' is a real banner. */
 .updates{margin:6px 24px 0;font-size:12.5px;color:var(--muted)}
 .updates:empty{display:none}
 .updates.checking{color:var(--muted)}
 .updates.current{color:#3f6b48}
 .updates.avail{background:#fbf1dc;border:1px solid #f0dcae;color:#7a5a1e;
   border-radius:10px;padding:12px 14px;font-size:13.5px;margin-top:8px}
 .updates.avail a{color:inherit}
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
 .hbar{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
 .ctl{text-align:right}
 .host{color:var(--muted);font-size:12.5px;margin-bottom:6px}
 .host b{color:var(--ink)}
 .ctlbtns{display:flex;gap:8px;justify-content:flex-end}
 .ctlbtns button{padding:6px 12px;font-size:13px}
 button.restart{background:var(--accent2)}
 button.shutdown{background:#8a3a2e}
 .overlay{position:fixed;inset:0;background:rgba(44,42,38,.72);display:flex;
   align-items:center;justify-content:center;z-index:999}
 .obox{background:var(--card);border-radius:14px;padding:28px 32px;max-width:460px;
   text-align:center;box-shadow:0 8px 40px rgba(0,0,0,.25)}
 .obox h2{margin:14px 0 8px;font-size:19px}
 .obox p{margin:0;color:var(--muted);font-size:14px;line-height:1.5}
 .obox code{background:#f3e7cf;padding:1px 6px;border-radius:4px;
   font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;user-select:all}
 .ospin{width:34px;height:34px;border:3px solid var(--line);border-top-color:var(--accent);
   border-radius:50%;margin:0 auto;animation:spin 1s linear infinite}
 .ospin.done{animation:none;border:0;font-size:34px;line-height:34px;width:auto;height:auto}
 @keyframes spin{to{transform:rotate(360deg)}}
</style></head><body>
<header><div class="hbar">
  <div><h1>Kapur Lab Diagnostic Tools</h1>
  <p class="sub">Pick a tool to launch it on this machine. Each opens in a new tab.</p></div>
  <div class="ctl" id="ctl" style="display:none">
    <div class="host" id="host"></div>
    <div class="ctlbtns">
      <button class="restart" onclick="restartDash()">↻ Restart dashboard</button>
      <button class="shutdown" onclick="shutdownDash()">⏻ Shut down</button>
    </div>
  </div>
</div></header>
<div id="overlay" class="overlay" style="display:none"><div class="obox">
  <div class="ospin" id="ospin"></div>
  <h2 id="otitle"></h2><p id="omsg"></p>
</div></div>
<div id="updates" class="updates"></div>
<div id="grid" class="grid"></div>
<p class="recheck" id="recheck" style="display:none"><button onclick="recheck(this)">↻ Re-check readiness</button></p>
<p class="note" id="note"></p>
<script>
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
let controlToken='';
async function ensureControl(){
  if(controlToken) return controlToken;
  const r=await fetch('./api/info',{cache:'no-store'});
  if(!r.ok) throw new Error('could not obtain dashboard control token');
  const d=await r.json(); controlToken=d.control_token||'';
  return controlToken;
}
async function controlFetch(url,options={}){
  const token=await ensureControl();
  const headers=new Headers(options.headers||{});
  if(token) headers.set('X-Bdtools-Control',token);
  return fetch(url,{...options,headers});
}
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
    const pill = t.updating ? `<span class="pill warn">updating</span>`
      : t.starting ? `<span class="pill">starting</span>`
      : t.running ? `<span class="pill on">running</span>`
      : needs ? `<span class="pill warn">needs setup</span>`
      : `<span class="pill">${t.installed?'installed':'not installed'}</span>`;
    c.innerHTML = `<div class="name">${t.label}</div>
      <div class="blurb">${t.blurb||''}</div>
      ${devBlock(t)}
      ${setupBlock(t)}
      ${noteBlock(t)}
      <div class="row">
        ${pill}
        <button ${(t.installed&&!t.updating)?'':'disabled'} data-tool="${t.name}" class="${t.running?'open':''}">
          ${t.updating?'Updating…':t.starting?'Starting…':t.running?'Open':'Launch'}</button>
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
  try{ await controlFetch('./api/recheck',{method:'POST'}); }catch(e){}
  await load(); btn.disabled=false; btn.textContent=was;
}
async function act(name,btn){
  const err=document.getElementById('err-'+name); err.textContent='';
  btn.disabled=true; const was=btn.textContent; btn.textContent='Starting…';
  try{
    const r=await controlFetch('./api/launch?tool='+encodeURIComponent(name),{method:'POST'});
    const j=await r.json();
    if(j.url){ window.open(j.url,'_blank'); }
    else { err.textContent = j.error || 'failed to launch'; }
  }catch(e){ err.textContent=String(e); }
  btn.disabled=false; btn.textContent=was; load();
}
// ---- Updates: checked in the BACKGROUND so the dashboard is usable at once.
//      Subtle while checking / up-to-date; a prominent banner only if updates exist.
let updatePolling = false;
let updatesPoll = null;
function renderUpdates(d){
  const box = document.getElementById('updates');
  if(!d || !d.checked){
    // Non-blocking: a tiny muted note (or nothing) — never a gate.
    box.className='updates checking';
    box.textContent = d && d.checking ? '↻ checking for updates in the background…' : '';
    return;
  }
  const items = d.items || [];
  const avail = items.filter(i=>i.update_available);
  if(!avail.length){
    box.className='updates current';
    box.innerHTML = `✓ Up to date. <a href="#" onclick="checkUpdates(true);return false" style="color:inherit">Re-check</a>`;
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
    + `Idle tool servers are stopped first. When it finishes, use <b>Restart dashboard</b> to load the new version.</div>`
    + `<div id="ulog" class="ulog" style="display:none"></div>`
    + `<div id="udone" class="udone"></div>`;
}
// Poll the cached result without blocking; keep polling only until it's ready.
async function pollUpdates(){
  try{
    const r = await fetch('./api/updates');
    const d = await r.json();
    renderUpdates(d);
    if(!d.checked){
      clearTimeout(updatesPoll);
      updatesPoll = setTimeout(pollUpdates, 2500);
    }
  }catch(e){ /* leave the dashboard alone; try again on the next tick */ }
}
async function checkUpdates(force){
  const box=document.getElementById('updates');
  box.className='updates checking'; box.textContent='↻ checking for updates in the background…';
  try{ await controlFetch('./api/check-updates',{method:'POST'}); }catch(e){}
  pollUpdates();
}
async function applyUpdates(target,btn){
  if(!confirm(target==='bdtools'
      ? 'Update bdtools (the suite + this dashboard) now?'
      : 'Install tool updates now? This rebuilds environments and may take several minutes.')) return;
  document.querySelectorAll('.updates button').forEach(b=>b.disabled=true);
  const log=document.getElementById('ulog'); if(log){ log.style.display='block'; log.textContent='Starting…\\n'; }
  try{
    const r=await controlFetch('./api/apply-updates?target='+encodeURIComponent(target),{method:'POST'});
    const j=await r.json();
    if(!j.started){
      if(log) log.textContent += describeBlock(j)+'\\n';
      document.querySelectorAll('.updates button').forEach(b=>b.disabled=false);
      return;
    }
    pollUpdate();
  }catch(e){ if(log) log.textContent += String(e)+'\\n'; }
}
async function pollUpdate(){
  if(updatePolling) return; updatePolling=true;
  const log=document.getElementById('ulog'); const done=document.getElementById('udone');
  const tick=async()=>{
    try{
      const r=await fetch('./api/update-status'); const s=await r.json();
      if(log){ log.textContent=(s.log||[]).join('\\n'); log.scrollTop=log.scrollHeight; }
      if(s.done){
        updatePolling=false;
        if(done) done.innerHTML = s.ok
          ? '✅ Updates installed. Use <b>Restart dashboard</b> above to load the new versions.'
          : '⚠ Update finished with errors — see the log above.';
        return;
      }
    }catch(e){}
    setTimeout(tick, 1500);
  };
  tick();
}
// ---- Which machine is this? + Shut down / Restart controls (local mode only).
async function loadInfo(){
  try{
    const r = await fetch('./api/info'); const d = await r.json();
    controlToken=d.control_token||'';
    document.getElementById('host').innerHTML =
      'This dashboard is running on <b>'+esc(d.host)+'</b>.';
    if(d.can_control) document.getElementById('ctl').style.display='';
  }catch(e){ /* leave controls hidden */ }
}
function describeBlock(j){
  let msg=(j&&j.error)||'operation blocked';
  const active=(j&&j.active)||[], errors=(j&&j.errors)||[];
  if(active.length) msg+='\\n\\nActive analyses:\\n'+active.map(x=>
    '• '+x.tool+' — '+(x.name||x.id||'job')+' ('+x.status+')').join('\\n');
  if(errors.length) msg+='\\n\\nCould not verify:\\n'+errors.map(x=>
    '• '+x.tool+' — '+x.error).join('\\n');
  msg+='\\n\\nWait for active jobs to finish (or stop them in the tool), then try again.';
  return msg;
}
function overlay(title,msg,doneGlyph){
  document.getElementById('otitle').textContent = title;
  document.getElementById('omsg').innerHTML = msg;
  const sp = document.getElementById('ospin');
  if(doneGlyph){ sp.className='ospin done'; sp.textContent=doneGlyph; }
  else { sp.className='ospin'; sp.textContent=''; }
  document.getElementById('overlay').style.display='flex';
}
async function shutdownDash(){
  if(!confirm(
    "Shut the dashboard completely down?\\n\\n"+
    "This stops every idle tool server AND the dashboard itself — the "+
    "“./bdtools dashboard” command in your terminal will exit. Use this when "+
    "you are done working. If an analysis is running, shutdown is safely blocked "+
    "until it finishes or you stop it in that tool.\\n\\n"+
    "A web page cannot start it back up, so to reopen it you'll go to a terminal and run:\\n"+
    "    ./bdtools dashboard")) return;
  try{
    const r=await controlFetch('./api/shutdown',{method:'POST'});
    if(!r.ok){
      const j=await r.json();
      alert(describeBlock(j));
      return;
    }
  }catch(e){ alert(String(e)); return; }
  overlay('Dashboard shut down',
    'Everything has stopped. You can close this tab.<br><br>'+
    'To start it again, run <code>./bdtools dashboard</code> in a terminal.','⏻');
}
async function restartDash(){
  if(!confirm(
    "Restart the dashboard?\\n\\n"+
    "This stops idle tool servers and relaunches the dashboard so any updated "+
    "code takes effect. It comes back on this same web address within a few "+
    "seconds and this page will reconnect on its own. Active analyses safely block "+
    "restart until they finish or are stopped in their tool.")) return;
  try{
    const r=await controlFetch('./api/restart',{method:'POST'});
    if(!r.ok){
      const j=await r.json();
      alert(describeBlock(j));
      return;
    }
  }catch(e){ alert(String(e)); return; }
  overlay('Restarting the dashboard…',
    'Stopping idle tool servers and reloading updated code. This page will reconnect automatically.');
  // The server exits, the terminal supervisor relaunches it on the same port;
  // poll until it answers again, then reload to the fresh dashboard.
  let tries=0;
  const ping=async()=>{
    tries++;
    try{
      const r=await fetch('./api/info',{cache:'no-store'});
      if(r.ok){ overlay('Back up','Reloading…','✓'); setTimeout(()=>location.reload(),700); return; }
    }catch(e){}
    if(tries>90){
      overlay('Still restarting…',
        'This is taking longer than usual. If the page does not come back, restart '+
        'from a terminal with <code>./bdtools dashboard --restart</code>.'); }
    setTimeout(ping,1000);
  };
  setTimeout(ping,1500);
}
load(); setInterval(load, 5000);
loadInfo();
pollUpdates();   // background update check — the cards above are usable immediately
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
        elif path == "/api/info":
            self._send(200, json.dumps(
                {"host": socket.gethostname(), "local": True, "can_control": True,
                 "control_token": CONTROL_TOKEN}))
        elif path == "/api/tools":
            self._send(200, json.dumps(SUITE.state()))
        elif path == "/api/updates":
            # Non-blocking: kick off the (slow, network-heavy) check in the
            # background the first time it's asked for, and return whatever we
            # have right now so the page never waits on it.
            SUITE.check_updates_async()
            self._send(200, json.dumps(SUITE.updates_state()))
        elif path == "/api/update-status":
            self._send(200, json.dumps(SUITE.update_status()))
        elif path == "/api/activity":
            self._send(200, json.dumps(SUITE.activity()))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        parsed = urlparse(self.path)
        supplied = self.headers.get("X-Bdtools-Control", "")
        if not secrets.compare_digest(supplied, CONTROL_TOKEN):
            self._send(403, json.dumps({"error": "forbidden (missing control token)"}))
            return
        if parsed.path == "/api/shutdown":
            if SUITE.update_job["running"]:
                self._send(409, json.dumps({
                    "error": "an update is still running", "safe": False,
                    "active": [], "errors": [{"tool": "dashboard", "error": "update in progress"}],
                }))
                return
            snapshot = SUITE.begin_quiesce()
            if not snapshot["safe"]:
                self._send(409, json.dumps({
                    "error": "active or unverifiable analyses prevent shutdown",
                    **snapshot,
                }))
                return
            self._send(200, json.dumps({"stopping": True}))
            _schedule_exit(0)
            return
        if parsed.path == "/api/restart":
            if SUITE.update_job["running"]:
                self._send(409, json.dumps({
                    "error": "an update is still running", "safe": False,
                    "active": [], "errors": [{"tool": "dashboard", "error": "update in progress"}],
                }))
                return
            snapshot = SUITE.begin_quiesce()
            if not snapshot["safe"]:
                self._send(409, json.dumps({
                    "error": "active or unverifiable analyses prevent restart",
                    **snapshot,
                }))
                return
            self._send(200, json.dumps({"restarting": True}))
            _schedule_exit(42)
            return
        if parsed.path == "/api/recheck":
            # Re-run readiness (after the user installs a database / fixes a dep).
            SUITE.refresh()
            self._send(200, json.dumps(SUITE.state()))
            return
        if parsed.path == "/api/check-updates":
            # Manual "re-check" — force a fresh background check, return at once.
            SUITE.check_updates_async(force=True)
            self._send(200, json.dumps(SUITE.updates_state()))
            return
        if parsed.path == "/api/apply-updates":
            target = (parse_qs(parsed.query).get("target") or ["all"])[0]
            valid = {"all", "bdtools"} | {t["name"] for t in SUITE.tools}
            if target not in valid:
                self._send(400, json.dumps({"error": f"unknown update target: {target}"}))
                return
            if SUITE.update_job["running"]:
                self._send(409, json.dumps({
                    "started": False, "error": "an update is already running",
                }))
                return
            snapshot = SUITE.prepare_update(target)
            if not snapshot["safe"]:
                self._send(409, json.dumps({
                    "started": False,
                    "error": "active or unverifiable analyses prevent updates",
                    **snapshot,
                }))
                return
            started, err = SUITE.apply_updates(target)
            if not started and target != "bdtools":
                SUITE.finish_update()
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
    SUITE.check_updates_async()  # warm the update check in the background
    httpd = ThreadingHTTPServer((args.host, port), Handler)
    write_dashboard_state(STATE_FILE, port, CONTROL_TOKEN)
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
    finally:
        _stop_tools()
        remove_dashboard_state(STATE_FILE)


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
