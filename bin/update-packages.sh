#!/usr/bin/env bash
# update-packages.sh — move a tool's ANALYSIS packages to the newest release on
# their conda channel, then bump the manifest pin to match.
#
#   bdtools update-packages <tool|all> [--dry-run] [--to NAME=VERSION] [--yes]
#   bdtools update-packages <tool|all> --check-pins [--platform L1,L2]
#
# A package is only moved to a version installable on EVERY platform the lab deploys
# to (default linux-64, osx-64, osx-arm64). Cross-platform consistency outranks being
# current: an update that lands on Linux and cannot land on macOS does not make the
# lab more current, it makes two machines disagree about what produced a result.
# --local-only overrides, for a machine-specific experiment.
#
# --check-pins verifies that the versions pinned in tools.yml can actually be
# installed on every platform the lab deploys to (default linux-64, osx-64,
# osx-arm64). Run it whenever you change a pin: a pin can be jointly unsatisfiable,
# or installable here and impossible elsewhere. Needs the network; takes minutes.
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
CHECK_PINS=0
LOCAL_ONLY=0
ALLOW_REPORT_ONLY=0
CONDA_BIN=""
# The platforms the lab deploys to. A pin has to be installable on all of them.
PLATFORMS="linux-64,osx-64,osx-arm64"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; export DRY_RUN; shift;;
    --to)      TO="${2:?--to needs NAME=VERSION}"; shift 2;;
    --to=*)    TO="${1#--to=}"; shift;;
    --yes|-y)  ASSUME_YES=1; shift;;
    --check-pins) CHECK_PINS=1; shift;;
    --local-only) LOCAL_ONLY=1; shift;;
    --allow-report-only) ALLOW_REPORT_ONLY=1; shift;;
    --platform)   PLATFORMS="${2:?--platform needs a comma-separated list}"; shift 2;;
    --platform=*) PLATFORMS="${1#--platform=}"; shift;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)        die "unknown option: $1 (see: bdtools update-packages --help)";;
    *)         TARGET="$1"; shift;;
  esac
done
[[ -n "${TARGET}" ]] || die "usage: bdtools update-packages <tool|all> [--to NAME=VERSION]"

_need_python
PKG_PY="${KT_BIN_DIR}/lib/packages.py"

# --check-pins — can the pins in tools.yml actually be installed, on every platform
# the lab deploys to?
#
# This is the gate that was missing, and both pin mistakes would have been caught by
# it. A pin can be wrong in two ways that nothing offline detects:
#   * jointly unsatisfiable — each package is fine alone, the set is not
#     (ncbi-amrfinderplus 4.2.7 + kraken2 2.17.1 + mlst 2.35.0);
#   * fine here, impossible elsewhere — mlst 2.34+ is NOARCH, so it looks portable,
#     and still cannot be installed on macOS because it depends on libxcrypt1, which
#     has no macOS build.
#
# It runs a real dry-run solve per tool per platform, so it needs the network and
# takes minutes. Run it when changing a pin, not on every command.
#
# CONDA_OVERRIDE_OSX is required for the osx targets: solving for a foreign platform
# otherwise fails on a missing __osx virtual package, which reads exactly like a real
# dependency conflict and sends you after the wrong thing.
check_pins() {
  local conda; conda="$(detect_conda)" || die "conda/mamba not found."
  CONDA_BIN="${conda}"
  local failures=0 checked=0
  local tool specs plat spec pkg ver pyver
  for tool in $(targets); do
    specs="$(manifest_get "${tool}" packages 2>/dev/null || true)"
    [[ -n "${specs}" ]] || continue
    local -a want=()
    for spec in ${specs}; do
      pkg="${spec##*::}"; ver="${pkg#*=}"; pkg="${pkg%%=*}"
      [[ -n "${ver}" && "${ver}" != "${pkg}" ]] && want+=("${pkg}=${ver}")
    done
    [[ ${#want[@]} -gt 0 ]] || continue
    # Match the python the tool's env is built with, since that constrains the solve.
    pyver="$(grep -m1 -oE 'python=3\.[0-9]+' "$(tool_dir "${tool}")/conda_setup/environment.yml" 2>/dev/null || true)"
    [[ -z "${pyver}" ]] && pyver="python=3.10"
    log "${tool}: ${want[*]}  (${pyver})"
    for plat in ${PLATFORMS//,/ }; do
      checked=$((checked + 1))
      local logf="${BDTOOLS_HOME}/logs/${tool}-pins-${plat}.log"
      mkdir -p "$(dirname "${logf}")"
      local -a env_pre=()
      [[ "${plat}" == osx-* ]] && env_pre=(env CONDA_OVERRIDE_OSX=13.0)
      if "${env_pre[@]}" "${conda}" create --dry-run -y -n "bdtools-pincheck-$$" \
           --platform "${plat}" -c conda-forge -c bioconda \
           "${pyver}" "${want[@]}" > "${logf}" 2>&1; then
        ok "  ${plat}: installable"
      else
        failures=$((failures + 1))
        local why; why="$(_solve_headline "${logf}")"
        warn "  ${plat}: NOT installable"
        [[ -n "${why}" ]] && info "      ${why}"
        info "      full output: ${logf}"
      fi
    done
  done
  echo
  if [[ ${checked} -eq 0 ]]; then
    warn "no pinned analysis packages found to check."
    return 0
  fi
  if [[ ${failures} -gt 0 ]]; then
    warn "${failures} of ${checked} pin/platform combination(s) cannot be installed."
    info "  Pick versions that solve everywhere the lab deploys, then re-run this."
    return 1
  fi
  ok "every pinned package set is installable on: ${PLATFORMS}"
}

targets() {
  if [[ "${TARGET}" == "all" ]]; then manifest_names; else
    manifest_has "${TARGET}" || die "no tool named '${TARGET}' in the manifest"
    echo "${TARGET}"
  fi
}

# Two different outcomes, deliberately kept apart:
#   FAILED  — something broke: conda missing, an install that failed after its solve
#             succeeded, patches that did not re-apply. Exit 1; a human must look.
#   BLOCKED — the suite correctly worked out that an update cannot be applied here
#             (the version does not exist for this OS, or it conflicts with the env).
#             Nothing is wrong and nothing was left half-done, so exit 0.
# Conflating them made "did what it could, and said why" print as
# "⚠ Update finished with errors", which teaches people to ignore real errors.
FAILED=()
BLOCKED=()
REPORT_ONLY=()
CHANGED=0

# Everything packages.py already knows: which env actually runs this tool, what is
# installed in it, and what the channel has. Asking it (rather than re-deriving)
# keeps the CLI and the dashboard from ever disagreeing about what needs updating.
records_for() {  # records_for <tool> -> TSV: pkg channel installed latest pinned env held reason
  "${PYBIN}" - "${PKG_PY}" "$1" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bdtools_packages", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
for rec in mod.report([sys.argv[2]]):
    # The reason travels with the record so the CLI and the dashboard badge give
    # the same answer. Whitespace-collapsed because it is a TSV field and conda's
    # headline can carry newlines.
    reason = " ".join((rec.get("held_reason") or "").split())
    print("\t".join([rec["package"], rec["channel"], rec["installed"],
                     rec["latest"], rec["pinned"], rec["env"],
                     "held" if rec.get("held") else "", reason]))
PY
}

# The one-line reason a solve failed, out of conda's ~200-line dependency tree.
# The tree is worth keeping in a log; it is not worth being the whole answer.
#
# Several shapes have to be recognised, and the FIRST version of this matched only
# one of them — so a Mac user got "these versions cannot coexist" with no reason
# printed at all, which is worse than the tree:
#   • "requires X, but none of the providers can be installed"   (env conflict)
#   • "nothing provides X needed by Y"                           (missing build)
#   • "does not exist (perhaps a missing channel)"               (missing build)
#   • "PackagesNotFoundError" / "not available from current channels"
# Falls back to the first line under conda's own summary header, so something
# useful is always printed.
_solve_headline() {
  local out
  out="$(grep -m1 -E 'requires .*, but none of the providers can be installed|nothing provides .* needed by|does not exist \(perhaps a missing channel\)|not available from current channels|PackagesNotFoundError' "$1" 2>/dev/null || true)"
  if [[ -z "${out}" ]]; then
    out="$(sed -n '/Encountered problems while solving/,$p' "$1" 2>/dev/null \
           | sed -n '2p' || true)"
  fi
  [[ -z "${out}" ]] && out="$(grep -m1 -E '[Ee]rror|[Ff]ailed' "$1" 2>/dev/null || true)"
  out="$(printf '%s' "${out}" | sed 's/^[[:space:]]*-[[:space:]]*//;s/^[[:space:]]*//')"
  # "…not available from current channels:" names the package on the NEXT line, so
  # the headline alone would say nothing about WHICH package. Pull it in.
  if [[ "${out}" == *: ]]; then
    local nxt
    nxt="$(grep -A3 -m1 -F "${out}" "$1" 2>/dev/null | sed -n '2,4p' \
           | grep -m1 -E '^[[:space:]]*-' | sed 's/^[[:space:]]*-[[:space:]]*//' || true)"
    [[ -n "${nxt}" ]] && out="${out} ${nxt}"
  fi
  printf '%s' "${out}"
}

# Is the failure "this build does not exist here" rather than "it conflicts with
# what is in the env"? The remedy is completely different: rebuilding the env
# cannot conjure a macOS build of a Linux-only package, so suggesting it there sends
# someone on a long, futile rebuild. mlst 2.34+ is the live example — noarch, but it
# depends on libxcrypt1, which has no macOS build at all.
_solve_is_unavailable() {
  grep -qE 'nothing provides|does not exist \(perhaps a missing channel\)|not available from current channels|PackagesNotFoundError' "$1" 2>/dev/null
}

# Can this exact spec set be installed on EVERY platform the lab deploys to?
# Echoes the platforms that failed. Empty output means "installable everywhere".
#
# This is the gate that makes cross-platform stability outrank version currency: an
# update that lands on Linux and cannot land on macOS does not make the lab more
# current, it makes two machines disagree about what produced a result. A lab running
# one older version everywhere is in a better position than one running the newest
# version in half the building.
_unavailable_platforms() {  # _unavailable_platforms <tool> <pyver> <spec>...
  local tool="$1" pyver="$2"; shift 2
  local plat bad=() logf
  for plat in ${PLATFORMS//,/ }; do
    logf="${BDTOOLS_HOME}/logs/${tool}-gate-${plat}.log"
    mkdir -p "$(dirname "${logf}")"
    local -a pre=()
    [[ "${plat}" == osx-* ]] && pre=(env CONDA_OVERRIDE_OSX=13.0)
    if ! "${pre[@]}" "${CONDA_BIN}" create --dry-run -y -n "bdtools-gate-$$" \
          --platform "${plat}" -c conda-forge -c bioconda "${pyver}" "$@" \
          > "${logf}" 2>&1; then
      bad+=("${plat}")
    fi
  done
  printf '%s' "${bad[*]}"
}

_tool_pyver() {
  local v
  v="$(grep -m1 -oE 'python=3\.[0-9]+' "$(tool_dir "$1")/conda_setup/environment.yml" 2>/dev/null || true)"
  printf '%s' "${v:-python=3.10}"
}

update_one_tool() {
  local tool="$1" pkg channel installed latest pinned env
  # Same gate as `bdtools update`: conda installing into a live env is smaller than
  # a rebuild, but it is still a change to the software that produces results, and
  # `all` reaches every tool. tools.yml decides; --allow-report-only overrides for
  # one named tool.
  if ! require_updatable "${tool}" "${ALLOW_REPORT_ONLY}" update-packages \
        "$([[ "${TARGET}" == "all" ]] && echo 0 || echo 1)"; then
    REPORT_ONLY+=("${tool}")
    return 0
  fi
  local dir; dir="$(tool_dir "${tool}")"
  local any=0 envdir="" specs=() plan=()

  # Collect every package that needs to move, then install them in ONE conda
  # transaction. Per-package transactions cannot work in a coupled bioinformatics
  # env: each solve sees the others at their OLD versions, so a set that is only
  # jointly satisfiable is rejected one member at a time.
  local hold reason
  while IFS=$'\t' read -r pkg channel installed latest pinned env hold reason; do
    [[ -n "${pkg}" ]] || continue
    any=1
    [[ -n "${env}" ]] && envdir="${env}"
    local want="${latest}"
    if [[ -n "${TO}" ]]; then
      [[ "${TO%%=*}" == "${pkg}" ]] || continue
      want="${TO#*=}"
    elif [[ "${hold}" == "held" ]]; then
      # Held: a newer release exists but this env cannot take it — either declared
      # in tools.yml, or established by a solve that was tried on this machine and
      # refused. Say so once rather than run a solve that is known to fail; the
      # recorded reason distinguishes the two, so this no longer sends someone to
      # tools.yml to look for an explanation that was never written there.
      # --to overrides, to try a version by hand.
      ok "${tool}/${pkg} held at ${installed} (newer: ${latest:-?})"
      info "    ${reason:-this env cannot take it; see tools.yml}"
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
      # Deliberately NOT rewriting the pin to match what happens to be installed.
      # A pin is a cross-platform decision; mirroring local reality made this
      # command overwrite it on every run, and since the answer differs per platform
      # (mlst 2.35.0 installs on Linux and cannot exist on macOS) two machines would
      # fight over tools.yml indefinitely. Drift is REPORTED instead —
      # `bdtools versions` flags it — and a pin only moves when a package is
      # actually installed here, below.
      [[ "${pinned}" != "${installed}" ]] && info \
        "    note: tools.yml pins ${pkg}=${pinned} (installed here: ${installed})"
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
  CONDA_BIN="${conda}"

  # Solve before installing. An unsatisfiable set is the normal outcome for an
  # older env — a newer package can need a newer libcurl/zlib/perl than something
  # else in the env can tolerate — and it deserves one clear sentence, not a
  # 200-line solver tree presented as "conda install failed".
  # Before the solve, so the dry run and the install that follows it are answering
  # the same question. A solve on one platform and an install on another can
  # disagree, and the disagreement surfaces as an install that fails after a
  # "successful" solve.
  _pin_env_subdir "${envdir}"
  local solvelog="${BDTOOLS_HOME}/logs/${tool}-package-solve.log"
  mkdir -p "$(dirname "${solvelog}")"
  info "  checking whether these versions can coexist in this env…"
  if ! "${conda}" install --dry-run -y -p "${envdir}" \
        -c conda-forge -c bioconda "${specs[@]}" > "${solvelog}" 2>&1; then
    local why; why="$(_solve_headline "${solvelog}")"
    local _blockwhy="conflicts with this environment"
    # Name the platform actually solved for, which on Apple Silicon is the env's
    # osx-64, not the host's Darwin/arm64.
    _solve_is_unavailable "${solvelog}" && \
      _blockwhy="not available for ${CONDA_SUBDIR:-$(uname -s) $(uname -m)}"
    for p in "${plan[@]}"; do
      IFS='|' read -r pkg channel installed want <<< "${p}"
      BLOCKED+=("${tool}/${pkg}: staying on ${installed} — ${want} ${_blockwhy}")
    done
    # Remember it, so the dashboard stops offering an update this machine has
    # already proven it cannot apply. Keyed by version: a newer release is tried
    # again. --to always overrides.
    for p in "${plan[@]}"; do
      IFS='|' read -r pkg channel installed want <<< "${p}"
      "${PYBIN}" - "${PKG_PY}" "${tool}" "${pkg}" "${want}" "${why}" <<'PYREC'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bdtools_packages", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.record_unsatisfiable(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
PYREC
    done
    if _solve_is_unavailable "${solvelog}"; then
      warn "${tool}: those versions are not available for this platform ($(uname -s) $(uname -m))."
      [[ -n "${why}" ]] && info "  conda: ${why}"
      info "  Nothing local can fix this — the build does not exist for this OS."
      info "  (A noarch package can still be unusable here: mlst 2.34+ needs"
      info "   libxcrypt1, which has no macOS build.) Options:"
      info "    • stay on what you have — it is the newest that exists for this OS"
      info "    • if the manifest pins the unavailable version, pin one that exists"
      info "      everywhere you deploy:  bin/bdtools versions ${tool}"
    else
      warn "${tool}: these versions cannot coexist in the existing env."
      [[ -n "${why}" ]] && info "  conda: ${why}"
      info "  This is a property of the env, not a failed download. Options:"
      info "    • rebuild the env FROM NOTHING, which re-solves everything together"
      info "      and sheds stale packages the conflict comes from (--rebuild is"
      info "      additive and cannot remove anything — it will loop right back here):"
      info "        bin/bdtools install ${tool} --fresh"
      info "    • update one package only:  bin/bdtools update-packages ${tool} --to <pkg>=<ver>"
      info "    • leave it: the env keeps working on the versions it has."
    fi
    info "  Not offered again until a newer version appears. Full solver output:"
    info "    ${solvelog}"
    return 0
  fi
  ok "  the set solves here"

  # ...and must solve everywhere else too, or it does not get applied. --local-only
  # is the deliberate escape hatch for a machine-specific experiment.
  if [[ ${LOCAL_ONLY} -eq 0 ]]; then
    info "  checking the same set on ${PLATFORMS}…"
    local badplats; badplats="$(_unavailable_platforms "${tool}" "$(_tool_pyver "${tool}")" "${specs[@]}")"
    if [[ -n "${badplats}" ]]; then
      for p in "${plan[@]}"; do
        IFS='|' read -r pkg channel installed want <<< "${p}"
        BLOCKED+=("${tool}/${pkg}: staying on ${installed} — ${want} is not installable on ${badplats}")
        "${PYBIN}" - "${PKG_PY}" "${tool}" "${pkg}" "${want}" "not installable on ${badplats}" <<'PYREC'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bdtools_packages", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.record_unsatisfiable(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
PYREC
      done
      ok "${tool}: not applied — ${specs[*]} cannot be installed on: ${badplats}"
      info "  Cross-platform consistency outranks being current: applying this here"
      info "  would leave this machine running something the rest of the lab cannot."
      info "  Override for a local experiment only:  --local-only"
      return 0
    fi
    ok "  installable on ${PLATFORMS}"
  fi

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

# Solve for the platform the env was BUILT for, never the host's.
#
# An env's architecture is fixed when it is created, and on Apple Silicon these
# envs are deliberately built osx-64 under Rosetta (install-local.sh's
# ensure_conda_subdir — most of the bioinformatics closure has no arm64 build).
# Nothing here pinned that, so bumping a pin on a Mac ran an osx-arm64 solve
# against an osx-64 prefix and linked arm64 packages into it. conda reports
# success; the tool then dies whenever the analysis reaches one of them —
# "mach-o file, but is an incompatible architecture" out of kraken2's perl deps,
# an hour into someone's day, from a command that only claimed to change a
# version number. install-local.sh has enforced this rule for its own solves all
# along; update-packages is the other door into the same env.
_pin_env_subdir() {   # ENVDIR
  local sd; sd="$(env_conda_subdir "$1")"
  [[ -n "${sd}" ]] || return 0
  export CONDA_SUBDIR="${sd}"
  local foreign; foreign="$(env_foreign_subdirs "$1")"
  [[ -n "${foreign}" ]] && warn "$1 already contains $(printf '%s' "${foreign}" | tr '\n' ';') package(s) — a mixed-architecture env; installing cannot remove them (bin/bdtools doctor explains the rebuild)."
  return 0
}

_conda_install_set() {
  local conda="$1" envdir="$2"; shift 2
  _pin_env_subdir "${envdir}"
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
if [[ ${CHECK_PINS} -eq 1 ]]; then
  check_pins
  exit $?
fi

for tool in $(targets); do
  update_one_tool "${tool}" || FAILED+=("${tool}: update aborted")
done

echo
if [[ ${#REPORT_ONLY[@]} -gt 0 ]]; then
  ok "left alone (report-only in tools.yml): ${REPORT_ONLY[*]}"
  info "  Their package versions are still reported by 'bdtools versions'."
fi
if [[ ${#BLOCKED[@]} -gt 0 ]]; then
  ok "${#BLOCKED[@]} package(s) cannot be updated on this machine — left as they are:"
  for b in "${BLOCKED[@]}"; do info "  • ${b}"; done
  info "  Recorded, so they are not offered again until a newer release appears."
  info "  This is not a failure: the tools keep working on the versions they have."
fi
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo
  warn "finished with problems that need attention:"
  for f in "${FAILED[@]}"; do info "  • ${f}"; done
  exit 1
fi
if [[ ${CHANGED} -eq 1 ]]; then
  ok "package updates complete. Restart the dashboard to pick them up."
  info "  tools.yml now pins what is installed — commit it to share this set."
elif [[ ${#BLOCKED[@]} -eq 0 && ${#REPORT_ONLY[@]} -eq 0 ]]; then
  ok "nothing to install: every declared package is at its newest version, or held."
  info "  Held packages are listed above with the newer version they cannot take."
fi
exit 0
