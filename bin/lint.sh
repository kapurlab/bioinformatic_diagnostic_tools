#!/usr/bin/env bash
# lint.sh — maintainer guardrail: dependency drift + OOD card validity.
#
# Two static checks, no env build and no scheduler, so it is fast enough for CI:
#
#   1. Dependency drift — compares the dependencies each tool's code actually
#      uses (python imports + programs it shells out to) against what its env
#      spec declares (environment.yml, requirements.txt, requirements.py).
#      Catches "the code grew a dependency the env doesn't ship" at release time
#      instead of on a user's fresh machine.
#   2. OOD cards — every card's YAML parses, form fields are defined, ERB
#      renders, the rendered submission carries the keys OOD needs with no empty
#      scheduler flags, and the shell templates pass `bash -n`. This is the only
#      automated coverage the batch_connect submission path has: it cannot be run
#      here (the reference OOD uses the linux_host adapter, which issues no
#      resource request), so a Slurm site would otherwise be its first execution.
#   Plus a frontend check: built assets must use relative URLs, or the OOD
#   dashboard's sub-path proxy 404s every one of them.
#
#   lint.sh [tool ...]      (default: every tool with a checkout)
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

ONLY=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) ONLY+=("$1"); shift;;
  esac
done
targets() { if [[ ${#ONLY[@]} -gt 0 ]]; then printf '%s\n' "${ONLY[@]}"; else manifest_names; fi; }

# The consolidated OOD dashboard reverse-proxies each tool under a sub-path
# (/t/<tool>/), exactly as OOD's /rnode already serves them under a sub-path.
# That only works if the built frontend references assets RELATIVELY (./assets,
# a Vite `base: "./"` build), never from the site root (/assets, base "/").
# A root-absolute build silently 404s every asset behind the proxy, so guard it
# here: catch it at release time, not on a user's screen.
check_frontend_base() {
  local dir="$1" name="$2" idx="$1/frontend/dist/index.html"
  [[ -f "${idx}" ]] || return 0
  # Absolute-root asset refs: src="/... or href="/... (but not protocol-relative //).
  if grep -qE '(src|href)="/[^/]' "${idx}"; then
    warn "${name}: frontend/dist/index.html has root-absolute asset URLs (src/href=\"/…\")."
    warn "  This breaks the OOD dashboard sub-path proxy. Rebuild the frontend with a"
    warn "  relative base (Vite: base: './'). See docs/BUILDING_A_TOOL.md."
    return 1
  fi
  return 0
}

# OOD cards are templates that only execute inside OOD, on a scheduler. The
# consolidated dashboard's submit.yml.erb has never run anywhere — the reference
# deployment uses the linux_host adapter, which issues no resource request — so
# the first Slurm site is the first execution. Validate everything that does not
# need a scheduler: YAML parses, form fields are defined, ERB renders, the
# rendered submission has the keys OOD needs with no empty flags, and the shell
# templates pass `bash -n`. See bin/lib/check_cards.py.
check_cards() {
  local dir="$1" name="$2" cards=()
  while IFS= read -r c; do [[ -n "${c}" ]] && cards+=("${c}"); done \
    < <(find "${dir}/ood/apps" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
  [[ ${#cards[@]} -gt 0 ]] || return 0
  "${PYBIN}" "${KT_BIN_DIR}/lib/check_cards.py" "${cards[@]}" || return 1
  return 0
}

issues=0; checked=0

# The umbrella owns the consolidated dashboard card; check it even when no tool
# checkout exists, because it is the one card a production site installs.
if [[ -d "${REPO_DIR}/ood/apps" ]]; then
  check_cards "${REPO_DIR}" umbrella || issues=$((issues + 1))
fi

while read -r name; do
  [[ -n "$name" ]] || continue
  manifest_has "$name" || { warn "unknown tool: $name"; continue; }
  dir="$(tool_dir "$name")"
  if [[ ! -d "${dir}" ]]; then
    [[ ${#ONLY[@]} -gt 0 ]] && echo "${name}: no checkout at ${dir}"
    continue
  fi
  checked=$((checked + 1))
  "${PYBIN}" "${KT_BIN_DIR}/lib/lint.py" --tool "$name" --dir "${dir}" || issues=$((issues + 1))
  check_frontend_base "${dir}" "${name}" || issues=$((issues + 1))
  check_cards "${dir}" "${name}" || issues=$((issues + 1))
done < <(targets)

echo
if [[ ${checked} -eq 0 ]]; then warn "no tool checkouts found to lint."; exit 0; fi
if [[ ${issues} -gt 0 ]]; then
  warn "${issues} check(s) failed. A ✗ is very likely real — a missing dependency"
  warn "belongs in that tool's environment.yml; a broken card would fail at OOD"
  warn "submit time. A ! is advisory — confirm before acting."
  exit 1
fi
ok "no dependency drift, and every OOD card validates, across ${checked} tool(s)."
