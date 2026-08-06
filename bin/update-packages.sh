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
records_for() {  # records_for <tool>  -> TSV: pkg channel installed latest pinned env held
  "${PYBIN}" - "${PKG_PY}" "$1" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bdtools_packages", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
for rec in mod.report([sys.argv[2]]):
    print("\t".join([rec["package"], rec["channel"], rec["installed"],
                     rec["latest"], rec["pinned"], rec["env"],
                     "held" if rec.get("held") else ""]))
PY
}

# The one-line reason a solve failed, out of conda's ~200-line dependency tree.
# The tree is worth keeping in a log; it is not worth being the whole answer.
_solve_headline() {
  grep -m1 -E 'requires .*, but none of the providers can be installed' "$1" 2>/dev/null \
    | sed 's/^[[:space:]]*-[[:space:]]*//' \
    || true
}

update_one_tool() {
  local tool="$1" pkg channel installed latest pinned env
  local dir; dir="$(tool_dir "${tool}")"
  local any=0 envdir="" specs=() plan=()

  # Collect every package that needs to move, then install them in ONE conda
  # transaction. Per-package transactions cannot work in a coupled bioinformatics
  # env: each solve sees the others at their OLD versions, so a set that is only
  # jointly satisfiable is rejected one member at a time.
  local hold
  while IFS=$'\t' read -r pkg channel installed latest pinned env hold; do
    [[ -n "${pkg}" ]] || continue
    any=1
    [[ -n "${env}" ]] && envdir="${env}"
    local want="${latest}"
    if [[ -n "${TO}" ]]; then
      [[ "${TO%%=*}" == "${pkg}" ]] || continue
      want="${TO#*=}"
    elif [[ "${hold}" == "held" ]]; then
      # Held in the manifest: a newer release exists but this env cannot take it,
      # and the reason is recorded next to the pin. Say so once rather than run a
      # solve that is known to fail. --to overrides, to try a version by hand.
      ok "${tool}/${pkg} held at ${installed} (newer: ${latest:-?} — this env cannot take it; see tools.yml)"
      continue
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
      [[ "${pinned}" != "${installed}" ]] && _bump_pin "${tool}" "${pkg}" "${channel}" "${installed}"
      continue
    fi
    specs+=("${pkg}=${want}")
    plan+=("${pkg}|${channel}|${installed}|${want}")
  done < <(records_for "${tool}")

  [[ ${any} -eq 1 ]] || { info "${tool}: no analysis packages declared in tools.yml — nothing to do"; return 0; }
  [[ ${#specs[@]} -gt 0 ]] || return 0

  local p
  for p in "${plan[@]}"; do
    IFS='|' read -r pkg channel installed want <<< "${p}"
    log "${tool}: ${pkg} ${installed:-none} -> ${want}"
  done
  info "  env: ${envdir}"

  local conda; conda="$(detect_conda)" || { FAILED+=("${tool}: conda not found"); return 0; }

  # Solve before installing. An unsatisfiable set is the normal outcome for an
  # older env — a newer package can need a newer libcurl/zlib/perl than something
  # else in the env can tolerate — and it deserves one clear sentence, not a
  # 200-line solver tree presented as "conda install failed".
  local solvelog="${BDTOOLS_HOME}/logs/${tool}-package-solve.log"
  mkdir -p "$(dirname "${solvelog}")"
  info "  checking whether these versions can coexist in this env…"
  if ! "${conda}" install --dry-run -y -p "${envdir}" \
        -c conda-forge -c bioconda "${specs[@]}" > "${solvelog}" 2>&1; then
    local why; why="$(_solve_headline "${solvelog}")"
    FAILED+=("${tool}: ${specs[*]} cannot be installed into this env")
    warn "${tool}: these versions cannot coexist in the existing env."
    [[ -n "${why}" ]] && info "  conda: ${why}"
    info "  This is a property of the env, not a failed download. Options:"
    info "    • rebuild the env from its spec, which re-solves everything together:"
    info "        bin/bdtools install ${tool}   (add --rebuild to refresh in place)"
    info "    • update one package only:  bin/bdtools update-packages ${tool} --to <pkg>=<ver>"
    info "    • leave it: the env keeps working on the versions it has."
    info "  Full solver output: ${solvelog}"
    return 0
  fi
  ok "  the set solves"

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    for p in "${plan[@]}"; do
      IFS='|' read -r pkg channel installed want <<< "${p}"
      info "  [dry-run] ${tool}: ${pkg} would become ${want}"
    done
    CHANGED=1
    return 0
  fi

  if ! _conda_install_set "${conda}" "${envdir}" "${specs[@]}"; then
    FAILED+=("${tool}: conda install failed after a successful solve — see ${solvelog}")
    return 0
  fi

  # Local patches, re-applied. A new package overwrites the patched files, and
  # apply.sh is idempotent precisely so this is safe after every package change.
  if [[ -x "${dir}/deploy/vsnp3-patches/apply.sh" ]]; then
    log "${tool}: re-applying local patches over the new package(s)"
    if ! run "${dir}/deploy/vsnp3-patches/apply.sh" "${envdir}"; then
      FAILED+=("${tool}: PATCHES DID NOT RE-APPLY — results may be wrong until fixed")
      return 0
    fi
  fi

  # Still runnable? A package update that breaks the env should be reported now,
  # not on the next analysis.
  if ! "${KT_BIN_DIR}/bdtools" doctor "${tool}" >/dev/null 2>&1; then
    warn "${tool}: doctor reports a problem after the update — run: bin/bdtools doctor ${tool}"
  fi

  for p in "${plan[@]}"; do
    IFS='|' read -r pkg channel installed want <<< "${p}"
    _bump_pin "${tool}" "${pkg}" "${channel}" "${want}"
    ok "${tool}: ${pkg} is now ${want}"
  done
  CHANGED=1
}

_conda_install_set() {
  local conda="$1" envdir="$2"; shift 2
  run "${conda}" install -y -p "${envdir}" -c conda-forge -c bioconda "$@"
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
  ok "nothing to install: every declared package is at its newest version, or held."
  info "  Held packages are listed above with the newer version they cannot take."
fi
