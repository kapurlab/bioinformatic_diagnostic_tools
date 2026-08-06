#!/usr/bin/env bash
# update-packages.sh — move a tool's ANALYSIS packages to the newest release on
# their conda channel, then bump the manifest pin to match.
#
#   bdtools update-packages <tool|all> [--dry-run] [--to NAME=VERSION] [--yes]
#
# This is deliberately NOT part of `bdtools update <tool>`. That command moves the
# GUI checkout to a new release tag and rebuilds its environment; this one changes
# the science inside an existing environment. Different blast radius, different
# decision, so a different command — and the dashboard shows them as separate
# updates for the same reason.
#
# What one package update does, in order:
#
#   1. resolve the newest version on the channel (api.anaconda.org, no solve),
#   2. conda install <pkg>=<newest> into the tool's env,
#   3. RE-APPLY the tool's local patches. This step is why a bare `conda install`
#      is not a safe way to do this by hand: vsnp_gui carries kapurlab patches to
#      the packaged vsnp3 (the minus-strand annotation fix among them), and a fresh
#      package overwrites the patched files. Losing that silently changes results,
#      which is the worst possible failure mode for a diagnostic tool.
#   4. verify the env still imports/runs the tool's entry point,
#   5. rewrite the pin in tools.yml so the next build reproduces this version.
#
# Any step failing leaves the remaining packages alone and reports at the end.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TARGET=""
TO=""
ASSUME_YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; export DRY_RUN; shift;;
    --to)      TO="${2:?--to needs NAME=VERSION}"; shift 2;;
    --to=*)    TO="${1#--to=}"; shift;;
    --yes|-y)  ASSUME_YES=1; shift;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)        die "unknown option: $1 (see: bdtools update-packages --help)";;
    *)         TARGET="$1"; shift;;
  esac
done
[[ -n "${TARGET}" ]] || die "usage: bdtools update-packages <tool|all> [--to NAME=VERSION]"

_need_python
PKG_PY="${KT_BIN_DIR}/lib/packages.py"

targets() {
  if [[ "${TARGET}" == "all" ]]; then manifest_names; else
    manifest_has "${TARGET}" || die "no tool named '${TARGET}' in the manifest"
    echo "${TARGET}"
  fi
}

FAILED=()
CHANGED=0

# Everything packages.py already knows: which env actually runs this tool, what is
# installed in it, and what the channel has. Asking it (rather than re-deriving)
# keeps the CLI and the dashboard from ever disagreeing about what needs updating.
records_for() {  # records_for <tool>  -> TSV: package channel installed latest pinned
  "${PYBIN}" - "${PKG_PY}" "$1" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bdtools_packages", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
for rec in mod.report([sys.argv[2]]):
    print("\t".join([rec["package"], rec["channel"], rec["installed"],
                     rec["latest"], rec["pinned"], rec["env"]]))
PY
}

update_one_tool() {
  local tool="$1" line pkg channel installed latest pinned env
  local dir; dir="$(tool_dir "${tool}")"
  local any=0

  while IFS=$'\t' read -r pkg channel installed latest pinned env; do
    [[ -n "${pkg}" ]] || continue
    any=1
    local want="${latest}"
    if [[ -n "${TO}" ]]; then
      [[ "${TO%%=*}" == "${pkg}" ]] || continue
      want="${TO#*=}"
    fi
    if [[ -z "${env}" ]]; then
      warn "${tool}: not installed here — skipping ${pkg} (run: bdtools install ${tool})"
      continue
    fi
    if [[ -z "${want}" ]]; then
      warn "${tool}/${pkg}: could not determine the newest version (offline?) — skipping"
      continue
    fi
    if [[ "${want}" == "${installed}" ]]; then
      ok "${tool}/${pkg} is already ${installed}"
      # The pin can still be stale — record reality either way.
      [[ "${pinned}" != "${installed}" ]] && _bump_pin "${tool}" "${pkg}" "${channel}" "${installed}"
      continue
    fi

    log "${tool}: ${pkg} ${installed:-none} -> ${want}  (${env})"
    local conda; conda="$(detect_conda)" || { FAILED+=("${tool}/${pkg}: conda not found"); continue; }
    if ! run "${conda}" install -y -p "${env}" -c conda-forge -c bioconda \
             "${pkg}=${want}"; then
      FAILED+=("${tool}/${pkg}: conda install failed")
      continue
    fi

    # 3. Local patches, re-applied. See the header: a new package overwrites the
    # patched files, and apply.sh is idempotent precisely so this is safe to run
    # after every package change.
    if [[ -x "${dir}/deploy/vsnp3-patches/apply.sh" ]]; then
      log "${tool}: re-applying local patches over the new ${pkg}"
      if ! run "${dir}/deploy/vsnp3-patches/apply.sh" "${env}"; then
        FAILED+=("${tool}/${pkg}: PATCHES DID NOT RE-APPLY — results may be wrong until fixed")
        continue
      fi
    fi

    # 4. Still runnable? doctor knows what each tool needs; a package update that
    # breaks the env should be reported now, not on the next analysis.
    if [[ "${DRY_RUN}" -ne 1 ]]; then
      if ! "${KT_BIN_DIR}/bdtools" doctor "${tool}" >/dev/null 2>&1; then
        warn "${tool}: doctor reports a problem after updating ${pkg} — run: bdtools doctor ${tool}"
      fi
    fi

    _bump_pin "${tool}" "${pkg}" "${channel}" "${want}"
    CHANGED=1
    if [[ "${DRY_RUN}" -eq 1 ]]; then
      info "  [dry-run] ${tool}: ${pkg} would become ${want}"
    else
      ok "${tool}: ${pkg} is now ${want}"
    fi
  done < <(records_for "${tool}")

  [[ ${any} -eq 1 ]] || info "${tool}: no analysis packages declared in tools.yml — nothing to do"
}

# Rewrite one entry of a tool's `packages:` list in tools.yml, leaving the others
# (and the file's comments and formatting) alone.
_bump_pin() {  # _bump_pin <tool> <pkg> <channel> <version>
  local tool="$1" pkg="$2" channel="$3" version="$4"
  local current; current="$(manifest_get "${tool}" packages || true)"
  [[ -n "${current}" ]] || return 0
  local out=() spec
  for spec in ${current}; do
    if [[ "${spec}" == *"::${pkg}="* || "${spec}" == "${pkg}="* || "${spec}" == "${pkg}" ]]; then
      out+=("${channel}::${pkg}=${version}")
    else
      out+=("${spec}")
    fi
  done
  local joined; joined="$(IFS=,; echo "${out[*]}")"
  joined="[${joined//,/, }]"
  run manifest_set "${tool}" packages "${joined}"
}

# NOT in a subshell: FAILED and CHANGED are set inside update_one_tool, and a
# subshell would discard both — reporting "everything is already up to date" after
# a run that failed, which is the one outcome this script must never produce.
# `|| FAILED+=` keeps set -e from aborting the whole run on one tool.
for tool in $(targets); do
  update_one_tool "${tool}" || FAILED+=("${tool}: update aborted")
done

echo
if [[ ${#FAILED[@]} -gt 0 ]]; then
  warn "finished with problems:"
  for f in "${FAILED[@]}"; do info "  • ${f}"; done
  exit 1
fi
if [[ ${CHANGED} -eq 1 ]]; then
  ok "package updates complete. Restart the dashboard to pick them up."
  info "  tools.yml now pins what is installed — commit it to share this set."
else
  ok "every declared package is already at its newest version."
fi
