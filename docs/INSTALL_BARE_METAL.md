# Bare-metal Linux lab server (full stack)

> **Status: partial.** `bdtools install --server` (layers 3–4: tool env +
> frontend + OOD app cards) is implemented. The lower layers (OS/storage) and
> the one-time site bootstrap (OOD core, groups, quotas, dashboard branding) are
> still driven by `ood-core/bootstrap_ood_core.sh` and the site-bootstrap phases
> of `vsnp_gui/deploy/install_ood.sh` — validated against the wgs3 reference
> install (`vsnp_gui/docs/deploy/INSTALL_OOD.md`). Promoting those site-bootstrap
> phases into the umbrella is a later step.

Use this when you control a fresh Linux box and there is **no Open OnDemand yet**.
You install bottom-up; only the top layers are "ours".

| Layer | What | Tool |
|---|---|---|
| 1. OS + storage | Ubuntu + an XFS `prjquota` data disk | manual |
| 2. OOD core | Open OnDemand, Apache+PAM, Apptainer, session image | `ood-core/bootstrap_ood_core.sh` |
| 3. Toolchain | conda envs, reference DBs, frontends | `bdtools install --server …` |
| 4. OOD app layer | the app cards + cluster config + portal | `bdtools install --server …` |

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
cp sites/site.conf.example sites/site.conf && $EDITOR sites/site.conf

sudo ood-core/bootstrap_ood_core.sh --dry-run     # layer 2 (skip if OOD already present)
sudo ood-core/bootstrap_ood_core.sh

sudo bin/bdtools install --server all --dry-run # layers 3-4
sudo bin/bdtools install --server all
```

Then browse to `http://<SERVERNAME>/`, log in, and launch a tool card.
Everything site-specific lives in `sites/site.conf`; the repo files are never
edited per-site.
