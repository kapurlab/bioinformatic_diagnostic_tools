# Sysadmin guide — publishing the tools as system OOD apps

> **Status: DRAFT / pending the `install-server` increment.** It generalizes the
> proven `vsnp_gui/deploy/install_ood.sh` (layers 3–4) + `register_ood_apps.sh`.

This guide is written so the install looks like **any other Open OnDemand app**
you already manage — nothing exotic.

## The familiar shape

Each tool is a standard `batch_connect` app distributed as a **git repo with
semantic-version tags and GitHub Releases**. Installing one is the same motion
you use for other OOD apps:

```bash
# per tool — the conventional OOD sys-app install
cd /var/www/ood/apps/sys
sudo git clone https://github.com/kapurlab/<tool>.git
sudo <tool>/deploy/install.sh           # builds conda env + frontend (no extra magic)
```

Because the app is a git checkout, OOD's built-in **version dropdown** lets you
pin or switch tags per app — the standard upgrade path.

The umbrella wraps the whole suite so you don't repeat this by hand:

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
cp sites/site.conf.example sites/site.conf   # set CLUSTER_NAME, paths, groups, branding
sudo bin/bdtools install --server all --dry-run   # review every change first
sudo bin/bdtools install --server all
```

## What it assumes about your site

- **You already run OOD core** — the installer does **not** touch Apache, PAM,
  your scheduler, or auth. It installs only the app cards + their conda envs +
  reference DBs (layers 3–4).
- **Cluster name is data, not code** — set `CLUSTER_NAME` in `site.conf`; the app
  forms are rendered with it. No hardcoded cluster.
- **Scheduler** — the cards use a `basic` `batch_connect` template (single proxied
  port). Slurm is assumed; other schedulers need the usual `clusters.d` adapter.
- **Everything is idempotent and `--dry-run`-able**, per-phase, with preflight
  checks — re-running is safe.

## Reference-database staging

Large/licensed reference sets are **not** bundled in the repos. The installer
stages them into a site DBs root (set in `site.conf`) with a documented
download/verify step per tool. Plan disk accordingly.

## Optional: Ansible

For sites that manage OOD with Ansible, an `osc.ood`-style role is provided under
[`../ansible/`](../ansible/) to clone the tools, run the installers, and register
the cards idempotently.
