# Handoff — Results pane rollout, kSNP4 + AMR fixes, site-path removal

Session: 2026-07-25 → 26. Owner: tks5563. (Development record, kept in-repo.)
Supersedes the 2026-07-23 provenance/dashboard handoff.

## TL;DR

- **All 9 tools released, tagged, pushed and pinned.** Suite `2026.07.26`.
  Everything below is live on this server and installable anywhere via the pins.
- **Every non-vSNP GUI now has the vSNP-style Results pane** and a Projects
  check-all. This was the main ask and it is done.
- **Three real bugs found and fixed by testing against live projects**, each of
  the same shape: *a failed run reported as success*. See §3 — one of them needs
  an operational decision from you.
- **Four items from the original list are NOT done** — training modules,
  citations, the NCBI page, and the remaining hard-coded paths. See §5.

| tool | tag | tool | tag |
|---|---|---|---|
| vsnp_gui | v0.4.34 | ksnp_gui | v0.3.0 |
| amr_plus_gui | **v0.3.0** | genoflu_gui | v0.3.0 |
| mlst_gui | v0.3.0 | irma_gui | v0.3.0 |
| kraken_id_parse_gui | v0.2.0 | ncbi_submit_gui | v0.2.0 |
| mhc_gui | v0.2.0 | | |

---

## 1. The Results pane (the main ask)

Modelled on vSNP Step 1 Results. Every tool now shows **every completed sample
(or run)** in one table — not just whichever one you last clicked:

- QC chip (PASS / REVIEW / FAIL with the reason), status, run date
- per-row **Files** accordion, including sibling-tool output — a Kraken **Krona**
  chart lives under `<project>/kraken/`, outside the tool's own run dir, which is
  exactly why it appeared in Projects but never in Results
- filter by name, run-date range (Today / Last 7d / Last 30d), show-only-flagged
- **CSV / XLSX export carrying the active filters**, so a download matches screen
- **"Current Run"** keeps its live batch status and sits *beside* the table

**Projects check-all**: tri-state checkbox + sample filter. It acts **only on
rows the filter leaves visible** — a select-all that also queues hidden samples
is how someone runs 900 samples instead of the 3 they filtered to. Applied to
amr, mlst, kraken, genoflu, irma. ksnp and mhc already had a Select-all; ncbi is
run-centric with no per-sample selection.

### How it is shared — read this before editing it

`frontend/src/{ResultsPane.jsx,useResults.js,ResultsPane.css}` are **vendored
byte-identically** into all 8 tools. **Source of truth: `amr_plus_gui`.**

Not an npm package, deliberately: each repo is cloned standalone from its own URL
and pinned to its own tag, so there is no monorepo root at install time — a
shared package would add a second, uncoordinated version axis on top of the tag
pins. The price of copying is drift, so:

```bash
bin/check-shared-frontend.sh      # proves all copies match; names the exact cp on drift
```

`vsnp_gui` is excluded — it is the model this was ported *from* and has its own
native implementation on a different stylesheet (`styles.css`, not `App.css`).

**Every class is `rp-` prefixed and `App.css` is never touched.** `App.css` is
copied verbatim across tools and marked "do not restyle", and `mhc_gui` already
defines `.qc-badge` / `.qc-pass` / `.qc-review` / `.qc-fail` with *different*
meanings — an unprefixed port would silently restyle its genotype table.

To change the pane: edit in `amr_plus_gui`, `npm run build`, re-copy to the other
7, rebuild each, re-tag each, bump pins. Run the check script before releasing.

**Build requirement:** vite 8 needs **Node ≥20.19**; this server has Node 18. A
scratch env was used (`conda create -p <dir> -c conda-forge 'nodejs>=22'`). Without
it `npm run build` fails and you silently ship a stale `dist/`.

---

## 2. kSNP4 was unfindable — fixed

**Symptom:** `WARNING: Kchooser4 not on PATH`, `ERROR: command not found: kSNP4`,
rc 127 — and the pipeline still wrote a report saying "Pipeline completed".

**Cause:** `.bdtools-tools-dir` pointed at `/tmp/bdtools-robust-worktrees`, so
tools launched from throwaway git worktrees. kSNP4 is a 545 MB SourceForge zip in
`ksnp_gui/vendor/`, which is **gitignored** — so the worktree had an empty
`vendor/` while two real copies sat in `/srv` and in the installed checkout.
`tool_launch.py` prepended the nonexistent path to `PATH` with no existence check.

**Fixed:** `_resolve_asset_dir()` resolves each `path_prepend` against the tool
tree → the installed checkout → a machine-wide vendor cache, warns loudly when
nothing resolves, and exports `BDTOOLS_MISSING_ASSETS`. `bdtools doctor` now
declares kSNP4/Kchooser4/MakeKSNP4infile and searches `vendor/` (it used to
report ksnp_gui green while the tool could not run at all).

⚠️ **`.bdtools-tools-dir` has been cleared.** Tools now run from the installed
checkouts. `bdtools dashboard --tools-dir none` clears it if it comes back.

---

## 3. Three "failed run reported as success" bugs

Same shape each time, and worth watching for elsewhere.

### 3a. kSNP — fixed
`ksnp_pipeline.py` logged a warning on rc 127 and carried on to write
`run_manifest.json` and `report.pdf`. Now: a preflight aborts before any output
exists, rc 127 is fatal, and a zero-SNP result with ≥2 genomes fails.

### 3b. AMRFinderPlus database version mismatch — **needs your decision**
Both samples in `AMR_training` showed a green "✓ results" badge having produced
nothing. `amrfinder` exited 1 with:

> The BLAST database for AMRProt was not found. Use `amrfinder -u` to download

That message is misleading — the database is present and correct. The real cause:

| | |
|---|---|
| shared DB | `/srv/kapurlab/databases/amrfinderplus/latest` → **format 4.2.0** (`AMRProt.fa`) |
| installed binary | **amrfinder 3.12.8**, reads only format 3.x (`AMRProt`, no extension) |

`_is_valid_amrfinder_db()` now compares the two majors, refuses a mismatched DB,
and prints the actual remedy instead of passing `-d` and failing opaquely.

**No AMR run on this server will produce calls until one side moves:**

```bash
conda install -n amr_plus 'ncbi-amrfinderplus>=4'
```

That is the cleaner fix (keeps the current database). The alternative is
installing a 3.x-era database. **Not done — your call.**

### 3c. `--mutation_all` without `-O`
AMRFinderPlus warns when `--mutation_all` is passed without `-O/--organism`; it
only screens point mutations, which are per-organism. Now sent only alongside
`-O`. *Correction to something said mid-session: this is a warning, not the cause
of 3b. The database mismatch is the cause.*

**Also:** the Results pane now reads `run_manifest` `return_code`, so a run whose
analysis exited non-zero reads **FAIL** with the reason rather than inheriting a
`qc.json` verdict that only graded the *input*.

---

## 4. Hard-coded paths — pattern established, mostly not applied

You raised this twice; the second time made clear the bar is **no hard-coded
paths at all**, not "demoted to a last-resort fallback". That is the right bar.

**Why it matters, concretely:** `amr_plus_gui` resolved its sibling MLST runner as
`/srv/kapurlab/tools/mlst_gui`. Off this server that path does not exist, so the
organism cross-check was **silently skipped** on every Mac and WSL box, with a log
line that read like an expected state.

**New: `bin/lib/site_paths.py`** resolves shared-projects root, database root and
tools root from (1) an env var, (2) the machine's recorded site config, (3) a
defensible derivation — and otherwise reports *absent*. It contains no path of its
own (a test asserts this). `sites/site.conf.example` always called itself "the ONE
place site-specific values live" but was only read at *install* time to rewrite
OOD files; `install-server.sh` and `setup-databases.sh` now record the resolved
values to `<BDTOOLS_HOME>/site.conf`, and `tool_launch` exports them into every
tool (and into the reproduce command).

**This server's values are recorded** in `~/.local/share/bdtools/site.conf` —
that is why removing the literals changed nothing here.

**Status: `amr_plus_gui` is at ZERO. The other 8 are not converted.** A ratchet
test (`test_hardcoded_site_paths_do_not_grow`) holds each tool's count so it can
only go **down**:

| tool | literals | tool | literals |
|---|---|---|---|
| vsnp_gui | 24 | genoflu_gui | 3 |
| mhc_gui | **11** (6 under `/home/vxk1`) | irma_gui | 3 |
| kraken_id_parse_gui | 4 | mlst_gui / ksnp_gui / ncbi_submit_gui | 2 each |

**`mhc_gui` is the urgent one**: six paths under `/home/vxk1` (rclone config,
barcode map, two conda envs). That account may not exist elsewhere and its home is
unreadable to other users, so the tool is not usable by anyone else on a shared
box. Those need real config keys, not fallbacks.

The conversion pattern is `amr_plus_gui` v0.2.9 — see that commit.

---

## 5. NOT DONE from the original list

Stated plainly so nothing is assumed finished:

1. **No Kraken training module.** `docs/TRAINING.md` still has 7 modules covering
   6 tools. Kraken appears only as a supporting actor — including lines 345-362,
   an embedded mini-tutorial for a tool the guide never introduces. It should slot
   in as a new Module 3 (before vSNP3), with a
   `### Choosing and adding a Kraken2 database` section pointing at
   **https://benlangmead.github.io/aws-indexes/k2** and explaining the index
   families and the size/RAM trade-off.
   - Two real gaps to fix while there: `GET /api/kraken-dbs`
     (`kraken_id_parse_gui/backend/app/main.py`) already scans installed indexes
     and its docstring says it is "for the settings dropdown", but **the frontend
     never calls it** — both surfaces are free-text boxes. And
     `setup-databases.sh` only ever fetches `k2_standard_08gb`, while
     `amr_plus_gui`'s docs call for **PlusPF**.
2. **No citations in any GUI.** Zero tools have a `<footer>`. `.note` in the
   shared `App.css` (12px, `var(--muted)`) is the right style and needs no new
   CSS; `ISO_REFERENCES` in each pipeline is the existing precedent to hang a
   `CITATIONS` list beside. ⚠️ The one existing citation contradicts itself —
   `vsnp_gui/README.md` says *BMC Genomics* 2024;25:**545**, its docs say
   25(1):**548** (same PMID 38822271). **Resolve before propagating to nine
   footers.**
3. **No `docs/NCBI_SUBMISSION.md`.** The tool is more complete than its label
   suggests — prep → validate → build `submission.xml` → FTP → poll is
   implemented with zero TODOs; dry-run defaults **true** and target defaults
   **test**. What is missing is the *page*: account setup, requesting
   programmatic FTP access, BioProject/BioSample description writing, the SRA and
   GenBank walkthroughs, and who to email when something goes wrong
   (`gb-admin@ncbi.nlm.nih.gov`, `sra@ncbi.nlm.nih.gov`,
   `biosamplehelp@ncbi.nlm.nih.gov`). Two facts to include: the tool **references
   but does not create** a BioProject (make `PRJNA…` at the portal first), and it
   uses **plain `ftplib.FTP`, not FTPS**.
4. **MHC not tested or documented.** DRB3 is validated; Class I is provisional and
   gated off by default. `docs/HANDOFF.md` in that repo is stale (calls
   `run_classI()` a stub — it is implemented). Its `CLAUDE.md` is a verbatim
   un-edited `amr_plus_gui` copy that documents `amrfinder` and
   `organism_map.yaml`; `config/` still holds amr leftovers. Fix those before
   writing docs against them.

---

## 6. Operational notes

- **Dark mode is live in all 9 tools.** The theme work existed on an unmerged
  `codex/robust-dashboard-ops` branch, never pushed, never tagged — which is why
  the dashboard was dark and the tools were light. It is now on `main` everywhere
  and pinned. Sharing one choice across tools requires the **single-port proxy**
  dashboard (same origin); the legacy fallback gives each tool its own port, so
  the theme is per-tool there.
- **The `?t=…` URL is not a bug.** `_dashboard_wants_token()` exempts macOS and
  WSL and defaults **on** everywhere else. Same code, different `uname`. Override
  with `BDTOOLS_DASHBOARD_AUTH=0|1`. The banner now explains itself in both
  directions; see `docs/INSTALL_LOCAL.md`.
- **Managed checkouts are shallow with a pinned fetch refspec**
  (`+refs/tags/v0.1.2:refs/tags/v0.1.2`), so a plain `git fetch origin` updates
  nothing. `bdtools update` works because `check-updates.sh` fetches an explicit
  target ref. Use `git fetch origin 'refs/heads/main:refs/remotes/origin/main' --tags`
  when working in a checkout by hand.
- **`bdtools doctor`** is green except vsnp_gui's reference-options path, which is
  a pre-existing local-install issue, unrelated to this work.
- `/srv/kapurlab/tools/<tool>` still sit on the divergent
  `add-noncommercial-license` branch. The LICENSE lives only there and on the open
  PRs; `main` has no LICENSE. Deliberately untouched — pending PSU OTM review.

## 7. Verify after pulling elsewhere

```bash
bin/bdtools update all
bin/check-shared-frontend.sh
bin/bdtools doctor
bin/bdtools dashboard          # open a tool -> Results pane lists completed samples
```
