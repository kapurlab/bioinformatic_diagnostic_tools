# Releasing (maintainers)

How versions flow through the suite, and how to publish GitHub Releases.

## Model

- Each tool repo carries **semantic-version git tags** (e.g. `v0.1.1`).
- The umbrella [`tools.yml`](../tools.yml) **pins** each tool to a tag — the
  single source of truth for "what's deployed."
- `bdtools check-updates` compares the pinned tag against the newest tag on each
  remote (via `git ls-remote`) — so **it works off plain tags and needs no
  GitHub Releases**. Releases are an optional nicety (a changelog page per
  version), not a dependency.

## Cut a new version of a tool

```bash
cd <tool>
git tag -a v0.2.0 -m "v0.2.0 — <what changed>"
git push origin v0.2.0
```

Then point the suite at it and verify:

```bash
cd bioinformatic_diagnostic_tools
bin/bdtools check-updates <tool>          # shows v0.1.1 -> v0.2.0 available
bin/bdtools update <tool>                 # bumps tools.yml pin + reinstalls
git add tools.yml && git commit -m "Bump <tool> to v0.2.0" && git push
```

## Publish GitHub Releases (one-time auth, then one command)

```bash
gh auth login                              # once per machine
bin/make-releases.sh --dry-run             # preview
bin/make-releases.sh                       # create a Release for every pinned tag
bin/make-releases.sh irma_gui mlst_gui     # or just specific tools
```

`make-releases.sh` skips tools already released and uses `--generate-notes` so
each Release gets an auto-built changelog. Current baseline tags: `vsnp_gui`
`v0.2.0`; all other tools `v0.1.1` (added the per-user OOD sandbox card).

## Tagging the whole suite

Tag the umbrella to pin the entire set for a reproducible site deployment:

```bash
cd bioinformatic_diagnostic_tools
git tag -a suite-2026.06 -m "Suite snapshot 2026.06"
git push origin suite-2026.06
```
