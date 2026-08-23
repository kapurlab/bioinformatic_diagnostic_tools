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

# env_for_pid PID — a running process's environment, one VAR=value per line.
# `ps eww` is the BSD idiom; procps on Linux accepts it, but the parse it
# forces (split on spaces) shreds any value containing a space — and on WSL
# that is GUARANTEED, because interop appends the Windows PATH ("/mnt/c/
# Program Files/..."), so the one variable this section exists to show would
# print truncated at 'Program' with junk lines after it. Linux has the exact
# answer: /proc/PID/environ is NUL-separated, so values with spaces survive.
# It can be unreadable (another user's process, or /proc mounted hidepid, a
# common HPC hardening) — the caller states that instead of showing an empty
# section, because "we could not read it" and "it was empty" are different
# diagnoses. Darwin has no /proc, so it keeps the ps path unchanged.
env_for_pid() {
  local p="${1:-}"
  if [[ -r "/proc/${p}/environ" ]]; then
    tr '\0' '\n' < "/proc/${p}/environ"
  else
    ps eww -o command= -p "${p}" 2>/dev/null | tr ' ' '\n'
  fi
}

# is_wsl — same test make-launcher.sh uses to pick its platform. /proc/version
# on any WSL kernel names microsoft; nothing else does.
is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

# mount_opts_for PATH — the mount options of the filesystem holding PATH.
# Needed because "Permission denied" on a file whose ls -l shows rwxr-xr-x is
# not a permissions problem — it is a noexec mount (HPC /tmp, scratch, some
# homes), and nothing about the file will ever say so. findmnt does the
# longest-prefix mountpoint match for us; without it, do the same match over
# /proc/mounts by hand. Prints nothing on hosts with neither (macOS).
mount_opts_for() {
  local p="${1:-}"
  [[ -n "${p}" ]] || return 0
  if command -v findmnt >/dev/null 2>&1; then
    findmnt -no OPTIONS -T "${p}" 2>/dev/null
  elif [[ -r /proc/mounts ]]; then
    awk -v p="${p}/" '
      { mp = ($2 == "/") ? "/" : $2 "/" }
      index(p, mp) == 1 && length($2) >= best { best = length($2); opts = $4 }
      END { if (opts != "") print opts }' /proc/mounts
  fi
}

# conda_bases — every conda base this machine could plausibly hold, one per
# line, deduped, the resolver's own pick FIRST. The old version of this list
# hardcoded four $HOME names while detect_conda accepts system-wide installs
# too (/opt/conda on HPC images and containers, /usr/local trees, the macOS
# installer's ~/opt). A lookalike env under a base this list missed is exactly
# the duplicate-env class this report exists to expose, so the list must be a
# superset of what the resolvers search — anchored on conda_base_dir, the base
# the launcher will actually use.
conda_bases() {
  {
    conda_base_dir 2>/dev/null || true
    local _b
    for _b in "${HOME}/miniforge3" "${HOME}/miniconda3" "${HOME}/mambaforge" \
              "${HOME}/anaconda3" "${HOME}/opt/anaconda3" "${HOME}/opt/miniconda3" \
              /opt/conda /opt/miniconda3 /opt/miniforge3 /opt/anaconda3 \
              /opt/mambaforge /usr/local/miniforge3 /usr/local/miniconda3; do
      [[ -d "${_b}" ]] && printf '%s\n' "${_b}"
    done
  } | awk 'NF && !seen[$0]++'
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
# Is THIS process translated? On Apple Silicon, a process running under Rosetta
# reports x86_64 from uname -m, and — the part that matters — every universal
# binary it launches inherits a preference for the x86_64 slice. That is how an
# arm64 env's tool ends up running its Intel half, and it is invisible unless
# asked about directly (2026-08-22).
if [[ "$(uname -s)" == "Darwin" ]]; then
  _tr="$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)"
  _native="$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)"
  if [[ "${_tr}" == "1" ]]; then
    echo "TRANSLATED: YES — this shell runs under Rosetta. Universal binaries it"
    echo "            launches prefer their x86_64 slice. Tools are launched with"
    echo "            an explicit arch pin so this cannot decide for them."
  else
    echo "translated: no (this process runs natively)"
  fi
  echo "apple silicon: $([[ "${_native}" == "1" ]] && echo yes || echo no)"
fi
echo "arch:       $(uname -m)   host conda subdir: $(host_conda_subdir)"
[[ "$(uname -s)" == "Darwin" ]] && {
  echo "macOS:      $(sw_vers -productVersion 2>/dev/null)"
  if /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
    echo "Rosetta 2:  available (x86_64 binaries can run)"
  else
    echo "Rosetta 2:  NOT available (x86_64 binaries cannot run here)"
  fi
}
# The Linux twins of the Rosetta questions. Nothing translates binaries on
# Linux, but three mechanisms redirect what actually loads and runs — the same
# way Rosetta silently redirected slices — and each is invisible unless asked
# about directly: the host glibc floor (a binary built against a newer glibc
# dies with "version `GLIBC_2.34' not found" though every record matches), the
# mount table (noexec makes a perfectly good rwxr-xr-x binary unexecutable),
# and the loader's environment overrides (LD_LIBRARY_PATH/LD_PRELOAD, which
# HPC `module load` sets and which then override every env's own libraries).
if [[ "$(uname -s)" == "Linux" ]]; then
  _glibc="$( (getconf GNU_LIBC_VERSION 2>/dev/null || ldd --version 2>/dev/null | head -1) | head -1 )"
  echo "glibc:      ${_glibc:-(none reported — musl host? conda-forge binaries need glibc and will fail with 'No such file or directory' on files that exist)}"
  _mopts="$(mount_opts_for "${BDTOOLS_HOME}")"
  if printf '%s' "${_mopts}" | tr ',' '\n' | grep -qx noexec; then
    echo "BDTOOLS_HOME mount: NOEXEC (${_mopts})"
    echo "            Files under ${BDTOOLS_HOME} can NEVER be executed, whatever"
    echo "            their permissions say — every env binary there fails with"
    echo "            'Permission denied' while ls -l shows rwxr-xr-x. Set"
    echo "            BDTOOLS_HOME to an exec-permitted filesystem and reinstall."
  else
    echo "BDTOOLS_HOME mount options: ${_mopts:-(could not determine)}"
  fi
  for _v in LD_LIBRARY_PATH LD_PRELOAD; do
    _val="$(eval "printf '%s' \"\${${_v}:-}\"")"
    if [[ -n "${_val}" ]]; then
      echo "${_v} is SET: [${_val}]"
      echo "            It redirects library loading for EVERY program launched — the"
      echo "            Linux analog of Rosetta redirecting slices. HPC 'module load'"
      echo "            sets it; conda binaries find their own libraries by RPATH and"
      echo "            never need it. If a tool dies with 'symbol lookup error' or"
      echo "            \"version ... not found\", run: module purge; unset ${_v}"
      echo "            and relaunch bdtools."
    else
      echo "${_v}: unset"
    fi
  done
  # The WSL twin of the Darwin translation block above. WSL is Linux with a
  # Windows machine grafted on, and every graft point is a distinct failure
  # mode: WSL1's syscall emulation predates what current conda-forge builds
  # assume; interop appends the entire Windows PATH so Windows shims (npm,
  # python.exe, CRLF .cmd wrappers) can shadow Linux tools AND every lookup
  # walks 30+ 9p-mounted directories; and anything installed under /mnt/*
  # sits on drvfs/9p, where symlinks, hardlinks, locking and speed are all
  # degraded — conda env builds there fail outright or take hours.
  if is_wsl; then
    _osrel="$(cat /proc/sys/kernel/osrelease 2>/dev/null || echo '?')"
    if printf '%s' "${_osrel}" | grep -qi 'microsoft-standard'; then
      echo "WSL:        2 (kernel ${_osrel})"
    else
      echo "WSL:        1 (kernel ${_osrel})"
      echo "            WSL 1's kernel emulation predates what current conda-forge"
      echo "            builds assume; the suite is only supported on WSL 2. Convert"
      echo "            (data survives): from PowerShell, wsl -l -v to find the"
      echo "            distro name, then wsl --set-version <distro> 2, reopen."
    fi
    # WSLInterop OR WSLInterop-late: on systemd-enabled distros (the out-of-box
    # Ubuntu 22.04/24.04 store images) systemd-binfmt flushes registrations at
    # boot and WSL re-registers interop as WSLInterop-late (microsoft/WSL#8843).
    # Probing only the classic name reported "not registered" on machines where
    # interop worked fine — adversarial-review catch.
    _interop_f=""
    for _f in /proc/sys/fs/binfmt_misc/WSLInterop /proc/sys/fs/binfmt_misc/WSLInterop-late; do
      [[ -f "${_f}" ]] && { _interop_f="${_f}"; break; }
    done
    if [[ -n "${_interop_f}" ]]; then
      echo "interop:    $(head -1 "${_interop_f}" 2>/dev/null) (${_interop_f##*/})"
    else
      echo "interop:    not registered (Windows .exe cannot be run from WSL;"
      echo "            opening the browser via wslview will fail)"
    fi
    _mnt_n="$(printf '%s' "${PATH}" | tr ':' '\n' | grep -c '^/mnt/[a-z]/' || true)"
    echo "Windows PATH entries (/mnt/*): ${_mnt_n}"
    if [[ "${_mnt_n}" != "0" ]]; then
      echo "            Interop appended the Windows PATH. Windows shims there (npm,"
      echo "            python.exe, Store app-execution aliases) can SHADOW Linux"
      echo "            tools, and every 'command -v'/conda/tool launch scans those"
      echo "            9p-mounted directories — a multi-second tax on each lookup."
      echo "            To cut them: add to /etc/wsl.conf:  [interop]"
      echo "            appendWindowsPath=false   then from PowerShell: wsl --shutdown"
    fi
    # /mnt/ is not synonymous with a Windows drive: `wsl --mount` puts ext4
    # disks under /mnt/wsl/<name> (and /mnt/wslg is WSLg's tmpfs) — native
    # Linux filesystems, exactly where a careful user parks a big
    # BDTOOLS_HOME. Only flag the rest of /mnt/.
    _on_windows_drive() {
      case "$1" in /mnt/wsl/*|/mnt/wslg/*) return 1;; /mnt/*) return 0;; esac
      return 1
    }
    _drv=""
    _on_windows_drive "${BDTOOLS_HOME}" && _drv="BDTOOLS_HOME (${BDTOOLS_HOME})"
    for _t in ${TOOLS[@]+"${TOOLS[@]}"}; do
      _td="$(tool_dir "${_t}")"
      _on_windows_drive "${_td}" && _drv="${_drv:+${_drv}; }checkout ${_t} (${_td})"
    done
    if [[ -n "${_drv}" ]]; then
      echo "ON A WINDOWS DRIVE (drvfs/9p): ${_drv}"
      echo "            Windows drives mounted into WSL degrade symlinks, hardlinks,"
      echo "            file locking and speed — conda cannot build reliable envs"
      echo "            there ('failed to create symbolic link', interrupted hardlink"
      echo "            phases, 10-50x slower). Move into the Linux filesystem:"
      echo "            export BDTOOLS_HOME=\$HOME/.local/share/bdtools and reinstall;"
      echo "            reach results from Windows via \\\\wsl\$\\<distro>\\home\\..."
    fi
  fi
fi

sec "SHELL ENVIRONMENT (what a tool inherits)"
# Interpreter-path variables first: these override an interpreter's own library
# root, so a stale one silently redirects every script the tool runs. Then the
# LOADER's own overrides — LD_LIBRARY_PATH/LD_PRELOAD do to every compiled
# binary what PERL5LIB does to perl scripts, and HPC module systems set them
# behind the user's back. BDTOOLS_HOME because a nonstandard value relocates
# every checkout and env this report is about.
for v in PERL5LIB PERLLIB PYTHONPATH PYTHONHOME RUBYLIB LD_LIBRARY_PATH LD_PRELOAD \
         CONDA_PREFIX CONDA_EXE CONDA_DEFAULT_ENV CONDA_SUBDIR \
         BDTOOLS_HOME BDTOOLS_TOOLSDIR BDTOOLS_MANIFEST; do
  printf '%-20s [%s]\n' "${v}" "$(eval "printf '%s' \"\${${v}:-unset}\"")"
done
echo "PATH (in order):"
printf '%s\n' "${PATH}" | tr ':' '\n' | nl -ba | sed 's/^/  /'

sec "CONDA"
echo "detect_conda:   $(detect_conda 2>/dev/null || echo '(none found)')"
echo "conda_base_dir: $(conda_base_dir 2>/dev/null || echo '(none)')"
# One list of bases, shared with the per-tool candidate scan below. This
# report used to keep two different hardcoded lists here and there — a third
# disagreement source in a report about resolver disagreements.
while IFS= read -r b; do
  [[ -d "${b}" ]] && echo "  base present: ${b}"
done < <(conda_bases)

# ${arr[@]+"${arr[@]}"} everywhere an array expands: macOS ships bash 3.2,
# where "${TOOLS[@]}" on an EMPTY array is a fatal 'unbound variable' under
# set -u — and the empty case is a machine with nothing installed, i.e. the
# machine most likely to be running diagnose. Same idiom the rest of the
# suite uses (fix.sh, install-local.sh).
for TOOL in ${TOOLS[@]+"${TOOLS[@]}"}; do
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
  # Candidates come from the same base list the CONDA section prints — headed
  # by conda_base_dir, the base the resolvers ACTUALLY use. The old hardcoded
  # $HOME-only sweep missed system-wide bases (/opt/conda on HPC, /usr/local),
  # so on exactly those machines the duplicate named env — the thing this
  # section exists to expose — was silently absent from the report.
  cands=("${DIR}/env")
  while IFS= read -r b; do
    [[ -n "${ENV_NAME}" && -d "${b}/envs/${ENV_NAME}" ]] && cands+=("${b}/envs/${ENV_NAME}")
  done < <(conda_bases)
  for e in ${cands[@]+"${cands[@]}"}; do
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
      # EXECUTE it: a universal binary matches every host on disk, so only
      # loading a native module shows which slice actually runs.
      if "${e}/bin/perl" -MCwd -e 1 >/dev/null 2>&1; then
        echo "    perl loads Cwd (native module): OK"
      else
        echo "    perl loads Cwd (native module): FAILS -> $("${e}/bin/perl" -MCwd -e 1 2>&1 | head -2 | tr '\n' ' ')"
      fi
      # THE CHAIN THAT MATTERS. The GUI does not run perl from a shell — a
    # python process spawns it. If a spawn picks a different slice than an
    # interactive shell does, that difference IS the bug, and only running
    # both shows it.
    if [[ -x "${e}/bin/python" && -x "${e}/bin/perl" ]]; then
      if PATH="${e}/bin:${PATH}" "${e}/bin/python" -c \
           'import subprocess,sys; sys.exit(subprocess.run(["perl","-MCwd","-e","1"]).returncode)' \
           >/dev/null 2>&1; then
        echo "    python -> perl -> Cwd (the GUI's chain): OK"
      else
        echo "    python -> perl -> Cwd (the GUI's chain): FAILS  <-- a spawn picks a different slice than the shell does"
      fi
      # Test the pin the LAUNCHER would actually apply for this env, not a
      # guess: on an osx-64 env the right pin is -x86_64, and testing -arm64
      # there fails for a reason that has nothing to do with the bug.
      _sub="$(env_conda_subdir "${e}")"
      case "${_sub}" in
        osx-arm64) _pin=arm64;;
        osx-64)    _pin=x86_64;;
        *)         _pin="";;
      esac
      if [[ -n "${_pin}" && "$(uname -s)" == "Darwin" && -x /usr/bin/arch ]]; then
        if PATH="${e}/bin:${PATH}" /usr/bin/arch -"${_pin}" "${e}/bin/python" -c \
             'import subprocess,sys; sys.exit(subprocess.run(["perl","-MCwd","-e","1"]).returncode)' \
             >/dev/null 2>&1; then
          echo "    same chain under 'arch -${_pin}' (this env's platform): OK  <-- pinning the launch fixes it"
        else
          echo "    same chain under 'arch -${_pin}' (this env's platform): FAILS <-- pinning the launch is NOT enough"
        fi
      fi
    fi
    if [[ "$(uname -s)" == "Darwin" ]] && file -b "${e}/bin/perl" 2>/dev/null | grep -q universal; then
        for a in arm64 x86_64; do
          if /usr/bin/arch -"${a}" "${e}/bin/perl" -MCwd -e 1 >/dev/null 2>&1; then
            echo "      slice ${a}: Cwd OK"
          else
            echo "      slice ${a}: Cwd FAILS  <-- this slice cannot run this env's modules"
          fi
        done
      fi
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
    cmdline="$(ps -o command= -p "${pid}" 2>/dev/null)"
    echo "  pid ${pid}: $(printf '%s' "${cmdline}" | cut -c1-160)"
    if [[ "$(uname -s)" == "Darwin" && "${cmdline}" != /usr/bin/arch* ]]; then
      echo "    NOTE: this backend was NOT launched with an architecture pin."
      echo "          Current code pins it (install-local.sh arch_prefix), so this"
      echo "          process predates the running code — a stale backend survives"
      echo "          Ctrl-C on the dashboard (start_new_session). Restart it:"
      echo "            pkill -f 'uvicorn app.main:app' && bdtools dashboard"
    fi
    # The Linux twin of that staleness note. A long-lived backend keeps the
    # python, env, and working directory it had WHEN IT STARTED; an update
    # that rebuilds the env replaces those files on disk, and the kernel then
    # marks the process's view of them '(deleted)' in /proc. That backend is
    # executing code an update has already replaced — doctor says the modules
    # are present (they are, in the NEW env) while the process imports from
    # the old, deleted one. Only /proc shows this.
    if [[ "$(uname -s)" == "Linux" ]]; then
      _exe="$(readlink "/proc/${pid}/exe" 2>/dev/null || true)"
      _cwd="$(readlink "/proc/${pid}/cwd" 2>/dev/null || true)"
      [[ -n "${_exe}" ]] && echo "    exe: ${_exe}"
      [[ -n "${_cwd}" ]] && echo "    cwd: ${_cwd}"
      if [[ "${_exe}" == *" (deleted)"* || "${_cwd}" == *" (deleted)"* ]]; then
        echo "    STALE: this backend is running code/an env that an update has since"
        echo "           replaced (its executable or working directory is deleted on"
        echo "           disk). Updates never reach a running process. Restart it:"
        echo "             pkill -f 'uvicorn app.main:app' && bdtools dashboard"
      fi
    fi
    # Can THIS machine reach the backend's port? A live pid proves the process
    # exists, not that it is answering — and "answers here but not in the
    # browser" moves the fault outside the process entirely. install-local.sh
    # always launches with --port N, so the port is on the command line.
    _port="$(printf '%s' "${cmdline}" | sed -n 's/.*--port[= ][= ]*\([0-9][0-9]*\).*/\1/p')"
    if [[ -n "${_port}" ]]; then
      if "${PYBIN}" -c "import socket; socket.create_connection(('127.0.0.1', ${_port}), 2).close()" 2>/dev/null; then
        echo "    port ${_port}: listening (reachable at 127.0.0.1:${_port} from this shell)"
        if is_wsl; then
          echo "      NOTE (WSL): if a WINDOWS browser still cannot open localhost:${_port},"
          echo "      the backend is fine — the WSL->Windows localhost forwarding layer"
          echo "      is what failed (typical after Windows sleep/resume). Reset it from"
          echo "      PowerShell:  wsl --shutdown   then reopen the terminal and relaunch."
        fi
      else
        echo "    port ${_port}: NOT reachable at 127.0.0.1:${_port} from this shell"
      fi
    fi
    _envlines="$(env_for_pid "${pid}" | grep -E '^(PATH|PERL5LIB|PYTHONPATH|LD_LIBRARY_PATH|LD_PRELOAD)=' || true)"
    if [[ -n "${_envlines}" ]]; then
      printf '%s\n' "${_envlines}" | sed 's/^/    /' | while IFS= read -r l; do
          printf '%s\n' "${l}" | sed 's/:/\n        /g'
        done
    else
      # Say so, rather than printing an empty section: "unreadable" and
      # "unset" are different diagnoses.
      echo "    (environment unreadable — another user's process, or /proc mounted hidepid)"
    fi
  done
fi

sec "DOCTOR (deep: every program is launched to prove it can start)"
for _t in ${TOOLS[@]+"${TOOLS[@]}"}; do
  _d="$(tool_dir "${_t}")"
  _py="$(tool_env_python "${_d}" "$(manifest_get "${_t}" env 2>/dev/null || true)" "${_t}")"
  "${PYBIN}" "${KT_BIN_DIR}/lib/check.py" --tool "${_t}" --dir "${_d}" \
    --python "${_py}" --scope all --deep 2>&1 || true
done

hr
echo "Report written to: ${OUT}"
echo "Send that file — it contains every answer the last debugging round needed."
