# HANDOFF — bioinformatic_diagnostic_tools umbrella

Pick-up notes for a fresh session. Last updated 2026-07-08.

## What this project is

A top-level **umbrella deployment repo** that installs, runs, updates, and
validates the Kapur Lab suite of bioinformatics GUIs across environments. Each
tool stays its own GitHub repo; the umbrella pins versions in `tools.yml` and
drives everything through one CLI: **`bdtools`**.

- **Repo (this dir):** `/srv/kapurlab/tools/bioinformatic_diagnostic_tools`
- **Remote:** `git@github.com:kapurlab/bioinformatic_diagnostic_tools.git` (public), branch `main`
- **CLI:** `bin/bdtools`  (per-user data dir `~/.local/share/bdtools`)
- **All 8 tool repos are public.** `bdtools check-updates` resolves over HTTPS.

## Current state (synced to origin/main)

```
bdtools list | status
bdtools install <tool|all> [--local | --sandbox | --server] [--with-dev] [--no-dashboard]
bdtools local  <tool> [--port N] [--no-browser]
bdtools dashboard [--port N] [--restart | --stop]      # local landing page
bdtools setup-databases [--home|--shared|--root DIR] [DB ...]   # download + wire ref DBs
bdtools doctor [tool ...]                              # runtime readiness + fixes
bdtools lint   [tool ...]                              # (maintainer) dependency-drift check
bdtools test   <tool|all> [--record] [--keep]          # post-deploy validation
bdtools check-updates | update <tool|all>
bdtools site-init [--site-conf F]                      # bare-metal bootstrap
```

### tools.yml pins
- `vsnp_gui` → **v0.2.1**, `kraken_id_parse_gui` → **v0.1.3**  (others → v0.1.1)
- suite tags: `suite-2026.06`, `suite-2026.06.1`

## Session 2026-07-08 — committed the env-refresh / bracken-macOS work (on main, pushed)

The Session 2026-06-17(b) item #6 ("Env-refresh path + platform-aware deps") had
been written but left **uncommitted** in the working tree — the earlier handoff
described it as pushed when it wasn't. This session reviewed that diff (a
correctness + security pass via subagents; security clean, no functional bugs),
applied two small polish fixes, and committed + pushed the whole thing:
- `install-local.sh --rebuild` → `conda env update` for existing envs;
  `check-updates.sh` (hence `bdtools update`) now passes it.
- `cmd_install` post-doctor rebuild offer (TTY prompt / non-TTY `ACTION:` line).
- `requirements.py platform_unavailable: {macos: [bracken]}`; `check.py` reports
  such binaries as **notes**, not rebuild-fixable issues; dashboard shows a muted
  note line.
- Polish: `check.py` OK line says "other programs on PATH" when a binary is
  platform-noted (avoids a contradictory ✓); the rebuild-detection heuristic in
  `bdtools` now matches `"bdtools update"` (not the bare substring `"update"`) so
  a DB fix that happens to say "update" can't wrongly trigger an env rebuild.

**Open (unchanged):** the kraken GUI still calls `bracken` unguarded on macOS —
the doctor/dashboard now handle it gracefully, but the GUI itself will still fail
the pie-chart step (spawned task). Next natural piece of work.

## Session 2026-06-17 (b) — install robustness + reference databases (on main, pushed)

Triggered by a real macOS failure (`ModuleNotFoundError: humanize` in
kraken_id_parse_gui): an install can "succeed" yet not run. Built a full chain so
a missing dependency / un-set-up database is caught and explained, not hit as a
mid-run traceback. Cross-platform (macOS / Linux / WSL / OOD).

1. **`bdtools setup-databases`** (`bin/setup-databases.sh` + `bin/lib/db_config.py`).
   Downloads the large third-party reference DBs and wires each GUI's config to
   them. Prompts **Home (`~/databases`) / Shared (`/srv/kapurlab/databases`) /
   Custom**, editable + writability-prechecked. DBs:
   - Kraken2 `k2_standard_08gb` → `<root>/kraken2/k2_standard_08gb` → kraken_id_parse_gui `kraken_db`
   - BLAST `ref_prok_rep_genomes` (via `update_blastdb.pl`) → `<root>/blast/...` → `blast_db`
   - USDA `vSNP_reference_options` → `<root>/vsnp3/reference_options` → vsnp_gui ref root
   - USDA `vsnp3_test_dataset` → its `vsnp_dependencies` → `<root>/vsnp3/vsnp_dependencies`
   Chosen root persisted to `BDTOOLS_HOME/db-root`; `build_vsnp_local` adopts it
   (no second clone). A local install offers setup at the end (TTY prompt).
   The curated Step-2 VCF databases (`mtbc0_v1.1`) stay **lab-private**, not shipped.

2. **`bdtools doctor`** (`bin/doctor.sh` + `bin/lib/check.py`, contract in
   `bin/lib/requirements.py`). Per-tool readiness: env built, modules import (in
   the tool's own env), programs on PATH, databases present (config value or the
   tool's computed default). Plain-language ✓/✗ + the exact fix under each ✗.
   Runs at the end of install; `--json` feeds the dashboard. `ksnp_gui` carries
   `os: linux` → SKIPs on macOS.

3. **Dashboard readiness badges** (`bin/dashboard.py`). Calls `doctor --json`;
   installed-but-not-runnable tools show a **"needs setup"** badge with the
   missing piece + fix command and a Re-check button (for non-CLI users).

4. **`bdtools lint`** (`bin/lint.sh` + `bin/lib/lint.py`). Maintainer/pre-release
   guardrail: statically diffs each tool's imports + invoked programs against its
   declared deps. `✗` = high-confidence drift (fails gate), `!` = advisory.
   **Run before tagging any release.**

5. **kraken_id_parse_gui env fixes (→ v0.1.2, then v0.1.3).** environment.yml was
   missing runtime deps that prod had but a fresh env didn't: v0.1.2 added
   `humanize` + the pipeline binaries (seqkit/blast/bwa/spades/bracken/picard/
   freebayes/vcflib/parallel/pigz); v0.1.3 added `pysam`, `pyyaml`, `svgwrite`,
   `cairosvg`, `pillow` (found by `lint`). `playwright` is optional (guarded
   fallback). Known dead Kraken1 ref `dvl_krakenreport2krona.sh` is a lint
   advisory only — flagged for removal (spawned task; not Kraken2-relevant).

6. **Env-refresh path + platform-aware deps (the bracken/macOS case).**
   - `install-local.sh --rebuild` runs `conda env update` so an EXISTING env
     picks up newly-declared deps (a plain build skipped when env present — why a
     stale env never got `humanize`). `bdtools update <tool>` now passes
     `--rebuild`, so its "rebuilds the env" promise is finally true.
   - `cmd_install` post-doctor: offers to rebuild any env with a **fixable** gap
     (TTY prompt / non-TTY ACTION line). Re-checks after.
   - **`bracken` is platform-unavailable on macOS**: its osx-64 conda builds
     (≤2.6.1, py≤3.8) are incompatible with the env's python 3.10, so it can't
     install on Apple Silicon (linux-64 has 3.x). `requirements.py`
     `platform_unavailable: {macos: [bracken]}` → doctor/dashboard report it as a
     NOTE (not a rebuild-fixable ✗), so the suite stops nagging `bdtools update`
     (which can't fix it). Dashboard shows a muted "⚠ … not available on macOS"
     line. **Open:** the kraken GUI still calls bracken unguarded → flagged
     (spawned task) to skip the Bracken/pie-chart step gracefully on macOS.

### Gotcha — interactive prompts only fire on a TTY
`setup-databases`' location prompt and `cmd_install`'s database / env-rebuild
prompts are gated on `[[ -t 0 && -t 1 ]]`. A Claude agent runs `bdtools` as a
non-TTY subprocess, so those prompts are **skipped** — the non-TTY branches print
explicit `ACTION:` lines instead, and AGENTS.md (§3) makes the agent responsible
for asking the user + running `setup-databases`/`update`. README install uses
plain `claude "…"` (interactive, approve each step); **`-p` does NOT work** for
install (headless, can't answer permission prompts).

## Major features added in the prior session (all on main, pushed)

1. **Validation suite — `bdtools test`** (`bin/test.sh` + `tests/`). All 7
   diagnostic GUIs have recorded golden results, validated on wgs3 (ncbi_submit
   is not tested by design):
   - mlst_gui — E. coli MG1655 `GCF_000005845.2` → scheme ecoli, ST 10
   - amr_plus_gui — K. pneumoniae HS11286 `GCF_000240185.1` → blaKPC/SHV/CTX-M/rmtB
   - irma_gui — flu-A `SRR39145037` → FLU, H5N1, 8 segments
   - genoflu_gui — cattle H5N1 (8 GenBank segs PP755669–76) → genotype B3.13
   - ksnp_gui — 3 Listeria genomes → snps_all ~44309 (**Linux only**, `requires_os: linux`)
   - kraken_id_parse_gui — MTB `SRR28623786` → genus Mycobacterium (tier-2, Kraken2 DB)
   - vsnp_gui — M. bovis `SRR1791695` → ref Mycobacterium_AF2122 + spoligotype SB0673 (tier-2)
   - Adapters in `tests/lib/` (amr_summarize.py, kraken_top.py, vsnp_excel.py) reduce
     non-JSON output to comparable JSON. Spec keys: `tier, fetch (genome|genomes|
     genbank|sra), accession, run_cmd, result_file, db_check (list, {tooldir}),
     requires_os`. See `tests/README.md`.

2. **Local dashboard** (`bin/dashboard.py`, `bdtools dashboard`). Landing page
   listing installed GUIs; click → launches that tool's uvicorn on a free port +
   opens it. `install` (local) ends by printing the access point and auto-opens
   the dashboard (TTY). `--restart`/`--stop` manage the running servers (no more
   pkill). Double-click launcher: `Open Dashboard.command`. Logs:
   `~/.local/share/bdtools/dashboard-logs/`.

3. **Personal-computer installs work on macOS / Linux / WSL.**
   - Apple Silicon: envs built **osx-64 under Rosetta 2** (bioconda lacks native
     arm64 builds; auto via CONDA_SUBDIR; needs Rosetta — installer tells you).
   - bash 3.2 (macOS default): empty-array expansions hardened with
     `${arr[@]+"${arr[@]}"}`.
   - SRA tests: `fetch_sra` finds sra-tools on PATH or in any conda env, SKIPs if
     none (install: `conda install -n base -c bioconda sra-tools`).

4. **vsnp_gui now installs + runs locally** (it was OOD-only). `build_vsnp_local`
   in `install-local.sh`: bioconda `vsnp3` env + web layer + Kapur Lab patches +
   downloads USDA-VS reference_options (~320 MB) to `~/.local/share/bdtools/
   vsnp3-refs/`, and lays out a **local site root** `~/.local/share/bdtools/
   vsnp3-site/` (refs + vcf_db_folders + a `tools/vsnp3 -> env` symlink). `launch()`
   exports `VSNP_GUI_SITE_ROOT` to it and **self-heals** a stale
   `~/.config/vsnp_gui/config.json` (the GUI froze /srv paths on first load).
   The conda vsnp3 package ships the sourmash best-reference index, so auto
   species-ID works; the curated Step-2 VCF databases are lab-private (not shipped).

5. **vsnp_gui v0.2.1 — reference-location fix.** `provenance_writer.py
   capture_reference_state()` now resolves the selected reference across **all**
   registered reference locations (the configured root + any added via the GUI
   "Reference Locations"), not just `vsnp3_reference_options_root`. Before, a
   reference in an added folder (e.g. a downloaded vsnp3 test dataset's
   `vsnp_dependencies`) failed Step 1 with "reference folder not found". Users
   update with `git pull && bdtools update vsnp_gui` (in-place, env preserved).

## Known limitations / open items

- **ksnp_gui does NOT run on macOS** — kSNP4 ships Linux-only ELF binaries (Rosetta
  doesn't run Linux ELF). Test SKIPs on macOS; install warns. **Follow-up task
  flagged** (spawn_task `task_a8bca10d`): vendor the macOS kSNP4 build in ksnp_gui,
  select per-OS, then drop `requires_os: linux` + the Linux-only notes.
- **kraken_id_parse_gui** needs a Kraken2 DB (~8 GB) — realistically OOD/Linux for
  the DB; SKIPs cleanly on a laptop without it.
- **vsnp_gui IGV/FigTree** are OOD-virtual-desktop features — won't launch in local
  mode (no X). Step 1/Step 2 pipelines + results work locally.
- `mtbc0_v1.1` is a lab-custom combined-MTBC reference, not in the public USDA set.
  Local users use Mycobacterium_H37 / _AF2122 / _orygis, or add their own folder
  under Reference Locations (works with v0.2.1+).

## How users get updates (the painful-but-correct path)
On the Mac/Linux client checkout:
```bash
git pull                       # (git stash && git pull if it complains about local edits)
bdtools update <tool>          # moves the checkout to the new pinned tag, env preserved
bdtools dashboard --restart    # closing the browser does NOT stop servers
```
The helper-agent's first macOS run left hand-edits in bin/*; `git stash` (or
`git reset --hard origin/main`) clears them — the same fixes are upstream.

## Quick verify (on wgs3)
```bash
cd /srv/kapurlab/tools/bioinformatic_diagnostic_tools
bin/bdtools list
BDTOOLS_TOOLSDIR=/srv/kapurlab/tools bin/bdtools test all     # PASS the 7; ncbi SKIP
```
Note: a throwaway local vsnp build exists at `~/.local/share/bdtools/checkouts/
vsnp_gui` (+ vsnp3-site, vsnp3-refs) from testing — harmless, in the per-user dir.

## Remaining from the original plan (not blocking use)
- `gh auth login`, GitHub Releases (`bin/make-releases.sh`), a real `--server`
  install on a test box.
- Promote `ood-core/bootstrap_ood_core.sh` into the umbrella.
