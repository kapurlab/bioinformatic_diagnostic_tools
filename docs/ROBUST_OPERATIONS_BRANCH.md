# Handoff — robust dashboard operations branch

Branch: `codex/robust-dashboard-ops`  
Date: 2026-07-23

This branch is intentionally **not deployed, tagged, pinned, or merged**. It is
for user testing before coordinated releases.

## What changed

- Browser and terminal restart/shutdown operations query every launched tool's
  `GET /api/jobs` endpoint. Any active/queued job—or any endpoint that cannot be
  verified—blocks the operation.
- Tool updates use the same guard, stop idle backends before changing their
  checkout/environment, and block relaunch while the update is running.
- Concurrent clicks share one per-tool startup task, preventing duplicate
  untracked Uvicorn processes.
- Backend termination is awaited and escalates to a targeted kill only after a
  grace period.
- Local mutation routes require a per-process custom-header token. Tool
  backends reject cross-site browser mutations and restrict development CORS to
  loopback origins.
- The private mode-0600 dashboard state file lets
  `bdtools dashboard --stop/--restart` use the guarded API instead of `pkill`.
- Existing server checkouts must match their exact manifest commit before a
  rebuild; personal updates refuse external/server source trees.
- vSNP now implements the suite-wide `GET /api/jobs` management contract.
- Non-vSNP command provenance resolves `--outdir` for direct CLI runs and records
  each subprocess's effective working directory. Its description now accurately
  says it captures commands launched directly by the orchestrator.
- The Bovine MHC tool now has a complete doctor dependency contract; optional
  AMRFinder/rclone integrations are reported as notes rather than core failures.
- The dashboard and all nine GUI headers offer polished Light, Dark, and System
  modes. The browser preference is persisted and shared across proxied tools,
  follows OS changes in System mode, and is applied before first paint.

## Testing tool feature worktrees

Exercise feature worktrees through the real dashboard without replacing an
installed checkout by setting `BDTOOLS_TOOLSDIR` to their common parent:

```bash
BDTOOLS_TOOLSDIR=/path/to/tool-worktrees bin/bdtools dashboard
```

The launcher uses the feature source while safely reusing the matching local
installation's Python environment, databases, and user configuration. Build
each worktree's `frontend/dist` first so its backend serves the feature bundle
rather than an older committed bundle.

## Coordinated branches

Every affected repository uses the same branch name:

```text
codex/robust-dashboard-ops
```

The tool branches were created from the currently pinned release tags in
isolated worktrees under `/tmp/bdtools-robust-worktrees/`. The live `/srv`
checkouts were not switched or edited. In particular:

- existing uncommitted Kraken backend work and handoff files remain untouched;
- the untracked vSNP `backend/quick2_renamed` file remains untouched;
- `/srv/kapurlab/tools/mhc_gui` remains the owner-managed plain deployment copy.

## Required release/deploy sequence after acceptance

1. Merge and tag each tool branch.
2. Update `tools.yml` to those new tags in the umbrella branch.
3. Run `bdtools lint`, `doctor`, and the golden validation suite.
4. Reconcile the `/srv` license branches with the tagged releases rather than
   force-checking out over local changes.
5. Deploy only when `GET /api/jobs` reports no active work, then restart the
   dashboard through its guarded control.
