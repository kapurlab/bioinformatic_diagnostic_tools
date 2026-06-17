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
  `BDTOOLS_NATIVE_ARM=1` (expect solve failures).
- **`vsnp_gui` is OOD-only** — it has no local-build path (its env + multi-GB
  TB/Brucella reference sets are provisioned by its OOD installer). `bdtools
  install --local` skips it cleanly; the other seven GUIs install locally. Run
  vsnp_gui via the OOD paths (`--sandbox` / `--server`).
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
server and opens it in a new browser tab. Each tool runs on its own `localhost`
port, exactly as `bdtools local` runs it — the dashboard is just the launcher.

To launch a single tool directly instead:

```bash
bin/bdtools local mlst_gui --port 8080      # then open http://127.0.0.1:8080/
```

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
