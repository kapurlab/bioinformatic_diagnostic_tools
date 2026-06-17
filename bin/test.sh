#!/usr/bin/env bash
# test.sh — post-deployment validation: run a known sample through a tool and
# diff the result against the committed expected (golden) result.
#
#   test.sh <tool|all> [--record] [--keep] [--workdir DIR]
#
# For each target tool with a tests/<tool>/test.yml spec:
#   1. SKIP if the tool isn't installed, or (Tier 2) its reference DB is absent.
#   2. Download the spec's fixed SRA/GenBank accession (cached under workdir).
#   3. Run the tool's headless pipeline (spec run_cmd) using the tool's own env.
#   4. Compare result_file against tests/<tool>/expected.json (tests/lib/compare.py).
#      With --record, write the produced result_file's headline fields to
#      expected.json instead of comparing (establish a new golden on a known-good
#      box — eyeball it before committing).
#
# Exit: 0 if every non-skipped tool PASSES; 1 if any FAIL. SKIPs don't fail.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TESTS_DIR="${REPO_DIR}/tests"
source "${TESTS_DIR}/lib/fetch.sh"

RECORD=0; KEEP=0
WORKDIR="${BDTOOLS_TEST_WORKDIR:-${BDTOOLS_HOME}/testwork}"
TARGET=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --record)  RECORD=1; shift;;
    --keep)    KEEP=1; shift;;
    --workdir) WORKDIR="$2"; shift 2;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)        die "unknown option: $1";;
    *)         TARGET="$1"; shift;;
  esac
done
[[ -n "${TARGET}" ]] || die "usage: bdtools test <tool|all> [--record] [--keep]"

spec() { python3 "${TESTS_DIR}/lib/readspec.py" "$1" "$2"; }

# Resolve the tool's env python + its bin dir (so the pipeline's CLI deps — mlst,
# shovill, kraken2, ... — are found). Mirrors install-local.sh:resolve_python.
resolve_env_bin() {  # tool dir -> echoes "<python>|<bindir>" or empty
  local dir="$1" envname="$2"
  if [[ -x "${dir}/env/bin/python" ]]; then echo "${dir}/env/bin/python|${dir}/env/bin"; return 0; fi
  local conda; conda="$(detect_conda 2>/dev/null || true)"
  if [[ -n "${conda}" && -n "${envname}" ]] \
     && "${conda}" env list 2>/dev/null | awk '{print $1}' | grep -qxF "${envname}"; then
    local prefix; prefix="$("${conda}" run -n "${envname}" sh -c 'echo $CONDA_PREFIX')"
    [[ -n "${prefix}" ]] && { echo "${prefix}/bin/python|${prefix}/bin"; return 0; }
  fi
  echo ""
}

run_one() {  # tool -> prints a status; sets RC_FAIL on FAIL
  local tool="$1"
  local sdir="${TESTS_DIR}/${tool}" yml exp
  yml="${sdir}/test.yml"; exp="${sdir}/expected.json"
  if [[ ! -f "${yml}" ]]; then echo "SKIP  ${tool}: no test spec (tests/${tool}/test.yml)"; return 0; fi

  local tier summary fetch acc run_cmd result_file db_check db_hint
  tier="$(spec "${yml}" tier)"; summary="$(spec "${yml}" summary)"
  fetch="$(spec "${yml}" fetch)"; acc="$(spec "${yml}" accession)"
  run_cmd="$(spec "${yml}" run_cmd)"; result_file="$(spec "${yml}" result_file)"
  db_check="$(spec "${yml}" db_check)"; db_hint="$(spec "${yml}" db_hint)"

  log "${tool}  (tier ${tier:-?}) — ${summary}"

  local dir; dir="$(tool_dir "${tool}")"
  [[ -d "${dir}/.git" ]] || { echo "SKIP  ${tool}: not installed at ${dir} (run: bdtools install ${tool})"; return 0; }

  # db_check may be one path or a space/comma list of candidates (e.g. a local
  # install path AND a server path); {tooldir} expands to the tool checkout. The
  # DB counts as present if ANY candidate exists.
  if [[ -n "${db_check}" ]]; then
    local cand found=0
    for cand in ${db_check}; do
      cand="${cand//\{tooldir\}/${dir}}"
      [[ -e "${cand}" ]] && { found=1; break; }
    done
    if [[ ${found} -eq 0 ]]; then
      echo "SKIP  ${tool}: required reference DB not found (looked for: ${db_check})"
      [[ -n "${db_hint}" ]] && info "  ${db_hint}"
      return 0
    fi
  fi

  local envname pybin py envbin
  envname="$(manifest_get "${tool}" env)"
  pybin="$(resolve_env_bin "${dir}" "${envname}")"
  [[ -n "${pybin}" ]] || { echo "SKIP  ${tool}: no usable env (looked for ${dir}/env and conda '${envname}')"; return 0; }
  py="${pybin%%|*}"; envbin="${pybin##*|}"

  # 1. fetch sample
  local work="${WORKDIR}/${tool}" inputs_dir="${WORKDIR}/${tool}/inputs" primary="" inputs_list=""
  mkdir -p "${inputs_dir}"
  log "fetching ${fetch} ${acc}"
  case "${fetch}" in
    genome)  primary="$(fetch_genome  "${acc}" "${inputs_dir}")" || { echo "FAIL  ${tool}: download failed (${acc})"; RC_FAIL=1; return 0; };;
    genbank) primary="$(fetch_genbank "${acc}" "${inputs_dir}")" || { echo "FAIL  ${tool}: download failed (${acc})"; RC_FAIL=1; return 0; };;
    sra)     primary="$(fetch_sra     "${acc}" "${inputs_dir}")" || { echo "FAIL  ${tool}: download failed (${acc})"; RC_FAIL=1; return 0; };;
    genomes) # space-separated list of assembly accessions -> {inputs}
      local a g
      for a in ${acc}; do
        g="$(fetch_genome "${a}" "${inputs_dir}")" || { echo "FAIL  ${tool}: download failed (${a})"; RC_FAIL=1; return 0; }
        inputs_list+="${g} "
      done
      inputs_list="${inputs_list% }"; primary="${inputs_list%% *}";;
    *)       echo "FAIL  ${tool}: unknown fetch method '${fetch}'"; RC_FAIL=1; return 0;;
  esac
  ok "input: ${inputs_list:-${primary}}"

  # 2. build run command (placeholder substitution)
  local out="${work}/out"; rm -rf "${out}"; mkdir -p "${out}"
  local fasta="" r1="" r2=""
  if [[ "${fetch}" == "genome" || "${fetch}" == "genbank" ]]; then fasta="${primary}"
  elif [[ "${fetch}" == "sra" ]]; then r1="${primary}"; r2="${primary/_1.fastq.gz/_2.fastq.gz}"; [[ -s "${r2}" ]] || r2=""; fi
  local cmd="${run_cmd}"
  cmd="${cmd//\{python\}/${py}}"
  cmd="${cmd//\{tooldir\}/${dir}}"
  cmd="${cmd//\{testsdir\}/${TESTS_DIR}}"
  cmd="${cmd//\{out\}/${out}}"
  cmd="${cmd//\{fasta\}/${fasta}}"
  cmd="${cmd//\{inputs\}/${inputs_list}}"
  cmd="${cmd//\{r1\}/${r1}}"
  cmd="${cmd//\{r2\}/${r2}}"

  # 3. run with the tool's env on PATH
  log "running: ${cmd}"
  if ! PATH="${envbin}:${PATH}" PYTHONPATH="${dir}/bin:${PYTHONPATH:-}" bash -c "${cmd}"; then
    echo "FAIL  ${tool}: pipeline run failed"; RC_FAIL=1; return 0
  fi

  local got="${out}/${result_file}"
  [[ -f "${got}" ]] || { echo "FAIL  ${tool}: result file not produced (${result_file})"; RC_FAIL=1; return 0; }

  # 4. record or compare
  if [[ ${RECORD} -eq 1 ]]; then
    cp "${got}" "${exp}.recorded.json"
    ok "recorded raw result -> ${exp}.recorded.json"
    warn "review it, then curate the headline fields into ${exp} (dotted keys; see tests/README.md)"
    return 0
  fi
  [[ -f "${exp}" ]] || { echo "SKIP  ${tool}: no expected.json yet (run with --record on a known-good box)"; return 0; }
  if python3 "${TESTS_DIR}/lib/compare.py" "${got}" "${exp}"; then
    ok "${tool}: PASS"
  else
    echo "FAIL  ${tool}: result did not match ${exp}"; RC_FAIL=1
  fi
}

RC_FAIL=0
if [[ "${TARGET}" == "all" ]]; then
  while read -r n; do
    [[ -n "$n" ]] || continue
    run_one "$n"; echo   # run_one SKIPs cleanly when a tool has no spec
  done < <(manifest_names)
else
  manifest_has "${TARGET}" || die "unknown tool: ${TARGET}"
  run_one "${TARGET}"
fi

[[ ${KEEP} -eq 1 ]] || true   # workdir kept by default; cleanup is the user's call
log "done."
exit ${RC_FAIL}
