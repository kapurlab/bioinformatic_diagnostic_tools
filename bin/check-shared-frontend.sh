#!/usr/bin/env bash
# check-shared-frontend.sh — the shared frontend files are copied, not packaged.
# Prove they are still identical everywhere.
#
# The Results pane (ResultsPane.jsx / useResults.js / ResultsPane.css) and the
# citation footer (Citations.jsx / Citations.css) are vendored byte-identically
# into every tool because each repo is cloned standalone from its own URL and
# pinned to its own tag — there is no monorepo root at install time, so a shared
# npm package would add a second, uncoordinated version axis on top of the tag
# pins. The price of copying is drift, and the only defence is checking.
#
# Source of truth: amr_plus_gui. Change it there, re-copy, re-tag.
#
#   bin/check-shared-frontend.sh [--dir DIR]   # DIR holds the tool checkouts
#
# Exit 0 when every copy matches, 1 on drift or a missing file.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SHARED=(ResultsPane.jsx useResults.js ResultsPane.css)
SOURCE_TOOL=amr_plus_gui
# vsnp_gui is the MODEL this pane was ported from, not a consumer of it: it has
# its own native Step 1 Results built on a different stylesheet (styles.css, not
# the shared App.css). It is correctly absent from the copy set.
SKIP_TOOLS=(vsnp_gui)

# Files shared with EVERY tool, vsnp_gui included. The citation footer is
# deliberately in this set and not in SHARED above: it carries its own prefixed
# stylesheet and uses only variables both App.css and vsnp_gui's styles.css
# define, so unlike the Results pane it drops into vsnp_gui unchanged — and
# "cite the tool that produced this result" has to hold for vSNP too.
SHARED_ALL=(Citations.jsx Citations.css)

ROOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) ROOT="${2:-}"; shift 2;;
    --dir=*) ROOT="${1#--dir=}"; shift;;
    -h|--help) sed -n '2,14p' "$0"; exit 0;;
    *) die "unknown argument: $1";;
  esac
done
[[ -n "${ROOT}" ]] || ROOT="${BDTOOLS_TOOLSDIR:-${BDTOOLS_HOME}/checkouts}"
[[ -d "${ROOT}" ]] || die "no tool checkouts at ${ROOT} (pass --dir)"

src_dir="${ROOT}/${SOURCE_TOOL}/frontend/src"
[[ -d "${src_dir}" ]] || die "source tool not found: ${src_dir}"

rc=0
checked=0

# check_set <every|most> <file>...
#   every — compared in all tools, vsnp_gui included
#   most  — SKIP_TOOLS excluded (files vsnp_gui deliberately does not carry)
check_set() {
  local scope="$1"; shift
  local f ref want d tool have_file got sk _skip
  for f in "$@"; do
    ref="${src_dir}/${f}"
    if [[ ! -f "${ref}" ]]; then
      warn "${SOURCE_TOOL} has no ${f} — nothing to compare against"
      continue
    fi
    want="$(md5sum < "${ref}" | awk '{print $1}')"
    for d in "${ROOT}"/*/; do
      tool="$(basename "${d}")"
      [[ "${tool}" == "${SOURCE_TOOL}" ]] && continue
      if [[ "${scope}" == "most" ]]; then
        _skip=0
        for sk in "${SKIP_TOOLS[@]}"; do [[ "${tool}" == "${sk}" ]] && _skip=1; done
        [[ ${_skip} -eq 1 ]] && continue
      fi
      # Only tools that have a frontend at all are in scope.
      [[ -d "${d}frontend/src" ]] || continue
      have_file="${d}frontend/src/${f}"
      if [[ ! -f "${have_file}" ]]; then
        warn "${tool}: missing ${f} (has it been copied here from ${SOURCE_TOOL}?)"
        rc=1
        continue
      fi
      checked=$((checked + 1))
      got="$(md5sum < "${have_file}" | awk '{print $1}')"
      if [[ "${got}" != "${want}" ]]; then
        warn "${tool}: ${f} has DRIFTED from ${SOURCE_TOOL}"
        warn "       fix: cp ${ref} ${have_file}   (then rebuild + re-tag ${tool})"
        rc=1
      fi
    done
  done
}

check_set most  "${SHARED[@]}"
check_set every "${SHARED_ALL[@]}"

# ---------------------------------------------------------------------------
# Look-and-feel files: ADVISORY only (never changes the exit code).
#
# App.css and ThemeToggle.jsx are what make a new tool read as part of the same
# product. They are copied between tools like the Results pane, but unlike it
# they carry per-tool additions, so a hash mismatch is not automatically a bug —
# it just needs a human to look. The strict block above never covered them, so
# a new tool could ship looking subtly unlike the rest and nothing would say so.
#
# Compared against the MAJORITY copy rather than against SOURCE_TOOL: for these
# two files amr_plus_gui is itself the outlier (reformatted), and reporting six
# tools as "drifted" every run is the kind of permanent noise that makes the
# seventh — the one that actually matters — invisible. Formatting is normalised
# away (whitespace + trailing commas) for the same reason: a reflow is not a
# change in the look.
look_hash() {
  tr -d '[:space:]' < "$1" | sed -e 's/,\([]})]\)/\1/g' | md5sum | awk '{print $1}'
}
LOOK=(App.css ThemeToggle.jsx)
# vsnp_gui has its own native stylesheet (styles.css); mhc_gui deliberately owns
# a different App.css (its .qc-badge/.qc-pass classes predate the shared pane and
# mean something else).
LOOK_SKIP=(vsnp_gui mhc_gui)
for f in "${LOOK[@]}"; do
  hashes=(); tools=()
  for d in "${ROOT}"/*/; do
    tool="$(basename "${d}")"
    _skip=0
    for sk in "${LOOK_SKIP[@]}"; do [[ "${tool}" == "${sk}" ]] && _skip=1; done
    [[ ${_skip} -eq 1 ]] && continue
    [[ -f "${d}frontend/src/${f}" ]] || continue
    tools+=("${tool}"); hashes+=("$(look_hash "${d}frontend/src/${f}")")
  done
  [[ ${#tools[@]} -ge 3 ]] || continue      # too few copies for a majority
  majority="$(printf '%s\n' "${hashes[@]}" | sort | uniq -c | sort -rn | head -1 | awk '{print $2}')"
  odd=()
  for i in "${!tools[@]}"; do
    [[ "${hashes[$i]}" == "${majority}" ]] || odd+=("${tools[$i]}")
  done
  if [[ ${#odd[@]} -gt 0 ]]; then
    info "note: ${f} differs from the rest of the suite in: ${odd[*]}"
    info "      advisory — confirm this is an intended per-tool addition, not an"
    info "      accidental divergence in the shared look."
  fi
done

# App.css must never be restyled by this work: it is copied verbatim between
# tools, and mhc_gui already owns .qc-badge/.qc-pass with different meanings.
for d in "${ROOT}"/*/; do
  tool="$(basename "${d}")"
  [[ -d "${d}.git" || -f "${d}.git" ]] || continue
  if git -C "${d}" diff --quiet -- frontend/src/App.css 2>/dev/null; then :; else
    warn "${tool}: frontend/src/App.css has uncommitted edits — the shared pane must not restyle it"
  fi
done

if [[ ${rc} -eq 0 ]]; then
  ok "shared frontend files identical across the suite (${checked} file copies checked)"
else
  die "shared frontend files have drifted or are missing — see above"
fi
exit ${rc}
