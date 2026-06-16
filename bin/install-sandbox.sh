#!/usr/bin/env bash
# install-sandbox.sh — per-user, NO-SUDO Open OnDemand sandbox app install.
#
# For an institutional OOD cluster where you are a regular user (not an admin).
# Everything lands under $HOME; nothing system-wide. The tool then appears under
# Develop -> My Sandbox Apps and launches as a normal batch_connect session on
# the site's scheduler + auth.
#
#   install-sandbox.sh <tool> [--conda-base DIR] [--prefix DIR]
#                             [--no-link] [--dry-run]
#
# Strategy:
#   * If the tool ships its own deploy/setup-sandbox.sh, delegate to it (it
#     knows the tool's env, patches, references, and sandbox card). This is the
#     proven path (vsnp_gui).
#   * Otherwise run the generic flow: build the env+frontend (reusing the tested
#     install-local build), write ~/.config/<tool>/sandbox.env, and link an OOD
#     card into ~/ondemand/dev/. If the tool has no sandbox-aware card yet, we
#     link the best available card and tell you exactly what's still needed.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TOOL=""; CONDA_BASE_OPT=""; DO_LINK=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --conda-base) CONDA_BASE_OPT="$2"; export CONDA_BASE="$2"; shift 2;;
    --prefix)     export BDTOOLS_HOME="$2"; shift 2;;
    --no-link)    DO_LINK=0; shift;;
    --dry-run)    DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help)    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)           die "unknown option: $1";;
    *)            TOOL="$1"; shift;;
  esac
done
[[ -n "${TOOL}" ]] || die "name a tool (see: bdtools list)"
manifest_has "${TOOL}" || die "unknown tool: ${TOOL}"

ensure_checkout "${TOOL}"
DIR="$(tool_dir "${TOOL}")"
ENV_NAME="$(manifest_get "${TOOL}" env)"

# --------------------------------------------------------------------------
# Preferred path: the tool ships its own sandbox installer.
# --------------------------------------------------------------------------
if [[ -x "${DIR}/deploy/setup-sandbox.sh" ]]; then
  log "delegating to ${TOOL}/deploy/setup-sandbox.sh (tool-native sandbox install)"
  args=()
  [[ ${DRY_RUN} -eq 1 ]]            && args+=(--dry-run)
  [[ -n "${CONDA_BASE_OPT}" ]]      && args+=(--conda-base "${CONDA_BASE_OPT}")
  [[ ${DO_LINK} -eq 0 ]] && grep -q -- '--no-link' "${DIR}/deploy/setup-sandbox.sh" && args+=(--no-link)
  exec "${DIR}/deploy/setup-sandbox.sh" "${args[@]}"
fi

# --------------------------------------------------------------------------
# Generic path.
# --------------------------------------------------------------------------
log "generic sandbox install for ${TOOL}"

# 1. env + frontend — reuse the tested local build (idempotent, no-sudo).
log "building env + frontend"
build_args=(--build-only); [[ ${DRY_RUN} -eq 1 ]] && build_args+=(--dry-run)
run "${KT_BIN_DIR}/install-local.sh" "${build_args[@]}" "${TOOL}" \
  || die "env/frontend build failed for ${TOOL}"

# 2. resolve the env path for the OOD card to use.
resolve_env() {
  if [[ -x "${DIR}/env/bin/python" ]]; then echo "${DIR}/env"; return; fi
  local conda; conda="$(detect_conda 2>/dev/null || true)"
  if [[ -n "${conda}" && -n "${ENV_NAME}" ]] \
     && "${conda}" env list 2>/dev/null | awk '{print $1}' | grep -qxF "${ENV_NAME}"; then
    "${conda}" run -n "${ENV_NAME}" sh -c 'echo $CONDA_PREFIX'; return
  fi
  echo ""   # unknown (dry-run, or not built)
}
APP_ENV="$(resolve_env)"

# 3. sandbox.env for the OOD card to source.
CFG_DIR="${HOME}/.config/${TOOL}"
log "writing ${CFG_DIR}/sandbox.env"
run mkdir -p "${CFG_DIR}"
if [[ ${DRY_RUN} -eq 0 ]]; then
  {
    echo "# Written by bdtools install-sandbox — sourced by the OOD sandbox card."
    echo "BDTOOLS_APP=${TOOL}"
    echo "BDTOOLS_APP_DIR=${DIR}"
    echo "BDTOOLS_APP_ENV=${APP_ENV}"
  } > "${CFG_DIR}/sandbox.env"
fi
ok "sandbox.env written (app dir + env for the card to source)"

# 4. link an OOD card into ~/ondemand/dev/.
CARD=""
for cand in "${TOOL}_sandbox" "${TOOL}_dev" "${TOOL}"; do
  [[ -d "${DIR}/ood/apps/${cand}" ]] && { CARD="${cand}"; break; }
done
if [[ ${DO_LINK} -eq 1 && -n "${CARD}" ]]; then
  DEV_DIR="${HOME}/ondemand/dev"
  log "linking OOD card ${CARD} -> ${DEV_DIR}/${TOOL}"
  run mkdir -p "${DEV_DIR}"
  if [[ -e "${DEV_DIR}/${TOOL}" && ! -L "${DEV_DIR}/${TOOL}" ]]; then
    warn "${DEV_DIR}/${TOOL} exists and is not a symlink — leaving it alone"
  else
    run ln -sfn "${DIR}/ood/apps/${CARD}" "${DEV_DIR}/${TOOL}"
    ok "card linked — appears under Develop -> My Sandbox Apps"
  fi
elif [[ -z "${CARD}" ]]; then
  warn "no OOD app dir found under ${DIR}/ood/apps — cannot link a card"
fi

echo
log "Next steps"
if [[ "${CARD}" != "${TOOL}_sandbox" ]]; then
  warn "${TOOL} has no dedicated *_sandbox card yet. The linked '${CARD}' card may"
  warn "assume a shared install path. For a clean per-user sandbox it needs a card that:"
  warn "  (a) sources ~/.config/${TOOL}/sandbox.env (BDTOOLS_APP_DIR + BDTOOLS_APP_ENV), and"
  warn "  (b) takes the cluster as a form value (not a hardcoded cluster:)."
  warn "See docs/BUILDING_A_TOOL.md. vsnp_gui's ood/apps/vsnp_gui_sandbox is the reference."
fi
cat <<EOF
  - In OOD: Develop -> My Sandbox Apps -> ${TOOL} -> set cluster/partition -> Launch.
  - Reference databases (if any) are not auto-pulled; stage them per the tool's docs.
EOF
