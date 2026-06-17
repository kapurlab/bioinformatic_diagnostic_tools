#!/usr/bin/env bash
# setup-databases.sh — download the suite's shared reference databases and
# point each GUI at them. These are large (tens of GB) and licensed/distributed
# by third parties, so they are NOT bundled with the tools — they're fetched
# on demand into a location you choose, once per machine (or once per lab).
#
#   setup-databases.sh [--home | --shared | --root DIR] [--dry-run] [DB ...]
#
# Location (where the databases are written):
#   --home        ~/databases                      (per-user; a laptop)
#   --shared      /srv/kapurlab/databases           (one copy for everyone)
#   --root DIR    a custom root
#   (no flag, interactive TTY: you're asked home vs shared)
#
# DB (which databases; default: all):
#   kraken        Kraken2 k2_standard_08gb        -> <root>/kraken2/k2_standard_08gb
#   blast         BLAST ref_prok_rep_genomes      -> <root>/blast/ref_prok_rep_genomes
#   vsnp-refs     USDA-VS vSNP_reference_options  -> <root>/vsnp3/reference_options
#   vsnp-deps     USDA-VS vsnp3 test dependencies -> <root>/vsnp3/vsnp_dependencies
#
# Consumers wired automatically:
#   kraken,blast  -> kraken_id_parse_gui  (~/.config/kraken_id_parse_gui/config.json)
#   vsnp-*        -> vsnp_gui             (reference locations + config.json)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

# ---- sources (single source of truth; mirror these in the README) ----------
KRAKEN_URL="https://genome-idx.s3.amazonaws.com/kraken/k2_standard_08_GB_20260226.tar.gz"
BLAST_DBNAME="ref_prok_rep_genomes"
VSNP_REFS_REPO="https://github.com/USDA-VS/vSNP_reference_options.git"
VSNP_DEPS_REPO="https://github.com/USDA-VS/vsnp3_test_dataset.git"

SHARED_ROOT_DEFAULT="/srv/kapurlab/databases"
HOME_ROOT_DEFAULT="${HOME}/databases"

ROOT=""; LOC=""; WANT=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --home)    LOC="home";   shift;;
    --shared)  LOC="shared"; shift;;
    --root)    ROOT="$2";    shift 2;;
    --dry-run) DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    kraken|blast|vsnp-refs|vsnp-deps) WANT+=("$1"); shift;;
    all) shift;;                       # explicit "all" == default
    -*)  die "unknown option: $1";;
    *)   die "unknown database: $1 (kraken|blast|vsnp-refs|vsnp-deps|all)";;
  esac
done
[[ ${#WANT[@]} -gt 0 ]] || WANT=(kraken blast vsnp-refs vsnp-deps)

# ---- resolve the install root ---------------------------------------------
if [[ -z "${ROOT}" ]]; then
  if [[ -z "${LOC}" ]]; then
    if [[ -t 0 && -t 1 ]]; then
      echo "Where should reference databases be installed?"
      echo "  1) Home    (${HOME_ROOT_DEFAULT})        — just this user"
      echo "  2) Shared  (${SHARED_ROOT_DEFAULT})  — one copy for the whole machine/lab"
      echo "  3) Custom  (you type the path)"
      read -r -p "Choose [1/2/3] (default 1): " ans
      case "${ans}" in 2) LOC="shared";; 3) LOC="custom";; *) LOC="home";; esac
    else
      LOC="home"   # non-interactive default
    fi
  fi
  case "${LOC}" in
    home)   ROOT="${HOME_ROOT_DEFAULT}";;
    # "Shared" is editable: the baked-in /srv/kapurlab path only exists on the
    # lab servers, so on a laptop/other host let the user point it anywhere.
    shared) if [[ -t 0 && -t 1 ]]; then
              read -r -p "Shared location [${SHARED_ROOT_DEFAULT}]: " sp
              ROOT="${sp:-${SHARED_ROOT_DEFAULT}}"
            else ROOT="${SHARED_ROOT_DEFAULT}"; fi;;
    custom) read -r -p "Database directory: " ROOT
            [[ -n "${ROOT}" ]] || die "no path given";;
  esac
fi
ROOT="${ROOT/#\~/${HOME}}"   # expand a leading ~ the user typed
ROOT="${ROOT%/}"
log "database root: ${ROOT}"
# Pre-check writability with a plain-language message instead of a raw mkdir
# error. Walk up to the nearest existing ancestor and test it.
if [[ ${DRY_RUN} -eq 0 ]]; then
  _anc="${ROOT}"; while [[ ! -e "${_anc}" && "${_anc}" != "/" ]]; do _anc="$(dirname "${_anc}")"; done
  if [[ ! -w "${_anc}" ]]; then
    die "Can't write to ${ROOT} (blocked at ${_anc}).
    • For a shared location like /srv/..., re-run with sudo, or
    • choose a folder you own — e.g. your home: bin/bdtools setup-databases --home"
  fi
  mkdir -p "${ROOT}" 2>/dev/null || die "could not create ${ROOT}"
fi
# Persist the chosen root so vsnp_gui's local build (install-local.sh) and
# re-runs of this script find the same databases.
if [[ ${DRY_RUN} -eq 0 ]]; then
  mkdir -p "${BDTOOLS_HOME}"
  printf '%s\n' "${ROOT}" > "${BDTOOLS_HOME}/db-root"
fi

KRAKEN_DEST="${ROOT}/kraken2/k2_standard_08gb"
BLAST_DIR="${ROOT}/blast"
BLAST_DB="${BLAST_DIR}/${BLAST_DBNAME}"
VSNP_REFS="${ROOT}/vsnp3/reference_options"
VSNP_DEPS="${ROOT}/vsnp3/vsnp_dependencies"

want() { local x; for x in "${WANT[@]}"; do [[ "$x" == "$1" ]] && return 0; done; return 1; }
fetcher() { command -v curl >/dev/null 2>&1 && echo "curl" || { command -v wget >/dev/null 2>&1 && echo "wget"; }; }

# ---- downloads (each idempotent: skip if the dest already has content) -----
fetch_kraken() {
  if [[ -f "${KRAKEN_DEST}/hash.k2d" ]]; then ok "kraken2 DB present: ${KRAKEN_DEST}"; return; fi
  log "downloading Kraken2 k2_standard_08gb (~8 GB) -> ${KRAKEN_DEST}"
  local f; f="$(fetcher)" || die "need curl or wget to download the Kraken2 DB"
  run mkdir -p "${KRAKEN_DEST}"
  if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] ${f} ${KRAKEN_URL} | tar -xz -C ${KRAKEN_DEST}"; return; fi
  if [[ "${f}" == curl ]]; then
    curl -fL "${KRAKEN_URL}" | tar -xz -C "${KRAKEN_DEST}"
  else
    wget -qO- "${KRAKEN_URL}" | tar -xz -C "${KRAKEN_DEST}"
  fi
  [[ -f "${KRAKEN_DEST}/hash.k2d" ]] || die "Kraken2 DB extracted but hash.k2d missing — check the download"
  ok "kraken2 DB ready: ${KRAKEN_DEST}"
}

# update_blastdb.pl ships with the `blast` conda package (now in the
# kraken_id_parse_gui env). Find it there first, then on PATH.
find_update_blastdb() {
  local kdir; kdir="$(tool_dir kraken_id_parse_gui)"
  [[ -x "${kdir}/env/bin/update_blastdb.pl" ]] && { echo "${kdir}/env/bin/update_blastdb.pl"; return 0; }
  command -v update_blastdb.pl 2>/dev/null && return 0
  return 1
}
fetch_blast() {
  if compgen -G "${BLAST_DB}.*" >/dev/null 2>&1; then ok "BLAST DB present: ${BLAST_DB}"; return; fi
  local ublast; if ! ublast="$(find_update_blastdb)"; then
    warn "update_blastdb.pl not found — install kraken_id_parse_gui first (its env ships BLAST), then re-run."
    info "  Or manually:  mkdir -p ${BLAST_DIR} && cd ${BLAST_DIR} && update_blastdb.pl --decompress ${BLAST_DBNAME}"
    return
  fi
  log "downloading BLAST ${BLAST_DBNAME} (large; tens of GB) -> ${BLAST_DIR}"
  run mkdir -p "${BLAST_DIR}"
  if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] (cd ${BLAST_DIR} && ${ublast} --decompress ${BLAST_DBNAME})"; return; fi
  ( cd "${BLAST_DIR}" && "${ublast}" --decompress "${BLAST_DBNAME}" ) \
    || die "update_blastdb.pl failed for ${BLAST_DBNAME}"
  ok "BLAST DB ready: ${BLAST_DB}"
}

clone_or_skip() {  # repo dest label
  local repo="$1" dest="$2" label="$3"
  if [[ -n "$(ls -A "${dest}" 2>/dev/null)" ]]; then ok "${label} present: ${dest}"; return; fi
  log "cloning ${label} -> ${dest}"
  run mkdir -p "$(dirname "${dest}")"
  run git clone --depth 1 "${repo}" "${dest}" || die "git clone failed: ${repo}"
}
fetch_vsnp_refs() { clone_or_skip "${VSNP_REFS_REPO}" "${VSNP_REFS}" "vSNP reference options"; }
fetch_vsnp_deps() {
  # The test dataset repo is large; we only want its vsnp_dependencies subtree.
  if [[ -n "$(ls -A "${VSNP_DEPS}" 2>/dev/null)" ]]; then ok "vsnp_dependencies present: ${VSNP_DEPS}"; return; fi
  local tmp="${ROOT}/vsnp3/.vsnp3_test_dataset"
  log "cloning USDA-VS vsnp3 test dataset (for vsnp_dependencies) -> ${VSNP_DEPS}"
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "  [dry-run] git clone --depth 1 ${VSNP_DEPS_REPO} ${tmp} && mv ${tmp}/vsnp_dependencies ${VSNP_DEPS}"
    return
  fi
  rm -rf "${tmp}"
  git clone --depth 1 "${VSNP_DEPS_REPO}" "${tmp}" || die "git clone failed: ${VSNP_DEPS_REPO}"
  if [[ -d "${tmp}/vsnp_dependencies" ]]; then
    mkdir -p "$(dirname "${VSNP_DEPS}")"
    mv "${tmp}/vsnp_dependencies" "${VSNP_DEPS}"
    rm -rf "${tmp}"
    ok "vsnp_dependencies ready: ${VSNP_DEPS}"
  else
    rm -rf "${tmp}"
    die "vsnp_dependencies/ not found in ${VSNP_DEPS_REPO} — repo layout changed?"
  fi
}

# ---- wire the GUIs --------------------------------------------------------
wire_kraken() {
  local args=()
  want kraken && [[ -d "${KRAKEN_DEST}" ]] && args+=(--kraken-db "${KRAKEN_DEST}")
  want blast  && compgen -G "${BLAST_DB}.*" >/dev/null 2>&1 && args+=(--blast-db "${BLAST_DB}")
  [[ ${#args[@]} -gt 0 ]] || return 0
  if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] db_config.py kraken ${args[*]}"; return; fi
  python3 "${KT_BIN_DIR}/lib/db_config.py" kraken "${args[@]}"
}

# vsnp_gui keys its reference root off VSNP_GUI_SITE_ROOT and the launcher
# self-heals config.json to the site path on every start. So when a local
# vsnp site exists we re-point the site's reference_options symlink at the DB
# (survives self-heal) and register both DB folders as reference locations.
# With no site yet, we write the config key directly; install-local.sh's
# build_vsnp_local will adopt the DB root (it reads BDTOOLS_HOME/db-root).
wire_vsnp() {
  want vsnp-refs || want vsnp-deps || return 0
  local site="${BDTOOLS_HOME}/vsnp3-site"
  local deps_reg="${site}/tools/vsnp3/dependencies/reference_options_paths.txt"
  if [[ -d "${site}" ]]; then
    if [[ ${DRY_RUN} -eq 1 ]]; then
      echo "  [dry-run] repoint ${site}/refs/vsnp3/reference_options -> ${VSNP_REFS}; register refs+deps"
      return
    fi
    mkdir -p "${site}/refs/vsnp3" "${site}/tools/vsnp3/dependencies"
    [[ -d "${VSNP_REFS}" ]] && ln -sfn "${VSNP_REFS}" "${site}/refs/vsnp3/reference_options"
    local p
    for p in "${VSNP_REFS}" "${VSNP_DEPS}"; do
      [[ -d "${p}" ]] || continue
      grep -qxF "${p}" "${deps_reg}" 2>/dev/null || printf '%s\n' "${p}" >> "${deps_reg}"
    done
    ok "vsnp_gui: reference_options -> ${VSNP_REFS}; registered reference locations"
  else
    if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] db_config.py vsnp --refs-root ${VSNP_REFS}"; return; fi
    [[ -d "${VSNP_REFS}" ]] && python3 "${KT_BIN_DIR}/lib/db_config.py" vsnp --refs-root "${VSNP_REFS}"
    info "  vsnp_gui not installed locally yet — its install will adopt these databases (${BDTOOLS_HOME}/db-root)."
  fi
}

# ---- run -------------------------------------------------------------------
want kraken    && fetch_kraken
want blast     && fetch_blast
want vsnp-refs && fetch_vsnp_refs
want vsnp-deps && fetch_vsnp_deps
wire_kraken
wire_vsnp

echo
ok "Database setup complete (root: ${ROOT})."
info "Installed GUIs now point at these databases. Restart a running tool to pick up new paths:"
info "    bin/bdtools dashboard --restart"
