# Adding a new tool — layout & look checklist

**Read this before scaffolding a new GUI into the suite.** It exists so a new
tool comes out looking and behaving like the other nine instead of like a
one-off that has to be retro-fitted later.

Working note, kept in the repo so every contributor and agent sees the same
version. It names in-progress state and per-machine detail, so expect parts of
it to age faster than the code. The normative, stable statement of the
*contract* is [`docs/BUILDING_A_TOOL.md`](../docs/BUILDING_A_TOOL.md);
the committed, shareable version of the *build guide* is
`amr_plus_gui/docs/BUILDING_A_SIBLING_TOOL.md`. This file is the "and here is
what those two don't tell you" layer.

**Copy from `mlst_gui`.** It is the smallest tool that has every piece of the
contract and nothing extra. `amr_plus_gui` is the most complete (organism
detection, sibling-tool cross-check, PDF/Excel report) and is the declared
source of truth for the shared Results pane — but its `App.css` and
`ThemeToggle.jsx` are reformatted relative to the other six, so copy the *look*
files from `mlst_gui` and the *Results pane* files from `amr_plus_gui`.

---

## 0. The one-line summary

A tool is a FastAPI backend serving a Vite-built React SPA out of
`frontend/dist/`, with **relative URLs only**, driven by the umbrella through
`tools.yml`. Everything below is what makes it indistinguishable from its
siblings.

---

## 1. Repo layout

```
<tool>/
├── backend/
│   ├── app/main.py            FastAPI app exposed as `app.main:app`
│   ├── app/config.py          per-user config.json + resolved roots
│   ├── app/jobs.py            job manager (drives GET /api/jobs)
│   ├── app/request_safety.py  COPY VERBATIM — same-origin guard
│   └── requirements.txt       pip deps for the web layer
├── bin/                       the analysis pipeline (importable, PYTHONPATH'd)
├── frontend/
│   ├── index.html             theme bootstrap script (see §2)
│   ├── vite.config.js         base: "./"   ← non-negotiable
│   └── src/{App.jsx,App.css,main.jsx,ThemeToggle.jsx,
│            ResultsPane.jsx,ResultsPane.css,useResults.js}
├── conda_setup/environment.yml
├── deploy/install.sh          idempotent, no-sudo, supports --personal
└── ood/apps/<tool>/           manifest.yml form.yml submit.yml.erb template/
```

`frontend/dist/` **is committed** (it is the prebuilt fallback for hosts without
Node ≥20.19). `node_modules/` is not. Gitignore both `node_modules/` *and*
`node_modules` — a committed symlink once broke the `_dev` worktree build.

---

## 2. The look — what actually makes it match

### 2a. Theme bootstrap, before first paint

`frontend/index.html` must carry this inline script in `<head>`, verbatim. It
runs before React so the page never flashes light-then-dark:

```html
<script>
  try { const m=localStorage.getItem("bdtools-theme")||"system"; const d=m==="dark"||(m==="system"&&matchMedia("(prefers-color-scheme: dark)").matches); document.documentElement.dataset.theme=d?"dark":"light"; document.documentElement.dataset.themeMode=m; document.documentElement.style.colorScheme=d?"dark":"light"; } catch {}
</script>
```

Key facts:
- The localStorage key is **`bdtools-theme`** — shared suite-wide.
- One choice propagates across tools only under the **single-port proxy**
  dashboard (same origin). On the legacy per-port fallback each tool is its own
  origin, so the choice is per tool. That is expected, not a bug.
- `ThemeToggle.jsx` also listens for the `storage` event, so two tabs stay in
  sync.

### 2b. Copy these files, do not rewrite them

| file | copy from | rule |
|---|---|---|
| `ResultsPane.jsx` | `amr_plus_gui` | byte-identical; enforced |
| `useResults.js` | `amr_plus_gui` | byte-identical; enforced |
| `ResultsPane.css` | `amr_plus_gui` | byte-identical; enforced |
| `App.css` | `mlst_gui` | copied verbatim, then append tool-specific rules at the end |
| `ThemeToggle.jsx` | `mlst_gui` | copied verbatim |
| `backend/app/request_safety.py` | any tool | byte-identical |

`bin/check-shared-frontend.sh` in the umbrella proves the first three match and
**advises** on the next two. Run it before tagging.

Every Results-pane class is `rp-` prefixed and `App.css` is never touched by it.
That is deliberate: `mhc_gui` already defines `.qc-badge` / `.qc-pass` /
`.qc-review` / `.qc-fail` with *different* meanings, and an unprefixed port
would silently restyle its genotype table.

### 2c. Header markup

```jsx
<header className="app-header">
  <div className="app-brand">
    <span className="app-logo" role="img" aria-label="…" style={{ fontSize: 30 }}>🧬</span>
    <div>
      <h1>ToolName <span className="version-tag">v{APP_VERSION}</span></h1>
      <p>One-line description of what the tool does</p>
    </div>
  </div>
  <div className="header-actions">
    <ThemeToggle />
    <div className="status-pill">
      <span className="dot" data-state={jobStatus} />
      <span>{statusText}</span>
    </div>
  </div>
</header>
```

`data-state` drives the dot colour: `running` (pulsing accent), `succeeded`
(green), `failed`/`error` (red), anything else (neutral).

### 2d. Page rhythm and class vocabulary

`<main className="layout">` holding, in this order:

1. `<section className="status-strip">` — `.status-item` / `.status-label` / `.status-value`
2. Projects + Inputs — `.row-header` + `.row-grid.row-grid-split`, panels of
   `.panel` > `.panel-header` > `h2`
3. **Results** — `.row-header` with a Hide/Show `.ghost` button, then
   `.row-grid.row-grid-split` holding **Current Run** (left, a `.panel`) and
   `<ResultsPane …/>` (right)
4. **Pipeline Log** — `.row-header` + `.row-grid.row-grid-single`, `.log` /
   `.log-line` / `.log-meta`

Use the existing vocabulary rather than inventing classes:
`.panel .panel-header .panel-actions .row-header .row-grid .row-grid-split
.row-grid-single .status-strip .status-item .list .list-item .list-title
.list-meta .sample-list .sample-item .selection-box .empty-msg .note .muted
.ghost .run-btn .dropzone .scope-badge .scope-shared .scope-personal
.badge-pe .badge-se .version-tag .alert-banner`.

Colours come from CSS variables only (`--bg --panel --panel-2 --text --muted
--accent --accent-2 --accent-text --danger --success --warning --border
--shadow`), each overridden under `html[data-theme="dark"]`. **Never hard-code a
hex in a component** — that is how a tool ends up unreadable in dark mode.

---

## 3. The Results pane contract

Every tool shows *every completed sample*, not just the last one clicked.

**Frontend** (see `mlst_gui/frontend/src/App.jsx`):

```jsx
const RESULT_COLUMNS = [
  { key: "scheme", label: "Scheme" },
  { key: "st",     label: "ST" },
  { key: "alleles", label: "Alleles", align: "right" },
  { key: "flags_note", label: "Note" },
];
const results = useResults(activeProject);
…
<ResultsPane project={activeProject} results={results}
             columns={RESULT_COLUMNS}
             labels={{ entity: "sample", sampleHeader: "Sample" }} />
```

`columns[].key` reads from the row's `metrics` object. Nothing else about the
pane is per-tool.

**Backend** — implement these four endpoints:

| endpoint | returns |
|---|---|
| `GET /api/projects/{name}/results` | `{project, tool, rows: [...]}` |
| `GET /api/projects/{name}/results.csv` | CSV honouring `start`/`end`/`q` |
| `GET /api/projects/{name}/results.xlsx` | same, XLSX |
| `GET /api/projects/{name}/file?path=…&inline=0` | one file, containment-checked |

Row shape (one dict per completed run dir under `<project>/<tool>/`):

```python
{
  "sample":     d.name,
  "status":     _rp_status(d),        # succeeded | failed | …
  "run_date":   _rp_finished_at(d),   # ISO, from run_manifest.json — NOT mtime
  "run_dir":    str(d),
  "flags":      {"level": "pass|review|fail", "reasons": [...]},
  "metrics":    {...},                # whatever RESULT_COLUMNS asks for
  "files":      _collect_<tool>_files(d, include_all),
  "cross_tool": _rp_cross_tool(name, project_dir, d.name),
}
```

Four things that are easy to get wrong and matter:

1. **`run_date` comes from `run_manifest.json`, not filesystem mtime.** An rsync
   or a later re-read touches files long after the analysis ran.
2. **`flags.level` must reflect `run_manifest`'s `return_code`.** A run whose
   analysis exited non-zero reads **FAIL**, with the reason — it must not
   inherit a `qc.json` verdict that only graded the *input*. Three separate
   "failed run reported as success" bugs in this suite were all this shape.
3. **`cross_tool` picks up sibling output that lives outside your run dir** —
   e.g. a Kraken Krona chart under `<project>/kraken/`. That is why such files
   showed in Projects but never in Results.
4. **Filter server-side too** (`_rp_filter`), so a CSV/XLSX download matches
   what is on screen.

**Projects check-all**: tri-state checkbox plus a sample filter, acting **only
on rows the filter leaves visible**. A select-all that also queues hidden
samples is how someone runs 900 samples instead of the 3 they filtered to.

---

## 4. Backend contract

- **`app.main:app`**, `cd backend`, uvicorn — that is how `tool_launch.py` starts
  every tool. Do not deviate.
- **All URLs relative** (`./api/…`), Vite `base: "./"`. This is what lets the
  same build serve standalone, behind OOD's `/rnode`, and under the consolidated
  dashboard's `/t/<tool>/` sub-path. `bdtools lint` fails a build whose
  `index.html` uses root-absolute (`src="/…"`) asset URLs.
- **`GET /api/jobs`** returns a JSON *list* of objects with at least `id`,
  `name`, `status`. Active states: `queued`, `running`, `stopping`, `cancelling`.
  The dashboard treats a missing/unreachable/malformed endpoint as **unsafe** and
  blocks restart, shutdown and updates. Get this wrong and every lifecycle
  action on the whole suite jams.
- **`install_request_safety(app)`** from the copied `request_safety.py`.
- **Static mount is last** and the index carries
  `Cache-Control: no-cache, no-store, must-revalidate` — otherwise a browser
  serves a stale SPA after an update.
- **No hard-coded site paths.** Read `BDTOOLS_TOOLS_ROOT`, `BDTOOLS_DB_ROOT`,
  `BDTOOLS_SHARED_PROJECTS_ROOT`, `BDTOOLS_HOME` — the launcher resolves them
  from the machine's recorded site config via `bin/lib/site_paths.py`.
  `amr_plus_gui` is the reference implementation and is at **zero** literals; a
  ratchet test in `tests/test_dashboard_safety.py` holds every tool's count so
  it can only go down. A new tool starts at zero and must stay there — add its
  name with budget `0` to `_SITE_PATH_BUDGET`.
- **File-serving endpoints must containment-check**: `.resolve()` the target,
  then require `root == target or root in target.parents`. Copy the pattern from
  `amr_plus_gui`'s `api_project_file` / `api_job_file`.
- **Never build a shell string from a filename.** Use a list argv, or
  `shlex.quote` every interpolated value. A sample name derived from a basename
  is user-controlled: interpolating it into `os.system(f'…')` is injectable, and
  it breaks on ordinary spaces and brackets long before anyone tries. Some older
  pipeline scripts in this suite predate that rule — see `drb3_type.py` in
  `mhc_gui` for the pattern to copy (`q = shlex.quote`, applied to every path).
- **Pin the bioconda versions that matter** in `environment.yml`. Unpinned
  `ncbi-amrfinderplus` is why one machine got 3.12.8 and another 4.2.7, and why
  AMR silently produced no calls against a format-4 database.

---

## 5. Umbrella wiring — the checklist people forget

Adding the tool repo is not enough; these are all in
`bioinformatic_diagnostic_tools`:

1. **`tools.yml`** — `name`, `repo`, `version` (a tag), `ood_apps`, `dev_apps`,
   `env`, optional `databases`.
2. **`bin/lib/suite_common.py`** — add to `PRETTY` (display name) and `BLURB`
   (one line, shown on every dashboard card). Add to `CAVEAT` **only** if the
   tool is not yet validated for diagnostic use — that renders the red
   development banner.
3. **`bin/lib/requirements.py`** — declare `modules`, `binaries`, optional
   `asset_dirs` / `databases` / `os` / `platform_unavailable`. This is what
   `bdtools doctor` checks. A tool absent from here reports green while being
   unrunnable (that is exactly how `ksnp_gui` passed doctor while every run
   exited 127).
4. **`bin/lib/tool_launch.py`** — only if the tool deviates from `DEFAULTS`. Add
   a `SPEC` entry for a shared sibling env, an extra `path_prepend` (vendored
   binaries), or `set_conda_prefix`. Anything in `path_prepend` gets resolved,
   not assumed — and warns loudly at launch when missing.
5. **`tests/<tool>/test.yml` + `expected.json`** — a known sample and its golden
   result, so `bdtools test <tool>` can validate the deployment. Tools with no
   golden test SKIP; that is allowed but say so deliberately.
6. **`ood/apps/<tool>/`** in the *tool* repo — `cluster` must come from site
   config, never a literal. `install-server.sh` rewrites the Kapur Lab literals
   through its `subst()`.
7. **`docs/TRAINING.md`** — a module, if the tool is user-facing.

---

## 6. Verify before tagging

```bash
bin/check-shared-frontend.sh     # shared pane identical; look files advisory
bin/bdtools lint <tool>          # declared deps vs actual imports/programs
bin/bdtools install <tool>       # clean build from the pin
bin/bdtools doctor <tool>        # env, modules, programs, databases
bin/bdtools test <tool>          # golden sample
bin/bdtools dashboard            # card renders, tool opens, Results pane lists samples
python3 -m pytest tests/test_dashboard_safety.py -q
```

Then tag the tool repo (`vN.N.N`), bump the pin in `tools.yml`, and commit.

---

## 7. Traps that have cost real time

1. **Node ≥20.19 for the frontend build** (vite 8). Older Node fails the build
   and `install-local.sh` falls back to the committed `dist/` with a warning —
   so you can silently ship a stale UI. This server has Node 18; use a scratch
   env: `conda create -p <dir> -c conda-forge 'nodejs>=22'`.
2. **Run bioconda tools with `<env>/bin` on PATH**, not just the binary path —
   otherwise Perl tools (`mlst`) grab system Perl and die on
   `List::MoreUtils`.
3. **`export CONDA_PREFIX=<env>` wherever `amrfinder` runs**, or it cannot find
   its database. That is what `set_conda_prefix` in `tool_launch.SPEC` does.
4. **Use `conda`/libmamba, not mamba 2.x**, for large bioconda solves — mamba 2.5
   ran amr_plus_gui's env at 100% CPU for 2h+ without finishing.
5. **Large vendored payloads are gitignored**, so a fresh clone or a feature
   worktree has an empty `vendor/`. Never prepend such a path to `PATH` without
   an existence check; declare it as `asset_dirs` so doctor can see it too.
6. **`git fetch origin` updates nothing in a managed checkout** — they are
   shallow with a pinned refspec. Use
   `git fetch origin 'refs/heads/main:refs/remotes/origin/main' --tags`.
7. **`bdtools update` refuses to touch a checkout outside
   `<BDTOOLS_HOME>/checkouts`.** Server trees under a site `tools/` root are
   reconciled deliberately via `install-server.sh`, which refuses to build
   source that disagrees with the manifest pin.
8. **The `?t=…` in the local dashboard URL is not a bug** — it is the session
   key, on by default except on macOS/WSL. Override with
   `BDTOOLS_DASHBOARD_AUTH=0|1`.
9. **The local dashboard answers only to `127.0.0.1`/`localhost` Host headers**
   (DNS-rebinding guard). If you front it with another name, set
   `BDTOOLS_ALLOWED_HOSTS=that.name`.
