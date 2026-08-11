#!/usr/bin/env bash
# check-updates.sh — report (and optionally apply) newer upstream versions.
#
#   check-updates.sh [tool|all]            report only (read-only)
#   check-updates.sh --apply <tool|all>    move the checkout to the newest ref
#                                          and rebuild (bumps the manifest pin)
#   check-updates.sh --apply --force ...   rebuild even if already up to date
#   ... --allow-report-only                also act on a tool tools.yml marks
#                                          report-only (name it explicitly)
#
# --apply only touches tools whose tools.yml entry says `updates: install`.
# Everything else is reported and left exactly as it is — a rebuild re-solves the
# whole env, and a transaction that dies part-way can cost a working tool. See the
# header of tools.yml.
#
# --apply skips any tool already sitting on the target ref with a built env
# (the rebuild would re-solve/re-download for no change). Pass --force to
# rebuild those anyway. A tool whose last build did NOT finish is never skipped,
# with or without --force. Tools not yet checked out are skipped with a note
# (install them with `bdtools install <tool>`) so `--apply all` never aborts.
#
# `--apply all` runs every tool even if one fails: each tool is updated in its
# own subshell, and the failures are listed together at the end (exit 1).
#
# "Newest" = the highest version-sorted git tag on the tool's remote (via
# `git ls-remote`, so it works for any public repo with no auth and even before
# GitHub Releases exist). Tools that have no tags yet track their pinned branch;
# --apply then fast-forwards that branch.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

APPLY=0
FORCE=0
ALLOW_REPORT_ONLY=0
NOT_INSTALLED=()   # tools named in an --apply run that aren't checked out yet
REPORT_ONLY=()     # tools tools.yml says bdtools must not change
FAILED=()          # tools whose update/build did not finish (reported at the end)
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)   APPLY=1; shift;;
    --force)   FORCE=1; shift;;
    # Act on a tool tools.yml marks report-only. One named tool at a time, by
    # hand — never from the dashboard, and never as part of `all`.
    --allow-report-only) ALLOW_REPORT_ONLY=1; shift;;
    --dry-run) DRY_RUN=1; export DRY_RUN; shift;;
    # Without this, --help was taken as a TOOL NAME and reported
    # "manifest: no tool named '--help'".
    -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *)         ARGS+=("$1"); shift;;
  esac
done
TARGET="${ARGS[0]:-all}"

latest_tag() {  # repo-url -> highest version-sorted RELEASE tag, or empty (never aborts)
  # Only vN.N… tags count. `sort -V | tail -1` is a plain string sort at heart:
  # any stray non-release tag that sorts after "v" (say "wip" or "working") would
  # become "latest" — and --apply force-checks-out that ref and rebuilds the env
  # on every machine that installs updates. Release tags are the only refs this
  # suite promises to ship, so filter to them rather than trusting tag hygiene.
  { git ls-remote --tags --refs "$1" 2>/dev/null \
      | awk -F/ '{print $NF}' | grep -E '^v[0-9]+(\.[0-9]+)*([._-].+)?$' \
      | sort -V | tail -1; } || true
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
  local name="$1" dir repo pinned latest target current dirty_path
  # Before anything is fetched, checked out or built. `--apply all` reaches every
  # tool in the manifest, which is exactly how one dashboard button rebuilt a tool
  # nobody had asked to change and left it broken.
  require_updatable "${name}" "${ALLOW_REPORT_ONLY}" update \
    "$([[ "${TARGET}" == "all" ]] && echo 0 || echo 1)" || return 5
  dir="$(tool_dir "$name")"; repo="$(manifest_get "$name" repo)"; pinned="$(manifest_get "$name" version)"
  latest="$(latest_tag "$repo")"
  target="${latest:-$pinned}"   # newest tag, else stay on the pinned branch

  # Not checked out yet: `update` refreshes existing installs — a fresh install
  # is `bdtools install`, which also builds the env (and any bundled DB). Don't
  # abort the whole run for it; skip with a note and collect it for the summary
  # so `update all` completes cleanly and tells the user what still needs installing.
  if [[ ! -d "${dir}/.git" ]]; then
    warn "${name} not installed — skipping (run: bdtools install ${name})"
    return 4     # collected by the caller (each tool runs in its own subshell)
  fi

  # `update` owns only bdtools' per-user managed checkouts. A server source tree
  # may contain reviewed site/licensing commits and must never be subjected to
  # the force checkout below. Server deployments are pinned and validated by
  # install-server.sh instead.
  if [[ "${dir}" != "${BDTOOLS_HOME}/checkouts/${name}" ]]; then
    die "refusing to update external checkout: ${dir}
       'bdtools update' force-refreshes managed personal checkouts only.
       For a server deployment, reconcile the source with the tools.yml pin,
       review any site/licensing commits, then run:
       bin/bdtools install --server ${name} --site-conf <path> --dry-run"
  fi

  # A force checkout is acceptable for files the installer itself regenerates,
  # but never for arbitrary source edits. Refuse before fetch/checkout so a
  # personal experiment remains exactly where the user left it.
  # Shared rule (common.sh:tool_blocking_edits) so this and install-local.sh's
  # ensure_checkout cannot disagree about what counts as a user edit. They did:
  # ensure_checkout required a fully clean tree, so it silently built stale code on
  # any machine whose frontend had ever been rebuilt.
  while IFS= read -r dirty_path; do
    [[ -n "${dirty_path}" ]] || continue
    die "${name} has local source changes: ${dirty_path}
       Refusing to overwrite them during update. Commit/stash the change, or
       use a separate developer checkout, then rerun."
  done < <(tool_blocking_edits "${dir}")

  # Fast path: already on the target tag AND the env is built. A rebuild here
  # would re-solve and re-download for zero change — this is exactly what made
  # `update all` grind through a fresh conda solve per tool even when nothing
  # was newer. Skip unless --force. Only for a concrete tag target; branch-
  # tracked tools (latest empty) always refresh in case the branch moved.
  #
  # "The env is built" cannot mean only "a python exists in it": this function
  # moves the checkout to the target ref BEFORE building, so a build that failed
  # half-way leaves the tool at the target ref with the PREVIOUS env still in
  # place, python and all. That state used to be reported as "already up to
  # date", which is how a failed conda update turned into new code silently
  # running an old env. A recorded build failure for this exact ref disqualifies
  # the fast path (see common.sh, build state).
  current="$(git -C "$dir" describe --tags --always 2>/dev/null || echo '')"
  if [[ ${FORCE} -eq 0 && -n "${latest}" && "${current}" == "${target}" ]] \
     && "${KT_BIN_DIR}/install-local.sh" --print-python "$name" >/dev/null 2>&1; then
    if build_failed_for "${name}" "${current}"; then
      warn "${name}: at ${target}, but its last build did not finish — rebuilding instead of skipping."
    else
      ok "${name} already at ${target} with a built env — skipping (use --force to rebuild)"
      return 0
    fi
  fi

  log "updating ${name} -> ${target}"
  run git -C "$dir" fetch --tags --force --depth 1 origin "${target}"
  # Site-localized OOD card config (ood/apps/**, e.g. a submit.yml.erb carrying
  # this cluster's name/account) is a deployment's own deliberate edit — carried
  # across the force checkout below, never a reason to refuse the update and
  # never clobbered by it (see common.sh: tool_blocking_edits exemption).
  local site_snapshot; site_snapshot="$(snapshot_site_edits "${dir}")"
  [[ -n "${site_snapshot}" ]] && log "preserving site-localized OOD card config across the update"
  # Force past locally-modified build artifacts. A managed checkout's working
  # tree gets dirtied every install because the frontend is rebuilt in place
  # (frontend/dist + package-lock are tracked, but regenerated with whatever
  # Node/vite is on the box, so the hashes differ from what's committed). A
  # plain `git checkout <tag>` aborts on that; -f is safe here because the very
  # next step rebuilds env + frontend. Fall back to FETCH_HEAD when the tag
  # didn't materialize as a local ref (shallow tag fetches land there).
  run git -C "$dir" checkout -f -q "${target}" \
    || run git -C "$dir" checkout -f -q FETCH_HEAD
  restore_site_edits "${dir}" "${site_snapshot}"
  # The pin moves BEFORE the build, and has to: install-local.sh's
  # ensure_checkout resolves the checkout against the manifest pin, so building
  # with the pin still on the old tag would check the code back out to the old
  # tag and build that instead. The cost is that a failed build below leaves the
  # manifest advertising a version whose env was never finished — which is why
  # install-local.sh records the failure and the fast path above honors it.
  if [[ -n "$latest" && "$latest" != "$pinned" ]]; then
    log "bumping manifest pin: ${name} ${pinned} -> ${latest}"
    run manifest_set "$name" version "$latest"
  fi
  log "rebuilding ${name}"
  local a=(--rebuild) rc=0
  [[ ${DRY_RUN} -eq 1 ]] && a+=(--dry-run)
  run "${KT_BIN_DIR}/install-local.sh" --build-only "${a[@]}" "$name" || rc=$?
  # 3 = install-local's "no local-build path" sentinel: the code was updated, the
  # env belongs to the tool's OOD installer. Not a failure.
  [[ ${rc} -eq 3 ]] && { warn "${name}: code updated; its env is provisioned by the OOD installer, not locally."; return 3; }
  [[ ${rc} -eq 0 ]] || return ${rc}
  ok "${name} updated"
}

if [[ ${APPLY} -eq 1 ]]; then
  [[ "${TARGET}" != "all" || ${#ARGS[@]} -gt 0 ]] || die "name a tool or 'all'"
  # One tool's failure must not abandon the rest. This ran under `set -e` with
  # apply_one called directly, so the first tool that failed to build — or hit a
  # `die` for a dirty checkout — ended the whole run, and every tool after it in
  # tools.yml was left un-updated with nothing said about it. A macOS conda
  # post-link error on one tool silently cost the user every update behind it.
  # Each tool now runs in its own subshell (so even `die` only ends that tool),
  # and the outcomes are summarized at the end.
  rc=0
  while read -r n; do
    [[ -n "$n" ]] || continue
    rc=0; ( apply_one "$n" ) || rc=$?
    case "${rc}" in
      0|3) ;;                            # updated, or code-only (no local env)
      4)   NOT_INSTALLED+=("$n");;
      5)   REPORT_ONLY+=("$n");;         # not ours to change; not a failure
      *)   FAILED+=("$n"); warn "${n}: update failed (exit ${rc}) — continuing with the remaining tools.";;
    esac
  done < <(targets)
  if [[ ${#REPORT_ONLY[@]} -gt 0 ]]; then
    echo
    ok "left alone (report-only in tools.yml): ${REPORT_ONLY[*]}"
    info "  Their versions are still reported; nothing about them was changed."
  fi
  if [[ ${#NOT_INSTALLED[@]} -gt 0 ]]; then
    echo
    warn "not installed (skipped): ${NOT_INSTALLED[*]}"
    info "  install them with:  bdtools install ${NOT_INSTALLED[*]}"
  fi
  if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo
    warn "FAILED to update: ${FAILED[*]}"
    info "  The real error is in the log above. These are recorded as unfinished, so"
    info "  re-running picks them up (no --force needed):"
    info "      bin/bdtools update ${FAILED[*]}"
    info "  To see what an env is missing:  bin/bdtools doctor ${FAILED[*]}"
    exit 1
  fi
else
  while read -r n; do [[ -n "$n" ]] && report_one "$n"; done < <(targets)
  # The lines above compare each GUI CHECKOUT against its newest release tag. That
  # says nothing about the analysis packages inside the envs — a new bioconda vsnp3
  # does not move a vsnp_gui tag — so report those too, from the same command.
  # Deliberately printed without a "pinned=" field: the dashboard's line parser
  # keys on that, so these lines stay out of the tool-update list and are picked up
  # via packages.py instead.
  pkg_out="$("${PYBIN}" "${KT_BIN_DIR}/lib/packages.py" \
             $([[ "${TARGET}" != "all" ]] && echo "${TARGET}") 2>/dev/null || true)"
  if [[ -n "${pkg_out}" ]]; then
    echo
    echo "Analysis packages (conda; what actually produced your results):"
    printf '%s\n' "${pkg_out}"
    if printf '%s' "${pkg_out}" | grep -q '↑'; then
      echo
      info "Update the packages for one tool (re-applies local patches, bumps the pin):"
      info "    bin/bdtools update-packages <tool>"
    fi
  fi
fi
