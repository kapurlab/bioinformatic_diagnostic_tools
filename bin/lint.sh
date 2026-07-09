#!/usr/bin/env bash
# lint.sh — maintainer guardrail: flag dependency drift across the tool repos.
#
# For each tool checkout, statically compares the dependencies its code actually
# uses (python imports + programs it shells out to) against what its env spec
# declares (environment.yml, requirements.txt, requirements.py). Catches the
# "code grew a dependency the env doesn't ship" bug at release time instead of
# on a user's fresh machine. No env build — fast enough for CI / pre-release.
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

issues=0; checked=0
while read -r name; do
  [[ -n "$name" ]] || continue
  manifest_has "$name" || { warn "unknown tool: $name"; continue; }
  dir="$(tool_dir "$name")"
  if [[ ! -d "${dir}" ]]; then
    [[ ${#ONLY[@]} -gt 0 ]] && echo "${name}: no checkout at ${dir}"
    continue
  fi
  checked=$((checked + 1))
  python3 "${KT_BIN_DIR}/lib/lint.py" --tool "$name" --dir "${dir}" || issues=$((issues + 1))
  check_frontend_base "${dir}" "${name}" || issues=$((issues + 1))
done < <(targets)

echo
if [[ ${checked} -eq 0 ]]; then warn "no tool checkouts found to lint."; exit 0; fi
if [[ ${issues} -gt 0 ]]; then
  warn "${issues} tool(s) have possible dependency drift. A ✗ is very likely a real gap"
  warn "(add it to that tool's environment.yml); a ! is advisory — confirm before acting."
  exit 1
fi
ok "no dependency drift across ${checked} tool(s)."
