# Post-deployment validation suite

This directory is the **diagnostic-validation baseline** for the suite. Each
covered tool has a fixed, public input sample (SRA or GenBank) and a curated
*expected* (golden) result. After any new deployment or update, run:

```bash
bdtools test all          # every covered tool: download sample, run, diff vs golden
bdtools test mlst_gui     # just one tool
```

Each tool reports **PASS / FAIL / SKIP**:

- **PASS** — the headline diagnostic fields matched the golden result (within the
  tolerances below).
- **FAIL** — a field differed; the runner prints `expected vs actual` per field.
  Do **not** edit the golden to make it pass — surface the discrepancy.
- **SKIP** — the tool isn't installed here, a required reference DB is absent, or
  it has no spec yet (see *Coverage* below). SKIPs never fail the run.

`bdtools test` exits non-zero only if a covered, non-skipped tool FAILs.

## Coverage

| Tool | Status | Sample | Headline validated |
|---|---|---|---|
| `mlst_gui` | ✅ tier 1 | *E. coli* K-12 MG1655 (`GCF_000005845.2`) | scheme `ecoli`, ST `10` |
| `amr_plus_gui` | ✅ tier 1 | *K. pneumoniae* HS11286 (`GCF_000240185.1`) | organism + acquired AMR genes (blaKPC/SHV/CTX-M/rmtB) |
| `irma_gui` | ✅ tier 1 | influenza-A Illumina paired SRA (`SRR39145037`) | module `FLU`, subtype `H5N1`, 8 segments |
| `genoflu_gui` | ✅ tier 1 | H5N1 2.3.4.4b cattle isolate (8 GenBank segments) | genotype `B3.13`, 8/8 segments |
| `ksnp_gui` | ✅ tier 1 (Linux only) | 3 *Listeria monocytogenes* genomes (EGD-e/F2365/10403S) | snps_all ~44309, core ~34713 |
| `kraken_id_parse_gui` | ✅ tier 2 (Kraken2 DB) | *M. tuberculosis* reads (`SRR28623786`) | genus `Mycobacterium` ≥90% |
| `vsnp_gui` | ✅ tier 2 (vsnp3 refs) | *M. bovis* reads (`SRR1791695`) | best-ref `Mycobacterium_AF2122`, spoligotype `SB0673` |
| `ncbi_submit_gui` | — not tested by design | — | SRA/GenBank submission tool — no diagnostic output |

All seven diagnostic GUIs are validated. The tier-2 tools need an external
reference DB (Kraken2/BLAST, or the vsnp3 reference set) and SKIP cleanly when it
is absent — so on a fresh laptop they SKIP while the tier-1 tools PASS, and on a
machine with the DBs all seven PASS.

## Spec format — `tests/<tool>/test.yml`

A flat `key: value` map (no nesting), parsed dependency-free by
[`lib/readspec.py`](lib/readspec.py). Keys:

| key | meaning |
|---|---|
| `tier` | 1 = bundled DB (always runnable); 2 = needs an external DB (SKIP when absent) |
| `summary` | one line shown when the test runs |
| `fetch` | `genome` (NCBI assembly FASTA), `genomes` (list → `{inputs}`), `genbank` (concat nuccore records), or `sra` (paired FASTQ) |
| `accession` | one accession, or an inline `[a, b, …]` list (for `genbank`/`genomes`) |
| `requires_os` | optional; e.g. `linux` — SKIP on other OSes (tool ships platform-specific binaries) |
| `run_cmd` | the headless pipeline command, with placeholders substituted by `bin/test.sh` |
| `result_file` | the JSON file (relative to the run's `{out}` dir) compared against `expected.json` |
| `db_check` | (tier 2) a path — or a `[a, b]` list of candidates, `{tooldir}` expanded — that must exist, else the tool SKIPs |
| `db_hint` | (tier 2) message telling the user how to stage the missing DB |

`run_cmd` placeholders: `{python}` (the tool's env Python), `{tooldir}` (the tool
checkout), `{testsdir}` (this `tests/` dir), `{out}` (the run output dir),
`{fasta}` (the fetched genome/genbank FASTA), `{inputs}` (space-joined multi-genome
list), `{r1}`/`{r2}` (the fetched reads).
The tool's `env/bin` is placed on `PATH` and `{tooldir}/bin` on `PYTHONPATH` so
the wrapped binaries and sibling modules resolve.

## Golden format — `tests/<tool>/expected.json`

Only the **headline** fields we validate, keyed by a dotted path into the result
JSON. Values use the matcher DSL in [`lib/compare.py`](lib/compare.py):

| form | example | meaning |
|---|---|---|
| exact | `"ecoli_achtman_4"`, `10`, `true` | equality (numbers compared numerically) |
| threshold | `">=7"`, `"<100"` | numeric comparison |
| range | `"0.5..0.8"` | inclusive numeric range |
| approx | `"~4400000"` | within 5% (or ±1 for small ints) |
| regex | `"/2\\.3\\.4\\.4b/"` | regex search on the stringified value |
| contains | `["blaKPC", "blaSHV"]` | actual (list or string) must contain each |

Prefer **tolerant** matchers (regex / contains / thresholds) for anything a DB
version bump could nudge, so a routine reference-DB update doesn't false-FAIL the
baseline. Keys beginning with `__` are ignored — use `__note` for provenance.

## Recording / re-establishing a golden

On a **known-good** box (envs installed, DBs present):

```bash
BDTOOLS_TOOLSDIR=/srv/kapurlab/tools bdtools test <tool> --record
```

This downloads the sample, runs the pipeline, and writes the raw result to
`tests/<tool>/expected.json.recorded.json` instead of comparing. **Eyeball it**,
then curate the headline fields (with the matchers above) into
`tests/<tool>/expected.json` and commit. Never auto-promote the recorded file to
the golden without review — the golden is a diagnostic claim.

`--keep` keeps the per-tool download/work dir (under `~/.local/share/bdtools/testwork`)
for inspection; `--workdir DIR` relocates it.

## Validating on macOS Apple Silicon (Rosetta)

On Apple Silicon the env is built as **osx-64 and run under Rosetta 2** (see
[docs/INSTALL_LOCAL.md](../docs/INSTALL_LOCAL.md)) — a different execution
environment than the Linux box these goldens were recorded on. For **real
diagnostic use on a Mac**, treat `bdtools test all` as the parity check: after
installing, run it and confirm the covered tools **PASS**. The headline calls are
deterministic and arch-independent (MLST ST 10; GenoFLU B3.13; IRMA FLU/H5N1; AMR
gene stems with a `>=` count; kSNP SNP counts; vSNP spoligotype SB0673), so a
PASS confirms the Rosetta env reproduces the validated baseline. A FAIL on a Mac
is a real signal — surface it, don't relax the golden. (Expect Rosetta runs to be
slower than native; the calls, not the wall-clock, are what's validated.)

Two expected macOS **SKIPs**, not failures:
- **`ksnp_gui`** — kSNP4 ships Linux-only ELF binaries (Rosetta translates macOS
  x86_64, not Linux ELF), so the spec carries `requires_os: linux` and SKIPs on
  macOS. Run kSNP on Linux or an OOD deployment.
- **SRA-based tests** (`irma_gui`, `kraken_id_parse_gui`, `vsnp_gui`) need
  **sra-tools** (`prefetch`/`fasterq-dump`). `fetch_sra` finds them on `PATH` or in
  any conda env; if none has them the test SKIPs with a hint
  (`conda install -n base -c bioconda sra-tools`).

## Adapters (non-JSON tool output)

Some tools don't emit a single JSON, so a small adapter (run as the last step of
the spec's `run_cmd`) reduces their output to a comparable JSON:

- `lib/amr_summarize.py` — AMRFinderPlus `amrfinder.tsv` + `organism_detection.json`
  → `amr_result.json` (organism, AMR gene count + symbols).
- `lib/kraken_top.py` — the Kraken2 report → `kraken_top.json` (top genus/species,
  classified %). Validated at genus level because Kraken2 cannot resolve the
  near-identical MTB-complex species.
- `lib/vsnp_excel.py` — the vSNP3 step1 stats workbook (headers row / values row)
  → `vsnp_result.json` (best reference, spoligotype octal/SB, group). Run it with
  the vsnp3 env Python (needs `openpyxl`).

## Gotchas worth knowing

- **kSNP4** crashes (`inline_frequency_check`: `mean()` of `None`) on atypically
  large/bloated assemblies whose k-mer coverage histogram has no valley — use
  clean, normally-sized reference genomes (the spec uses ~3 Mb *Listeria* refs).
- **Multi-genome fetch** isolates each accession's NCBI Datasets extraction in its
  own dir; a shared `ncbi_dataset/` dir would let the cat-glob mix sibling genomes.
