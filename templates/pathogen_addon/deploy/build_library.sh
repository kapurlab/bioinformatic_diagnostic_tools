#!/usr/bin/env bash
# Build this add-on's reference library into the folder bdtools names.
#
#   bash deploy/build_library.sh <library dir>
#
# bdtools runs this after building the add-on's env (that env's bin/ is first
# on PATH) with <db root>/addons/__ADDON_ID__ as the folder, and records it in
# .library_path. Idempotent: a library already built for this LIBRARY_VERSION
# is left alone. No sudo; never writes outside the library folder.
#
# What a library must hold (addon.json "library.markers" are checked by
# bdtools doctor and vsnp_gui):
#   reference_options/<name>/<name>.fasta   one vsnp3 reference folder per
#                                           reference the chooser may return
#                                           (+ <name>.gbk/.gff for annotation)
#   vcf_db_folders/<name>/<db>/*_zc.vcf     optional Step 2 databases
#   scopes.json                             the project form's scope picker
#   LIBRARY_VERSION                         written last: the library is complete
set -euo pipefail
LIB="${1:?usage: build_library.sh <library dir>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="1"
if [[ -f "${LIB}/LIBRARY_VERSION" && "$(cat "${LIB}/LIBRARY_VERSION")" == "${VERSION}" ]]; then
  echo "library ${LIB} is current (version ${VERSION})"
  exit 0
fi
mkdir -p "${LIB}"
# TODO(add-on author): replace the example below with the real library build:
# fetch the reference genomes (from a pinned release, never "latest"), make one
# vsnp3 reference folder per reference, build any typing database, and write
# scopes.json from the library's own hierarchy.
NAME="__ADDON_ID___example"
REF="${LIB}/reference_options/${NAME}"
mkdir -p "${REF}"
cp "${HERE}/deploy/example/reference.fasta" "${REF}/${NAME}.fasta"
cat > "${LIB}/scopes.json" <<JSON
{"label": "Scope", "default": "all",
 "options": [{"value": "all", "label": "Any reference"},
             {"value": "genome:${NAME}", "label": "${NAME}"}]}
JSON
printf '%s\n' "${VERSION}" > "${LIB}/LIBRARY_VERSION"
echo "library built: ${LIB}"
