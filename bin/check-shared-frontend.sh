#!/usr/bin/env bash
# check-shared-frontend.sh — the Results pane is copied, not packaged. Prove it
# is still identical everywhere.
#
# ResultsPane.jsx / useResults.js / ResultsPane.css are vendored byte-identically
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
for f in "${SHARED[@]}"; do
  ref="${src_dir}/${f}"
  if [[ ! -f "${ref}" ]]; then
    warn "${SOURCE_TOOL} has no ${f} — nothing to compare against"
    continue
  fi
  want="$(md5sum < "${ref}" | awk '{print $1}')"
  for d in "${ROOT}"/*/; do
    tool="$(basename "${d}")"
    [[ "${tool}" == "${SOURCE_TOOL}" ]] && continue
    _skip=0
    for sk in "${SKIP_TOOLS[@]}"; do [[ "${tool}" == "${sk}" ]] && _skip=1; done
    [[ ${_skip} -eq 1 ]] && continue
    # Only tools that have a frontend at all are in scope.
    [[ -d "${d}frontend/src" ]] || continue
    have_file="${d}frontend/src/${f}"
    if [[ ! -f "${have_file}" ]]; then
      warn "${tool}: missing ${f} (has the Results pane been copied here?)"
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
  ok "shared Results-pane files identical across the suite (${checked} file copies checked)"
else
  die "shared frontend files have drifted or are missing — see above"
fi
exit ${rc}
