#!/usr/bin/env bash
# diagnose.sh — one command that captures EVERYTHING about how a tool resolves
# and executes, into one pasteable file.
#
#   diagnose.sh [tool ...]          (default: every installed tool)
#
# WHY THIS EXISTS. A tool that will not run is diagnosed by a conversation:
# someone runs a command, pastes the output, is asked for another. That loop
# cost four wrong fixes on one macOS failure in August 2026, because each round
# answered one question and raised two. Every fix targeted a mechanism the next
# round disproved — PATH order, a missing interpreter, an environment variable —
# while the actual cause (an in-env perl whose baked-in library root pointed at
# a DIFFERENT conda env) needed three facts that were never on screen together.
#
# So this collects all of them at once, and it collects them the way the tool
# does — by ASKING the same resolvers the launcher asks, and by RUNNING the
# interpreters rather than looking at where their files sit. What a file is
# named, where it lives, and what it does when executed are three different
# questions, and this incident turned on the gap between them.
#
# Read-only: it runs `--version`/`--help`-class probes and reads files. It
# changes nothing.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

OUT="${BDTOOLS_HOME}/diagnose-$(date +%Y%m%d-%H%M%S).txt"
TOOLS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--out) OUT="$2"; shift 2;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*) die "unknown option: $1";;
    *) TOOLS+=("$1"); shift;;
  esac
done
[[ ${#TOOLS[@]} -gt 0 ]] || while IFS= read -r _n; do
  [[ -d "$(tool_dir "${_n}")" ]] && TOOLS+=("${_n}")
done < <(manifest_names)

mkdir -p "$(dirname "${OUT}")" 2>/dev/null || true
exec > >(tee "${OUT}") 2>&1

hr()  { printf '\n%s\n' "================================================================"; }
sec() { hr; printf '## %s\n' "$*"; printf '%s\n' "----------------------------------------------------------------"; }

# describe_file PATH — the three facts that turned out to matter, per file:
# where it really is (symlinks resolved), whether two paths are ONE file
# (inode — a hardlink shared with another env is invisible to `ls` alone), and
# what architecture it actually is on disk (records can disagree with reality).
describe_file() {
  local p="${1:-}"
  [[ -n "${p}" && -e "${p}" ]] || { echo "    (absent)"; return; }
  local real; real="$(cd "$(dirname "${p}")" 2>/dev/null && echo "$(pwd -P)/$(basename "${p}")")"
  printf '    path:  %s\n' "${p}"
  [[ "${real}" != "${p}" ]] && printf '    real:  %s\n' "${real}"
  printf '    inode: %s\n' "$(ls -li "${p}" 2>/dev/null | awk '{print $1" (links "$3")"}')"
  printf '    type:  %s\n' "$(file -b "${p}" 2>/dev/null | head -1)"
  local head1; head1="$(head -c 2 "${p}" 2>/dev/null)"
  [[ "${head1}" == "#!" ]] && printf '    shebang: %s\n' "$(head -1 "${p}" 2>/dev/null)"
}

sec "bdtools diagnose — $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "report:        ${OUT}"
echo "suite repo:    ${REPO_DIR}"
echo "suite HEAD:    $(git -C "${REPO_DIR}" describe --tags --always --dirty 2>/dev/null || echo '?')"
echo "suite branch:  $(git -C "${REPO_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
echo "manifest:      ${BDTOOLS_MANIFEST:-${REPO_DIR}/tools.yml}"
echo "BDTOOLS_HOME:  ${BDTOOLS_HOME}"
echo "tools:         ${TOOLS[*]:-none}"

sec "HOST"
echo "uname:      $(uname -a)"
echo "arch:       $(uname -m)   host conda subdir: $(host_conda_subdir)"
[[ "$(uname -s)" == "Darwin" ]] && {
  echo "macOS:      $(sw_vers -productVersion 2>/dev/null)"
  if /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
    echo "Rosetta 2:  available (x86_64 binaries can run)"
  else
    echo "Rosetta 2:  NOT available (x86_64 binaries cannot run here)"
  fi
}

sec "SHELL ENVIRONMENT (what a tool inherits)"
# Interpreter-path variables first: these override an interpreter's own library
# root, so a stale one silently redirects every script the tool runs.
for v in PERL5LIB PERLLIB PYTHONPATH PYTHONHOME RUBYLIB CONDA_PREFIX CONDA_EXE \
         CONDA_DEFAULT_ENV CONDA_SUBDIR BDTOOLS_TOOLSDIR BDTOOLS_MANIFEST; do
  printf '%-20s [%s]\n' "${v}" "$(eval "printf '%s' \"\${${v}:-unset}\"")"
done
echo "PATH (in order):"
printf '%s\n' "${PATH}" | tr ':' '\n' | nl -ba | sed 's/^/  /'

sec "CONDA"
echo "detect_conda:   $(detect_conda 2>/dev/null || echo '(none found)')"
echo "conda_base_dir: $(conda_base_dir 2>/dev/null || echo '(none)')"
for b in "${HOME}/miniforge3" "${HOME}/miniconda3" "${HOME}/mambaforge" \
         "${HOME}/anaconda3" "/opt/miniforge3" "/opt/miniconda3"; do
  [[ -d "${b}" ]] && echo "  base present: ${b}"
done

for TOOL in "${TOOLS[@]}"; do
  DIR="$(tool_dir "${TOOL}")"
  ENV_NAME="$(manifest_get "${TOOL}" env 2>/dev/null || true)"
  sec "TOOL: ${TOOL}"
  echo "checkout:   ${DIR}"
  echo "git:        $(git -C "${DIR}" describe --tags --always --dirty 2>/dev/null || echo '?') on $(git -C "${DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  echo "pinned:     $(manifest_get "${TOOL}" version 2>/dev/null)"
  echo "env name:   ${ENV_NAME}"

  # THE RESOLVERS. Three pieces of code answer "which env runs this tool", and
  # this incident was a disagreement between them. Print all three side by side
  # rather than trusting any one of them.
  echo
  echo "-- which env each resolver picks (they must agree) --"
  local_env="$([[ -x "${DIR}/env/bin/python" ]] && echo "${DIR}/env" || echo '')"
  printf '  %-26s %s\n' "<checkout>/env:" "${local_env:-(no python there)}"
  printf '  %-26s %s\n' "tool_env_prefix (doctor):" "$(tool_env_prefix "${TOOL}" 2>/dev/null || echo '(none)')"
  tl="$("${PYBIN}" - "${KT_BIN_DIR}/lib" "${TOOL}" <<'PY' 2>/dev/null || true
import json, sys
sys.path.insert(0, sys.argv[1])
try:
    import tool_launch
    p = tool_launch.resolve(sys.argv[2], 0)
    print(json.dumps({"env_dir": p.get("env_dir", ""),
                      "python": (p.get("argv") or [""])[0],
                      "PATH_PREPEND": p.get("env_overrides", {}).get("PATH_PREPEND", "")}))
except Exception as e:
    print(json.dumps({"error": "%s: %s" % (type(e).__name__, e)}))
PY
)"
  printf '  %-26s %s\n' "tool_launch (launcher):" "${tl:-(failed)}"

  # Every env that could be mistaken for this tool's, graded the same way. A
  # second env with the manifest's name is what every resolver disagreement in
  # this incident had in common.
  echo
  echo "-- candidate environments --"
  cands=("${DIR}/env")
  for b in "${HOME}/miniforge3" "${HOME}/miniconda3" "${HOME}/mambaforge" "${HOME}/anaconda3"; do
    [[ -n "${ENV_NAME}" && -d "${b}/envs/${ENV_NAME}" ]] && cands+=("${b}/envs/${ENV_NAME}")
  done
  for e in "${cands[@]}"; do
    [[ -d "${e}" ]] || continue
    echo "  ENV ${e}"
    printf '    records say: %s' "$(env_conda_subdir "${e}")"
    fo="$(env_foreign_subdirs "${e}")"
    [[ -n "${fo}" ]] && printf '   FOREIGN RECORDS: %s' "$(printf '%s' "${fo}" | tr '\n' ';')"
    echo
    printf '    python:\n'; describe_file "${e}/bin/python"
    printf '    perl:\n';   describe_file "${e}/bin/perl"
    # What the interpreters say about THEMSELVES. The whole point: a file in the
    # right place can still be another env's interpreter.
    if [[ -x "${e}/bin/perl" ]]; then
      echo "    perl reports \$^X: $("${e}/bin/perl" -e 'print $^X' 2>&1 | head -1)"
      echo "    perl reports @INC[0]: $("${e}/bin/perl" -e 'print((grep { $_ ne "." } @INC)[0])' 2>&1 | head -1)"
    fi
    if [[ -x "${e}/bin/python" ]]; then
      echo "    python sys.prefix: $("${e}/bin/python" -c 'import sys; print(sys.prefix)' 2>&1 | head -1)"
      echo "    python platform:   $("${e}/bin/python" -c 'import platform; print(platform.machine())' 2>&1 | head -1)"
    fi
  done

  # Every declared program: what PATH finds, what it really is, and — for a
  # script — which interpreter will run it and what THAT loads.
  echo
  echo "-- declared programs, as PATH resolves them right now --"
  bins="$("${PYBIN}" - "${KT_BIN_DIR}/lib" "${TOOL}" <<'PY' 2>/dev/null || true
import sys
sys.path.insert(0, sys.argv[1])
import requirements
print(" ".join(requirements.for_tool(sys.argv[2]).get("binaries", [])))
PY
)"
  envbin="$(tool_env_prefix "${TOOL}" 2>/dev/null)/bin"
  for b in ${bins}; do
    p="$(PATH="${envbin}:${PATH}" command -v "${b}" 2>/dev/null || true)"
    echo "  ${b}:"
    describe_file "${p}"
    if [[ -n "${p}" && "$(head -c 2 "${p}" 2>/dev/null)" == "#!" ]]; then
      interp="$(head -1 "${p}" | sed 's/^#! *//' | awk '{ if ($1 ~ /env$/) print $2; else print $1 }')"
      ipath="$(PATH="${envbin}:${PATH}" command -v "${interp}" 2>/dev/null || echo "${interp}")"
      echo "    -> interpreter '${interp}' resolves to: ${ipath:-(not found)}"
      [[ -x "${ipath}" ]] && describe_file "${ipath}" | sed 's/^/  /'
      case "$(basename "${interp}")" in
        perl) [[ -x "${ipath}" ]] && echo "      that perl loads from: $("${ipath}" -e 'print((grep { $_ ne "." } @INC)[0])' 2>&1 | head -1)";;
        python|python3) [[ -x "${ipath}" ]] && echo "      that python prefix:  $("${ipath}" -c 'import sys; print(sys.prefix)' 2>&1 | head -1)";;
      esac
    fi
  done

  echo
  echo "-- last launch recorded for this tool --"
  grep '^cd ' "${BDTOOLS_HOME}/dashboard-logs/${TOOL}.log" 2>/dev/null | tail -1 \
    || echo "  (no launch line in ${BDTOOLS_HOME}/dashboard-logs/${TOOL}.log)"
  echo
  echo "-- build state --"
  bs="${BDTOOLS_HOME}/state/${TOOL}.build-failed"
  [[ -f "${bs}" ]] && sed 's/^/  /' "${bs}" || echo "  (no recorded build failure)"
  bl="${BDTOOLS_HOME}/state/build-logs/${TOOL}.log"
  [[ -f "${bl}" ]] && echo "  build log: ${bl} ($(wc -l < "${bl}" | tr -d ' ') lines)"
done

sec "RUNNING TOOL BACKENDS (their real PATH)"
pids="$(pgrep -f 'uvicorn app.main:app' 2>/dev/null || true)"
if [[ -z "${pids}" ]]; then
  echo "  (no tool backend running — start the tool, reproduce the failure, then re-run this)"
else
  for pid in ${pids}; do
    echo "  pid ${pid}: $(ps -o command= -p "${pid}" 2>/dev/null | cut -c1-160)"
    ps eww -o command= -p "${pid}" 2>/dev/null | tr ' ' '\n' | grep -E '^(PATH|PERL5LIB|PYTHONPATH)=' \
      | sed 's/^/    /' | while IFS= read -r l; do
          printf '%s\n' "${l}" | sed 's/:/\n        /g'
        done
  done
fi

sec "DOCTOR"
"${KT_BIN_DIR}/doctor.sh" "${TOOLS[@]}" 2>&1 || true

hr
echo "Report written to: ${OUT}"
echo "Send that file — it contains every answer the last debugging round needed."
