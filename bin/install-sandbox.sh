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
#   install-sandbox.sh --dashboard [--cluster ID] [--dry-run]
#
# --dashboard renders the CONSOLIDATED card into ~/ondemand/dev/ — the same one
# front door the server path installs: one session for the whole suite, every
# tool on the node it already allocated, behind one authenticated proxy. Prefer
# it. The per-tool cards below predate it, start a scheduler job each, and have
# no application-level authentication (docs/INSTALL_HPC_OOD.md, "Limitations").
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

TOOL=""; CONDA_BASE_OPT=""; DO_LINK=1; DASHBOARD=0; CLUSTER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --conda-base) CONDA_BASE_OPT="$2"; export CONDA_BASE="$2"; shift 2;;
    --prefix)     export BDTOOLS_HOME="$2"; shift 2;;
    --no-link)    DO_LINK=0; shift;;
    --dashboard)  DASHBOARD=1; shift;;
    --cluster)    CLUSTER="$2"; shift 2;;
    --dry-run)    DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help)    sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)           die "unknown option: $1";;
    *)            TOOL="$1"; shift;;
  esac
done

# --------------------------------------------------------------------------
# --dashboard: the consolidated card, per-user.
# --------------------------------------------------------------------------
# Path A renders this same card with install-server.sh, which rewrites the
# reference site's literals from sites/site.conf. A sandbox install has neither
# root nor a site.conf, so the two values that must not survive at their
# reference defaults are rewritten here instead:
#
#   cluster:      form.yml ships the reference site's id. Nothing rewrites it on
#                 this path, so the card would submit to a cluster this site has
#                 never heard of — and OOD reports that as a form error with no
#                 hint that the id came from another institution.
#   BDTOOLS_REPO  the session script's per-user fallback is
#                 $HOME/bioinformatic_diagnostic_tools. A sandbox user clones the
#                 umbrella wherever they like, so bake in the checkout this
#                 script is running from rather than requiring one exact path.
#   BDTOOLS_HOME  where tool envs were built. The session script falls back to
#                 ~/.local/share/bdtools, but a site that sets BDTOOLS_HOME (a
#                 group tree, a --prefix install) builds its envs elsewhere — and
#                 a batch job does not reliably inherit a variable set in a login
#                 profile. Unbaked, the session finds no python and exits, while
#                 every tool it lists has one.
#
# Rendered as a COPY, never a symlink. The per-tool path below symlinks into the
# checkout, which puts the cluster edit inside a git tree that `bdtools update`
# force-checks-out — docs/INSTALL_HPC_OOD.md has to tell users to re-apply it by
# hand after every update. A copy outside the checkout cannot be reverted that way.
detect_cluster() {
  local d=/etc/ood/config/clusters.d f n=0 last=""
  [[ -d "${d}" ]] || return 1
  for f in "${d}"/*.yml; do
    [[ -e "${f}" ]] || continue
    n=$((n + 1)); last="$(basename "${f}" .yml)"
  done
  [[ ${n} -eq 1 ]] || return 1     # two or more: guessing would be a coin flip
  printf '%s' "${last}"
}

install_dashboard_sandbox() {
  local src="${REPO_DIR}/ood/apps/bdtools_dashboard"
  local dst="${HOME}/ondemand/dev/bdtools_dashboard"
  [[ -d "${src}" ]] || die "no dashboard card at ${src} (is this the umbrella checkout?)"

  if [[ -z "${CLUSTER}" ]]; then
    CLUSTER="$(detect_cluster || true)"
  fi
  if [[ -z "${CLUSTER}" ]]; then
    local avail=""
    [[ -d /etc/ood/config/clusters.d ]] && \
      avail="$(find /etc/ood/config/clusters.d -maxdepth 1 -name '*.yml' -exec basename {} .yml \; 2>/dev/null | sort | tr '\n' ' ')"
    die "cannot tell which cluster to submit to — pass --cluster <id>.
       The id is a filename (minus .yml) in /etc/ood/config/clusters.d on the OOD
       web node: ${avail:-<not readable from this host; check the OOD portal or ask your admin>}
       A card left pointing at another site's cluster id fails at Launch, not now."
  fi

  log "rendering dashboard card -> ${dst} (cluster: ${CLUSTER})"

  # The destination must never resolve back into a checkout of this suite. The
  # sandbox route this command replaces was a documented manual symlink at
  # exactly this path, pointing at ood/apps/bdtools_dashboard — and rendering
  # "into" that symlink followed it into the checkout, where `sed "${f}" > "${out}"`
  # with f and out the SAME FILE truncated every card file to zero bytes before
  # sed could read it. Anyone upgrading from those instructions would have had
  # the card destroyed by the command meant to install it.
  if [[ -L "${dst}" ]]; then
    warn "replacing a symlinked card (the old manual sandbox route) with a rendered copy"
    run rm -f "${dst}"
  fi
  if [[ -d "${dst}" ]]; then
    local _dst_real _src_real
    _dst_real="$(cd "${dst}" && pwd -P)"; _src_real="$(cd "${src}" && pwd -P)"
    [[ "${_dst_real}" != "${_src_real}" ]] \
      || die "refusing to render the card onto its own source (${_src_real})"
    # Only ever replace something that is recognisably this card.
    [[ -f "${dst}/manifest.yml" ]] \
      || die "${dst} exists and is not a bdtools card — move it aside, then re-run."
  fi

  # Stage, then swap: an in-place render leaves behind any file a newer release
  # has removed, and a card half-written is still a card OOD will let someone
  # launch.
  local stage="${dst}.new.$$"
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "  [dry-run] render $(find "${src}" -type f | wc -l | tr -d ' ') file(s) ${src} -> ${dst}"
  else
    rm -rf "${stage}"
    mkdir -p "${stage}"
    local f rel out
    while IFS= read -r f; do
      rel="${f#"${src}"/}"; out="${stage}/${rel}"
      mkdir -p "$(dirname "${out}")"
      sed -e "s|^cluster: .*|cluster: \"${CLUSTER}\"|" \
          -e "s|\${BDTOOLS_REPO:-\${HOME}/bioinformatic_diagnostic_tools}|\${BDTOOLS_REPO:-${REPO_DIR}}|g" \
          -e "s|\${BDTOOLS_HOME:-\${XDG_DATA_HOME:-\${HOME}/.local/share}/bdtools}|\${BDTOOLS_HOME:-${BDTOOLS_HOME}}|g" \
          "${f}" > "${out}"
      [[ -x "${f}" ]] && chmod +x "${out}"
    done < <(find "${src}" -type f)
    rm -rf "${dst}"
    mv "${stage}" "${dst}"
    ok "card rendered as a copy outside the checkout (an update cannot revert it)"
  fi

  # Verify what we WROTE, not what we intended: both rewrites are silent no-ops
  # if the upstream text ever moves, and a card that still names another site's
  # cluster looks perfectly fine until someone clicks Launch.
  if [[ ${DRY_RUN} -eq 0 ]]; then
    local got
    got="$(sed -n 's/^cluster: *"\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' "${dst}/form.yml" | head -1)"
    [[ "${got}" == "${CLUSTER}" ]] \
      || die "cluster rewrite did not take (form.yml says '${got}') — card left at ${dst}"
    ok "cluster: ${got}"
    if grep -q "BDTOOLS_REPO:-${REPO_DIR}" "${dst}/template/script.sh.erb"; then
      ok "umbrella baked in: ${REPO_DIR}"
    else
      warn "could not bake the checkout path into script.sh.erb — the session will"
      warn "  look for the umbrella at \$HOME/bioinformatic_diagnostic_tools."
      warn "  Fix: export BDTOOLS_REPO=${REPO_DIR} in the session, or move the checkout."
    fi
    if grep -q "BDTOOLS_HOME:-${BDTOOLS_HOME}" "${dst}/template/script.sh.erb"; then
      ok "tool envs searched under: ${BDTOOLS_HOME}/checkouts"
    else
      warn "could not bake BDTOOLS_HOME into script.sh.erb — the session will look for"
      warn "  tool envs under \$HOME/.local/share/bdtools/checkouts, not ${BDTOOLS_HOME}."
    fi
    "${PYBIN:-python3}" "${KT_BIN_DIR}/lib/check_cards.py" "${dst}" \
      || warn "the rendered card did not pass check_cards (above) — fix before launching"
  fi

  echo
  log "Next steps"
  cat <<EOF
  - The dashboard needs a python with starlette+httpx+uvicorn; any tool's env
    supplies it:  bdtools install --sandbox <tool>
  - In OOD: Develop -> My Sandbox Apps -> Diagnostic Tools Dashboard -> Launch.
  - No Develop menu? Enabling it is an admin action — see docs/INSTALL_HPC_OOD.md
    ("Do you have permission?").
EOF
}

if [[ ${DASHBOARD} -eq 1 ]]; then
  [[ -z "${TOOL}" ]] || die "--dashboard installs the one consolidated card; drop the tool name
       (to build a tool's environment as well: bdtools install --sandbox ${TOOL})"
  install_dashboard_sandbox
  exit 0
fi

[[ -n "${TOOL}" ]] || die "name a tool (see: bdtools list), or pass --dashboard"
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
