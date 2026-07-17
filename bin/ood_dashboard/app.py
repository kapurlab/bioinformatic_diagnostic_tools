#!/usr/bin/env python3
"""ood_dashboard — the authenticated reverse-proxy entry point for the tool suite.

Serves the whole suite behind ONE port. It:
  * lists the installed GUIs and launches each on demand as a uvicorn bound to
    127.0.0.1 (via bin/lib/tool_launch.py) — so no other host/user reaches a
    tool directly;
  * reverse-proxies each tool under /t/<tool>/ (this process is the only thing
    the outside reaches);
  * enforces authentication ONCE here, so the tools need no auth code:
      - a per-session random token, presented as a one-time ?t=... link and
        converted to an HttpOnly cookie; and
      - an OOD-username check: X-Forwarded-User (injected + overwritten by
        mod_ood_proxy, so unspoofable) must equal the session owner.

Two deployments share this one app:
  * OOD — one batch_connect app runs it on a full compute node (bound 0.0.0.0,
    reached only via OOD's /rnode proxy); the node is allocated ONCE and every
    tool shares that allocation instead of a job per tool.
  * Local (`bdtools dashboard`, BDTOOLS_LOCAL=1) — bound to 127.0.0.1 on a
    laptop/WSL/SSH box; a single forwarded port serves every tool. Local mode
    additionally exposes readiness badges + a self-update UI (see landing()).

Config (environment):
  BDTOOLS_LOCAL                 "1" => local mode (rich landing + update routes)
  BDTOOLS_SESSION_TOKEN[_FILE]  the per-session secret (OOD $password, or minted
                                by `bdtools dashboard` on a shared host)
  BDTOOLS_SESSION_OWNER         the launching user ($USER); enforced vs X-Forwarded-User
  BDTOOLS_STRICT_USER_HEADER    "1" => require the header to be present (403 if absent)
  BDTOOLS_TOOLSDIR / BDTOOLS_MANIFEST / BDTOOLS_HOME  passed through to tool_launch

Run:  uvicorn app:app --host <0.0.0.0|127.0.0.1> --port $port   (from bin/ood_dashboard/)
Deps: starlette, httpx, uvicorn — all present in the tool conda envs.
"""
import asyncio
import functools
import html
import os
import socket
import sys
import time

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                                 RedirectResponse, Response, StreamingResponse)
from starlette.routing import Route

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "lib"))
import tool_launch  # noqa: E402
import manifest  # noqa: E402
import suite_common as sc  # noqa: E402  (shared, stdlib-only helpers)

# Local mode (bdtools dashboard on a laptop/WSL/SSH box) enables the readiness
# badges and the self-update UI. Under OOD this stays off: users can't update a
# shared install, and readiness is the admin's concern, not per-session.
LOCAL = os.environ.get("BDTOOLS_LOCAL", "").strip() in ("1", "true", "yes")

_REPO_DIR = os.path.dirname(os.path.dirname(_HERE))
_MANIFEST = os.environ.get("BDTOOLS_MANIFEST", os.path.join(_REPO_DIR, "tools.yml"))

# Hop-by-hop headers must not be forwarded (RFC 7230 §6.1).
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailer", "transfer-encoding", "upgrade"}

# Display tables + the update/readiness helpers live in suite_common so the
# legacy stdlib dashboard and this proxy dashboard stay in lock-step.
PRETTY, BLURB, CAVEAT, pretty = sc.PRETTY, sc.BLURB, sc.CAVEAT, sc.pretty
UPDATES = sc.UpdateManager()


# ----- config -----
def _load_token():
    tok = os.environ.get("BDTOOLS_SESSION_TOKEN", "").strip()
    if tok:
        return tok
    path = os.environ.get("BDTOOLS_SESSION_TOKEN_FILE", "").strip()
    if path:
        try:
            return open(path).read().strip()
        except OSError:
            return ""
    return ""


TOKEN = _load_token()
OWNER = os.environ.get("BDTOOLS_SESSION_OWNER", "").strip()
STRICT_USER = os.environ.get("BDTOOLS_STRICT_USER_HEADER", "").strip() in ("1", "true", "yes")
COOKIE = "bdtools_session"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def _port_open(port):
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.close()
        return True
    except OSError:
        return False


class Suite:
    """Installed tools + the tool servers this dashboard has launched (loopback)."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.running = {}  # tool -> {"port", "proc"}
        self.tools = self._discover()
        self.readiness = {}  # name -> doctor record; filled lazily in local mode

    async def refresh_readiness(self):
        """Populate the readiness map (local mode only). `bdtools doctor --json`
        is slow and network-touching, so run it off the event loop."""
        if not LOCAL:
            return
        loop = asyncio.get_event_loop()
        self.readiness = await loop.run_in_executor(None, sc.readiness_map)

    def _discover(self):
        out = []
        _, tools = manifest.parse(_MANIFEST)
        for t in tools:
            name = t.get("name")
            if not name:
                continue
            try:
                tool_launch.resolve(name, 0)  # cheap: checks backend dir + a python
                installed = True
            except Exception:
                installed = False
            out.append({"name": name, "label": pretty(name),
                        "blurb": BLURB.get(name, ""), "caveat": CAVEAT.get(name, ""),
                        "installed": installed})
        return out

    async def launch(self, name):
        async with self.lock:
            cur = self.running.get(name)
            if cur and cur["proc"].returncode is None and await _port_open(cur["port"]):
                return f"t/{name}/", None
            try:
                plan = tool_launch.resolve(name, 0)
            except Exception as exc:
                return None, str(exc)
            port = _free_port()
            plan = tool_launch.resolve(name, port)
            logdir = os.path.join(os.environ.get("BDTOOLS_HOME",
                     os.path.expanduser("~/.local/share/bdtools")), "dashboard-logs")
            os.makedirs(logdir, exist_ok=True)
            logf = open(os.path.join(logdir, f"{name}.log"), "ab")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *plan["argv"], cwd=plan["cwd"], env=plan["env"],
                    stdout=logf, stderr=asyncio.subprocess.STDOUT)
            except OSError as exc:
                return None, str(exc)
            self.running[name] = {"port": port, "proc": proc}
        # wait (outside the lock) for the tool's uvicorn to come up
        for _ in range(120):  # ~60s
            if proc.returncode is not None:
                return None, f"the tool exited early — see {logf.name}"
            if await _port_open(port):
                return f"t/{name}/", None
            await asyncio.sleep(0.5)
        return None, "timed out waiting for the tool to start (first launch may still be building)"

    def port_of(self, name):
        cur = self.running.get(name)
        if cur and cur["proc"].returncode is None:
            return cur["port"]
        return None

    async def state(self):
        out = []
        for t in self.tools:
            running = self.port_of(t["name"]) is not None
            r = self.readiness.get(t["name"]) if t.get("installed") else None
            out.append(dict(
                t,
                running=running,
                url=(f"t/{t['name']}/" if running else None),
                # ready is None when unknown (doctor unavailable / not local); the
                # UI only badges an explicit False.
                ready=(r["ok"] if r else None),
                issues=(r.get("issues", []) if r else []),
                notes=(r.get("notes", []) if r else []),
            ))
        return out

    async def shutdown(self):
        for name, v in list(self.running.items()):
            p = v["proc"]
            if p.returncode is None:
                try:
                    p.terminate()
                except ProcessLookupError:
                    pass


SUITE = None
CLIENT = None


# ----- auth -----
class AuthMiddleware(BaseHTTPMiddleware):
    """Token cookie bootstrap + unspoofable X-Forwarded-User match, enforced once."""

    async def dispatch(self, request, call_next):
        # 1) token bootstrap: ?t=<token> on any GET -> set cookie, strip the param.
        if TOKEN:
            qt = request.query_params.get("t")
            if qt is not None:
                if qt != TOKEN:
                    return PlainTextResponse("forbidden", status_code=403)
                clean = request.url.remove_query_params("t")
                resp = RedirectResponse(str(clean), status_code=303)
                resp.set_cookie(COOKIE, TOKEN, httponly=True, samesite="lax", path="/")
                return resp
            if request.cookies.get(COOKIE) != TOKEN:
                msg = ("This dashboard is private. Open the http://…/?t=… link printed "
                       "in the terminal where you started it."
                       if LOCAL else
                       "This session is private. Open it from your own OnDemand session card.")
                return PlainTextResponse(msg, status_code=403)
        # 2) OOD username match. mod_ood_proxy overwrites X-Forwarded-User with the
        #    authenticated portal user, so a client cannot forge it.
        xfu = request.headers.get("x-forwarded-user", "").strip()
        if xfu:
            if OWNER and xfu != OWNER:
                return PlainTextResponse("forbidden (user mismatch)", status_code=403)
        elif STRICT_USER:
            return PlainTextResponse("forbidden (missing user header)", status_code=403)
        return await call_next(request)


# ----- landing + control plane -----
# The OOD landing: a lean grid (no self-update UI — users can't update a shared
# install). Local mode serves the full-featured page from the legacy dashboard
# (readiness badges + self-update), reused verbatim so there's one template.
SIMPLE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kapur Lab Diagnostic Tools</title><style>
 :root{{--bg:#f6f3ee;--card:#fff;--ink:#2c2a26;--muted:#7c756a;--accent:#a8553a;--accent2:#6b8f71;--line:#e6ded2}}
 *{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}}
 header{{padding:28px 24px 8px}}h1{{margin:0;font-size:22px}}p.sub{{margin:4px 0 0;color:var(--muted)}}
 .who{{color:var(--muted);font-size:13px;padding:2px 24px 0}}
 .grid{{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));padding:20px 24px 40px}}
 .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:8px}}
 .name{{font-weight:650;font-size:16px}}.blurb{{color:var(--muted);font-size:13px;min-height:34px}}
 .row{{display:flex;align-items:center;justify-content:space-between;margin-top:4px}}
 button{{font:inherit;border:0;border-radius:8px;padding:8px 14px;cursor:pointer;background:var(--accent);color:#fff;font-weight:600}}
 button:disabled{{background:#cfc7ba;cursor:not-allowed}}button.open{{background:var(--accent2)}}
 .pill{{font-size:12px;padding:2px 9px;border-radius:999px;background:#efe9df;color:var(--muted)}}
 .pill.on{{background:#e2efe4;color:#3f6b48}}.err{{color:#b23b2e;font-size:12px;min-height:14px}}
 .dev{{background:#fbecec;border:1px solid #ecc9c4;border-radius:8px;padding:8px 10px;font-size:12px;color:#8a3324}}
</style></head><body>
<header><h1>Kapur Lab Diagnostic Tools</h1>
<p class="sub">One session, one allocation. Pick a tool to launch it on this node.</p></header>
<p class="who">{who}</p>
<div id="grid" class="grid"></div>
<script>
async function load(){{
 const r=await fetch('./api/tools');const tools=await r.json();
 const g=document.getElementById('grid');g.innerHTML='';
 for(const t of tools){{
  const c=document.createElement('div');c.className='card';
  const pill=t.running?'<span class="pill on">running</span>':'<span class="pill">'+(t.installed?'installed':'not installed')+'</span>';
  c.innerHTML='<div class="name">'+t.label+'</div><div class="blurb">'+(t.blurb||'')+'</div>'+
   (t.caveat?'<div class="dev"><b>⚠ Development status:</b> '+t.caveat+'</div>':'')+
   '<div class="row">'+pill+'<button '+(t.installed?'':'disabled')+' class="'+(t.running?'open':'')+'">'+(t.running?'Open':'Launch')+'</button></div>'+
   '<div class="err" id="err-'+t.name+'"></div>';
  const b=c.querySelector('button');b.onclick=()=>act(t.name,b);g.appendChild(c);
 }}
}}
async function act(name,btn){{
 const err=document.getElementById('err-'+name);err.textContent='';
 btn.disabled=true;const was=btn.textContent;btn.textContent='Starting…';
 try{{const r=await fetch('./api/launch?tool='+encodeURIComponent(name),{{method:'POST'}});
  const j=await r.json();
  if(j.url){{window.open(j.url,'_blank');}}else{{err.textContent=j.error||'failed to launch';}}
 }}catch(e){{err.textContent=String(e);}}
 btn.disabled=false;btn.textContent=was;load();
}}
load();setInterval(load,5000);
</script></body></html>"""


@functools.lru_cache(maxsize=1)
def _rich_page():
    # The full local landing page (readiness + self-update UI) is owned by the
    # legacy dashboard module; import lazily so the OOD path never touches it.
    sys.path.insert(0, os.path.dirname(_HERE))  # bin/
    import dashboard  # noqa: E402
    return dashboard.PAGE


async def landing(request):
    if LOCAL:
        return HTMLResponse(_rich_page())
    who = f"Signed in as {html.escape(OWNER)}." if OWNER else ""
    return HTMLResponse(SIMPLE_PAGE.format(who=who))


async def api_tools(request):
    return JSONResponse(await SUITE.state())


async def api_launch(request):
    tool = request.query_params.get("tool", "")
    if tool not in {t["name"] for t in SUITE.tools}:
        return JSONResponse({"error": "unknown tool"}, status_code=400)
    url, err = await SUITE.launch(tool)
    if url:
        return JSONResponse({"url": url})
    return JSONResponse({"error": err or "launch failed"}, status_code=500)


# ----- updates + readiness (local mode only) -----
def _valid_targets():
    return {"all", "bdtools"} | {t["name"] for t in SUITE.tools}


async def api_updates(request):
    # Non-blocking: kick off the (slow) check on first ask, return what we have.
    UPDATES.check_async()
    return JSONResponse(UPDATES.state())


async def api_check_updates(request):
    UPDATES.check_async(force=True)
    return JSONResponse(UPDATES.state())


async def api_apply_updates(request):
    target = request.query_params.get("target", "all")
    started, err = UPDATES.apply(target, _valid_targets())
    return JSONResponse({"started": started, "error": err}, status_code=200 if started else 409)


async def api_update_status(request):
    return JSONResponse(UPDATES.job_status())


async def api_recheck(request):
    # Re-run readiness after the user installs a database / fixes a dep.
    await SUITE.refresh_readiness()
    return JSONResponse(await SUITE.state())


# ----- reverse proxy -----
async def proxy_noslash(request):
    # /t/<tool> -> /t/<tool>/  (trailing slash: relative ./assets and ./api resolve here)
    return RedirectResponse(f"/t/{request.path_params['tool']}/", status_code=307)


async def proxy(request):
    tool = request.path_params["tool"]
    sub = request.path_params["path"]
    port = SUITE.port_of(tool)
    if port is None:
        return PlainTextResponse(f"{tool} is not running — launch it from the dashboard.",
                                 status_code=502)
    url = f"http://127.0.0.1:{port}/{sub}"
    if request.url.query:
        url += "?" + request.url.query

    # request headers: drop hop-by-hop, host, content-length (httpx re-derives)
    req_headers = [(k, v) for k, v in request.headers.items()
                   if k.lower() not in HOP_BY_HOP and k.lower() not in ("host", "content-length")]
    # SSE / EventSource: no read timeout (streams idle between events)
    is_sse = "text/event-stream" in request.headers.get("accept", "")
    timeout = httpx.Timeout(10.0, read=None) if is_sse else httpx.Timeout(10.0, read=300.0)

    upstream = CLIENT.build_request(request.method, url, headers=req_headers,
                                    content=request.stream(), timeout=timeout)
    resp = await CLIENT.send(upstream, stream=True)

    prefix = f"/t/{tool}"
    out_headers = {}
    set_cookies = []
    for k, v in resp.headers.multi_items():
        lk = k.lower()
        if lk in HOP_BY_HOP:
            continue
        if lk == "set-cookie":
            # re-scope cookies to this tool's sub-path so tools don't collide
            v = v.replace("Path=/;", f"Path={prefix}/;").replace("path=/;", f"Path={prefix}/;")
            if v.rstrip().endswith("Path=/") or v.rstrip().endswith("path=/"):
                v = v.rstrip()[:-1] + f"{prefix}/"
            set_cookies.append(v)
            continue
        if lk == "location" and v.startswith("/"):
            v = prefix + v  # keep absolute redirects inside the sub-path
        out_headers[k] = v

    response = StreamingResponse(resp.aiter_raw(), status_code=resp.status_code,
                                 headers=out_headers,
                                 background=BackgroundTask(resp.aclose))
    for c in set_cookies:
        response.raw_headers.append((b"set-cookie", c.encode("latin-1")))
    return response


def build_app():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        global SUITE, CLIENT
        SUITE = Suite()
        CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0), follow_redirects=False)
        if LOCAL:
            # Warm both in the background so the page is usable immediately.
            asyncio.create_task(SUITE.refresh_readiness())
            UPDATES.check_async()
        try:
            yield
        finally:
            await SUITE.shutdown()
            await CLIENT.aclose()

    routes = [
        Route("/", landing),
        Route("/api/tools", api_tools),
        Route("/api/launch", api_launch, methods=["POST"]),
    ]
    if LOCAL:
        # Self-update + readiness control plane — local only (see landing()).
        routes += [
            Route("/api/updates", api_updates),
            Route("/api/check-updates", api_check_updates, methods=["POST"]),
            Route("/api/apply-updates", api_apply_updates, methods=["POST"]),
            Route("/api/update-status", api_update_status),
            Route("/api/recheck", api_recheck, methods=["POST"]),
        ]
    routes += [
        Route("/t/{tool}", proxy_noslash),
        Route("/t/{tool}/{path:path}", proxy,
              methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
    ]
    return Starlette(routes=routes, middleware=[Middleware(AuthMiddleware)], lifespan=lifespan)


app = build_app()
