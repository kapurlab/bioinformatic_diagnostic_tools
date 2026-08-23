#!/usr/bin/env bash
# sync.sh — reconcile a site's shared tool checkouts with the repo state.
#
#   sync.sh [tool|all] [--toolsdir DIR] [--dry-run]
#
# THE recurring deploy step for a shared site install (e.g. /srv/kapurlab/tools).
# After pushing tool changes or bumping a manifest pin, one command moves every
# shared checkout to what it should be — instead of one hand-typed git command
# per tool, different for branch-deployed and pin-deployed tools:
#
#   * a checkout ON A BRANCH (e.g. vsnp_gui, deliberately deployed on main)
#     fast-forwards that branch from origin — never merges, never rebases;
#   * a DETACHED checkout (the pinned tools) moves to the manifest pin
#     (tools.yml `version:`), fetching tags as needed;
#   * a checkout with local TRACKED edits is skipped loudly — nothing is ever
#     clobbered; untracked files (built envs, scratch dirs) never block;
#   * a missing/git-less checkout is reported and skipped, not an error.
#
# CODE ONLY, on purpose: conda envs and OOD cards are not touched — a release
# that changes those goes through `bdtools install --server <tool>` as a
# deliberate act. Sessions load code at launch, so users pick a sync up on
# their next session (or a backend restart), never mid-analysis.
#
# This is for SITE checkouts. Personal managed checkouts (~/.local/share/
# bdtools/checkouts) have their own path — `bdtools update` — which also
# rebuilds envs and enforces the per-tool updates: policy. sync deliberately
# does neither: a site tree is the deployment of record, and what it should
# contain is exactly what the manifest + its branch already say.
#
# Idempotent and cron-safe: quiet "already current" lines, non-zero exit only
# when something that should have moved could not (fetch failure, non-ff).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TARGET="all"; TOOLSDIR="${BDTOOLS_TOOLSDIR:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --toolsdir) TOOLSDIR="$2"; shift 2;;
    --dry-run)  DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help)  sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)         die "unknown option: $1";;
    *)          TARGET="$1"; shift;;
  esac
done

# Resolve the site tools dir: flag/env above, else the site config's TOOLS_ROOT.
if [[ -z "${TOOLSDIR}" && -f "${REPO_DIR}/sites/site.conf" ]]; then
  TOOLSDIR="$(
    # shellcheck disable=SC1091
    source "${REPO_DIR}/sites/site.conf" >/dev/null 2>&1 || true
    echo "${TOOLS_ROOT:-}"
  )"
fi
[[ -n "${TOOLSDIR}" ]] || die "no site tools dir: pass --toolsdir DIR, set BDTOOLS_TOOLSDIR, or define TOOLS_ROOT in sites/site.conf"
[[ -d "${TOOLSDIR}" ]] || die "not a directory: ${TOOLSDIR}"

targets() { if [[ "${TARGET}" == "all" ]]; then manifest_names; else echo "${TARGET}"; fi; }

FAILED=()
sync_one() {
  local name="$1" dir branch before after pin
  dir="${TOOLSDIR}/${name}"
  if [[ ! -d "${dir}/.git" ]]; then
    warn "${name}: no git checkout at ${dir} — skipping (install it first, or it is not deployed here)"
    return 0
  fi
  # Local tracked edits mean a human is mid-something; never move under them.
  if ! git -C "${dir}" diff --quiet || ! git -C "${dir}" diff --cached --quiet; then
    warn "${name}: local tracked edits in ${dir} — skipped (commit/stash them, then re-run)"
    FAILED+=("${name} (dirty)")
    return 0
  fi
  before="$(git -C "${dir}" describe --tags --always 2>/dev/null || echo '?')"
  branch="$(git -C "${dir}" symbolic-ref --short -q HEAD || true)"

  local fetch_err
  if ! fetch_err="$(git -C "${dir}" fetch --quiet --tags origin 2>&1)"; then
    if [[ "${fetch_err}" == *"dubious ownership"* ]]; then
      # Site checkouts are owned by whoever installed them; git refuses to
      # touch another user's repo until it is marked safe. Say the actual fix
      # instead of blaming the network.
      warn "${name}: git refuses ${dir} (owned by another user). Mark it safe once, then re-run:"
      warn "    git config --global --add safe.directory ${dir}"
    else
      warn "${name}: CHECK FAILED — could not fetch origin (no network from this host?)"
      [[ -n "${fetch_err}" ]] && warn "    $(echo "${fetch_err}" | tail -1)"
    fi
    FAILED+=("${name} (fetch)")
    return 0
  fi

  if [[ -n "${branch}" ]]; then
    # Branch-deployed (vsnp_gui on main): fast-forward only.
    local behind
    behind="$(git -C "${dir}" rev-list --count "HEAD..origin/${branch}" 2>/dev/null || echo 0)"
    if [[ "${behind}" == "0" ]]; then
      log "${name}: already current on ${branch} (${before})"
      return 0
    fi
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
      log "${name}: WOULD fast-forward ${branch} by ${behind} commit(s) (${before} → origin/${branch})"
      return 0
    fi
    if ! git -C "${dir}" -c core.autocrlf=false -c core.eol=lf merge --ff-only --quiet "origin/${branch}"; then
      warn "${name}: ${branch} has DIVERGED from origin/${branch} — not touching it. Reconcile by hand."
      FAILED+=("${name} (diverged)")
      return 0
    fi
    after="$(git -C "${dir}" describe --tags --always 2>/dev/null || echo '?')"
    ok "${name}: ${before} → ${after} (fast-forwarded ${branch}, ${behind} commit(s))"
  else
    # Pin-deployed (detached): the manifest says where this site should be.
    pin="$(manifest_get "${name}" version)"
    [[ -n "${pin}" ]] || { warn "${name}: no version pin in manifest — skipped"; return 0; }
    if [[ "${before}" == "${pin}" ]]; then
      log "${name}: already at the pin (${pin})"
      return 0
    fi
    if ! git -C "${dir}" rev-parse -q --verify "${pin}^{commit}" >/dev/null; then
      warn "${name}: pin ${pin} not found on origin — is the tag pushed?"
      FAILED+=("${name} (no ${pin})")
      return 0
    fi
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
      log "${name}: WOULD move ${before} → ${pin}"
      return 0
    fi
    if ! git -C "${dir}" -c advice.detachedHead=false -c core.autocrlf=false -c core.eol=lf checkout --quiet "${pin}"; then
      warn "${name}: checkout of ${pin} failed — see git output above"
      FAILED+=("${name} (checkout)")
      return 0
    fi
    ok "${name}: ${before} → ${pin}"
  fi
}

log "sync: reconciling ${TOOLSDIR} with the manifest$( [[ ${DRY_RUN:-0} -eq 1 ]] && echo ' (dry run)' )"
while read -r name; do
  [[ -n "${name}" ]] || continue
  sync_one "${name}"
done < <(targets)

if [[ ${#FAILED[@]} -gt 0 ]]; then
  warn "not synced: ${FAILED[*]}"
  exit 1
fi
log "sync: done. Sessions pick this up at their next launch/restart."
