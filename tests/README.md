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
| `mlst_gui` | ✅ validated | *E. coli* K-12 MG1655 (`GCF_000005845.2`) | scheme `ecoli`, ST `10` |
| `amr_plus_gui` | ✅ validated | *K. pneumoniae* HS11286 (`GCF_000240185.1`) | organism + acquired AMR genes detected |
| `irma_gui` | ✅ validated | influenza-A Illumina paired SRA | module `FLU`, subtype, ≥7 segments |
| `genoflu_gui` | ✅ validated | H5N1 2.3.4.4b isolate (8 GenBank segments) | genotype `2.3.4.4b…`, 8/8 segments |
| `ksnp_gui` | ⏳ no spec → SKIP | — | needs multi-genome fetch (≥2 inputs) |
| `vsnp_gui` | ⏳ no spec → SKIP | — | needs an Excel→JSON adapter + vsnp3 refs gating |
| `kraken_id_parse_gui` | ⏳ no spec → SKIP | — | writes to CWD; needs Kraken2 + BLAST DB gating |
| `ncbi_submit_gui` | — not tested by design | — | submission tool — no diagnostic output |

The four ⏳ tools currently print `SKIP <tool>: no test spec`. Adding them is the
next increment — see *Extending the suite*.

## Spec format — `tests/<tool>/test.yml`

A flat `key: value` map (no nesting), parsed dependency-free by
[`lib/readspec.py`](lib/readspec.py). Keys:

| key | meaning |
|---|---|
| `tier` | 1 = bundled DB (always runnable); 2 = needs an external DB (SKIP when absent) |
| `summary` | one line shown when the test runs |
| `fetch` | `genome` (NCBI assembly FASTA), `genbank` (concat nuccore records), or `sra` (paired FASTQ) |
| `accession` | one accession, or an inline `[a, b, …]` list (for `genbank`, the segment records) |
| `run_cmd` | the headless pipeline command, with placeholders substituted by `bin/test.sh` |
| `result_file` | the JSON file (relative to the run's `{out}` dir) compared against `expected.json` |
| `db_check` | (tier 2) a path that must exist or the tool SKIPs |
| `db_hint` | (tier 2) message telling the user how to stage the missing DB |

`run_cmd` placeholders: `{python}` (the tool's env Python), `{tooldir}` (the tool
checkout), `{testsdir}` (this `tests/` dir), `{out}` (the run output dir),
`{fasta}` (the fetched genome/genbank FASTA), `{r1}`/`{r2}` (the fetched reads).
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

## Extending the suite (the ⏳ tools)

- **ksnp_gui** — kSNP needs ≥2 genomes. Add a `fetch: genomes` (plural) path that
  fetches each accession in an `accession: [a, b, c]` list and passes them as
  `--inputs`; validate `run_manifest.json` `results.snps_all`.
- **vsnp_gui** — vSNP3 step1 emits an Excel stats workbook, not JSON, and needs
  the vsnp3 reference set. Add a small Excel→JSON adapter (like
  `lib/amr_summarize.py`) emitting the reference/spoligotype call, and a
  `db_check` on `/srv/kapurlab/refs/vsnp3/reference_options`.
- **kraken_id_parse_gui** — writes outputs to the CWD (no `--outdir`) and needs a
  Kraken2 + BLAST DB. Run it inside `{out}`, add `db_check`/`db_hint` for both
  DBs, and validate `run_manifest.json`.
