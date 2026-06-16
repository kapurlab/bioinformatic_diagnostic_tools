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

> **The one site-specific edit.** Every suite tool now ships a dedicated
> `*_sandbox` card, which the installer links automatically. Each card's
> `form.yml` has `cluster: "CHANGE_ME"` — set it to your HPC's cluster id (ask
> your OOD admin). The card sources `~/.config/<tool>/sandbox.env` (written by
> the installer), so your checkout + conda env can live anywhere in `$HOME`.
> The session runs on a Slurm compute node (cores/memory/partition are form
> fields). `vsnp_gui/ood/apps/vsnp_gui_sandbox` is the reference design.

Reference databases that aren't bundled are not auto-pulled — stage them into
your space (or point at a shared copy) per the tool's own docs.
