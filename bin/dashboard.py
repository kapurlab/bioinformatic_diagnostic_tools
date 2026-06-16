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


class Suite:
    """Tracks installed tools and any tool servers this dashboard has launched."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = {}  # name -> {"port": int, "proc": Popen, "url": str}
        self.tools = []    # [{"name","label","blurb","installed"}]
        self.refresh()

    def refresh(self):
        tools = []
        for name in list_tools():
            tools.append({
                "name": name,
                "label": pretty(name),
                "blurb": BLURB.get(name, ""),
                "installed": tool_python(name) is not None,
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
 .note{padding:0 24px;color:var(--muted);font-size:13px}
 a.foot{color:var(--accent)}
 .err{color:#b23b2e;font-size:12px;min-height:14px}
</style></head><body>
<header><h1>Kapur Lab Diagnostic Tools</h1>
<p class="sub">Pick a tool to launch it on this machine. Each opens in a new tab.</p></header>
<div id="grid" class="grid"></div>
<p class="note" id="note"></p>
<script>
async function load(){
  const r = await fetch('./api/tools'); const tools = await r.json();
  const g = document.getElementById('grid'); g.innerHTML='';
  let anyInstalled=false;
  for(const t of tools){
    if(t.installed) anyInstalled=true;
    const c=document.createElement('div'); c.className='card';
    c.innerHTML = `<div class="name">${t.label}</div>
      <div class="blurb">${t.blurb||''}</div>
      <div class="row">
        <span class="pill ${t.running?'on':''}">${t.running?'running':(t.installed?'installed':'not installed')}</span>
        <button ${t.installed?'':'disabled'} data-tool="${t.name}" class="${t.running?'open':''}">
          ${t.running?'Open':'Launch'}</button>
      </div><div class="err" id="err-${t.name}"></div>`;
    const b=c.querySelector('button');
    b.onclick=()=>act(t.name,b);
    g.appendChild(c);
  }
  document.getElementById('note').innerHTML = anyInstalled ? '' :
    'No tools are built yet. Install one first, e.g. <code>bin/bdtools install mlst_gui</code>.';
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
load(); setInterval(load, 5000);
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
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        parsed = urlparse(self.path)
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

    port = args.port
    if port is None:
        port = 8080 if not port_open(args.host, 8080) else free_port()

    SUITE = Suite()
    httpd = ThreadingHTTPServer((args.host, port), Handler)
    url = f"http://{args.host}:{port}/"
    print(f"Kapur Lab dashboard: {url}  (Ctrl-C to stop)")
    n_installed = sum(1 for t in SUITE.tools if t["installed"])
    print(f"  {n_installed}/{len(SUITE.tools)} tools installed and ready to launch.")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1), _open(url)), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping dashboard.")


def _open(url):
    for cmd in (["xdg-open", url], ["open", url], ["wslview", url]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue


if __name__ == "__main__":
    main()
