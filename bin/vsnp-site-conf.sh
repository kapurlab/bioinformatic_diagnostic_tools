#!/usr/bin/env bash
# vsnp-site-conf.sh — write vsnp_gui's deploy/site.conf from this site's
# sites/site.conf, so Step 3b builds the vsnp3 env where the launcher looks.
#
#   bash bin/vsnp-site-conf.sh [--site-conf PATH] [--dry-run]
#
# Step 3b (docs/INSTALL_HPC_OOD.md) runs vsnp_gui's own installer, which reads
# its OWN site.conf, and the guide said "edit it so these match". Copying values
# by hand between two files is how VSNP3_PREFIX ends up somewhere the launcher
# never checks — and a site tool now launches only from site state
# (tool_launch._site_tools_root), so that is the difference between vSNP opening
# for every user and for none (USDA, 2026-10-29). Three more traps in the
# example file, each of which this writes around:
#   * ADMIN_USER= and SERVERNAME= ship empty, and install_ood.sh refuses to run
#     with either unset — even for the toolchain phase, which uses neither.
#   * A CONDA_BASE without bin/conda does not fail the toolchain phase: it WARNS
#     "no conda found; skipping vsnp3 env creation" and carries on. Checked here.
#   * NPM_BIN=/usr/bin/npm makes the toolchain phase run `npm ci && npm run
#     build` as root inside the vsnp_gui checkout, rewriting the committed
#     frontend/dist — a dirty checkout every later `bdtools sync` refuses to
#     update. The prebuilt dist is what every other install path serves, so the
#     rebuild is skipped (NPM_BIN empty).
#
# An existing deploy/site.conf is edited IN PLACE — only the keys below, with a
# timestamped backup — so a site's own notes and settings survive. Otherwise one
# is started from deploy/site.conf.example. Nothing is read from your shell:
# sites/site.conf is sourced in a clean environment, so a CONDA_BASE your login
# profile exports cannot stand in for the site's.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SITE_CONF="${REPO_DIR}/sites/site.conf"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-conf) SITE_CONF="${2:?--site-conf needs a path}"; shift 2;;
    --site-conf=*) SITE_CONF="${1#--site-conf=}"; shift;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) die "unknown option: $1 (see --help)";;
  esac
done
[[ -f "${SITE_CONF}" ]] || die "no ${SITE_CONF} — write the site's sites/site.conf first (docs/INSTALL_HPC_OOD.md Step 2)"

# The site's values, from a clean environment: names only this file defines.
KEYS=(SITE_NAME SITE_DISPLAY SITE_ROOT CLUSTER_NAME TOOLS_ROOT CONDA_BASE
      ADMINS_GROUP MEMBERS_GROUP ADMIN_USER SERVERNAME)
while IFS=$'\t' read -r k v; do
  [[ -n "${k}" ]] && printf -v "S_${k}" '%s' "${v}"
done < <(env -i HOME="${HOME}" PATH=/usr/bin:/bin bash -c '
  set -a; . "$1" >/dev/null 2>&1; shift
  for v in "$@"; do printf "%s\t%s\n" "$v" "${!v:-}"; done' _ "${SITE_CONF}" "${KEYS[@]}")

[[ -n "${S_SITE_ROOT:-}" ]] || die "SITE_ROOT is not set in ${SITE_CONF}"
[[ -n "${S_CLUSTER_NAME:-}" ]] || die "CLUSTER_NAME is not set in ${SITE_CONF}"
TOOLS="${S_TOOLS_ROOT:-${S_SITE_ROOT}/tools}"
GUI="${TOOLS}/vsnp_gui"
DEST="${GUI}/deploy/site.conf"
[[ -f "${GUI}/deploy/site.conf.example" ]] \
  || die "vsnp_gui is not at ${GUI} — install it first: sudo bin/bdtools install vsnp_gui --server --site-conf ${SITE_CONF}"

PIN="$(sed -n 's/.*bioconda::vsnp3=\([0-9][0-9.]*\).*/\1/p' "${REPO_DIR}/tools.yml" | head -1)"
[[ -n "${PIN}" ]] || die "no bioconda::vsnp3=<version> pin in ${REPO_DIR}/tools.yml"

CB="${S_CONDA_BASE:-}"
if [[ -z "${CB}" || ! -x "${CB}/bin/conda" ]]; then
  die "CONDA_BASE in ${SITE_CONF} is '${CB:-<unset>}', which has no bin/conda.
       vSNP's installer would skip building the env with only a warning. Set
       CONDA_BASE to the site's conda (on storage the compute nodes mount)."
fi
owner="$(stat -c %U "${CB}" 2>/dev/null || stat -f %Su "${CB}" 2>/dev/null || echo '?')"
if [[ "${owner}" != "root" && "${owner}" != "?" ]]; then
  warn "CONDA_BASE ${CB} belongs to ${owner}. Step 3b runs as root, so root will write"
  info "  into that conda's package cache, and ${owner}'s own conda commands can then"
  info "  hit root-owned files there. A site-owned conda avoids that."
fi

# key -> value to write. ADMINS_GROUP/MEMBERS_GROUP only when the site sets them;
# otherwise the example's own ${SITE_NAME}-admins defaults stand.
names=(SITE_NAME SITE_DISPLAY SITE_ROOT CLUSTER_NAME CONDA_BASE VSNP3_PREFIX
       VSNP3_VERSION VSNP_GUI_DIR ADMIN_USER SERVERNAME NPM_BIN)
vals=("${S_SITE_NAME:-}" "${S_SITE_DISPLAY:-}" "${S_SITE_ROOT}" "${S_CLUSTER_NAME}" "${CB}"
      "${TOOLS}/vsnp3" "${PIN}" "${GUI}"
      "${S_ADMIN_USER:-${SUDO_USER:-${USER:-$(id -un)}}}"
      "${S_SERVERNAME:-$(hostname -f 2>/dev/null || hostname)}" "")
[[ -n "${S_ADMINS_GROUP:-}" ]] && { names+=(ADMINS_GROUP); vals+=("${S_ADMINS_GROUP}"); }
[[ -n "${S_MEMBERS_GROUP:-}" ]] && { names+=(MEMBERS_GROUP); vals+=("${S_MEMBERS_GROUP}"); }

src="${DEST}"; [[ -f "${DEST}" ]] || src="${GUI}/deploy/site.conf.example"
tmp="$(mktemp "${TMPDIR:-/tmp}/vsnp-site-conf.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT
# Replace each named key's line where it stands (order matters: later lines
# expand ${SITE_ROOT} and friends), append any the file lacks.
args=()
for i in "${!names[@]}"; do
  v="${vals[$i]}"; v="${v//\\/\\\\}"; v="${v//\"/\\\"}"; v="${v//\$/\\\$}"; v="${v//\`/\\\`}"
  args+=("${names[$i]}=\"${v}\"")
done
awk -v n="${#args[@]}" '
  BEGIN { for (i = 1; i <= n; i++) { line = ARGV[i]; key = substr(line, 1, index(line, "=") - 1)
                                     want[key] = line; order[i] = key; ARGV[i] = "" } }
  match($0, /^[A-Za-z_][A-Za-z0-9_]*=/) {
    key = substr($0, 1, RLENGTH - 1)
    if (key in want) { print want[key]; done[key] = 1; next }
  }
  { print }
  END { hdr = 0
        for (i = 1; i <= n; i++) if (!(order[i] in done)) {
          if (!hdr) { print ""; print "# set by bioinformatic_diagnostic_tools/bin/vsnp-site-conf.sh"; hdr = 1 }
          print want[order[i]] } }
' "${args[@]}" "${src}" > "${tmp}"

# Verify what would be WRITTEN, not what was intended: the launcher checks
# <TOOLS_ROOT>/vsnp3, so that is the one value that must come out exactly.
got="$(env -i PATH=/usr/bin:/bin bash -c 'set -a; . "$1" >/dev/null 2>&1; printf %s "${VSNP3_PREFIX:-}"' _ "${tmp}")"
[[ "${got}" == "${TOOLS}/vsnp3" ]] \
  || die "the written file resolves VSNP3_PREFIX to '${got}', not ${TOOLS}/vsnp3 — not written"

log "vsnp_gui site.conf: ${DEST}"
for i in "${!names[@]}"; do info "  ${names[$i]}=${vals[$i]}"; done
info "  (NPM_BIN empty: the shipped frontend/dist is served; no rebuild as root)"
if [[ ${DRY_RUN} -eq 1 ]]; then
  warn "dry run — nothing written"
  exit 0
fi
if [[ -f "${DEST}" ]]; then
  bak="${DEST}.bak.$(date +%Y%m%d_%H%M%S)"
  cp -p "${DEST}" "${bak}" 2>/dev/null || die "cannot write in ${GUI}/deploy — re-run with sudo: sudo bash $0"
  ok "previous copy kept: ${bak}"
fi
cat "${tmp}" > "${DEST}" 2>/dev/null || die "cannot write ${DEST} — re-run with sudo: sudo bash $0"
ok "wrote ${DEST}"
echo
log "Next — Step 3b, dry run first:"
info "  cd ${GUI} && sudo ./deploy/install_ood.sh --site-conf deploy/site.conf --dry-run toolchain"
info "  cd ${GUI} && sudo ./deploy/install_ood.sh --site-conf deploy/site.conf toolchain"
