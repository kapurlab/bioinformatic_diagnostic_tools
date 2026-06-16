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

Prerequisite for every path: a working `conda`/`miniforge` (the installer points
you at one if missing) and `git`. The OOD paths additionally assume an existing
or freshly-bootstrapped Open OnDemand install.
