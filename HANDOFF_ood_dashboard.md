# HANDOFF — consolidated + authenticated OOD dashboard

Pick-up notes for the `ood-consolidated-dashboard` branch. Last updated 2026-07-09.

- **Branch:** `ood-consolidated-dashboard` (commit `11e8919`), **not merged to main**.
- **Why:** two requirements from a July 2026 OOD-admin meeting (below).
- **Status:** implemented + validated off-cluster on wgs3 (Linux). Remaining work is a
  real on-a-Slurm-node end-to-end test, then merge.
- **Full user-facing writeup:** [`docs/OOD_DASHBOARD.md`](docs/OOD_DASHBOARD.md).

## The two goals

1. **Session confinement (security).** Old model: every tool's `uvicorn` bound
   `0.0.0.0:$port` on the compute node with *no* app-level auth, so any authenticated
   OOD user who knew the host+port could open another user's session through `/rnode`.
2. **Resource consolidation.** Old model: one OOD app per tool ⇒ one Slurm job per tool
   opened. Admins wanted the dashboard to allocate a node **once** and run all tools on it.

## The design that shipped (approach B)

A single OOD `batch_connect` app — the dashboard — is the only process `/rnode` reaches.
- Each tool is launched on **`127.0.0.1:<port>`** on the same node (unreachable by other
  users/nodes). The dashboard reverse-proxies `/t/<tool>/` to it.
- Auth is enforced **once**, at the dashboard, so the 8 tool repos need no auth code:
  - **token** — OOD's generated `$password`, delivered as a one-time `?t=…` link that
    the dashboard converts to an HttpOnly `SameSite=Lax` cookie (then strips the param);
  - **username** — `X-Forwarded-User` must equal the session owner. `mod_ood_proxy`
    sets *and overwrites* this header on every `/rnode` request, so it can't be forged
    (verified: `/opt/ood/mod_ood_proxy/lib/ood/proxy.lua:26`, OOD 3.1.16). **No OOD-admin
    config change is needed for this.** Absent header ⇒ token still applies; set
    `BDTOOLS_STRICT_USER_HEADER=1` to hard-require it.
- Sub-path proxying is safe because the tool frontends already use **relative** URLs
  (`./api`, `./assets`, relative `EventSource`) and already run under a sub-path today
  (`/rnode/<host>/<port>/`). Adding `/t/<tool>/` is the same class of routing.

## Files (all on the branch)

New:
- `bin/lib/tool_launch.py` — the single launch resolver. Given a tool + port, reproduces
  exactly what that tool's `ood/apps/<tool>/template/script.sh.erb` does (shared
  `<dir>/env`, `PYTHONPATH=<dir>/bin`, vSNP's sibling `vsnp3` env + no PYTHONPATH, kSNP's
  `vendor/kSNP4-bin` on PATH, AMR's `CONDA_PREFIX`) but **always binds 127.0.0.1**.
  Per-tool deltas live in the small `SPEC` table; everything else uses `DEFAULTS`.
  CLI: `python3 bin/lib/tool_launch.py show <tool> <port>`.
- `bin/ood_dashboard/app.py` — Starlette + httpx ASGI app. Routes: `/` (landing),
  `/api/tools`, `/api/launch`, `/t/{tool}` (307→slash), `/t/{tool}/{path}` (proxy).
  Proxy streams SSE unbuffered (read `timeout=None` when `Accept: text/event-stream`),
  passes Range/206 through, rewrites `Location: /…`→`/t/<tool>/…` and `Set-Cookie`
  `Path=/`→`Path=/t/<tool>/`, strips hop-by-hop headers. `AuthMiddleware` does the token
  + username checks. Config via env (see its docstring).
- `ood/apps/bdtools_dashboard/` — the OOD card: `manifest.yml`, `form.yml` (full-node:
  partition/account/cores/mem/hours), `submit.yml.erb`, `template/before.sh` (allocates
  `$port`), `template/script.sh.erb` (finds a python with starlette+httpx+uvicorn among the
  tool envs; exports `BDTOOLS_SESSION_TOKEN='<%= password %>'` + `BDTOOLS_SESSION_OWNER=$USER`;
  execs uvicorn on `0.0.0.0:$port`), `view.html.erb` (button → `/rnode/…/?t=<%= password %>`).
- `docs/OOD_DASHBOARD.md`.

Changed:
- `bin/install-server.sh` — `--dashboard` (render the umbrella card into `SYS_APPS_DIR`
  via the existing `subst()` loop; no checkout/toolchain) and `--no-card` (build a tool's
  env, skip its per-tool card). Dashboard-aware `phase_verify`.
- `bin/bdtools` — `cmd_install` passes `--no-card`; `--dashboard` short-circuits to
  `install-server.sh --dashboard` (server-only).
- `bin/lint.sh` — `check_frontend_base` fails a tool whose `frontend/dist/index.html` uses
  root-absolute (`src="/…"`) asset URLs (the sub-path proxy needs relative Vite `base:'./'`).
- `docs/SYSADMIN.md`, `docs/BUILDING_A_TOOL.md`, `HANDOFF.md`.

**Deliberately unchanged (local Mac/WSL/Linux path):** `bin/dashboard.py`,
`bin/install-local.sh`, `bin/lib/common.sh`, `tools.yml`, `Open Dashboard.command`.
Personal installs behave exactly as before.

## Install flow (sysadmin, on the OOD cluster)

```bash
cp sites/site.conf.example sites/site.conf     # set CLUSTER_NAME, TOOLS_ROOT, groups, ...
# 1) build tool envs only (no per-tool cards)
for t in $(bin/bdtools list | awk 'NR>1 && $1!~/:/{print $1}'); do
  sudo bin/bdtools install "$t" --server --no-card --site-conf sites/site.conf
done
# 2) install the single consolidated dashboard card
sudo bin/bdtools install --server --dashboard --site-conf sites/site.conf
```
The umbrella must be checked out at `TOOLS_ROOT/bioinformatic_diagnostic_tools`
(the card's `script.sh.erb` resolves it there; `$HOME/…` fallback for a sandbox user).
Per-tool cards remain available for a dedicated single-tool node — omit `--no-card`.

## What was verified off-cluster (wgs3, Linux) — and how to repeat it

Ran the dashboard under a tool env python with `BDTOOLS_TOOLSDIR=/srv/kapurlab/tools`,
`BDTOOLS_SESSION_TOKEN=…`, `BDTOOLS_SESSION_OWNER=$(whoami)`:
- Resolver correct for all 8 tools (`tool_launch.py show <tool> <port>`), incl. vSNP
  sibling env, kSNP vendor PATH, AMR `CONDA_PREFIX`.
- Proxy served **mlst_gui** and **vsnp_gui** (the outlier): index + `./assets` + relative
  `./api/*` all 200.
- **SSE** arrives incrementally (~0.5s cadence over `curl -N`) — not buffered. NB: httpx
  `ASGITransport` in-process *does* buffer, so test SSE over a real socket, not ASGITransport.
- **Range** `bytes=8-15` → 206 with intact `Content-Range`, exactly 8 bytes.
- `Location: /landed` → `/t/fake/landed`; `Set-Cookie Path=/` → `Path=/t/fake/`.
- Auth: no token → 403; `?t=<token>` → 303 + cookie; cookie → 200;
  `X-Forwarded-User: someone-else` → 403 on both control and proxied paths.
- Card renders portably: real `subst()` for a synthetic `nivedi` site rewrote
  `/srv/kapurlab/tools`→`/opt/nivedi/tools` and cluster `wgs3`→`roar`; `<%= password %>`
  ERB preserved.
- Local `bin/dashboard.py` still serves 200; `install --local --dry-run` unaffected.

## Remaining before merge

1. **Real on-node E2E** (needs the Slurm cluster): launch "Diagnostic Tools Dashboard" in
   OOD; confirm the landing page loads through `/rnode`, a tool opens under `/t/<tool>/`,
   a live job log streams, and IGV seeking works. Spoof check: `curl -H 'X-Forwarded-User:
   other'` → 403.
2. **Process lifecycle on-node:** confirm tool uvicorns launched by the dashboard die when
   the Slurm job ends. The dashboard has an ASGI shutdown handler that `terminate()`s them,
   and Slurm's cgroup kill covers the tree, but verify (OOD basic template's `clean_up` runs
   `pkill -P $$`, which only reaches direct children).
3. **Resource sizing:** one full-node allocation now hosts N tool uvicorns + their pipelines
   (raxml/spades/kraken). Confirm the form defaults (16 cores / 64 GB) don't oversubscribe;
   document that a very heavy run can still use a dedicated single-tool card.
4. **Sandbox (`--sandbox`) dashboard card:** not done. The server path is complete; the
   sandbox link-in was deferred because the card's `cluster:` is a literal that a sandbox
   user would need to edit per site (no `subst()` runs in sandbox mode). `tool_launch.py`
   already handles the sandbox layout (`~/.config/<tool>/sandbox.env`), so this is mostly a
   packaging step in `bin/install-sandbox.sh`.

## Gotchas / notes

- `bin/dashboard.py` (local) runs `bdtools doctor --json` at startup, so it takes several
  seconds to begin listening — not a hang.
- The dashboard needs a python with `starlette`+`httpx`+`uvicorn`; every tool conda env has
  them. `script.sh.erb` picks the first that imports all three. If a node has *no* tool env
  built yet, it errors clearly.
- Don't `pkill -f 'uvicorn app:app'` on a shared node — it self-matches your own shell and
  can match unrelated sessions. Kill by port (`fuser -k <port>/tcp`) instead.
