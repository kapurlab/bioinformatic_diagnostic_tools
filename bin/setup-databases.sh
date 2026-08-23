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
#   vcf-dbs       kapurlab vcf_db_directories     -> <root>/vsnp3/vcf_db_directories
#                 (Step 2 VCF comparison DBs; seeded ONCE into the GUI's
#                 vcf_db_folders root — later removals/additions are yours)
#
# Consumers wired automatically:
#   kraken,blast  -> kraken_id_parse_gui  (~/.config/kraken_id_parse_gui/config.json)
#   vsnp-*        -> vsnp_gui             (reference locations + config.json)
#   vcf-dbs       -> vsnp_gui             (vcf_db_folders root, one-time)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

# ---- sources (single source of truth; mirror these in the README) ----------
KRAKEN_URL="https://genome-idx.s3.amazonaws.com/kraken/k2_standard_08_GB_20260226.tar.gz"
BLAST_DBNAME="ref_prok_rep_genomes"
VSNP_REFS_REPO="https://github.com/USDA-VS/vSNP_reference_options.git"
VSNP_DEPS_REPO="https://github.com/USDA-VS/vsnp3_test_dataset.git"

# The "shared" location comes from what this deployment declares (site.conf
# DB_ROOT / SITE_ROOT, via site_paths), not from a literal. It used to be this
# lab's own path, which meant anyone else choosing "shared" was offered
# /srv/kapurlab/databases as though it were a general convention — a directory
# that exists on exactly one set of machines. Empty here means "this machine has
# no shared root declared", and we ask instead of suggesting someone else's.
SHARED_ROOT_DEFAULT="$("${PYBIN:-python3}" - "${KT_BIN_DIR}/lib" "${REPO_DIR}" <<'PY' 2>/dev/null || true
import sys
sys.path.insert(0, sys.argv[1])
import site_paths
# Pass the repo so the admin-edited sites/site.conf counts, not just the
# per-machine record that a previous run may have left behind.
cfg = site_paths.site_config(sys.argv[2])
for key in ("DB_ROOT", "DATABASES_ROOT"):
    if (cfg.get(key) or "").strip():
        print(cfg[key].strip()); break
else:
    root = (cfg.get("SITE_ROOT") or "").strip()
    if root:
        print(root.rstrip("/") + "/databases")
PY
)"
HOME_ROOT_DEFAULT="${HOME}/databases"

ROOT=""; LOC=""; WANT=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --home)    LOC="home";   shift;;
    --shared)  LOC="shared"; shift;;
    --root)    ROOT="$2";    shift 2;;
    --dry-run) DRY_RUN=1; export DRY_RUN; shift;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    kraken|blast|vsnp-refs|vsnp-deps|vcf-dbs) WANT+=("$1"); shift;;
    all) shift;;                       # explicit "all" == default
    -*)  die "unknown option: $1";;
    *)   die "unknown database: $1 (kraken|blast|vsnp-refs|vsnp-deps|vcf-dbs|all)";;
  esac
done
[[ ${#WANT[@]} -gt 0 ]] || WANT=(kraken blast vsnp-refs vsnp-deps vcf-dbs)

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
    # "Shared" is whatever this machine declares, and always editable. With no
    # declared root there is nothing sensible to suggest, so require a path
    # rather than offer one that belongs to another site.
    shared) if [[ -t 0 && -t 1 ]]; then
              if [[ -n "${SHARED_ROOT_DEFAULT}" ]]; then
                read -r -p "Shared location [${SHARED_ROOT_DEFAULT}]: " sp
                ROOT="${sp:-${SHARED_ROOT_DEFAULT}}"
              else
                read -r -p "Shared location (full path): " sp
                ROOT="${sp}"
                [[ -n "${ROOT}" ]] || die "no path given"
              fi
            else
              ROOT="${SHARED_ROOT_DEFAULT}"
              [[ -n "${ROOT}" ]] || die "no shared database root is configured on this machine.
  Pass --root DIR, or declare DB_ROOT (or SITE_ROOT) in sites/site.conf."
            fi;;
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
  # Also record it as site config. Tool backends contain NO database path of
  # their own — they read BDTOOLS_DB_ROOT, which the launcher resolves from here
  # (bin/lib/site_paths.py). Without this the same code would have to embed a
  # site layout, which is only ever right on one machine.
  "${PYBIN:-python3}" - "${KT_BIN_DIR}/lib" "${ROOT}" <<'PY' 2>/dev/null || true
import sys
sys.path.insert(0, sys.argv[1])
import site_paths
site_paths.write_site_file({"DB_ROOT": sys.argv[2]})
PY
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

# arch_prefix ENVDIR — the /usr/bin/arch pin under which a program from this
# env must run, derived from the platform the env was BUILT for (conda-meta)
# and never from `uname -m` (a translated process reports x86_64 exactly when
# its inherited slice preference is the thing that must be overridden). Mirrors
# the production launchers (install-local.sh:arch_prefix).
#
# It exists here because update_blastdb.pl is an env PERL SCRIPT run outside
# any pinned launcher — the exact shape of the August incident: a universal
# env perl with single-arch XS bundles, launched from a process tree preferring
# the other slice, dies loading Cwd with a mach-o error nothing on disk
# explains. This script is spawned by `bdtools install` and `bdtools
# setup-databases` (bin/bdtools), i.e. possibly under a translated dashboard or
# terminal, so the caller's inherited preference is precisely what cannot be
# trusted.
arch_prefix() {
  local envdir="${1:-}" sub
  [[ "$(uname -s)" == "Darwin" ]] || return 0
  [[ -x /usr/bin/arch ]] || return 0
  sub="$(env_conda_subdir "${envdir}" 2>/dev/null || true)"
  case "${sub}" in
    osx-arm64) printf '%s' "/usr/bin/arch -arm64";;
    osx-64)    printf '%s' "/usr/bin/arch -x86_64";;
  esac
  return 0
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
  # Pin from the env the script actually resolved from (its bin's parent) —
  # covers both the kraken env path and a PATH hit that happens to live inside
  # some other env; a system install has no conda-meta and stays unpinned.
  # Prepended as argv, not spliced into a command string.
  local _archp _arch_cmd=()
  _archp="$(arch_prefix "$(dirname "$(dirname "${ublast}")")")"
  [[ -n "${_archp}" ]] && read -ra _arch_cmd <<< "${_archp}"
  if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] (cd ${BLAST_DIR} && ${_archp:+${_archp} }${ublast} --decompress ${BLAST_DBNAME})"; return; fi
  ( cd "${BLAST_DIR}" && ${_arch_cmd[@]+"${_arch_cmd[@]}"} "${ublast}" --decompress "${BLAST_DBNAME}" ) \
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
  "${PYBIN}" "${KT_BIN_DIR}/lib/db_config.py" kraken "${args[@]}"
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
      registry_add_line "${deps_reg}" "${p}"   # hardlink-safe (see common.sh)
    done
    ok "vsnp_gui: reference_options -> ${VSNP_REFS}; registered reference locations"
  else
    if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] db_config.py vsnp --refs-root ${VSNP_REFS}"; return; fi
    [[ -d "${VSNP_REFS}" ]] && "${PYBIN}" "${KT_BIN_DIR}/lib/db_config.py" vsnp --refs-root "${VSNP_REFS}"
    info "  vsnp_gui not installed locally yet — its install will adopt these databases (${BDTOOLS_HOME}/db-root)."
  fi
}

# Step 2 VCF comparison databases. The clone lands under the database root;
# the folders are linked (once) into whichever vcf_db_folders root this
# machine's vSNP GUI actually reads: the local site tree when there is one,
# else the declared SITE_ROOT layout.
wire_vcf_dbs() {
  want vcf-dbs || return 0
  local site="${BDTOOLS_HOME}/vsnp3-site" target=""
  if [[ -d "${site}" ]]; then
    target="${site}/refs/vsnp3/vcf_db_folders"
  else
    local sroot; sroot="$("${PYBIN:-python3}" - "${KT_BIN_DIR}/lib" "${REPO_DIR}" <<'PY' 2>/dev/null || true
import sys
sys.path.insert(0, sys.argv[1])
import site_paths
root = site_paths.site_root(sys.argv[2])
print(root or "")
PY
)"
    [[ -n "${sroot}" ]] && target="${sroot}/refs/vsnp3/vcf_db_folders"
  fi
  if [[ -z "${target}" ]]; then
    target="${ROOT}/vsnp3/vcf_db_folders"
    warn "no local vsnp site or SITE_ROOT declared — VCF DBs land at ${target};"
    warn "  add them in the vSNP GUI (VCF DB folders) or set SITE_ROOT and re-run."
  fi
  seed_vcf_db_directories "${target}" "${ROOT}/vsnp3"
}

# ---- run -------------------------------------------------------------------
want kraken    && fetch_kraken
want blast     && fetch_blast
want vsnp-refs && fetch_vsnp_refs
want vsnp-deps && fetch_vsnp_deps
wire_kraken
wire_vsnp
wire_vcf_dbs

echo
ok "Database setup complete (root: ${ROOT})."
info "Installed GUIs now point at these databases. Restart a running tool to pick up new paths:"
info "    bin/bdtools dashboard --restart"
