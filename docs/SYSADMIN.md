# Sysadmin guide — publishing the tools as system OOD apps

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

## What `install --server` does (per tool)

Phases (`preflight toolchain app verify`, all idempotent, `--dry-run`-able):

- **preflight** — checks OOD core is present, conda/npm available, the
  `CLUSTER_NAME` cluster is defined, and the sys-apps dir is writable.
- **toolchain** — checks out the pinned tool at `TOOLS_ROOT/<tool>` and builds
  its conda env + frontend via the tool's own `deploy/install.sh`.
- **app** — renders each `ood/apps/<app>` into the sys-apps dir, rewriting the
  Kapur Lab literals (paths, cluster name, group names) from `site.conf`.
- **verify** — confirms the env, frontend, and card are in place.

## What it assumes / does NOT touch

- **You already run OOD core** — it does **not** touch Apache, PAM, your
  scheduler, auth, Unix groups, storage/quotas, or dashboard branding. Those are
  one-time site bootstrap (institutional sites own them; a bare-metal lab server
  uses `ood-core/bootstrap_ood_core.sh` + the site-bootstrap phases of
  `vsnp_gui/deploy/install_ood.sh`).
- **Cluster name is data, not code** — set `CLUSTER_NAME` in `site.conf`; each
  app form is rendered with it. It does **not** create or overwrite your
  `clusters.d/<cluster>.yml` (institutional sites already have one); preflight
  just warns if the named cluster isn't defined.
- **Scheduler** — the cards use a `basic` `batch_connect` template (single
  proxied port). Slurm is assumed; other schedulers need the usual adapter.

## Reference databases

Large/licensed reference sets are **not** bundled and are **not** auto-staged by
`install --server`. Stage them into your `DATABASES_ROOT` per each tool's own
docs (some tools bundle their refs in the conda package; others need a download).
Plan disk accordingly.

## Optional: Ansible

For sites that manage OOD with Ansible, an `osc.ood`-style role is provided under
[`../ansible/`](../ansible/) to clone the tools, run the installers, and register
the cards idempotently.
