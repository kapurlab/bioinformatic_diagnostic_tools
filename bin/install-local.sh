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

TOOL=""; RUN_ONLY=0; BUILD_ONLY=0; PORT=""; NO_BROWSER=0; PRINT_PYTHON=0; REBUILD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-only)   RUN_ONLY=1; shift;;
    --build-only) BUILD_ONLY=1; shift;;
    --rebuild)    REBUILD=1; shift;;            # refresh an existing env from its spec (apply new deps)
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

# Progress helpers for the long, often-silent build steps (conda solve, package
# download, delegated deploy/install.sh). The problem they solve: a solve is
# CPU-bound and silent, a download is I/O-bound and silent, and a *stalled*
# download (dead mirror connection, no timeout) is silent too — on the command
# line all three look identical, which is the root of the "hung for hours"
# reports. So we watch two independent progress signals and act on them.

# _tree_cpu_ticks PID — total CPU ticks (utime+stime) of PID and all descendants.
# Rises during a solve/extract even when nothing is written to disk.
_tree_cpu_ticks() {
  local frontier="$1" next pid t total=0
  while [[ -n "${frontier}" ]]; do
    next=""
    for pid in ${frontier}; do
      t="$(awk '{print $14+$15}' "/proc/${pid}/stat" 2>/dev/null || echo 0)"
      total=$(( total + ${t:-0} ))
      next="${next} $(pgrep -P "${pid}" 2>/dev/null | tr '\n' ' ')"
    done
    frontier="${next}"
  done
  echo "${total}"
}

# _watched_bytes — total bytes across the paths a build writes to (the pkg cache,
# the target env prefix, the frontend). Rises during a download/extract/link even
# when CPU is idle. Cheap enough at heartbeat cadence; missing paths are skipped.
_watched_bytes() {
  local p b total=0
  for p in "$@"; do
    [[ -e "${p}" ]] || continue
    b="$(du -sb "${p}" 2>/dev/null | cut -f1)"
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
  if [[ "${DRY_RUN:-0}" -eq 1 ]]; then echo "  [dry-run] ${label}: $*"; return 0; fi
  local tries="${BDTOOLS_BUILD_TRIES:-2}" attempt=1 rc=0
  while :; do
    [[ "${tries}" -gt 1 ]] && log "${label} — attempt ${attempt}/${tries}"
    rc=0; _run_watched "${label}" "$@" || rc=$?
    [[ ${rc} -eq 0 ]] && return 0
    if [[ ${attempt} -ge ${tries} ]]; then
      warn "${label} — giving up after ${attempt} attempt(s) (exit ${rc})"
      return ${rc}
    fi
    warn "${label} — failed/stalled (exit ${rc}); retrying in 5s…"
    sleep 5; attempt=$(( attempt + 1 ))
  done
}

# _run_watched: one attempt — launch, monitor CPU+disk progress, kill on stall.
_run_watched() {
  local label="$1"; shift
  local secs="${BDTOOLS_HEARTBEAT_SECS:-30}" idle_max="${BDTOOLS_IDLE_TIMEOUT:-300}"
  local watch=() cbase
  cbase="$(conda_base_dir 2>/dev/null || true)"
  [[ -n "${cbase}" ]] && watch+=("${cbase}/pkgs")
  [[ -n "${cbase}" && -n "${ENV_NAME:-}" ]] && watch+=("${cbase}/envs/${ENV_NAME}")
  watch+=("${DIR}/env" "${DIR}/frontend/node_modules" "${DIR}/frontend/dist")
  local t0 last cpu0 disk0 cpu disk now e idle rc=0
  t0="$(date +%s)"; last="${t0}"
  log "${label} — started $(date '+%H:%M:%S')  (heartbeat ${secs}s; stall-kill after ${idle_max}s of no progress)"
  "$@" & local cmd=$!
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
        run git -C "${DIR}" fetch --tags --depth 1 origin "${VERSION}" >/dev/null 2>&1 || true
        want="$(git -C "${DIR}" rev-parse -q --verify "refs/tags/${VERSION}^{commit}" 2>/dev/null \
                || git -C "${DIR}" rev-parse -q --verify FETCH_HEAD 2>/dev/null || true)"
      fi
      local head; head="$(git -C "${DIR}" rev-parse -q --verify HEAD 2>/dev/null || true)"
      if [[ -n "${want}" && "${head}" != "${want}" ]]; then
        if git -C "${DIR}" diff --quiet && git -C "${DIR}" diff --cached --quiet; then
          log "moving ${TOOL} checkout ${at} -> pinned ${VERSION}"
          run git -C "${DIR}" checkout -q "${VERSION}" 2>/dev/null || run git -C "${DIR}" checkout -q "${want}"
          at="$(git -C "${DIR}" describe --tags --always 2>/dev/null || echo '?')"
        else
          warn "${TOOL} checkout is ${at} but pin is ${VERSION}, and it has local edits — not moving. Commit/stash them, or run: bdtools update ${TOOL}"
        fi
      fi
    fi
    ok "checkout present: ${DIR} (${at})"
    return
  fi
  [[ ${RUN_ONLY} -eq 1 ]] && die "${TOOL} is not installed at ${DIR} (run: bdtools install ${TOOL})"
  log "cloning ${TOOL} @ ${VERSION}"
  run mkdir -p "$(dirname "${DIR}")"
  run git clone --branch "${VERSION}" --depth 1 "${REPO}" "${DIR}" \
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
        "${conda}" env update -p "${DIR}/env" -f "${env_file}"
    else
      ok "env present: ${DIR}/env"
    fi
  elif [[ -f "${env_file}" ]]; then
    with_progress "${TOOL}: creating conda env (solve can take several minutes)" \
      "${conda}" env create -p "${DIR}/env" -f "${env_file}"
  else
    die "no ${env_file} — cannot build env"
  fi
  ensure_env_java "${DIR}/env"
  if [[ -f "${DIR}/backend/requirements.txt" ]]; then
    log "pip install backend requirements"
    run "${DIR}/env/bin/python" -m pip install -r "${DIR}/backend/requirements.txt"
  fi
  if [[ -d "${DIR}/frontend" && ! -f "${DIR}/frontend/dist/index.html" ]]; then
    log "building frontend"
    ( cd "${DIR}/frontend" && command -v npm >/dev/null 2>&1 \
        && { run npm ci || run npm install; run npm run build; } \
        || warn "npm not found — frontend not built" )
  fi
}

# On Apple Silicon (macOS arm64), most of the bioinformatics dependency closure
# has NO native osx-arm64 conda build — e.g. IRMA needs `blat`, and shovill pulls
# spades/mash/skesa — none fully built for arm64 on bioconda. A native solve just
# fails. The standard fix is to build the env as osx-64 and let Rosetta 2 run it
# (the complete, mature osx-64 package set resolves cleanly). Exporting
# CONDA_SUBDIR here is inherited by each tool's deploy/install.sh. Opt out with
# BDTOOLS_NATIVE_ARM=1 (expect solve failures) or by pre-setting CONDA_SUBDIR.
ensure_conda_subdir() {
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
  if [[ -x "${DIR}/env/bin/python" ]]; then
    ok "env present: ${DIR}/env"
  else
    with_progress "${TOOL}: creating vsnp3 env (bioconda vsnp3 + snp-dists; solve can take several minutes)" \
      "${conda}" create -y -p "${DIR}/env" -c conda-forge -c bioconda vsnp3 snp-dists
  fi
  # 2. web layer (uvicorn is served from this same python)
  [[ -x "${DIR}/env/bin/pip" ]] && run "${DIR}/env/bin/pip" install --upgrade \
      fastapi uvicorn pydantic python-multipart aiofiles
  # 3. Kapur Lab vsnp3 patches (idempotent; safe on the packaged version)
  [[ -x "${DIR}/deploy/vsnp3-patches/apply.sh" ]] && \
    { run "${DIR}/deploy/vsnp3-patches/apply.sh" "${DIR}/env" || warn "vsnp3 patch step reported an issue (continuing)"; }
  # 4. reference_options (USDA-VS) + register the path vsnp3 reads at runtime.
  #    Prefer a database-setup-managed reference set (bdtools setup-databases
  #    writes BDTOOLS_HOME/db-root) so we don't clone a second copy; otherwise
  #    fall back to a vsnp_gui-private clone.
  local refs db_root="" vsnp_deps=""
  [[ -f "${BDTOOLS_HOME}/db-root" ]] && db_root="$(cat "${BDTOOLS_HOME}/db-root" 2>/dev/null || true)"
  if [[ -n "${db_root}" && -n "$(ls -A "${db_root}/vsnp3/reference_options" 2>/dev/null)" ]]; then
    refs="${db_root}/vsnp3/reference_options"
    ok "using database-setup reference options: ${refs}"
    [[ -d "${db_root}/vsnp3/vsnp_dependencies" ]] && vsnp_deps="${db_root}/vsnp3/vsnp_dependencies"
  else
    refs="${BDTOOLS_HOME}/vsnp3-refs/vSNP_reference_options"
    if [[ -n "$(ls -A "${refs}" 2>/dev/null)" ]]; then
      ok "reference options present: ${refs}"
    else
      log "downloading vSNP reference options (USDA-VS) -> ${refs}"
      run mkdir -p "$(dirname "${refs}")"
      run git clone --depth 1 "${VSNP_REFS_REPO}" "${refs}"
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
    ln -sfn "${refs}" "${site}/refs/vsnp3/reference_options"   # GUI reference root
    ln -sfn "${DIR}/env" "${site}/tools/vsnp3"                  # GUI vsnp3_path
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
    grep -qxF "${refpath}" "${rop}" 2>/dev/null || { echo "${refpath}" >> "${rop}"; }
    # Register the USDA vsnp_dependencies reference set too, when database-setup
    # provided it (extra references like the Brucella/MTBC test references).
    [[ -n "${vsnp_deps}" ]] && { grep -qxF "${vsnp_deps}" "${rop}" 2>/dev/null || echo "${vsnp_deps}" >> "${rop}"; }
    ok "configured local vsnp site: ${site} (references + vcf_db_folders + env link)"
    info "  Step 2's curated VCF databases are lab-private and are NOT downloaded; add your own"
    info "  VCF folders under ${site}/refs/vsnp3/vcf_db_folders or via the GUI settings."
    # stable in-checkout pointer so the validation harness can find the refs
    [[ -e "${DIR}/vSNP_reference_options" ]] || ln -s "${refs}" "${DIR}/vSNP_reference_options" 2>/dev/null || true
  fi
  # 5. frontend
  if [[ -d "${DIR}/frontend" && ! -f "${DIR}/frontend/dist/index.html" ]]; then
    log "building frontend"
    ( cd "${DIR}/frontend" && command -v npm >/dev/null 2>&1 \
        && { run npm ci || run npm install; run npm run build; } \
        || warn "npm not found — frontend not built" )
  fi
}

build() {
  ensure_conda_subdir
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
    # Every GUI ships a prebuilt frontend/dist. The tool installers otherwise try
    # to rebuild it and hard-fail when Node is absent (a laptop without node, or
    # node_modules present but no node binary). Mirror generic_build's
    # skip-if-already-built behavior: when dist exists, skip the frontend build.
    if [[ -f "${DIR}/frontend/dist/index.html" ]] \
       && grep -q -- '--skip-frontend' "${DIR}/deploy/install.sh" 2>/dev/null; then
      args+=(--skip-frontend)
    fi
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
}

# Non-dying check: is a usable python env already present?
have_python() {
  [[ -x "${DIR}/env/bin/python" ]] && return 0
  local conda; conda="$(detect_conda 2>/dev/null || true)"
  [[ -n "${conda}" && -n "${ENV_NAME}" ]] \
    && "${conda}" env list 2>/dev/null | awk '{print $1}' | grep -qxF "${ENV_NAME}"
}

# --------------------------------------------------------------------------
# 3. launch
# --------------------------------------------------------------------------
resolve_python() {
  if [[ -x "${DIR}/env/bin/python" ]]; then echo "${DIR}/env/bin/python"; return; fi
  local conda; conda="$(detect_conda 2>/dev/null || true)"
  if [[ -n "${conda}" && -n "${ENV_NAME}" ]] \
     && "${conda}" env list 2>/dev/null | awk '{print $1}' | grep -qxF "${ENV_NAME}"; then
    "${conda}" run -n "${ENV_NAME}" sh -c 'echo $CONDA_PREFIX/bin/python'; return
  fi
  die "no usable python env for ${TOOL} (looked for ${DIR}/env and conda env '${ENV_NAME}')"
}

launch() {
  local py envbin; py="$(resolve_python)"; envbin="$(dirname "${py}")"
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
    VSNP_GUI_SITE_ROOT="${site}" "${py}" - <<'PY' || true
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
  [[ ${NO_BROWSER} -eq 1 ]] || ( sleep 2; open_url "${url}" ) &
  cd "${DIR}/backend"
  PATH="${envbin}:${PATH}" PYTHONPATH="${DIR}/bin:${PYTHONPATH:-}" \
    exec "${py}" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" --log-level info
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

[[ ${DO_BUILD} -eq 1 ]]  && build

# Self-check the env we just built: confirm the tool's required python modules
# import and its programs are on PATH (scope=env skips database checks — those
# are handled by `bdtools setup-databases`). This turns a silent-but-broken env
# (e.g. a missing 'humanize' that would crash mid-run) into an actionable
# message at install time. Non-fatal: a usable-but-incomplete install is still
# worth launching, and `bdtools doctor` gives the authoritative report.
if [[ ${DO_BUILD} -eq 1 && ${DRY_RUN} -eq 0 ]] && have_python; then
  py_chk="$(resolve_python 2>/dev/null || true)"
  if [[ -n "${py_chk}" ]] && ! python3 "${KT_BIN_DIR}/lib/check.py" \
        --tool "${TOOL}" --dir "${DIR}" --python "${py_chk}" --scope env; then
    warn "${TOOL}: the build finished but the self-check above found problems — run the suggested fix."
  fi
fi

# Heads-up for tools with Linux-only vendored binaries (e.g. ksnp_gui's kSNP4):
# they install fine but the analysis can't run off Linux (Rosetta translates
# macOS x86_64, not Linux ELF).
if [[ ${DO_BUILD} -eq 1 && "$(uname -s)" != "Linux" && -d "${DIR}/vendor/kSNP4-bin" ]]; then
  warn "${TOOL}: its kSNP4 analysis binaries are Linux-only and will NOT run on $(uname -s)."
  info "  The GUI installs, but run the kSNP pipeline on a Linux machine or an OOD deployment."
fi

[[ ${DO_LAUNCH} -eq 1 ]] && launch
[[ ${DO_LAUNCH} -eq 0 ]] && ok "${TOOL} built (not launched)"
exit 0
