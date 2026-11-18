# Building a pathogen add-on

A pathogen add-on puts everything specific to one pathogen (FMDV, HPAI, ASF, …)
in a repository of its own, while vsnp_gui stays a tool for running vsnp3 and
viewing its results. bdtools ships vsnp_gui and every add-on to everyone. A
vsnp_gui project names its pathogen when it is created, or names none, and a
project that names none runs exactly as vsnp_gui always has.

Every add-on is built from the same template and checked by the same command.
Adding the fifth pathogen is therefore the same job as adding the first.

- [The shape](#the-shape)
- [Start one](#start-one)
- [The contract (version 1)](#the-contract-version-1)
- [What vsnp_gui does with it](#what-vsnp_gui-does-with-it)
- [Installing, pinning, updating](#installing-pinning-updating)
- [Release checklist](#release-checklist)
- [How the template maps to FMDV, HPAI and ASF](#how-the-template-maps-to-fmdv-hpai-and-asf)

## The shape

```
<id>_addon/
  addon.json                    the manifest (contract 1)
  conda_setup/environment.yml   the add-on's own env: every program its hooks call
  deploy/build_library.sh       builds the reference library under <db root>/addons/<id>
  hooks/choose_reference.py     each sample's vsnp3 reference, before alignment
  hooks/prepare_reads.py        optional: the reads vsnp3 should align (e.g. primer clipping)
  hooks/after_sample.py         the sample's Results row and report, after vsnp3
  hooks/after_run.py            the run's summary and report
  actions/*.py                  on-demand jobs from the Pathogen tab (trees, retyping…)
  lib/addon_sdk.py              vendored helpers; identical in every add-on
  tests/contract/fixture/       the project `bdtools addon-check` runs every hook on
  tests/known_answer/run.py     optional: a simulated run with an answer key
  .github/workflows/contract.yml  CI: env, library, unit tests, contract check
```

The id is lower case (`fmdv`, `hpai`, `asf`). The repository and tools.yml
name is `<id>_addon`.

## Start one

```bash
bin/bdtools new-addon hpai --name "Highly pathogenic avian influenza" --dest ~/git/hpai_addon
```

This copies `templates/pathogen_addon/` with the id and name filled in. The
copy is a working add-on (one example reference, a placeholder chooser, a
report per sample, a run report, an example action), so it installs and passes
`addon-check` before any pathogen-specific code is written. Then replace the
TODOs in `deploy/build_library.sh`, `hooks/choose_reference.py` and
`hooks/after_sample.py`.

## The contract (version 1)

The prose is here. The code that enforces it is vsnp_gui's `app/addons.py`
(the runtime) and `app/addon_check.py` (the check). `bdtools addon-check` runs
vsnp_gui's own checker, so there is no second copy of the rules to drift.

### addon.json

| Field | Meaning |
|---|---|
| `contract` | `1` |
| `id`, `tool`, `name`, `version` | `fmdv`, `fmdv_addon`, `Foot-and-mouth disease virus`, `0.1.0` |
| `consumer` | `vsnp_gui` |
| `env` | the env's name for conda fallbacks (normally the tool name) |
| `sample_naming` | `folder` (default): vsnp3 gets `-n <sample folder>` |
| `requires` | `{"binaries": [...], "modules": [...]}` graded by `bdtools doctor` in the add-on's env |
| `library` | `markers` that prove the library is built; `reference_options`, `vcf_db_folders` subfolders |
| `scopes` | `{"file": "scopes.json", "label", "default"}`: the project form's scope picker |
| `hooks` | `choose_reference` and `after_sample` required; `prepare_reads`, `after_run` optional |
| `actions` | `[{id, label, description, command, params: [{name, label, choices, default, fixture}]}]` |
| `qc_thresholds` | a QC threshold layer for this pathogen's projects (e.g. `genome_coverage`) |
| `columns` | `[{key, label, unit}]`: Step 1 Results columns, read from each row's metrics |
| `reports` | `{"sample": "report.html", "run": "run_report.html"}` |

### Hooks

vsnp_gui runs every hook the same way:

```
<add-on env>/bin/python hooks/<hook>.py --context <file.json>      (cwd = the hook's output folder)
```

The add-on env's `bin/` is first on PATH, the env's architecture prefix
(`/usr/bin/arch -x86_64` for an osx-64 env on Apple Silicon) is applied, and
nothing of vsnp_gui is on PYTHONPATH. The context file holds everything a hook
may use:

- the project and the pathogen folder;
- the sample, its reads and read type;
- vsnp3's Step 1 outputs (stats workbook, alignment folder, zero-coverage VCF, BAM);
- the sample's QC row and verdict, and the thresholds;
- the reference folder, the library, the scope and a thread count.

An add-on never reads vsnp_gui's config or imports its modules.

| Hook | When | Writes | If it fails |
|---|---|---|---|
| `choose_reference` | per sample, before vsnp3 | `reference.json` `{"reference_dir": …}` | the sample is not aligned; the reason shows in Step 1 Results |
| `prepare_reads` | per sample, after choose | `reads.json` `{"reads": [R1, R2?]}`, same number of mates | vsnp3 aligns the original reads; the sample is marked review |
| `after_sample` | per sample, after vsnp3 exits 0 | `result.json`: metrics, flags `{level, reasons}`, files | the vsnp3 result stands; the sample is marked review |
| `after_run` | once per batch | `summary.json` and the run report | noted in the batch log |
| action | from the Pathogen tab | `action.json` `{status, files, summary}` in its run folder | the run shows failed |

Where outputs go:

- Sample outputs: `<project>/pathogen/<id>/samples/<sample>/`.
- Run outputs: `<project>/pathogen/<id>/`.
- Action outputs: `<project>/pathogen/<id>/runs/<stamp>_<action>/`.

Never write into vsnp3's folders or under `step2/`, where every folder is
listed as a vSNP group.

Files must be real files: the viewers resolve symlinks and refuse anything
outside the project. HTML reports must be self-contained. A tree's `.xlsx`
tables sit next to its `.tre` or `.nwk`, where the tree viewer lists them.

### The library

```
<db root>/addons/<id>/
  reference_options/<name>/<name>.fasta   (+ .gbk/.gff)   what the chooser may return
  vcf_db_folders/<name>/<db>/*_zc.vcf                     Step 2 databases, per reference
  scopes.json                                             {"label", "default", "options": [{value, label, children}]}
  <markers>                                               e.g. LIBRARY_VERSION, written last
```

Rules for the library:

- It is **read-only at run time**. Build everything up front, including
  coordinates, primer BEDs and typing databases; caches go in the project.
- Reference folder names match `[A-Za-z0-9][A-Za-z0-9_-]*`. No dots: vsnp3
  cuts a name at its first dot, so `MTBC0_v1.1` becomes `MTBC0_v1`.
- Each folder holds `<name>.fasta`.
- No name may already exist in the site's own reference locations. The site's
  folder would win, and Step 2 would open it instead. `addon-check` refuses a
  clash.
- The library is listed last among vsnp_gui's reference locations, cannot be
  removed there, and its references are tagged so the stock pickers leave
  them out.

## What vsnp_gui does with it

- **Project form:** a pathogen select (the site default comes from
  `DEFAULT_PATHOGEN`) and the add-on's scope picker. The choice is stored in
  project.json as `pathogen` and `pathogen_scope`.
- **Step 1:** around each sample's vsnp3 call, the batch script runs the hooks
  through `app.addon_run`. Running them inside the batch keeps Stop, the
  restart guard, the parallel pool and Live Logs working unchanged.
  - `-t` comes from the chooser, `-n` from the folder name.
  - A sample is complete only once `after_sample` has run for its current
    alignment (`.provenance/addon_done`).
  - Provenance records the add-on's id, version and commit, every hook's
    command, and the chosen reference by path with its manifest hash.
- **Step 1 Results:**
  - the add-on's columns;
  - its flags joined to the QC verdict (the more severe level wins);
  - its thresholds as a layer: defaults < user < add-on < project;
  - a row stamped for another alignment shows as pending.
- **Pathogen tab:**
  - the summary;
  - each sample's row and reports;
  - samples with no reference chosen;
  - the actions and their runs, whose trees and tables open in vsnp_gui's
    viewers;
  - "Re-run add-on steps".
- **Step 2:** one comparison per reference. You pick the comparison
  reference, the add-on's databases for it are offered, and samples on
  another lineage are left out of that run and listed, never refused or
  offered for deletion.

## Installing, pinning, updating

tools.yml entry:

```yaml
  - name: fmdv_addon
    repo: https://github.com/kapurlab/fmdv_addon.git
    version: v0.1.0
    updates: install
    addon_for: [vsnp_gui]
    env: fmdv_addon
    disk_gb: 2
    packages: [bioconda::minimap2=2.28, bioconda::samtools=1.21]
```

What bdtools does with it:

- `bdtools install fmdv_addon` builds `env/` from `conda_setup/environment.yml`
  (the same generic build as any tool) and enforces `packages:` exactly.
- It then runs `deploy/build_library.sh <db root>/addons/fmdv` with the env on
  PATH and records the path in `.library_path`.
- The add-on has no dashboard card. It is listed on vsnp_gui's card, and
  `bdtools local fmdv_addon` refuses to start it.
- `bdtools doctor` grades its env, its `requires`, and its library markers. It
  is reported as an optional sibling of vsnp_gui: vsnp_gui runs without it,
  and a project naming its pathogen does not.
- Updating an add-on also stops vsnp_gui, whose jobs run the add-on's env.
  Restart vsnp_gui after installing an add-on: the launcher hands the add-on's
  env over at start.

## Release checklist

1. `env/bin/python tests/test_hooks.py` (the add-on's own unit tests).
2. `bin/bdtools addon-check <id>` passes: manifest, env, library, every hook
   and action on the fixture.
3. `bin/bdtools addon-check <id> --known-answer` passes, if the add-on ships a
   known-answer run.
4. CI (`contract.yml`) is green on the commit you tag.
5. Bump `version` in addon.json, tag `vX.Y.Z`, push the tag, then bump the pin
   in tools.yml.

## How the template maps to FMDV, HPAI and ASF

| | FMDV | HPAI | ASF |
|---|---|---|---|
| Library | 79 NanoFMDV lineage genomes, VP1 panel, GenBank VP1 sets, primers | H5 and segment references | p72 / genotype reference set |
| `choose_reference` | minimap2 vote on the VP1 panel | per segment; builds a combined reference in `pathogen/hpai/refs/` | sourmash (about scaled 100) or minimap2 on a p72 panel |
| `prepare_reads` | primer clipping | — | — |
| `after_sample` | consensus, VP1 typing, sample report | reads IRMA and GenoFLU output from `<project>/irma`, `<project>/genoflu` | p72 genotype, sample report |
| actions | VP1 tree, retype | HA tree | p72 tree |
| Step 2 | vsnp3, one comparison per lineage reference | vsnp3 | vsnp3 |
