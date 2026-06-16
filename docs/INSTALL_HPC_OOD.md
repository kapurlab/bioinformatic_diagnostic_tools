# Institutional OOD install (user sandbox app)

> **Status: DRAFT / pending the `install-sandbox` increment.** The mechanism is
> proven in `vsnp_gui/deploy/setup-sandbox.sh`; this doc + the umbrella
> `install-sandbox.sh` generalize it to any tool.

Use this when your university/institution **already runs Open OnDemand** and you
are a regular (non-admin) user. You install a tool as a personal **sandbox app**
under `~/ondemand/dev/` — no sysadmin involvement, no system changes.

What the install will do (per `setup-sandbox.sh`):

1. Ensure a `$HOME` conda (miniforge).
2. Build the tool's conda env + frontend under `$HOME`.
3. Write `~/.config/<tool>/sandbox.env` for the OOD launch script to source.
4. Symlink `ood/apps/<tool>` into `~/ondemand/dev/` so the card appears under
   **My Sandbox Apps** in the dashboard.

Intended command:

```bash
bin/bdtools install --sandbox <tool>
```

The tool then runs as a normal `batch_connect` session on the institution's
scheduler, under the institution's auth — you launch it from the dashboard like
any other interactive app. Reference databases that aren't bundled are pulled
into your space (or pointed at a shared copy) per the tool's docs.
