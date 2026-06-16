# Bare-metal Linux lab server (full stack)

Use this when you control a fresh Linux box and there is **no Open OnDemand yet**.
You install bottom-up; only the top layers are "ours". Everything site-specific
lives in one `site.conf`; repo files are never edited per-site.

| Layer | What | Tool |
|---|---|---|
| 1. OS + storage | Ubuntu + an XFS `prjquota` data disk | manual |
| 2. OOD core | Open OnDemand, Apache+PAM, Apptainer, session image | `ood-core/bootstrap_ood_core.sh` |
| 3. Site bootstrap | Unix groups, shared storage tree, dashboard branding | `bdtools site-init` |
| 4. Per-tool apps | conda envs + frontends + OOD app cards | `bdtools install --server …` |

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
cp sites/site.conf.example sites/site.conf && $EDITOR sites/site.conf

# Layer 2 — OOD core (skip if OOD is already installed)
sudo ood-core/bootstrap_ood_core.sh --dry-run
sudo ood-core/bootstrap_ood_core.sh

# Layer 3 — site bootstrap (groups, storage tree, starter branding)
sudo bin/bdtools site-init --site-conf sites/site.conf --dry-run
sudo bin/bdtools site-init --site-conf sites/site.conf

# Layer 4 — install every tool as a system OOD app
sudo bin/bdtools install --server all --site-conf sites/site.conf --dry-run
sudo bin/bdtools install --server all --site-conf sites/site.conf
```

Then browse to `http://<SERVERNAME>/`, log in, and launch a tool card.

> **Still manual / out of scope for the umbrella:** OS install, disk/XFS+quota
> provisioning (layer 1), the scheduler + auth, and deep dashboard prose/logo
> branding. `site-init` writes a correct *starter* branding snippet; the full
> wgs3 reference (with admin scripts, quotas, provenance cron) lives in
> `vsnp_gui/deploy/install_ood.sh` + `docs/deploy/INSTALL_OOD.md` if you want it.
