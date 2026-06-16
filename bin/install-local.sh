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

TOOL=""; RUN_ONLY=0; BUILD_ONLY=0; PORT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-only)   RUN_ONLY=1; shift;;
    --build-only) BUILD_ONLY=1; shift;;
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

# --------------------------------------------------------------------------
# 1. checkout
# --------------------------------------------------------------------------
ensure_checkout() {
  if [[ -d "${DIR}/.git" ]]; then
    ok "checkout present: ${DIR} ($(git -C "${DIR}" describe --tags --always 2>/dev/null || echo '?'))"
    return
  fi
  [[ ${RUN_ONLY} -eq 1 ]] && die "${TOOL} is not installed at ${DIR} (run: bdtools install ${TOOL})"
  log "cloning ${TOOL} @ ${VERSION}"
  run mkdir -p "$(dirname "${DIR}")"
  run git clone --branch "${VERSION}" --depth 1 "${REPO}" "${DIR}" \
    || die "git clone failed (${REPO} @ ${VERSION})"
}

# --------------------------------------------------------------------------
# 2. build (env + frontend)
# --------------------------------------------------------------------------
generic_build() {
  local conda; conda="$(detect_conda)" || die "conda/mamba not found. Install miniforge first."
  ok "conda: ${conda}"
  local env_file="${DIR}/conda_setup/environment.yml"
  if [[ -x "${DIR}/env/bin/python" ]]; then
    ok "env present: ${DIR}/env"
  elif [[ -f "${env_file}" ]]; then
    log "creating conda env at ${DIR}/env (solve can take several minutes)"
    run "${conda}" env create -p "${DIR}/env" -f "${env_file}"
  else
    die "no ${env_file} — cannot build env"
  fi
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

build() {
  ensure_conda_subdir
  if [[ -x "${DIR}/deploy/install.sh" ]]; then
    log "delegating env+frontend build to ${TOOL}/deploy/install.sh"
    local args=(); [[ ${DRY_RUN} -eq 1 ]] && args+=(--dry-run)
    # Prefer a personal/standalone env if the tool's installer supports it.
    if grep -q -- '--personal' "${DIR}/deploy/install.sh" 2>/dev/null; then args+=(--personal); fi
    run "${DIR}/deploy/install.sh" "${args[@]}" || die "${TOOL} deploy/install.sh failed"
  elif [[ -f "${DIR}/conda_setup/environment.yml" ]]; then
    log "no deploy/install.sh in ${TOOL}; using generic build"
    generic_build
  else
    die "${TOOL} has no standalone local-build path (no deploy/install.sh, no conda_setup/environment.yml). Its env is built by its OOD installer — use --sandbox or --server."
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
  [[ -f "${DIR}/frontend/dist/index.html" ]] || warn "frontend/dist not built — the GUI may not load"
  [[ -n "${PORT}" ]] || PORT="$(find_free_port)"
  local url="http://127.0.0.1:${PORT}/"
  log "starting ${TOOL} at ${url}  (Ctrl-C to stop)"
  echo "  python: ${py}"
  if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] would exec uvicorn on ${PORT}"; return; fi
  ( sleep 2; open_url "${url}" ) &
  cd "${DIR}/backend"
  PATH="${envbin}:${PATH}" PYTHONPATH="${DIR}/bin:${PYTHONPATH:-}" \
    exec "${py}" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" --log-level info
}

ensure_checkout

DO_BUILD=1; DO_LAUNCH=1
[[ ${RUN_ONLY} -eq 1 ]]   && DO_BUILD=0
[[ ${BUILD_ONLY} -eq 1 ]] && DO_LAUNCH=0
# run-only but nothing built yet → build first anyway
if [[ ${DO_BUILD} -eq 0 ]] && ! have_python; then
  warn "${TOOL} not built yet — building first"; DO_BUILD=1
fi

[[ ${DO_BUILD} -eq 1 ]]  && build
[[ ${DO_LAUNCH} -eq 1 ]] && launch
[[ ${DO_LAUNCH} -eq 0 ]] && ok "${TOOL} built (not launched)"
exit 0
