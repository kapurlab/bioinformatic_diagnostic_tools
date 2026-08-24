#!/usr/bin/env bash
# pull.sh — update the umbrella checkout without losing this site's card edits.
#
#   pull.sh [--dry-run]
#
# WHY THIS EXISTS. `bdtools update` moves the TOOL checkouts; the umbrella
# itself is updated with plain `git pull`, and that is where a deployment's own
# card edits collide with every release.
#
# ood/apps/** is the one tracked area a site is EXPECTED to edit: a cluster
# name, an account, the CPU/memory/walltime floors a scheduler or a local policy
# requires. The suite already treats that as the install working as designed —
# common.sh:tool_blocking_edits exempts ood/apps/* so it never blocks a tool
# update, and snapshot_site_edits carries it across the force checkout. The
# umbrella got none of that, because nothing here runs git on its behalf. So a
# site that had set, say, a 16 GB floor on the dashboard card met this on every
# single pull:
#
#     error: Your local changes to the following files would be overwritten by
#     merge:  ood/apps/bdtools_dashboard/form.yml
#
# and the obvious ways out are both wrong: `git checkout --` throws the site's
# policy away, and `git pull -f` is not a thing that means what people hope.
#
# WHAT THIS DOES. Stash the card edits, pull, put them back. Not
# snapshot/restore — that is right for a tool, where the updater does a
# `git checkout -f` to a tag and there is nothing to merge, but here git CAN
# merge: a site changing `min:` and a release changing `help:` two lines away
# is a clean three-way merge, and copying the old file back over the new one
# would silently drop the release's change.
#
# WHAT IT REFUSES. Any dirty tracked file OUTSIDE ood/apps/. Those are edits to
# suite code — someone's experiment, or a hand-patch that a pull is about to
# make invisible. Same rule and same reasoning as the tool updaters; naming them
# and stopping is the only safe answer.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *)         die "unknown option: $1";;
  esac
done

cd "${REPO_DIR}"
[[ -d .git ]] || die "not a git checkout: ${REPO_DIR}
       This command updates the umbrella repository. A tarball/copy install has
       nothing to pull — re-download it, or clone it with git."

# An interrupted merge comes FIRST, before anything else is judged. This is the
# state a person is most likely to be in when they reach for this command — they
# tried the manual stash/pull/pop, it conflicted, and now they are looking for
# something that will sort it out. Without this check the unmerged file looks
# like an ordinary card edit (it is under ood/apps/, so it is exempt below),
# `git stash push` refuses it with "needs merge", and the script reported
# "could not stash the card edits" — true, and useless.
unmerged="$(git diff --name-only --diff-filter=U 2>/dev/null || true)"
if [[ -n "${unmerged}" ]] || [[ -f "$(git rev-parse --git-path MERGE_HEAD 2>/dev/null)" ]]; then
  msg="this checkout is in the middle of a merge that is not finished."$'\n'
  if [[ -n "${unmerged}" ]]; then
    msg+="       Files still holding conflict markers:"$'\n'
    while IFS= read -r p; do [[ -n "${p}" ]] && msg+="         ${p}"$'\n'; done <<< "${unmerged}"
  fi
  msg+="       Finish it before pulling again. In an OOD card, that usually means"$'\n'
  msg+="       keeping THIS SITE's numbers and the RELEASE's wording — neither half of"$'\n'
  msg+="       the conflict is complete on its own, so \`git checkout --ours/--theirs\`"$'\n'
  msg+="       will silently drop one of them. Edit the file, delete the <<<< ==== >>>>"$'\n'
  msg+="       lines, then:"$'\n'
  msg+="         git add <file>"$'\n'
  if git stash list 2>/dev/null | grep -q .; then
    msg+="         git stash drop        # your pre-pull version, no longer needed"$'\n'
    msg+="       See that version first with:  git stash show -p"$'\n'
  fi
  die "${msg%$'\n'}"
fi

# Refuse on anything that is NOT site-localized card config, before touching
# the tree. Named individually: "you have local changes" is not actionable.
blocking=""
while IFS= read -r p; do
  [[ -n "${p}" ]] || continue
  blocking+="       ${p}"$'\n'
done < <(tool_blocking_edits "${REPO_DIR}")
if [[ -n "${blocking}" ]]; then
  die "these tracked files have local changes that are not site card config:
${blocking}       A pull would overwrite them. Commit or stash them yourself, then
       re-run. (ood/apps/** is carried across automatically; nothing else is.)"
fi

# The site-localized card edits, if any.
edits=""
while IFS= read -r p; do
  [[ -n "${p}" ]] || continue
  edits+="${p}"$'\n'
done < <(tool_site_edits "${REPO_DIR}")

stashed=0
if [[ -n "${edits}" ]]; then
  log "carrying this site's card edits across the pull"
  while IFS= read -r p; do [[ -n "${p}" ]] && info "${p}"; done <<< "${edits}"
  if [[ ${DRY_RUN:-0} -eq 0 ]]; then
    # -- <paths> so only the card config is stashed; nothing else is touched.
    # shellcheck disable=SC2086
    git stash push -q -- $(printf '%s ' ${edits}) || die "could not stash the card edits"
    stashed=1
  else
    echo "  [dry-run] git stash push -- <the files above>"
  fi
fi

# Undo the stash on ANY failure from here on, including a Ctrl-C. Without this a
# failed pull would leave the site's edits parked in a stash entry nobody knows
# about, and the card silently back to the shipped defaults.
restore_conflicted=0
restore() {
  [[ ${stashed} -eq 1 ]] || return 0
  stashed=0
  if git stash pop >/dev/null 2>&1; then
    ok "card edits restored"
    return 0
  fi
  # A real conflict: this release changed the SAME lines the site did. Nothing
  # is lost — the stash entry is still there and the conflict markers are in the
  # tree — but this is a human decision (whose value wins), so say so and do not
  # let the caller read a success line afterwards.
  restore_conflicted=1
  warn "this release changed the same lines your site did, so your card edits"
  warn "did not re-apply cleanly. NOTHING IS LOST. Conflicted:"
  while IFS= read -r f; do [[ -n "${f}" ]] && warn "    ${f}"; done \
    < <(git diff --name-only --diff-filter=U 2>/dev/null)
  warn "  Keep BOTH halves: this site's numbers (min/max/value/cluster/account)"
  warn "  and the release's wording. Neither side is complete on its own, so"
  warn "  git checkout --ours / --theirs will quietly drop one of them."
  warn "  Your pre-pull version:   git stash show -p"
  warn "  Then, once the <<<< ==== >>>> lines are gone:"
  warn "      git add <file> && git stash drop"
}
trap restore EXIT INT TERM

# Fetch and decide SEPARATELY from merging, so a failure can be named instead of
# guessed at. `git pull --ff-only` exits non-zero for several unrelated reasons
# — no network, a dirty file, real divergence — and an earlier draft of this
# script reported every one of them as "this checkout has commits origin does
# not". That is the cry-wolf failure this suite has been bitten by before: a
# message confident about a cause it never checked sends someone to rebase a
# branch that was never diverged.
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
[[ -n "${upstream}" ]] || die "this branch tracks nothing, so there is nothing to pull.
       Set an upstream:  git branch --set-upstream-to=origin/main"

log "git fetch"
if [[ ${DRY_RUN:-0} -eq 1 ]]; then
  echo "  [dry-run] git fetch --tags && git merge --ff-only ${upstream}"
else
  git fetch --tags --quiet origin \
    || die "could not reach origin. Your card edits are untouched.
       On a compute node with no outbound network, pull from a login node instead."

  if git merge-base --is-ancestor HEAD "${upstream}" 2>/dev/null; then
    if git rev-parse HEAD >/dev/null && [[ "$(git rev-parse HEAD)" == "$(git rev-parse "${upstream}")" ]]; then
      ok "already up to date"
    else
      # A fast-forward, which is what a site checkout should always be. The
      # stashed card edits merge back on top afterwards.
      git merge --ff-only --quiet "${upstream}" \
        || die "fast-forward failed unexpectedly; your card edits are restored below."
      ok "fast-forwarded to ${upstream}"
    fi
  else
    ahead="$(git rev-list --count "${upstream}..HEAD" 2>/dev/null || echo '?')"
    die "this checkout has ${ahead} commit(s) ${upstream} does not, so a pull is not a
       fast-forward. Nothing was merged and your card edits are restored below.
       See what they are:
         git log --oneline ${upstream}..HEAD
       then rebase or merge deliberately."
  fi
fi

trap - EXIT INT TERM
restore
at="$(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD)"
if [[ ${restore_conflicted} -eq 1 ]]; then
  warn "umbrella is at ${at}, but the working tree needs your decision first (above)."
  exit 1
fi
ok "umbrella now at ${at}"
info "tools move separately:  bin/bdtools check-updates"
