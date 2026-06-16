#!/usr/bin/env bash
# site-init.sh — one-time, site-level bootstrap for a Kapur Lab tool server.
#
# The pieces that are shared by the WHOLE suite and done ONCE per server (not
# per tool): Unix groups, the shared storage subtree, and a starter dashboard
# branding snippet. Promoted/generalized from the site phases of
# vsnp_gui/deploy/install_ood.sh. Run as root. Always --dry-run first.
#
#   site-init.sh [--site-conf PATH] [--dry-run] [phase ...]
#
# Phases (default: all): preflight groups storage portal verify
#   preflight  OOD core present? root? site.conf sane?
#   groups     create MEMBERS_GROUP + ADMINS_GROUP
#   storage    create the SITE_ROOT subtree (setgid shared dirs)
#   portal     write a starter dashboard branding snippet (title = SITE_DISPLAY)
#   verify     groups + dirs present
#
# NOT here: OOD core itself (ood-core/bootstrap_ood_core.sh), the scheduler,
# auth, per-tool app cards (`bdtools install --server <tool>`), or reference
# data. Deep dashboard branding/prose stays a manual edit (this writes a
# minimal, correct starting point only).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SITE_CONF="${REPO_DIR}/sites/site.conf"
PHASES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-conf) SITE_CONF="$2"; shift 2;;
    --dry-run)   DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help)   sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)          die "unknown option: $1";;
    *)           PHASES+=("$1"); shift;;
  esac
done
[[ ${#PHASES[@]} -gt 0 ]] || PHASES=(preflight groups storage portal verify)

[[ -f "${SITE_CONF}" ]] || die "site config not found: ${SITE_CONF}
       cp ${REPO_DIR}/sites/site.conf.example <path> and edit it, then pass --site-conf <path>."
# shellcheck disable=SC1090
source "${SITE_CONF}"

_missing=()
for v in SITE_NAME SITE_DISPLAY SITE_ROOT TOOLS_ROOT; do
  [[ -z "${!v:-}" ]] && _missing+=("$v")
done
[[ ${#_missing[@]} -eq 0 ]] || die "required site.conf vars unset: ${_missing[*]}"

# Derived
MEMBERS_GROUP="${MEMBERS_GROUP:-${SITE_NAME}-members}"
ADMINS_GROUP="${ADMINS_GROUP:-${SITE_NAME}-admins}"
PROJECTS_ROOT="${PROJECTS_ROOT:-${SITE_ROOT}/projects}"
DATABASES_ROOT="${DATABASES_ROOT:-${SITE_ROOT}/databases}"
OOD_CFG_DIR="${OOD_CFG_DIR:-/etc/ood/config}"

need_root() {
  [[ ${DRY_RUN} -eq 1 ]] && return 0
  [[ "$(id -u)" -eq 0 ]] || die "phase '$1' needs root (groupadd / /etc writes); run under sudo"
}
# mkdir_grp DIR GROUP MODE — idempotent setgid shared dir.
mkdir_grp() {
  run mkdir -p "$1"
  if [[ -n "$2" ]] && getent group "$2" >/dev/null 2>&1; then run chgrp "$2" "$1"; fi
  run chmod "$3" "$1"
}

phase_preflight() {
  log "preflight — site bootstrap for ${SITE_DISPLAY} (${SITE_ROOT})"
  [[ -d "${OOD_CFG_DIR}" ]] && ok "OOD core present (${OOD_CFG_DIR})" \
    || warn "OOD core not detected — run ood-core/bootstrap_ood_core.sh first"
  if [[ ${DRY_RUN} -eq 0 && "$(id -u)" -ne 0 ]]; then
    warn "not root — groups/portal phases will need sudo"
  fi
  info "groups: ${MEMBERS_GROUP}, ${ADMINS_GROUP}"
  info "tree:   ${SITE_ROOT} {tools=${TOOLS_ROOT}, projects=${PROJECTS_ROOT}, databases=${DATABASES_ROOT}}"
}

phase_groups() {
  need_root groups
  log "groups — ${MEMBERS_GROUP}, ${ADMINS_GROUP}"
  for g in "${MEMBERS_GROUP}" "${ADMINS_GROUP}"; do
    if getent group "${g}" >/dev/null 2>&1; then ok "group ${g} exists"
    else run groupadd "${g}"; ok "created group ${g}"; fi
  done
}

phase_storage() {
  need_writable "${SITE_ROOT}" storage
  log "storage — ${SITE_ROOT} shared subtree (setgid)"
  mkdir_grp "${SITE_ROOT}"      "${MEMBERS_GROUP}" 0755
  mkdir_grp "${TOOLS_ROOT}"     "${ADMINS_GROUP}"  2775
  mkdir_grp "${PROJECTS_ROOT}"  "${MEMBERS_GROUP}" 2775
  mkdir_grp "${DATABASES_ROOT}" "${MEMBERS_GROUP}" 2775
  ok "shared tree created (setgid; new files inherit the group)"
}

phase_portal() {
  need_root portal
  log "portal — starter dashboard branding"
  local dest="${OOD_CFG_DIR}/ondemand.d/${SITE_NAME}.yml"
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "  [dry-run] write ${dest} (dashboard_title: ${SITE_DISPLAY})"
  else
    run mkdir -p "${OOD_CFG_DIR}/ondemand.d"
    cat > "${dest}" <<YAML
# Starter dashboard branding written by 'bdtools site-init'. Minimal + correct;
# customize freely (logo, links, MOTD). See OnDemand dashboard customization docs.
dashboard_title: "${SITE_DISPLAY} Pipelines"
dashboard_header_logo: ""
brand_bg_color: "#1f6feb"
brand_link_active_bg_color: "#114a99"
YAML
    ok "wrote ${dest}"
  fi
  info "Deep branding (logo, home-page prose, pinned-apps layout) remains a manual edit."
}

phase_verify() {
  log "verify"
  for g in "${MEMBERS_GROUP}" "${ADMINS_GROUP}"; do
    getent group "${g}" >/dev/null 2>&1 && ok "group ${g}" || warn "group ${g} missing"
  done
  for d in "${SITE_ROOT}" "${TOOLS_ROOT}" "${PROJECTS_ROOT}" "${DATABASES_ROOT}"; do
    [[ -d "${d}" ]] && ok "dir ${d}" || warn "dir ${d} missing"
  done
  info "Next: bdtools install --server all --site-conf <file>   (per-tool app install)"
}

echo "bdtools site-init  (${SITE_DISPLAY} / ${SITE_ROOT})"
[[ ${DRY_RUN} -eq 1 ]] && warn "DRY RUN — nothing will be modified"
echo "phases: ${PHASES[*]}"; echo
for ph in "${PHASES[@]}"; do
  case "${ph}" in
    preflight) phase_preflight;;
    groups)    phase_groups;;
    storage)   phase_storage;;
    portal)    phase_portal;;
    verify)    phase_verify;;
    *)         die "unknown phase: ${ph}";;
  esac
  echo
done
log "done."
[[ ${DRY_RUN} -eq 1 ]] && echo "Re-run without --dry-run as root to apply."
exit 0
