# Handoff — Results pane rollout, kSNP4 + AMR fixes, site-path removal

Session: 2026-07-25 → 27. Owner: tks5563. (Development record, kept in-repo.)
Supersedes the 2026-07-23 provenance/dashboard handoff.

## TL;DR

- **All 9 tools released, tagged, pushed and pinned.** Suite `2026.07.27`.
  Everything below is live on this server and installable anywhere via the pins.
- **Every non-vSNP GUI now has the vSNP-style Results pane** and a Projects
  check-all. This was the main ask and it is done.
- **Three real bugs found and fixed by testing against live projects**, each of
  the same shape: *a failed run reported as success*. See §3 — one of them needs
  an operational decision from you.
- **Three items from the original list are NOT done** — training modules, the
  NCBI page, and the remaining hard-coded paths. See §5. (Citations were §5.2 and
  are now done — §7b.)
- **2026-07-27, released:** kSNP4 now installs and runs on macOS (it was wrongly
  treated as Linux-only suite-wide), and all 9 GUIs carry a citation footer.
  See §7 — read §7a before touching `vendor/` on a Mac.

| tool | tag | tool | tag |
|---|---|---|---|
| vsnp_gui | v0.4.35 | ksnp_gui | **v0.4.1** |
| amr_plus_gui | v0.3.1 | genoflu_gui | v0.3.1 |
| mlst_gui | v0.3.2 | irma_gui | v0.3.1 |
| kraken_id_parse_gui | v0.2.1 | ncbi_submit_gui | v0.2.1 |
| mhc_gui | v0.2.1 | | |

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
2. ~~**No citations in any GUI.**~~ **DONE 2026-07-27** — see §7. All 9 GUIs end
   with a "How to cite" footer (bdtools + the upstream tool's paper), shared
   byte-identically as `Citations.jsx`/`Citations.css`. The 545-vs-548
   contradiction was resolved first: Europe PMC for PMID 38822271 gives page
   **545**, so 548 was the error, and `docs/DOCUMENTATION_INDEX.md` had also
   credited the wrong first author (Hicks, not Stuber). Both fixed in `vsnp_gui`
   before the reference propagated anywhere.
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

## 7. Session 2026-07-27 — kSNP4 on macOS, and the citation footers

Two items, both **released**: all 9 tools tagged and pushed, `tools.yml` pins and
`suite_version` bumped to `2026.07.27`.

### 7a. kSNP4 is not Linux-only, and "on PATH" is not "runnable"

A macOS run of 16 MTBC genomes died 0.4 s in with
`OSError: [Errno 8] Exec format error: 'MakeKSNP4infile'`.

SourceForge publishes a **kSNP4.1 Mac package** (Mach-O x86_64, fine under
Rosetta 2) next to the Linux one. The suite assumed Linux-only, so:
`deploy/install.sh` hard-coded the Linux URL; `install-local.sh` passed
`--skip-ksnp` off Linux; `requirements.py` had `os: linux`, which made **doctor
SKIP ksnp_gui on macOS** — so the one check that could have caught a wrong-OS
payload never ran on the hosts carrying one. `tests/ksnp_gui/test.yml` had
`requires_os: linux` for the same reason.

Underneath all of it, one mistake repeated in three places: every readiness check
asked `shutil.which(tool) is not None`. That answers "is there a file with this
name on PATH", not "can this host exec it". The Linux payload satisfies it.

**Where kSNP4 runs, definitively.** Both published packages are **x86_64 only**,
so the OS is not the whole story:

| host | verdict |
|---|---|
| x86_64 Linux (incl. WSL2 on Intel/AMD) | ✅ Linux package |
| Intel Mac | ✅ Mac package |
| Apple Silicon Mac | ✅ Mac package via Rosetta 2 |
| **ARM Linux** (WSL2 on Windows-on-ARM, Graviton) | ❌ **no package exists** |
| Apple Silicon **without** Rosetta 2 | ❌ until Rosetta is installed |

The first version of this fix only compared ELF-vs-Mach-O, which would have let
the identical bug through on ARM Linux — x86_64 ELF passes an "is it ELF" test and
then fails at exec with no translation layer to save it. The check now reads the
CPU field too (ELF `e_machine`, Mach-O `cputype`) and reports those last two rows
as distinct states, because each has a different remedy: ARM Linux is "use another
machine", missing Rosetta is a one-line `softwareupdate` fix. `install.sh` refuses
ARM Linux up front rather than downloading 545 MB that can never run.

Fixed: install picks the package by `uname -s`/`uname -m` and replaces a wrong-OS
payload instead of calling it "already installed"; `ksnp_gui/bin/ksnp_platform.py`
is the one answer to "can kSNP4 run here", shared by the GUI's readiness gate, the
pipeline preflight, **and `deploy/install.sh` via a small CLI** (`check-dir` /
`describe` / `package`) — that logic was briefly duplicated in bash with `od(1)`,
and two implementations of a subtle check is exactly how they drift apart.
`check.py` gained `check_binary_format()` behind a `binary_format_probes` spec key.

⚠️ **A repaired Mac has both archives unpacked in `vendor/`.** The post-unzip
search for the kSNP4 dir is now format-aware — before, "first `kSNP4` that `find`
turns up" could re-link the payload just replaced. The wrong-OS files are left on
disk (545 MB) rather than deleted; `install.sh` says so.

**Second bug, found while verifying the first:** `bdtools local` built the
server's PATH from `<env>/bin` alone and never applied `path_prepend`, while the
dashboard launches through `tool_launch` and does. So `bdtools local ksnp_gui`
served a GUI reporting kSNP4 "not installed" on a **correct** install. `launch()`
now asks `tool_launch` for `PATH_PREPEND` instead of re-deriving it.

Verified on macOS 26.5 / arm64:
- `bdtools doctor ksnp_gui` names the wrong-OS payload before, passes after
- 16 MTBC genomes: 12,057 SNPs, 9,867 core, 27 trees, rc 0
- `bdtools test ksnp_gui`: **PASS**, reproducing the Linux golden exactly
  (snps_all 44309, core_snps 34713) — the gate is now removed so this runs at all
- kSNP4 prints "the output directory is missing some expected files" on the
  3-genome golden set. Output dir is complete and the 16-genome run is clean, so
  it reads as a small-N artifact of kSNP4's own check — **not confirmed against a
  Linux run of the same 3 genomes.** Worth one check next time you're on wgs3.

### 7b. Citation footers — all 9 GUIs

`Citations.jsx` / `Citations.css`, vendored byte-identically (source of truth
`amr_plus_gui`) and now covered by `check-shared-frontend.sh` via a new
`SHARED_ALL` set — **shared with `vsnp_gui` too**, unlike the Results pane: every
class is `cite-` prefixed and the sheet uses only variables both `App.css` and
`styles.css` define, so it drops into either without restyling anything.

Each GUI shows the bdtools GitHub citation plus its upstream paper(s): vSNP3
(Hicks 2024), AMRFinderPlus (Feldgarden 2021) + mlst/PubMLST, IRMA (Shepard
2016), GenoFLU (Youk 2023 — no software paper exists; that is what its README
asks for), mlst + PubMLST/BIGSdb (Jolley 2018), Kraken 2 (Wood 2019) + Krona
(Ondov 2011) + Bracken (Lu 2017), kSNP4 (Hall & Nisbet 2023). `ncbi_submit_gui`
and `mhc_gui` show the suite citation only — the first has no upstream analysis
tool, the second is developmental and should not yet advertise a method citation.

Every reference was checked against Europe PMC or the upstream repo. **Do not add
one from memory** — a wrong volume in a footer propagates into other people's
bibliographies, which is exactly how the 545/548 error survived (§5.2).

### 7d. mlst_gui v0.3.2 — the XLSX export was dead

`bdtools lint`'s one ✗ (openpyxl imported but not declared) was real, and fixing it
uncovered a second defect in the same endpoint: `Response` was missing from the
`fastapi.responses` import, so even with openpyxl installed the final
`return Response(...)` would have raised NameError. The endpoint's own
`HTTPException(501, "Excel export needs openpyxl...")` masked it — that message
reads like a deliberate optional-feature notice, so a packaging bug looked like
intended behaviour, and nobody ever reached the return statement to find the
second one. Worth remembering: a friendly fallback message can hide the bug behind it.

`bdtools lint` is now clean across all 9 tools.

### 7e. Dashboard port handling — two bugs, found by tripping over them

Both were exposed by a self-inflicted mess (a dashboard left backgrounded during
release verification), which is the only reason they surfaced at all.

**1. The port guard never ran on the common path.** `bdtools dashboard` prints its
"Open this in your web browser" banner and *then* lets uvicorn bind. The friendly
"port is already in use but no safe dashboard state record exists" diagnostic sat
inside `if [[ "${mode}" != serve ]]`, so it only fired for `--restart`/`--stop`.
A plain `bdtools dashboard` skipped it and fell through to a raw
`ERROR: [Errno 48] error while attempting to bind` — printed *after* the banner
told the user the dashboard was ready. It reads as "started, then broke" when it
never started.

Now: `_dashboard_port_free` runs on every path, before the banner. If the port
holds *our own recorded* dashboard it says so and prints the URL (wanting "the
dashboard" is already satisfied); otherwise it names the process holding the port
and offers `--port`. A post-loop check also catches a lost bind race, so uvicorn's
bare errno is never the last word under an invitation to open the page.

**2. `--stop` claimed success while a server was still running.** With no state
record it printed "No running dashboard was recorded" and exited **0** — even with
a live dashboard on the port. That is how the port stayed occupied while the user
believed it was free. Now it checks the port, and if something is still listening
it names the PID, explains why it cannot stop it safely (no control token, and a
blind kill can orphan a running analysis), lists the three ways to stop it, and
exits **1**.

⚠️ **The bind test must set `SO_REUSEADDR`.** The first version of this fix did not,
which made it *stricter* than the bind it exists to predict: uvicorn sets
`SO_REUSEADDR` (`uvicorn.config.Config.bind_socket`), so a port in `TIME_WAIT` — the
normal state for a moment after a stop — failed the test while uvicorn would have
bound fine. That turned a fix for a confusing error into a confusing error of its
own. `SO_REUSEADDR` does not permit binding over a live listener, so a real server
is still detected.

⚠️ **And it needs a grace period.** Shutdown is asynchronous: `/api/shutdown`
flushes its response, stops child tool servers, then exits, so for well under a
second after `--stop` returns the old process still holds the socket. Without
`_dashboard_wait_port_free` (5s), the obvious `--stop && dashboard` loses a race
with the process it just stopped and reports a busy port — telling you to stop the
dashboard you already stopped. Both of these were caught by testing, not review.

**Known limitation, pre-existing, now visible rather than silent:** there is one
`dashboard-state.json`, so it describes only the most recently started dashboard.
Run two on different ports and the first becomes unrecorded — `--stop` then
correctly refuses to touch it and tells you to kill it by PID. Fine for the
one-dashboard case that is the norm; worth knowing before running two.

Verified: cold start; stop-then-immediately-start; plain start against our own
running dashboard; plain start against an unrecorded one; `--stop` with and without
a record; `--restart`; `--port 8081` alongside a busy 8080; tool launch + proxy
still 200 after a restart; 23 unit tests pass.

### 7f. tools.yml was blocking every bdtools update — a real deadlock

Reported from the Mac: updating bdtools failed *every time*, naming tools.yml as a
blocking local change, on a file never touched by hand. Not Mac-specific, not a
false positive, and not formatting noise (verified: manifest.py leaves the file
byte-identical when the value is unchanged, and rewrites exactly one line when it
is not). It is a deadlock between two features:

1. `bdtools update <tool>` records the version it moved to by rewriting `version:`
   in tools.yml (`check-updates.sh apply_one` -> `manifest_set`).
2. tools.yml is git-tracked, so that dirties the umbrella checkout.
3. The umbrella self-update is `git pull --ff-only`, which refuses a dirty tree.

So updating any tool made it impossible to update bdtools until you ran
`git restore tools.yml` by hand. It fires on every release; it only *looked*
constant this week because several tags were cut in a row. Bumping the shipped pins
at release time (§7c) narrows the window but cannot close it — between a tag being
pushed and a machine pulling, that machine's `bdtools update` always writes a pin
the pull then trips over.

Fixed in `suite_common.suite_update_command`: when tools.yml is the **only** dirty
file and its diff touches **only** `version:` / `suite_version:` values, it is
`bdtools update`'s own bookkeeping — derived state that origin's manifest
supersedes — so it is restored and the pull proceeds, with both facts logged.
Anything else still refuses: a comment, a repo URL, a new tool entry, or any other
dirty file. Six cases verified, including the three that must keep refusing.

⚠️ **Watch the porcelain parsing.** The first version used `ln[3:]` to read the
path out of `git status --porcelain`, but the caller `.strip()`s the whole block for
display, which eats the leading space of the first line (`" M f"` -> `"M f"`) and
shifts every column — so it read `"ools.yml"` and never matched. `_dirty_paths`
parses instead, and is unit-checked against that stripped-first-line shape.

If this ever needs a deeper fix, the honest one is to stop storing per-machine pin
state in a git-tracked file at all (a local override alongside `dashboard-state.json`),
leaving tools.yml purely as the shipped manifest. Not done — larger change, and
`install --server` reads the pin.

### 7g. kSNP GUI layout — ksnp_gui v0.4.1

Projects sat in a two-column grid whose right column stacked Inputs + Sample
Metadata + Genomes-selected. `.row-grid` is `align-items: stretch` with
`.row-grid > .panel { height: 100% }`, so Projects was stretched to the height of
all three — a mostly empty left column beside two wide list panes crammed into a
1.4fr column. Both panes now sit below the grid as full-width rows; Projects only
has to match Inputs (552x680 vs 772x680 at 1440px). Genomes-selected gained its own
Hide button.

Done with **no App.css change** — `.layout` is already a flex column, so a panel
placed directly in it is full width. App.css is copied verbatim across the suite and
marked do-not-restyle, so fixing this in CSS would have restyled all nine tools.

### 7c. Released — and how it reaches other machines

All 9 tools were fast-forwarded onto `main` and tagged; `tools.yml` pins and
`suite_version` are bumped to `2026.07.27`; the umbrella is tagged
`suite-2026.07.3`.

**Two halves, and both are needed here.** `bdtools update <tool>` picks the
highest `v*` tag off each tool's remote — so the tool changes travel by tag. But
the doctor format/arch check and the `bdtools local` PATH fix live in the
*umbrella*, so a machine that updates only its tools gets the new ksnp_gui and the
old doctor. Update **bdtools first, then the tools** — the dashboard's Updates
panel lists bdtools as its own row for exactly this reason.

Two things that block an update, both by design:
- the umbrella self-update is `git pull --ff-only` and **refuses a dirty
  checkout**. The pins are bumped in this commit precisely so `bdtools update`
  finds `latest == pinned` and never rewrites `tools.yml` under the user, which is
  what would dirty it.
- `bdtools update <tool>` **refuses a tool checkout with local source changes**
  (it tolerates only `frontend/dist/*` and `frontend/package-lock.json`).

After updating, **`bdtools dashboard --restart`** — the running dashboard and tool
servers keep serving old code until restarted.

**The OOD server does not use `bdtools update`** (it refuses external checkouts on
purpose). wgs3 goes through `bdtools install --server <tool> --site-conf ...`,
which checks out the `tools.yml` pin — so the pin bump in this commit is what
carries the release there. Use `--dry-run` first.

## 8. Verify after pulling elsewhere

```bash
bin/bdtools update all
bin/check-shared-frontend.sh
bin/bdtools doctor
bin/bdtools dashboard          # open a tool -> Results pane lists completed samples
```
