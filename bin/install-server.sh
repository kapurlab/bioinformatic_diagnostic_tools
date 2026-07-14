#!/usr/bin/env bash
# install-server.sh — install ONE tool as a system Open OnDemand app.
#
# The sysadmin / bare-metal path. Installs a tool's source + conda env at
# TOOLS_ROOT/<tool> and renders its OOD card into the sys-apps dir, rewriting
# the Kapur Lab literals (paths, cluster name, groups) from the site config.
# Run as root (it writes /var/www/ood/apps/sys). Always --dry-run first.
#
#   install-server.sh <tool> [--site-conf PATH] [--with-dev] [--no-card] [--dry-run] [phase ...]
#   install-server.sh --dashboard [--site-conf PATH] [--dry-run]
#
# Phases (default: all): preflight toolchain app verify
#   preflight  OOD core present? conda/node? cluster defined? sys-apps writable?
#   toolchain  checkout the pinned tool at TOOLS_ROOT/<tool>; build env+frontend
#   app        render ood/apps/<tool>/* into SYS_APPS_DIR/<tool> (site subst)
#   verify     card + env + frontend present
#
# CONSOLIDATED DASHBOARD (recommended): with --dashboard, installs the single
# umbrella-owned card (SYS_APPS_DIR/bdtools_dashboard) that allocates ONE node per
# session and runs every tool on it behind one authenticated reverse proxy. Build
# each tool's env first with `--no-card` (env only, no per-tool card):
#   for t in $(bin/bdtools list ...); do install-server.sh "$t" --no-card; done
#   install-server.sh --dashboard
# Per-tool cards are still available (omit --no-card) for a dedicated single-tool
# session, but are no longer required for routine use.
#
# By default ONLY the production card(s) (tools.yml `ood_apps`) are installed —
# that is all a normal user sees in the dashboard. Pass --with-dev to ALSO
# install the developer branch-picker card(s) (`dev_apps`, e.g. <tool>_dev).
# Developers only; do not use on a site meant for routine diagnostic users.
#
# SCOPE: this installs the *app* (the reusable, multi-tool part). It does NOT
# manage OOD core, the scheduler, auth, Unix groups, storage/quotas, dashboard
# branding, or clusters.d — institutional sites already own those, and a
# bare-metal lab server should run ood-core/bootstrap_ood_core.sh (+ the
# site-bootstrap phases of vsnp_gui/deploy/install_ood.sh) for them.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SITE_CONF="${REPO_DIR}/sites/site.conf"
TOOL=""; PHASES=(); WITH_DEV=0; DASHBOARD=0; NO_CARD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-conf) SITE_CONF="$2"; shift 2;;
    --with-dev)  WITH_DEV=1; shift;;
    --dashboard) DASHBOARD=1; shift;;   # install the umbrella's consolidated dashboard card
    --no-card)   NO_CARD=1; shift;;     # build a tool's env but do NOT install its per-tool card
    --dry-run)   DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help)   sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)          die "unknown option: $1";;
    *)           if [[ -z "${TOOL}" ]] && manifest_has "$1"; then TOOL="$1"; else PHASES+=("$1"); fi; shift;;
  esac
done
if [[ ${DASHBOARD} -eq 1 ]]; then
  TOOL="bdtools_dashboard"        # the umbrella-owned card; no tool checkout needed
  [[ ${#PHASES[@]} -gt 0 ]] || PHASES=(preflight app verify)
else
  [[ -n "${TOOL}" ]] || die "name a tool (see: bdtools list), or pass --dashboard"
  if [[ ${#PHASES[@]} -eq 0 ]]; then
    if [[ ${NO_CARD} -eq 1 ]]; then PHASES=(preflight toolchain verify)   # env only, no card
    else PHASES=(preflight toolchain app verify); fi
  fi
fi

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

APP_DST_BASE="${SYS_APPS_DIR}"
if [[ ${DASHBOARD} -eq 1 ]]; then
  # The umbrella checkout IS the "source"; its card lives under ood/apps/.
  DIR="${REPO_DIR}"; REPO=""; VERSION=""
  OOD_APPS=(bdtools_dashboard); DEV_APPS=()
else
  DIR="${TOOLS_ROOT}/${TOOL}"
  REPO="$(manifest_get "${TOOL}" repo)"
  VERSION="$(manifest_get "${TOOL}" version)"
  # Production cards always; developer (branch-picker) cards only with --with-dev.
  OOD_APPS=( $(manifest_get "${TOOL}" ood_apps) )
  DEV_APPS=( $(manifest_get "${TOOL}" dev_apps) )
  if [[ ${WITH_DEV} -eq 1 && ${#DEV_APPS[@]} -gt 0 ]]; then
    OOD_APPS+=( "${DEV_APPS[@]}" )
  fi
fi

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
  if [[ ${WITH_DEV} -eq 1 ]]; then
    if [[ ${#DEV_APPS[@]} -gt 0 ]]; then
      warn "--with-dev: ALSO installing developer card(s): ${DEV_APPS[*]} (developers only)"
    else
      info "--with-dev given but ${TOOL} declares no dev_apps — production card(s) only"
    fi
  else
    ok "production card(s) only: ${OOD_APPS[*]} (dev cards hidden; use --with-dev to add them)"
  fi
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
    # conda base: an explicit CONDA_BASE (from site conf) wins; otherwise resolve a
    # real one so the tool installer doesn't fall back to its ~/miniforge3 default
    # and die "conda not found" on a miniconda3 host.
    if [[ -n "${CONDA_BASE:-}" ]]; then a+=(--conda-base "${CONDA_BASE}")
    elif grep -q -- '--conda-base' "${DIR}/deploy/install.sh" 2>/dev/null; then
      local _cbase; _cbase="$(conda_base_dir)"; [[ -n "${_cbase}" ]] && a+=(--conda-base "${_cbase}")
    fi
    # Skip the frontend rebuild when a prebuilt dist ships. All GUIs use vite
    # base:"./", so the shipped dist is path-portable (works under any OOD
    # sub-path) — this avoids a hard Node dependency on the server without a
    # rebuild being needed. Mirrors install-local.sh.
    if [[ -f "${DIR}/frontend/dist/index.html" ]] \
       && grep -q -- '--skip-frontend' "${DIR}/deploy/install.sh" 2>/dev/null; then
      a+=(--skip-frontend)
    fi
    run "${DIR}/deploy/install.sh" ${a[@]+"${a[@]}"} || die "${TOOL} deploy/install.sh failed"
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
  if [[ ${DASHBOARD} -eq 1 ]]; then
    [[ -f "${DIR}/bin/ood_dashboard/app.py" ]] && ok "dashboard app present" \
      || warn "missing ${DIR}/bin/ood_dashboard/app.py"
    [[ -f "${APP_DST_BASE}/bdtools_dashboard/manifest.yml" ]] && ok "card installed: bdtools_dashboard" \
      || warn "card missing: bdtools_dashboard"
    local anypy="" p
    for p in "${TOOLS_ROOT}"/*/env/bin/python "${TOOLS_ROOT}"/vsnp3/bin/python; do
      [[ -x "$p" ]] && "$p" -c 'import starlette,httpx,uvicorn' 2>/dev/null && { anypy="$p"; break; }
    done
    [[ -n "${anypy}" ]] && ok "a python with the dashboard deps exists (${anypy})" \
      || warn "no tool env has starlette+httpx+uvicorn yet — install a tool first (e.g. bdtools install mlst_gui --server --no-card)"
    info "Final manual check: launch 'Diagnostic Tools Dashboard' in OOD; confirm the landing page loads and a tool opens through /rnode."
    return
  fi
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
