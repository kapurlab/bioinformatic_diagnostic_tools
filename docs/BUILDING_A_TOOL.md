# The tool-repo contract

A tool repo stays independent and individually releasable. To be drivable from
the umbrella it must satisfy this light contract — which the existing tools
already mostly follow (see the canonical guide in
`amr_plus_gui/docs/BUILDING_A_SIBLING_TOOL.md`, to be promoted here).

## Required layout

```
<tool>/
├── backend/app/main.py          FastAPI app exposed as `app.main:app`; serves frontend/dist/
├── backend/requirements.txt     pip deps for the web layer
├── frontend/                    React + Vite SPA; vite base "./"; relative API URLs
├── conda_setup/environment.yml  the tool's conda env (pinned where it matters)
├── deploy/install.sh            no-sudo build of env + frontend (supports --personal)
└── ood/apps/<tool>/             batch_connect app: manifest.yml form.yml submit.yml.erb template/
```

## Rules that keep it portable

1. **All frontend URLs relative** (`./api/...`); FastAPI serves `frontend/dist/`.
   This is what lets the same app run behind OOD's proxy *and* standalone locally.
2. **Cluster name is data, not code** — `ood/apps/<tool>/form.yml` must take the
   cluster from site config, not a hardcoded `cluster: "..."`.
3. **No hardcoded site paths** in committed files — read roots from config/env.
4. **`deploy/install.sh` is idempotent and no-sudo** for the env + frontend build.
5. **Reference DBs are staged, not committed** — a documented download/verify step
   keyed on a DBs root.
6. **Release with semantic-version git tags** (+ a GitHub Release) so the umbrella
   manifest can pin them and `check-updates` can see them.

## Manifest entry

Add the tool to the umbrella's [`tools.yml`](../tools.yml):

```yaml
  - name: <tool>
    repo: https://github.com/kapurlab/<tool>.git
    version: v1.0.0          # tag (preferred) or branch
    ood_apps: [<tool>]
    env: <conda-env-name>
    databases: [<db-key>]    # optional
```
