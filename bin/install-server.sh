#!/usr/bin/env bash
# install-server.sh — system-wide OOD app install (sysadmin / bare-metal).
#
# STATUS: SCAFFOLD (deferred). This is the generalization of the proven
# vsnp_gui/deploy/install_ood.sh (layers 3-4) + register_ood_apps.sh into a
# manifest-driven loop that installs ANY subset of the suite's tools as system
# OOD apps, reading site-specific values from sites/site.conf.
#
# Two sub-cases (decided by site.conf / flags):
#   * Bare-metal lab server: optionally bootstrap OOD core first
#     (ood-core/bootstrap_ood_core.sh), then install layers 3-4.
#   * Existing institutional OOD (e.g. a university cluster): SKIP core; install
#     only the toolchain + app cards under /var/www/ood/apps/sys, under the
#     site's existing scheduler + auth.
#
# Per tool it will: build/locate the conda env, stage reference DBs, build the
# frontend, render ood/apps/<tool> with site values (cluster name, paths), and
# register the card. Idempotent, --dry-run, per-phase — same idiom as install_ood.sh.
#
# References to promote: vsnp_gui/deploy/install_ood.sh, install_kraken.sh,
#   register_ood_apps.sh, site.conf.example.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TOOL="${1:-}"
warn "install-server is not implemented yet (planned increment)."
warn "Promote vsnp_gui/deploy/install_ood.sh (layers 3-4) + register_ood_apps.sh here,"
warn "looping over tools.yml and rendering each ood/apps/<tool> from sites/site.conf."
[[ -n "${TOOL}" ]] && manifest_has "${TOOL}" \
  && echo "Would install '${TOOL}' as a system OOD app (sys-apps dir) using sites/site.conf."
exit 2
