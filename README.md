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

> **You'll be asked to approve steps.** The agent pauses for permission before
> running install commands — click **Allow** (or *Allow for this session*) to
> approve each one. (Don't add `-p` — that's headless/print mode, which can't
> answer these prompts and will stall.)

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

## Quick start — personal computer (Linux / macOS / WSL2)

This is the **local** path: no Open OnDemand, the tools run on your own machine.
`bdtools install` defaults to `--local`, so `install all` below is exactly the
same as `install --local all`. **On an HPC / Open OnDemand cluster, do not use
this** — jump to [Installing on Open OnDemand](#installing-on-open-ondemand-hpc).

> **Before you start** you need **git** and a **conda/miniforge**
> ([Miniforge](https://github.com/conda-forge/miniforge)); the installer stops
> with a clear message if either is missing. On **Apple Silicon Macs (M1/M2/M3…)**
> the env is built as `osx-64` under **Rosetta 2** (bioconda has no native arm64
> builds for the pipeline toolchain) — install it once with `softwareupdate
> --install-rosetta --agree-to-license`. Node.js/npm is only needed when building
> a tool from a branch (release tarballs ship the frontend prebuilt). Full
> platform notes: [docs/INSTALL_LOCAL.md](docs/INSTALL_LOCAL.md).

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
bin/bdtools list                 # what's in the suite
bin/bdtools install all          # same as: install --local all   (Linux / macOS / WSL2)
bin/bdtools dashboard            # landing page: pick a GUI -> opens at http://127.0.0.1:8080/
bin/bdtools test all             # validate against known samples (PASS/FAIL/SKIP)
```

## Opening your tools — the local dashboard

*(Personal/local installs only. On Open OnDemand your tools appear as cards in
your institution's OOD dashboard instead — see the OOD section below.)*

You don't need to be a "command-line person." After installing, a **dashboard**
opens in your web browser automatically — a home page listing your tools. Click
a tool and it opens in a new tab. That's it.

**How it works (plain version):**

1. When the install finishes, your browser opens to the dashboard at
   **http://127.0.0.1:8080/** (that address means "this computer," nothing is on
   the internet). A small window also stays open in the background — that window
   *is* the dashboard. **Leave it open while you work.**
2. In the dashboard, click **Launch / Open** next to a tool. It opens in a new
   browser tab. Use as many tools as you like.
3. When you're done you can just leave it, or close that small window to stop.

**Re-opening it later (e.g. after you restart your computer):**

Restarting your computer stops the dashboard — this is normal, nothing broke.
To get it back, pick whichever is easiest:

- **Easiest (macOS / double-click):** open the `bioinformatic_diagnostic_tools`
  folder and double-click **`Open Dashboard.command`**. Your browser opens to the
  dashboard again. *(The very first time on macOS, right-click the file → **Open**
  → **Open** to get past a one-time security prompt. After that, double-click
  works. Tip: drag it to your Dock or Desktop for one-click access.)*
- **Or type one line** (Terminal on macOS/Linux, or your WSL window):
  ```bash
  cd ~/bioinformatic_diagnostic_tools   # the folder you installed into
  bin/bdtools dashboard
  ```
  This re-opens the dashboard in your browser. To stop it, close that window or
  press **Control-C**. (If it's already running, this just re-opens the tab.)

You only ever need to remember one thing: **open the dashboard, then click your
tool.** Single tool instead? `bin/bdtools local <tool> --port 8080`, then open
http://127.0.0.1:8080/.

## Reference databases

A few tools need large third-party **reference databases** that aren't shipped
with the code (they're tens of GB and maintained upstream). The installer
**offers to set these up for you** at the end of a local install — just answer
**y** when asked. You can also run it anytime:

```bash
bin/bdtools setup-databases
```

It first asks **where** to put the databases:

- **Home** (`~/databases`) — a personal copy, good for a laptop.
- **Shared** (`/srv/kapurlab/databases`) — one copy the whole machine/lab uses.

then downloads each database and **points the relevant GUIs at it automatically**
(no manual path editing). Re-running is safe — anything already present is
skipped. Restart a running tool afterward to pick up the new paths:
`bin/bdtools dashboard --restart`.

| Database | Used by | Installs to | Source |
|---|---|---|---|
| Kraken2 `k2_standard_08gb` (~8 GB) | kraken_id_parse_gui | `<root>/kraken2/k2_standard_08gb` | [genome-idx.s3](https://genome-idx.s3.amazonaws.com/kraken/k2_standard_08_GB_20260226.tar.gz) |
| BLAST `ref_prok_rep_genomes` | kraken_id_parse_gui | `<root>/blast/ref_prok_rep_genomes` | NCBI (`update_blastdb.pl`) |
| vSNP reference options | vsnp_gui | `<root>/vsnp3/reference_options` | [USDA-VS/vSNP_reference_options](https://github.com/USDA-VS/vSNP_reference_options) |
| vsnp dependencies | vsnp_gui | `<root>/vsnp3/vsnp_dependencies` | [USDA-VS/vsnp3_test_dataset](https://github.com/USDA-VS/vsnp3_test_dataset) (`vsnp_dependencies/`) |

Set up only some of them by naming which: `bin/bdtools setup-databases kraken vsnp-refs`
(choices: `kraken blast vsnp-refs vsnp-deps`). Pick the location non-interactively
with `--home`, `--shared`, or `--root DIR`.

**Doing it by hand instead — and staging on large storage.** These databases
are big (Kraken2 standard ~8 GB and up; BLAST nucleotide DBs are tens of GB). If
your home directory is on a small disk, download them to a **large-storage
volume** and `ln -s` them into the databases root each GUI reads (`~/databases`
by default), or point the tool's config there directly. Set `BIG` below to your
large-storage mount. (This is exactly what `setup-databases` automates.)

**Kraken2 database + taxonomy** (kraken_id_parse_gui → config key `kraken_db`).
Prebuilt Kraken2/Bracken indexes — with current sizes, dates, and download
links — are published at the AWS-hosted index collection:
**<https://benlangmead.github.io/aws-indexes/k2>**. Pick a build (e.g.
*Standard-8* ~8 GB for a laptop, or the full *Standard* for a server), copy its
`.tar.gz` link from that page, and stage it on large storage. One extracted
folder holds the **database and its taxonomy together** (`hash.k2d`, `opts.k2d`,
`taxo.k2d`) — Kraken reads all three from that one directory, so keep them in
place and link the whole folder.

```bash
BIG=/mnt/bigstore                         # <- your large-storage mount

# 1. Download + extract onto large storage (use the current link from the k2
#    page above; the pinned example below is the one setup-databases uses):
mkdir -p "$BIG/kraken2/k2_standard_08gb"
curl -fL https://genome-idx.s3.amazonaws.com/kraken/k2_standard_08_GB_20260226.tar.gz \
  | tar -xz -C "$BIG/kraken2/k2_standard_08gb"
ls "$BIG/kraken2/k2_standard_08gb"        # -> hash.k2d  opts.k2d  taxo.k2d  (+ seqid2taxid.map)

# 2. Link the database (incl. taxonomy) into the databases root the GUI reads:
mkdir -p ~/databases/kraken2
ln -s "$BIG/kraken2/k2_standard_08gb" ~/databases/kraken2/k2_standard_08gb

# 3. Point kraken_id_parse_gui at it (or use the tool's Settings page):
python3 bin/lib/db_config.py kraken --kraken-db ~/databases/kraken2/k2_standard_08gb
```

> Building your **own** Kraken2 DB rather than using a prebuilt index? Then you
> fetch the taxonomy yourself first: `kraken2-build --download-taxonomy --db
> <dir>` (large), then `--download-library` / `--build`. Put `<dir>` on large
> storage and link it the same way. The prebuilt indexes above already bundle the
> taxonomy, so most users don't need this.

**BLAST databases + taxonomy** (kraken_id_parse_gui → config key `blast_db`).
BLAST DBs come from NCBI via `update_blastdb.pl`, which ships in the
kraken_id_parse_gui conda env (the `blast` package). **First list what's
available to download:**

```bash
# the env's copy (or `conda activate kraken_id_parse` first, then just update_blastdb.pl):
UB=~/.local/share/bdtools/checkouts/kraken_id_parse_gui/env/bin/update_blastdb.pl
"$UB" --showall pretty          # every downloadable NCBI BLAST DB, with sizes + descriptions
```

Then stage the DB you want (e.g. `ref_prok_rep_genomes`) on large storage, add
`taxdb` (so hits carry organism names), and link the folder into the databases
root:

```bash
BIG=/mnt/bigstore
mkdir -p "$BIG/blast" && cd "$BIG/blast"
"$UB" --decompress ref_prok_rep_genomes   # the DB (multi-volume, tens of GB)
"$UB" --decompress taxdb                  # taxonomy names for BLAST hits

# link the whole blast dir into the databases root, then point the GUI at the
# DB *base name* (no file extension):
ln -s "$BIG/blast" ~/databases/blast
python3 bin/lib/db_config.py kraken --blast-db ~/databases/blast/ref_prok_rep_genomes
```

> Keeping several BLAST DBs in one directory? Export `BLASTDB=$BIG/blast` so
> every tool finds them by base name without a full path.

**vSNP references** (vsnp_gui). Small enough to keep under `~/databases`, but the
same `ln -s`-to-large-storage trick applies if you prefer:

```bash
# vSNP reference options (vsnp_gui → Reference Locations / "vsnp3_reference_options_root")
git clone --depth 1 https://github.com/USDA-VS/vSNP_reference_options.git \
  ~/databases/vsnp3/reference_options

# vsnp dependencies (vsnp_gui → add as a Reference Location)
git clone --depth 1 https://github.com/USDA-VS/vsnp3_test_dataset.git /tmp/vsnp3_test_dataset
mv /tmp/vsnp3_test_dataset/vsnp_dependencies ~/databases/vsnp3/vsnp_dependencies
```

> The curated Step-2 **VCF databases** in vsnp_gui (e.g. `mtbc0_v1.1`) are
> lab-private and are not part of this setup — add them under
> `vcf_db_folders` in vsnp_gui's settings if you have access to them.

## Installing on Open OnDemand (HPC)

The same `bdtools` CLI installs onto an Open OnDemand cluster — but **not** with
the `install all` from the local Quick start above (that builds a personal
`localhost` copy). On OOD the **access point is your institution's OOD
dashboard**: the tools appear there as cards for users to launch. Pick the path
that matches your access.

### A regular user (no admin rights) — `--sandbox`

Per-user install into your own OOD sandbox (`~/ondemand/dev/`); nothing
system-wide, no sysadmin needed.

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
bin/bdtools install --sandbox all        # or a single tool, e.g. install --sandbox mlst_gui
```

Then open your OOD portal → the **Dev / sandbox** apps, and launch a tool card.
Full runbook: [docs/INSTALL_HPC_OOD.md](docs/INSTALL_HPC_OOD.md).

### The OOD sysadmin (publish to all users) — `--server`

Installs each tool as a **system app** under `/var/www/ood/apps/sys/`, registering
**only the production card** (developer cards stay hidden; add `--with-dev` per
tool only if you want them). Requires root and an already-running OOD. Always
dry-run first — it shows exactly what it would write and changes nothing.

```bash
sudo git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git /opt/bdtools
cd /opt/bdtools

# 1. Describe your site once (paths, cluster name, Unix groups):
cp sites/site.conf.example sites/site.conf
"$EDITOR" sites/site.conf          # set CLUSTER_NAME, TOOLS_ROOT, SYS_APPS_DIR, groups

# 2. Dry-run FIRST — prints every action, writes nothing:
sudo bin/bdtools install --server all --site-conf sites/site.conf --dry-run

# 3. Real install once the dry-run looks right:
sudo bin/bdtools install --server all --site-conf sites/site.conf

# 4. Validate (download known samples, run, diff vs expected):
BDTOOLS_TOOLSDIR=<your TOOLS_ROOT> bin/bdtools test all
```

The production tool cards now show up in the OOD dashboard for all users. Full
runbook (preflight checks, what it does and does **not** touch, updating):
[docs/SYSADMIN.md](docs/SYSADMIN.md). Standing up a brand-new lab server from bare
metal (no OOD yet)? Start at [docs/INSTALL_BARE_METAL.md](docs/INSTALL_BARE_METAL.md).

## All deployment paths at a glance

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
compares the result to a committed expected (golden) result. All seven diagnostic
GUIs have recorded goldens (`ncbi_submit_gui`, the submission tool, is not tested
by design). The tier-2 tools (`kraken_id_parse_gui`, `vsnp_gui`) need an external
reference DB and **SKIP** cleanly when it's absent — a SKIP is not a failure. The
accessions and expected values are in [`tests/`](tests/) — see
[tests/README.md](tests/README.md) for the coverage table and how the golden
results were established. These are the suite's diagnostic-validation baseline.

## Troubleshooting (local installs)

**First stop for any "it won't run" problem — ask the doctor.** It checks every
installed tool (its environment, the programs it needs, and its reference
databases) and prints, in plain language, exactly what to run to fix anything
that's wrong:

```bash
bin/bdtools doctor               # all installed tools
bin/bdtools doctor vsnp_gui      # just one
```

A healthy tool shows all ✓; anything broken shows a ✗ with the fix command right
under it (e.g. `bin/bdtools setup-databases kraken` for a missing database, or
`bin/bdtools update <tool>` to rebuild an incomplete environment). The installer
runs this for you at the end of an install, too.

**A tool failed partway through `install all` — how do I resume (and pick up a
fix)?** `install all` builds the tools in order and stops at the first failure;
everything before it is already done. When a bug in a tool has since been fixed
upstream (a new pinned version), get the fix and re-run — the install is
idempotent and resumable:

```bash
cd ~/bioinformatic_diagnostic_tools   # your umbrella checkout
git pull                              # updated tools.yml pins (the fixes) + docs
bin/bdtools install all               # resumes: done tools are skipped in <1s
bin/bdtools doctor                    # confirm every tool is ✓
```

`install` reuses each already-built tool (its conda env is detected and skipped,
so finished tools cost ~1s) and **moves any tool whose checkout is behind the
newly-pinned version onto that version before building** — so the re-run picks up
the fix rather than silently rebuilding the old code. It then continues to the
tool that failed and any not yet reached. Re-running is always safe. (If a single
tool is the problem, `bin/bdtools update <tool>` does the same move-to-pin +
rebuild for just that one.) This is the standard "get me back on track" recipe to
hand a group hitting environment-specific snags: **`git pull` → `install all` →
`doctor`.** If `git pull` complains about local changes, `git stash && git pull`
first (see the stash note below).

**After updating, the tools still behave like the old version.** The dashboard
and any open tools keep running until you stop them — closing the browser tab
does *not* stop the servers. After a `git pull`, restart them so the new code
takes effect:

```bash
bin/bdtools dashboard --restart      # stops the running dashboard + tools, starts fresh
```

(`--stop` stops everything without restarting.) Re-open a tool from the dashboard
afterward so it relaunches on the new code. You'll know the old one is still up if
you see *"The dashboard is already running"* when you expected a fresh start.

**`git pull` says "Your local changes would be overwritten by merge."** Something
edited a tracked file locally. Set those edits aside and pull:

```bash
git stash && git pull                # then: bin/bdtools dashboard --restart
```

(Don't `git stash pop` afterward — the stashed edits are superseded by what you
pulled. If you don't care about local edits at all, `git fetch origin && git
reset --hard origin/main` forces an exact match; your downloaded data and conda
envs live outside the repo and are untouched.)

**vsnp_gui Step 1 fails: "reference folder not found: …".** Get the latest
vsnp_gui, then restart:

```bash
git stash && git pull
bin/bdtools update vsnp_gui           # moves to v0.2.1+ (env preserved)
rm -f ~/.config/vsnp_gui/config.json  # clears any frozen /srv paths (rebuilt correctly on next launch)
bin/bdtools dashboard --restart
```

Which path is in the error tells you which case it is:
- **`/srv/kapurlab/refs/…`** — an old build/config pointing at the lab server.
  The `update` + config reset above fixes it. (If you build by hand, the install
  must print `configured local vsnp site: …/vsnp3-site`.)
- **`…/vsnp3-site/refs/…/<your-reference>`** — the reference lives in a folder you
  added under **Reference Locations**, not the default set. vsnp_gui **v0.2.1+**
  searches all your added locations; `bin/bdtools update vsnp_gui` gets it.

Built-in references for Step 1: **`Mycobacterium_H37`** (M. tuberculosis) or
**`Mycobacterium_AF2122`** (M. bovis). `mtbc0_v1.1` isn't in the public set — to
use it, add the folder that contains it under **Reference Locations** (e.g. a
downloaded vsnp3 test dataset's `vsnp_dependencies`) and make sure you're on
v0.2.1+.

**Nothing happens when I double-click `Open Dashboard.command` (macOS).** The
first time, right-click it → **Open** → **Open** to clear the one-time security
prompt; after that a normal double-click works.

## How it relates to the tool repos

The tool repos stay independent and individually releasable. This umbrella only
*references* them (by repo + version in `tools.yml`) and provides the shared
install/update/site machinery. See [docs/BUILDING_A_TOOL.md](docs/BUILDING_A_TOOL.md)
for the light contract a tool repo must satisfy to be drivable from here.
