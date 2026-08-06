# Handoff — dashboard restart, desktop launcher, version visibility, cross-platform stability

Session: 2026-08-06. Owner: tks5563. (Development record, kept in-repo.)
Supersedes [docs/HANDOFF_2026-07-27.md](docs/HANDOFF_2026-07-27.md), which is archived
rather than deleted — it holds the macOS kSNP4 work, the citation footers, and the
"five guards that protected the wrong thing" analysis this session extends.

## TL;DR

Everything below is **released, tagged, pushed and pinned**. Suite `2026.08.04`.

| tool | tag | tool | tag |
|---|---|---|---|
| vsnp_gui | **v0.4.37** | ksnp_gui | v0.5.0 |
| amr_plus_gui | **v0.3.3** | genoflu_gui | v0.3.2 |
| mlst_gui | v0.3.3 | irma_gui | v0.3.2 |
| kraken_id_parse_gui | v0.2.3 | ncbi_submit_gui | v0.2.2 |
| mhc_gui | v0.2.2 | | |

**What shipped**

- **The in-browser Restart no longer hangs.** Root cause measured, not guessed: the
  outgoing dashboard keeps answering for up to 10.5 s, and the page accepted that as
  "we're back". §1.
- **A real desktop launcher** — `bdtools make-launcher` builds a macOS `.app` (icon, no
  Terminal window, no Gatekeeper prompt) or a Linux/WSL menu entry, movable anywhere. §2.
- **You can see which analysis package versions produced a result**, per tool, on every
  card — and be told when a newer one exists. `bdtools versions`. §3.
- **Cross-platform stability is now enforced, not documented.** A package only moves to
  a version installable on *every* deployed platform. §4.
- **"Cannot be done" stopped being reported as an error.** §4.
- **One env per analysis tool** is now the rule, with the plumbing to follow it. It lifted
  AMRFinderPlus from 3.12.8 — which cannot read the deployed database at all — to 4.2.7. §5.
- **conda builds survive a strict ambient shell**, so a failed env build can self-heal
  instead of failing identically twice. §6.
- **IGV no longer refuses a reference GFF it just handed out** (a new user's viewer failed
  to load entirely). §7.

**Read §8 before trusting anything I claimed.** Four of this session's bugs were mine,
including one that blanked the dashboard twice. What prevents each recurrence is listed.

**Still open — §9.** The amr_plus assembler divergence needs a decision from you.

**§10 is a follow-up fix**: the update banner kept offering packages a completed run
had just established cannot be installed here, so the dashboard looked as though the
update had never happened. It now goes quiet, and the held versions are reported on
the cards instead.

---

## 1. The Restart hang

Restart is two processes handing one port over, and the page could not tell them apart.

Measured on wgs3: after `POST /api/restart` the **old** process keeps answering
`/api/info` with 200 for **~10.5 s** while it stops tool servers (`stop_backends` allowed
a 10 s SIGTERM grace, awaited unbounded). The page treated the first 200 as "the new
dashboard is up" and reloaded 0.7 s later — into a server with 8 s left to live. A reload
landing in the changeover destroys both the page and the script that was retrying, which
is why it presented as a permanent hang.

Contract now, in both dashboards (they share `dashboard.py`'s `PAGE`):

- `/api/info` returns `boot_id` (pid + start ms) and **503 once the process is exiting**.
- The restart poller waits for `boot_id` to **change**, not for any 200.
- Exit is bounded: 3 s SIGTERM grace inside a 6 s budget, then exit regardless — an
  unreapable SIGKILLed child (network volume) must not strand a restart.
- The supervisor waits for the port to be **bindable** (30 s) before relaunching. macOS
  and Linux differ on rebinding, and losing that race used to end the loop silently,
  which from the browser is indistinguishable from a hang.

Result: 10.9 s → **4.0 s** with a backend that ignores SIGTERM; ~1 s normally.

A wedged tool makes Restart return **409** (`active or unverifiable analyses`) as an
alert — a different failure, not this one.

## 2. `bdtools make-launcher`

`Open Dashboard.command` is opened *by* Terminal.app (hence the window), and locates the
suite with `cd "$(dirname "$0")"` — so a copy on the Desktop silently does nothing.

`bdtools make-launcher` generates the platform's native thing instead:

| platform | output |
|---|---|
| macOS | `Kapur Lab Dashboard.app` — icon, **no Terminal window**, not quarantined so no right-click→Open |
| Linux | applications-menu entry + hicolor icons (`--dest ~/Desktop` for a trusted desktop copy) |
| WSL | the same entry (WSLg surfaces it in the Start Menu) + a `.ico` for a native shortcut |

- The install path is **baked in at generation time**, so it survives being moved to the
  Dock, Desktop or `/Applications`. Re-run after moving or reinstalling.
- The dashboard is started **detached**: quitting the launcher or Cmd-Q can never
  interrupt a running analysis. Stopping stays with the browser's **Shut down**.
- Every launch logs to `~/Library/Logs/bdtools/dashboard.log` (macOS) or
  `~/.local/state/bdtools/dashboard.log`, and a dashboard that never answers raises a
  dialog naming that log — silence is the one failure mode a double-click cannot afford.
- Icons: `bin/make-icons.py` (needs Pillow — run it with a tool env python) draws one
  master and emits `.icns` + sized PNGs + `.ico` into `templates/launcher/icons/`. The
  derived files are **committed**, so no user machine needs Pillow.
- `BDTOOLS_LAUNCHER_PLATFORM=macos` builds and tests the bundle off a Mac — a `.app` is
  a directory, so there was no reason that path could not be tested.

## 3. Analysis package versions

The suite tracked its GUI repos and nothing else. The software that actually produces
results — vsnp3, AMRFinderPlus, kraken2, mlst, IRMA, GenoFLU — is conda packages inside
each env, and no version of it was recorded, displayed or checked. "Which vsnp3 wrote
this report?" had no answer short of listing `conda-meta` by hand.

- `tools.yml` gains `packages:` (exact pins) and `packages_held:` (a newer release exists
  that this env cannot take — shown, never offered).
- `bin/lib/packages.py` reads installed versions from `conda-meta` (a directory listing,
  no solve) and the newest from `api.anaconda.org` (one request per package, cached 6 h).
  It reads the env that would **actually run** the tool via `tool_launch.resolve` — not
  always `<checkout>/env`.
- Every installed card shows `vSNP3 v0.4.36 · vsnp3 3.35`, always — not only when an
  update exists. `bdtools versions` prints the same in a terminal.
- Newer packages appear in the update banner as their own items with their own button.
- `bdtools update-packages <tool|all>` installs, **re-applies that tool's local patches**,
  checks doctor, and bumps the pin. The re-apply is why this is a command and not a
  documented `conda install`: vsnp_gui patches the packaged vsnp3 (the minus-strand
  annotation fix), and a fresh package overwrites the patched files.

**The three update buttons run in a fixed order** and are laid out left to right,
numbered, because the order matters:

1. **Update bdtools** — `bdtools update <tool>` rewrites pins in `tools.yml`, and the
   bdtools `git pull --ff-only` restores that file to get a clean tree, so tools-first
   silently discards the pin record. A tool rebuild should also run under the *new*
   install scripts.
2. **Install tool updates** — each GUI's own release (tag + env rebuild).
3. **Update conda packages** — the analysis software inside an env. Last, because a
   bdtools pull can change the pins these work from.

## 4. Cross-platform stability outranks version currency

Your stated priority, now enforced in the tool.

Every update solves locally **and** on `linux-64, osx-64, osx-arm64` before anything is
installed. If any platform cannot take it, **nothing is applied anywhere**, the refusing
platforms are named, and the version is recorded so it is not proposed again.
`--local-only` is the deliberate override.

Verified: `--to mlst=2.34.0` solves on Linux and is then refused with
`cannot be installed on: osx-64 osx-arm64`.

**`bdtools update-packages all --check-pins`** is the gate to run when changing a pin — a
real dry-run solve per platform. Both pin mistakes made this session would have been
caught by it.

**Severity is split**, because a run that correctly works out an update cannot be applied
is not a failure:

| outcome | meaning | exit |
|---|---|---|
| **BLOCKED** | cannot be installed here or elsewhere; tools keep working | **0** |
| **FAILED** | conda missing, install died after a good solve, patches did not re-apply | 1 |

Conflating them printed "⚠ Update finished with errors" for a run in which nothing was
wrong — which is how you teach people to ignore the word "error".

**Two facts worth keeping:**

- **noarch does not mean portable.** `mlst 2.34.0`/`2.35.0` are noarch and depend on
  `libxcrypt1`, which has no macOS build. **2.33.1** is the newest mlst that solves on
  both, so that is the pin. Confirmed from bioconda file metadata *and* a foreign-platform
  solve.
- **Foreign-platform solves need `CONDA_OVERRIDE_OSX`** or they fail on a missing `__osx`
  virtual package — which reads exactly like a real dependency conflict and cost me one
  wrong conclusion.

## 5. One env per analysis tool

**Rule:** a tool that needs another tool's software invokes it from **that tool's env** —
`<env>/bin/<prog>` with `<env>/bin` first on `PATH`, so the callee's own dependencies
(perl, blast, any2fasta) resolve there. Not `conda run` (startup cost per call, and it
can interleave stdout).

`tool_launch` now exports **`BDTOOLS_SIBLING_ENV_<TOOL>`** for every installed sibling
(plus a `BDTOOLS_SIBLING_ENVS` map). Each path is resolved by asking `resolve()` about
that tool — a shared sibling env or a personal conda env both win over
`<checkout>/env`, so it cannot be guessed.

> `resolve()` builds that map by calling `resolve()` per sibling, which recurses;
> `_SCANNING_SIBLINGS` guards it. `resolve()` is on the dashboard's hot path, so
> unguarded recursion would have exhausted the stack there. 0.01 s per call with the map.

**Why it matters (amr_plus_gui v0.3.3):** a duplicated `mlst` pulled
`perl-bioperl → perl-bio-samtools`, holding perl and zlib down, which pinned
`ncbi-amrfinderplus` at **3.12.8** — a version that **cannot read the deployed AMRFinder
database**. The 4.x layout renamed `AMRProt` → `AMRProt.fa`, so 3.12.8 reports
*"the BLAST database for AMRProt was not found"*, and its own default DB path points into
a conda build directory that does not exist. The older version was the broken one.

mlst left that spec; MLST corroboration runs from mlst_gui's env. AMRFinderPlus 4.2.7 +
kraken2 2.17.1 pass `--check-pins` on all three platforms.

> **An existing env still physically contains mlst**, so it keeps its old versions until
> rebuilt (`bdtools install amr_plus_gui --rebuild`). Reported as blocked, not an error.
> **AMR gene calls will change** — validate with `bdtools test amr_plus_gui` before users see it.

## 6. conda builds survive a strict shell

A macOS env build failed twice and gave up, on a bug the code already knew about:

```
deactivate_clangxx_osx-arm64.sh: CONDA_BACKUP_CLANGXX: unbound variable
LinkError: post-link script failed for package spades-4.3.0
```

`harden_conda_hooks` was powerless because **it can only patch hooks that already
exist**. The env was being *created*: nothing to harden, the transaction installed the
hook, a post-link script sourced it and died under `set -u`, and the rollback **deleted
the hook** — so the retry began with nothing to harden and failed identically.

`_conda_step` now runs conda with `SHELLOPTS`/`BASHOPTS` scrubbed and every
`CONDA_BACKUP_<VAR>` defined. Verified both halves: with `SHELLOPTS` exported a child bash
inherits `nounset` and the hook dies with that exact message; scrubbed, it runs clean.

Also: hooks are hardened **after** a step as well as before (a transaction can install
the hooks that break the next one), and a retried create **clears a partial prefix**
first — conda's rollback leaves untracked `__pycache__` behind, which is the
`ClobberError` storm. Guarded to absolute + env-shaped + no `bin/python`; verified it
refuses `""`, `/`, a home directory and any built env.

## 7. IGV — vsnp_gui v0.4.37

A new user on a Mac got `IGV failed to load: Error accessing resource … status: 400` and
an empty viewer. `serve_project_file` resolved the requested path but compared it against
roots **not resolved into the same namespace** — which only matters when a reference root
is a *directory of symlinks*, and `install-local` builds exactly that whenever there is
**no shared reference set**. With `/srv` present the root is one symlink to the whole
collection, so it always worked here.

Fixed by comparing both sides in both as-given and resolved form (as-given normalized
first, so `..` cannot escape). That also closed two latent holes: `startswith` without a
path boundary accepted a sibling sharing a name prefix, and an empty root accepted
everything.

Two frontend consequences of the same class:

- the annotation track loads **after** `createBrowser`, so one unreachable GFF no longer
  costs the reads and the calls too;
- clicks arriving during the initial load are **queued** with their locus, instead of
  dropped behind a sticky "IGV not ready yet" over a fully loaded viewer.

## 8. Four bugs of mine, and what stops each recurring

Read this before trusting a claim of "verified".

1. **The dashboard went blank, twice.** I wrote a JS string as `'a tool\'s env'`. `PAGE`
   is a Python `"""…"""` literal, so Python collapsed `\'` to a bare `'` **before it was
   served** — the script block failed to parse, so `load()`, `loadInfo()` and
   `pollUpdates()` never ran. My check extracted the script from the `.py` source and
   *unescaped it* before `node --check` — I tested a copy in which the bug did not exist.
   **Now:** `tests/test_page_js.py` takes `PAGE` **as evaluated** and parses every script
   block, then executes `renderUpdates()` for each update-kind combination.
   `bin/lint.sh` runs it first. Confirmed it catches that exact bug.
   **Rule: never validate `PAGE` by reading the file. Use double-quoted JS strings for
   text with apostrophes.**
2. **I wrote pins that could not be satisfied.** `ncbi-amrfinderplus=4.2.7 +
   kraken2=2.17.1 + mlst=2.35.0` is unsatisfiable together; `mlst=2.35.0` is impossible on
   macOS. I picked the newest of each independently and never solved the set.
   **Now:** `--check-pins`, plus an offline guard rejecting versions verified unavailable.
3. **`update-packages` rewrote the pin to match local reality** on every run — it silently
   reverted my own new pin while I was testing it. Since the right version differs per
   platform, two machines would have fought over `tools.yml` forever. **Now:** drift is
   reported; a pin only moves when a package is actually installed by that command.
4. **Two tests read the real `$BDTOOLS_HOME` cache**, so they passed or failed by local
   history. **Now** hermetic; verified identical with the cache empty and populated.

## 9. Still open

1. **The amr_plus assembler diverges by platform — needs your decision.** Both platforms
   agree on AMRFinderPlus 4.2.7, kraken2 2.17.1, samtools 1.24, blast 2.17.0. The
   assembler does not: **shovill 1.4.2 + spades 3.15.5** on linux-64 versus **shovill
   0.9.0 + spades 4.3.0** on osx-64. The chain is closed — shovill 1.4.2 needs
   `spades >=3.14,<4`, spades 3.x needs `libzlib <1.3`, and AMRFinderPlus 4.2.7 needs
   `libzlib >=1.3.1`. So the same reads would assemble differently per platform, and the
   macOS pairing (shovill 0.9.0 with spades 4.x) may not even work at runtime.
   Under the stability-first priority the fix is to **drop shovill and use one pinned
   spades everywhere** — `amr_pipeline.py` already has that fallback path. It changes
   assemblies and therefore AMR calls, so it is not done.
2. **The macOS `kraken_id_parse_gui` build needs re-testing** after §6. The mechanism and
   the guard were proved on Linux; there is no Mac here, so the arm64 build was not
   reproduced. If it still fails, the log will differ — send it plus
   `bdtools doctor kraken_id_parse_gui`, and check whether anything in the shell profile
   exports `SHELLOPTS`.
3. **AMRFinderPlus 4.2.7 needs validating** against a known sample before users see it
   (`bdtools test amr_plus_gui`), and existing envs need
   `bdtools install amr_plus_gui --rebuild` to gain it.
4. **The one-env-per-tool rule is not in `docs/BUILDING_A_TOOL.md`** yet — it lives in
   code comments and commit messages, so a new tool will not be steered by it.
5. **`/srv/kapurlab/tools/amr_plus_gui` is at v0.2.7**, well behind the v0.3.3 pin. I
   restored it untouched after mistakenly committing onto its detached HEAD — do the
   deploy deliberately. Other `/srv` checkouts likely lag too.
6. **This dev box has mlst 2.35.0 with the pin at 2.33.1.** Drift is reported on every
   run. Converge with `bdtools update-packages mlst_gui --to mlst=2.33.1`.

## 10. Follow-up — the update banner would not go quiet

Reported after §3 shipped: run **Update conda packages**, get a green ✅, reopen the
dashboard, and the same three packages are offered again. Nothing was wrong with the
update — the run correctly established that all three are uninstallable here — but
nothing on screen ever reflected that, so the only visible remedy was to run it
again.

Three separate causes, all now closed:

1. **The page never repainted the banner.** `pollUpdate()` wrote "✅ Finished" and
   stopped. The backend *had* refreshed its cache (`check_async(force=True)`), and
   the answer was correct — the page just kept displaying the list the user had
   acted on. It now re-checks and repaints on completion.
2. **A re-check redisplayed the stale answer.** `checked` stays true while a
   re-check runs, because the cache still holds the previous result — so
   `renderUpdates` rendered the old list mid-check, which is exactly the moment it
   is wrong. It now shows "↻ checking…" whenever a check is in flight (and
   `pollUpdates` keeps polling through it, or the page would freeze on that line).
3. **`mlst` was offered on every fresh machine.** `tools.yml` pins 2.33.1 and
   explains at length that 2.34+ can never install on macOS — but never declared it
   `packages_held`, so every install offered 2.35.0, spent minutes on a solve, and
   refused it. Now declared. The hold is by name: remove it when an mlst above
   2.33.1 builds on macOS.

The run log moved **out of** the banner (`#urun`), because a repaint would otherwise
destroy the record of the run that just finished. It stays until dismissed.

Where the information went, now that the banner is silent about it:

- `✓ Up to date. 2 analysis packages are held at the installed version …` — the
  count is on the quiet line, hover for the list. "Up to date" alone would have been
  claiming the newest version is installed when it is not.
- The card: `ncbi-amrfinderplus 3.12.8 · held (4.2.7)`, whose tooltip carries the
  **recorded** reason and, where one exists, the remedy
  (`bdtools install amr_plus_gui --rebuild`). The old badge said "see tools.yml" for
  every hold — a dead end for the ones that came from a solve tried on this machine,
  which is most of them. `held_reason`/`held_fix` are on the record now, so the CLI
  (`bdtools versions`, `update-packages`) and the dashboard cannot disagree.
- A hold now expires when the env catches up: `held` means something is being held
  *back*, so it is false once the installed version has reached the channel's
  newest. A manifest hold is by name and forever; the env is not.

## 11. Verify after pulling elsewhere

```bash
git pull
bin/lint.sh                                   # includes the dashboard-page JS check
python3 -m unittest discover -s tests -p "test_*.py"   # 135 tests
bin/bdtools versions                          # what each tool actually runs
bin/bdtools update-packages all --check-pins  # only when changing a pin (minutes)
bin/bdtools doctor
```

The dashboard needs **Restart dashboard** to pick up new page code. If it comes back
blank, that is §8.1 — the page script failed to parse; check the browser console and run
`python3 -m unittest tests.test_page_js`.
