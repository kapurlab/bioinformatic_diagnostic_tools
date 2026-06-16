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
- **macOS** — works; most bioconda tools have macOS builds. A few are
  Linux-only — for those, use the OOD deployment or a container.
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
bin/bdtools install irma_gui        # clone + build env + build frontend
                                       # (delegates to the tool's deploy/install.sh)
```

The installer clones the tool (pinned version from `tools.yml`) into
`~/.local/share/bdtools/checkouts/<tool>/`, builds its conda env and
frontend, then launches it and opens your browser.

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
