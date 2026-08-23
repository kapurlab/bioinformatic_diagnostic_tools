#!/usr/bin/env bash
# install-local.sh — standalone (no-OOD) install + run of one tool.
#
# The "personal computer" path: Linux, macOS, or Windows via WSL2. There is no
# Open OnDemand here — the same FastAPI backend that OOD proxies in production
# is run directly, serving its built React SPA at http://127.0.0.1:<port>/.
# Because every tool's frontend uses relative URLs and FastAPI serves dist/,
# the app is identical with or without OOD in front of it.
#
#   install-local.sh <tool> [--prefix DIR] [--port N] [--dry-run]
#   install-local.sh --run-only <tool> [--port N]      # skip build, just launch
#
# Steps (build):
#   1. Clone the tool at its manifest-pinned version (if not already present).
#   2. Build the conda env + frontend — delegating to the tool's own
#      deploy/install.sh when it exists, else a generic env+frontend build.
#   3. Launch uvicorn on a free localhost port and open the browser.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TOOL=""; RUN_ONLY=0; BUILD_ONLY=0; PORT=""; NO_BROWSER=0; PRINT_PYTHON=0; REBUILD=0; FRESH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-only)   RUN_ONLY=1; shift;;
    --build-only) BUILD_ONLY=1; shift;;
    --rebuild)    REBUILD=1; shift;;            # ADDITIVE: conda env update from the spec (picks up newly-declared deps)
    --fresh)      FRESH=1; shift;;              # START OVER: discard the existing env and build it from nothing
    --no-browser) NO_BROWSER=1; shift;;        # launch but don't open a browser (used by the dashboard)
    --print-python) PRINT_PYTHON=1; shift;;     # print the tool's env python if built, else exit 1; no build/launch
    --prefix)   export BDTOOLS_HOME="$2"; shift 2;;
    --port)     PORT="$2"; shift 2;;
    --dry-run)  DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help)  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)         die "unknown option: $1";;
    *)          TOOL="$1"; shift;;
  esac
done
[[ -n "${TOOL}" ]] || die "name a tool (see: bdtools list)"
manifest_has "${TOOL}" || die "unknown tool: ${TOOL}"

# Refuse a BDTOOLS_HOME on a Windows drive under WSL, before anything clones or
# solves. Users pick /mnt/c deliberately ("keep it on the big drive"), and
# nothing used to warn — the failure then surfaced as an inscrutable conda
# transaction error hours into a solve, not as a stated precondition. drvfs
# (the 9p mount behind /mnt/*) cannot host a conda env: no hardlinks (conda
# links packages out of its cache), unreliable symlink semantics (activate
# scripts and the ensure_env_java link break), POSIX permissions are
# approximated, and every metadata operation is 10-50x slower than the Linux
# filesystem — so `conda env create` fails mid-transaction or yields an env
# whose binaries do not run. A function (not inline top-level code) so tests
# can exercise it with a fabricated kernel string; the optional argument
# exists only for that.
_require_linux_fs_home() {
  local kernel="${1:-$(_wsl_kernel)}"
  case "${kernel}" in *[Mm]icrosoft*) ;; *) return 0;; esac
  [[ "${BDTOOLS_HOME}" == /mnt/* ]] || return 0
  # /mnt/wsl and /mnt/wslg are NOT Windows drives: `wsl --mount` attaches ext4
  # disks under /mnt/wsl/<name> (tmpfs parent, native Linux fs, full hardlink
  # and symlink semantics) — Microsoft's documented way to give WSL a large
  # disk, i.e. exactly where a careful user puts a big BDTOOLS_HOME. Refusing
  # those punishes the person who followed the best advice.
  case "${BDTOOLS_HOME}" in /mnt/wsl/*|/mnt/wslg/*) return 0;; esac
  die "BDTOOLS_HOME is on a Windows drive (${BDTOOLS_HOME}), and conda envs cannot be built on one:
       /mnt/* is a drvfs mount — no hardlinks, broken symlink semantics, 10-50x slower
       metadata, so 'conda env create' fails mid-transaction or produces an env that
       cannot run. Use a path inside the Linux filesystem instead:
         unset BDTOOLS_HOME            # default: ~/.local/share/bdtools (ext4)
         # or: bin/bdtools install ${TOOL} --prefix ~/bdtools   (any Linux-fs path)"
}
# Launch-only and query modes clone and solve nothing, so a legacy /mnt install
# that limps along must stay LAUNCHABLE — the guard's own rationale is scoped
# to build time, and dying here also broke --print-python, which check-updates
# and the dashboard use to detect built tools (a hard die there misreported
# every built env as unbuilt). Warn on those paths; die only when a build could
# actually start. --dry-run keeps the die: a dry run's job is to report that
# the real run would be refused.
if [[ ${RUN_ONLY} -eq 1 || ${PRINT_PYTHON} -eq 1 ]]; then
  case "$(_wsl_kernel)" in *[Mm]icrosoft*)
    if [[ "${BDTOOLS_HOME}" == /mnt/* ]]; then
      case "${BDTOOLS_HOME}" in /mnt/wsl/*|/mnt/wslg/*) ;; *)
        warn "BDTOOLS_HOME is on a Windows drive (${BDTOOLS_HOME}) — launches may misbehave and builds here will be refused; move it to a Linux-fs path (see bin/bdtools doctor)";;
      esac
    fi;;
  esac
else
  _require_linux_fs_home
fi

DIR="$(tool_dir "${TOOL}")"
REPO="$(manifest_get "${TOOL}" repo)"
VERSION="$(manifest_get "${TOOL}" version)"
ENV_NAME="$(manifest_get "${TOOL}" env)"

# Strict channel priority for every conda/mamba solve below. It's the
# bioconda-recommended setting: the solver honors channel order up front
# (conda-forge > bioconda > defaults) instead of exploring cross-channel
# package combinations — the latter is what makes a mixed-channel
# environment.yml solve spin at 100% CPU for minutes (or effectively hang).
# Exported so it also reaches tools that delegate to their own
# deploy/install.sh (their `mamba env create` inherits it). Operator override
# wins if one is already set in the environment.
export CONDA_CHANNEL_PRIORITY="${CONDA_CHANNEL_PRIORITY:-strict}"

# Neutralize the Anaconda `defaults` channel. Every tool's environment.yml lists
# `- defaults` (repo.anaconda.com) alongside conda-forge/bioconda; mixing that
# third, differently-populated channel into a large bioconda stack balloons the
# solver's search space and is what makes e.g. amr_plus_gui grind for 15+ min at
# 100% CPU. Rather than edit and re-tag eight separate tool repos, remap what
# `defaults` expands to onto the channels these envs already use — so the
# `- defaults` line resolves to conda-forge/bioconda instead of repo.anaconda.com
# and stops widening the solve. Exported so delegated deploy/install.sh builds
# inherit it. Operator override wins if one is already set.
export CONDA_DEFAULT_CHANNELS="${CONDA_DEFAULT_CHANNELS:-conda-forge,bioconda}"

# Prefer conda's libmamba solver over standalone mamba for env builds. mamba 2.x
# has a solver regression that spins indefinitely on large bioconda graphs:
# amr_plus_gui's env ran mamba 2.5 at 100% CPU for 2h+ without finishing, while
# conda/libmamba solved the identical spec in ~6 min (full build 13 min). The
# tool deploy/install.sh scripts prefer mamba but honor a preset CONDA_FRONTEND,
# so point them at conda. Only set when a conda binary is resolvable and the
# operator hasn't already chosen a frontend.
if [[ -z "${CONDA_FRONTEND:-}" ]]; then
  _cf_base="$(conda_base_dir 2>/dev/null || true)"
  [[ -n "${_cf_base}" && -x "${_cf_base}/bin/conda" ]] && export CONDA_FRONTEND="${_cf_base}/bin/conda"
fi

# Progress helpers for the long, often-silent build steps (conda solve, package
# download, delegated deploy/install.sh). The problem they solve: a solve is
# CPU-bound and silent, a download is I/O-bound and silent, and a *stalled*
# download (dead mirror connection, no timeout) is silent too — on the command
# line all three look identical, which is the root of the "hung for hours"
# reports. So we watch two independent progress signals and act on them.

# _tree_cpu_ticks PID — total CPU ticks (utime+stime) of PID and all descendants.
# Rises during a solve/extract even when nothing is written to disk.
# Cumulative CPU time of a process and its descendants, in hundredths of a second.
#
# Asks ps rather than reading /proc/<pid>/stat: /proc is Linux-only, so the old
# version returned 0 for every process on macOS — see _watched_bytes for what that
# combination cost. `ps -o time=` is in POSIX and prints [[DD-]HH:]MM:SS[.ss] on
# both GNU and BSD.
_tree_cpu_ticks() {
  local frontier="$1" next pid pids=""
  while [[ -n "${frontier}" ]]; do
    next=""
    for pid in ${frontier}; do
      pids="${pids} ${pid}"
      next="${next} $(pgrep -P "${pid}" 2>/dev/null | tr '\n' ' ')"
    done
    frontier="${next}"
  done
  [[ -n "${pids// /}" ]] || { echo 0; return; }
  # ONE comma-separated pid operand, not word-split bare operands. The
  # space-separated form happened to work on macOS BSD ps (extra numeric
  # operands are taken as pids) and on procps (bare digits are legacy pid
  # selectors), but neither behavior is the documented grammar — procps
  # documents a blank-separated list only as a single quoted argument, and a
  # busybox-class ps or a stricter parse silently returns nothing. "Silently
  # returns nothing" here means _tree_cpu_ticks reports 0 forever, which
  # re-opens the exact "no CPU signal -> stall-kill every long step" failure
  # the comments above document from macOS. Commas are the one form procps,
  # BSD/macOS, and busybox-with-procps all accept — and passing a single
  # quoted argument also stops relying on word-splitting an unquoted variable.
  local plist=""
  for pid in ${pids}; do plist="${plist:+${plist},}${pid}"; done
  ps -o time= -p "${plist}" 2>/dev/null | awk '
    {
      line = $0
      gsub(/^[ \t]+|[ \t]+$/, "", line)
      if (line == "") next
      days = 0
      # "DD-HH:MM:SS" — days are separated by "-", not ":", so fold them apart
      # or a long-running step would be scaled by 60 instead of 24.
      if (match(line, /^[0-9]+-/)) {
        days = substr(line, 1, RLENGTH - 1) + 0
        line = substr(line, RLENGTH + 1)
      }
      n = split(line, f, ":")
      s = 0
      for (i = 1; i <= n; i++) s = s * 60 + f[i]
      total += (days * 86400 + s) * 100
    }
    END { printf "%d\n", total }'
}

# _watched_bytes — total bytes across the paths a build writes to (the pkg cache,
# the target env prefix, the frontend). Rises during a download/extract/link even
# when CPU is idle. Cheap enough at heartbeat cadence; missing paths are skipped.
# Total size of the watched paths, in KiB.
#
# `du -sk` not `du -sb`: -b is a GNU extension and BSD/macOS du rejects it
# ("du: invalid option -- b"), so this returned 0 for every path on every Mac.
# Combined with _tree_cpu_ticks also returning 0 there (no /proc), the stall
# detector saw NEITHER cpu nor disk progress ever — so every build step longer
# than BDTOOLS_IDLE_TIMEOUT was killed and retried on macOS. That is what killed
# the 1 GB kSNP4 Mac-package download at 300s, mid-transfer, twice.
#
# TODO(HPC): the watch list includes the shared conda pkgs cache, and on a
# parallel/network filesystem (nfs/lustre/gpfs) a `du -sk` of a tens-of-GB,
# hundreds-of-thousands-of-inodes cache is a full metadata sweep every
# heartbeat — it can lag the monitor behind the build and hammer the metadata
# servers site-wide. A safe fix needs longest-prefix mount matching against
# the fstype (and macOS has no /proc/mounts), so it is deliberately not done
# here as a drive-by; replace the full-cache walk with a newest-entry
# freshness probe, or skip du for watch paths on those fstypes.
_watched_bytes() {
  local p b total=0
  for p in "$@"; do
    [[ -e "${p}" ]] || continue
    b="$(du -sk "${p}" 2>/dev/null | awk 'NR==1{print $1}')"
    total=$(( total + ${b:-0} ))
  done
  echo "${total}"
}

# _kill_tree PID — SIGKILL PID and all descendants (mamba spawns worker children).
_kill_tree() {
  local p
  for p in $(pgrep -P "$1" 2>/dev/null); do _kill_tree "${p}"; done
  kill -9 "$1" 2>/dev/null || true
}

# _env_built [conda_base] — is the tool's target conda env created yet? Handles
# both a prefix env (<checkout>/env) and a named --personal env (<base>/envs/<name>).
# Used to gate the runaway-solve guard: a stuck solve never produces an env.
_env_built() {
  [[ -x "${DIR}/env/bin/python" ]] && return 0
  [[ -n "${1:-}" && -n "${ENV_NAME:-}" && -x "${1}/envs/${ENV_NAME}/bin/python" ]] && return 0
  return 1
}

# with_progress "<label>" cmd [args...] — run a long build step with a heartbeat,
# a stall detector, and automatic retry. Every BDTOOLS_HEARTBEAT_SECS (default 30)
# it checks CPU-tree ticks and watched-bytes; if NEITHER has advanced for
# BDTOOLS_IDLE_TIMEOUT seconds (default 300, 0 disables) the step is treated as
# wedged — the process tree is killed and the whole step retried, up to
# BDTOOLS_BUILD_TRIES attempts (default 2). This turns a dead-mirror stall from an
# indefinite hang into a bounded wait + retry. Honors --dry-run and `set -e`;
# returns the command's own exit code (124 if it was killed for stalling).
with_progress() {
  local label="$1"; shift
  if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
    # Print the command the user could run, not our internal wrapper: a payload
    # of `_conda_step <envdir> conda env update …` is reported as the conda call.
    local shown=("$@")
    [[ "${shown[0]:-}" == "_conda_step" ]] && shown=("${shown[@]:2}")
    echo "  [dry-run] ${label}: ${shown[*]:-}"
    return 0
  fi
  # Keep every step's full output in a per-tool log. A failed build's output IS
  # the diagnosis, and it used to exist only in scrollback: two failed rebuilds
  # of kraken_id_parse_gui on a Mac left nothing to read but an exit code, so
  # the actual defect (a solve dying for one platform) stayed unknown through
  # both. Truncated once per install run, appended per step; _run_watched tees
  # into it (a local, so dynamic scoping hands it down). Best-effort: a machine
  # where the log cannot be written still builds.
  local _BUILD_LOG=""
  if [[ -n "${BDTOOLS_HOME:-}" ]]; then
    _BUILD_LOG="${BDTOOLS_HOME}/state/build-logs/${TOOL:-step}.log"
    if mkdir -p "${_BUILD_LOG%/*}" 2>/dev/null; then
      if [[ -z "${_BUILD_LOG_STARTED:-}" ]]; then
        { : > "${_BUILD_LOG}"; } 2>/dev/null && _BUILD_LOG_STARTED=1 || _BUILD_LOG=""
      fi
    else
      _BUILD_LOG=""
    fi
  fi
  local tries="${BDTOOLS_BUILD_TRIES:-2}" attempt=1 rc=0
  while :; do
    [[ "${tries}" -gt 1 ]] && log "${label} — attempt ${attempt}/${tries}"
    [[ -n "${_BUILD_LOG}" ]] && printf '\n===== %s — attempt %s — %s =====\n' \
      "${label}" "${attempt}/${tries}" "$(date '+%Y-%m-%d %H:%M:%S')" >> "${_BUILD_LOG}" 2>/dev/null
    rc=0; _run_watched "${label}" "$@" || rc=$?
    [[ ${rc} -eq 0 ]] && return 0
    if [[ ${attempt} -ge ${tries} ]]; then
      warn "${label} — giving up after ${attempt} attempt(s) (exit ${rc})"
      # The final failure must not leave a partial prefix behind either: a
      # corpse that outlives the run is what a later build's subdir decision
      # mistook for an existing env (see _discard_partial_env_prefix).
      [[ "${1:-}" == "_conda_step" ]] && _discard_partial_env_prefix "${2:-}"
      [[ -n "${_BUILD_LOG}" ]] && info "  the step's full output is saved at: ${_BUILD_LOG}"
      return ${rc}
    fi
    # Clear a partial prefix so attempt 2 starts clean — conda's rollback leaves
    # untracked __pycache__ behind, and the retry otherwise dies in a storm of
    # "ClobberError: path already exists in the target prefix".
    [[ "${1:-}" == "_conda_step" ]] && _discard_partial_env_prefix "${2:-}"
    warn "${label} — failed/stalled (exit ${rc}); retrying in 5s…"
    sleep 5; attempt=$(( attempt + 1 ))
  done
}

# _discard_partial_env_prefix ENVDIR — remove the partial prefix a FAILED
# _conda_step left at ENVDIR, if — and only if — that step created it.
#
# Why it cannot stay: a dead `conda env create` leaves a conda-meta/ directory
# with no bin/python. Between attempts that corpse ClobberErrors the retry; left
# after the FINAL attempt it survives to the next run, where anything that reads
# an env's platform off conda-meta can mistake it for an existing env. That is
# the seed of the 2026-08 mixed-architecture macOS incident: a leftover
# ${DIR}/env said osx-64, the subdir decision believed it, and the mutations ran
# CONDA_SUBDIR=osx-64 against the real osx-arm64 named env — one foreign openssl
# later, no additive operation could repair kraken_id_parse_gui.
#
# ONLY a prefix THIS STEP CREATED (_ENV_PREEXISTING=0). "No working python"
# alone was the old guard, and it is exactly the state a rolled-back update of
# an EXISTING env leaves behind — deleting on that alone once cost a working
# kraken_id_parse_gui. An env that was here before the step is never ours to
# delete, however broken it now looks: it can be restored from the snapshot
# (see restore_env_hint), and it may still run.
_discard_partial_env_prefix() {
  local _envdir="${1:-}"
  [[ -n "${_envdir}" && "${_envdir}" == /* && -d "${_envdir}" ]] || return 0
  if [[ "${_ENV_PREEXISTING:-0}" -eq 0 && ! -x "${_envdir}/bin/python" \
        && ( "${_envdir}" == */env || "${_envdir}" == */envs/* ) ]]; then
    warn "  removing the partially-created env (it is not a usable environment): ${_envdir}"
    rm -rf "${_envdir}"
  elif [[ "${_ENV_PREEXISTING:-0}" -eq 1 ]]; then
    info "  keeping the existing env as it is: ${_envdir}"
  fi
  return 0
}

# _run_watched: one attempt — launch, monitor CPU+disk progress, kill on stall.
_run_watched() {
  local label="$1"; shift
  local secs="${BDTOOLS_HEARTBEAT_SECS:-30}" idle_max="${BDTOOLS_IDLE_TIMEOUT:-300}"
  local solve_max="${BDTOOLS_SOLVE_MAX_SECS:-2400}"   # cap the env-create (solve+download) phase
  local watch=() cbase
  cbase="$(conda_base_dir 2>/dev/null || true)"
  [[ -n "${cbase}" ]] && watch+=("${cbase}/pkgs")
  [[ -n "${cbase}" && -n "${ENV_NAME:-}" ]] && watch+=("${cbase}/envs/${ENV_NAME}")
  # vendor/ holds hand-downloaded third-party payloads (ksnp_gui's ~1 GB kSNP4.1
  # package). Without it, a long download is invisible to the disk check and the
  # step looks wedged even while curl is writing steadily — which is exactly how
  # that download got stall-killed at 300s, twice, on a machine with a fine network.
  watch+=("${DIR}/env" "${DIR}/frontend/node_modules" "${DIR}/frontend/dist" "${DIR}/vendor")
  local t0 last cpu0 disk0 cpu disk now e idle rc=0 cmd
  t0="$(date +%s)"; last="${t0}"
  log "${label} — started $(date '+%H:%M:%S')  (heartbeat ${secs}s; stall-kill after ${idle_max}s of no progress)"
  if [[ -n "${_BUILD_LOG:-}" ]]; then
    # Tee the payload's output into the build log. The payload stays the DIRECT
    # background job (process substitution, not a pipeline): $! must be the
    # payload's own pid or the CPU-progress watcher below would be watching tee.
    "$@" > >(tee -a "${_BUILD_LOG}" 2>/dev/null) 2>&1 & cmd=$!
  else
    "$@" & cmd=$!
  fi
  cpu0="$(_tree_cpu_ticks "${cmd}")"; disk0="$(_watched_bytes "${watch[@]}")"
  while kill -0 "${cmd}" 2>/dev/null; do
    sleep "${secs}"
    kill -0 "${cmd}" 2>/dev/null || break
    now="$(date +%s)"; e=$(( now - t0 ))
    cpu="$(_tree_cpu_ticks "${cmd}")"; disk="$(_watched_bytes "${watch[@]}")"
    if [[ "${cpu}" != "${cpu0}" || "${disk}" != "${disk0}" ]]; then
      last="${now}"; cpu0="${cpu}"; disk0="${disk}"
      printf '  … %s: working, %dm%02ds elapsed\n' "${label}" $((e/60)) $((e%60))
    else
      idle=$(( now - last ))
      printf '  … %s: NO cpu/disk progress for %ds (elapsed %dm%02ds)\n' "${label}" "${idle}" $((e/60)) $((e%60))
      if [[ "${idle_max}" -gt 0 && ${idle} -ge ${idle_max} ]]; then
        warn "${label} — stalled ${idle}s with no CPU or disk progress; killing to retry"
        _kill_tree "${cmd}"; wait "${cmd}" 2>/dev/null || true
        return 124
      fi
    fi
    # Runaway-solve guard: a stuck mamba/conda solve spins at 100% CPU — so the
    # idle check above (which resets on CPU progress) never fires — and never
    # creates the env. Cap the solve+download phase: if the target env still
    # doesn't exist after solve_max seconds, treat it as runaway and kill+retry.
    # Disarmed the moment the env appears, so a long post-solve step (a big DB
    # download/extract to an unwatched path, npm, pip) is never capped — only
    # the idle check guards those.
    if [[ "${solve_max}" -gt 0 && ${e} -ge ${solve_max} ]] && ! _env_built "${cbase}"; then
      warn "${label} — env still not created after ${e}s (runaway solve?); killing to retry"
      _kill_tree "${cmd}"; wait "${cmd}" 2>/dev/null || true
      return 124
    fi
  done
  if wait "${cmd}"; then rc=0; else rc=$?; fi
  local tot=$(( $(date +%s) - t0 ))
  if [[ ${rc} -eq 0 ]]; then ok "${label} — done in $((tot/60))m$((tot%60))s"
  else warn "${label} — exited ${rc} after $((tot/60))m$((tot%60))s"; fi
  return ${rc}
}

# --------------------------------------------------------------------------
# 1. checkout
# --------------------------------------------------------------------------
ensure_checkout() {
  if [[ -d "${DIR}/.git" ]]; then
    local at; at="$(git -C "${DIR}" describe --tags --always 2>/dev/null || echo '?')"
    # Reuse the existing checkout, but first move it onto the manifest-pinned
    # ref if it isn't there yet. This makes `git pull` + re-run pick up a shipped
    # fix (a bumped pin) instead of silently reusing old code — the key to
    # resuming a partial/failed `install all` after an upstream fix. Skipped for
    # --run-only, and never clobbers local (tracked) edits.
    if [[ ${RUN_ONLY} -eq 0 && -n "${VERSION}" ]]; then
      local want; want="$(git -C "${DIR}" rev-parse -q --verify "refs/tags/${VERSION}^{commit}" 2>/dev/null || true)"
      if [[ -z "${want}" ]]; then
        # Every managed git operation that can materialize working-tree files
        # carries -c core.autocrlf=false -c core.eol=lf. A user's global
        # autocrlf=true (a Windows gitconfig copied onto WSL) otherwise writes
        # every tracked script with CRLF, and the first shebanged one dies at
        # exec with "/usr/bin/env: 'bash\r': No such file or directory" —
        # breaking the INSTALLER itself, before any check can run. These
        # checkouts are bdtools' own artifacts, so pinning the translation
        # inside them overrides nothing the user owns. Same flags on the
        # clone below and on common.sh:ensure_checkout.
        run git -C "${DIR}" -c core.autocrlf=false -c core.eol=lf fetch --tags --depth 1 origin "${VERSION}" >/dev/null 2>&1 || true
        want="$(git -C "${DIR}" rev-parse -q --verify "refs/tags/${VERSION}^{commit}" 2>/dev/null \
                || git -C "${DIR}" rev-parse -q --verify FETCH_HEAD 2>/dev/null || true)"
      fi
      local head; head="$(git -C "${DIR}" rev-parse -q --verify HEAD 2>/dev/null || true)"
      if [[ -n "${want}" && "${head}" != "${want}" ]]; then
        # "Any dirty file" was the wrong bar: every install rewrites the tracked
        # frontend/dist + package-lock, so a built checkout is permanently dirty
        # and this could never advance the pin again — it warned, then BUILT WITH
        # THE OLD CODE while reporting the new pin. Use the shared rule (see
        # common.sh:tool_blocking_edits) and force past regenerable output, which
        # the build immediately recreates anyway.
        local blocking; blocking="$(tool_blocking_edits "${DIR}")"
        if [[ -z "${blocking}" ]]; then
          log "moving ${TOOL} checkout ${at} -> pinned ${VERSION}"
          # Carry site-localized OOD card config (ood/apps/**) across the force
          # checkout — a deployment's own cluster/account edits (common.sh).
          local _site_snap; _site_snap="$(snapshot_site_edits "${DIR}")"
          run git -C "${DIR}" -c core.autocrlf=false -c core.eol=lf checkout -f -q "${VERSION}" 2>/dev/null \
            || run git -C "${DIR}" -c core.autocrlf=false -c core.eol=lf checkout -f -q "${want}"
          restore_site_edits "${DIR}" "${_site_snap}"
          at="$(git -C "${DIR}" describe --tags --always 2>/dev/null || echo '?')"
        else
          # Name the consequence, not just the condition: the build below is about
          # to run the OLD installer, which is precisely the confusing case where a
          # shipped fix appears not to work.
          warn "${TOOL} checkout is ${at} but the pin is ${VERSION}, and these tracked files have local edits:"
          while IFS= read -r _b; do [[ -n "${_b}" ]] && warn "    ${_b}"; done <<< "${blocking}"
          warn "  NOT moving — so this build will use ${at}'s code, NOT the pinned ${VERSION}."
          warn "  Any fix shipped in ${VERSION} will appear not to work until you resolve this:"
          warn "    cd ${DIR} && git stash    # or commit the edits"
          warn "    then re-run: bin/bdtools install ${TOOL}"
        fi
      fi
    fi
    ok "checkout present: ${DIR} (${at})"
    return
  fi
  [[ ${RUN_ONLY} -eq 1 ]] && die "${TOOL} is not installed at ${DIR} (run: bdtools install ${TOOL})"
  log "cloning ${TOOL} @ ${VERSION}"
  run mkdir -p "$(dirname "${DIR}")"
  # Line-ending pin: see the fetch above — a global autocrlf=true on WSL would
  # otherwise CRLF-corrupt every script this clone materializes.
  run git clone --config core.autocrlf=false --config core.eol=lf --branch "${VERSION}" --depth 1 "${REPO}" "${DIR}" \
    || die "git clone failed (${REPO} @ ${VERSION}). If this said 'Disk quota exceeded', your home filesystem is full — on an HPC set BDTOOLS_HOME to a larger scratch/work/group filesystem and re-run (see docs/INSTALL_LOCAL.md)."
}

# --------------------------------------------------------------------------
# 2. build (env + frontend)
# --------------------------------------------------------------------------
# conda-forge openjdk on osx-64 installs the JRE under <env>/lib/jvm/bin/ and only
# exports JAVA_HOME from its activate.d hook. But tools here run with just
# <env>/bin on PATH (no `conda activate`), so java-based tools can't find `java`
# and die — e.g. picard in kraken_id_parse_gui, or pilon/trimmomatic invoked by
# shovill in mlst_gui. Symlink java into <env>/bin so it resolves without
# activation. No-op on Linux (openjdk already provides bin/java) and for envs
# with no JRE. Works for both in-checkout envs and named conda envs.
ensure_env_java() {
  local envdir="$1"
  [[ -x "${envdir}/lib/jvm/bin/java" && ! -e "${envdir}/bin/java" ]] || return 0
  ln -sfn ../lib/jvm/bin/java "${envdir}/bin/java" 2>/dev/null \
    && ok "linked ${envdir}/bin/java -> lib/jvm/bin/java (JRE)"
}

# _conda_step ENVDIR cmd [args...] — one conda operation, with the env's
# activation hooks guarded first. Run as the with_progress payload (not around
# it) on purpose: if a transaction installs a hook that then breaks a later
# package's post-link script, attempt 2 of the same step starts by fixing the
# hook the failed attempt left behind, so the retry can actually succeed.
_conda_step() {
  local envdir="$1"; shift
  # THE PLATFORM INVARIANT: a conda operation must solve for the platform of the
  # prefix it is about to change. If the prefix records one (conda-meta) and the
  # ambient CONDA_SUBDIR says otherwise, running would link foreign-architecture
  # binaries into it — the 2026-08 macOS incident: a subdir read off a leftover
  # half-built prefix (osx-64) was inherited by a `conda install` into the real
  # osx-arm64 env, and the resulting mixed env was unrepairable by any additive
  # operation. Refusing here makes that class of bug impossible to reintroduce
  # from any caller. When nothing contradicts, pin the prefix's own platform so
  # a solve can never default to the host's.
  local _want; _want="$(env_conda_subdir "${envdir}" 2>/dev/null || true)"
  if [[ -n "${_want}" ]]; then
    if [[ -n "${CONDA_SUBDIR:-}" && "${CONDA_SUBDIR}" != "${_want}" ]]; then
      die "refusing to run conda against ${envdir}: CONDA_SUBDIR=${CONDA_SUBDIR}, but that prefix was built for ${_want}.
       Mixing platforms in one prefix produces binaries that cannot run (this exact
       mismatch built the mixed-architecture kraken env on macOS, 2026-08).
       To rebuild the env for ${CONDA_SUBDIR} instead:  CONDA_SUBDIR=${CONDA_SUBDIR} bin/bdtools install ${TOOL:-<tool>} --fresh"
    fi
    export CONDA_SUBDIR="${_want}"
  fi
  # Was there a working env here BEFORE this step? Two things depend on the answer:
  # whether a failed attempt may delete the prefix (it may not), and whether there
  # is anything worth snapshotting. Exported so with_progress's retry can see it.
  if [[ -x "${envdir}/bin/python" ]]; then
    _ENV_PREEXISTING=1
    snapshot_env "${TOOL:-env}" "${envdir}"
  else
    _ENV_PREEXISTING=0
  fi
  harden_conda_hooks "${envdir}"
  # Scrub the strict-shell options and pre-define the CONDA_BACKUP_* names before
  # handing control to conda. Both are needed, and neither is covered by
  # harden_conda_hooks, which can only patch hooks that ALREADY exist:
  #
  #   * On a fresh `env create` there are no hooks yet. The transaction installs the
  #     clang/gfortran hooks and then runs a post-link script that sources them; the
  #     hook reads $CONDA_BACKUP_CLANGXX with no default, dies under `set -u`, and
  #     conda rolls the transaction back — which DELETES the hooks again. So the
  #     retry starts with nothing to harden and fails identically. That is the loop
  #     seen on macOS: "post-link script failed for package spades-4.3.0",
  #     "deactivate_clangxx_osx-arm64.sh: CONDA_BACKUP_CLANGXX: unbound variable",
  #     twice, then "giving up after 2 attempt(s)".
  #   * `set -u` reaches those scripts only when SHELLOPTS is exported somewhere in
  #     the chain (a profile, a wrapper, a parent process) — verified: with
  #     SHELLOPTS exported a child bash inherits nounset and the hook dies; with it
  #     scrubbed the same hook runs clean. We cannot control every ancestor, so
  #     scrub it here rather than hope.
  #
  # Defining the CONDA_BACKUP_* names as empty is the belt to that braces: a hook
  # that reads one now finds it set, whether or not it was ever patched, whether or
  # not the transaction rolled back, on every platform.
  local -a pre=(env -u SHELLOPTS -u BASHOPTS)
  # Preserve any BASH_ENV the user already had; the prelude below chains to it.
  [[ -n "${BASH_ENV:-}" ]] && pre+=("_BDTOOLS_PREV_BASH_ENV=${BASH_ENV}")
  local v
  for v in ${_CONDA_BACKUP_VARS}; do
    # Mirror the current value when the variable is set, empty when it is not.
    # Built as an ARRAY, not a command substitution: CFLAGS/LDFLAGS/CMAKE_ARGS
    # routinely contain spaces, and unquoted word-splitting would turn the rest of
    # the flags into the command conda was supposed to be.
    if [[ -n "${!v+x}" ]]; then pre+=("CONDA_BACKUP_${v}=${!v}")
    else pre+=("CONDA_BACKUP_${v}="); fi
    # ...and define the PLAIN variable too, which is the half that was missing.
    # The toolchain's ACTIVATE hook only records a backup when the plain variable
    # is defined:
    #     if [ ! -z "${AR+x}" ]; then export CONDA_BACKUP_AR="$AR"; fi
    # With AR undefined it stores nothing, the matching deactivate hook then reads
    # an unset CONDA_BACKUP_AR, and under `set -u` that is the failure that has
    # been killing macOS env builds:
    #     deactivate_cctools_osx-64.sh: line 63: CONDA_BACKUP_AR: unbound variable
    #     LinkError: post-link script failed for package ...::spades-4.3.0...
    # Defining it EMPTY satisfies the `+x` test and cannot point a compiler
    # anywhere: conda's activate hook overwrites it with the env's real path a line
    # later.
    [[ -n "${!v+x}" ]] || pre+=("${v}=")
  done
  # BASH_ENV: the belt that does not depend on any hook's shape.
  #
  # Read conda/utils.py:wrap_subprocess_call — for a package with a post-link
  # script conda writes a temp shell script that does, in order:
  #     conda activate <prefix>
  #     . "<pkg>-post-link.sh"          <-- SOURCED, not executed
  #     . "<prefix>/etc/conda/deactivate.d/<each>.sh"
  # and runs it as `bash <script>`. Because the post-link script is SOURCED, a
  # `set -u` inside it (bioconda's spades has one) stays set for the deactivate
  # hooks sourced afterwards. That is the whole mechanism, and it means scrubbing
  # SHELLOPTS from conda's environment — which is what we shipped first — could
  # never have fixed it: the `set -u` does not come from an ancestor shell, it
  # comes from the package's own script two lines earlier.
  #
  # bash sources $BASH_ENV at the top of a non-interactive script, which is that
  # wrapper. A prelude that defines every name those hooks read makes the unguarded
  # reads legal no matter what set -u is in force and no matter which hook variant
  # the package ships. It only ever defines variables, so it is safe to apply to
  # every script conda runs during the transaction.
  local prelude; prelude="$(_conda_prelude_file)"
  [[ -n "${prelude}" ]] && pre+=("BASH_ENV=${prelude}")
  local rc=0
  "${pre[@]}" "$@" || rc=$?
  # Harden again on the way out: this transaction may have just installed the very
  # hooks that break the NEXT one, and hardening only before a step can never see
  # those. Cheap and idempotent.
  harden_conda_hooks "${envdir}"
  return ${rc}
}

# The toolchain variables whose conda deactivate hooks read $CONDA_BACKUP_<VAR> with
# no default. Over-inclusive on purpose: an unused placeholder costs nothing, while a
# missing one is a rolled-back transaction that cannot self-heal.
# Write (once per run) the BASH_ENV prelude every shell conda starts will source.
# Echoes its path, or nothing if it could not be written — in which case the
# environment-variable belt above still applies.
_conda_prelude_file() {
  local f="${BDTOOLS_HOME}/state/conda-prelude.sh" v
  [[ -s "${f}" && -n "${_CONDA_PRELUDE_WRITTEN:-}" ]] && { printf '%s' "${f}"; return 0; }
  mkdir -p "$(dirname "${f}")" 2>/dev/null || return 0
  {
    echo '# Generated by bdtools (install-local.sh). Sourced via BASH_ENV by every'
    echo '# non-interactive shell conda starts during a transaction, so that the'
    echo '# toolchain activate/deactivate hooks cannot die on an unbound variable'
    echo '# under a `set -u` that a package post-link script turned on.'
    # Chain, never replace: a user with their own BASH_ENV keeps it.
    echo 'if [ -n "${_BDTOOLS_PREV_BASH_ENV:-}" ] && [ -r "${_BDTOOLS_PREV_BASH_ENV}" ]; then'
    echo '  . "${_BDTOOLS_PREV_BASH_ENV}"'
    echo 'fi'
    for v in ${_CONDA_BACKUP_VARS}; do
      # `:=` defines it (possibly empty) without disturbing a real value.
      printf ': "${%s:=}"; export %s\n' "${v}" "${v}"
      printf ': "${CONDA_BACKUP_%s:=}"; export CONDA_BACKUP_%s\n' "${v}" "${v}"
    done
  } > "${f}.tmp" 2>/dev/null || { rm -f "${f}.tmp"; return 0; }
  mv -f "${f}.tmp" "${f}" 2>/dev/null || return 0
  _CONDA_PRELUDE_WRITTEN=1
  printf '%s' "${f}"
}

_CONDA_BACKUP_VARS="CC CXX CPP FC F77 F90 GCC GXX GFORTRAN CLANG CLANGXX
  AR AS RANLIB LD LD_GOLD NM STRIP OBJDUMP OBJCOPY READELF SIZE STRINGS ADDR2LINE
  CFLAGS CXXFLAGS CPPFLAGS FFLAGS LDFLAGS DEBUG_CFLAGS DEBUG_CXXFLAGS DEBUG_FFLAGS
  DEBUG_CPPFLAGS HOST BUILD CONDA_BUILD_SYSROOT CMAKE_ARGS CMAKE_PREFIX_PATH
  MESON_ARGS _CONDA_PYTHON_SYSCONFIGDATA_NAME"

# A leftover ${DIR}/env with no usable python is the corpse of a build that died
# mid-create. Going forward _discard_partial_env_prefix removes those at the
# moment of failure, but corpses predate that fix, and a delegated
# deploy/install.sh that dies can still leave one. `conda env create` refuses an
# existing prefix, and the corpse's conda-meta is exactly what a platform
# decision must never read (the 2026-08 mixed-architecture incident). Set it
# aside rather than delete: this code did not create it, so it is not ours to
# destroy — and the message says when it is safe to remove.
_clear_partial_checkout_env() {
  [[ -d "${DIR}/env" && ! -x "${DIR}/env/bin/python" ]] || return 0
  local aside="${DIR}/env.partial-$(date +%Y%m%d%H%M%S)"
  if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
    echo "  [dry-run] mv ${DIR}/env ${aside}  (leftover partial env, no usable python)"
    return 0
  fi
  if mv "${DIR}/env" "${aside}" 2>/dev/null; then
    warn "found a leftover partial env at ${DIR}/env (no usable python) — moved to ${aside}"
    info "  delete it once the new build works:  rm -rf ${aside}"
  else
    warn "could not move the leftover partial env at ${DIR}/env aside — the create below may refuse the existing prefix"
  fi
  return 0
}

# When --fresh set the old env aside from somewhere OTHER than where the new one
# is built (a legacy named env being replaced by a checkout env), say so — a
# silent relocation reads as a lost environment.
_note_fresh_relocation() {
  [[ -n "${FRESH_ORIG:-}" && "${FRESH_ORIG}" != "${DIR}/env" ]] || return 0
  info "--fresh: the old env was set aside from ${FRESH_ORIG}; the new one is built at ${DIR}/env (launches prefer the checkout env)"
}

# _npm_path — the npm `command -v` resolves, "" when there is none. Split out
# of have_usable_npm so tests can override it with a fabricated path.
_npm_path() { command -v npm 2>/dev/null || true; }

# have_usable_npm — is there an npm on PATH this build can actually use?
#
# On WSL the answer is not "command -v npm succeeded": Windows' Node install
# puts /mnt/c/Program Files/nodejs on PATH via interop, and it ships an
# extensionless `npm` wrapper — so a default WSL session resolves the WINDOWS
# npm. That npm runs Windows node.exe against a Linux-side checkout under
# ~/.local/share/bdtools: it cannot resolve the working directory (a UNC/
# virtual path from Windows' side), breaks on CRLF-vs-LF wrapper scripts, and
# writes Windows-format node_modules — so `npm ci`/`npm run build` fails or
# produces a broken dist, and the existing fallback warning then blames the
# Node VERSION ("vite needs Node >=20.19"), sending the user down the wrong
# path entirely. Same two-userlands confusion the suite already fixed for
# conda. Treat a /mnt/* npm under WSL as "npm not found", loudly, so every
# caller falls back to the committed prebuilt dist instead of half-building.
#
# One helper for all three call sites (generic_build, build_vsnp_local,
# build's --skip-frontend decision) so they cannot drift. The optional
# argument is a kernel-string override for tests; real callers pass nothing.
have_usable_npm() {
  local kernel="${1:-$(_wsl_kernel)}" np
  np="$(_npm_path)"
  [[ -n "${np}" ]] || return 1
  case "${kernel}" in
    *[Mm]icrosoft*)
      if [[ "${np}" == /mnt/* ]]; then
        warn "the npm on PATH is the WINDOWS npm (${np}) — it breaks on CRLF wrappers and writes Windows-format node_modules into a Linux checkout; install Linux node inside WSL (e.g. 'sudo apt install nodejs npm', or nvm) — treating npm as not found."
        return 1
      fi;;
  esac
  return 0
}

generic_build() {
  local conda; conda="$(detect_conda)" || die "conda/mamba not found. Install miniforge first."
  ok "conda: ${conda}"
  local env_file="${DIR}/conda_setup/environment.yml"
  if [[ -x "${DIR}/env/bin/python" ]]; then
    if [[ ${REBUILD} -eq 1 && -f "${env_file}" ]]; then
      # Refresh an existing env from its spec so newly-declared dependencies are
      # installed (a plain build skips when the env exists, which is why a stale
      # env never picked up additions like 'humanize'). conda env update is
      # additive — it won't remove anything the user added.
      with_progress "${TOOL}: updating conda env from spec (--rebuild)" \
        _conda_step "${DIR}/env" "${conda}" env update -p "${DIR}/env" -f "${env_file}"
    else
      ok "env present: ${DIR}/env"
    fi
  elif [[ ${FRESH} -eq 0 && -n "$(resolve_env_prefix)" ]]; then
    # An env this tool already runs from, kept outside the checkout (a shared
    # site env, a sandbox's own conda env). Building a second one here would
    # solve for minutes and then be ignored by every launch, which is what
    # `resolve_python` would still pick. Same reasoning as build_vsnp_local.
    #
    # Skipped under --fresh: "start over" must never resolve to "keep whatever
    # is here and report success". That silent no-op returned 0, reached
    # build_state_ok, and CLEARED the failed-build record — so `bdtools update`
    # then called a build finished that had built nothing (2026-08, macOS).
    ok "using this machine's existing ${TOOL} env: $(resolve_env_prefix)"
    info "  (managed outside the checkout — left as it is)"
    return 0
  elif [[ -f "${env_file}" ]]; then
    _clear_partial_checkout_env
    _note_fresh_relocation
    with_progress "${TOOL}: creating conda env (solve can take several minutes)" \
      _conda_step "${DIR}/env" "${conda}" env create -p "${DIR}/env" -f "${env_file}"
  else
    die "no ${env_file} — cannot build env"
  fi
  harden_conda_hooks "${DIR}/env"   # hooks this build installed: fix them for next time
  ensure_env_java "${DIR}/env"
  if [[ -f "${DIR}/backend/requirements.txt" ]]; then
    log "pip install backend requirements"
    # The pip layer is arch-pinned like the launch (see arch_prefix): pip
    # selects — and, for sdists, compiles — wheels for the RUNNING
    # interpreter's architecture, so a universal env python started from a
    # translated ancestor (dashboard Update button -> bdtools update -> here)
    # quietly fills the env with wrong-arch extension modules. conda-meta
    # records stay clean, so the arch audit cannot see the damage, and the
    # loader smoke test covers conda-declared binaries, not pip's. Array +
    # ${arr[@]+...} guard: bash 3.2 (macOS /bin/bash) treats an empty array
    # expansion as unbound under `set -u`.
    local _pip_archp _pip_arch=()
    _pip_archp="$(arch_prefix "${DIR}/env")"
    [[ -n "${_pip_archp}" ]] && read -ra _pip_arch <<< "${_pip_archp}"
    run ${_pip_arch[@]+"${_pip_arch[@]}"} "${DIR}/env/bin/python" -m pip install -r "${DIR}/backend/requirements.txt"
  fi
  if [[ -d "${DIR}/frontend" ]]; then
    # Rebuild the frontend whenever npm is available so a tool update actually
    # ships its new UI. dist/ IS committed (a prebuilt fallback for Node-less
    # hosts), so a stale committed dist is exactly what a skipped build would
    # leave serving — rebuild rather than trust it. Only fall back to the
    # committed dist when Node is genuinely absent or too old.
    if have_usable_npm; then
      log "building frontend"
      # Non-fatal: a build failure (e.g. a Node older than the tool's vite needs
      # — vite 8 wants Node >=20.19) must not brick the tool when a committed
      # prebuilt dist exists. Fall back to that dist with a loud warning; only
      # hard-fail if there's nothing to serve.
      if ! ( cd "${DIR}/frontend" && { run npm ci || run npm install; run npm run build; } ); then
        if [[ -f "${DIR}/frontend/dist/index.html" ]]; then
          warn "frontend build failed — falling back to the committed prebuilt dist. Check Node (vite needs Node >=20.19; try 'module load nodejs')."
        else
          die "frontend build failed and there is no prebuilt dist to fall back to (need Node >=20.19 for the frontend build)"
        fi
      fi
    elif [[ ! -f "${DIR}/frontend/dist/index.html" ]]; then
      warn "npm not found and no prebuilt dist — frontend not built"
    else
      warn "npm not found — keeping the existing frontend build (it may be stale)"
    fi
  fi
}

# resolve_env_prefix — the env prefix this tool runs from and this build
# operates on, or "" when it has none yet. THE suite-side resolver, singular on
# purpose: the subdir decision (ensure_conda_subdir), every mutation
# (enforce_package_pins, enforce_env_constraints, generic_build,
# build_vsnp_local) and the launch (resolve_python) all read this one answer.
# Precedence: <checkout>/env wins, else the conda env NAMED by the manifest's
# `env:` — asked of conda itself, never guessed from a base path.
#
# Two rules, both with a history:
#
# * ONE resolver for deciding and for mutating. Until 2026-08 the subdir
#   decision read the env through a SEPARATE resolver whose in-checkout test was
#   `-d env/conda-meta` while the mutations required `-x env/bin/python`. A
#   half-built ${DIR}/env — conda-meta but no python, what a dead `conda env
#   create` leaves — satisfied one and failed the other, so the build read
#   CONDA_SUBDIR=osx-64 off the corpse and ran its installs against the real
#   osx-arm64 NAMED env. One foreign openssl later, kraken_id_parse_gui was a
#   mixed-architecture env that no additive operation could repair. Two
#   resolvers answering "which env" is the whole defect; do not add another.
#
# * A usable env means `-x bin/python`, never merely a directory: a prefix
#   without a python can neither launch the tool nor anchor a platform
#   decision, and treating one as an env is how the corpse above got a vote.
#
# Adopting an existing EXTERNAL env (rather than building a second one) is also
# load-bearing: `bdtools update` on a deployment whose env lives outside the
# checkout (an OOD sandbox env, a shared site env) used to see no <checkout>/env,
# decide the tool had no environment, and start a multi-minute solve for an env
# nothing would ever launch — one HPC ran that for 46 minutes before it was
# killed as a runaway, all to create an env every launch would then ignore.
resolve_env_prefix() {
  if [[ -x "${DIR}/env/bin/python" ]]; then echo "${DIR}/env"; return 0; fi
  local conda p; conda="$(detect_conda 2>/dev/null || true)"
  [[ -n "${conda}" && -n "${ENV_NAME}" ]] || return 0
  "${conda}" env list 2>/dev/null | awk '{print $1}' | grep -qxF "${ENV_NAME}" || return 0
  p="$("${conda}" run -n "${ENV_NAME}" sh -c 'echo $CONDA_PREFIX' 2>/dev/null)"
  [[ -n "${p}" && -x "${p}/bin/python" ]] && echo "${p}"
  return 0
}

# Decide the conda platform (subdir) every solve in this build must target.
#
# Rule 1 — an EXISTING env wins, on every platform. Its architecture was fixed
# when it was created, so an update has to keep solving for that same subdir; a
# mixed-architecture prefix is at best a post-link failure and at worst a tool
# that dies mid-analysis. This is not hypothetical: an env built osx-64 (rule 2)
# was later updated by an osx-arm64 solve on the same Mac, because nothing
# re-derived the subdir from the env.
#
# Rule 2 — a FRESH env on Apple Silicon (macOS arm64) is built osx-64 under
# Rosetta 2. Much of the bioinformatics closure has no native osx-arm64 build —
# IRMA needs `blat`, shovill pulls spades/mash/skesa — so a native solve fails or
# resolves a partial toolchain. The mature osx-64 set resolves cleanly.
#
# Either way CONDA_SUBDIR is exported, so each tool's deploy/install.sh inherits
# it. Opt out of rule 2 with BDTOOLS_NATIVE_ARM=1 (expect solve failures) or by
# pre-setting CONDA_SUBDIR; rule 1 always wins over both, because it describes
# what is already on disk rather than a preference.
ensure_conda_subdir() {
  local envdir existing host
  # The SAME resolver every mutation reads (see resolve_env_prefix): the subdir
  # is pinned from the env the installs will actually target, so the two can
  # never disagree about which env that is.
  envdir="$(resolve_env_prefix)"
  existing="$(env_conda_subdir "${envdir}")"
  if [[ -n "${existing}" ]]; then
    if [[ -n "${CONDA_SUBDIR:-}" && "${CONDA_SUBDIR}" != "${existing}" ]]; then
      warn "CONDA_SUBDIR=${CONDA_SUBDIR}, but ${envdir} was built for ${existing} — solving for ${existing}."
      info "  An env's architecture is fixed at creation; updating it for another platform mixes binaries."
      info "  To move this tool to ${CONDA_SUBDIR}:  CONDA_SUBDIR=${CONDA_SUBDIR} bin/bdtools install ${TOOL} --fresh"
    fi
    export CONDA_SUBDIR="${existing}"
    ok "env platform: ${existing} (pinned from the existing env, not the host)"
    # Damage already done by an earlier mixed-platform update: packages from
    # another architecture are linked into this prefix. Pinning stops it getting
    # worse, but those binaries still cannot run — only a rebuild clears them.
    local foreign; foreign="$(env_foreign_subdirs "${envdir}")"
    if [[ -n "${foreign}" ]]; then
      warn "${envdir} is a MIXED-architecture env: $(printf '%s' "${foreign}" | tr '\n' ';') package(s) are not ${existing}."
      info "  Those binaries cannot run in an ${existing} prefix, and updating cannot remove them."
      info "  Rebuild it:  bin/bdtools install ${TOOL} --fresh"
    fi
    # An env from another machine/arch cannot run here at all. Say so now, at
    # install time, instead of leaving "Bad CPU type"/missing-symbol errors to
    # surface mid-analysis. osx-64 on Apple Silicon is the one expected mismatch
    # (rule 2 — Rosetta runs it).
    host="$(host_conda_subdir)"
    if [[ -n "${host}" && "${existing}" != "${host}" ]] \
       && ! [[ "${host}" == "osx-arm64" && "${existing}" == "osx-64" ]]; then
      warn "${envdir} was built for ${existing}, but this machine is ${host} — that env cannot run here."
      info "  Rebuild it for this machine:  bin/bdtools install ${TOOL} --fresh"
    fi
    return 0
  fi
  [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || return 0
  [[ -n "${CONDA_SUBDIR:-}" ]] && { info "CONDA_SUBDIR preset to ${CONDA_SUBDIR} — honoring it."; return 0; }
  [[ "${BDTOOLS_NATIVE_ARM:-0}" == "1" ]] && {
    warn "BDTOOLS_NATIVE_ARM=1 — attempting a native osx-arm64 env; bioconda lacks arm64 builds for the assembler/blat toolchain, so expect a solve failure."
    return 0; }
  if ! /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
    die "Apple Silicon detected, but Rosetta 2 is not installed. These tools' bioinformatics dependencies have no native arm64 build, so the conda env must be x86-64 under Rosetta. Install it once with:
    softwareupdate --install-rosetta --agree-to-license
then re-run this install."
  fi
  export CONDA_SUBDIR=osx-64
  ok "Apple Silicon: building the conda env as osx-64 under Rosetta 2 (native arm64 bioconda builds are incomplete). Override with BDTOOLS_NATIVE_ARM=1."
}

# vsnp_gui is special: no environment.yml / deploy/install.sh. Its env is the
# bioconda `vsnp3` package + a web layer + Kapur Lab patches, and it needs the
# USDA-VS reference_options (the conda package already ships the sourmash
# best-reference index). Build all of that locally so it runs standalone.
VSNP_REFS_REPO="https://github.com/USDA-VS/vSNP_reference_options.git"
build_vsnp_local() {
  local conda; conda="$(detect_conda)" || die "conda/mamba not found. Install miniforge first."
  ok "conda: ${conda}"
  # 1. vsnp3 env (+ snp-dists for Step 2). CONDA_SUBDIR=osx-64 already exported on
  #    Apple Silicon by ensure_conda_subdir, so this runs under Rosetta there.
  #    ENVP is the env that will actually run this tool: <checkout>/env, or an
  #    existing external one (see resolve_env_prefix). Everything below targets
  #    ENVP, so an update refreshes the env in use rather than provisioning a
  #    parallel one nothing launches.
  local ENVP; ENVP="$(resolve_env_prefix)"
  if [[ ${FRESH} -eq 1 && -n "${ENVP}" && "${ENVP}" != "${DIR}/env" ]]; then
    # --fresh means START OVER. An external env the resolver still finds was not
    # (or could not be) set aside — adopting it would quietly turn --fresh into
    # a refresh of the very env the user asked to replace (generic_build makes
    # the same argument for its skip branch).
    info "--fresh: not adopting the external env at ${ENVP} — building a new one at ${DIR}/env"
    ENVP=""
  fi
  if [[ -n "${ENVP}" ]]; then
    if [[ "${ENVP}" == "${DIR}/env" ]]; then
      ok "env present: ${ENVP}"
    else
      ok "using this machine's existing ${TOOL} env: ${ENVP}"
      info "  (managed outside the checkout — refreshing it in place, not rebuilding)"
    fi
  else
    ENVP="${DIR}/env"
    _clear_partial_checkout_env
    _note_fresh_relocation
    # Create the env AT the manifest pins, not open-ended.
    #
    # `conda create ... vsnp3 snp-dists` looks harmless and is not: with no
    # version asked for, the solver is free to take the newest python and then
    # back-solve vsnp3 to whatever still fits. vsnp3 3.35 needs python <=3.12
    # and 3.36 needs <3.14, but 3.16 (2023) declares only python >=3.8 — so on
    # a machine where python 3.14 is current, the "successful" install produced
    # vsnp3 3.16 while tools.yml pinned 3.35. enforce_package_pins then could
    # not repair it (installing 3.35 into a python-3.14 env is unsatisfiable),
    # warned, and returned 0, so the install reported success and the OOD
    # deployment ran a 19-release-old analysis package. That is precisely the
    # reproducibility hole the pins exist to close.
    #
    # Asking for the pinned version instead makes the package's own python
    # constraint drive the solve, so the env is right the first time and
    # enforce_package_pins becomes the confirmation it was meant to be. Falls
    # back to the open specs when the manifest has no pins.
    local create_specs=() _s _p
    for _s in $(manifest_get "${TOOL}" packages 2>/dev/null || true); do
      _p="${_s##*::}"                      # drop the channel: bioconda::vsnp3=3.35
      [[ -n "${_p}" ]] && create_specs+=("${_p}")
    done
    [[ ${#create_specs[@]} -gt 0 ]] || create_specs=(vsnp3 snp-dists)
    with_progress "${TOOL}: creating vsnp3 env (${create_specs[*]}; solve can take several minutes)" \
      _conda_step "${ENVP}" "${conda}" create -y -p "${ENVP}" -c conda-forge -c bioconda "${create_specs[@]}"
  fi
  harden_conda_hooks "${ENVP}"
  # 2. web layer (uvicorn is served from this same python). Arch-pinned like
  #    generic_build's pip step and for the same reason: pip keys wheel
  #    selection off the RUNNING interpreter's architecture, so a translated
  #    ancestor poisons the pip layer invisibly to conda records.
  local _vpip_archp _vpip_arch=()
  _vpip_archp="$(arch_prefix "${ENVP}")"
  [[ -n "${_vpip_archp}" ]] && read -ra _vpip_arch <<< "${_vpip_archp}"
  [[ -x "${ENVP}/bin/pip" ]] && run ${_vpip_arch[@]+"${_vpip_arch[@]}"} "${ENVP}/bin/pip" install --upgrade \
      fastapi uvicorn pydantic python-multipart aiofiles
  # 3. Kapur Lab vsnp3 patches (idempotent; safe on the packaged version)
  [[ -x "${DIR}/deploy/vsnp3-patches/apply.sh" ]] && \
    { run "${DIR}/deploy/vsnp3-patches/apply.sh" "${ENVP}" || warn "vsnp3 patch step reported an issue (continuing)"; }
  # 4. reference_options (USDA-VS) + register the path vsnp3 reads at runtime.
  #    Prefer a database-setup-managed reference set (bdtools setup-databases
  #    writes BDTOOLS_HOME/db-root) so we don't clone a second copy; otherwise
  #    fall back to a vsnp_gui-private clone.
  local refs db_root="" vsnp_deps="" refs_mode="supplemental"
  [[ -f "${BDTOOLS_HOME}/db-root" ]] && db_root="$(cat "${BDTOOLS_HOME}/db-root" 2>/dev/null || true)"
  if [[ -n "${db_root}" && -n "$(ls -A "${db_root}/vsnp3/reference_options" 2>/dev/null)" ]]; then
    refs="${db_root}/vsnp3/reference_options"
    refs_mode="shared"   # database-setup set is authoritative — expose it whole
    ok "using database-setup reference options: ${refs}"
    [[ -d "${db_root}/vsnp3/vsnp_dependencies" ]] && vsnp_deps="${db_root}/vsnp3/vsnp_dependencies"
  else
    # Fallback: a vsnp_gui-private USDA-VS clone used as a CACHE. We don't expose
    # it wholesale — the site block below registers ONLY the references that
    # aren't already available via the locations vsnp3 already knows about (what
    # `vsnp3_path_adder.py -s` lists), so a shared set the user has registered
    # (e.g. /srv/kapurlab/refs/vsnp3/reference_options) is never duplicated.
    refs="${BDTOOLS_HOME}/vsnp3-refs/vSNP_reference_options"
    if [[ -n "$(ls -A "${refs}" 2>/dev/null)" ]]; then
      ok "reference options cache present: ${refs}"
    else
      log "downloading vSNP reference options (USDA-VS) -> ${refs}"
      run mkdir -p "$(dirname "${refs}")"
      # Line-ending pin as on every managed clone (see ensure_checkout): a
      # global autocrlf=true on WSL would CRLF-corrupt the reference fastas.
      run git clone --config core.autocrlf=false --config core.eol=lf --depth 1 "${VSNP_REFS_REPO}" "${refs}"
    fi
  fi
  # 4b. Local "site root" so the GUI backend (config.py keys everything off
  #     VSNP_GUI_SITE_ROOT, default /srv/kapurlab) resolves the reference root,
  #     vsnp3 env path, and VCF-db root to LOCAL locations. Without this the GUI
  #     looks under /srv/kapurlab/refs/... and Step 1 fails ("reference folder
  #     not found"). launch() exports VSNP_GUI_SITE_ROOT to this tree.
  if [[ ${DRY_RUN} -eq 0 ]]; then
    local site="${BDTOOLS_HOME}/vsnp3-site"
    mkdir -p "${site}/refs/vsnp3/vcf_db_folders" "${site}/tools" "${site}/projects" "${site}/audit"
    ln -sfn "${ENVP}" "${site}/tools/vsnp3"                     # GUI vsnp3_path
    # Kraken ID Parse is a sibling tool the vSNP backend shells out to from Step 1.
    # Link the CHECKOUT dir here — NOT its env like vsnp3 above — because the
    # backend appends /bin/kraken_id_parse.py and /env/bin/python to this path
    # itself (_resolve_kraken_runtime). Guarded so a vsnp-only install (kraken not
    # checked out yet) doesn't fail; launch() self-heals the link either way.
    local kdir; kdir="$(tool_dir kraken_id_parse_gui)"
    [[ -d "${kdir}" ]] && ln -sfn "${kdir}" "${site}/tools/kraken_id_parse_gui"
    # vSNP's embedded Kraken/BLAST runner defaults its DBs to SITE_ROOT/databases/...
    # (the server layout). Locally the DBs live at BDTOOLS_HOME/db-root, so adopt it
    # here — otherwise "Kraken + Krona" fails with "does not contain necessary file
    # taxo.k2d". (db_root resolved above from BDTOOLS_HOME/db-root.)
    [[ -n "${db_root}" && -d "${db_root}" ]] && ln -sfn "${db_root}" "${site}/databases"
    local rop="${site}/tools/vsnp3/dependencies/reference_options_paths.txt"
    local refpath="${site}/refs/vsnp3/reference_options"
    mkdir -p "${site}/tools/vsnp3/dependencies"
    touch "${rop}"
    if [[ "${refs_mode}" == "shared" ]]; then
      # Authoritative shared set — expose it whole (symlink) and register it.
      ln -sfn "${refs}" "${refpath}"
      registry_add_line "${rop}" "${refpath}"
    else
      # Supplemental: expose ONLY references not already available elsewhere.
      # "Already available" = every reference subdir name reachable from the
      # other paths currently registered (i.e. what `vsnp3_path_adder.py -s`
      # lists), so e.g. a user-registered /srv/kapurlab/refs set is not
      # duplicated. Missing ones are symlinked into refpath from the cache.
      local already; already="$(
        while IFS= read -r p; do
          [[ -z "$p" ]] && continue
          [[ "$p" == "${refpath}" ]] && continue     # ignore our own managed dir
          [[ -d "$p" ]] || continue
          for d in "$p"/*/; do [[ -d "$d" ]] && basename "$d"; done
        done < "${rop}" | sort -u
      )"
      # Rebuild refpath as a real dir of symlinks (it may currently be a symlink
      # to the full cache from an older install — replace it, don't follow it).
      rm -rf "${refpath}"
      mkdir -p "${refpath}"
      local added=0 name
      for d in "${refs}"/*/; do
        [[ -d "$d" ]] || continue
        name="$(basename "$d")"
        grep -qxF "${name}" <<< "${already}" && continue   # already available → skip
        ln -sfn "$d" "${refpath}/${name}"
        added=$((added+1))
      done
      if [[ ${added} -gt 0 ]]; then
        registry_add_line "${rop}" "${refpath}"
        ok "added ${added} supplemental reference(s) not already available -> ${refpath}"
      else
        # Nothing new to contribute — don't leave a redundant registration behind.
        registry_remove_line "${rop}" "${refpath}"
        ok "all USDA-VS references already available (per vsnp3_path_adder.py -s); none added"
      fi
    fi
    # The USDA vsnp_dependencies set (when database-setup provided it) mostly
    # DUPLICATES the reference_options set registered above — registering it
    # whole used to double every entry in the GUI's reference dropdowns. Expose
    # only what it uniquely contributes (e.g. mtbc0_v1.1): a managed dir of
    # symlinks to the names not already reachable via the other registered
    # locations, registered only when non-empty. Rebuilt on every install run,
    # so it tracks upstream additions and never goes stale. A pre-existing
    # whole-dir registration (an older install, or a deliberate user choice in
    # the GUI's Reference Locations editor) is left alone — in that case every
    # deps name is already reachable and this contributes nothing.
    if [[ -n "${vsnp_deps}" ]]; then
      local deps_extra="${site}/refs/vsnp3/vsnp_dependencies_extra"
      local deps_already; deps_already="$(
        while IFS= read -r p; do
          [[ -z "$p" ]] && continue
          [[ "$p" == "${deps_extra}" ]] && continue   # ignore our own managed dir
          [[ -d "$p" ]] || continue
          for d in "$p"/*/; do [[ -d "$d" ]] && basename "$d"; done
        done < "${rop}" | sort -u
      )"
      rm -rf "${deps_extra}"
      mkdir -p "${deps_extra}"
      local deps_added=0 dname
      for d in "${vsnp_deps}"/*/; do
        [[ -d "$d" ]] || continue
        dname="$(basename "$d")"
        [[ "${dname}" == .* ]] && continue                        # .git is not a reference
        compgen -G "${d}*.fasta" >/dev/null || compgen -G "${d}*.xlsx" >/dev/null || continue
        grep -qxF "${dname}" <<< "${deps_already}" && continue    # already available → skip
        ln -sfn "${d%/}" "${deps_extra}/${dname}"
        deps_added=$((deps_added+1))
      done
      if [[ ${deps_added} -gt 0 ]]; then
        registry_add_line "${rop}" "${deps_extra}"
        ok "added ${deps_added} reference(s) unique to vsnp_dependencies -> ${deps_extra}"
      else
        registry_remove_line "${rop}" "${deps_extra}"
        rmdir "${deps_extra}" 2>/dev/null || true
        ok "vsnp_dependencies adds no references not already available; none registered"
      fi
    fi
    ok "configured local vsnp site: ${site} (references + vcf_db_folders + env link)"
    # Step 2's curated VCF comparison databases (kapurlab/vcf_db_directories).
    # One-time seed — see common.sh; an admin's later removals/additions win.
    seed_vcf_db_directories "${site}/refs/vsnp3/vcf_db_folders" "${BDTOOLS_HOME}"
    # stable in-checkout pointer so the validation harness can find the refs
    [[ -e "${DIR}/vSNP_reference_options" ]] || ln -s "${refs}" "${DIR}/vSNP_reference_options" 2>/dev/null || true
  fi
  # 5. frontend
  if [[ -d "${DIR}/frontend" ]]; then
    # Rebuild the frontend whenever npm is available so a tool update actually
    # ships its new UI. dist/ IS committed (a prebuilt fallback for Node-less
    # hosts), so a stale committed dist is exactly what a skipped build would
    # leave serving — rebuild rather than trust it. Only fall back to the
    # committed dist when Node is genuinely absent or too old.
    if have_usable_npm; then
      log "building frontend"
      # Non-fatal: a build failure (e.g. a Node older than the tool's vite needs
      # — vite 8 wants Node >=20.19) must not brick the tool when a committed
      # prebuilt dist exists. Fall back to that dist with a loud warning; only
      # hard-fail if there's nothing to serve.
      if ! ( cd "${DIR}/frontend" && { run npm ci || run npm install; run npm run build; } ); then
        if [[ -f "${DIR}/frontend/dist/index.html" ]]; then
          warn "frontend build failed — falling back to the committed prebuilt dist. Check Node (vite needs Node >=20.19; try 'module load nodejs')."
        else
          die "frontend build failed and there is no prebuilt dist to fall back to (need Node >=20.19 for the frontend build)"
        fi
      fi
    elif [[ ! -f "${DIR}/frontend/dist/index.html" ]]; then
      warn "npm not found and no prebuilt dist — frontend not built"
    else
      warn "npm not found — keeping the existing frontend build (it may be stale)"
    fi
  fi
}

# --fresh — start this tool's env over from nothing.
#
# The single command to hand someone whose env is wrong in a way no update can
# repair: a mixed-architecture prefix, a python swapped out from under the pip
# layer, a half-linked transaction, a package set nobody can account for. Those
# all share one property — something is PRESENT that should not be — and every
# other mode here is additive. `--rebuild` is `conda env update`, which by
# design cannot remove anything; enforce_package_pins moves versions but not
# platforms. Up to now the answer was a two-part recipe (`rm -rf <env> &&
# bdtools install <tool>`) that nobody should be asked to type: get the path
# wrong and you delete the wrong env, and if the build then fails you are left
# with nothing at all. That is also why plain `install` refuses to touch an env
# that already exists.
#
# So this does not delete anything. It snapshots the package set, MOVES the
# prefix aside on the same filesystem — instant, and reversible — and builds from
# nothing. A conda env is not relocatable, but the copy is never run from its new
# path; it is only ever moved BACK, restoring the original prefix byte for byte.
# A failed build puts it back automatically. A build that passes its self-check
# removes it, and the snapshot stays behind either way, so `bdtools restore-env`
# can still name every package that was there.
#
# On Apple Silicon this is also the only way to fix a platform mistake: the
# subdir is decided when an env is created (ensure_conda_subdir), and with the
# old prefix gone, rule 2 gets to make that decision again from scratch.
FRESH_ASIDE=""; FRESH_ORIG=""
discard_env_for_fresh() {
  [[ ${FRESH} -eq 1 ]] || return 0
  local envdir; envdir="$(tool_env_prefix "${TOOL}" 2>/dev/null || true)"
  if [[ -z "${envdir}" || ! -d "${envdir}" ]]; then
    ok "--fresh: ${TOOL} has no env here yet — building a new one"
    return 0
  fi
  # Resolved through tool_env_python, so this is the env the tool actually LAUNCHES
  # with, which on a shared install can be one somebody else owns. Not ours to move.
  [[ -w "$(dirname "${envdir}")" && -w "${envdir}" ]]     || die "--fresh: ${envdir} is not yours to replace (no write permission).
       That is the env ${TOOL} runs from. Ask whoever owns it, or install your own copy."
  log "--fresh: replacing ${envdir}"
  snapshot_env "${TOOL}" "${envdir}"
  local aside="${envdir}.bdtools-old-$(date +%Y%m%d%H%M%S)"
  if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
    echo "  [dry-run] mv ${envdir} ${aside}"
    echo "  [dry-run] build a new env at ${envdir}, then remove ${aside}"
    return 0
  fi
  mv "${envdir}" "${aside}" \
    || die "--fresh: could not move ${envdir} aside. Is the tool still running? Stop it and retry."
  FRESH_ORIG="${envdir}"; FRESH_ASIDE="${aside}"
  ok "--fresh: old env set aside — it goes back automatically if this build fails"
}

# Put it back. Called from the build's EXIT trap, so it covers `die` too.
restore_env_from_fresh() {
  [[ -n "${FRESH_ASIDE}" && -d "${FRESH_ASIDE}" ]] || return 0
  rm -rf "${FRESH_ORIG}" 2>/dev/null || true
  if mv "${FRESH_ASIDE}" "${FRESH_ORIG}"; then
    warn "--fresh: build failed — the previous env has been put back at ${FRESH_ORIG}"
    info "  It is exactly what it was, so the tool should run as it did before."
  else
    warn "--fresh: build failed AND the old env could not be moved back."
    info "  It is intact at ${FRESH_ASIDE} — restore it with:"
    info "      mv ${FRESH_ASIDE} ${FRESH_ORIG}"
  fi
  FRESH_ASIDE=""
}

build() {
  discard_env_for_fresh
  ensure_conda_subdir
  # Guard the existing env's activation hooks before anything runs conda —
  # including a delegated deploy/install.sh, whose own conda calls hit the same
  # upstream CONDA_BACKUP_* bug (see harden_conda_hooks).
  harden_conda_hooks "$(resolve_env_prefix)"
  if [[ -x "${DIR}/deploy/install.sh" ]]; then
    log "delegating env+frontend build to ${TOOL}/deploy/install.sh"
    local args=(); [[ ${DRY_RUN} -eq 1 ]] && args+=(--dry-run)
    # Prefer a personal/standalone env if the tool's installer supports it.
    if grep -q -- '--personal' "${DIR}/deploy/install.sh" 2>/dev/null; then args+=(--personal); fi
    # Tell the tool installer where conda lives. Its own default is ~/miniforge3
    # and it can't see the `conda` shell function from this subprocess, so on a
    # box with miniconda3 (or any non-default base) it would die "conda not
    # found". We already resolved a real base for our own steps — pass it through
    # when the installer accepts --conda-base.
    if grep -q -- '--conda-base' "${DIR}/deploy/install.sh" 2>/dev/null; then
      local _cbase; _cbase="$(conda_base_dir)"
      [[ -n "${_cbase}" ]] && args+=(--conda-base "${_cbase}")
    fi
    # Let the tool installer rebuild the frontend when npm is available, so a
    # tool update actually ships its new UI (dist/ is gitignored, so a stale
    # dist from the previous install would otherwise be kept). Only skip the
    # frontend build — keeping the existing dist — when Node is absent, which is
    # also where the tool installers hard-fail if asked to build.
    if [[ -f "${DIR}/frontend/dist/index.html" ]] \
       && ! have_usable_npm \
       && grep -q -- '--skip-frontend' "${DIR}/deploy/install.sh" 2>/dev/null; then
      args+=(--skip-frontend)
    fi
    # kSNP4 is not a conda package: SourceForge publishes a Linux package (ELF,
    # ~545 MB) and a Mac package (Mach-O, ~1.0 GB). This used to skip the download
    # entirely off Linux, on the belief that kSNP4 was Linux-only — it is not, and
    # macOS users were left with a GUI that could not analyse anything. The tool's
    # own deploy/install.sh now picks the package for the host, so just let it run.
    # Only genuinely unsupported platforms skip.
    case "$(uname -s)" in
      Linux|Darwin) ;;
      *)
        if grep -q -- '--skip-ksnp' "${DIR}/deploy/install.sh" 2>/dev/null; then
          warn "${TOOL}: skipping the kSNP4 download — no kSNP4.1 package is published for $(uname -s)."
          args+=(--skip-ksnp)
        fi;;
    esac
    # Hand the tool's installer a ready-to-splice architecture pin for anything
    # it runs FROM the env it builds. CONDA_SUBDIR (exported by
    # ensure_conda_subdir above) makes its conda SOLVES target the right
    # platform, but says nothing about how macOS picks the slice of a universal
    # binary: pip steps, version probes, post-install smoke tests, and
    # vendored-payload unpackers inside deploy/install.sh all execute env
    # interpreters under the CALLER's inherited preference — the suite cannot
    # see inside these scripts to pin them itself (the 2026-08-22 incident
    # class, one delegation away). Tool authors: prepend
    # ${BDTOOLS_ARCH_PREFIX} (unquoted — it is empty or "/usr/bin/arch -<sub>",
    # no other spaces) to commands that run an INTERPRETER OR WRAPPER out of
    # the env being built (python, perl, bash scripts). Never splice it
    # directly onto an individual tool binary: arch ENFORCES, so a thin
    # foreign-arch binary that would run via Rosetta dies under it — the
    # preference an interpreter passes down to its children is the safe
    # mechanism, enforcement on leaves is not. Empty when there is nothing to
    # pin (Linux, no env yet, noarch-only env), so splicing costs nothing.
    BDTOOLS_ARCH_PREFIX="$(arch_prefix "$(resolve_env_prefix)")"
    export BDTOOLS_ARCH_PREFIX
    with_progress "${TOOL}: building env + frontend (deploy/install.sh)" \
      "${DIR}/deploy/install.sh" ${args[@]+"${args[@]}"} || die "${TOOL} deploy/install.sh failed"
  elif [[ -f "${DIR}/conda_setup/environment.yml" ]]; then
    log "no deploy/install.sh in ${TOOL}; using generic build"
    generic_build
  elif [[ -x "${DIR}/deploy/vsnp3-patches/apply.sh" ]]; then
    # vsnp_gui: bioconda vsnp3 + web layer + patches + USDA reference_options.
    log "building ${TOOL} locally (vsnp3 conda package + reference options)"
    build_vsnp_local
  else
    # Not an error: some tools have no local-build path — skip cleanly with a
    # sentinel exit so `install all` isn't marked failed.
    warn "${TOOL} has no local-build path — its conda env and reference databases are provisioned by its OOD installer, not in local mode."
    info "  Run it on an OOD deployment: 'bdtools install --sandbox ${TOOL}' (user) or '--server' (admin)."
    exit 3
  fi
  enforce_package_pins
  enforce_env_constraints
}

# _version_ge A B — is version A >= version B?  (numeric-aware, no dependencies)
#
# `2025.5.1` vs `2024.8` cannot be compared as strings ("10" < "9") and sort -V
# is GNU-only, so it is absent on macOS and on some minimal OOD images. Delegate
# to the python that already reads tools.yml: available everywhere by definition,
# because nothing here runs without it.
_version_ge() {
  "${PYBIN}" -c '
import sys
def key(v):
    out = []
    for part in v.replace("-", ".").replace("_", ".").split("."):
        out.append((0, int(part), "") if part.isdigit() else (1, 0, part))
    return out
sys.exit(0 if key(sys.argv[1]) >= key(sys.argv[2]) else 1)
' "$1" "$2" 2>/dev/null
}

# The manifest's `constraints:` floors, applied to the env we just built.
#
# WHY THIS EXISTS. `packages:` states the versions a diagnostic result depends
# on. This states the versions the env merely has to be ABLE TO RUN — library
# floors that each tool's own environment.yml leaves open, where the open
# version is not a free upgrade but a latent break. The case it was written for
# (2026-08-21, macOS, but nothing about it is macOS-specific): a --fresh
# kraken_id_parse_gui solved to numpy 2.2.6 with dask 2023.3.0, and `import
# allel` died inside dask on numpy 2's removal of `np.round_`. Every package in
# `packages:` was present and correct, so nothing here noticed, and since the
# solve is deterministic per spec+channel a rebuild landed in the same place.
#
# A FLOOR, NOT A PIN, and that difference is the point: `dask>=2024.8` is
# satisfied by anything newer, so a machine that already solved to a good
# version is left completely alone — no solve, no download, no divergence
# between two machines that are both fine. Only an env that is actually below
# the floor is touched. Same cheap conda-meta guard as enforce_package_pins for
# the same reason: a satisfied `conda install` still costs a full solve, and
# minutes on every build is how a safety check gets removed.
#
# Never fatal. A floor that cannot be met is a loud warning on an otherwise
# working install — doctor will name the resulting import failure precisely
# (bin/lib/check.py), which is the backstop for exactly this case.
enforce_env_constraints() {
  local specs; specs="$(manifest_get "${TOOL}" constraints 2>/dev/null || true)"
  [[ -n "${specs}" ]] || return 0
  local envdir; envdir="$(resolve_env_prefix)"
  [[ -n "${envdir}" && -x "${envdir}/bin/python" ]] || return 0

  local wanted=() spec pkg req have
  for spec in ${specs}; do
    # Only `pkg>=version` is supported, deliberately: a floor is the one
    # constraint shape that cannot make two machines disagree about what is
    # installed. Anything else is skipped loudly rather than half-honoured.
    if [[ "${spec}" != *">="* ]]; then
      warn "${TOOL}: ignoring constraint '${spec}' — only 'package>=version' floors are supported"
      continue
    fi
    pkg="${spec%%>=*}"; req="${spec##*>=}"
    have="$(ls "${envdir}/conda-meta" 2>/dev/null \
            | sed -n "s/^${pkg}-\([^-]*\)-[^-]*\.json$/\1/p" | head -1)"
    if [[ -z "${have}" ]]; then
      # Not installed at all. Not this function's job to add packages the tool
      # never asked for — the env simply does not use it.
      continue
    fi
    if _version_ge "${have}" "${req}"; then
      ok "${TOOL}: ${pkg} ${have} (>= ${req})"
    else
      info "  ${TOOL}: ${pkg} ${have} is below the ${req} floor this env needs"
      wanted+=("${spec}")
    fi
  done
  [[ ${#wanted[@]} -gt 0 ]] || return 0

  local conda; conda="$(detect_conda 2>/dev/null || true)"
  if [[ -z "${conda}" ]]; then
    warn "${TOOL}: cannot enforce constraints (${wanted[*]}) — conda not found"
    return 0
  fi
  # Solve for THIS prefix's platform, stated on the command itself — never
  # inherited from the ambient environment. check.py::_conda_cmd has always done
  # this for the remedies it PRINTS; the command that RUNS must match it: an
  # inherited CONDA_SUBDIR (2026-08, read off a leftover half-built prefix) is
  # how one osx-64 openssl got linked into this tool's osx-arm64 env.
  # _conda_step additionally refuses a contradictory ambient subdir outright.
  local sub; sub="$(env_conda_subdir "${envdir}" 2>/dev/null || true)"
  local subdir_env=(); [[ -n "${sub}" ]] && subdir_env=(env "CONDA_SUBDIR=${sub}")
  with_progress "${TOOL}: raising dependency floors (${wanted[*]})" \
    _conda_step "${envdir}" ${subdir_env[@]+"${subdir_env[@]}"} "${conda}" install -y -p "${envdir}" \
      -c conda-forge -c bioconda "${wanted[@]}" \
    || { warn "${TOOL}: could not satisfy ${wanted[*]}."
         info "  The env is built, but a package it needs is older than this tool can use."
         info "  Check what breaks:  bin/bdtools doctor ${TOOL}"
         return 0; }
}

# The manifest's `packages:` pins, applied to the env we just built.
#
# Every tool's own spec leaves these versions open (`mlst>=2.23`, plain `vsnp3`),
# so what a build produced depended on the day it ran — this suite release has
# already yielded mlst 2.33.1 on one machine and 2.35.0 on another. The manifest
# pin is the site's answer, so enforce it here rather than hoping.
#
# Guarded by a comparison against conda-meta so the common case costs nothing: a
# conda install that is already satisfied still runs a full solve, which would add
# minutes to every build. Never fatal — a pin that cannot be met is worth a loud
# warning, not a failed install of an otherwise working tool.
enforce_package_pins() {
  local specs; specs="$(manifest_get "${TOOL}" packages 2>/dev/null || true)"
  [[ -n "${specs}" ]] || return 0
  # Read the env that actually runs the tool, not just <checkout>/env — on a
  # deployment whose env lives elsewhere this used to check nothing and report
  # nothing, so a pinned version could drift there unseen.
  local envdir; envdir="$(resolve_env_prefix)"
  [[ -n "${envdir}" && -x "${envdir}/bin/python" ]] || return 0

  local wanted=() spec pkg ver have
  for spec in ${specs}; do
    pkg="${spec##*::}"; ver="${pkg#*=}"; pkg="${pkg%%=*}"
    [[ -n "${ver}" && "${ver}" != "${pkg}" ]] || continue
    have="$(ls "${envdir}/conda-meta" 2>/dev/null \
            | sed -n "s/^${pkg}-\([^-]*\)-[^-]*\.json$/\1/p" | head -1)"
    if [[ "${have}" == "${ver}" ]]; then
      ok "${TOOL}: ${pkg} ${ver} (pinned)"
    else
      wanted+=("${pkg}=${ver}")
      [[ -n "${have}" ]] && info "  ${TOOL}: ${pkg} ${have} installed, manifest pins ${ver}"
    fi
  done
  [[ ${#wanted[@]} -gt 0 ]] || return 0

  local conda; conda="$(detect_conda 2>/dev/null || true)"
  if [[ -z "${conda}" ]]; then
    warn "${TOOL}: cannot enforce package pins (${wanted[*]}) — conda not found"
    return 0
  fi
  # Same platform statement as enforce_env_constraints, for the same incident:
  # a mutation solves for the prefix it is about to change, said on the command.
  local sub; sub="$(env_conda_subdir "${envdir}" 2>/dev/null || true)"
  local subdir_env=(); [[ -n "${sub}" ]] && subdir_env=(env "CONDA_SUBDIR=${sub}")
  with_progress "${TOOL}: pinning analysis packages (${wanted[*]})" \
    _conda_step "${envdir}" ${subdir_env[@]+"${subdir_env[@]}"} "${conda}" install -y -p "${envdir}" \
      -c conda-forge -c bioconda "${wanted[@]}" \
    || { warn "${TOOL}: could not install the pinned versions (${wanted[*]})."
         info "  The env is usable, but not the version tools.yml records."
         info "  See what you have:  bin/bdtools versions ${TOOL}"
         return 0; }
  # A package install overwrites patched files; re-apply. Same reason
  # update-packages.sh does it, and the same consequence if it is skipped.
  if [[ -x "${DIR}/deploy/vsnp3-patches/apply.sh" ]]; then
    run "${DIR}/deploy/vsnp3-patches/apply.sh" "${envdir}" \
      || warn "${TOOL}: patches did not re-apply after pinning — run bin/bdtools doctor ${TOOL}"
  fi
}

# Non-dying check: is a usable python env already present? Same single answer
# as resolve_env_prefix — a second opinion here is how a "present" env could
# differ from the one the build just targeted.
have_python() {
  [[ -n "$(resolve_env_prefix)" ]]
}

# --------------------------------------------------------------------------
# 3. launch
# --------------------------------------------------------------------------
# The interpreter inside resolve_env_prefix's answer (the resolver is defined
# above the build section, because the build reads it too). Dies — with the
# names of both places it looked — when nothing resolves, which is the right
# behavior at launch and only at launch.
resolve_python() {
  local p; p="$(resolve_env_prefix)"
  [[ -n "${p}" ]] && { echo "${p}/bin/python"; return 0; }
  die "no usable python env for ${TOOL} (looked for ${DIR}/env and conda env '${ENV_NAME}')"
}

# merge_launch_path ENVBIN PREPEND — the PATH prefix a tool launches with.
#
# THE INVARIANT: the env that provides the tool's python must also provide its
# PATH. This used to be "PATH_PREPEND, or ENVBIN if there is none", which let
# the two come from DIFFERENT envs. tool_launch resolves an env its own way
# (sandbox -> shared sibling -> checkout -> a personal conda env named by the
# manifest), resolve_env_prefix resolves it another, and on a machine with BOTH
# a checkout env and a legacy personal env of the same name they disagree. The
# tool then runs python from env A with PATH from env B, and everything that
# resolves through PATH — every `#!/usr/bin/env <interp>` shebang — silently
# comes from the wrong environment.
#
# Not hypothetical (2026-08-21, macOS). kraken2 is a perl script shebanged
# `#!/usr/bin/env perl`. Launched with the checkout env's python but the legacy
# env's bin first on PATH, the SCRIPT resolved correctly (the tool finds it
# relative to its own python) and was then executed by the LEGACY env's perl,
# which loaded that env's perl modules and died on an architecture mismatch:
#
#   Can't load '.../envs/kraken_id_parse/.../Cwd.bundle' ... (have 'arm64',
#   need 'x86_64') ... Compilation failed in require at <checkout>/env/bin/kraken2
#
# Every pre-flight check passed — each names an absolute path — and no analysis
# could start. So: ENVBIN always first, and PATH_PREPEND's other entries (the
# vendored asset dirs, e.g. ksnp_gui's kSNP4-bin) after it.
merge_launch_path() {
  local envbin="$1" prepend="${2:-}" out="$1" p
  local IFS=:
  for p in ${prepend}; do
    [[ -z "${p}" || "${p}" == "${envbin}" ]] && continue
    out="${out}:${p}"
  done
  printf '%s' "${out}"
}

# arch_prefix ENVDIR — the `arch` command that pins this launch to the env's
# platform, as an array (empty when there is nothing to pin).
#
# WHY A PROCESS NEEDS AN ARCHITECTURE, not just a PATH. macOS picks which slice
# of a UNIVERSAL binary to run from the architecture preference it inherits down
# the process tree. A conda env can legitimately contain a universal interpreter
# alongside single-architecture extension modules — and then the slice decides
# whether the tool runs. The 2026-08-22 case: an osx-arm64 kraken env whose perl
# was universal (x86_64 + arm64) while its XS bundles were arm64-only. Launched
# from a tree preferring x86_64, perl ran its x86_64 slice and died loading its
# own modules:
#
#   Can't load '.../auto/Cwd/Cwd.bundle' ... (mach-o file, but is an
#   incompatible architecture (have 'arm64', need 'x86_64'))
#
# The same command from an arm64 shell worked, which is what made it look like a
# PATH problem for four rounds. It never was: every path resolved correctly and
# the ONE env was healthy. The env's recorded platform is the authority on how
# its binaries must run, so state it at launch instead of inheriting whatever
# the caller happened to be.
arch_prefix() {
  local envdir="${1:-}" sub host
  [[ "$(uname -s)" == "Darwin" ]] || return 0
  [[ -x /usr/bin/arch ]] || return 0
  sub="$(env_conda_subdir "${envdir}" 2>/dev/null || true)"
  # NO host-architecture guard. `uname -m` reports x86_64 inside a translated
  # process, so a guard like [[ "$(uname -m)" == arm64 ]] is FALSE exactly when
  # the caller is the Rosetta process whose preference we need to override —
  # it disabled the pin in the only case that needed it (2026-08-22: `bdtools
  # local` from an arm64 shell pinned and worked, the dashboard did not pin and
  # failed, same env). Nothing is lost by dropping it: an osx-arm64 env can only
  # exist on an arm64 host, and if it somehow does not, `arch` says so plainly.
  case "${sub}" in
    osx-arm64) printf '%s' "/usr/bin/arch -arm64";;
    # An osx-64 env on Apple Silicon is the deliberate Rosetta case
    # (ensure_conda_subdir rule 2); pin it too, so a universal binary there
    # cannot pick an arm64 slice its neighbours are not built for.
    osx-64)    printf '%s' "/usr/bin/arch -x86_64";;
  esac
  return 0
}

launch() {
  local py envbin; py="$(resolve_python)"; envbin="$(dirname "${py}")"
  # The env's architecture pin, computed ONCE at the top so that EVERY run of
  # the env's interpreter inside launch() carries it — not just the final exec.
  # The vsnp config-repair heredoc below used to run "${py}" a hundred lines
  # before the pin was computed: a universal python whose off-preference slice
  # cannot start then made launch() die on a loader error the pinned path
  # would never show, before the pin existed to prevent it.
  local _archp _arch_cmd=()
  _archp="$(arch_prefix "${envbin%/bin}")"
  [[ -n "${_archp}" ]] && read -ra _arch_cmd <<< "${_archp}"
  # Universal self-heal: ensure java resolves for any tool that needs it (covers
  # deploy/install.sh tools like mlst_gui that generic_build never touches, and
  # existing installs from before this fix). envbin is <env>/bin, so pass <env>.
  ensure_env_java "${envbin%/bin}"
  [[ -f "${DIR}/frontend/dist/index.html" ]] || warn "frontend/dist not built — the GUI may not load"
  [[ -n "${PORT}" ]] || PORT="$(find_free_port)"
  local url="http://127.0.0.1:${PORT}/"
  log "starting ${TOOL} at ${url}  (Ctrl-C to stop)"
  echo "  python: ${py}"
  if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] would exec uvicorn on ${PORT}"; return; fi
  # vsnp_gui's backend resolves its shared paths (references, vcf_db, vsnp3 env)
  # from VSNP_GUI_SITE_ROOT (default /srv/kapurlab). Point it at the local site
  # tree build_vsnp_local() laid out, or Step 1 looks under /srv and fails.
  if [[ -d "${DIR}/deploy/vsnp3-patches" ]]; then
    local site="${BDTOOLS_HOME}/vsnp3-site"
    export VSNP_GUI_SITE_ROOT="${site}"
    # Single-user local install: collapse to one Projects root. Disable the
    # (multi-user) shared projects root so it isn't auto-derived from SITE_ROOT
    # and doesn't shadow the user's chosen Projects root. Present-but-empty is
    # authoritative in the backend (see config.py load_config). The lab SERVER
    # deployment doesn't run this launcher, so its shared sharing is unaffected.
    export VSNP_GUI_SHARED_PROJECTS_ROOT=""
    # Self-heal the Kraken tool link for installs done before this fix, or when
    # kraken was checked out after vsnp. Point at the CHECKOUT dir (not its env) —
    # the backend appends /bin and /env itself. Idempotent; no-op if absent.
    { local kdir; kdir="$(tool_dir kraken_id_parse_gui)"; [[ -d "${kdir}" ]] && \
        ln -sfn "${kdir}" "${site}/tools/kraken_id_parse_gui"; } 2>/dev/null || true
    # Self-heal the DB-root link too (see build_vsnp_local): local DBs live at
    # db-root, but the vSNP embedded Kraken/BLAST default is SITE_ROOT/databases/...
    { local dbr; dbr="$(cat "${BDTOOLS_HOME}/db-root" 2>/dev/null || true)"; \
        [[ -n "${dbr}" && -d "${dbr}" ]] && ln -sfn "${dbr}" "${site}/databases"; } 2>/dev/null || true
    # Self-heal a stale per-user config.json: load_config() froze /srv paths into
    # it on the first GUI load (before this fix). Repoint the derived shared-path
    # keys to the local site, preserving user prefs. No-op on a fresh machine.
    VSNP_GUI_SITE_ROOT="${site}" ${_arch_cmd[@]+"${_arch_cmd[@]}"} "${py}" - <<'PY' || true
import json, os
from pathlib import Path
site = os.environ["VSNP_GUI_SITE_ROOT"]
cfgp = Path.home() / ".config" / "vsnp_gui" / "config.json"
derived = {
    "vsnp3_path": f"{site}/tools/vsnp3",
    "vsnp3_reference_options_root": f"{site}/refs/vsnp3/reference_options",
    "vcf_db_folders_root": f"{site}/refs/vsnp3/vcf_db_folders",
    "vsnp_gui_deploy_path": f"{site}/tools/vsnp_gui",
    "audit_root": f"{site}/audit",
    # Single-user local install: no shared projects root (one Projects root).
    "shared_projects_root": "",
}
try:
    cfg = json.loads(cfgp.read_text())
except Exception:
    raise SystemExit(0)  # no/!readable config -> defaults (env-var) handle it
changed = [k for k, v in derived.items() if cfg.get(k) != v]
if changed:
    cfg.update(derived)
    cfgp.write_text(json.dumps(cfg, indent=2, sort_keys=True))
    print(f"  repaired stale vsnp_gui config paths -> {site}")
PY
  fi
  # PATH for the server process. Until this used `${envbin}` alone, and that is
  # not enough for a tool with vendored binaries outside its conda env: the
  # dashboard launches through tool_launch (which applies `path_prepend`) while
  # `bdtools local` did not, so `bdtools local ksnp_gui` served a GUI that
  # reported kSNP4 "not installed" and disabled Run on a perfectly good install.
  #
  # Ask tool_launch for the answer rather than re-deriving it here — it owns the
  # resolution rule (tool tree -> installed checkout -> machine-wide vendor
  # cache), and a second copy of that rule is how the two launchers disagreed in
  # the first place. Fall back to envbin if anything goes wrong: a tool that
  # needs no vendored payload must still launch on a machine where this fails.
  # PYBIN is a plain system python3 from common.sh, and tool_launch is
  # stdlib-only — it does not need the tool's own env to answer this.
  local launch_path="${envbin}" _pp _tl_show
  _tl_show="$("${PYBIN}" "${KT_BIN_DIR}/lib/tool_launch.py" show "${TOOL}" "${PORT}" 2>/dev/null || true)"
  _pp="$(printf '%s' "${_tl_show}" | "${PYBIN}" -c 'import json,sys
try: print(json.load(sys.stdin)["env_overrides"].get("PATH_PREPEND",""))
except Exception: print("")' 2>/dev/null)"
  launch_path="$(merge_launch_path "${envbin}" "${_pp}")"
  # A disagreement between the two resolvers is worth saying out loud: it means
  # tool_launch would run this tool from an env other than the one being
  # launched, and the next person to debug PATH here should not have to
  # rediscover that. The launch itself is already safe (envbin is first).
  if [[ -n "${_pp}" && ":${_pp}:" != *":${envbin}:"* ]]; then
    warn "${TOOL}: tool_launch resolves a different env than this launch."
    info "  launching with: ${envbin}"
    info "  tool_launch says: ${_pp}"
    info "  Using this env's bin FIRST, so its scripts run under its own interpreters."
    info "  Check for a stale duplicate env:  bin/bdtools doctor ${TOOL}"
  fi

  # Hand the tool this deployment's resolved roots, exactly as the proxy dashboard
  # does through tool_launch. Without this, `bdtools local` (and the legacy
  # multi-port dashboard, which launches tools *through* `bdtools local`) started
  # backends with only PATH+PYTHONPATH set — so a tool that asks the deployment
  # where its databases are got no answer here while getting a correct one under
  # the proxy dashboard. Two launchers disagreeing about the environment is the
  # same failure mode the PATH_PREPEND lookup above exists to prevent.
  #
  # Values come from tool_launch (which owns the rule) and are applied only when
  # not already set, so an explicit export by the caller still wins.
  local _sv _k
  while IFS= read -r _sv; do
    [[ -n "${_sv}" ]] || continue
    _k="${_sv%%=*}"
    [[ -n "${!_k:-}" ]] && continue             # already set: the caller wins
    export "${_sv}"
  done < <(printf '%s' "${_tl_show}" | "${PYBIN}" -c 'import json,sys
KEYS = ("BDTOOLS_DB_ROOT", "BDTOOLS_SHARED_PROJECTS_ROOT",
        "BDTOOLS_SITE_ROOT", "BDTOOLS_TOOLS_ROOT")
try:
    ov = json.load(sys.stdin)["env_overrides"]
except Exception:
    sys.exit(0)
for k in KEYS:
    v = ov.get(k)
    if v:
        print("%s=%s" % (k, v))' 2>/dev/null)

  # Enough open files for the assembly stack, before the header below records
  # what the tool got. Every analysis subprocess inherits it (raise_file_limit).
  raise_file_limit

  # Record the exact, reproducible launch command to the tool's dashboard log, so
  # every run (including this direct `bdtools local` one) is copy-paste rerunnable
  # from a terminal. Mirrors the header the dashboards write (tool_launch.log_header).
  local bdhome logdir extra_env=""
  bdhome="${BDTOOLS_HOME:-$HOME/.local/share/bdtools}"; logdir="${bdhome}/dashboard-logs"
  mkdir -p "${logdir}" 2>/dev/null || true
  [[ -n "${VSNP_GUI_SITE_ROOT:-}" ]] && \
    extra_env="VSNP_GUI_SITE_ROOT=$(printf '%q' "${VSNP_GUI_SITE_ROOT}") VSNP_GUI_SHARED_PROJECTS_ROOT=$(printf '%q' "${VSNP_GUI_SHARED_PROJECTS_ROOT:-}") "
  {
    printf '\n# %s\n' '===================================================================='
    printf '# bdtools tool launch — %s\n' "${TOOL}"
    printf '# started: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf '# python env: %s\n' "${envbin%/bin}"
    printf '# open files: %s (soft RLIMIT_NOFILE, inherited by every analysis subprocess)\n' "$(file_limit)"
    printf '# Reproduce this exact run from a terminal (copy/paste the line below):\n#\n'
    printf 'cd %q && PATH=%q:$PATH PYTHONPATH=%q %s%q -m uvicorn app.main:app --host 127.0.0.1 --port %q --log-level info\n' \
      "${DIR}/backend" "${launch_path}" "${DIR}/bin" "${extra_env}" "${py}" "${PORT}"
    printf '# %s\n' '===================================================================='
  } >> "${logdir}/${TOOL}.log" 2>/dev/null || true

  [[ ${NO_BROWSER} -eq 1 ]] || ( sleep 2; open_url "${url}" ) &
  cd "${DIR}/backend"
  # Pin the architecture for the whole backend tree (see arch_prefix): every
  # analysis subprocess inherits it, so a universal binary anywhere below here
  # picks the slice this env is actually built for. _archp/_arch_cmd were
  # computed once at the top of launch() — the heredoc repair above already
  # ran under the same pin.
  [[ -n "${_archp}" ]] && echo "  arch:   ${_archp} (pinned from the env's platform)"
  echo "  files:  $(file_limit) open-file limit (the assembly stack needs hundreds)"
  PATH="${launch_path}:${PATH}" PYTHONPATH="${DIR}/bin:${PYTHONPATH:-}" \
    exec ${_arch_cmd[@]+"${_arch_cmd[@]}"} "${py}" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" --log-level info
}

ensure_checkout

# --print-python: report the tool's env python if it is built (no build/launch).
# Used by the dashboard to detect which tools are installed and how to run them.
if [[ ${PRINT_PYTHON} -eq 1 ]]; then
  have_python || exit 1
  resolve_python
  exit 0
fi

DO_BUILD=1; DO_LAUNCH=1
[[ ${RUN_ONLY} -eq 1 ]]   && DO_BUILD=0
[[ ${BUILD_ONLY} -eq 1 ]] && DO_LAUNCH=0
# run-only but nothing built yet → build first anyway
if [[ ${DO_BUILD} -eq 0 ]] && ! have_python; then
  warn "${TOOL} not built yet — building first"; DO_BUILD=1
fi

# Build, and leave a record of whether it finished. Almost every failure path in
# build() ends in `die`/`set -e` (an exit, not a return), so an EXIT trap is the
# only way to catch them all — and the record matters: `bdtools update` decides
# whether to retry a tool from it (see common.sh, build state), and without it a
# half-built env at the right git ref looked exactly like a finished one.
if [[ ${DO_BUILD} -eq 1 ]]; then
  BUILD_REF="$(git -C "${DIR}" describe --tags --always 2>/dev/null || echo unknown)"
  _record_build_exit() {
    local rc=$?
    # 3 is install-local's "this tool has no local-build path" sentinel — a clean
    # skip, not a failed build (bdtools install treats it the same way).
    [[ ${rc} -eq 0 || ${rc} -eq 3 ]] && return 0
    build_state_fail "${TOOL}" "${BUILD_REF}" "install-local.sh --build exit ${rc}"
    warn "${TOOL}: build did not finish (exit ${rc}) — recorded, so 'bdtools update ${TOOL}' will retry it rather than skip it."
    # The moment the snapshot is worth something. A failed transaction is rolled
    # back, not restored, so say plainly that the env was left alone and how to
    # put it back to what it was.
    restore_env_from_fresh
    if [[ -z "${FRESH_ORIG}" && -x "${DIR}/env/bin/python" ]]; then
      info "  The existing env was NOT deleted — the tool may still run: bin/bdtools doctor ${TOOL}"
    fi
    restore_env_hint "${TOOL}"
  }
  trap _record_build_exit EXIT
  build
  trap - EXIT
  build_state_ok "${TOOL}"
fi

# Self-check the env we just built: confirm the tool's required python modules
# import and its programs are on PATH (scope=env skips database checks — those
# are handled by `bdtools setup-databases`). This turns a silent-but-broken env
# (e.g. a missing 'humanize' that would crash mid-run) into an actionable
# message at install time. Non-fatal: a usable-but-incomplete install is still
# worth launching, and `bdtools doctor` gives the authoritative report.
if [[ ${DO_BUILD} -eq 1 && ${DRY_RUN} -eq 0 ]] && have_python; then
  py_chk="$(resolve_python 2>/dev/null || true)"
  if [[ -n "${py_chk}" ]] && ! "${PYBIN}" "${KT_BIN_DIR}/lib/check.py" \
        --tool "${TOOL}" --dir "${DIR}" --python "${py_chk}" --scope env; then
    warn "${TOOL}: the build finished but the self-check above found problems — run the suggested fix."
    SELF_CHECK_OK=0
  fi
fi

# The set-aside env from --fresh is discarded only once the new one has PASSED its
# self-check. A build can exit 0 and still produce an env that does not import
# what the tool needs, and that is precisely when someone wants the old one back.
if [[ -n "${FRESH_ASIDE:-}" && -d "${FRESH_ASIDE}" ]]; then
  if [[ "${SELF_CHECK_OK:-1}" -eq 1 ]]; then
    rm -rf "${FRESH_ASIDE}" && ok "--fresh: new env passed its self-check; the old one is gone"
    info "  Its package list is still recorded: bin/bdtools restore-env ${TOOL} --prev"
  else
    warn "--fresh: keeping the previous env at ${FRESH_ASIDE} — the new one did not pass its self-check."
    info "  Go back to it with:  rm -rf ${FRESH_ORIG} && mv ${FRESH_ASIDE} ${FRESH_ORIG}"
  fi
fi

# kSNP4 runs on Linux and macOS (each from its own SourceForge package), but on
# nothing else — say so rather than leaving the GUI's disabled Run unexplained.
if [[ ${DO_BUILD} -eq 1 && "${TOOL}" == "ksnp_gui" ]]; then
  case "$(uname -s)" in
    Linux|Darwin) ;;
    *)
      warn "${TOOL}: no kSNP4.1 package is published for $(uname -s), so analyses will NOT run here."
      info "  The GUI installs and can browse past runs, but Run is disabled and explains why."
      info "  Run kSNP analyses on Linux, macOS, or an OOD deployment.";;
  esac
fi

[[ ${DO_LAUNCH} -eq 1 ]] && launch
[[ ${DO_LAUNCH} -eq 0 ]] && ok "${TOOL} built (not launched)"
exit 0
