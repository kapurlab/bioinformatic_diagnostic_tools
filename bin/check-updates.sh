#!/usr/bin/env bash
# check-updates.sh — report (and optionally apply) newer upstream versions.
#
#   check-updates.sh [tool|all]            report only (read-only)
#   check-updates.sh --apply <tool|all>    move the checkout to the newest ref
#                                          and rebuild (bumps the manifest pin)
#
# "Newest" = the highest version-sorted git tag on the tool's remote (via
# `git ls-remote`, so it works for any public repo with no auth and even before
# GitHub Releases exist). Tools that have no tags yet track their pinned branch;
# --apply then fast-forwards that branch.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

APPLY=0
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)   APPLY=1; shift;;
    --dry-run) DRY_RUN=1; export DRY_RUN; shift;;
    *)         ARGS+=("$1"); shift;;
  esac
done
TARGET="${ARGS[0]:-all}"

latest_tag() {  # repo-url -> highest version-sorted tag, or empty (never aborts)
  { git ls-remote --tags --refs "$1" 2>/dev/null \
      | awk -F/ '{print $NF}' | sort -V | tail -1; } || true
}

targets() { if [[ "${TARGET}" == "all" ]]; then manifest_names; else echo "${TARGET}"; fi; }

report_one() {
  local name="$1" dir repo pinned installed latest status
  dir="$(tool_dir "$name")"; repo="$(manifest_get "$name" repo)"; pinned="$(manifest_get "$name" version)"
  installed="$([[ -d "${dir}/.git" ]] && git -C "$dir" describe --tags --always 2>/dev/null || echo '—')"
  latest="$(latest_tag "$repo")"
  if [[ -z "$latest" ]]; then status="no tags (tracks ${pinned})"
  elif [[ "$latest" == "$pinned" ]]; then status="up to date"
  else status="↑ ${latest} available"; fi
  printf '%-22s pinned=%-14s installed=%-16s latest=%-12s %s\n' \
    "$name" "$pinned" "$installed" "${latest:-—}" "$status"
}

apply_one() {
  local name="$1" dir repo pinned latest target
  dir="$(tool_dir "$name")"; repo="$(manifest_get "$name" repo)"; pinned="$(manifest_get "$name" version)"
  latest="$(latest_tag "$repo")"
  target="${latest:-$pinned}"   # newest tag, else stay on the pinned branch
  [[ -d "${dir}/.git" ]] || die "${name} not installed at ${dir} (run: bdtools install ${name})"

  log "updating ${name} -> ${target}"
  run git -C "$dir" fetch --tags --depth 1 origin "${target}"
  run git -C "$dir" checkout -q "${target}" || run git -C "$dir" checkout -q -B "${target}" "origin/${target}"
  if [[ -n "$latest" && "$latest" != "$pinned" ]]; then
    log "bumping manifest pin: ${name} ${pinned} -> ${latest}"
    run manifest_set "$name" version "$latest"
  fi
  log "rebuilding ${name}"
  local a=(); [[ ${DRY_RUN} -eq 1 ]] && a+=(--dry-run)
  run "${KT_BIN_DIR}/install-local.sh" --build-only ${a[@]+"${a[@]}"} "$name"
  ok "${name} updated"
}

if [[ ${APPLY} -eq 1 ]]; then
  [[ "${TARGET}" != "all" || ${#ARGS[@]} -gt 0 ]] || die "name a tool or 'all'"
  while read -r n; do [[ -n "$n" ]] && apply_one "$n"; done < <(targets)
else
  while read -r n; do [[ -n "$n" ]] && report_one "$n"; done < <(targets)
fi
