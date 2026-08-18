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

# ---- python interpreter ----------------------------------------------------
# Resolve the newest usable python3 (>=3.7) with NO action required from the
# user. Some HPC/OOD hosts default `python3` to an EOL 3.6 that lacks features
# the suite's stdlib-only helpers rely on (e.g. argparse's
# add_subparsers(required=...)). The suite's helpers need only a modern
# interpreter, not a dedicated env, so we prefer, in order:
#   1. an explicit override ($BDTOOLS_PYTHON),
#   2. the highest python3.X on PATH (a loaded module / venv, if any),
#   3. the conda base python already installed for the tool envs (modern,
#      >=3.9, needs no `module load` or activation),
#   4. plain `python3` (may be an old system 3.6 — last resort).
# NOTE: PYBIN is assigned at the END of this file, after conda_base_dir is
# defined, since step 3 calls it.
bd_python() {
  if [[ -n "${BDTOOLS_PYTHON:-}" ]]; then echo "${BDTOOLS_PYTHON}"; return 0; fi
  local c cbase
  for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7; do
    command -v "$c" >/dev/null 2>&1 && { echo "$c"; return 0; }
  done
  cbase="$(conda_base_dir 2>/dev/null || true)"
  [[ -n "${cbase}" && -x "${cbase}/bin/python3" ]] && { echo "${cbase}/bin/python3"; return 0; }
  command -v python3 >/dev/null 2>&1 && { echo python3; return 0; }
  return 1
}

# ---- manifest access ------------------------------------------------------
_need_python() { command -v "${PYBIN}" >/dev/null 2>&1 || die "python3 is required to read tools.yml"; }
manifest_suite_version() { _need_python; "${PYBIN}" "${MANIFEST_PY}" "${MANIFEST}" suite_version; }
manifest_names()         { _need_python; "${PYBIN}" "${MANIFEST_PY}" "${MANIFEST}" names; }
manifest_get()           { _need_python; "${PYBIN}" "${MANIFEST_PY}" "${MANIFEST}" get "$1" "$2"; }
manifest_set()           { _need_python; "${PYBIN}" "${MANIFEST_PY}" "${MANIFEST}" set "$1" "$2" "$3"; }
manifest_has() { manifest_names | grep -qxF "$1"; }

# ---- what bdtools is allowed to CHANGE -------------------------------------
# tools.yml's `updates:` field, defaulting to the safe answer. An env rebuild
# re-solves every dependency and a conda transaction that fails part-way can
# leave a tool that no longer runs — so changing a tool is opt-in per tool, and
# only vsnp_gui is opted in today. Reading and displaying versions is unaffected.
tool_updates_policy() {   # -> install | report
  local p; p="$(manifest_get "$1" updates 2>/dev/null || true)"
  [[ "${p}" == "install" ]] && { printf 'install'; return; }
  printf 'report'
}

# Gate every code path that would move a checkout or touch an env. Deliberately
# NOT overridable by an environment variable: the whole point is that no script,
# no dashboard button and no `all` sweep can decide this on the user's behalf.
# The override is an explicit flag on an explicitly named tool.
#
# `explain` is 0 for a sweep: eight tools × a five-line explanation is a wall of
# warnings for a run in which nothing went wrong, and a wall of warnings is how
# people learn to skip warnings. The caller lists them in one summary line
# instead. When the user named the tool, they get the full reason.
require_updatable() {   # require_updatable <tool> <allow_flag 0|1> <cmd> [explain 0|1]
  local tool="$1" allowed="${2:-0}" cmd="${3:-update}" explain="${4:-1}"
  [[ "$(tool_updates_policy "${tool}")" == "install" ]] && return 0
  [[ "${allowed}" -eq 1 ]] && {
    warn "${tool} is report-only in tools.yml — proceeding because --allow-report-only was given."
    return 0
  }
  if [[ "${explain}" -eq 1 ]]; then
    warn "${tool}: left unchanged — it is report-only in tools.yml."
    info "  Changing a tool is opt-in per tool. A rebuild re-solves the whole env,"
    info "  and a transaction that dies part-way can leave a working tool broken;"
    info "  being a release behind cannot. Versions are still read and reported."
    info "  To do it anyway, deliberately, one named tool at a time:"
    info "      bin/bdtools ${cmd} ${tool} --allow-report-only"
  fi
  return 1
}

# Resolve a tool's checkout dir: explicit $BDTOOLS_TOOLSDIR wins (e.g. the
# lab's existing /srv/kapurlab/tools tree), else the per-user home.
# Dirty tracked paths in a tool checkout that are NOT regenerable build output —
# i.e. the ones that must actually stop us force-checking-out a pinned tag.
# Empty output means "safe to move".
#
# frontend/dist and frontend/package-lock.json are tracked but rewritten by every
# install (vite output hashes and npm's lockfile depend on the local Node), so a
# managed checkout is permanently dirty after its first build. install-local.sh's
# ensure_checkout used to require a completely clean tree, which meant it could
# never advance the pin again — it warned and then **built with the old code while
# announcing the new pin**. That is how a shipped ksnp_gui fix (v0.4.2) silently
# failed to reach a machine still running v0.4.0's installer.
#
# check-updates.sh had the correct tolerance list inline; this is that same rule in
# one place so the two callers cannot drift apart again.
tool_blocking_edits() {
  local dir="$1" p
  { git -C "${dir}" diff --name-only 2>/dev/null
    git -C "${dir}" diff --cached --name-only 2>/dev/null; } | sort -u | while IFS= read -r p; do
    [[ -n "${p}" ]] || continue
    case "${p}" in
      frontend/dist|frontend/dist/*|frontend/package-lock.json) ;;
      # Site-localized OOD card config: a deployment CANNOT run the cards
      # without writing its own cluster/account values into these (e.g.
      # ood/apps/*/submit.yml.erb). That edit is the install working as
      # designed, not a personal experiment — it must neither block an update
      # nor be destroyed by one. The updaters snapshot and restore these
      # around their force checkout (snapshot_site_edits below).
      ood/apps/*) ;;
      *) printf '%s\n' "${p}";;
    esac
  done
}

# The dirty tracked paths under ood/apps/ — the site-localized card config
# exempted above. Listed separately so an updater can carry them across a
# `git checkout -f` to a new tag.
tool_site_edits() {
  local dir="$1" p
  { git -C "${dir}" diff --name-only 2>/dev/null
    git -C "${dir}" diff --cached --name-only 2>/dev/null; } | sort -u | while IFS= read -r p; do
    case "${p}" in ood/apps/*) printf '%s\n' "${p}";; esac
  done
}

# snapshot_site_edits DIR — copy the site-localized edits to a temp dir and
# print its path ("" when there is nothing to preserve). Pair with
# restore_site_edits after the checkout moves; restore also removes the temp.
snapshot_site_edits() {
  local dir="$1" tmp="" p edits
  edits="$(tool_site_edits "${dir}")"
  [[ -n "${edits}" ]] || { printf ''; return 0; }
  tmp="$(mktemp -d)" || return 1
  while IFS= read -r p; do
    [[ -n "${p}" && -f "${dir}/${p}" ]] || continue
    mkdir -p "${tmp}/$(dirname "${p}")"
    cp -p "${dir}/${p}" "${tmp}/${p}"
  done <<< "${edits}"
  printf '%s' "${tmp}"
}

restore_site_edits() {  # DIR TMPDIR
  local dir="$1" tmp="$2" f rel
  [[ -n "${tmp}" && -d "${tmp}" ]] || return 0
  while IFS= read -r -d '' f; do
    rel="${f#"${tmp}"/}"
    mkdir -p "${dir}/$(dirname "${rel}")"
    cp -p "${f}" "${dir}/${rel}"
  done < <(find "${tmp}" -type f -print0)
  rm -rf "${tmp}"
}

tool_dir() {
  local name="$1"
  if [[ -n "${BDTOOLS_TOOLSDIR:-}" && -d "${BDTOOLS_TOOLSDIR}/${name}" ]]; then
    echo "${BDTOOLS_TOOLSDIR}/${name}"
  else
    echo "${BDTOOLS_HOME}/checkouts/${name}"
  fi
}

# Install this checkout's local ignore rules into .git/info/exclude.
#
# A conda env and a node_modules tree are built INSIDE a checkout, and neither
# belongs in a commit. The rules that can only be expressed at the repo root
# live here, per clone, rather than in a tracked file — .git/info/exclude is
# never pushed, so every checkout gets the protection and no repo carries a
# root-level ignore file. Rules scoped to one directory stay in that
# directory's own .gitignore, which IS tracked.
#
# Idempotent: the block is delimited and rewritten in place, never appended
# twice, and a checkout the user has edited by hand keeps their lines.
install_checkout_excludes() {
  local dir="${1:?checkout dir}" exclude
  [[ -d "${dir}/.git" ]] || return 0
  exclude="$(git -C "${dir}" rev-parse --git-path info/exclude 2>/dev/null)" || return 0
  [[ -n "${exclude}" ]] || return 0
  [[ "${exclude}" = /* ]] || exclude="${dir}/${exclude}"

  if [[ ${DRY_RUN:-0} -eq 1 ]]; then
    log "DRY-RUN: would write ignore rules to ${exclude}"
    return 0
  fi

  mkdir -p "$(dirname "${exclude}")"
  local kept=""
  if [[ -f "${exclude}" ]]; then
    kept="$(awk '/^# >>> bdtools ignore rules >>>$/{skip=1} !skip{print} /^# <<< bdtools ignore rules <<<$/{skip=0}' "${exclude}")"
  fi
  { [[ -n "${kept}" ]] && printf '%s\n' "${kept}"
    cat <<'RULES'
# >>> bdtools ignore rules >>>
# Managed by bdtools; edits inside this block are overwritten. Add your own
# rules above or below it.
env/
node_modules/
node_modules
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.DS_Store
*.swp
*.log
.env
.env.*
!.env.example
# per-machine config and credentials a tool writes beside itself
*.local.json
# macOS bundles built from a checkout
*.app/
*.icns
# a root .gitignore, if one exists here, is local to this checkout: git honours
# it whether or not it is tracked, so it works without being published
/.gitignore
# local working notes, kept on the machine that produced them
.claude/
CLAUDE.md
AGENTS.md
HANDOFF*.md
*_HANDOFF.md
docs/HANDOFF_*.md
docs/dev/
# <<< bdtools ignore rules <<<
RULES
  } > "${exclude}.tmp" && mv "${exclude}.tmp" "${exclude}"
}

# Ensure a tool is checked out at its manifest-pinned version (clones if absent).
# Honors DRY_RUN. Echoes nothing; callers use tool_dir to get the path.
ensure_checkout() {
  local name="$1" dir repo version
  dir="$(tool_dir "$name")"; repo="$(manifest_get "$name" repo)"; version="$(manifest_get "$name" version)"
  if [[ -d "${dir}/.git" ]]; then
    ok "checkout present: ${dir} ($(git -C "${dir}" describe --tags --always 2>/dev/null || echo '?'))"
    install_checkout_excludes "${dir}"
    return 0
  fi
  log "cloning ${name} @ ${version}"
  run mkdir -p "$(dirname "${dir}")"
  run git clone --branch "${version}" --depth 1 "${repo}" "${dir}" \
    || die "git clone failed (${repo} @ ${version})"
  install_checkout_excludes "${dir}"
}

# ---- misc -----------------------------------------------------------------
# Pick a free TCP port on localhost (used by `bdtools local`).
find_free_port() {
  _need_python
  "${PYBIN}" - <<'PY'
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
# Resolve a real conda/mamba BINARY (never a shell function). conda's shell init
# defines `conda` as a function that isn't visible to child scripts (e.g. a
# tool's deploy/install.sh), and `command -v conda` then prints just "conda",
# which is useless as a path. So probe explicit overrides, CONDA_EXE, and the
# common install bases, and only accept a `command -v` result if it's executable.
detect_conda() {
  local base="${CONDA_BASE:-}" b p
  [[ -n "${base}" && -x "${base}/bin/conda" ]] && { echo "${base}/bin/conda"; return 0; }
  [[ -n "${CONDA_EXE:-}" && -x "${CONDA_EXE}" ]] && { echo "${CONDA_EXE}"; return 0; }
  for b in "${HOME}/miniforge3" "${HOME}/miniconda3" "${HOME}/mambaforge" \
           "${HOME}/anaconda3" "/opt/miniforge3" "/opt/miniconda3" \
           "/opt/homebrew/Caskroom/miniforge/base"; do
    [[ -x "${b}/bin/conda" ]] && { echo "${b}/bin/conda"; return 0; }
  done
  p="$(command -v mamba 2>/dev/null || true)"; [[ -n "${p}" && -x "${p}" ]] && { echo "${p}"; return 0; }
  p="$(command -v conda 2>/dev/null || true)"; [[ -n "${p}" && -x "${p}" ]] && { echo "${p}"; return 0; }
  return 1
}

# The conda base directory (parent of bin/) for the resolved binary, or empty.
conda_base_dir() {
  local bin; bin="$(detect_conda 2>/dev/null || true)"
  [[ -n "${bin}" ]] && dirname "$(dirname "${bin}")"
}

# ---- conda env platform ----------------------------------------------------
# host_conda_subdir — the conda platform this machine runs natively. Used to
# spot an env that cannot run here at all (one built on/for another arch), which
# otherwise surfaces as unexplained "Bad CPU type" / missing-symbol failures at
# run time rather than at install time.
host_conda_subdir() {
  local s m; s="$(uname -s)"; m="$(uname -m)"
  case "${s}:${m}" in
    Darwin:arm64)          echo osx-arm64;;
    Darwin:x86_64)         echo osx-64;;
    Linux:x86_64)          echo linux-64;;
    Linux:aarch64|Linux:arm64) echo linux-aarch64;;
    Linux:ppc64le)         echo linux-ppc64le;;
    *)                     echo "";;            # unknown: callers skip the check
  esac
}

# env_conda_subdir ENVDIR — the conda platform an EXISTING env was built for,
# read from the packages it actually contains (conda-meta/*.json "subdir"),
# ignoring noarch. Empty when the env doesn't exist or records nothing.
#
# Why this exists: an env's architecture is fixed at creation — every binary in
# it was linked for one subdir. Nothing was pinning the solver to that subdir on
# a later update, so an env created for one platform could be updated for
# another (the live case: an osx-64 env on Apple Silicon — see install-local.sh's
# ensure_conda_subdir — updated with an osx-arm64 solve). That mixes
# architectures inside one prefix: if you are lucky it fails in a post-link
# script, if you are not it fails at run time in a diagnostic pipeline.
# Callers pin CONDA_SUBDIR to this value before any conda op on an existing env.
#
# The trailing `|| true`s matter throughout: every grep here returns 1 on
# "nothing matched" (a noarch-only env), and callers run under `set -euo
# pipefail`, where that would abort the install instead of reporting "no
# architecture recorded".
_env_subdirs() {   # ENVDIR — one platform per installed package, noarch excluded
  local envdir="$1"
  { grep -ho '"subdir":[[:space:]]*"[^"]*"' "${envdir}"/conda-meta/*.json 2>/dev/null || true; } \
    | sed 's/.*"\([^"]*\)"[[:space:]]*$/\1/' | grep -v '^noarch$' || true
}

env_conda_subdir() {
  local envdir="${1:-}" out
  [[ -n "${envdir}" && -d "${envdir}/conda-meta" ]] || return 0
  # Majority vote: a partially-applied update can leave a few records from
  # another platform (that is exactly the bug above, seen in the wild as 4
  # osx-arm64 packages inside a 240-package osx-64 env), and the env's real
  # architecture is whatever the bulk of it was built for.
  out="$( _env_subdirs "${envdir}" | sort | uniq -c | sort -rn | awk 'NR==1{print $2}' || true )"
  printf '%s' "${out}"
}

# env_foreign_subdirs ENVDIR — "<count> <platform>" for each platform in the env
# that is NOT its majority one; empty when the env is coherent. A non-empty
# result means binaries that cannot run in this prefix were linked into it, which
# no further update can repair — the env has to be rebuilt.
env_foreign_subdirs() {
  local envdir="${1:-}" main out
  [[ -n "${envdir}" && -d "${envdir}/conda-meta" ]] || return 0
  main="$(env_conda_subdir "${envdir}")"
  [[ -n "${main}" ]] || return 0
  out="$( _env_subdirs "${envdir}" | grep -vxF "${main}" | sort | uniq -c \
          | awk '{print $1" "$2}' || true )"
  printf '%s' "${out}"
}

# Which python does this tool ACTUALLY run on?
#
# Ask tool_launch.resolve — the same resolver the dashboard launches through and
# that packages.env_dir_for reports versions from. Doctor used to answer this
# question on its own (checkout env, else a conda env matching the manifest's
# `env:` NAME), and on any machine where those disagree it audited an env the
# tool never touches. Live example: a shared site install runs vsnp_gui from the
# PREFIX env /srv/kapurlab/tools/vsnp3, while a stale personal `vsnp3` env from
# an old install still existed in the user's conda — doctor graded the personal
# one and reported fastapi/uvicorn/pydantic and snp-dists "missing", plus a fix
# ("rebuilds the vsnp3 env") that would have rebuilt a perfectly good env to cure
# a problem in a different one. Every finding was a false positive, and the card
# said "Needs setup before it can run" about a tool that was running.
#
# The heuristics stay as fallbacks for installs tool_launch cannot resolve.
tool_env_python() {
  local dir="$1" envname="$2" name="${3:-}" conda py
  if [[ -n "${name}" ]]; then
    # lib dir passed as argv[1], not via the environment: KT_BIN_DIR is a plain
    # shell variable in common.sh, never exported, so reading it from os.environ
    # here silently found nothing and every lookup fell through to the old
    # heuristics — the bug this function exists to fix, still happening.
    py="$("${PYBIN}" -c '
import os, sys
sys.path.insert(0, sys.argv[1])
try:
    import tool_launch
    d = (tool_launch.resolve(sys.argv[2], 0) or {}).get("env_dir") or ""
except Exception:
    d = ""
if d and d != "(base)":
    p = os.path.join(d, "bin", "python")
    if os.path.exists(p):
        print(p)
' "${KT_BIN_DIR}/lib" "${name}" 2>/dev/null)"
    [[ -n "${py}" ]] && { echo "${py}"; return; }
  fi
  if [[ -x "${dir}/env/bin/python" ]]; then echo "${dir}/env/bin/python"; return; fi
  conda="$(detect_conda 2>/dev/null || true)"
  if [[ -n "${conda}" && -n "${envname}" ]] \
     && "${conda}" env list 2>/dev/null | awk '{print $1}' | grep -qxF "${envname}"; then
    "${conda}" run -n "${envname}" sh -c 'echo $CONDA_PREFIX/bin/python' 2>/dev/null
  fi
}

# tool_env_prefix TOOL — the env DIRECTORY the tool runs from ("" if none).
# Callers that operate on an env (restore, targeted conda repair) need the prefix
# rather than the interpreter, and must not re-derive it: a resolver that only
# knows <checkout>/env sees no env at all for every tool installed as a NAMED
# conda env, and the sensible-looking fallback — "no env, so rebuild it" — sends
# someone to rebuild a working install instead of repairing it.
tool_env_prefix() {   # TOOL
  local py; py="$(tool_env_python "$(tool_dir "$1")" "$(manifest_get "$1" env 2>/dev/null || true)" "$1")"
  [[ -n "${py}" ]] || return 0
  printf '%s' "${py%/bin/python}"
}

# harden_conda_hooks ENVDIR — make an env's conda activate/deactivate hooks
# survive `set -u`. Idempotent; safe to call before and after every conda op.
#
# The compiler-toolchain packages (cctools/ld64/clang_*/gcc_*) ship
# etc/conda/{activate,deactivate}.d hooks that read $CONDA_BACKUP_<TOOL> with no
# default. conda sources those hooks around package post-link scripts, so under
# `set -u` the first package with a post-link script dies and takes the whole
# transaction with it:
#
#   deactivate_cctools_osx-64.sh: line 63: CONDA_BACKUP_AR: unbound variable
#   LinkError: post-link script failed for package ...::spades-4.3.0...
#
# conda then rolls back — so one upstream hook bug blocks every future env
# update for that tool, on every machine whose env has such a hook. Wrapping
# each affected hook in a save/restore of `set -u` fixes it without changing
# what the hook does.
#
# Plain `case $-` / `set` rather than a function with `local -`: these files are
# sourced by whatever shell the user (or conda) is running — bash for post-link
# scripts, zsh for an interactive `conda activate` on macOS — so the guard has to
# be portable shell. The one wart is that a hook which `return`s early leaves
# `set +u` set in the sourcing shell; harmless in both of those contexts, and far
# better than a failed transaction.
harden_conda_hooks() {
  local envdir="${1:-}" f n=0 first
  [[ "${DRY_RUN:-0}" -eq 1 ]] && return 0
  [[ -n "${envdir}" && -d "${envdir}" ]] || return 0
  for f in "${envdir}"/etc/conda/activate.d/*.sh "${envdir}"/etc/conda/deactivate.d/*.sh; do
    [[ -f "${f}" && -w "${f}" ]] || continue          # missing, or a read-only shared env: leave it
    grep -q 'CONDA_BACKUP_' "${f}" 2>/dev/null || continue   # only toolchain hooks have this bug
    grep -q 'bdtools set-u guard' "${f}" 2>/dev/null && continue   # already guarded
    # Keep any shebang on line 1 (cosmetic for a sourced file, but a file that
    # starts with a guard comment no longer looks like the script it is).
    first="$(head -1 "${f}" 2>/dev/null || true)"
    # Write through the original file (not a rename) so mode/ownership survive.
    { case "${first}" in '#!'*) printf '%s\n' "${first}";; esac
      printf '%s\n' \
        '# >>> bdtools set-u guard >>> (this hook reads CONDA_BACKUP_* unguarded)' \
        'case $- in *u*) _bdtools_reset_u=1; set +u ;; *) _bdtools_reset_u=0 ;; esac'
      case "${first}" in '#!'*) tail -n +2 "${f}";; *) cat "${f}";; esac
      printf '%s\n' \
        '[ "${_bdtools_reset_u:-0}" = 1 ] && set -u' \
        'unset _bdtools_reset_u' \
        '# <<< bdtools set-u guard <<<'
    } > "${f}.bdtools-tmp" 2>/dev/null || { rm -f "${f}.bdtools-tmp"; continue; }
    cat "${f}.bdtools-tmp" > "${f}" && n=$(( n + 1 ))
    rm -f "${f}.bdtools-tmp"
  done
  [[ ${n} -gt 0 ]] && ok "guarded ${n} conda activation hook(s) in ${envdir} against \`set -u\` (upstream CONDA_BACKUP_* bug)"
  return 0
}

# ---- env snapshots ---------------------------------------------------------
# Exactly what is installed in an env, written BEFORE anything changes it.
#
# conda rolls a failed transaction back, but a rollback is not a restore: it can
# leave an env that no longer imports what the tool needs, and there is then no
# record of what was in it. `conda list --explicit` is that record — a list of
# package URLs, resolved and reproducible, needing no solve to replay. It is a
# directory read of conda-meta, so it costs nothing to take on every change.
#
# Not covered: pip-installed packages (they are re-installed by the build's own
# pip step) and any file a user hand-edited inside the env.
_env_snapshot_file() { printf '%s' "${BDTOOLS_HOME}/state/${1}.env-explicit.txt"; }

snapshot_env() {   # snapshot_env TOOL ENVDIR
  local tool="$1" envdir="$2" f conda
  [[ "${DRY_RUN:-0}" -eq 1 ]] && return 0
  [[ -n "${tool}" && -x "${envdir}/bin/python" ]] || return 0
  conda="$(detect_conda 2>/dev/null)" || return 0
  f="$(_env_snapshot_file "${tool}")"
  mkdir -p "$(dirname "${f}")" 2>/dev/null || return 0
  # Keep one generation back: the most useful snapshot is sometimes the one from
  # before the change you are now trying to undo.
  [[ -f "${f}" ]] && cp -f "${f}" "${f}.prev" 2>/dev/null || true
  if "${conda}" list --explicit -p "${envdir}" > "${f}.tmp" 2>/dev/null \
     && grep -q '@EXPLICIT' "${f}.tmp"; then
    mv -f "${f}.tmp" "${f}"
  else
    rm -f "${f}.tmp"
  fi
  return 0
}

# Print how to put an env back, if we have a snapshot for it. Called when a build
# fails, because that is the moment the information is worth something.
restore_env_hint() {   # restore_env_hint TOOL
  local f; f="$(_env_snapshot_file "$1")"
  [[ -f "${f}" ]] || return 0
  info "  The env as it was before this run was recorded. To put it back exactly:"
  info "      bin/bdtools restore-env $1"
  info "    (snapshot: ${f})"
}

# ---- build state -----------------------------------------------------------
# Did a tool's env build finish? `bdtools update` must be able to answer that on
# the NEXT run, and it could not: the update force-checks-out the new tag and
# bumps the manifest pin BEFORE building (install-local.sh's ensure_checkout
# resolves the checkout against the pin, so the pin has to move first), and its
# fast path then skips any tool already at the target ref whose env has a
# python. A build that died half-way leaves exactly that state — new code, old
# env, python still present — so the next `bdtools update` said "already at
# <tag> with a built env — skipping" and never retried. These helpers record the
# failure so the retry cannot be silently skipped.
_build_state_file() { printf '%s' "${BDTOOLS_HOME}/state/${1}.build-failed"; }

build_state_fail() {   # TOOL REF DETAIL — remember that this build did not finish
  [[ "${DRY_RUN:-0}" -eq 1 ]] && return 0
  local f; f="$(_build_state_file "$1")"
  mkdir -p "$(dirname "${f}")" 2>/dev/null || return 0
  printf 'ref=%s\nwhen=%s\ndetail=%s\n' \
    "${2:-unknown}" "$(date '+%Y-%m-%d %H:%M:%S')" "${3:-}" > "${f}" 2>/dev/null || true
}

build_state_ok() {     # TOOL — this build finished; forget any recorded failure
  [[ "${DRY_RUN:-0}" -eq 1 ]] && return 0
  rm -f "$(_build_state_file "$1")" 2>/dev/null || true
}

# build_failed_for TOOL REF — did the last recorded build of TOOL fail at REF?
# A failure recorded against a different ref is stale (the tool has moved on)
# and is ignored; a failure with no ref recorded counts for any ref.
build_failed_for() {
  local f ref; f="$(_build_state_file "$1")"
  [[ -f "${f}" ]] || return 1
  ref="$(sed -n 's/^ref=//p' "${f}" 2>/dev/null | head -1)"
  [[ -z "${ref}" || "${ref}" == "unknown" || "${ref}" == "${2:-}" ]]
}

# ---- resolve the suite's python (deferred until conda_base_dir exists) -----
PYBIN="$(bd_python || true)"; : "${PYBIN:=python3}"

# ---- registry files that may be conda-cache hardlinks -----------------------
# vsnp3's dependencies/reference_options_paths.txt inside a conda env is a
# HARDLINK into the package cache (pkgs/vsnp3-*/dependencies/...). Writing it
# in place (>>, sed -i, `> file`) writes through to the cache, so every future
# `conda create ... vsnp3` on the machine is born pre-seeded with this
# install's paths — that is exactly how junk reference locations spread to
# fresh envs. Always rewrite via tmp+mv: the rename breaks the hardlink and
# edits only this install.
registry_add_line() {   # <file> <line> — append if absent, hardlink-safe
  local f="$1" line="$2" tmp
  grep -qxF "${line}" "${f}" 2>/dev/null && return 0
  mkdir -p "$(dirname "${f}")"
  tmp="$(mktemp "${f}.XXXXXX")" || return 1
  { [[ -f "${f}" ]] && cat "${f}"; printf '%s\n' "${line}"; } > "${tmp}" || { rm -f "${tmp}"; return 1; }
  mv -f "${tmp}" "${f}"
}

registry_remove_line() {  # <file> <line> — remove exact line, hardlink-safe
  local f="$1" line="$2" tmp
  [[ -f "${f}" ]] || return 0
  grep -qxF "${line}" "${f}" 2>/dev/null || return 0
  tmp="$(mktemp "${f}.XXXXXX")" || return 1
  grep -vxF "${line}" "${f}" > "${tmp}" || true
  mv -f "${tmp}" "${f}"
}

# ---- Step 2 VCF databases (kapurlab/vcf_db_directories) ---------------------
# The lab's curated VCF comparison databases for vSNP Step 2, published at
# github.com/kapurlab/vcf_db_directories with the exact 2-level layout the GUI
# auto-discovers under vcf_db_folders_root (<reference>/<db_name>/*.vcf).
# Seeded ONCE per machine: the marker means an admin who later removes a DB
# never has it silently re-added, and an existing entry of the same name is
# never overwritten. Clone failure is a warn, not a die — these databases are
# an enhancement, and the repo may be unreachable (offline install, private).
VCF_DB_DIRS_REPO="https://github.com/kapurlab/vcf_db_directories.git"

seed_vcf_db_directories() {  # <vcf_db_folders_root> <clone_parent_dir>
  local db_root="$1" clone_parent="$2"
  local marker="${BDTOOLS_HOME}/state/vcf-db-directories.seeded"
  [[ -f "${marker}" ]] && return 0
  [[ "${DRY_RUN:-0}" -eq 1 ]] && { echo "  [dry-run] clone ${VCF_DB_DIRS_REPO} -> ${clone_parent}/vcf_db_directories; link into ${db_root}"; return 0; }
  local dest="${clone_parent}/vcf_db_directories"
  if [[ ! -d "${dest}" ]]; then
    mkdir -p "${clone_parent}"
    if ! git clone --depth 1 "${VCF_DB_DIRS_REPO}" "${dest}" 2>/dev/null; then
      warn "could not clone ${VCF_DB_DIRS_REPO} (offline?) — Step 2 VCF databases not seeded; re-run 'bdtools setup-databases vcf-dbs' later"
      return 0
    fi
  fi
  mkdir -p "${db_root}"
  local d name linked=0
  for d in "${dest}"/*/; do
    [[ -d "${d}" ]] || continue
    name="$(basename "${d}")"
    case "${name}" in .*) continue;; esac
    if [[ ! -e "${db_root}/${name}" ]]; then
      ln -sfn "${d%/}" "${db_root}/${name}" && linked=$((linked+1))
    fi
  done
  mkdir -p "${BDTOOLS_HOME}/state"
  printf 'when=%s\nclone=%s\nroot=%s\nlinked=%s\n' \
    "$(date -u +%FT%TZ)" "${dest}" "${db_root}" "${linked}" > "${marker}"
  ok "Step 2 VCF databases seeded: ${dest} -> ${db_root} (${linked} linked)"
}
