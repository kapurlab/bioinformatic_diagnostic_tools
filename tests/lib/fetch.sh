#!/usr/bin/env bash
# fetch.sh — download helpers for the validation suite. Sourced by bin/test.sh.
#
# Three methods, all best-effort and cached (a re-run won't re-download):
#   fetch_genome  ACCESSION OUTDIR   -> OUTDIR/<ACCESSION>.fna   (NCBI assembly FASTA)
#   fetch_sra     ACCESSION OUTDIR   -> OUTDIR/<ACC>_1.fastq.gz [+ _2]  (paired reads)
#   fetch_genbank "ACC1 ACC2 .." OUTDIR -> OUTDIR/<ACC1>_set.fna  (concatenated nuccore FASTA)
#
# fetch_genome prefers the `datasets` CLI if present, else falls back to the
# NCBI Datasets v2 REST API over plain curl (no extra tooling). fetch_sra uses
# sra-tools (prefetch + fasterq-dump), which are commonly present system-wide.
# fetch_genbank uses the NCBI E-utilities efetch REST endpoint over curl — used
# when a tool needs several GenBank nucleotide records in one FASTA (e.g. the
# 8 influenza segments GenoFLU expects), for which there is no single assembly
# accession.

# echo the produced primary file path on success; non-zero + message on failure.

fetch_genome() {
  local acc="$1" out="$2" fna="${2}/${1}.fna"
  mkdir -p "${out}"
  if [[ -s "${fna}" ]]; then echo "${fna}"; return 0; fi
  command -v unzip >/dev/null 2>&1 || { echo "unzip not found" >&2; return 1; }

  # Extract each accession into its OWN dir so the cat glob can't pick up a
  # sibling genome left behind by a previous fetch into the same OUTDIR
  # (matters when several genomes are fetched for one tool, e.g. ksnp).
  local zip="${out}/${acc}.zip" xdir="${out}/.x_${acc}"
  rm -rf "${xdir}"; mkdir -p "${xdir}"
  if command -v datasets >/dev/null 2>&1; then
    datasets download genome accession "${acc}" --include genome --filename "${zip}" >/dev/null 2>&1 \
      || { echo "datasets download failed for ${acc}" >&2; return 1; }
  else
    # REST fallback: the download endpoint returns a zip of the dataset.
    curl -sS -L -m 600 \
      "https://api.ncbi.nlm.nih.gov/datasets/v2alpha/genome/accession/${acc}/download?include_annotation_type=GENOME_FASTA" \
      -o "${zip}" || { echo "NCBI API download failed for ${acc}" >&2; return 1; }
  fi
  ( cd "${xdir}" && unzip -o -q "${zip}" )
  cat "${xdir}"/ncbi_dataset/data/*/*.fna > "${fna}" 2>/dev/null
  rm -rf "${xdir}" "${zip}"
  [[ -s "${fna}" ]] || { echo "no FASTA extracted for ${acc}" >&2; return 1; }
  echo "${fna}"
}

# ---- architecture pin for env-resolved sra-tools ---------------------------
# _ensure_sra_tools may satisfy prefetch/fasterq-dump from ANY conda env's bin.
# An env's binaries were linked for the platform its conda-meta records, not
# for whatever slice preference this test process happened to inherit — the
# August macOS incident shape: launched from a translated caller, a universal
# binary picks the slice the env was never built with and dies, and here that
# surfaces as "SRA download failed" with nothing on disk to explain it, blamed
# on the network before the pipeline even starts. So when the tools come from a
# conda env, run them under that env's /usr/bin/arch pin, exactly like the
# production launchers (install-local.sh:arch_prefix); when they resolve from
# ordinary PATH outside any env, there is no recorded platform to assert, so
# run them unpinned.
_SRA_ARCH_PREFIX=""   # set by _ensure_sra_tools; consumed by fetch_sra

_sra_arch_prefix() {  # ENVDIR -> echoes "/usr/bin/arch -<arch>" or nothing
  local envdir="${1:-}" sub
  [[ "$(uname -s)" == "Darwin" ]] || return 0
  [[ -x /usr/bin/arch ]] || return 0
  # From conda-meta, NEVER `uname -m`: inside a translated process uname -m
  # reports x86_64 — false exactly when the caller is the Rosetta process whose
  # preference needs overriding. This suite already shipped that guard once.
  sub="$(env_conda_subdir "${envdir}" 2>/dev/null || true)"
  case "${sub}" in
    osx-arm64) printf '%s' "/usr/bin/arch -arm64";;
    osx-64)    printf '%s' "/usr/bin/arch -x86_64";;
  esac
  return 0
}

# Set _SRA_ARCH_PREFIX from wherever `prefetch` actually resolved. A bin dir
# whose parent carries conda-meta is a conda env — env prefixes don't have to
# contain "/envs/" (checkout-local envs live at <tool>/env), so the marker on
# disk is the test, not the path's spelling. Anything else is a system install
# with no recorded platform: leave the prefix empty.
_sra_arch_from_resolved() {
  _SRA_ARCH_PREFIX=""
  local p envdir
  p="$(command -v prefetch 2>/dev/null || true)"
  [[ -n "${p}" ]] || return 0
  envdir="$(dirname "$(dirname "${p}")")"
  [[ -d "${envdir}/conda-meta" ]] && _SRA_ARCH_PREFIX="$(_sra_arch_prefix "${envdir}")"
  return 0
}

# Make sra-tools (prefetch + fasterq-dump) available on PATH. On macOS there is
# usually no system sra-tools, so look inside the conda envs too — `[[ -x ]]`
# follows symlinks (conda often symlinks these into <env>/bin), unlike
# `find -type f`. Returns 0 if usable, non-zero if sra-tools can't be found.
# Every successful path also records the tools' arch pin (see above): even
# tools already on PATH may live inside an env somebody activated.
_ensure_sra_tools() {
  if command -v prefetch >/dev/null 2>&1 && command -v fasterq-dump >/dev/null 2>&1; then
    _sra_arch_from_resolved; return 0
  fi
  local conda envroot; conda="$(detect_conda 2>/dev/null || true)"
  if [[ -n "${conda}" ]]; then
    while read -r envroot; do
      [[ -n "${envroot}" ]] || continue
      if [[ -x "${envroot}/bin/prefetch" && -x "${envroot}/bin/fasterq-dump" ]]; then
        export PATH="${envroot}/bin:${PATH}"
        _SRA_ARCH_PREFIX="$(_sra_arch_prefix "${envroot}")"
        return 0
      fi
    done < <("${conda}" env list 2>/dev/null | awk '{print $NF}' | grep '^/')
  fi
  command -v prefetch >/dev/null 2>&1 && command -v fasterq-dump >/dev/null 2>&1 || return 1
  _sra_arch_from_resolved
}

fetch_sra() {
  local acc="$1" out="$2"
  mkdir -p "${out}"
  local r1="${out}/${acc}_1.fastq.gz" r2="${out}/${acc}_2.fastq.gz"
  if [[ -s "${r1}" ]]; then echo "${r1}"; return 0; fi
  # exit 2 = sra-tools missing (caller SKIPs); exit 1 = a real download failure.
  _ensure_sra_tools || { echo "SRATOOLS_MISSING: prefetch/fasterq-dump not found on PATH or in any conda env" >&2; return 2; }
  # APPLY the pin _ensure_sra_tools computed. The adversarial review caught the
  # first version of this feature computing and testing the pin without ever
  # splicing it into the invocation — fully inert while its detection tests
  # passed. The consumption test in test_platform_guards now runs a stubbed
  # prefetch and asserts the argv it actually received begins with the pin.
  local _pin=()
  [[ -n "${_SRA_ARCH_PREFIX}" ]] && read -ra _pin <<< "${_SRA_ARCH_PREFIX}"
  ( cd "${out}" && ${_pin[@]+"${_pin[@]}"} prefetch -O . "${acc}" >/dev/null 2>&1 \
       && ${_pin[@]+"${_pin[@]}"} fasterq-dump --split-files -O . "${acc}" >/dev/null 2>&1 ) \
    || { echo "SRA download failed for ${acc}" >&2; return 1; }
  # gzip the split files (fasterq-dump leaves them uncompressed)
  [[ -f "${out}/${acc}_1.fastq" ]] && gzip -f "${out}/${acc}_1.fastq"
  [[ -f "${out}/${acc}_2.fastq" ]] && gzip -f "${out}/${acc}_2.fastq"
  [[ -f "${out}/${acc}.fastq"   ]] && { gzip -f "${out}/${acc}.fastq"; mv -f "${out}/${acc}.fastq.gz" "${r1}"; }
  [[ -s "${r1}" ]] || { echo "no FASTQ produced for ${acc}" >&2; return 1; }
  echo "${r1}"
}

fetch_genbank() {
  # "ACC1 ACC2 ..." OUTDIR -> one concatenated FASTA of all the nuccore records.
  local accs="$1" out="$2"
  mkdir -p "${out}"
  local first; first="$(printf '%s\n' ${accs} | head -1)"
  local fna="${out}/${first}_set.fna"
  if [[ -s "${fna}" ]]; then echo "${fna}"; return 0; fi
  command -v curl >/dev/null 2>&1 || { echo "curl not found" >&2; return 1; }
  # efetch accepts a comma-joined id list and returns concatenated FASTA.
  local ids; ids="$(printf '%s' "${accs}" | tr -s ' ' ',')"
  curl -sS -L -m 300 \
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=${ids}&rettype=fasta&retmode=text" \
    -o "${fna}" || { echo "efetch failed for ${accs}" >&2; return 1; }
  grep -q '^>' "${fna}" 2>/dev/null || { echo "efetch returned no FASTA records for ${accs}" >&2; rm -f "${fna}"; return 1; }
  echo "${fna}"
}
