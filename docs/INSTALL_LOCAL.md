# Local install — Linux, macOS, Windows (WSL2)

Run a Kapur Lab tool on your own computer. There is **no Open OnDemand** in this
mode — the tool's FastAPI backend runs directly and serves its web GUI at
`http://127.0.0.1:<port>/`, which you open in your normal browser. This is the
same backend that OOD proxies in production; only the front door differs.

## Prerequisites

- **git** and a **conda/miniforge** install (Miniforge: <https://github.com/conda-forge/miniforge>).
- **Node.js + npm** *only if* a tool ships an unbuilt frontend (most release
  tarballs ship `frontend/dist/` prebuilt; building from a branch needs npm).
- **Disk + RAM**: pipelines like SPAdes/IRMA are memory-hungry. Local mode is
  fine for small jobs; large genomes belong on the HPC/OOD deployment.

## Platform notes

- **Linux** — works directly.
- **macOS (Intel)** — works directly.
- **macOS (Apple Silicon, M1/M2/M3…)** — bioconda has no native arm64 builds for
  the pipeline toolchain (IRMA's `blat`, shovill/spades/mash/skesa), so a native
  solve fails for `mlst_gui`, `amr_plus_gui`, and `irma_gui`. `bdtools install`
  detects Apple Silicon and **builds the env as osx-64 under Rosetta 2**
  automatically — you don't edit any `environment.yml`. One-time prerequisite:
  `softwareupdate --install-rosetta --agree-to-license` (the installer tells you
  if it's missing). `genoflu_gui` happens to resolve natively, but all four use
  the Rosetta env for consistency. Force a native attempt with
  `BDTOOLS_NATIVE_ARM=1` (expect solve failures). An env that already exists
  keeps the platform it was built for — updates re-derive it from the env itself,
  so an osx-64 env is never updated with an arm64 solve (that mixes
  architectures in one prefix). To move a tool to a different platform, delete
  its `env/` and reinstall.
- **`vsnp_gui` is heavier to install** — `bdtools install vsnp_gui` builds the
  bioconda `vsnp3` env (+ web layer + patches) and downloads the USDA-VS
  reference sets (~320 MB) into `~/.local/share/bdtools/vsnp3-refs/`. The
  sourmash best-reference index ships with the conda package, so auto species
  detection works out of the box. IGV/FigTree are OOD-desktop features (not
  available in local mode); the Step 1/Step 2 SNP pipelines work locally.
- **Windows** — use **WSL2** (a real Linux). Install miniforge *inside* WSL2 and
  run the commands there; WSL2 forwards `localhost` to your Windows browser, so
  the Web GUI opens normally on Windows. (Native Windows is not supported because
  bioconda tools are Linux/macOS only — this is expected, not a limitation of the
  tools.)

## Steps

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools

bin/bdtools list                    # see available tools
bin/bdtools install all             # clone + build env + frontend for every tool
                                       # (or name one, e.g. `install irma_gui`)
```

The installer clones each tool (pinned version from `tools.yml`) into
`~/.local/share/bdtools/checkouts/<tool>/` and builds its conda env and frontend.
When it finishes it prints your access point and — when run in a terminal — opens
the **local dashboard**.

## Access point: the dashboard

```bash
bin/bdtools dashboard               # opens http://127.0.0.1:8080/
```

The dashboard is your local landing page (the equivalent of the OOD dashboard):
it lists the GUIs installed on this machine, and clicking one starts that tool's
server and opens it in a new browser tab.

Each tool's server binds a private loopback port, but you never open that port
directly: the dashboard reverse-proxies each one at `http://127.0.0.1:8080/t/<tool>/`,
so **only port 8080 ever has to be reachable** — which is what makes SSH use a
single `-L 8080:127.0.0.1:8080`. It also means every tool shares an origin with
the dashboard, so one appearance choice (Light / Dark / System) applies across all
of them.

> If `starlette`, `httpx` and `uvicorn` aren't importable, the dashboard falls
> back to a legacy mode that puts each tool on its own port. That still works, but
> you'd have to forward one port per tool and each tool then remembers its own
> theme separately. Installing any tool pulls those three in.

**Lifecycle / re-opening after a restart.** Restarting your computer stops the
dashboard (normal). Give users a real launcher once, at the end of the install:

```bash
bin/bdtools make-launcher              # add --dest ~/Desktop on Linux for a desktop copy
```

macOS gets `Kapur Lab Dashboard.app` on the Desktop (icon, no Terminal window, no
Gatekeeper prompt — it was generated locally, so it is not quarantined); Linux and
WSL get an applications-menu entry. It can be moved anywhere, since the install
path is baked in — re-run the command after moving or reinstalling the suite. The
dashboard it starts is detached, so quitting the launcher cannot interrupt a run;
users stop it with **Shut down** in the browser. Failures are logged to
`~/Library/Logs/bdtools/dashboard.log` (macOS) or
`~/.local/state/bdtools/dashboard.log`, and raise a dialog.

`Open Dashboard.command` in the repo folder still works for macOS, with a Terminal
window alongside. Anyone can instead `cd` into the folder and run
`bin/bdtools dashboard` again. Running it when it's already up just re-opens the
browser tab — it won't start a second copy.

To launch a single tool directly instead:

```bash
bin/bdtools local mlst_gui --port 8080      # then open http://127.0.0.1:8080/
```

### Why does my dashboard URL have `?t=…`?

Because the machine looks like a **shared** one. Nothing is broken, and it is not
a sign of a failed update — the same `bdtools dashboard` command deliberately
prints a different URL depending on where it runs:

| Machine | URL printed | Why |
|---|---|---|
| macOS | `http://127.0.0.1:8080/` | personal desktop — `127.0.0.1` is yours alone |
| WSL2 / WSL | `http://127.0.0.1:8080/` | same reasoning |
| anything else (a lab server, HPC login node) | `http://127.0.0.1:8080/?t=<key>` | other Unix accounts on that host also reach `127.0.0.1`, so without a key any of them could open your dashboard and your data |

The `?t=<key>` is a **one-time session key**, minted fresh on every start. Opening
the link exchanges it for an httponly cookie and strips it from the address bar,
so it appears only once. Treat it like a password: don't paste it into chat or a
ticket. Anyone without the cookie gets a 403.

Override the platform guess in either direction:

```bash
BDTOOLS_DASHBOARD_AUTH=0 bin/bdtools dashboard    # no key, even on a server
BDTOOLS_DASHBOARD_AUTH=1 bin/bdtools dashboard    # require a key, even on a Mac
```

Two things worth knowing:

- This is **not** `BDTOOLS_CONTROL_TOKEN`. That one is a CSRF header guarding the
  dashboard's own `/api/*` actions (Launch, Restart, Shut down). It is always
  minted, never appears in a URL, and needs nothing from you.
- The **legacy fallback dashboard has no session key at all**. If you are on a
  shared server and the banner doesn't say "single-port proxy", install any tool
  so `starlette`/`httpx`/`uvicorn` are present and the protected proxy is used.

After installing, validate against known samples (see [tests/README.md](../tests/README.md)):

```bash
bin/bdtools test all                # PASS / FAIL / SKIP per tool
```

Re-launch later without rebuilding:

```bash
bin/bdtools local irma_gui
bin/bdtools local irma_gui --port 8765    # pin a port if you prefer
```

Check status / update:

```bash
bin/bdtools status
bin/bdtools check-updates
bin/bdtools update irma_gui
```

### When an update fails

`bin/bdtools update all` runs every tool even if one of them fails. Failures are
listed together at the end and the command exits non-zero:

```
!! FAILED to update: kraken_id_parse_gui
   ...
   bin/bdtools update kraken_id_parse_gui
```

What to know:

- **The other tools were still updated.** Only the named ones need attention.
- **A build that didn't finish is remembered** (in
  `$BDTOOLS_HOME/state/<tool>.build-failed`). Re-running `bdtools update <tool>`
  retries it — no `--force` needed. This matters because the update moves the
  checkout to the new tag *before* building, so a failed build leaves new code
  next to the previous env; without that record the next update would report the
  tool as already up to date.
- **The env is not destroyed by a failed update.** `conda env update` is additive
  and conda rolls a failed transaction back, so the previous env stays in place —
  the tool keeps running, on its old dependencies, until the rebuild succeeds.
- **`bin/bdtools doctor <tool>`** says which modules/programs an env is actually
  missing, which is the fastest way to tell whether a failed update matters for
  the analyses you run.
- **Still broken, or the error text looks alien?** `bin/bdtools diagnose <tool>`
  writes one report file to send, and [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
  indexes every known error message by its exact text — with one command to run
  and the fix for each.

## Where things live

| Item | Path |
|---|---|
| Tool checkout | `~/.local/share/bdtools/checkouts/<tool>/` |
| Conda env | `<checkout>/env/` (or a named env, per the tool's installer) |
| Built frontend | `<checkout>/frontend/dist/` |

Override the checkout location with `--prefix DIR` or `BDTOOLS_HOME`.
If you already have the tools cloned elsewhere (e.g. a shared
`/srv/<lab>/tools` tree), point `BDTOOLS_TOOLSDIR` at it and the CLI will use
those checkouts in place instead of cloning.

> ### ⚠️ On an HPC / cluster: move `BDTOOLS_HOME` off your home directory first
>
> Cluster home directories are small, quota-limited filesystems, and the tools'
> conda environments are large (several GB total). Building them under the default
> `~/.local/share/bdtools` will fail partway through with **`Disk quota
> exceeded`** on a `git clone`, or an opaque **conda error mid-solve** (conda
> aborts when it can't write). Point `BDTOOLS_HOME` at a large **scratch / work /
> group** filesystem *before* installing, and set it persistently so the
> dashboard and later commands resolve the same location:
>
> ```bash
> # example paths — use your cluster's large-storage mount:
> echo 'export BDTOOLS_HOME=/storage/work/$USER/bdtools' >> ~/.bashrc
> export BDTOOLS_HOME=/storage/work/$USER/bdtools
>
> # if a partial install already filled your home quota, reclaim it first:
> rm -rf ~/.local/share/bdtools
>
> bin/bdtools install all      # now builds under large storage
> bin/bdtools doctor
> ```
>
> A whole lab can share one install by pointing `BDTOOLS_HOME` at a group
> allocation (e.g. `/storage/group/<grp>/bdtools`). Keep conda's **package cache**
> off home too — set `pkgs_dirs` and `envs_dirs` to scratch in `~/.condarc`.
