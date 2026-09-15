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
#   * a checkout with local SOURCE edits is skipped loudly, naming the files —
#     nothing a human wrote is ever clobbered. Regenerated build output does
#     NOT count as an edit (common.sh:tool_blocking_edits — frontend/dist,
#     package-lock.json, __pycache__/*.pyc): those are derived from what the
#     tag already carries, and treating them as human edits is what stopped
#     sync deploying at all. Untracked files (built envs, scratch) never block;
#   * ood/apps/** site-localized card config is carried ACROSS the move;
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
# when something that should have moved could not (fetch failure, non-ff,
# source edits, or a branch with nothing upstream to sync to).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TARGET="all"; TOOLSDIR="${BDTOOLS_TOOLSDIR:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --toolsdir) TOOLSDIR="$2"; shift 2;;
    --dry-run)  DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help)  sed -n '2,37p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
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
  local name="$1" dir branch before after pin p
  dir="${TOOLSDIR}/${name}"
  if [[ ! -d "${dir}/.git" ]]; then
    warn "${name}: no git checkout at ${dir} — skipping (install it first, or it is not deployed here)"
    return 0
  fi
  # Can git open it at all? Site checkouts are owned by whoever installed them,
  # and git refuses another user's repo until it is marked safe. That has to be
  # asked BEFORE the dirty check: `git diff --quiet` on a repo git will not open
  # exits 129 and prints the whole `git diff --no-index` usage text, and the
  # check below read any non-zero exit as "has local edits". So a root-owned
  # but CLEAN checkout was skipped as (dirty) on every run — a guard that never
  # lets go — and the safe.directory hint, which only the fetch printed, never
  # appeared because the fetch was never reached (ICAR-NIVEDI, 2026-09-01:
  # eight tools, 58 KB of usage text, the one line per tool that mattered lost
  # in it).
  local probe_err
  if ! probe_err="$(git -C "${dir}" rev-parse --git-dir 2>&1)"; then
    if [[ "${probe_err}" == *"dubious ownership"* ]]; then
      warn "${name}: git refuses ${dir} (owned by another user). Mark it safe once, then re-run:"
      warn "    git config --global --add safe.directory ${dir}"
    else
      warn "${name}: git cannot open ${dir} — skipped"
      warn "    $(printf '%s\n' "${probe_err}" | head -1)"
    fi
    FAILED+=("${name} (unreadable)")
    return 0
  fi
  # Local tracked edits mean a human is mid-something; never move under them.
  #
  # What counts as "a human is mid-something" is the SHARED rule
  # (common.sh:tool_blocking_edits), not a bare `git diff --quiet`. That is the
  # whole reason the rule lives in one place — and sync asked git directly
  # anyway, so it drifted from the two updaters exactly as the helper's comment
  # warns (live 2026-09-15, this site: six of nine checkouts skipped as dirty,
  # four of them behind their pin, over nothing but regenerated
  # `__pycache__/*.pyc` and an npm-rewritten `frontend/package-lock.json` —
  # files no user ever touched. `bdtools update` had already been taught to
  # tolerate them; sync had not, so THE deploy step was the one command that
  # could not deploy).
  #
  # Report the paths, like check-updates does: "local tracked edits" without
  # saying which file sends the operator to `git status` in every skipped
  # checkout to find out whether it was their edit or the interpreter's.
  local blocking
  blocking="$(tool_blocking_edits "${dir}")"
  if [[ -n "${blocking}" ]]; then
    warn "${name}: local source changes in ${dir} — skipped (commit/stash them, then re-run)"
    while IFS= read -r p; do [[ -n "${p}" ]] && warn "    ${p}"; done <<< "${blocking}"
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
    #
    # The branch must actually HAVE an upstream. `rev-list HEAD..origin/<branch>`
    # fails when origin/<branch> does not resolve, and the `|| echo 0` below
    # read that failure as "zero commits behind" — so a checkout deployed on a
    # branch with nothing upstream was reported "already current", in green, at
    # every run. That is the worst shape a deploy bug can take: the failure is
    # indistinguishable from success, so nobody looks. It happens whenever the
    # branch was never pushed, or was deleted upstream after deploy — the
    # checkout keeps running code no one else can fetch, and the one command
    # whose job is to notice says it is fine. Ask git whether the ref exists
    # instead of inferring it from a count that cannot fail loudly.
    local behind ahead
    if ! git -C "${dir}" rev-parse -q --verify "refs/remotes/origin/${branch}" >/dev/null; then
      warn "${name}: deployed on local-only branch '${branch}' — origin has no such branch, so there is nothing to sync to."
      warn "    Push it (git -C ${dir} push -u origin ${branch}), or put the checkout back on its pin."
      FAILED+=("${name} (no upstream)")
      return 0
    fi
    behind="$(git -C "${dir}" rev-list --count "HEAD..origin/${branch}" 2>/dev/null || echo 0)"
    ahead="$(git -C "${dir}" rev-list --count "origin/${branch}..HEAD" 2>/dev/null || echo 0)"
    # Diverged is about HISTORY, so decide it from the commit counts. Letting
    # `merge --ff-only` be the judge conflated it with the working tree: the
    # merge also refuses when a tracked file would be overwritten, so a checkout
    # that was a plain fast-forward with a regenerated .pyc in it got reported
    # as DIVERGED — sending the operator to reconcile a history that was never
    # in conflict.
    if [[ "${ahead}" != "0" ]]; then
      warn "${name}: ${branch} has DIVERGED from origin/${branch} (${ahead} local commit(s) not upstream) — not touching it. Reconcile by hand."
      FAILED+=("${name} (diverged)")
      return 0
    fi
    if [[ "${behind}" == "0" ]]; then
      log "${name}: already current on ${branch} (${before})"
      return 0
    fi
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
      log "${name}: WOULD fast-forward ${branch} by ${behind} commit(s) (${before} → origin/${branch})"
      return 0
    fi
    # Clear the regenerable dirt the merge would otherwise trip over. Only
    # tolerated paths can still be here — anything blocking returned above —
    # and ood/apps/** rides across, same contract as the pin path below.
    local site_snapshot; site_snapshot="$(snapshot_site_edits "${dir}")"
    [[ -n "${site_snapshot}" ]] && log "${name}: preserving site-localized OOD card config across the move"
    git -C "${dir}" -c core.autocrlf=false -c core.eol=lf checkout -f HEAD -- . >/dev/null 2>&1 || true
    if ! git -C "${dir}" -c core.autocrlf=false -c core.eol=lf merge --ff-only --quiet "origin/${branch}"; then
      restore_site_edits "${dir}" "${site_snapshot}"
      warn "${name}: ${branch} could not fast-forward to origin/${branch} — not touching it. Reconcile by hand."
      FAILED+=("${name} (non-ff)")
      return 0
    fi
    restore_site_edits "${dir}" "${site_snapshot}"
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
    # Tolerating regenerable dirt above is only half the fix: a PLAIN checkout
    # still aborts on it ("Your local changes to the following files would be
    # overwritten"), so the tolerance would have bought a clearer error and
    # nothing else. Force past it, exactly as check-updates.sh and
    # install-local.sh do — and for the same reason it is safe: everything
    # tool_blocking_edits lets through is derived output, so the tag's own
    # committed copy is the correct one to land. sync builds nothing, which
    # makes that MORE true here, not less: a release ships its built
    # frontend/dist, and the deployed code should be the release's, not
    # whatever the last local npm run left behind.
    #
    # ood/apps/** is the exception the force must not eat: this site's cluster
    # and account are written into those cards, and a deployment cannot run
    # them otherwise. Snapshot and put them back, as the updaters do.
    local site_snapshot; site_snapshot="$(snapshot_site_edits "${dir}")"
    [[ -n "${site_snapshot}" ]] && log "${name}: preserving site-localized OOD card config across the move"
    if ! git -C "${dir}" -c advice.detachedHead=false -c core.autocrlf=false -c core.eol=lf checkout -f --quiet "${pin}"; then
      restore_site_edits "${dir}" "${site_snapshot}"
      warn "${name}: checkout of ${pin} failed — see git output above"
      FAILED+=("${name} (checkout)")
      return 0
    fi
    restore_site_edits "${dir}" "${site_snapshot}"
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
