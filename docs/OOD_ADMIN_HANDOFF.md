# bdtools as an OOD app — handout for the OOD admin team

One-page brief for the sysadmins who will register the app. Companion docs:
[OOD_DASHBOARD.md](OOD_DASHBOARD.md) (design), [SYSADMIN.md](SYSADMIN.md)
(install mechanics), [INSTALL_HPC_OOD.md](INSTALL_HPC_OOD.md) (no-admin sandbox
path).

## What we're asking you to add

**One** app: `bdtools_dashboard` — "Kapur Laboratory bioinformatic diagnostic
tools". It fronts nine bacterial/viral WGS diagnostic GUIs (vSNP, AMRFinderPlus,
IRMA, GenoFlu, MLST, Kraken2, kSNP4, NCBI submission, bovine MHC typing).

Not nine apps. The suite *can* install one card per tool, and that is how it runs
on our lab server today, but that model spawns one scheduler job per tool a user
opens. The consolidated dashboard was built specifically to replace it: it
allocates a node **once per session** and runs every tool the user opens on that
same allocation, behind **one** authenticated reverse proxy. Please install only
the dashboard card; leave the per-tool cards out.

Source: `ood/apps/bdtools_dashboard/` in
`https://github.com/kapurlab/bioinformatic_diagnostic_tools`.

## It's `batch_connect`, not Passenger

The OSC tutorial that usually gets circulated
([tutorials-passenger-apps](https://osc.github.io/ood-documentation/latest/tutorials/tutorials-passenger-apps.html))
covers apps that run **on the OOD web node** under Phusion Passenger. This is not
that. `manifest.yml` declares `role: batch_connect`: the session script is
submitted to the scheduler, `uvicorn` starts **on the compute node**, and OOD
proxies to it through `/rnode/<host>/<port>/`.

That distinction is load-bearing — these are 16-core, 64 GB, multi-hour
bioinformatics pipelines (read alignment, tree building, Kraken2 against a 315 GB
index). They must not run on the web node.

What *does* carry over from the Passenger tutorial: where files live
(`/var/www/ood/apps/sys/<app>`), how an app appears in the dashboard, and the
`~/ondemand/dev/` sandbox mechanism for testing before promotion. Those are
role-independent.

## What we need from you

1. **The cluster id** — `form.yml` has `cluster: "wgs3"` (our lab box). It must
   become your `/etc/ood/config/clusters.d/<id>.yml` name. The installer rewrites
   it from `CLUSTER_NAME` in `sites/site.conf`; or change the one line by hand.
2. **Partition / account policy.** The form currently exposes Slurm partition and
   account as free-text fields, defaulting to empty. If your site expects
   specific partitions or requires `-A`, tell us the values — we would rather
   ship a `select` with your real partitions than have users guess. Defaults we
   picked: 16 cores, 64 GB, 8 hours (max 48).
3. **Register the card**, the ordinary way:
   ```bash
   sudo bin/bdtools install --server --dashboard --site-conf sites/site.conf --dry-run
   sudo bin/bdtools install --server --dashboard --site-conf sites/site.conf
   ```
   `--dry-run` prints every file it would write. Or just copy the four rendered
   files into `/var/www/ood/apps/sys/bdtools_dashboard/` yourself — it is a
   normal card (`manifest.yml`, `form.yml`, `submit.yml.erb`, `view.html.erb`,
   `template/{before.sh,script.sh.erb}`).
4. **Storage** — see below. This is the biggest ask, and it isn't an OOD ask.

Explicitly **not** asked for: no Apache/nginx changes, no PAM or auth changes, no
dashboard rebranding, no new Unix groups, no `clusters.d` edits, no setuid, no
daemon on the web node. Nothing runs as root; everything runs as the invoking
user.

## Security posture (the questions admins actually ask)

Both items below came out of our July 2026 review with OOD admins and are
implemented, not planned.

- **Nothing but the dashboard is reachable.** Each tool's `uvicorn` binds
  `127.0.0.1` on the compute node, so it is unreachable from other nodes or by
  other users even if they guess host+port. Only the dashboard binds the
  OOD-proxied `$port`.
- **Two-factor session confinement, enforced once, at the dashboard:**
  - the per-session secret from the `batch_connect` `basic` template
    (`$password`), handed to the browser one time as `?t=…` then held as an
    HttpOnly cookie; **and**
  - `X-Forwarded-User` must equal the session owner. `mod_ood_proxy` sets and
    overwrites that header (`/opt/ood/mod_ood_proxy/lib/ood/proxy.lua:26` in OOD
    3.1.16), so it is not spoofable. Absent header → token still applies; set
    `BDTOOLS_STRICT_USER_HEADER=1` to hard-require it.
  - Spoof check: `curl -H 'X-Forwarded-User: someone-else' …` → 403.
- **Proxy requirements:** Server-Sent Events (`text/event-stream`) must stream
  unbuffered, and HTTP `Range`/`206` must pass through (vSNP serves BAM/BAI to an
  embedded IGV). **No WebSockets anywhere.**
- Frontend assets use relative URLs (Vite `base: './'`), which is what makes
  sub-path proxying work; `bdtools lint` fails the build if that regresses.

## Resources and storage

| item | size |
|---|---|
| tool conda envs (8 built) | ~9 GB |
| conda base (miniforge) | ~14 GB |
| reference databases | **~345 GB** |

The databases dominate and are the real planning item: Kraken2 standard index
315 GB, BLAST 25 GB, bovine MHC 4 GB, AMRFinderPlus 240 MB. They are **not**
bundled and **not** auto-staged — we stage them, but we need a shared,
group-readable path and quota for them, plus a projects/output area. Both paths
are configuration (`DATABASES_ROOT`, `SHARED_PROJECTS_ROOT` in `site.conf`); no
absolute site path is compiled into tool code.

Build-time prerequisites (ours to satisfy, on a login/build node): conda or
mamba, and Node ≥ 20.19 for the frontend build — otherwise the prebuilt `dist`
that ships in each tool repo is used instead.

## Known gaps — please read before we agree on a date

- **The Slurm submission path has never been exercised.** Our production server
  runs OOD 3.1.16 with the **`linux_host`** adapter (Singularity + tmux, no
  scheduler), so its cards carry no resource request at all. The dashboard's
  `submit.yml.erb` emits real Slurm flags (`--cpus-per-task`, `--mem`,
  `--partition`, optional `--account`) that have only been reviewed, not run.
  The dashboard *application* is heavily used daily — just via the equivalent
  single-port local proxy, not through `batch_connect` + Slurm. Expect the first
  session launch to need iteration on the submit template, not on the app.
- Version upgrades on a server tree deliberately do **not** use
  `bdtools update` (which force-checks-out). `install --server` verifies `HEAD`
  matches the pinned tag in `tools.yml` and refuses to build a diverged tree.
- kSNP4 is a vendored 545 MB SourceForge zip (not conda), x86_64-only, and
  unpacks group-unreadable — it needs a permission fix at install time.
- The MHC typing tool is flagged in-app as under development: DRB3 is
  production-ready, Class I calls are provisional. Not for diagnostic use yet.
- AMRFinderPlus currently has a database/binary version mismatch pending a
  decision on which side moves; that tool will produce no calls until resolved.

## Recommended path: prove it as a sandbox app first

This costs the admin team nothing and de-risks the sys-app install.

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
bin/bdtools install --sandbox <tool> --dry-run
```

That builds under `$HOME` and links a card into `~/ondemand/dev/`, visible under
**Develop → My Sandbox Apps** — no system changes, running on the real scheduler
under real site auth. Once one session launches cleanly there and we've settled
the partition/account fields, promoting to `/var/www/ood/apps/sys/` is a copy.

## Acceptance test

1. Launch **"Diagnostic Tools Dashboard"**; the job queues and starts.
2. The connect button opens the landing page through `/rnode/<host>/<port>/?t=…`.
3. A tool opens under `/t/<tool>/` — assets load, no mixed-content or 404s.
4. A run streams live progress (that is the SSE path).
5. In vSNP, an IGV panel loads a BAM (that is the Range/206 path).
6. `curl -H 'X-Forwarded-User: someone-else'` against the session → 403.
