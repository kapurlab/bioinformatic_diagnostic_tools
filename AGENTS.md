# AGENTS.md — install & validate the Kapur Lab diagnostic tool suite

These are instructions for an AI coding agent (e.g. Claude Code) asked to
**install, update, or validate** the Kapur Lab bioinformatics GUI suite on the
machine it is running on. Follow them top to bottom. Everything routes through
one CLI: `bin/bdtools`. Do not invent install steps — `bdtools` already knows
how to build each tool in each environment.

> Humans who would rather not use an agent: see [INSTALL.md](INSTALL.md) for the
> equivalent manual runbooks.

---

## 0. Ground rules

- **Normal users get the `main`/production build only.** Never install developer
  (`*_dev`) cards or check out non-release branches unless the user explicitly
  says they are a developer and want the dev path. The defaults already do the
  right thing — do not add `--with-dev`.
- Prefer `--dry-run` first for anything that writes system paths (`--server`,
  `site-init`), show the user the diff, then re-run for real.
- Never edit files under `/home/vxk1` or other users' home dirs.
- If `git` or a conda/mamba (miniforge) base is missing, stop and tell the user
  how to install it rather than guessing.

---

## 1. Get the umbrella repo

If you are not already inside a checkout:

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
bin/bdtools list          # sanity check: prints the suite manifest
```

---

## 2. Detect the environment, then pick a mode

Run these checks and choose **exactly one** install mode.

```bash
uname -s                                   # Darwin = macOS, Linux = Linux/WSL
grep -qi microsoft /proc/version 2>/dev/null && echo "WSL" || echo "not-WSL"
test -d /etc/ood/config && echo "OOD core present" || echo "no OOD"
id -u                                       # 0 = root
```

Decision table:

| Detected | Mode | Command |
|---|---|---|
| macOS, no `/etc/ood/config` | personal | `bin/bdtools install --local <tool>` |
| Linux or WSL, no `/etc/ood/config` | personal | `bin/bdtools install --local <tool>` |
| `/etc/ood/config` present, **not root** and sys-apps dir not writable | OOD user | `bin/bdtools install --sandbox <tool>` |
| `/etc/ood/config` present, **root** (or sys-apps writable) | OOD sysadmin | `bin/bdtools install --server <tool> --site-conf sites/site.conf` |

Notes:
- **Ambiguous?** (e.g. OOD present but you're unsure whether the user is an admin)
  — ask the user whether they are a regular user (→ `--sandbox`) or the OOD admin
  (→ `--server`). Default to `--sandbox` if they just want it for themselves.
- `--server` needs a `sites/site.conf` (copy `sites/site.conf.example`, set
  `CLUSTER_NAME`, paths, groups). Run with `--dry-run` first; it writes
  system paths and needs sudo. Bare-metal-from-scratch (no OOD yet) is a bigger
  job — point the user at [docs/INSTALL_BARE_METAL.md](docs/INSTALL_BARE_METAL.md).
- `--local` is build-only by default. After a local install, `bdtools install`
  prints the access point and (when run in a terminal) opens the **local
  dashboard** — a landing page that lists the installed GUIs and launches the one
  the user picks. Re-open it any time with `bin/bdtools dashboard` (serves
  `http://127.0.0.1:8080/`). To launch one tool directly:
  `bin/bdtools local <tool> --port 8080` then open `http://127.0.0.1:8080/`.
- **macOS Apple Silicon (arm64):** bioconda has no native arm64 builds for the
  pipeline toolchain (IRMA's `blat`, shovill/spades/mash/skesa, etc.), so a
  native solve fails for `mlst_gui`, `amr_plus_gui`, and `irma_gui`. `--local`
  handles this automatically by building the env as **osx-64 under Rosetta 2**.
  Do **not** "fix" this by editing a tool's `conda_setup/environment.yml` — the
  packages don't exist for arm64; removing them would break the tool. If the
  installer reports Rosetta 2 is missing, run
  `softwareupdate --install-rosetta --agree-to-license` and re-run. To force a
  native attempt anyway, set `BDTOOLS_NATIVE_ARM=1` (expect solve failures).

Which `<tool>`? Use a name from `bin/bdtools list`, or `all`. If the user didn't
say, ask which tool(s) they want. The tools are: `vsnp_gui`, `amr_plus_gui`,
`irma_gui`, `genoflu_gui`, `mlst_gui`, `kraken_id_parse_gui`, `ksnp_gui`,
`ncbi_submit_gui`, `mhc_gui`. `mhc_gui` is clearly marked developmental in the
dashboard and must not be presented as validated diagnostic output.

> **`vsnp_gui` installs locally too, but it's heavier.** Unlike the others it has
> no `environment.yml`; `bdtools install --local vsnp_gui` builds the bioconda
> `vsnp3` env (+ web layer + Kapur Lab patches), downloads the USDA-VS
> reference_options (~320 MB) into `~/.local/share/bdtools/vsnp3-refs/`, and
> registers them. The sourmash best-reference index ships with the conda package.
> On Apple Silicon it builds osx-64 under Rosetta like the rest. (If a future
> tool genuinely has no local path, `install` skips it cleanly — not a failure —
> and says to use `--sandbox`/`--server`.) Note: IGV/FigTree are OOD-desktop
> features and aren't available in local mode; the Step 1/Step 2 pipelines are.

---

## 3. Reference databases — REQUIRED install step (don't skip)

Some tools need large external reference databases that are **not** bundled:
- `kraken_id_parse_gui` → Kraken2 `k2_standard_08gb` + BLAST `ref_prok_rep_genomes`.
- `vsnp_gui` → vSNP reference options + vsnp dependencies.

**You (the agent) MUST handle this — the installer cannot.** `bdtools install`'s
own database prompt only fires on an interactive TTY; when you run `bdtools` as a
subprocess it is NOT a TTY, so that prompt is skipped and the databases are left
unset (tools then fail at run time for a missing DB). So after installing, do
this yourself as a step in the flow:

1. **Ask the user** whether to set up the databases and **where** — they're tens
   of GB: Home (`~/databases`, a laptop) or Shared (`/srv/kapurlab/databases`,
   one copy for the machine/lab). Ask in the chat; do not assume.
2. Run it with their choice (re-running is safe — present DBs are skipped):

```bash
bin/bdtools setup-databases --home      # ~/databases (per-user laptop)
bin/bdtools setup-databases --shared    # /srv/kapurlab/databases (whole machine/lab)
bin/bdtools setup-databases --root DIR  # a custom location
```

Name specific DBs to limit scope: `bin/bdtools setup-databases kraken vsnp-refs`
(`kraken blast vsnp-refs vsnp-deps`). Then run `bdtools doctor` (§3a) — any tool
still showing a missing DB means this step was skipped or a download failed.

The other diagnostic GUIs (`mlst_gui`, `ksnp_gui`, `genoflu_gui`, `irma_gui`)
bundle their references in their conda env and need no download
(`amr_plus_gui` manages its own AMRFinder DB, separate from `setup-databases`). The
curated Step-2 VCF databases in `vsnp_gui` (e.g. `mtbc0_v1.1`) are lab-private
and not part of `setup-databases`. If a DB-dependent tool's install or validation
fails for a missing DB, tell the user exactly which DB and offer
`setup-databases` — do not download multi-GB databases without asking.

---

## 3a. Check an install is runnable — `bdtools doctor`

After installing (and after `setup-databases`), run the readiness check. It
verifies each installed tool's env, the programs it shells out to, and its
databases — and prints the exact fix for anything missing. The installer runs
it automatically at the end; run it on demand when diagnosing a problem:

```bash
bin/bdtools doctor            # all installed tools
bin/bdtools doctor <tool>     # one tool
```

A non-zero exit means something needs attention; relay the ✗ lines and their
suggested fix commands to the user verbatim — they're written for non-technical
users. The per-tool contract lives in `bin/lib/requirements.py` (modules,
binaries, databases); extend it when a tool gains a dependency.

**Maintainer guardrail — `bdtools lint`.** Before tagging a release, run
`bin/bdtools lint`: it statically compares each tool's actual imports + invoked
programs against its declared dependencies (`environment.yml`, `requirements.txt`,
`requirements.py`) and flags drift — the "code uses a package the env doesn't
ship" bug (a `✗` is high-confidence; a `!` is advisory). Fix a `✗` by adding the
dependency to that tool's `environment.yml`, then cut the release.

## 4. Update an existing install

```bash
bin/bdtools check-updates           # report newer upstream tags (read-only)
bin/bdtools update <tool|all>       # move to newest tag + rebuild, bump the pin
```

---

## 5. Validate a deployment

After installing (or after any new deployment), run the validation suite to
confirm the tools produce correct diagnostic output on known samples:

```bash
bin/bdtools test all                # download known samples, run, diff vs expected
bin/bdtools test <tool>             # just one tool
```

Report results as **PASS / FAIL / SKIP** per tool:
- **PASS** — output matched the stored expected result within tolerance.
- **FAIL** — show the diff (which field, expected vs actual). Do not "fix" the
  expected file to make it pass; surface the discrepancy to the user.
- **SKIP** — the tool is not installed here, a required reference DB is absent
  (name the missing DB so the user can stage it, then re-run), or the tool has
  no validation spec yet. A SKIP is not a failure.

Coverage today: all seven diagnostic GUIs are validated — `mlst_gui`,
`amr_plus_gui`, `irma_gui`, `genoflu_gui`, `ksnp_gui`, `vsnp_gui`, and
`kraken_id_parse_gui` have recorded golden results. The last three are **tier 2**
(they need an external reference DB — Kraken2/BLAST or the vsnp3 reference set)
and SKIP cleanly when that DB is absent. `ncbi_submit_gui` (the SRA/GenBank
submission tool) and the developmental `mhc_gui` have no golden validation test
by design and SKIP. So on a machine with the DBs,
`bdtools test all` PASSing all seven is the expected good result; on a fresh
laptop the tier-2 tools SKIP and the rest PASS.

The accession, run command, and expected headline result for each tool live in
[`tests/<tool>/test.yml`](tests/) and `tests/<tool>/expected.json`. Pick the
sample from there — never from memory. See [tests/README.md](tests/README.md) for
how the golden results were established and how to re-validate by hand.

---

## 6. Hand off the access point

Finish by telling the user how to get to their tools — this is the whole point
of the install:

```bash
bin/bdtools dashboard          # local landing page; pick a GUI -> it opens in a tab
bin/bdtools dashboard --restart  # after an update/`git pull`: stop stale servers, start fresh
# or one tool directly:
bin/bdtools local <tool> --port 8080   # then open http://127.0.0.1:8080/
```

> After you `git pull`/update on a machine that already had the dashboard open,
> tell the user to run `bin/bdtools dashboard --restart` — the old dashboard and
> tool servers keep running old code until restarted (closing the browser doesn't
> stop them).

On a personal machine the standard build flow is
**install → set up databases (§3) → doctor (§3a) → validate → dashboard**:
`bdtools install <tool|all>` (prints the access point), then **ask the user and
run `bdtools setup-databases`** (§3 — the installer can't prompt for this from an
agent), `bdtools doctor` (confirm all ✓), `bdtools test all` (report
PASS/FAIL/SKIP), then `bdtools dashboard` (or tell the user the command).
On an OOD deployment the access point is the institution's OOD dashboard, where
the production tool cards now appear.
