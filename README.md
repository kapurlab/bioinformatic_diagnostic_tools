# bioinformatic_diagnostic_tools (bdtools)

The single point to **install, run, and update** the Kapur Lab suite of
bioinformatics GUIs (vSNP, IRMA, AMR, MLST, GenoFLU, kSNP, Kraken ID-Parse,
NCBI-Submit, and the developmental Bovine MHC Typer). Each tool lives in its own
repo and is released independently; this umbrella repo pins the set in a
manifest ([`tools.yml`](tools.yml)) and drives a uniform install/update
experience across environments.

```
bioinformatic_diagnostic_tools/
├── tools.yml          the suite manifest — each tool repo + pinned version
├── bin/bdtools     the CLI (install | local | status | versions | check-updates | update)
├── sites/             per-site config for OOD server installs (site.conf)
├── ood-core/          optional OOD-core bootstrap for bare-metal lab servers
└── docs/              per-environment runbooks + sysadmin guide
```

## 🚀 Quick start — personal computer (Linux / macOS / WSL2)

This is the **local** path: no Open OnDemand, the tools run on your own machine.
`bdtools install` defaults to `--local`, so `install all` below is exactly the
same as `install --local all`. **On an HPC / Open OnDemand cluster, do not use
this** — jump to [Installing on Open OnDemand](#installing-on-open-ondemand-hpc).

> **Before you start** you need **git** and **conda/miniforge** — the installer
> stops with a clear message if either is missing (it does **not** install conda
> for you). If you don't have conda yet, install Miniforge first (one-time):
>
> ```bash
> # If needed, install Miniforge (one-time) — Linux / macOS / WSL2:
> curl -L -O "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh"
> bash Miniforge3-$(uname)-$(uname -m).sh
> ```
>
> **WSL2** is Linux — use the commands above as-is. On **macOS**, if you've never
> used the Terminal, first run `xcode-select --install` (this also provides
> `git`). **Apple Silicon Macs (M1/M2/M3…)** also need Rosetta 2 once:
> `softwareupdate --install-rosetta --agree-to-license`.

> 💽 **On an HPC / shared cluster?** Set **`BDTOOLS_HOME`** to large
> **scratch/work/group** storage *before* `install all` — cluster home dirs are
> quota-limited and the conda envs are multi-GB, so the default
> (`~/.local/share/bdtools`) fails partway with *Disk quota exceeded*. Persist it
> once and it's used by every command (install, dashboard, …):
>
>     echo 'export BDTOOLS_HOME=/path/on/large-storage/bdtools' >> ~/.bashrc && source ~/.bashrc
>
> A lab can share one install via a group path. More:
> [docs/INSTALL_LOCAL.md](docs/INSTALL_LOCAL.md#where-things-live).

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
# HPC/cluster? first set large-storage BDTOOLS_HOME (home quotas are too small — see note above)
bin/bdtools list                 # what's in the suite
bin/bdtools install all          # same as: install --local all   (Linux / macOS / WSL2)
# If prompted to install databases, pick the best location and say yes (see "Reference databases" below).
bin/bdtools dashboard            # landing page: pick a GUI -> opens at http://127.0.0.1:8080/
bin/bdtools test all             # validate against known samples (PASS/FAIL/SKIP)
```

> ✅ **When it finishes**, the installer prints where your tools live and opens
> the **dashboard** in your browser automatically at **http://127.0.0.1:8080/** —
> pick a tool and it opens in a tab. **Keep that window open while you work.**
> For a proper double-click launcher with an icon (and no terminal window at all),
> run **`bin/bdtools make-launcher`** once — it puts *Kapur Lab Dashboard* on your
> Desktop (macOS) or in your applications menu (Linux/WSL). Otherwise re-open the
> dashboard with `bin/bdtools dashboard`. To launch just one tool:
> `bin/bdtools local vsnp_gui --port 8080`, then open http://127.0.0.1:8080/.

> 🩺 **If `install all` reports a problem** — computing environments differ, so
> this can happen — run the doctor; it checks every tool and prints the exact fix
> under each ✗:
>
>     bin/bdtools doctor
>
> Fixes ship as new tool versions, so the standard "get back on track" recovery
> is to pull and re-run — the install is safe and resumable (finished tools are
> skipped, the failed one is retried):
>
>     git pull && bin/bdtools install all && bin/bdtools doctor
>
> Full details in [🩺 Troubleshooting](#-troubleshooting-local-installs);
> error messages are indexed by their exact text in
> [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## 🖥️ Opening your tools — the local dashboard

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

- **Easiest (double-click, recommended):** make yourself a real launcher once:

  ```bash
  bin/bdtools make-launcher
  ```

  On macOS that creates **Kapur Lab Dashboard.app** on your Desktop: double-click
  it and your browser opens, with **no terminal window** and no security prompt.
  On Linux/WSL it adds *Kapur Lab Dashboard* to your applications menu (add
  `--dest ~/Desktop` for a desktop copy too). You can move it wherever you like —
  Dock, Desktop, `/Applications` — because it remembers where the tools are
  installed. Re-run the command if you ever move or reinstall the tools.

  The dashboard it starts keeps running after the launcher exits, so quitting the
  app can never interrupt an analysis. Stop it with **Shut down** in the browser.
- **Or the plain script (macOS):** double-click **`Open Dashboard.command`** in
  the repo folder. This still works, but it opens a Terminal window alongside the
  dashboard, and the first time you must right-click → **Open** → **Open** to
  clear a one-time security prompt.
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

**Did your URL end in `?t=…`?** That's expected on a shared machine — it's a
one-time session key that keeps other accounts on the same host out of your
dashboard. A personal Mac or WSL box gets a plain URL instead. Neither is a sign
of a failed update; see
[Why does my dashboard URL have `?t=…`?](docs/INSTALL_LOCAL.md#why-does-my-dashboard-url-have-t)

**Safe restarts, shutdowns, and updates.** Analyses run independently so they can
survive a browser disconnect. The dashboard therefore checks every launched
tool before stopping or updating anything. If a job is running or a tool cannot
be verified, the operation is blocked and names the tool/job to resolve. Finish
the job—or stop it from that tool's own interface—then try again. Terminal
`dashboard --stop`/`--restart` commands use the same guard; they never use broad
process-name kills.

**Light, dark, or system appearance.** Use the compact appearance control in the
dashboard or any tool header. The choice is saved in the browser and applied
before the page paints, so there's no flash of the wrong theme. System mode
tracks the operating-system preference automatically.

The choice is **shared across tools** when they run through the consolidated
dashboard, because that serves every tool from the dashboard's own address
(`/t/<tool>/`). In the legacy fallback — each tool on its own port, used only when
`starlette`/`httpx`/`uvicorn` are missing — each tool remembers its own setting
instead. If the dashboard looks dark but a tool opens light, that tool is on a
release older than the appearance control: run `bin/bdtools update <tool>`.

## 💾 Reference databases

A few tools need large third-party **reference databases** that aren't shipped
with the code (they're tens of GB and maintained upstream). The installer
**offers to set these up for you** at the end of a local install — just answer
**y** when asked. You can also run it anytime:

```bash
bin/bdtools setup-databases
```

It first asks **where** to put the databases:

- **Home** (`~/databases`) — a personal copy, good for a laptop.
- **Shared** — one copy the whole machine or lab uses. You are prompted for the
  path. If this machine already declares a shared root (see
  [Where the tools look](#-where-the-tools-look-for-databases)) it is offered as
  the default; otherwise type the full path you want.
- **Custom** — type any path.

then downloads each database and **points the relevant GUIs at it automatically**
(no manual path editing). Re-running is safe — anything already present is
skipped. Restart a running tool afterward to pick up the new paths:
`bin/bdtools dashboard --restart`.

| Database | Used by | Installs to | Source |
|---|---|---|---|
| Kraken2 `k2_standard_08gb` (~8 GB) | kraken_id_parse_gui | `<root>/kraken2/k2_standard_08gb` | [AWS index collection](https://benlangmead.github.io/aws-indexes/k2) (builds are re-published; `setup-databases` pins a known-good one) |
| BLAST `ref_prok_rep_genomes` | kraken_id_parse_gui | `<root>/blast/ref_prok_rep_genomes` | NCBI (`update_blastdb.pl`) |
| vSNP reference options | vsnp_gui | `<root>/vsnp3/reference_options` | [USDA-VS/vSNP_reference_options](https://github.com/USDA-VS/vSNP_reference_options) |
| vsnp dependencies | vsnp_gui | `<root>/vsnp3/vsnp_dependencies` | [USDA-VS/vsnp3_test_dataset](https://github.com/USDA-VS/vsnp3_test_dataset) (`vsnp_dependencies/`) |
| Step 2 VCF comparison DBs | vsnp_gui | `<root>/vsnp3/vcf_db_directories` (linked once into the GUI's VCF-DB root) | [kapurlab/vcf_db_directories](https://github.com/kapurlab/vcf_db_directories) |

Set up only some of them by naming which: `bin/bdtools setup-databases kraken vsnp-refs`
(choices: `kraken blast vsnp-refs vsnp-deps vcf-dbs`). Pick the location non-interactively
with `--home`, `--shared`, or `--root DIR`.

The VCF comparison databases are seeded **once**: after the first run they are
yours to curate — a folder you remove is never silently re-added, and a folder
you added yourself is never overwritten.

The other five tools — `mlst_gui`, `genoflu_gui`, `irma_gui`, `ksnp_gui`,
`ncbi_submit_gui` — need **no** external database. `amr_plus_gui` needs none
either: its AMRFinderPlus database ships inside its conda environment.
`mhc_gui` ships its BoLA references in the repository. So the list above is the
whole job.

### 📍 Where the tools look for databases

You rarely need this, but when a path looks wrong this is the order to check. A
tool is *told* where its databases are — it does not assume — and the first answer
below wins:

1. **What you set in the tool's Settings page** — saved in
   `~/.config/<tool>/config.json`. Yours, permanent, and it beats everything
   below. `bdtools setup-databases` writes here for you.
2. **`BDTOOLS_DB_ROOT`**, if something in your shell already exported it.
3. **The location `setup-databases` recorded** — `~/.local/share/bdtools/db-root`.
   Note this outranks `sites/site.conf`: a stale file here explains a path that
   ignores your site config.
4. **What the machine declares** — `DB_ROOT`, `DATABASES_ROOT`, or `SITE_ROOT` in
   `sites/site.conf` (a site install writes this; a laptop usually has none).
5. **`~/databases`** — the last-resort personal default.

Whatever wins, a tool only offers the path if the data is actually there. If it
isn't, the field stays **blank** and the tool asks you to choose — it will not show
you a path that was never going to work.

To see what your machine resolves, run this **from the repository directory**:

```bash
bin/lib/site_paths.py .
```

> **Updating does not change the database paths you have set.** A tool loads your
> saved `config.json` and fills in only the keys that are *missing*, so your
> `kraken_db`, `blast_db` and equivalents survive an update untouched.
>
> Two deliberate exceptions, so they don't surprise you:
> - **vSNP, local installs only.** `bdtools install` / `bdtools local` repoints five
>   *derived* keys in `~/.config/vsnp_gui/config.json` (`vsnp3_path`,
>   `vsnp3_reference_options_root`, `vcf_db_folders_root`, `vsnp_gui_deploy_path`,
>   `audit_root`) at your local install and sets `shared_projects_root` to empty.
>   That repairs configs which had frozen a server's paths; your other preferences
>   in the file are kept. A server/OOD deployment never runs this step.
> - **The shared-projects root in AMR Plus and vSNP** comes from the deployment when
>   it provides one, in preference to the saved value — that is what lets a laptop
>   switch off a shared area which only exists on a server.
>
> Your personal `projects_root` and your database paths are never overridden.

**One rough edge, so it doesn't surprise you.** The above is true for *databases*.
The **shared projects** folder is only partly converted — it reaches four of the
nine tools:

- **Honours a configured location:** AMR Plus, Kraken ID Parse, vSNP, and MLST.
- **Does not:** GenoFLU, IRMA, kSNP, NCBI Submit, and MHC — these still look at a
  fixed path that exists only on the original lab server.

Those five check whether the directory is there first, so elsewhere they show an
**empty** shared area rather than a broken path. Nothing breaks; just don't expect a
shared results view in those five yet. **Your own `~/projects` works normally in
every tool** — this only affects the *shared* area.

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

Then open your OOD portal → **Develop → My Sandbox Apps**, and launch a tool card.

Two things to know before you rely on this:

- **The Develop menu usually has to be switched on for you** — on a stock OOD site
  that is a one-time admin action, not something you can do yourself. If you do not
  see the menu, ask your admin.
- **A sandbox tool session is not private to you.** Per-tool cards listen on all
  interfaces with no password, so any authenticated OOD user at your site who
  learns the host and port can use your running session. Fine for trying things
  out; use the site-wide install below for real specimen data.

Full runbook, including both points: [docs/INSTALL_HPC_OOD.md](docs/INSTALL_HPC_OOD.md).

### The OOD sysadmin (publish to all users) — `--server`

The recommended deployment registers **one** OOD app: a dashboard that allocates a
compute node once per session and runs every tool the user opens on that node. One
scheduler job per session instead of one per tool, and authentication enforced once
(per-session token plus an OOD-username match) instead of not at all.

Requires root and an already-running OOD. Always dry-run first — it prints every
action and changes nothing.

```bash
sudo git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git /opt/bdtools
cd /opt/bdtools

# 1. Describe your site once (paths, cluster name, Unix groups):
cp sites/site.conf.example sites/site.conf
"$EDITOR" sites/site.conf          # set SITE_ROOT, CLUSTER_NAME, TOOLS_ROOT, SYS_APPS_DIR

# 2. Build the tool environments, WITHOUT registering a card for each one:
sudo bin/bdtools install all --server --no-card --site-conf sites/site.conf --dry-run
sudo bin/bdtools install all --server --no-card --site-conf sites/site.conf

# 3. Register the single dashboard card:
sudo bin/bdtools install --server --dashboard --site-conf sites/site.conf --dry-run
sudo bin/bdtools install --server --dashboard --site-conf sites/site.conf

# 4. Validate (download known samples, run, diff vs expected):
BDTOOLS_TOOLSDIR=<your TOOLS_ROOT> bin/bdtools test all
```

> **Step 2 does not build every tool.** `vsnp_gui` and `kraken_id_parse_gui` do not
> ship the standard installer, so it prints a warning for each and carries on —
> the command still exits 0 with seven of nine environments built. Both need one
> extra command each. **[docs/INSTALL_HPC_OOD.md](docs/INSTALL_HPC_OOD.md) is the
> guide to follow for a real deployment**; the block above is the shape, not the
> whole procedure.

Per-tool cards are still supported for a dedicated single-tool allocation — omit
`--no-card` for that tool — but they are not needed for routine use, and each one
starts its own job with no application-level authentication.

Also relevant: [docs/OOD_DASHBOARD.md](docs/OOD_DASHBOARD.md) (design and auth
model), [docs/SYSADMIN.md](docs/SYSADMIN.md) (installer phases). Standing up a
brand-new lab server from bare metal, with no OOD yet? Start at
[docs/INSTALL_BARE_METAL.md](docs/INSTALL_BARE_METAL.md).

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

Three things update independently — run these from your
`bioinformatic_diagnostic_tools` checkout, in this order:

```bash
cd bioinformatic_diagnostic_tools   # the folder you installed into

# 1. The checkout itself (the bdtools CLI, tools.yml manifest, install scripts).
#    `bin/bdtools update` does NOT touch this — pull it yourself:
git pull

# 2. The individual tools — each tool repo, moved to its newest tag:
bin/bdtools check-updates          # report newer upstream versions (read-only)
bin/bdtools update <tool|all>      # move to the newest tag + rebuild

# 3. The ANALYSIS packages inside each tool's conda env — vsnp3, AMRFinderPlus,
#    kraken2, mlst, IRMA, GenoFLU. A new bioconda release of these does NOT move
#    any tool tag, so step 2 cannot see it:
bin/bdtools versions               # what you are running, per tool
bin/bdtools update-packages <tool> # move its packages to the newest release
```

**Updating a tool is opt-in, per tool.** `tools.yml` carries `updates: install`
or `updates: report` on every entry, and today **only vSNP3 is opted in**. For
everything else the suite reads and displays versions and changes nothing —
`bdtools update`, `update-packages` and the dashboard buttons all skip them.

Why: an env rebuild re-solves every dependency, and a conda transaction that dies
part-way rolls back into an env that may no longer run the tool. That is not
hypothetical. A dashboard "Install tool updates" run — which targets *all* —
rebuilt `kraken_id_parse_gui` on macOS, hit an upstream `spades` post-link bug,
and left a working install broken. Being a release behind cannot do that. A
diagnostic lab keeps the version it validated until it decides otherwise.

Do it deliberately for one named tool when you have a reason to:

```bash
bin/bdtools update kraken_id_parse_gui --allow-report-only
```

There is no dashboard path to that, and no bulk form of it. To opt a tool in
permanently, change its `updates:` in `tools.yml` and say why.

**Order matters, and it is the order above.** `bdtools update <tool>` rewrites the
pins in `tools.yml`; the bdtools self-update is `git pull --ff-only`, which restores
that file to get a clean tree — so doing tools first silently discards the pin
record. A tool rebuild should also run under the *new* install scripts, and step 3
works from pins that step 1 can change.

The dashboard does all three for you: every installed card shows the versions in
use (`vSNP3 v0.4.36 · vsnp3 3.35`), and when something newer exists the banner
offers them as separate numbered buttons, laid out left to right in the order to
run them — **1 Update bdtools** → **2 Install tool updates** → **3 Update conda
packages**. They are different acts with different risk, so they are never the same
button. Each listed item names the tool it belongs to and what is moving, e.g.
`vSNP3 — vsnp3 [conda package]: 3.35 → 3.36` versus `vSNP3 [app release]: v0.4.36 →
v0.4.37`. Restart the dashboard after each step to load the new code.

### Analysis package versions

`tools.yml` pins each analysis package exactly
(`packages: [bioconda::vsnp3=3.35, …]`), and the installer enforces those pins
after building an env. This matters because the tools' own `environment.yml`
files floor-pin (`mlst>=2.23`) or leave the version open, so before pinning, the
version you got depended on the day you built — the same suite release produced
mlst 2.33.1 on one machine and 2.35.0 on another.

`bdtools versions` reports installed-vs-newest per tool, reading the installed
version from `conda-meta` (instant, no conda solve) and the newest from
`api.anaconda.org` (one request per package, cached 6h, and reported as *unknown*
rather than "up to date" when the network is unavailable). It reads the env that
would **actually run** each tool, which is not always `<checkout>/env`.

**Changing a pin? Verify it first.** A pin can be wrong in two ways nothing offline
catches — jointly unsatisfiable, or installable on your machine and impossible on
someone else's:

```bash
bin/bdtools update-packages all --check-pins     # real solve per platform; minutes
```

Default targets are `linux-64,osx-64,osx-arm64`. Both pin mistakes this suite has
made would have been caught by it: `ncbi-amrfinderplus=4.2.7 + kraken2=2.17.1 +
mlst=2.35.0` is unsatisfiable together, and `mlst 2.34+` is *noarch* yet cannot be
installed on macOS at all because it depends on `libxcrypt1`, which has no macOS
build. **noarch does not mean portable.**

**Cross-platform consistency outranks being current.** A package is only moved to a
version installable on *every* platform the lab deploys to. An update that lands on
Linux and cannot land on macOS does not make the lab more current — it makes two
machines disagree about what produced a result, and one lab-wide older version is a
better position than the newest version in half the building. Every update therefore
solves locally *and* on `linux-64, osx-64, osx-arm64` before it is applied;
`--local-only` overrides for a machine-specific experiment.

**"Cannot be updated" is not an error.** When a newer version exists but cannot be
installed here — wrong platform, or it conflicts with the environment — the tools
keep working on what they have, the reason is printed in one line, the version is
recorded so it is not offered again until a newer release appears, and the command
**exits 0**. Only real breakage (an install that dies after a successful solve,
patches that fail to re-apply) is reported as a failure.

**And the dashboard goes quiet about it.** When a run finishes, the banner is
re-checked and repainted from the answer, so a package that has just been
established as uninstallable here stops being offered — instead of the banner still
listing it, which made every visit look as though the update had never been run. It
does not disappear, either: the "✓ Up to date" line says how many packages are
**held**, and the tool's card carries the reason and the way out
(`ncbi-amrfinderplus 3.12.8 · held (4.2.7)`, hover for the solver's reason and, when
a rebuild would lift it, the exact command). `bdtools versions` prints the same.
A package can be held two ways: declared in `tools.yml` (`packages_held:`, for a
version that can never be installed on a platform the lab deploys to — mlst 2.34+ on
macOS), or recorded per platform after a solve that was tried here and refused. Both
are keyed so a *newer* release is tried again; a manifest hold is by name and lasts
until it is removed.

`update-packages` installs the newest version, then **re-applies that tool's local
patches** and bumps the pin. The re-apply is why this is a command and not a
hand-run `conda install`: vsnp_gui carries Kapur Lab patches over the packaged
vsnp3 (the minus-strand annotation fix among them) and a fresh package overwrites
the patched files. Losing that silently changes results. Commit the updated
`tools.yml` to move the whole site to the version you just validated.

Pull the repo first, then `bin/bdtools update`, so any new install-script behavior
is in effect when tools rebuild. On local (macOS/Linux) installs, launching a GUI
after updating also self-heals its shared-tool links (e.g. vSNP3's link to Kraken
ID Parse).

### A failed build never costs you a working env

Two rules, both learned from losing one:

- **Nothing bdtools does deletes an env that was already there.** A prefix is
  only cleared between retries when *this run created it* — a rolled-back
  `conda create` leaves untracked `__pycache__` that makes attempt 2 die in
  `ClobberError`s. The old rule was "no working python", which is exactly what a
  rolled-back update of an existing env looks like, so the cleanup deleted the
  user's env and every retry then failed with nothing to fall back on.
- **Every env-changing operation records the env first.** `conda list --explicit`
  goes to `$BDTOOLS_HOME/state/<tool>.env-explicit.txt` (plus one generation
  back) before anything is installed. conda rolls a failed transaction back, but
  a rollback is not a restore — it can leave an env that no longer imports what
  the tool needs, and there was previously no record of what it had held.

```bash
bin/bdtools restore-env <tool>          # replay the snapshot: no solve, exact versions
bin/bdtools restore-env <tool> --prev   # the generation before that
```

Not covered by a conda snapshot: pip-installed packages and the built frontend.
Finish with `bin/bdtools install <tool>` if `doctor` still reports missing python
modules.

**A package's own post-link script can no longer take a build down.** conda
*sources* a post-link script into the wrapper shell and then sources the env's
`deactivate.d` hooks (see `conda/utils.py:wrap_subprocess_call`), so a `set -u`
inside the package's script — bioconda's `spades` has one — is still in force
when the conda-forge toolchain hooks read `$CONDA_BACKUP_AR` with no default:

```
deactivate_cctools_osx-64.sh: line 63: CONDA_BACKUP_AR: unbound variable
LinkError: post-link script failed for package defaults::spades-4.3.0
```

The `set -u` comes from the package, two lines earlier — not from an ancestor
shell — which is why scrubbing `SHELLOPTS` did not fix it. Every conda call now
runs with the plain toolchain variables defined (so the *activate* hook records a
backup) **and** with `BASH_ENV` pointing at a generated prelude that binds every
name those hooks read, whatever variant a package ships.

`update all` updates every tool even if one of them fails, lists the failures at
the end, and exits non-zero. A tool whose build did not finish is recorded as
unfinished, so re-running `bin/bdtools update <tool>` retries it instead of
reporting it as already up to date — and `bin/bdtools doctor` names it. A failed
build does not destroy the existing environment (conda rolls the transaction
back), so the tool keeps running on its previous dependencies until the rebuild
succeeds. See
[docs/INSTALL_LOCAL.md](docs/INSTALL_LOCAL.md#when-an-update-fails).

`bdtools update` intentionally manages personal bdtools checkouts only; it will
not force-checkout an external or server source tree. For OOD production,
reconcile each server checkout with the version pinned in `tools.yml`, review
any site/licensing commits, then run `install --server --dry-run` followed by
the real install. The server installer refuses stale or divergent source; see
[docs/SYSADMIN.md](docs/SYSADMIN.md#updating-a-server-deployment).

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
GUIs have recorded goldens (`ncbi_submit_gui` and the developmental `mhc_gui`
have no golden by design and SKIP). The tier-2 tools (`kraken_id_parse_gui`,
`vsnp_gui`) need an external reference DB and **SKIP** cleanly when it's absent
— a SKIP is not a failure. The accessions and expected values are in
[`tests/`](tests/) — see [tests/README.md](tests/README.md) for the coverage
table and how the golden results were established. These are the suite's
diagnostic-validation baseline.

## 🩺 Troubleshooting (local installs)

**First stop for any "it won't run" problem — ask the doctor.** It checks every
installed tool (its environment, the programs it needs, and its reference
databases) and prints, in plain language, exactly what to run to fix anything
that's wrong:

```bash
bin/bdtools doctor               # all installed tools
bin/bdtools doctor vsnp_gui      # just one
```

A healthy tool shows all ✓; anything broken shows a ✗ with the fix command right
under it (e.g. `bin/bdtools setup-databases kraken` for a missing database). The
installer runs this for you at the end of an install, too.

**The three steps, in order.** If you only remember one thing to tell someone
whose tool won't run, make it the third:

```bash
bin/bdtools doctor <tool>            # 1. what is actually wrong, and the command for it
bin/bdtools fix <tool> --apply       # 2. run the repairs that cannot break anything
bin/bdtools install <tool> --fresh   # 3. start the environment over from nothing
```

Step 3 is the one that always applies. An environment can be *incomplete* — a
missing module, a database that was never downloaded — and steps 1 and 2 repair
that in place, in seconds. But an environment can also be *wrong*: packages built
for another architecture, a python version swapped out from under the pip layer,
a transaction that failed half-way. Nothing additive fixes that, because
something is present that should not be. `--fresh` is the answer to all of it,
and it is safe to reach for:

* The old environment is **moved aside, not deleted**, and put back automatically
  if the build fails — so the worst case is the tool you already had.
* It builds at the version `tools.yml` pins, so it repairs without changing which
  version of the analysis software you are running.
* It is not gated by the per-tool update policy, so it works on every tool.
* A fresh environment gets its platform decided from scratch, which on Apple
  Silicon is the only way to correct one built for the wrong architecture.

Expect it to take as long as the original install of that tool (minutes, mostly
solving and downloading). `--rebuild`, by contrast, only *adds* newly declared
dependencies to the environment that is already there.

**Doctor is green but the tool still fails — or the error looks alien?** Run
`bin/bdtools diagnose <tool>` — read-only, it writes one pasteable report file
to send. And every error message this suite has seen is indexed by its exact
text in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): find the line you
saw, get one command to run and the fix.

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
- **A path under some shared server you are not on** (anything outside your home
  or `BDTOOLS_HOME`) — an old build or a saved config still pointing at a previous
  deployment. The `update` + config reset above fixes it. (If you build by hand,
  the install must print `configured local vsnp site: …/vsnp3-site`.)
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
prompt; after that a normal double-click works. Or skip that file altogether and
run `bin/bdtools make-launcher` — the app it generates is not quarantined, so it
opens on the first double-click.

**I copied `Open Dashboard.command` to my Desktop and now it does nothing.** That
file only works from inside the repo folder: it starts the dashboard from whatever
folder it is sitting in. `bin/bdtools make-launcher` builds a launcher that can be
moved anywhere, because the install location is written into it.

**The launcher app opens nothing and shows no error.** It logs every launch to
`~/Library/Logs/bdtools/dashboard.log` (macOS) or
`~/.local/state/bdtools/dashboard.log` (Linux/WSL); the reason is at the end of
that file. It also raises a dialog when the dashboard fails to answer within 60s.

## How it relates to the tool repos

The tool repos stay independent and individually releasable. This umbrella only
*references* them (by repo + version in `tools.yml`) and provides the shared
install/update/site machinery. See [docs/BUILDING_A_TOOL.md](docs/BUILDING_A_TOOL.md)
for the light contract a tool repo must satisfy to be drivable from here.

## 🎓 Training

New to the suite, or onboarding students? **[docs/TRAINING.md](docs/TRAINING.md)**
is a hands-on, step-by-step walkthrough of every tool using real public data you
can copy-paste straight into each GUI — assemble an influenza genome (IRMA) and
genotype it (GenoFLU), run the two-step vSNP3 SNP workflow on TB isolates and
build a whole-*M. tuberculosis*-complex phylogeny, profile resistance genes
(AMRFinderPlus), assign a sequence type (MLST), and build a reference-free SNP
tree (kSNP). No command-line experience required; each module explains how to run
the tool **and how to interpret its output**.

## 🪟 On Windows? Set up WSL2 first

These tools run on Linux/macOS. On Windows you run them inside **WSL2** (a real
Ubuntu Linux alongside Windows — no dual-boot). If you've never used WSL, the
**[docs/INSTALL_WSL.md](docs/INSTALL_WSL.md)** guide walks you through it from
scratch: what you need, the one-time `wsl --install` (and when admin rights are
required), how to verify it, and where to go next — then you follow the normal
Linux install above.
