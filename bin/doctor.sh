#!/usr/bin/env bash
# doctor.sh — check installed tools for runtime readiness and tell the user, in
# plain language, exactly what to run to fix anything that's wrong.
#
# Verifies each tool against its requirements spec (bin/lib/requirements.py):
# the env is built, the python modules import, the external programs are on
# PATH, and the reference databases exist. Catches the failure modes that
# otherwise only surface as a traceback mid-analysis (a missing dependency, an
# unset/empty database path).
#
#   doctor.sh [tool ...]      (default: every installed tool)
#   doctor.sh --scope env     (skip database checks — used by the build self-check)
#   doctor.sh --deep          also LAUNCH every declared program to prove it can
#                             start — catches the binary that resolves and exists
#                             but dies on exec (wrong slice, missing loader lib).
#                             Seconds per tool, so on demand rather than every
#                             run; `bdtools diagnose` always uses it.
set -uo pipefail   # NOTE: not -e; a failing tool check must not abort the sweep
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SCOPE="all"; JSON=0; DEEP=0; ONLY=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope) SCOPE="$2"; shift 2;;
    --json)  JSON=1; shift;;          # machine-readable array (used by the dashboard)
    --deep)  DEEP=1; shift;;          # forwarded to check.py (see header)
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) ONLY+=("$1"); shift;;
  esac
done
# check.py already accepts --deep; doctor just failed to offer it, so the only
# way to launch-test the programs was `bdtools diagnose` (which always passes
# it). Kept as an array so the no-flag case adds NOTHING to the argv.
deep_args=(); [[ ${DEEP} -eq 1 ]] && deep_args=(--deep)

targets() { if [[ ${#ONLY[@]} -gt 0 ]]; then printf '%s\n' "${ONLY[@]}"; else manifest_names; fi; }

# Name the tool the user probably meant. Every tool has BOTH a name and a conda
# env name and they are not the same string (kraken_id_parse_gui builds the env
# `kraken_id_parse`), so typing the one printed by `conda env list` — or by this
# tool's own error messages, which name envs constantly — got "unknown tool" and
# nothing else. Matches the manifest's `env:` values first, then any name that
# contains, or is contained by, what was asked for.
suggest_tool() {
  local want="$1" n e
  while read -r n; do
    [[ -n "${n}" ]] || continue
    e="$(manifest_get "${n}" env 2>/dev/null || true)"
    [[ -n "${e}" && "${e}" == "${want}" ]] && { printf '%s' "${n}"; return 0; }
  done < <(manifest_names)
  while read -r n; do
    [[ -n "${n}" ]] || continue
    [[ "${n}" == *"${want}"* || "${want}" == *"${n}"* ]] && { printf '%s' "${n}"; return 0; }
  done < <(manifest_names)
  return 1
}

issues=0; checked=0; unknown=0; json_items=()
while read -r name; do
  [[ -n "$name" ]] || continue
  if ! manifest_has "$name"; then
    if [[ ${JSON} -eq 0 ]]; then
      s="$(suggest_tool "$name" || true)"
      if [[ -n "${s}" ]]; then
        warn "unknown tool: $name — did you mean ${s}?  (bin/bdtools doctor ${s})"
      else
        warn "unknown tool: $name (known: $(manifest_names | tr '\n' ' '))"
      fi
    fi
    unknown=$((unknown + 1))
    continue
  fi
  dir="$(tool_dir "$name")"
  if [[ ! -d "${dir}/.git" && ! -x "${dir}/env/bin/python" ]]; then
    # Not installed here — silent unless the user asked for this tool by name.
    [[ ${JSON} -eq 0 && ${#ONLY[@]} -gt 0 ]] && echo "${name}: not installed (bin/bdtools install ${name})"
    continue
  fi
  checked=$((checked + 1))
  py="$(tool_env_python "${dir}" "$(manifest_get "$name" env)" "$name")"

  # Two findings check.py cannot make, because neither is visible from inside the
  # env's python. Computed for BOTH output modes: they used to exist only on the
  # human report, so the dashboard card — where people actually look — could not
  # show them. A card that says "missing modules" while saying nothing about the
  # env being half one architecture is describing the symptom and hiding the cause.
  extra_labels=(); extra_fixes=()
  # 1. Packages built for another architecture, linked in by a solve that ran for
  #    the wrong platform. They fail only when the analysis calls them.
  envdir="${py%/bin/python}"
  foreign="$([[ -n "${py}" ]] && env_foreign_subdirs "${envdir}" || true)"
  if [[ -n "${foreign}" ]]; then
    extra_labels+=("mixed-architecture env: $(printf '%s' "${foreign}" | tr '\n' ';') package(s) are not $(env_conda_subdir "${envdir}") — they cannot run here")
    extra_fixes+=("${KT_BIN_DIR}/bdtools install ${name} --fresh")
  fi
  # 2. The WRONG env: if the last build didn't finish, this code is newer than the
  #    environment running it (conda rolls a failed transaction back, so the
  #    previous env survives intact — and silently).
  ref="$(git -C "${dir}" describe --tags --always 2>/dev/null || echo '')"
  if build_failed_for "$name" "${ref}"; then
    extra_labels+=("the last environment build did not finish — this checkout (${ref:-?}) may be running the previous env")
    extra_fixes+=("${KT_BIN_DIR}/bdtools install ${name} --fresh")
  fi

  if [[ ${JSON} -eq 1 ]]; then
    item="$("${PYBIN}" "${KT_BIN_DIR}/lib/check.py" --tool "$name" --dir "${dir}" \
              --python "${py}" --scope "${SCOPE}" ${deep_args[@]+"${deep_args[@]}"} --json)" || issues=$((issues + 1))
    if [[ -n "${item}" && ${#extra_labels[@]} -gt 0 ]]; then
      # Merge them into the tool's object rather than appending a second object:
      # the dashboard groups by tool, and the architecture of an env belongs to
      # the same card as everything else about it.
      item="$("${PYBIN}" - "${item}" "${extra_labels[@]}" "--" "${extra_fixes[@]}" <<'MERGE'
import json, sys
item = json.loads(sys.argv[1])
rest = sys.argv[2:]
cut = rest.index("--")
for label, fix in zip(rest[:cut], rest[cut + 1:]):
    item.setdefault("issues", []).append({"label": label, "fix": fix})
item["status"] = "issues"
item["ok"] = False
print(json.dumps(item))
MERGE
)"
      issues=$((issues + 1))
    fi
    [[ -n "${item}" ]] && json_items+=("${item}")
  else
    "${PYBIN}" "${KT_BIN_DIR}/lib/check.py" --tool "$name" --dir "${dir}" \
            --python "${py}" --scope "${SCOPE}" ${deep_args[@]+"${deep_args[@]}"} || issues=$((issues + 1))
    for i in "${!extra_labels[@]}"; do
      warn "${name}: ${extra_labels[$i]}"
      info "  FIX: ${extra_fixes[$i]}   (an update cannot repair either of these; --fresh rebuilds the env)"
      issues=$((issues + 1))
    done
    echo
  fi
done < <(targets)

if [[ ${JSON} -eq 1 ]]; then
  ( IFS=,; echo "[${json_items[*]-}]" )
  exit $([[ ${issues} -gt 0 ]] && echo 1 || echo 0)
fi
if [[ ${checked} -eq 0 ]]; then
  if [[ ${unknown} -gt 0 ]]; then
    # Nothing was checked because nothing that was ASKED FOR exists. Saying "no
    # installed tools found" here contradicts the report directly above it and
    # sends someone to install a tool they already have.
    warn "nothing checked: no tool by that name (see the suggestion above)"
    exit 1
  fi
  warn "no installed tools found to check (install one: bin/bdtools install <tool>)"
  exit 0
fi
if [[ ${issues} -gt 0 ]]; then
  warn "${issues} tool(s) need attention — run the suggested fix above, then re-run: bin/bdtools doctor"
  exit 1
fi
ok "all ${checked} installed tool(s) ready."
