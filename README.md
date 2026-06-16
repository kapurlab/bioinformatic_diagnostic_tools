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

## Install with a Claude agent (recommended)

The easiest path: let a [Claude Code](https://claude.com/claude-code) agent
detect your system and install the right way. Clone the repo and point the agent
at [`AGENTS.md`](AGENTS.md):

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
claude "Follow AGENTS.md to install the Kapur Lab diagnostic tools suite on this system."
```

The agent figures out whether you're on a personal computer, an Open OnDemand
cluster (as a user or an admin), and installs accordingly — production build
only, no developer cards. The build flow is **install → validate → dashboard**:
after building it validates against known samples and then hands you the access
point (the local dashboard, or your OOD dashboard). To validate on demand:

```bash
claude "Follow AGENTS.md to validate this deployment with bdtools test all and report PASS/FAIL/SKIP."
```

> No agent? The same steps by hand are in [INSTALL.md](INSTALL.md) and the
> per-environment runbooks below.

## Quick start (manual)

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
bin/bdtools list                 # what's in the suite
bin/bdtools install all          # local mode (default) — Linux / macOS / WSL2
bin/bdtools dashboard            # landing page: pick a GUI -> opens at http://127.0.0.1:8080/
bin/bdtools test all             # validate against known samples (PASS/FAIL/SKIP)
```

On a personal machine, `bdtools install` ends by printing your access point and
(in a terminal) opening the **local dashboard** — a home page listing the
installed GUIs where you pick the one to run. Re-open it any time with
`bdtools dashboard`, or launch a single tool with
`bdtools local <tool> --port 8080`.

## The five deployment targets

| Environment | Command | Notes |
|---|---|---|
| **Personal Linux / macOS / Windows (WSL2)** | `bdtools install --local <tool>` | Standalone: conda env + uvicorn + browser at `localhost`. No OOD. See [docs/INSTALL_LOCAL.md](docs/INSTALL_LOCAL.md). |
| **Institutional HPC OOD — as a user** | `bdtools install --sandbox <tool>` | Per-user app in `~/ondemand/dev/`, no sysadmin needed. See [docs/INSTALL_HPC_OOD.md](docs/INSTALL_HPC_OOD.md). |
| **Institutional HPC OOD — as a sysadmin** | `bdtools install --server <tool>` | System app under `/var/www/ood/apps/sys`. See [docs/SYSADMIN.md](docs/SYSADMIN.md). |
| **Bare-metal Linux lab server** | `ood-core` → `bdtools site-init` → `bdtools install --server all` | Full stack: OOD core, then groups/storage/branding, then every tool. See [docs/INSTALL_BARE_METAL.md](docs/INSTALL_BARE_METAL.md). |

> **Production vs developer cards:** every tool ships a production card (what
> users see), a developer branch-picker (`<tool>_dev`), and a per-user sandbox.
> A normal `install --server` registers **only the production card** — dev cards
> stay hidden. Developers opt in per tool with `install --server --with-dev`, or
> use the no-admin per-user `install --sandbox`. Typical users never see or need
> the dev path.

> **Status:** `--local`, `--sandbox`, and `--server` are implemented.
> `--sandbox` delegates to a tool's own `deploy/setup-sandbox.sh` when present
> (e.g. vsnp_gui), else a generic per-user build + card-link. `--server`
> installs a tool's source+env at `TOOLS_ROOT/<tool>` and renders its OOD card
> into the sys-apps dir, rewriting site literals (paths, cluster, groups) from
> `sites/site.conf`. Full site bootstrap (OOD core, groups, storage, dashboard
> branding) stays with `ood-core/` + `vsnp_gui/deploy/install_ood.sh`.

## Updating

```bash
bdtools check-updates          # report newer upstream versions
bdtools update <tool|all>      # move to the newest tag + rebuild
```

The manifest is the source of truth: tagging this repo (`suite-YYYY.MM`) pins
the entire set, so any site can reproduce an exact deployment. Maintainers: see
[docs/RELEASING.md](docs/RELEASING.md) for cutting tags and publishing GitHub
Releases (`bin/make-releases.sh`).

## Validating a deployment

After installing or updating, confirm the tools still produce correct diagnostic
output on known public samples:

```bash
bdtools test all          # download known SRA/GenBank samples, run, diff vs expected
bdtools test mlst_gui     # one tool
```

Each test downloads a fixed SRA/GenBank accession, runs the tool headlessly, and
compares the result to a committed expected (golden) result. `mlst_gui`,
`amr_plus_gui`, `irma_gui`, and `genoflu_gui` are validated today; the remaining
tools **SKIP** cleanly (no spec yet, not installed, or a required reference DB is
absent) and a SKIP is not a failure. The accessions and expected values are in
[`tests/`](tests/) — see [tests/README.md](tests/README.md) for the coverage
table and how the golden results were established. These are the suite's
diagnostic-validation baseline.

## How it relates to the tool repos

The tool repos stay independent and individually releasable. This umbrella only
*references* them (by repo + version in `tools.yml`) and provides the shared
install/update/site machinery. See [docs/BUILDING_A_TOOL.md](docs/BUILDING_A_TOOL.md)
for the light contract a tool repo must satisfy to be drivable from here.
