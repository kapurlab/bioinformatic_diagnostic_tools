#!/usr/bin/env bash
# install-server.sh — install ONE tool as a system Open OnDemand app.
#
# The sysadmin / bare-metal path. Installs a tool's source + conda env at
# TOOLS_ROOT/<tool> and renders its OOD card into the sys-apps dir, rewriting
# the Kapur Lab literals (paths, cluster name, groups) from the site config.
# Run as root (it writes /var/www/ood/apps/sys). Always --dry-run first.
#
#   install-server.sh <tool> [--site-conf PATH] [--dry-run] [phase ...]
#
# Phases (default: all): preflight toolchain app verify
#   preflight  OOD core present? conda/node? cluster defined? sys-apps writable?
#   toolchain  checkout the pinned tool at TOOLS_ROOT/<tool>; build env+frontend
#   app        render ood/apps/<tool>/* into SYS_APPS_DIR/<tool> (site subst)
#   verify     card + env + frontend present
#
# SCOPE: this installs the *app* (the reusable, multi-tool part). It does NOT
# manage OOD core, the scheduler, auth, Unix groups, storage/quotas, dashboard
# branding, or clusters.d — institutional sites already own those, and a
# bare-metal lab server should run ood-core/bootstrap_ood_core.sh (+ the
# site-bootstrap phases of vsnp_gui/deploy/install_ood.sh) for them.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SITE_CONF="${REPO_DIR}/sites/site.conf"
TOOL=""; PHASES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-conf) SITE_CONF="$2"; shift 2;;
    --dry-run)   DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help)   sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)          die "unknown option: $1";;
    *)           if [[ -z "${TOOL}" ]] && manifest_has "$1"; then TOOL="$1"; else PHASES+=("$1"); fi; shift;;
  esac
done
[[ -n "${TOOL}" ]] || die "name a tool (see: bdtools list)"
[[ ${#PHASES[@]} -gt 0 ]] || PHASES=(preflight toolchain app verify)

[[ -f "${SITE_CONF}" ]] || die "site config not found: ${SITE_CONF}
       cp ${REPO_DIR}/sites/site.conf.example <path> and edit it, then pass --site-conf <path>."
# shellcheck disable=SC1090
source "${SITE_CONF}"

# Required site vars
_missing=()
for v in SITE_NAME SITE_DISPLAY SITE_ROOT CLUSTER_NAME TOOLS_ROOT SYS_APPS_DIR; do
  [[ -z "${!v:-}" ]] && _missing+=("$v")
done
[[ ${#_missing[@]} -eq 0 ]] || die "required site.conf vars unset: ${_missing[*]}"

DIR="${TOOLS_ROOT}/${TOOL}"
REPO="$(manifest_get "${TOOL}" repo)"
VERSION="$(manifest_get "${TOOL}" version)"
APP_DST_BASE="${SYS_APPS_DIR}"
OOD_APPS=( $(manifest_get "${TOOL}" ood_apps) )

# subst — rewrite Kapur Lab literals to this site's values. Longest-match first
# so /srv/kapurlab/tools and the group names are consumed before bare 'kapurlab'.
# Mirrors vsnp_gui/deploy/install_ood.sh:subst. Branding PROSE is left alone.
subst() {
  sed -e "s|/srv/kapurlab/tools|${TOOLS_ROOT}|g" \
      -e "s|/srv/kapurlab|${SITE_ROOT}|g" \
      -e "s|kapurlab-admins|${ADMINS_GROUP:-kapurlab-admins}|g" \
      -e "s|kapurlab-members|${MEMBERS_GROUP:-kapurlab-members}|g" \
      -e "s|\"wgs3\"|\"${CLUSTER_NAME}\"|g" \
      -e "s|WGS3|${SITE_DISPLAY}|g" \
      -e "s|wgs3|${CLUSTER_NAME}|g" \
      -e "s|kapurlab|${SITE_NAME}|g" \
      -e "s|vxk1|${ADMIN_USER:-vxk1}|g" \
      -e "s|100\.68\.171\.59|${SERVERNAME:-100.68.171.59}|g" \
      "$1"
}

# ---------------------------------------------------------------------------
phase_preflight() {
  log "preflight — ${TOOL}"
  [[ -d /etc/ood/config ]] && ok "OOD core present (/etc/ood/config)" \
    || warn "OOD core not detected — institutional sites have it; bare-metal: run ood-core/bootstrap_ood_core.sh"
  if [[ -f "/etc/ood/config/clusters.d/${CLUSTER_NAME}.yml" ]]; then
    ok "cluster '${CLUSTER_NAME}' is defined"
  else
    warn "no clusters.d/${CLUSTER_NAME}.yml — the card's form pins cluster '${CLUSTER_NAME}'."
    warn "  institutional: set CLUSTER_NAME to the site's existing cluster id."
    warn "  bare-metal: define it (ood-core bootstrap / vsnp_gui install_ood.sh)."
  fi
  detect_conda >/dev/null 2>&1 && ok "conda available" || warn "no conda — toolchain phase needs miniforge at ${CONDA_BASE}"
  command -v npm >/dev/null 2>&1 && ok "npm available" || warn "no npm — frontend build needs Node"
  if [[ ${DRY_RUN} -eq 0 && "$(id -u)" -ne 0 && ! -w "${SYS_APPS_DIR}" ]]; then
    warn "${SYS_APPS_DIR} not writable as $(whoami) — the app phase needs sudo"
  fi
}

phase_toolchain() {
  need_writable "${TOOLS_ROOT}" toolchain
  log "toolchain — source + env + frontend at ${DIR}"
  if [[ -d "${DIR}/.git" ]]; then
    ok "source present: ${DIR} ($(git -C "${DIR}" describe --tags --always 2>/dev/null || echo '?'))"
  else
    log "cloning ${TOOL} @ ${VERSION} -> ${DIR}"
    run mkdir -p "${TOOLS_ROOT}"
    run git clone --branch "${VERSION}" --depth 1 "${REPO}" "${DIR}" || die "git clone failed"
  fi
  # Build env + frontend via the tool's own no-sudo installer (shared env at <dir>/env).
  if [[ -x "${DIR}/deploy/install.sh" ]]; then
    local a=(); [[ ${DRY_RUN} -eq 1 ]] && a+=(--dry-run)
    [[ -n "${CONDA_BASE:-}" ]] && a+=(--conda-base "${CONDA_BASE}")
    run "${DIR}/deploy/install.sh" "${a[@]}" || die "${TOOL} deploy/install.sh failed"
  else
    warn "${TOOL} has no deploy/install.sh — build its env+frontend manually,"
    warn "  or (for vsnp_gui) use vsnp_gui/deploy/install_ood.sh which builds the vsnp3 env."
  fi
}

phase_app() {
  need_writable "${SYS_APPS_DIR}" app
  log "app — render OOD card(s) into ${APP_DST_BASE}"
  local app src dst f
  for app in "${OOD_APPS[@]}"; do
    src="${DIR}/ood/apps/${app}"
    dst="${APP_DST_BASE}/${app}"
    [[ -d "${src}" ]] || { warn "missing app source ${src} — skipping"; continue; }
    # back up an existing card
    if [[ -d "${dst}" && ${DRY_RUN} -eq 0 ]]; then
      local bak="/var/backups/ood/${app}/$(date +%Y%m%d_%H%M%S 2>/dev/null || echo backup)"
      run mkdir -p "${bak}"; run cp -a "${dst}/." "${bak}/" 2>/dev/null || true
      ok "backed up existing card -> ${bak}"
    fi
    log "rendering ${app} (site subst) -> ${dst}"
    if [[ ${DRY_RUN} -eq 1 ]]; then
      echo "  [dry-run] subst+install $(find "${src}" -type f | wc -l | tr -d ' ') file(s) ${src} -> ${dst}"
    else
      while IFS= read -r f; do
        local rel out
        rel="${f#${src}/}"; out="${dst}/${rel}"
        mkdir -p "$(dirname "${out}")"
        subst "${f}" > "${out}"
        # preserve executability of template scripts
        [[ -x "${f}" ]] && chmod +x "${out}"
      done < <(find "${src}" -type f)
      chmod -R go+rX "${dst}"
      ok "installed card ${dst}"
    fi
  done
}

phase_verify() {
  log "verify — ${TOOL}"
  [[ -x "${DIR}/env/bin/python" ]] && ok "env python present" || warn "no ${DIR}/env/bin/python (toolchain not built?)"
  [[ -f "${DIR}/frontend/dist/index.html" ]] && ok "frontend built" || warn "frontend/dist missing"
  local app
  for app in "${OOD_APPS[@]}"; do
    [[ -f "${APP_DST_BASE}/${app}/manifest.yml" ]] && ok "card installed: ${app}" || warn "card missing: ${app}"
  done
  info "Final manual check: launch the card in OOD and confirm the GUI loads through /rnode."
}

# ---------------------------------------------------------------------------
echo "bdtools install --server  (tool: ${TOOL}; site: ${SITE_DISPLAY} / ${SITE_ROOT})"
[[ ${DRY_RUN} -eq 1 ]] && warn "DRY RUN — nothing will be modified"
echo "phases: ${PHASES[*]}"; echo

for ph in "${PHASES[@]}"; do
  case "${ph}" in
    preflight) phase_preflight;;
    toolchain) phase_toolchain;;
    app)       phase_app;;
    verify)    phase_verify;;
    *)         die "unknown phase: ${ph}";;
  esac
  echo
done
log "done."
[[ ${DRY_RUN} -eq 1 ]] && echo "Re-run without --dry-run as root to apply."
exit 0
