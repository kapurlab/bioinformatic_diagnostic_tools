#!/usr/bin/env bash
# complete-env.sh — add what a tool's conda_setup/environment.yml declares that
# its environment lacks. Only that.
#
#   bdtools complete-env <tool|all> [--dry-run] [--allow-report-only]
#
# `bdtools sync` moves a site's code and nothing else; `bdtools update` rebuilds
# only the managed checkouts it owns. So a release that adds a package to a
# tool's spec reaches the code everywhere and the environment nowhere, and the
# new code imports what the env does not have (kraken_id_parse_gui + plotly,
# Ames HPC, 2026-10-02: an hour of alignment, then "No module named 'plotly'").
# The dashboard's "Update tool environments" button runs this.
#
# Why not `install --rebuild`: that re-solves the WHOLE spec additively, so
# packages already installed may move to satisfy it — on a working env, in a
# diagnostic lab, that is a change nobody asked for. This asks conda for the
# missing declared packages with every installed package frozen
# (--freeze-installed): the env gains exactly those packages and whatever new
# dependencies they need, or it is left exactly as it was, with the solver's
# reason and the fuller remedies named. The science pins in tools.yml cannot
# move here, because nothing installed can.
#
# Per tool, in order:
#   1. lib/env_spec.py: which env runs the tool (the launch's own resolution) and
#      which declared packages its conda-meta lacks. Nothing missing → done.
#   2. a dry-run solve with everything installed frozen. Refused → nothing
#      changes; the refusal is recorded so the dashboard stops offering it until
#      the spec changes (a terminal run always retries).
#   3. snapshot (bdtools restore-env <tool> puts it back), pin the solve to the
#      env's own platform, guard the activation hooks, install.
#   4. re-apply the tool's local patches if it has any; run the env self-check
#      before and after (lib/check.py --scope env) — a check that passed before
#      and fails after is a failure of this run, reported with the restore
#      command, never auto-restored.
# `all` passes complete envs quietly and skips report-only tools with a note
# (tools.yml `updates:`), like `update all`; a named report-only tool needs
# --allow-report-only, like `update <tool>`.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

ENV_SPEC_PY="${KT_BIN_DIR}/lib/env_spec.py"
CHECK_PY="${KT_BIN_DIR}/lib/check.py"
TARGET=""; ALLOW_REPORT_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)           DRY_RUN=1; export DRY_RUN; shift;;
    --allow-report-only) ALLOW_REPORT_ONLY=1; shift;;
    -h|--help)           sed -n '2,37p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)                  die "unknown option: $1";;
    *)                   [[ -z "${TARGET}" ]] || die "name one tool, or 'all'"; TARGET="$1"; shift;;
  esac
done
[[ -n "${TARGET}" ]] || die "name a tool or 'all' (see: bdtools list)"
if [[ "${TARGET}" != "all" ]]; then
  manifest_has "${TARGET}" || die "unknown tool: ${TARGET} (see: bdtools list)"
fi
_need_python

targets() { if [[ "${TARGET}" == "all" ]]; then manifest_names; else echo "${TARGET}"; fi; }

ADDED=(); SKIPPED=(); BLOCKED=(); FAILED=()

# One field of lib/env_spec.py's answer for a tool. Lists come one per line;
# read with a loop, not mapfile, because macOS still ships bash 3.2.
_field() { "${PYBIN}" "${ENV_SPEC_PY}" "$1" --field "$2"; }

# The env's self-check, as a verdict: 0 passes, 1 fails. Quiet; doctor has the words.
_selfcheck() {   # _selfcheck TOOL DIR ENVDIR
  "${PYBIN}" "${CHECK_PY}" --tool "$1" --dir "$2" --python "$3/bin/python" --scope env >/dev/null 2>&1
}

complete_one() {   # complete_one TOOL SWEEP(0|1)
  local tool="$1" sweep="$2"
  local applies; applies="$(_field "${tool}" applies)"
  if [[ "${applies}" != "true" ]]; then
    [[ "${sweep}" -eq 1 ]] || info "${tool}: no conda_setup/environment.yml, or no environment resolves for it — nothing to complete (bin/bdtools doctor ${tool} says which)"
    return 0
  fi
  local envdir spec; envdir="$(_field "${tool}" env_dir)"; spec="$(_field "${tool}" spec)"
  local -a missing=() channels=() names=()
  local line
  while IFS= read -r line; do [[ -n "${line}" ]] && missing+=("${line}"); done < <(_field "${tool}" missing)
  while IFS= read -r line; do [[ -n "${line}" ]] && channels+=("${line}"); done < <(_field "${tool}" channels)
  if [[ ${#missing[@]} -eq 0 ]]; then
    ok "${tool}: the environment has everything its spec declares (${envdir})"
    return 0
  fi
  # Names without constraints, for the summary lines.
  local m
  for m in "${missing[@]}"; do names+=("$(printf '%s' "${m#*::}" | sed -E 's/[=<>!~ [].*$//')"); done

  if ! require_updatable "${tool}" "${ALLOW_REPORT_ONLY}" complete-env "$([[ ${sweep} -eq 1 ]] && echo 0 || echo 1)"; then
    SKIPPED+=("${tool}")
    return 0
  fi

  log "${tool}: ${names[*]} — declared in conda_setup/environment.yml, not in the environment"
  info "  env:  ${envdir}"
  info "  spec: ${spec}"
  local dir; dir="$(tool_dir "${tool}")"
  local conda; conda="$(detect_conda)" || { FAILED+=("${tool}: conda/mamba not found"); return 0; }

  # The spec's own channels, in its order; the suite's two when it names none.
  local -a ch=()
  local c
  for c in "${channels[@]}"; do ch+=(-c "${c}"); done
  [[ ${#ch[@]} -gt 0 ]] || ch=(-c conda-forge -c bioconda)
  # Solve for the platform the env was BUILT for, never the host's (the rule
  # install-local.sh and update-packages.sh both keep, for the 2026-08 incident).
  local sd; sd="$(env_conda_subdir "${envdir}" 2>/dev/null || true)"
  [[ -n "${sd}" ]] && export CONDA_SUBDIR="${sd}"

  local solvelog="${BDTOOLS_HOME}/logs/${tool}-complete-env-solve.log"
  mkdir -p "$(dirname "${solvelog}")" 2>/dev/null || solvelog="/tmp/${tool}-complete-env-solve.log"
  info "  solving with every installed package frozen…"
  if ! "${conda}" install --dry-run -y -p "${envdir}" --freeze-installed "${ch[@]}" "${missing[@]}" > "${solvelog}" 2>&1; then
    warn "${tool}: ${names[*]} cannot be added without changing packages already installed — the environment was left exactly as it was."
    info "  That is this command's promise. The fuller operations are deliberate acts:"
    info "    re-solve the whole spec additively (other packages may move):  bin/bdtools install ${tool} --rebuild"
    info "    build from nothing (sheds whatever the conflict comes from):    bin/bdtools install ${tool} --fresh"
    info "  Solver output: ${solvelog}"
    info "  Not offered on the dashboard again until the spec changes; this command always retries."
    "${PYBIN}" "${ENV_SPEC_PY}" "${tool}" --field applies \
      --record-blocked "${names[*]} could not be added without changing packages already installed (checked here $(date +%Y-%m-%d))" >/dev/null 2>&1 || true
    BLOCKED+=("${tool}: ${names[*]}")
    return 0
  fi
  ok "  solves with nothing already installed moving"

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    info "  [dry-run] ${tool}: would add ${names[*]}"
    ADDED+=("${tool}: ${names[*]} (dry-run)")
    return 0
  fi

  local before=1
  _selfcheck "${tool}" "${dir}" "${envdir}" || before=0
  snapshot_env "${tool}" "${envdir}"
  harden_conda_hooks "${envdir}"
  if ! run "${conda}" install -y -p "${envdir}" --freeze-installed "${ch[@]}" "${missing[@]}"; then
    harden_conda_hooks "${envdir}"
    warn "${tool}: conda install failed after a successful solve — conda rolls the transaction back."
    restore_env_hint "${tool}"
    FAILED+=("${tool}: conda install failed after a successful solve — see above")
    return 0
  fi
  harden_conda_hooks "${envdir}"
  "${PYBIN}" "${ENV_SPEC_PY}" "${tool}" --field applies --clear-blocked >/dev/null 2>&1 || true

  # Local patches, re-applied: a package install can overwrite patched files, and
  # apply.sh is idempotent precisely so this is safe after every package change.
  if [[ -x "${dir}/deploy/vsnp3-patches/apply.sh" ]]; then
    log "${tool}: re-applying local patches"
    run "${dir}/deploy/vsnp3-patches/apply.sh" "${envdir}" \
      || FAILED+=("${tool}: PATCHES DID NOT RE-APPLY — results may be wrong until fixed (bin/bdtools doctor ${tool})")
  fi

  if _selfcheck "${tool}" "${dir}" "${envdir}"; then
    ok "${tool}: added ${names[*]}; the environment passes its self-check"
  elif [[ ${before} -eq 0 ]]; then
    ok "${tool}: added ${names[*]}"
    info "  The environment's self-check was already failing before this run and still is — unrelated to"
    info "  this addition; see: bin/bdtools doctor ${tool}"
  else
    warn "${tool}: added ${names[*]}, but the environment's self-check passed before this run and fails now."
    info "  See what: bin/bdtools doctor ${tool}"
    restore_env_hint "${tool}"
    FAILED+=("${tool}: self-check fails after the addition — bin/bdtools restore-env ${tool} puts the env back")
    return 0
  fi
  ADDED+=("${tool}: ${names[*]}")
}

for t in $(targets); do
  complete_one "${t}" "$([[ "${TARGET}" == "all" ]] && echo 1 || echo 0)"
done

echo
[[ ${#ADDED[@]}   -gt 0 ]] && { log "added:";   for a in "${ADDED[@]}";   do info "  ${a}"; done; }
[[ ${#BLOCKED[@]} -gt 0 ]] && { log "not added (environment unchanged):"; for a in "${BLOCKED[@]}"; do info "  ${a}"; done; }
[[ ${#SKIPPED[@]} -gt 0 ]] && info "left unchanged (report-only in tools.yml): ${SKIPPED[*]}"
if [[ ${#FAILED[@]} -gt 0 ]]; then
  log "failed:"; for a in "${FAILED[@]}"; do warn "  ${a}"; done
  exit 1
fi
# A refusal is not a failure of this command — the env is exactly as it was — but
# the caller (a dashboard button, a script) must not read "done" either.
[[ ${#BLOCKED[@]} -eq 0 ]] || exit 2
# A named report-only tool without the flag: said above; make it count.
if [[ "${TARGET}" != "all" && ${#SKIPPED[@]} -gt 0 ]]; then exit 5; fi
exit 0
