# Institutional OOD install (user sandbox app)

Use this when your university/institution **already runs Open OnDemand** and you
are a regular (non-admin) user. You install a tool as a personal **sandbox app**
under `~/ondemand/dev/` — no sysadmin involvement, no system changes.

```bash
git clone https://github.com/kapurlab/bioinformatic_diagnostic_tools.git
cd bioinformatic_diagnostic_tools
bin/bdtools install --sandbox <tool> --dry-run    # review first
bin/bdtools install --sandbox <tool>
```

What it does:

1. Ensure the tool is checked out (clones the pinned version if needed).
2. **If the tool ships its own `deploy/setup-sandbox.sh`** (e.g. vsnp_gui),
   delegate to it — it builds the tool's env, applies any patches, registers
   references, and links the tool's dedicated sandbox card. Fully working today.
3. **Otherwise** (generic path): build the conda env + frontend under your space
   (reusing the tool's build), write `~/.config/<tool>/sandbox.env`
   (`BDTOOLS_APP_DIR` + `BDTOOLS_APP_ENV`), and symlink an OOD card into
   `~/ondemand/dev/<tool>` so it shows up under **Develop → My Sandbox Apps**.

The tool then runs as a normal `batch_connect` session on the institution's
scheduler, under the institution's auth.

> **Per-tool caveat.** Only tools with a dedicated `*_sandbox` card (currently
> vsnp_gui) launch cleanly from a per-user checkout out of the box. For other
> tools the installer links the `*_dev`/prod card and warns you: that card needs
> to (a) source `~/.config/<tool>/sandbox.env` and (b) take the cluster as a form
> value rather than a hardcoded `cluster:`. See
> [BUILDING_A_TOOL.md](BUILDING_A_TOOL.md); `vsnp_gui/ood/apps/vsnp_gui_sandbox`
> is the reference to copy. Adding these cards is a small per-tool task.

Reference databases that aren't bundled are not auto-pulled — stage them into
your space (or point at a shared copy) per the tool's own docs.
