#!/usr/bin/env bash
# check-updates.sh — report (and optionally apply) newer upstream versions.
#
#   check-updates.sh [tool|all]            report only (read-only)
#   check-updates.sh --apply <tool|all>    move the checkout to the newest ref
#                                          and rebuild (bumps the manifest pin)
#   check-updates.sh --apply --force ...   rebuild even if already up to date
#
# --apply skips any tool already sitting on the target ref with a built env
# (the rebuild would re-solve/re-download for no change). Pass --force to
# rebuild those anyway. Tools not yet checked out are skipped with a note
# (install them with `bdtools install <tool>`) so `--apply all` never aborts.
#
# "Newest" = the highest version-sorted git tag on the tool's remote (via
# `git ls-remote`, so it works for any public repo with no auth and even before
# GitHub Releases exist). Tools that have no tags yet track their pinned branch;
# --apply then fast-forwards that branch.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

APPLY=0
FORCE=0
NOT_INSTALLED=()   # tools named in an --apply run that aren't checked out yet
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)   APPLY=1; shift;;
    --force)   FORCE=1; shift;;
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
  local name="$1" dir repo pinned latest target current
  dir="$(tool_dir "$name")"; repo="$(manifest_get "$name" repo)"; pinned="$(manifest_get "$name" version)"
  latest="$(latest_tag "$repo")"
  target="${latest:-$pinned}"   # newest tag, else stay on the pinned branch

  # Not checked out yet: `update` refreshes existing installs — a fresh install
  # is `bdtools install`, which also builds the env (and any bundled DB). Don't
  # abort the whole run for it; skip with a note and collect it for the summary
  # so `update all` completes cleanly and tells the user what still needs installing.
  if [[ ! -d "${dir}/.git" ]]; then
    warn "${name} not installed — skipping (run: bdtools install ${name})"
    NOT_INSTALLED+=("${name}")
    return 0
  fi

  # Fast path: already on the target tag AND the env is built. A rebuild here
  # would re-solve and re-download for zero change — this is exactly what made
  # `update all` grind through a fresh conda solve per tool even when nothing
  # was newer. Skip unless --force. Only for a concrete tag target; branch-
  # tracked tools (latest empty) always refresh in case the branch moved.
  current="$(git -C "$dir" describe --tags --always 2>/dev/null || echo '')"
  if [[ ${FORCE} -eq 0 && -n "${latest}" && "${current}" == "${target}" ]] \
     && "${KT_BIN_DIR}/install-local.sh" --print-python "$name" >/dev/null 2>&1; then
    ok "${name} already at ${target} with a built env — skipping (use --force to rebuild)"
    return 0
  fi

  log "updating ${name} -> ${target}"
  run git -C "$dir" fetch --tags --depth 1 origin "${target}"
  run git -C "$dir" checkout -q "${target}" || run git -C "$dir" checkout -q -B "${target}" "origin/${target}"
  if [[ -n "$latest" && "$latest" != "$pinned" ]]; then
    log "bumping manifest pin: ${name} ${pinned} -> ${latest}"
    run manifest_set "$name" version "$latest"
  fi
  log "rebuilding ${name}"
  local a=(--rebuild); [[ ${DRY_RUN} -eq 1 ]] && a+=(--dry-run)
  run "${KT_BIN_DIR}/install-local.sh" --build-only "${a[@]}" "$name"
  ok "${name} updated"
}

if [[ ${APPLY} -eq 1 ]]; then
  [[ "${TARGET}" != "all" || ${#ARGS[@]} -gt 0 ]] || die "name a tool or 'all'"
  while read -r n; do [[ -n "$n" ]] && apply_one "$n"; done < <(targets)
  if [[ ${#NOT_INSTALLED[@]} -gt 0 ]]; then
    echo
    warn "not installed (skipped): ${NOT_INSTALLED[*]}"
    info "  install them with:  bdtools install ${NOT_INSTALLED[*]}"
  fi
else
  while read -r n; do [[ -n "$n" ]] && report_one "$n"; done < <(targets)
fi
