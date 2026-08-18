#!/usr/bin/env bash
# fix.sh — run the repairs `bdtools doctor` recommends, for the classes that are
# safe to run without a human watching.
#
#   fix.sh [tool|all]                 show the plan; change nothing (DEFAULT)
#   fix.sh [tool|all] --apply         run the auto-applicable repairs
#   fix.sh [tool|all] --allow-report-only   also act on a report-only tool
#
# WHY THIS EXISTS. The same handful of setup problems recur on every new machine
# (a reference set that was never downloaded, a vendored payload missing from a
# fresh clone), and the remedy was a command a human had to read off a card and
# retype. That is toil, and it does not scale to many sites.
#
# WHAT IS NOT AUTOMATED, AND WHY. Anything that rebuilds or re-solves a conda
# environment is proposed, never run. A rebuild re-solves every dependency, and a
# transaction that dies part-way can leave an env that no longer runs the tool —
# so in a diagnostic lab it is a decision, not a repair (tools.yml's header makes
# the same argument for `updates:` being opt-in per tool). Same for anything that
# deletes (`rm -rf`) or changes which VERSION of the analysis software runs:
# moving a pinned package changes results, which is a revalidation event.
#
# A `pip install` of the WEB LAYER (fastapi/uvicorn/…) into the existing env is
# the deliberate exception: it is additive, touches no conda transaction and no
# analysis package, and exists precisely because a rebuild that died part-way
# tends to leave a healthy analysis env whose only gap is that pip step — the
# state a rebuild is too risky to fix and `bdtools update` refuses to touch on
# report-only tools.
#
# The classifier is an ALLOWLIST — a remedy this script does not recognise is
# proposed, not guessed at. New remedy, new explicit decision.
#
# THE CHECKS MUST BE RIGHT FIRST. This runs on doctor's findings, so it inherits
# doctor's accuracy. That is not theoretical: doctor once graded a stale
# lookalike env and reported four problems a healthy tool did not have, offering
# "rebuilds the vsnp3 env" as the cure — auto-running that would have destroyed a
# freshly validated environment to fix nothing. Hence: rebuilds stay manual, and
# a wrong finding costs a printed line rather than a working tool.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

APPLY=0; ALLOW_REPORT_ONLY=0; TARGET="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)             APPLY=1; shift;;
    --allow-report-only) ALLOW_REPORT_ONLY=1; shift;;
    --dry-run)           APPLY=0; shift;;    # the default; accepted for symmetry
    -h|--help)           sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)                  die "unknown option: $1";;
    *)                   TARGET="$1"; shift;;
  esac
done

# Which remedies may run unattended. Additive operations only — data/payload
# fetches and web-layer pip installs: they cannot break an environment that
# currently works — the worst case is a failed download/install, which leaves
# exactly what was there before.
fix_class() {
  local c="$1"
  case "${c}" in
    *"rm -rf"*)                 echo manual;;   # destructive, never
    *"setup-databases"*)        echo auto;;     # fetches reference data
    *"deploy/install.sh"*)      echo auto;;     # fetches a vendored payload (kSNP4)
    *"conda install"*)          echo manual;;   # a conda transaction on a live env: small, but it CHANGES analysis software — propose it, never unattended. Checked before the pip case because a combined remedy (missing web layer AND a missing analysis package) contains both.
    *" -m pip install "*)       echo auto;;     # adds the web layer into the EXISTING env — no conda re-solve, no analysis version change
    *"update-packages"*)        echo manual;;   # changes the analysis version
    *"bdtools update"*)         echo manual;;   # rebuilds the env
    *"bdtools install"*)        echo manual;;   # builds/rebuilds the env
    *)                          echo manual;;   # unknown remedy -> propose it
  esac
}

DOCTOR_ARGS=()
[[ "${TARGET}" != "all" ]] && DOCTOR_ARGS=("${TARGET}")

report="$("${KT_BIN_DIR}/doctor.sh" --json "${DOCTOR_ARGS[@]+"${DOCTOR_ARGS[@]}"}" 2>/dev/null)" || true
[[ -n "${report}" ]] || die "could not read doctor output"

# One line per (tool, remedy): "<tool>\t<class>\t<policy>\t<labels>\t<command>"
plan="$("${PYBIN}" - "${report}" <<'PY'
import json, sys
try:
    items = json.loads(sys.argv[1] or "[]")
except ValueError:
    items = []
seen = {}
for it in items:
    tool = it.get("tool", "")
    for iss in it.get("issues", []):
        cmd = (iss.get("fix") or "").strip()
        if not cmd:
            continue
        # One remedy can answer several findings (a rebuild covers every missing
        # module); group them so it is proposed once, with everything it fixes.
        seen.setdefault((tool, cmd), []).append(iss.get("label", ""))
for (tool, cmd), labels in seen.items():
    print("\t".join([tool, cmd, "; ".join(labels)]))
PY
)"

if [[ -z "${plan}" ]]; then
  ok "nothing to fix — every checked tool passed."
  exit 0
fi

auto_cmds=(); auto_tools=(); manual_lines=()
while IFS=$'\t' read -r tool cmd labels; do
  [[ -n "${tool}" ]] || continue
  klass="$(fix_class "${cmd}")"
  policy="$(tool_updates_policy "${tool}" 2>/dev/null || echo report)"
  # A report-only tool is one bdtools must not CHANGE. A data fetch changes no
  # software, and a web-layer pip install changes no ANALYSIS software (the
  # versions a diagnostic result depends on are untouched — without the web
  # layer the tool cannot even start to use them). Both stay allowed; anything
  # else needs the same explicit opt-in the update path requires.
  if [[ "${klass}" == "auto" && "${policy}" != "install" && ${ALLOW_REPORT_ONLY} -eq 0 \
        && "${cmd}" != *"setup-databases"* && "${cmd}" != *" -m pip install "* ]]; then
    klass="manual"
    labels="${labels} [report-only tool]"
  fi
  if [[ "${klass}" == "auto" ]]; then
    auto_cmds+=("${cmd}")
    auto_tools+=("${tool}")
  else
    manual_lines+=("${tool}|${labels}|${cmd}")
  fi
done <<< "${plan}"

echo
log "fix plan${APPLY:+ }$( [[ ${APPLY} -eq 1 ]] && echo '(applying)' || echo '(preview — nothing will change)')"
echo

if [[ ${#auto_cmds[@]} -gt 0 ]]; then
  echo "  WILL RUN (safe to automate — data/payload fetches, web-layer pip installs):"
  for i in "${!auto_cmds[@]}"; do
    printf '    %-22s %s\n' "${auto_tools[$i]}" "${auto_cmds[$i]}"
  done
  echo
fi
if [[ ${#manual_lines[@]} -gt 0 ]]; then
  echo "  NEEDS A HUMAN (changes or rebuilds an environment):"
  for line in "${manual_lines[@]}"; do
    IFS='|' read -r t l c <<< "${line}"
    printf '    %-22s %s\n' "${t}" "${l}"
    printf '    %-22s   run: %s\n' "" "${c}"
  done
  echo
fi

if [[ ${APPLY} -eq 0 ]]; then
  if [[ ${#auto_cmds[@]} -gt 0 ]]; then
    info "re-run with --apply to run the safe repairs above."
  else
    info "nothing here is safe to automate; run the commands above by hand."
  fi
  exit 0
fi

if [[ ${#auto_cmds[@]} -eq 0 ]]; then
  warn "nothing to apply — every remaining remedy needs a human (see above)."
  exit 0
fi

failed=()
for i in "${!auto_cmds[@]}"; do
  cmd="${auto_cmds[$i]}"; tool="${auto_tools[$i]}"
  log "${tool}: ${cmd}"
  # Remedies are written relative to the repo root (that is how they are shown
  # on the cards and in doctor), so run them from there.
  if ! ( cd "${REPO_DIR}" && eval "${cmd}" ); then
    warn "${tool}: remedy failed — ${cmd}"
    failed+=("${tool}")
  fi
done

echo
log "re-checking"
"${KT_BIN_DIR}/doctor.sh" "${DOCTOR_ARGS[@]+"${DOCTOR_ARGS[@]}"}" || true

if [[ ${#failed[@]} -gt 0 ]]; then
  warn "remedies that did not finish: ${failed[*]}"
  exit 1
fi
ok "applied ${#auto_cmds[@]} repair(s)."
