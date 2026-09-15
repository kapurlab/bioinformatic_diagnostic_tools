# Install — pick your environment

This page routes you to the right runbook. All paths use the same CLI
(`bin/bdtools`) and the same manifest ([`tools.yml`](tools.yml)).

1. **Personal computer** (Linux, macOS, or Windows via WSL2) — you just want to
   run a tool on your own machine → [docs/INSTALL_LOCAL.md](docs/INSTALL_LOCAL.md)

2. **Your university / institution already runs Open OnDemand** and you are a
   regular user → [docs/INSTALL_HPC_OOD.md](docs/INSTALL_HPC_OOD.md) (sandbox app)

3. **You administer an Open OnDemand system** and want to publish these tools to
   all users → [docs/SYSADMIN.md](docs/SYSADMIN.md)

4. **You are standing up a new lab server from bare metal** (no OOD yet) →
   [docs/INSTALL_BARE_METAL.md](docs/INSTALL_BARE_METAL.md)

5. **Already installed, but a tool won't run** →
   [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — find your exact error
   message there. First command: `bin/bdtools doctor`; one-shot report to send:
   `bin/bdtools diagnose <tool>`.

Prerequisite for every path: a working `conda`/`miniforge` (the installer points
you at one if missing) and `git`. The OOD paths additionally assume an existing
or freshly-bootstrapped Open OnDemand install.

## Before you start on an Apple Silicon Mac (macOS 26 / 27 and newer)

Four of the nine tools run natively on arm64. The other five need Rosetta 2 —
four because a package they depend on has no arm64 build, and `ksnp_gui` because
its kSNP4 payload is downloaded from upstream as an Intel-only binary:

| Runs natively (no Rosetta) | Needs Rosetta 2 |
|---|---|
| `vsnp_gui`, `amr_plus_gui`, `kraken_id_parse_gui`, `genoflu_gui` | `irma_gui` (`blat`), `mlst_gui` (`libxcrypt1`, `perl`, `spades`), `ncbi_submit_gui` (`table2asn`), `mhc_gui` (`nanoq`), `ksnp_gui` (kSNP4 ships x86_64-only) |

**Rosetta is not present by default, and a major macOS upgrade can remove it.**
Verified on macOS 27.0 (2026-09-15): the upgrade left an install whose every
tool had been working unable to start a single binary. If you want the whole
suite, install it once — it takes about a minute:

```bash
softwareupdate --install-rosetta --agree-to-license
```

If you only need the native four, skip it. `bdtools install` no longer
stops when Rosetta is missing: it builds a native arm64 env instead, and a tool
whose dependencies have no arm64 build fails the solve and names the package.

Check your own machine before deciding — the answer moves as bioconda publishes
builds, so the table above is a snapshot, not a rule:

```bash
bin/bdtools rebuild-native --report
```

**Symptom to recognise.** If several tools go "needs setup" at once after a
macOS upgrade — especially with `bad CPU type in executable` — that is Rosetta,
not a broken install. `bin/bdtools doctor` names it directly; see
[every tool needs setup after a macOS upgrade](docs/TROUBLESHOOTING.md#every-tool-needs-setup-after-a-macos-upgrade).

Already installed under Rosetta and want to stop depending on it?
`bin/bdtools rebuild-native --apply` moves every env that can go native; it sets
the old env aside and puts it back if a build fails. Details in
[docs/INSTALL_LOCAL.md](docs/INSTALL_LOCAL.md).
