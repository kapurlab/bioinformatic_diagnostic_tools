#!/usr/bin/env bash
# install-sandbox.sh — per-user, no-sudo OOD "sandbox" app install.
#
# STATUS: SCAFFOLD (deferred). This is the generalization of the proven
# vsnp_gui/deploy/setup-sandbox.sh into a manifest-driven, any-tool installer.
# It touches an existing tool repo's conventions, so it is intentionally left
# for the increment after the umbrella scaffold is reviewed.
#
# Target: an institutional OOD cluster where the user is NOT an admin. It will:
#   1. ensure a $HOME conda (miniforge),
#   2. build the tool's conda env + frontend under $HOME (reusing the tool's
#      deploy/install.sh --personal where available),
#   3. write ~/.config/<tool>/sandbox.env for the OOD script to source,
#   4. symlink the tool's ood/apps/<tool> card into ~/ondemand/dev/ so it shows
#      up under "My Sandbox Apps".
#
# Reference implementation to promote: vsnp_gui/deploy/setup-sandbox.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TOOL="${1:-}"
warn "install-sandbox is not implemented yet (planned increment)."
warn "Promote vsnp_gui/deploy/setup-sandbox.sh here, parameterized by tool."
[[ -n "${TOOL}" ]] && manifest_has "${TOOL}" \
  && echo "Would install '${TOOL}' as an OOD sandbox app for $(whoami) under ~/ondemand/dev/."
echo "For now, on a personal machine use:  kapurtools install --local ${TOOL:-<tool>}"
exit 2
