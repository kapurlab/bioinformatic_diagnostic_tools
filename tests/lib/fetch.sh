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

fetch_sra() {
  local acc="$1" out="$2"
  mkdir -p "${out}"
  local r1="${out}/${acc}_1.fastq.gz" r2="${out}/${acc}_2.fastq.gz"
  if [[ -s "${r1}" ]]; then echo "${r1}"; return 0; fi
  command -v prefetch    >/dev/null 2>&1 || { echo "sra-tools 'prefetch' not found" >&2; return 1; }
  command -v fasterq-dump >/dev/null 2>&1 || { echo "sra-tools 'fasterq-dump' not found" >&2; return 1; }
  ( cd "${out}" && prefetch -O . "${acc}" >/dev/null 2>&1 \
       && fasterq-dump --split-files -O . "${acc}" >/dev/null 2>&1 ) \
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
