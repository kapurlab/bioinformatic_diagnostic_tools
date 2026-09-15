#!/usr/bin/env bash
# rebuild-native.sh — move a tool's conda env off a translation layer and onto
# the platform this machine runs natively.
#
#   rebuild-native.sh [tool ...] [--apply]
#
# Reports by default; changes nothing without --apply.
#
# WHY THIS EXISTS. On Apple Silicon the suite builds osx-64 envs that run under
# Rosetta 2 (install-local.sh's ensure_conda_subdir, rule 2): when that rule was
# written, much of the bioinformatics closure had no native arm64 build, so a
# native solve either failed or resolved a partial toolchain. That made Rosetta a
# hard dependency of every Mac install, and the only remedy the suite could offer
# when it was missing was "install Rosetta" — which stops being an answer the day
# Apple withdraws it. macOS 27 already ships without it until it is installed by
# hand (2026-09-15: a macOS 27 upgrade left five tools unable to start a single
# binary), and Apple has said the translation layer is being wound down.
#
# The premise behind rule 2 also expires quietly. bioconda gains osx-arm64 builds
# over time, and nothing was watching for that — so an install could stay pinned
# to a translation layer it no longer needed, with no way to find out but trying.
# This script asks the question the only way it can be answered honestly: it runs
# a real solve, for THIS machine's native platform, against the same spec the
# installer would use, and reports per tool. A tool whose dependencies have
# native builds today is rebuilt; one whose dependencies do not is named,
# together with the package that blocks it, so the answer is a fact about
# bioconda rather than a guess.
#
# NOT macOS-specific, by construction. The same question — "is this env built for
# a platform this host cannot run natively, and can it be moved?" — is asked from
# host_conda_subdir on every platform, so an x86_64 env on aarch64 Linux (where
# there is no translation layer at all, and the env simply cannot run) gets the
# same report and the same rebuild. Linux/x86_64 and Intel Macs are already
# native and report as such; nothing is rebuilt there.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

APPLY=0
WANT=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)   APPLY=1; shift;;
    --report)  APPLY=0; shift;;            # the default; accepted for symmetry
    --dry-run) DRY_RUN=1; export DRY_RUN; shift;;
    # 's/^# \{0,1\}//', not 's/^# \?//': BSD sed (macOS) has no \? in a basic
    # regex and matches it literally, so the shipped help printed every line
    # with its leading '#' still attached. The rest of the suite already uses
    # this form (install-local.sh, doctor.sh) for the same reason.
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*)        die "unknown option: $1 (see: bdtools rebuild-native --help)";;
    *)         WANT+=("$1"); shift;;
  esac
done

NATIVE="$(host_conda_subdir)"
[[ -n "${NATIVE}" ]] || die "this platform ($(uname -s) $(uname -m)) has no conda subdir mapping — nothing to rebuild against"

if [[ ${#WANT[@]} -eq 0 ]]; then
  while IFS= read -r _t; do WANT+=("${_t}"); done < <(manifest_names)
fi

CONDA="$(detect_conda)" || die "conda/mamba not found. Install miniforge first."

log "native platform for this machine: ${NATIVE}"
info "  conda: ${CONDA}"
[[ ${APPLY} -eq 1 ]] || info "  REPORT ONLY — nothing will be changed. Re-run with --apply to rebuild."
echo

# Merge a tool's conda_setup/environment.yml with the manifest's exact
# `packages:` pins — see _probe_native for why that combination is the spec that
# matters.
_native_specs() {   # TOOL -> one conda spec per line (empty when there is none)
  _need_python
  "${PYBIN}" - "$(tool_dir "$1")/conda_setup/environment.yml" \
      "$(manifest_get "$1" packages 2>/dev/null || true)" <<'PY'
import re, sys
env_file, pinned = sys.argv[1], sys.argv[2].split()

# The manifest's exact pins, by package name: bioconda::vsnp3=3.36 -> vsnp3.
pins = {}
for spec in pinned:
    spec = spec.split("::")[-1]
    name = re.split(r"[=<>!~ ]", spec, 1)[0]
    if name:
        pins[name] = spec

specs, seen = [], set()
try:
    lines = open(env_file, encoding="utf-8", errors="replace").read().splitlines()
except OSError:
    lines = []

in_deps = in_pip = False
for ln in lines:
    if re.match(r"^\s*dependencies:", ln):
        in_deps = True
        continue
    if not in_deps:
        continue
    if re.match(r"^\s*-\s*pip:\s*$", ln):
        in_pip = True                      # pip deps are not part of the conda solve
        continue
    if in_pip:
        if re.match(r"^\s{6,}-", ln):
            continue
        in_pip = False
    if re.match(r"^\s*\w+:", ln):          # a new top-level key ends dependencies:
        break
    m = re.match(r"^\s*-\s+([^#]+)", ln)
    if not m:
        continue
    spec = " ".join(m.group(1).split())     # 'plotly <6' -> 'plotly <6'
    if not spec:
        continue
    name = re.split(r"[=<>!~ ]", spec, 1)[0]
    # The manifest pin REPLACES the spec's own range. generic_build solves the
    # open spec and enforce_package_pins then installs the exact pin into the
    # built env, so a probe that solved 'mlst>=2.23' would answer a question the
    # installer never asks — and could report "CAN go native" for an env whose
    # pinned version has no native build.
    specs.append(pins.pop(name, spec))
    seen.add(name)

specs.extend(pins[n] for n in list(pins))   # pins for packages the spec omits
print("\n".join(specs))
PY
}

# Channels the tool's own spec declares, so the probe resolves against the same
# set the build will. Defaults to the suite's pair when the file says nothing.
_native_channels() {   # TOOL
  local f chans
  f="$(tool_dir "$1")/conda_setup/environment.yml"
  chans="$(sed -n '/^channels:/,/^[a-z_]*:/p' "${f}" 2>/dev/null \
           | grep -E '^\s*-\s' | sed 's/^[[:space:]]*-[[:space:]]*//' | grep -v '^$' || true)"
  [[ -n "${chans}" ]] || chans=$'conda-forge\nbioconda'
  printf '%s\n' "${chans}"
}

# Can this tool's env be built for ${NATIVE}? 0 yes, 1 no (blockers on stdout),
# 2 nothing to solve.
#
# Solves the spec the INSTALLER would produce: the tool's
# conda_setup/environment.yml with the manifest's exact `packages:` pins applied
# over it (generic_build then enforce_package_pins), or the pins alone for a
# tool that ships no env file (build_vsnp_local). Deliberately not a third
# notion of "this tool's spec" — a probe that solves something the build would
# not is a probe that can be confidently wrong, which is the failure mode this
# whole script exists to end.
_probe_native() {   # TOOL
  local tool="$1" out rc _c specs=() chans=()
  while IFS= read -r _c; do [[ -n "${_c}" ]] && specs+=("${_c}"); done < <(_native_specs "${tool}")
  if [[ ${#specs[@]} -eq 0 ]]; then
    echo "no conda spec in the checkout and no packages: pins in the manifest — nothing to solve"
    return 2
  fi
  while IFS= read -r _c; do [[ -n "${_c}" ]] && chans+=(-c "${_c}"); done < <(_native_channels "${tool}")
  out="$(mktemp)"
  # A NAMED probe env, never a prefix inside the checkout: under --dry-run conda
  # writes nothing either way, but a directory left in a tool's tree would be
  # indistinguishable from a half-built env to resolve_env_prefix — the "corpse
  # gets a vote" defect install-local.sh documents.
  CONDA_SUBDIR="${NATIVE}" "${CONDA}" create --dry-run -y \
      -n "_bdtools_native_probe_${tool}" "${chans[@]}" "${specs[@]}" >"${out}" 2>&1
  rc=$?
  if [[ ${rc} -ne 0 ]]; then
    _solve_blockers "${out}"
    rm -f "${out}"
    return 1
  fi
  rm -f "${out}"
  return 0
}

# The package(s) with no native build, as ONE line, from either solver's words.
#
# conda and mamba report an unsatisfiable solve in completely different formats,
# and detect_conda returns whichever the machine has — so parsing one of them is
# parsing half the installs. classic conda raises PackagesNotFoundError with a
# bullet list; libmamba prints a dependency tree with box-drawing glyphs. The
# first cut of this function knew only libmamba's wording, and the two tools it
# could not parse (ncbi_submit_gui, mhc_gui) reported "BLOCKED" with an empty
# reason — a verdict with no evidence, which is the same defect as the doctor
# misdiagnosis this whole change exists to fix.
#
# Falls back to the solver's last words rather than printing nothing: an
# unrecognised error is still evidence, and silence is not.
_solve_blockers() {   # LOGFILE
  _need_python
  "${PYBIN}" - "$1" "${NATIVE}" <<'PY'
import re, sys

try:
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except OSError:
    text = ""
native = sys.argv[2]

names = []
def add(n):
    n = n.strip().strip(";.,")
    # A package name starts with a letter. Without this the version ranges in
    # libmamba's own wording leak through as package names: "perl
    # >=5.26.2,<5.26.3.0a0 *, which does not exist" reported '5.26.3.0a0', and
    # "spades >=3.14,<4 *" reported '4' — because the comma inside a range ends
    # the field a looser pattern was matching on.
    if n and n[0].isalpha() and n not in names:
        names.append(n)

# libmamba: "nothing provides blat >=35 needed by irma-1.3.1-..."
for m in re.finditer(r"nothing provides ([A-Za-z][A-Za-z0-9_.+-]*)", text):
    add(m.group(1))
# libmamba tree: "|- blat >=35 *, which does not exist (perhaps a missing channel)."
# Anchored on the tree glyph that precedes the name, so a version range inside
# the line can never be mistaken for one.
for line in text.splitlines():
    if "which does not exist" not in line:
        continue
    m = re.search(r"[\u2514\u251c]\u2500\s*([A-Za-z][A-Za-z0-9_.+-]*)", line)
    if m:
        add(m.group(1))
# classic conda: PackagesNotFoundError, then a bullet per package.
m = re.search(r"PackagesNotFoundError.*?\n(.*?)(?:\nCurrent channels:|\Z)",
              text, re.DOTALL)
if m:
    for line in m.group(1).splitlines():
        b = re.match(r"\s+-\s+(\S+)", line)
        if b:
            add(b.group(1))

if names:
    print(f"no {native} build for: " + ", ".join(sorted(names)))
else:
    # An unrecognised solver error is still evidence, and silence is not: the
    # first cut of this printed nothing for the two tools whose solver spoke a
    # format it did not know, which is a verdict with no reason attached — the
    # same defect as the doctor misdiagnosis this change exists to fix.
    tail = [ln.strip() for ln in text.splitlines() if ln.strip()][-2:]
    print(" ".join(tail) if tail else "the solver failed and said nothing")
PY
}

# Vendored binaries this host cannot execute, comma-separated, or empty.
#
# A conda env is not the whole tool. ksnp_gui's kSNP4 payload is downloaded by
# hand from SourceForge and is x86_64-only, so its env can be osx-arm64 — this
# report called it "already native" — while the tool still cannot run a single
# analysis without Rosetta. Rebuilding cannot fix that: there is no arm64 build
# to fetch. Saying "already native" and stopping there answers the question the
# user is actually asking ("can I stop needing Rosetta?") with a fact about the
# wrong half of the tool.
#
# Judged against the NATIVE platform, deliberately NOT against doctor's "can
# this host execute it" rule. Those answer different questions and must not be
# conflated here: with Rosetta installed, an Intel payload runs, so doctor is
# right to stay quiet — but this report exists to answer "can this tool stop
# needing Rosetta", and there the same payload is a hard no. Asking doctor's
# question here made ksnp_gui read "already native" the moment Rosetta was
# reinstalled, which is exactly the wrong answer to the question being asked.
#
# check.py still owns the file-format reading (_binary_target), so there is one
# parser; only the verdict differs, and the comment above says why.
_vendor_blockers() {   # TOOL ENVDIR
  _need_python
  "${PYBIN}" - "${KT_BIN_DIR}/lib" "$1" "$(tool_dir "$1")" "$2" "${NATIVE}" <<'PY'
import os, sys
sys.path.insert(0, sys.argv[1])
try:
    import check as CHECK
    import requirements
except Exception:
    print(""); raise SystemExit
tool, tool_dir, envdir, native = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
try:
    want = CHECK._SUBDIR_TARGET.get(native)
    spec = requirements.for_tool(tool)
    probes = spec.get("binary_format_probes") or []
    if not probes or not want:
        print(""); raise SystemExit
    found, _missing = CHECK.resolve_asset_dirs(
        tool, tool_dir, spec.get("asset_dirs") or [])
    bad = []
    for probe in probes:
        path = CHECK.find_binary(probe, os.path.join(envdir, "bin"), found)
        if not path:
            continue                     # absent is the existence check's job
        target = CHECK._binary_target(path)
        if target is None or target[1] == "universal":
            continue                     # a script, or runs on both slices
        if target != want:
            bad.append(probe)
    print(", ".join(bad))
except Exception:
    print("")            # never let the extra check fail the report
PY
}

NEEDS=(); CAN=(); CANNOT=(); NATIVE_OK=(); NOENV=(); VENDOR=()
for tool in "${WANT[@]}"; do
  manifest_has "${tool}" || { warn "no tool named '${tool}' in the manifest — skipping"; continue; }
  envdir="$(tool_env_prefix "${tool}" 2>/dev/null || true)"
  if [[ -z "${envdir}" || ! -d "${envdir}" ]]; then
    NOENV+=("${tool}"); continue
  fi
  cur="$(env_conda_subdir "${envdir}")"
  if [[ -z "${cur}" ]]; then
    # A noarch-only env records no platform, so there is nothing to move and
    # nothing to judge — not a finding, and not a silent pass either.
    NATIVE_OK+=("${tool} (records no platform — noarch only)"); continue
  fi
  vb="$(_vendor_blockers "${tool}" "${envdir}")"
  [[ -n "${vb}" ]] && VENDOR+=("${tool}|${vb}")
  if [[ "${cur}" == "${NATIVE}" ]]; then
    NATIVE_OK+=("${tool} (${cur})"); continue
  fi
  NEEDS+=("${tool}|${envdir}|${cur}")
done

if [[ ${#NEEDS[@]} -eq 0 ]]; then
  # Distinguish "checked them all, all native" from "there was nothing to
  # check": claiming every tool is native after examining none is a true
  # statement that reads as a result.
  if [[ ${#NATIVE_OK[@]} -eq 0 ]]; then
    info "no installed tool to examine here — nothing to rebuild"
  else
    ok "every installed tool's env is already native (${NATIVE}) — nothing to rebuild"
  fi
else
  log "checking whether each non-native env can be rebuilt for ${NATIVE}"
  for rec in "${NEEDS[@]}"; do
    tool="${rec%%|*}"; rest="${rec#*|}"; envdir="${rest%%|*}"; cur="${rest##*|}"
    printf '  %-22s %s -> %s ... ' "${tool}" "${cur}" "${NATIVE}"
    # rc captured explicitly, never read as $? inside an elif: there the last
    # command is the failed assignment's own substitution, which happens to work
    # today and silently would not the moment a line is inserted between them.
    prc=0
    detail="$(_probe_native "${tool}")" || prc=$?
    if [[ ${prc} -eq 0 ]]; then
      echo "CAN go native"
      CAN+=("${tool}|${envdir}|${cur}")
    elif [[ ${prc} -eq 2 ]]; then
      echo "cannot tell"
      info "      ${detail}"
    else
      echo "BLOCKED"
      CANNOT+=("${tool}|${detail}")
    fi
  done
fi

echo
log "summary"
for t in "${NATIVE_OK[@]+"${NATIVE_OK[@]}"}"; do ok "already native: ${t}"; done
for t in "${NOENV[@]+"${NOENV[@]}"}";   do info "not installed here: ${t}"; done
for rec in "${CAN[@]+"${CAN[@]}"}"; do
  tool="${rec%%|*}"; cur="${rec##*|}"
  ok "can go native: ${tool} (${cur} -> ${NATIVE})"
done
for rec in "${CANNOT[@]+"${CANNOT[@]}"}"; do
  tool="${rec%%|*}"; detail="${rec#*|}"
  warn "cannot go native yet: ${tool}"
  info "      ${detail}"
done
# Reported after the env verdicts and separately from them: this is a different
# fault with a different remedy (there is none — the payload has no native
# build), and folding it into "cannot go native" would suggest a rebuild might
# help.
for rec in "${VENDOR[@]+"${VENDOR[@]}"}"; do
  tool="${rec%%|*}"; detail="${rec#*|}"
  # "not ${NATIVE}", never "cannot run": with Rosetta installed they run fine.
  # The finding is that this tool cannot be freed of the translation layer,
  # which is a different claim from "broken" and stays true in both states.
  warn "${tool}: a native env is not enough — vendored binaries are not ${NATIVE}: ${detail}"
  info "      These are downloaded payloads, not conda packages, so no rebuild"
  info "      changes them. This tool needs the translation layer until its"
  info "      upstream ships a native build."
done

if [[ ${#CANNOT[@]} -gt 0 ]]; then
  echo
  info "Those tools keep the platform they have; their env still runs if it can run here."
  # Rosetta is Apple Silicon's alone, and only for x86_64-on-arm64. Offering it
  # on an Intel Mac (where NATIVE is osx-64 and the foreign env is arm64) or on
  # Linux would be advice that cannot help — and telling someone to install what
  # they already have reads as "action required" when none is.
  if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    if /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
      info "  Rosetta 2 is installed, so they run — nothing to do for them."
    else
      info "  On Apple Silicon that means they need Rosetta 2, which is NOT installed:"
      info "      softwareupdate --install-rosetta --agree-to-license"
    fi
  fi
  info "  This is a fact about the channels today, not a permanent one — re-run"
  info "  this report after a suite update and a blocked tool may have moved."
fi

if [[ ${#CAN[@]} -eq 0 ]]; then
  echo; info "nothing to rebuild."
  exit 0
fi

if [[ ${APPLY} -eq 0 ]]; then
  echo
  log "to rebuild the tools that can go native:"
  for rec in "${CAN[@]}"; do info "      bin/bdtools rebuild-native ${rec%%|*} --apply"; done
  info "  or all of them at once:  bin/bdtools rebuild-native --apply"
  info "  Each rebuild sets the old env aside and puts it back if the build fails."
  info "  To go back after a build that SUCCEEDED:  bin/bdtools restore-env <tool>"
  exit 0
fi

# ---- apply -----------------------------------------------------------------
# A rebuild REPLACES an env, so each tool goes in its own subshell: one failed
# solve must not abandon the rest, the same rule `bdtools update` follows. The
# old env is set aside and restored on failure by --fresh itself (see
# discard_env_for_fresh), which is why nothing is snapshotted here.
echo
warn "rebuilding ${#CAN[@]} env(s) for ${NATIVE}. This replaces each env and can take several minutes per tool."
failed=()
for rec in "${CAN[@]}"; do
  tool="${rec%%|*}"; rest="${rec#*|}"; envdir="${rest%%|*}"; cur="${rest##*|}"
  echo
  log "${tool}: ${cur} -> ${NATIVE}"
  # No snapshot_env here: --fresh takes one itself (discard_env_for_fresh), and
  # taking a second would rotate the just-written snapshot into .prev, throwing
  # away the older generation that is the whole point of keeping one back.
  # --fresh also sets the old env aside and puts it back automatically if the
  # build fails, which is a stronger guarantee than the snapshot.
  #
  # --build-only: this is a rebuild, not a launch. Without it install-local.sh
  # ends by starting the tool and opening a browser, so rebuilding three envs
  # would open three tabs and leave three servers running.
  #
  # CONDA_SUBDIR is honoured because build() discards the old env BEFORE
  # ensure_conda_subdir runs: with no existing env to read, rule 1 has nothing
  # to pin and the preset wins. That ordering is load-bearing for this whole
  # command, so the result is verified below rather than assumed.
  if ( CONDA_SUBDIR="${NATIVE}" export CONDA_SUBDIR
       "${KT_BIN_DIR}/install-local.sh" "${tool}" --fresh --build-only ); then
    # No `local`: this loop runs at the top level of the script, where bash
    # rejects it outright ("local: can only be used in a function") — and
    # `bash -n` does not catch that, so it would have failed at the end of a
    # multi-minute rebuild rather than at parse time.
    newenv="$(tool_env_prefix "${tool}" 2>/dev/null || true)"
    got="$(env_conda_subdir "${newenv}")"
    if [[ -n "${got}" && "${got}" != "${NATIVE}" ]]; then
      # The build reported success and produced the platform we were moving
      # AWAY from. Saying "rebuilt for osx-arm64" here would be the same class
      # of lie this command exists to remove from the doctor.
      failed+=("${tool}")
      warn "${tool}: build succeeded but the env is ${got}, not ${NATIVE} — not migrated."
      info "  Its previous env was set aside and restored only on failure, so check:"
      info "      bin/bdtools doctor ${tool}"
    else
      ok "${tool}: rebuilt for ${got:-${NATIVE}}"
    fi
  else
    failed+=("${tool}")
    warn "${tool}: rebuild failed — --fresh put the previous env back, so the tool is as it was."
    info "      bin/bdtools restore-env ${tool}    # if anything still looks wrong"
  fi
done

echo
if [[ ${#failed[@]} -gt 0 ]]; then
  warn "failed to rebuild: ${failed[*]}"
  info "  Verify what is left:  bin/bdtools doctor ${failed[*]}"
  exit 1
fi
ok "all requested envs are now ${NATIVE}"
info "  Verify:  bin/bdtools doctor"
