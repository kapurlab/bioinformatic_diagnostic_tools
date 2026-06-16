#!/usr/bin/env bash
# common.sh — shared helpers for the bdtools CLI.
# Sourced by bdtools and the install-*.sh scripts. Promoted/condensed from
# the proven vsnp_gui/deploy helpers (same logging + dry-run idiom).

# ---- repo + manifest locations --------------------------------------------
# REPO_DIR is the umbrella checkout root (parent of bin/).
KT_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "${KT_BIN_DIR}/.." && pwd)"
MANIFEST="${BDTOOLS_MANIFEST:-${REPO_DIR}/tools.yml}"
MANIFEST_PY="${KT_BIN_DIR}/lib/manifest.py"

# Where tool checkouts live for non-system installs. Override with --prefix or
# $BDTOOLS_HOME. Defaults to an XDG-friendly per-user location.
BDTOOLS_HOME="${BDTOOLS_HOME:-${XDG_DATA_HOME:-${HOME}/.local/share}/bdtools}"

DRY_RUN="${DRY_RUN:-0}"

# ---- logging (matches vsnp_gui/deploy) ------------------------------------
if [[ -t 1 ]]; then
  _c_blu=$'\e[1;34m'; _c_grn=$'\e[1;32m'; _c_ylw=$'\e[1;33m'; _c_red=$'\e[1;31m'; _c_rst=$'\e[0m'
else
  _c_blu=""; _c_grn=""; _c_ylw=""; _c_red=""; _c_rst=""
fi
log()  { printf '%s==>%s %s\n' "${_c_blu}" "${_c_rst}" "$*"; }
ok()   { printf '  %sok%s %s\n' "${_c_grn}" "${_c_rst}" "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  %s!!%s %s\n' "${_c_ylw}" "${_c_rst}" "$*" >&2; }
die()  { printf '%sERROR%s %s\n' "${_c_red}" "${_c_rst}" "$*" >&2; exit 1; }

# need_writable PATH PHASE — ensure PATH (or its nearest existing ancestor) is
# writable; otherwise the phase needs sudo. Skipped under --dry-run.
need_writable() {
  [[ "${DRY_RUN:-0}" -eq 1 ]] && return 0
  local p="$1"
  while [[ ! -e "${p}" && "${p}" != "/" ]]; do p="$(dirname "${p}")"; done
  [[ -w "${p}" ]] || die "phase '$2' must write ${1}, which is not writable as $(whoami) — run under sudo"
}
# run CMD... — execute, or just print under --dry-run.
run()  { if [[ "${DRY_RUN}" -eq 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi; }

# ---- manifest access ------------------------------------------------------
_need_python() { command -v python3 >/dev/null 2>&1 || die "python3 is required to read tools.yml"; }
manifest_suite_version() { _need_python; python3 "${MANIFEST_PY}" "${MANIFEST}" suite_version; }
manifest_names()         { _need_python; python3 "${MANIFEST_PY}" "${MANIFEST}" names; }
manifest_get()           { _need_python; python3 "${MANIFEST_PY}" "${MANIFEST}" get "$1" "$2"; }
manifest_set()           { _need_python; python3 "${MANIFEST_PY}" "${MANIFEST}" set "$1" "$2" "$3"; }
manifest_has() { manifest_names | grep -qxF "$1"; }

# Resolve a tool's checkout dir: explicit $BDTOOLS_TOOLSDIR wins (e.g. the
# lab's existing /srv/kapurlab/tools tree), else the per-user home.
tool_dir() {
  local name="$1"
  if [[ -n "${BDTOOLS_TOOLSDIR:-}" && -d "${BDTOOLS_TOOLSDIR}/${name}" ]]; then
    echo "${BDTOOLS_TOOLSDIR}/${name}"
  else
    echo "${BDTOOLS_HOME}/checkouts/${name}"
  fi
}

# Ensure a tool is checked out at its manifest-pinned version (clones if absent).
# Honors DRY_RUN. Echoes nothing; callers use tool_dir to get the path.
ensure_checkout() {
  local name="$1" dir repo version
  dir="$(tool_dir "$name")"; repo="$(manifest_get "$name" repo)"; version="$(manifest_get "$name" version)"
  if [[ -d "${dir}/.git" ]]; then
    ok "checkout present: ${dir} ($(git -C "${dir}" describe --tags --always 2>/dev/null || echo '?'))"
    return 0
  fi
  log "cloning ${name} @ ${version}"
  run mkdir -p "$(dirname "${dir}")"
  run git clone --branch "${version}" --depth 1 "${repo}" "${dir}" \
    || die "git clone failed (${repo} @ ${version})"
}

# ---- misc -----------------------------------------------------------------
# Pick a free TCP port on localhost (used by `bdtools local`).
find_free_port() {
  _need_python
  python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

# Open a URL in the user's browser, best-effort, cross-platform.
open_url() {
  local url="$1"
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 &
  elif command -v open   >/dev/null 2>&1; then open "$url" >/dev/null 2>&1 &        # macOS
  elif command -v wslview >/dev/null 2>&1; then wslview "$url" >/dev/null 2>&1 &     # WSL
  else warn "open ${url} in your browser"; fi
}

# Detect a usable conda/mamba base; prefer mamba (conda's classic solver hangs).
detect_conda() {
  local base="${CONDA_BASE:-${HOME}/miniforge3}"
  if [[ -x "${base}/bin/conda" ]]; then echo "${base}/bin/conda"; return 0; fi
  command -v mamba 2>/dev/null && return 0
  command -v conda 2>/dev/null && return 0
  return 1
}
