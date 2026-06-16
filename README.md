# bioinformatic_diagnostic_tools

The single point to **install, run, and update** the Kapur Lab suite of
bioinformatics GUIs (vSNP, IRMA, AMR, MLST, GenoFLU, kSNP, Kraken ID-Parse,
NCBI-Submit). Each tool lives in its own repo and is released independently;
this umbrella repo pins the set in a manifest ([`tools.yml`](tools.yml)) and
drives a uniform install/update experience across environments.

```
bioinformatic_diagnostic_tools/
├── tools.yml          the suite manifest — each tool repo + pinned version
├── bin/bdtools     the CLI (install | local | status | check-updates | update)
├── sites/             per-site config for OOD server installs (site.conf)
├── ood-core/          optional OOD-core bootstrap for bare-metal lab servers
└── docs/              per-environment runbooks + sysadmin guide
```

## Quick start

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
bin/bdtools list                 # what's in the suite
bin/bdtools install irma_gui     # local mode (default) — Linux / macOS / WSL2
bin/bdtools local irma_gui       # re-launch a tool you've installed
```

## The five deployment targets

| Environment | Command | Notes |
|---|---|---|
| **Personal Linux / macOS / Windows (WSL2)** | `bdtools install --local <tool>` | Standalone: conda env + uvicorn + browser at `localhost`. No OOD. See [docs/INSTALL_LOCAL.md](docs/INSTALL_LOCAL.md). |
| **Institutional HPC OOD — as a user** | `bdtools install --sandbox <tool>` | Per-user app in `~/ondemand/dev/`, no sysadmin needed. See [docs/INSTALL_HPC_OOD.md](docs/INSTALL_HPC_OOD.md). |
| **Institutional HPC OOD — as a sysadmin** | `bdtools install --server <tool>` | System app under `/var/www/ood/apps/sys`. See [docs/SYSADMIN.md](docs/SYSADMIN.md). |
| **Bare-metal Linux lab server** | `bdtools install --server all` (+ ood-core) | Full stack incl. OOD core. See [docs/INSTALL_BARE_METAL.md](docs/INSTALL_BARE_METAL.md). |

> **Status:** `--local` and `--sandbox` are implemented. `--sandbox` delegates
> to a tool's own `deploy/setup-sandbox.sh` when present (e.g. vsnp_gui) and
> otherwise runs a generic per-user build + card-link. `--server` (system OOD
> install) is being promoted from `vsnp_gui/deploy/install_ood.sh`.

## Updating

```bash
bdtools check-updates          # report newer upstream versions
bdtools update <tool|all>      # move to the newest tag + rebuild
```

The manifest is the source of truth: tagging this repo (`suite-YYYY.MM`) pins
the entire set, so any site can reproduce an exact deployment.

## How it relates to the tool repos

The tool repos stay independent and individually releasable. This umbrella only
*references* them (by repo + version in `tools.yml`) and provides the shared
install/update/site machinery. See [docs/BUILDING_A_TOOL.md](docs/BUILDING_A_TOOL.md)
for the light contract a tool repo must satisfy to be drivable from here.
