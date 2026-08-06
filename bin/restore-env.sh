#!/usr/bin/env bash
# restore-env.sh — put a tool's conda env back to a recorded package set.
#
#   restore-env.sh <tool> [--prev] [--dry-run]
#
# The snapshot is `conda list --explicit` taken before the last operation that
# modified the env (common.sh:snapshot_env). It is a list of resolved package
# URLs, so replaying it needs no solve and cannot pick different versions than
# the ones that were there — which is what makes it a restore rather than
# another build.
#
# Why this exists: conda rolls a failed transaction back, and a rollback is not a
# restore. It can leave an env that no longer imports what the tool needs, and
# before this there was no record of what the env had contained — so the only way
# back was a full rebuild, which is exactly the operation that had just failed.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TOOL=""
USE_PREV=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prev)    USE_PREV=1; shift;;
    --dry-run) DRY_RUN=1; export DRY_RUN; shift;;
    -*)        die "unknown option: $1 (see: bdtools restore-env --help)";;
    *)         TOOL="$1"; shift;;
  esac
done
[[ -n "${TOOL}" ]] || die "usage: bdtools restore-env <tool> [--prev]"
manifest_has "${TOOL}" || die "no tool named '${TOOL}' in the manifest"

SNAP="$(_env_snapshot_file "${TOOL}")"
[[ ${USE_PREV} -eq 1 ]] && SNAP="${SNAP}.prev"
[[ -f "${SNAP}" ]] || die "no env snapshot for ${TOOL} at ${SNAP}
       Snapshots are written when bdtools changes an env, so there is none
       until the first such change. Rebuild instead:  bin/bdtools install ${TOOL}"

DIR="$(tool_dir "${TOOL}")"
ENVDIR="${DIR}/env"
[[ -d "${ENVDIR}" ]] || die "${TOOL} has no env at ${ENVDIR} (run: bin/bdtools install ${TOOL})"

CONDA="$(detect_conda)" || die "conda/mamba not found."

n="$(grep -cv '^\(#\|@\)' "${SNAP}" 2>/dev/null || echo 0)"
log "restoring ${TOOL}: ${n} package(s) from $(basename "${SNAP}")"
info "  env:      ${ENVDIR}"
info "  snapshot: ${SNAP}"

# --force-reinstall so a package the failed transaction left half-linked is
# replaced rather than considered satisfied. An explicit list pins every URL, so
# this cannot drift to a different version.
if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
  echo "  [dry-run] ${CONDA} install -y -p ${ENVDIR} --force-reinstall --file ${SNAP}"
  exit 0
fi
rc=0
_conda_restore() { "${CONDA}" install -y -p "${ENVDIR}" --force-reinstall --file "${SNAP}"; }
_conda_restore || rc=$?
harden_conda_hooks "${ENVDIR}"
if [[ ${rc} -ne 0 ]]; then
  warn "${TOOL}: restore did not finish (exit ${rc})."
  info "  The env was not deleted. If it still does not run, rebuild it:"
  info "      bin/bdtools install ${TOOL}"
  exit "${rc}"
fi
ok "${TOOL}: conda packages restored from the snapshot."
info "  pip requirements and the frontend are not part of a conda snapshot."
info "  If the tool still reports missing python modules, finish with:"
info "      bin/bdtools install ${TOOL}"
info "  Then confirm:  bin/bdtools doctor ${TOOL}"
