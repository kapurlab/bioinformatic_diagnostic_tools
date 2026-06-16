#!/usr/bin/env bash
# make-releases.sh — create a GitHub Release for each tool's manifest-pinned tag.
#
# A maintainer one-shot. `bdtools check-updates` works off plain git tags and
# does NOT need Releases — these just give each version a nice changelog page on
# GitHub. Requires the `gh` CLI authenticated once: `gh auth login`.
#
#   make-releases.sh [--dry-run] [tool ...]      (default: all manifest tools)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

DRY_RUN="${DRY_RUN:-0}"; ONLY=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) ONLY+=("$1"); shift;;
  esac
done

command -v gh >/dev/null 2>&1 || die "gh CLI not found — install it, then 'gh auth login'."
if [[ ${DRY_RUN} -eq 0 ]]; then
  gh auth status >/dev/null 2>&1 || die "not authenticated — run 'gh auth login' first."
fi

repo_slug() { sed -E 's#^https?://github.com/##; s#\.git$##' <<<"$1"; }

targets() { if [[ ${#ONLY[@]} -gt 0 ]]; then printf '%s\n' "${ONLY[@]}"; else manifest_names; fi; }

while read -r name; do
  [[ -n "$name" ]] || continue
  tag="$(manifest_get "$name" version)"
  slug="$(repo_slug "$(manifest_get "$name" repo)")"
  if [[ "$tag" != v* ]]; then warn "${name}: pinned to non-tag '${tag}' — skipping"; continue; fi
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "  [dry-run] gh release create ${tag} --repo ${slug} --title '${name} ${tag}' --generate-notes"
    continue
  fi
  if gh release view "${tag}" --repo "${slug}" >/dev/null 2>&1; then
    ok "${name} ${tag} already released"
  else
    if gh release create "${tag}" --repo "${slug}" --title "${name} ${tag}" --generate-notes; then
      ok "released ${name} ${tag}"
    else
      warn "failed to release ${name} ${tag} (tag pushed? repo access?)"
    fi
  fi
done < <(targets)
