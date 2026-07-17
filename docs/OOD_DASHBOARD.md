# Consolidated OOD dashboard (one session, one allocation, authenticated)

This is the recommended way to deploy the suite on Open OnDemand. It replaces
"one OOD app (and one Slurm job) per tool" with a **single dashboard app** that
allocates a node once and runs every tool on it, behind one authenticated
reverse proxy.

## What it fixes

1. **Session confinement (security).** Previously each tool's `uvicorn` bound
   `0.0.0.0:$port` on the compute node with no app-level auth, reachable by any
   authenticated OOD user who knew the host+port via `/rnode`. Now:
   - the dashboard is the **only** process `/rnode` reaches;
   - each tool binds **`127.0.0.1`** on the node (unreachable by other users/nodes);
   - the dashboard enforces auth once:
     - a **per-session token** — OOD's generated `$password`, handed to the
       browser one time as `?t=…` and stored as an HttpOnly cookie; and
     - an **OOD-username match** — `X-Forwarded-User` (set and overwritten by
       `mod_ood_proxy`, so unspoofable) must equal the session owner. If the
       header is absent the token still applies; set
       `BDTOOLS_STRICT_USER_HEADER=1` to hard-require it.

2. **Resource consolidation.** The dashboard's OOD card requests the node once
   (cores / memory / partition / hours). Every tool opened in that session shares
   the allocation instead of spawning its own Slurm job.

## How it works

- OOD card: `ood/apps/bdtools_dashboard/` (umbrella-owned). Its `script.sh.erb`
  starts `bin/ood_dashboard/app.py` (Starlette + httpx) on `0.0.0.0:$port`.
- The dashboard lists installed tools, and on click launches each via
  `bin/lib/tool_launch.py` — the single source of truth that reproduces each
  tool's env (shared `<dir>/env`, `PYTHONPATH`, vsnp's sibling `vsnp3` env, ksnp's
  vendored bin, amr's `CONDA_PREFIX`) but always binds `127.0.0.1`.
- It reverse-proxies `/t/<tool>/…` to that loopback port, streaming SSE
  (`text/event-stream`) unbuffered and passing HTTP Range/206 through (vSNP/IGV).
  Tool frontends already use relative URLs and already run under a sub-path
  (`/rnode/…`), so serving them under `/t/<tool>/` works the same way.

## Also powers local mode (single-port `bdtools dashboard`)

The same `bin/ood_dashboard/app.py` is what `bdtools dashboard` now runs locally
(with `BDTOOLS_LOCAL=1`), bound to `127.0.0.1` instead of `0.0.0.0`. This gives
laptop / WSL / SSH users the same one-port model: every tool is served under
`/t/<tool>/`, so working over SSH needs only a single forward
(`ssh -L 8080:127.0.0.1:8080 <host>`) instead of chasing a random port per tool.

Differences from OOD:
- **Auth.** No `mod_ood_proxy`, so there's no `X-Forwarded-User`. The launcher
  auto-mints a per-session token on shared multi-user hosts (any non-macOS,
  non-WSL machine) and prints a `?t=…` URL; personal Mac/WSL run tokenless
  (loopback is single-user). Override with `BDTOOLS_DASHBOARD_AUTH=1|0`.
- **Extra UI.** Local mode adds readiness badges and a self-update panel
  (`/api/updates`, `/api/apply-updates`, `/api/recheck`) — hidden under OOD,
  where users can't update a shared install.
- **Fallback.** If a python with `starlette`+`httpx`+`uvicorn` isn't found,
  `bdtools dashboard` falls back to the legacy `bin/dashboard.py` (each tool on
  its own port). Installing any tool provides the deps and enables proxy mode.

Shared logic (display tables, readiness, update check/apply) lives in
`bin/lib/suite_common.py`, imported by both dashboards.

## Install (sysadmin)

Build each tool's environment **without** its per-tool card, then install the
one dashboard card:

```bash
cp sites/site.conf.example sites/site.conf   # edit CLUSTER_NAME, TOOLS_ROOT, ...
# 1) build tool envs only (no per-tool OOD cards)
for t in $(bin/bdtools list | awk 'NR>1 && $1!~/:/{print $1}'); do
  sudo bin/bdtools install "$t" --server --no-card --site-conf sites/site.conf
done
# 2) install the single consolidated dashboard card
sudo bin/bdtools install --server --dashboard --site-conf sites/site.conf
```

The umbrella must be checked out at `TOOLS_ROOT/bioinformatic_diagnostic_tools`
(the card's `script.sh.erb` resolves it there, or `$HOME/…` for a sandbox user).

Per-tool cards are still available for a dedicated single-tool allocation — just
omit `--no-card` for that tool. They are no longer needed for routine use.

## Verify

`bin/bdtools install --server --dashboard --dry-run --site-conf …` prints the
render plan. After a real install, launch **"Diagnostic Tools Dashboard"** in OOD
and confirm the landing page loads through `/rnode` and a tool opens under
`/t/<tool>/`. A quick spoof check: `curl -H 'X-Forwarded-User: someone-else'`
against the session should get 403.

## Requirement for tool frontends

The sub-path proxy requires **relative** asset URLs (Vite `base: './'`). This is
already true for all suite tools and is enforced by `bin/bdtools lint`, which
fails if a `frontend/dist/index.html` uses root-absolute (`src="/…"`) asset URLs.
