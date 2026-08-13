#!/usr/bin/env bash
# doctor.sh — check installed tools for runtime readiness and tell the user, in
# plain language, exactly what to run to fix anything that's wrong.
#
# Verifies each tool against its requirements spec (bin/lib/requirements.py):
# the env is built, the python modules import, the external programs are on
# PATH, and the reference databases exist. Catches the failure modes that
# otherwise only surface as a traceback mid-analysis (a missing dependency, an
# unset/empty database path).
#
#   doctor.sh [tool ...]      (default: every installed tool)
#   doctor.sh --scope env     (skip database checks — used by the build self-check)
set -uo pipefail   # NOTE: not -e; a failing tool check must not abort the sweep
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SCOPE="all"; JSON=0; ONLY=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope) SCOPE="$2"; shift 2;;
    --json)  JSON=1; shift;;          # machine-readable array (used by the dashboard)
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) ONLY+=("$1"); shift;;
  esac
done

# Which python does this tool ACTUALLY run on?
#
# Ask tool_launch.resolve — the same resolver the dashboard launches through and
# that packages.env_dir_for reports versions from. Doctor used to answer this
# question on its own (checkout env, else a conda env matching the manifest's
# `env:` NAME), and on any machine where those disagree it audited an env the
# tool never touches. Live example: a shared site install runs vsnp_gui from the
# PREFIX env /srv/kapurlab/tools/vsnp3, while a stale personal `vsnp3` env from
# an old install still existed in the user's conda — doctor graded the personal
# one and reported fastapi/uvicorn/pydantic and snp-dists "missing", plus a fix
# ("rebuilds the vsnp3 env") that would have rebuilt a perfectly good env to cure
# a problem in a different one. Every finding was a false positive, and the card
# said "Needs setup before it can run" about a tool that was running.
#
# The heuristics stay as fallbacks for installs tool_launch cannot resolve.
resolve_env_python() {
  local dir="$1" envname="$2" name="${3:-}" conda py
  if [[ -n "${name}" ]]; then
    # lib dir passed as argv[1], not via the environment: KT_BIN_DIR is a plain
    # shell variable in common.sh, never exported, so reading it from os.environ
    # here silently found nothing and every lookup fell through to the old
    # heuristics — the bug this function exists to fix, still happening.
    py="$("${PYBIN}" -c '
import os, sys
sys.path.insert(0, sys.argv[1])
try:
    import tool_launch
    d = (tool_launch.resolve(sys.argv[2], 0) or {}).get("env_dir") or ""
except Exception:
    d = ""
if d and d != "(base)":
    p = os.path.join(d, "bin", "python")
    if os.path.exists(p):
        print(p)
' "${KT_BIN_DIR}/lib" "${name}" 2>/dev/null)"
    [[ -n "${py}" ]] && { echo "${py}"; return; }
  fi
  if [[ -x "${dir}/env/bin/python" ]]; then echo "${dir}/env/bin/python"; return; fi
  conda="$(detect_conda 2>/dev/null || true)"
  if [[ -n "${conda}" && -n "${envname}" ]] \
     && "${conda}" env list 2>/dev/null | awk '{print $1}' | grep -qxF "${envname}"; then
    "${conda}" run -n "${envname}" sh -c 'echo $CONDA_PREFIX/bin/python' 2>/dev/null
  fi
}

targets() { if [[ ${#ONLY[@]} -gt 0 ]]; then printf '%s\n' "${ONLY[@]}"; else manifest_names; fi; }

issues=0; checked=0; json_items=()
while read -r name; do
  [[ -n "$name" ]] || continue
  manifest_has "$name" || { [[ ${JSON} -eq 1 ]] || warn "unknown tool: $name"; continue; }
  dir="$(tool_dir "$name")"
  if [[ ! -d "${dir}/.git" && ! -x "${dir}/env/bin/python" ]]; then
    # Not installed here — silent unless the user asked for this tool by name.
    [[ ${JSON} -eq 0 && ${#ONLY[@]} -gt 0 ]] && echo "${name}: not installed (bin/bdtools install ${name})"
    continue
  fi
  checked=$((checked + 1))
  py="$(resolve_env_python "${dir}" "$(manifest_get "$name" env)" "$name")"
  if [[ ${JSON} -eq 1 ]]; then
    item="$("${PYBIN}" "${KT_BIN_DIR}/lib/check.py" --tool "$name" --dir "${dir}" \
              --python "${py}" --scope "${SCOPE}" --json)" || issues=$((issues + 1))
    [[ -n "${item}" ]] && json_items+=("${item}")
  else
    "${PYBIN}" "${KT_BIN_DIR}/lib/check.py" --tool "$name" --dir "${dir}" \
            --python "${py}" --scope "${SCOPE}" || issues=$((issues + 1))
    # An env can pass every check above — modules import, programs resolve — and
    # still contain packages built for another architecture, linked in by an
    # update that solved for the wrong platform. They fail only when the analysis
    # actually calls them, so name them here.
    envdir="${py%/bin/python}"
    foreign="$([[ -n "${py}" ]] && env_foreign_subdirs "${envdir}" || true)"
    if [[ -n "${foreign}" ]]; then
      warn "${name}: mixed-architecture env — $(printf '%s' "${foreign}" | tr '\n' ';') package(s) are not $(env_conda_subdir "${envdir}")."
      info "  FIX: rm -rf ${envdir} && bin/bdtools install ${name}   (an update cannot remove them)"
      issues=$((issues + 1))
    fi
    # An env can also pass every check and still be the WRONG env: if the last
    # build didn't finish, this code is newer than the environment running it
    # (conda rolls a failed transaction back, so the previous env survives
    # intact — and silently). Say so here, where users look when something is off.
    ref="$(git -C "${dir}" describe --tags --always 2>/dev/null || echo '')"
    if build_failed_for "$name" "${ref}"; then
      warn "${name}: the last environment build did not finish — this checkout (${ref:-?}) may be running the previous env."
      info "  FIX: bin/bdtools update ${name}"
      issues=$((issues + 1))
    fi
    echo
  fi
done < <(targets)

if [[ ${JSON} -eq 1 ]]; then
  ( IFS=,; echo "[${json_items[*]-}]" )
  exit $([[ ${issues} -gt 0 ]] && echo 1 || echo 0)
fi
if [[ ${checked} -eq 0 ]]; then
  warn "no installed tools found to check (install one: bin/bdtools install <tool>)"
  exit 0
fi
if [[ ${issues} -gt 0 ]]; then
  warn "${issues} tool(s) need attention — run the suggested fix above, then re-run: bin/bdtools doctor"
  exit 1
fi
ok "all ${checked} installed tool(s) ready."
