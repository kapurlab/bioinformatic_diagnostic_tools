# ood-core/

The bare-metal Open OnDemand core bootstrap goes here, promoted from
`vsnp_gui/deploy/bootstrap_ood_core.sh`. It installs OOD core (Apache+PAM,
Apptainer, the session image, portal config) on a fresh Linux box — layer 2 of
the bare-metal install. Skip it entirely on a site that already runs OOD.

Status: pending the `install-server` increment (see docs/INSTALL_BARE_METAL.md).
