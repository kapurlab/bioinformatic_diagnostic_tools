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

# Resolve a tool's env interpreter without building anything: prefer the local
# per-tool env, else a same-named conda env. Echoes the python path or nothing.
resolve_env_python() {
  local dir="$1" envname="$2" conda
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
  py="$(resolve_env_python "${dir}" "$(manifest_get "$name" env)")"
  if [[ ${JSON} -eq 1 ]]; then
    item="$(python3 "${KT_BIN_DIR}/lib/check.py" --tool "$name" --dir "${dir}" \
              --python "${py}" --scope "${SCOPE}" --json)" || issues=$((issues + 1))
    [[ -n "${item}" ]] && json_items+=("${item}")
  else
    python3 "${KT_BIN_DIR}/lib/check.py" --tool "$name" --dir "${dir}" \
            --python "${py}" --scope "${SCOPE}" || issues=$((issues + 1))
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
