# __ADDON_NAME__ add-on for vsnp_gui

A pathogen add-on (contract 1). bdtools installs it beside vsnp_gui; a vsnp_gui
project that names pathogen `__ADDON_ID__` runs its hooks around vsnp3. The
contract, the hooks and the release checklist are in bdtools'
`docs/BUILDING_AN_ADDON.md`.

| File | What it is |
|---|---|
| `addon.json` | the manifest: id, hooks, actions, columns, QC thresholds, library markers |
| `conda_setup/environment.yml` | the add-on's own env (bdtools builds it at `env/`) |
| `deploy/build_library.sh` | builds the reference library under `<db root>/addons/__ADDON_ID__` |
| `hooks/choose_reference.py` | picks each sample's vsnp3 reference before alignment |
| `hooks/after_sample.py` | the sample's Results row and report, after vsnp3 |
| `hooks/after_run.py` | the run summary and report |
| `actions/` | on-demand jobs started from the Pathogen tab |
| `lib/addon_sdk.py` | the shared helpers (vendored; do not edit here) |
| `tests/contract/fixture/` | the project `bdtools addon-check` runs every hook on |
| `tests/known_answer/run.py` | optional: a simulated run with an answer key |

Check it:

```bash
bin/bdtools addon-check __ADDON_ID__            # manifest, library, every hook on the fixture
bin/bdtools addon-check __ADDON_ID__ --known-answer
```
