# Bare-metal Linux lab server (full stack)

> **Status: DRAFT / pending the `install-server` increment.** The full four-layer
> runbook already exists and is validated against the wgs3 reference install:
> see `vsnp_gui/docs/deploy/INSTALL_OOD.md`. This umbrella version generalizes it
> to install the whole suite from one manifest.

Use this when you control a fresh Linux box and there is **no Open OnDemand yet**.
You install bottom-up; only the top layers are "ours".

| Layer | What | Tool |
|---|---|---|
| 1. OS + storage | Ubuntu + an XFS `prjquota` data disk | manual |
| 2. OOD core | Open OnDemand, Apache+PAM, Apptainer, session image | `ood-core/bootstrap_ood_core.sh` |
| 3. Toolchain | conda envs, reference DBs, frontends | `kapurtools install --server …` |
| 4. OOD app layer | the app cards + cluster config + portal | `kapurtools install --server …` |

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
cp sites/site.conf.example sites/site.conf && $EDITOR sites/site.conf

sudo ood-core/bootstrap_ood_core.sh --dry-run     # layer 2 (skip if OOD already present)
sudo ood-core/bootstrap_ood_core.sh

sudo bin/kapurtools install --server all --dry-run # layers 3-4
sudo bin/kapurtools install --server all
```

Then browse to `http://<SERVERNAME>/`, log in, and launch a tool card.
Everything site-specific lives in `sites/site.conf`; the repo files are never
edited per-site.
