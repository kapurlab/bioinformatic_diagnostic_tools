# Troubleshooting — start from what you see

You do not need to know the cause. Find the exact text you saw in the index
below; every entry says what it means, gives **one command to run**, how to read
that command's output, and the fix. If nothing matches, run
`bin/bdtools diagnose <tool>` — it is read-only and writes one pasteable file to
send (see [Reporting a problem](#reporting-a-problem)).

All commands run from your `bioinformatic_diagnostic_tools` checkout.

## The repair order

This is the same order bare `bdtools` prints. Run these in order; stop when
it's fixed:

**1. `bdtools status` — every tool at its pin? (mismatch → `bdtools update <tool>`)**

```bash
bin/bdtools status
```

**2. `bdtools doctor` — what is wrong + the exact fix command per finding**

```bash
bin/bdtools doctor <tool>
```

**3. `bdtools fix --apply` — auto-runs the safe fixes (DB fetches, web layer, links); env-changing fixes are printed for you to run yourself**

```bash
bin/bdtools fix <tool> --apply
```

**4. `bdtools diagnose` — doctor is green but the tool still fails, or you need one file to send: the one-shot report**

```bash
bin/bdtools diagnose <tool>
```

**5. Run the command doctor/fix/diagnose printed for anything left.**

**6. `bdtools test` — prove it on a known sample**

```bash
bin/bdtools test <tool>
```

**What each lever cannot fix** — so you reach for the right one:

- `fix --apply` runs only the repairs that cannot break anything (database
  fetches, web layer, links). It never changes a conda env — env-changing fixes
  are printed for you to run deliberately.
- `update <tool>` moves a tool to the newest upstream release tag, rebuilds it,
  and bumps the `tools.yml` pin to that tag — the pin is the target only when
  upstream is unreachable or untagged. It is refused on report-only (frozen)
  tools and on server source trees.
- `install <tool> --rebuild` is additive: it adds newly-declared dependencies to
  the env that is already there. It cannot remove something that should not be
  there.
- `install <tool> --fresh` is the answer when the env is *wrong* rather than
  incomplete — mixed architectures, a swapped python, a half-linked transaction.
  The old env is set aside, not deleted, and put back if the build fails.
- `restore-env <tool> [--prev]` replays the exact pre-change package set, with
  no solve. It does not cover pip packages or the built frontend — finish with
  `install <tool>` if doctor still reports missing modules.

## Symptom index

| The first line you saw | Entry below |
|---|---|
| `incompatible architecture (have 'arm64', need 'x86_64')`, `Can't load ....bundle` | incompatible architecture |
| `Library not loaded` | Library not loaded |
| `cannot open shared object file` | cannot open shared object file |
| ``version `GLIBC_2.34' not found`` (any GLIBC/GLIBCXX/CXXABI version) | version GLIBC not found |
| `symbol lookup error`, `undefined symbol` | symbol lookup error |
| `Exec format error`, `Bad CPU type` | Exec format error |
| `bad interpreter`, `/usr/bin/env: 'perl\r': No such file or directory` | bad interpreter (CRLF) |
| `Permission denied` on a file that has `+x` | Permission denied on an executable |
| `No such file or directory` on a file that is plainly there | No such file or directory on a file that exists |
| `command not found: bdtools` | command not found: bdtools |
| tool exited 255 | tool exited 255 |
| GUI stuck at "Waiting for output" | GUI stuck at Waiting for output |
| Dashboard card says "Needs setup" but the tool runs fine | Needs setup while the tool works |
| `Unable to create prefix directory`, `Check that you have sufficient permissions` | Unable to create prefix directory |
| `post-link script failed for package`, `CONDA_BACKUP_AR: unbound variable` | post-link script failed |
| `No valid AMRFinder database is found`, `The BLAST database for AMRProt was not found` | No valid AMRFinder database |
| `does not contain necessary file taxo.k2d` (or `opts.k2d` / `hash.k2d`) | kraken2 database incomplete |
| `Cannot open temporary file ..._00253.bin`, `Could not determine genome size` | Cannot open temporary file (open-file limit) |
| The same update is offered again and again and never lands | An update is offered repeatedly |
| `git pull` aborts: "Your local changes to the following files would be overwritten by merge" | git pull refuses over a card edit |
| A shipped fix does not appear in a run, and every tool reports "up to date" | A shipped fix does not reach a run |
| A run names `/srv/...` or another site's directory that does not exist here | A run names another site's directory |
| Windows browser can't reach a tool running in WSL | Windows browser cannot reach a WSL tool |

On Windows/WSL, also read [WSL: the four rules](#wsl-the-four-rules).

---

### incompatible architecture (have 'arm64', need 'x86_64')

**What it means.** macOS is loading a compiled module into an interpreter of the
other architecture. Three distinct shapes produce this exact line: a
foreign-architecture file inside the env (an interrupted conda transaction —
the rollback restores the *records*, not the disk), an interpreter borrowed
from another conda env via PATH, or a universal (fat) binary whose running
slice is chosen by the launching process tree — which is why the same command
can work in a terminal and die under the dashboard, minutes apart.

**Run this:**

```bash
bin/bdtools diagnose <tool>
```

**Reading the output.** In the report: (1) the HOST section prints
`TRANSLATED: YES` (with the Rosetta explanation) when the reporting shell runs
translated, or `translated: no (this process runs natively)` otherwise — the
translated case is how a dashboard's process tree comes to prefer x86_64; (2) the per-slice interpreter probe names which slice of a fat
interpreter cannot load this env's modules; (3) the resolver section shows
whether two envs disagree about which one runs the tool; (4) the deep doctor
section lists any foreign-architecture files with the conda package that owns
them.

**The fix.** For the fat-slice case, get the launcher that pins every launch to
the env's platform, then restart so it is the one running:

```bash
git pull
```

```bash
bin/bdtools dashboard --restart
```

For foreign files or a borrowed interpreter, run the subdir-qualified
force-reinstall doctor prints, **verbatim**. A bare
`conda install --force-reinstall <pkg>` is a convincing false negative: the
foreign build already satisfies the spec, so conda re-links the same wrong
package from its cache and reports success. If several packages are split:

```bash
bin/bdtools install <tool> --fresh
```

### Library not loaded

**What it means.** macOS's loader (dyld) could not load a shared library a
program needs. In this suite that is almost always a mixed-architecture env or
a records-vs-disk split: conda-meta says one platform while the bytes on disk
are another, so record-level checks pass and only execution fails.

**Run this:**

```bash
bin/bdtools doctor <tool>
```

**Reading the output.** The finding names the library that failed to load, the
conda package that owns it, and prints the subdir-qualified reinstall for
exactly that package.

**The fix.** Run the printed command exactly as printed (the
`CONDA_SUBDIR=... "channel/subdir::pkg"` qualification is required — see the
entry above for why the bare form silently does nothing). If more than one
package is affected:

```bash
bin/bdtools install <tool> --fresh
```

### cannot open shared object file

**What it means.** The Linux loader could not find a shared library at exec
time. Usually the same class as the macOS entry above — a mixed env or an
interrupted transaction. One special case: `libcrypt.so.1` is expected from the
*system*, and modern distros (RHEL 9, Ubuntu 24.04) no longer ship it, so older
bioconda builds fail on a perfectly healthy env.

**Run this:**

```bash
bin/bdtools doctor <tool>
```

**Reading the output.** The finding names the missing library and, when a conda
package owns it, prints the qualified reinstall for that package.

**The fix.** Run the printed command. If the missing library is `libcrypt.so.1`
(no owner inside the env), put it inside the env — no root needed:

```bash
CONDA_SUBDIR=<env-subdir> conda install -y -p <env> -c conda-forge libxcrypt
```

(`<env-subdir>` is the env's platform, e.g. `linux-64`; doctor's other findings
print it, and `<env>` is usually `~/.local/share/bdtools/checkouts/<tool>/env`.)

### version GLIBC not found

Full text: ``/lib64/libc.so.6: version `GLIBC_2.34' not found (required by ...)``
— also `GLIBCXX_...` / `CXXABI_...` from `libstdc++.so.6`.

**What it means.** The program was built against a newer system glibc than this
host has — typical on older HPC distros (CentOS 7 / RHEL 8 era). The file is
present, its architecture matches, its records match; only execution shows it.

**Run this:**

```bash
getconf GNU_LIBC_VERSION
```

**Reading the output.** Compare the host version it prints against the version
named in the error. Host older than required = this entry.

**The fix.** Reinstall the owning package so the solver picks a build for this
host — conda-forge respects the `__glibc` virtual package:

```bash
CONDA_SUBDIR=<env-subdir> conda install -y -p <env> <owning-package>
```

If the binary is a vendored payload rather than a conda package (the kSNP4
class), there is no in-place fix — run this tool on a newer host or inside an
Apptainer/Singularity container.

### symbol lookup error

**What it means.** `LD_LIBRARY_PATH` (or `LD_PRELOAD`) is redirecting which
libraries every program loads — usually left behind by an HPC `module load`.
This is the Linux twin of the macOS slice redirect: the environment silently
decides what loads, so the tool works in a fresh shell and dies after
`module load`, or vice versa.

**Run this:**

```bash
echo "$LD_LIBRARY_PATH"
```

**Reading the output.** Any entry outside the tool's own env means the
environment is overriding the env's libraries. Empty output means this is not
your entry — go back to `bin/bdtools doctor <tool>`.

**The fix.**

```bash
module purge
```

```bash
unset LD_LIBRARY_PATH LD_PRELOAD
```

Then relaunch bdtools. Do not add conda envs to `LD_LIBRARY_PATH` — conda
binaries find their libraries by RPATH and need neither variable.

### Exec format error

**What it means.** The kernel refused to execute a binary built for another OS
or CPU — a Linux ELF binary on macOS, or an x86_64 binary on an aarch64 host.
"The file is on PATH" is not "this host can run it"; three name-resolution
checks can pass while exec fails in the first millisecond.

**Run this:**

```bash
bin/bdtools doctor <tool>
```

**Reading the output.** The binary-format finding reads the file's magic bytes
and names the file and its real format (e.g. "ELF on macOS").

**The fix.**

```bash
bin/bdtools install <tool> --fresh
```

This fetches the correct-OS payload. On Apple Silicon, the Mac x86_64 kSNP4
payload running under Rosetta 2 is by design, not a defect. If the payload has
no build for this CPU at all (kSNP4 ships x86_64 only), no reinstall can help —
run that tool on an x86_64 host.

### bad interpreter (CRLF)

Full texts: `bad interpreter: /bin/bash^M: No such file or directory` and
`/usr/bin/env: 'perl\r': No such file or directory`.

**What it means.** The script has Windows (CRLF) line endings, so the kernel is
looking for an interpreter literally named `perl\r` or `bash^M` — which does
not exist. The cause is almost always `git core.autocrlf=true` on WSL, or a
Windows editor touching a checkout.

**Run this** to confirm (look for `\r \n` at the end of the first line):

```bash
head -1 <file> | od -c | head -2
```

**The fix.** Repair the file:

```bash
perl -pi -e 's/\r$//' <file>
```

(`perl -pi`, not `sed -i`: BSD sed on macOS parses the script as `-i`'s backup
suffix and dies; perl behaves identically on macOS, Linux, and WSL.)

Then fix the cause so it does not come back:

```bash
git config --global core.autocrlf false
```

and re-clone (or `git checkout -- .`) the affected checkout. Never edit checkout
scripts with Windows editors.

### Permission denied on an executable

**What it means.** A file with `rwxr-xr-x` can still refuse to run for two
reasons the permission bits cannot show: the filesystem it lives on is mounted
`noexec` (common for HPC `/tmp`, scratch, and some home mounts), or SELinux is
enforcing and denies the exec.

**Run this:**

```bash
findmnt -no OPTIONS -T <env>/bin
```

**Reading the output.** `noexec` anywhere in the options is the answer: nothing
on that filesystem can ever be executed, whatever its permissions.

**The fix.** Move `BDTOOLS_HOME` to an exec-permitted filesystem and reinstall:

```bash
export BDTOOLS_HOME=/work/<group>/bdtools
```

```bash
bin/bdtools install <tool> --fresh
```

(Persist the export in `~/.bashrc`, and if `/tmp` is also noexec, export
`TMPDIR=$BDTOOLS_HOME/tmp` so conda/pip builds can run.) If the mount options
are clean, check SELinux:

```bash
getenforce
```

`Enforcing` means this is not user-fixable on a shared system — send your admin
the denied path and time so they can check `ausearch -m avc -ts recent`.

### No such file or directory on a file that exists

**What it means.** The error names the binary, but what is actually missing is
the binary's ELF interpreter — the glibc loader `/lib64/ld-linux-x86-64.so.2`.
Hosts using musl libc (Alpine, some minimal container images) do not have it,
and conda-forge/bioconda binaries require glibc. Do not chase the "missing"
file; it is the loader.

**Run this:**

```bash
ls /lib64/ld-linux-x86-64.so.2
```

**Reading the output.** Absent = this entry.

**The fix.** There is no in-place fix on a musl host. Deploy on a glibc distro
(Ubuntu/Debian/RHEL) or inside a glibc container.

### command not found: bdtools

**What it means.** `bdtools` is not installed onto your PATH — it runs from the
umbrella checkout as `bin/bdtools`. Every command in this suite's documentation
is written that way for the same reason.

**Run this** from the folder you cloned:

```bash
cd ~/bioinformatic_diagnostic_tools
```

```bash
bin/bdtools status
```

**The fix.** Always run it as `bin/bdtools` from the checkout. If you want a
bare `bdtools`, symlink it into any directory on your PATH — the script
resolves its own real location through symlinks:

```bash
ln -s "$PWD/bin/bdtools" ~/.local/bin/bdtools
```

### tool exited 255

**What it means.** 255 is the catch-all status of a process that died before it
could say anything more specific — usually an exec or loader failure at
startup, whose real message went to a log rather than the screen. Everything in
this index that starts with a loader error can end as a bare "exited 255" when
seen from the dashboard.

**Run this:**

```bash
bin/bdtools diagnose <tool>
```

**Reading the output.** The loader-smoke section launches every declared
program under the production arch pin and reports the actual loader error; the
running-backends section flags a stale process. The tool's build log at
`$BDTOOLS_HOME/state/build-logs/<tool>.log` holds the full text when the
failure was during a build.

**The fix.** Per the finding — typically the printed qualified reinstall, or:

```bash
bin/bdtools dashboard --restart
```

### GUI stuck at Waiting for output

**What it means.** The page is up but the backend never produced its stream:
the backend is stale (started before a fix landed), the tool process died at
launch, or — OOD deployments only — a proxy is buffering the event stream.

**Run this:**

```bash
bin/bdtools dashboard --restart
```

**Reading the output.** If the tool streams after the restart, the old backend
was the problem — updates never reach a running process. Still stuck: run
`bin/bdtools diagnose <tool>` and read the loader-smoke and backend sections.
On OOD, unbuffered `text/event-stream` is a proxy requirement — see the
troubleshooting table in [INSTALL_HPC_OOD.md](INSTALL_HPC_OOD.md).

### Needs setup while the tool works

**What it means.** The dashboard card is painted from a probe, not from the
tool itself. A dashboard running yesterday's code — or a probe older than the
current pins — reports a gap that no longer exists. The tool is fine; the
reporter is stale.

**Run this:** pull the latest code, then restart the dashboard so the current
probe is the one running:

```bash
git pull
```

```bash
bin/bdtools dashboard --restart
```

**Reading the output.** The card should now agree with doctor:

```bash
bin/bdtools doctor <tool>
```

If doctor also reports the gap, the card was right — follow doctor's fix.

### Unable to create prefix directory

**What it means.** `conda env create` could not make `<checkout>/env`. The
message blames permissions, and usually it is not permissions: `mkdir` refuses
that path for four different reasons and conda reports all four identically.

- **A dead `env` symlink** — the common one. When a tool runs from a *named*
  conda env, doctor's remedy is `ln -sfn <named env> <checkout>/env`; delete that
  conda env later and the link stays, pointing at nothing. `mkdir` fails with
  EEXIST, while every `[[ -d … ]]` test reads the broken link as absent.
  **Reinstalling does not clear it**: the checkouts live in
  `~/.local/share/bdtools`, not in this repo, so removing the repo *and* the
  conda env leaves it exactly where it was.
- **A file** at that path.
- **A checkout you cannot write to** — a build once run with `sudo` leaves
  root-owned directories behind.
- **A full disk.**

**Run this:**

```bash
bin/bdtools install <tool>
```

**Reading the output.** Current installs check the path before the solve, so the
answer is in the first lines: `removed a dead symlink at …/env (pointed at …,
which is gone)` and the build continues, or `found a FILE at …/env … moved to
env.partial-…`, or it stops with `cannot create a directory inside <checkout>`
followed by the owner, mode, ACL and free space — the three facts that decide
which of the last two it is. If you are on an older checkout, look yourself:

```bash
ls -lde ~/.local/share/bdtools/checkouts/<tool> ~/.local/share/bdtools/checkouts/<tool>/env; df -h ~/.local/share/bdtools
```

An `env ->` line whose target does not exist is the dead link; `dr-x` or an
owner that is not you is the permission case; `100%` capacity is the disk.

**The fix.** For a dead link, remove it and re-run the install — it holds no
data:

```bash
rm ~/.local/share/bdtools/checkouts/<tool>/env
```

For an unwritable checkout, whichever the listing showed:

```bash
chmod u+w ~/.local/share/bdtools/checkouts/<tool>
```

```bash
sudo chown -R "$(id -un)" ~/.local/share/bdtools/checkouts/<tool>
```

### post-link script failed

**What it means.** An upstream packaging bug, not a broken machine and not a
broken download. Three things line up:

1. conda **sources** a package's post-link script into its wrapper shell
   (`conda/core/link.py` passes `(".", path)`), so
2. bioconda's spades script opening with `set -eu -o pipefail` leaves **nounset
   on** for whatever the wrapper does next, and
3. the conda-forge toolchain hooks read `$CONDA_BACKUP_<TOOL>` with no default:

       deactivate_cctools_osx-64.sh: line 63: CONDA_BACKUP_AR: unbound variable
       LinkError: post-link script failed for package ...::spades-4.3.0...

conda then rolls the transaction back, which deletes the hooks — so the retry
starts from the same place and fails identically, and the env never gets built.

**Run this:**

```bash
bin/bdtools install <tool>
```

**Reading the output.** Current installs hand conda a value for every toolchain
variable before the transaction starts, which makes the activate hook *record* a
backup the deactivate hook can find. If you still see the line above, check that
the checkout is up to date (`git log --oneline -1`) and that the failing variable
is one the guard knows:

```bash
grep -A4 '^_CONDA_TOOL_VARS=' bin/install-local.sh
```

A name in the error that is **not** in that list is a new hook variant — add it
there; the placeholder is derived from the name.

**The fix.** Nothing to repair by hand: pull and re-run the install.

```bash
git pull && bin/bdtools install <tool> --fresh
```

Nothing about this is macOS-only in principle, but in practice it takes the
osx-64 toolchain packages: a Mac builds these envs as osx-64 under Rosetta, and
those are the hooks that ship the unguarded reads.

### No valid AMRFinder database

**What it means.** AMRFinderPlus has no database it can read, so the AMR step
exits 1 — *after* the pipeline has written `report.pdf` and the stats workbook,
which is why the run can look finished and contain no AMR calls at all. Two
distinct causes print nearly the same thing:

- **No database anywhere.** The bioconda `ncbi-amrfinderplus` package ships the
  program and **no data** (15 files, none of them a database), so a fresh env
  has nothing to search until the database is downloaded once.
- **A database this binary cannot read.** AMRFinderPlus 4.x renamed `AMRProt`
  to `AMRProt.fa` and bumped `database_format_version`. Hand a 4.x database to a
  3.x binary and it reports `The BLAST database for AMRProt was not found. Use
  amrfinder -u to download` — blaming a download that already succeeded.

**Run this:**

```bash
bin/bdtools doctor amr_plus_gui
```

**Reading the output.** The `AMRFinderPlus DB` line is answered by amrfinder
itself, so it says which of the two you have: `missing … no AMRFinderPlus
database there` (nothing installed) versus `is format 4.x but this amrfinder is
3.x` (a mismatch — nothing is corrupt and re-downloading will not help). A
passing line prints the database version in play, e.g.
`✓ AMRFinderPlus DB: …/data/latest (2026-05-15.1)`.

**The fix.** Nothing installed — download it once (about 200 MB; it is also
wired into the tool's Settings for you):

```bash
bin/bdtools setup-databases amrfinder
```

Format mismatch — move the program to the database's major version, which is
the side that keeps the data you already have:

```bash
bin/bdtools install amr_plus_gui --fresh
```

`--fresh`, not `--rebuild`: an env built before the sibling split still contains
mlst and kraken2, whose perl closure is what pinned `ncbi-amrfinderplus` to
3.12.8 in the first place, and `--rebuild` is additive so it cannot remove them.

### kraken2 database incomplete

**What it means.** kraken2 needs `taxo.k2d`, `opts.k2d` **and** `hash.k2d`, and
its wrapper aborts on the first one missing — so this line names whichever file
it checked first, not necessarily the only one absent. The usual causes are an
interrupted extraction and a path that points at a database on another machine
(a `/srv/...` value stored in a config, on a laptop). In the AMR pipeline this
is not fatal: organism detection is skipped, the run continues on MLST, and the
AMR calls that need `-O` are simply not made.

**Run this:**

```bash
bin/bdtools doctor amr_plus_gui
```

**Reading the output.** Both tools that read a Kraken2 database now name the
path they were given, so two configs pointing at different copies are visible:
`Kraken2 DB (organism detection)` is amr_plus_gui's own `kraken_db`, graded
separately from kraken_id_parse_gui's `Kraken2 DB`. A finding reads
`… — no taxo.k2d (kraken2 needs taxo.k2d, opts.k2d, hash.k2d)`.

**The fix.** Re-fetch (an incomplete directory is detected and replaced):

```bash
bin/bdtools setup-databases kraken
```

If the path belongs to another machine, clear it in the tool's Settings page
first — a stored value always beats the one this machine would have derived.

### Cannot open temporary file (open-file limit)

**What it means.** The process ran out of file descriptors. macOS gives a
GUI-launched process a soft limit of **256** open files, and the assembly stack
needs hundreds: KMC (inside shovill) opens one temp file per k-mer bin and dies
at `Cannot open temporary file …/kmc_00253.bin` — file 253 of 256, minus stdio.
shovill then reports `Could not determine genome size` and the pipeline falls
back to plain SPAdes, which logs its own ceiling in the same run (`Open file
limit set to 256`) and survives only because it needs 80. Nothing in either
message names a limit, which is why this reads as a shovill bug.

**Run this:** the launchers raise the limit themselves, so first make sure the
running dashboard is one that does:

```bash
git pull && bin/bdtools dashboard --restart
```

**Reading the output.** Every launch now records what the tool was given, at the
top of `~/.local/share/bdtools/dashboard-logs/<tool>.log` and in the console
line `files:  8192 open-file limit`:

```bash
grep -h "open files" ~/.local/share/bdtools/dashboard-logs/*.log | tail -3
```

`open files: 256` means the tool was launched by something older than this fix
(or by hand from a shell with the default limit). `8192` means the limit is not
your problem — read the surrounding error for the real cause, e.g. a full disk
or a `TMPDIR` that has gone away.

**The fix.** Restart through the launcher, as above. For a one-off run started
by hand, raise it in that shell first:

```bash
ulimit -Sn 8192
```

### An update is offered repeatedly

**What it means.** Not an error. A version that cannot be installed on *every*
platform the lab deploys is refused everywhere — one lab-wide older version
beats two machines disagreeing about what produced a result. The cautionary
case: mlst 2.34+ is noarch, and still can never install on macOS because it
depends on libxcrypt1, which has no macOS build. **noarch does not mean
portable.** Newer manifests declare such versions held, which stops the offer.

**Run this:**

```bash
git pull
```

**Reading the output.** After the pull, the dashboard's "Up to date" line
counts held packages and the tool's card carries the solver's reason on hover;
`bin/bdtools versions` prints the same. A **manifest** hold (`packages_held`
in tools.yml) is by name and suppresses every future release until the entry
is removed; only a machine-local "tried here and failed" hold is re-tried when
a release newer than the one that failed appears.

**The fix.** Usually nothing. To override deliberately for one tool:

```bash
bin/bdtools update-packages <tool> --to <pkg>=<version>
```

### git pull refuses over a card edit

Full text:

```
error: Your local changes to the following files would be overwritten by merge:
        ood/apps/bdtools_dashboard/form.yml
Please commit your changes or stash them before you merge.
Aborting
```

**What it means.** Not a corrupt checkout, and not a file that should have been
untracked. `ood/apps/**` holds the OOD cards, which must be in the repo — a
fresh clone with no card cannot be launched at all — and which a site is
expected to edit: the cluster name, the account, the CPU/memory/walltime floors
local policy requires. So the file is both shipped and locally owned, and the
moment a release touches it, `git pull` stops.

The tool checkouts never hit this, because `bdtools update` knows the rule
(`common.sh:tool_blocking_edits` exempts `ood/apps/*`, and the updater carries
those files across the tag checkout). The umbrella is pulled by hand, so nothing
was applying it here.

**Run this instead of `git pull`:**

```bash
bin/bdtools pull
```

**Reading the output.** It names the card files it is carrying, fast-forwards,
and puts them back:

```
==> carrying this site's card edits across the pull
  ood/apps/bdtools_dashboard/form.yml
==> git fetch
  ok fast-forwarded to origin/main
  ok card edits restored
```

Your values and the release's changes both survive — git merges them, so a site
changing `min:` and a release changing `help:` two lines away is not a conflict.
If the release changed the *same* lines, it says so, leaves the conflict in the
tree with your version still in the stash, and exits non-zero.

A dirty tracked file anywhere outside `ood/apps/**` is refused by name and
nothing is touched — that is suite code, and a pull silently overwriting it is
how a hand-patch disappears.

**Doing it by hand** is fine too; this is all the command does:

```bash
git stash push -- ood/apps/bdtools_dashboard/form.yml && git pull && git stash pop
```

### A shipped fix does not reach a run

**What it means.** The code that ran is not the version you think it is. Three
versions are in play per tool and only one of them decides behaviour:

* `pinned` — what `tools.yml` says the suite should be on
* `installed` — what `git describe` says about the checkout that actually runs
* `latest` — the newest release in the tool's repo

Only **installed** runs. A per-user install gives every user their own checkout
under `~/.local/share/bdtools/checkouts`, so an admin who updates their own
account changes nothing for anyone else — and this is the usual cause when a
release "was applied" and its behaviour never showed up.

`check-updates` used to compute its verdict from `pinned` vs `latest`, so a
checkout eleven releases behind its own pin printed `up to date`. It now grades
`installed` and reports that case as STALE.

**Run this — as the user whose runs are wrong, not as the admin:**

```bash
bin/bdtools check-updates
```

**Reading the output.**

```
irma_gui   pinned=v0.3.16   installed=v0.3.5   latest=v0.3.16   STALE — running v0.3.5, pinned v0.3.16
```

**The fix.**

```bash
bin/bdtools install irma_gui        # move the checkout to the pin and rebuild
```

### A run names another site's directory

Full text in a log: a path such as
`--genoflu-db /srv/kapurlab/databases/genoflu/dependencies` on a machine that has
no `/srv/kapurlab`, usually followed by a warning naming the same directory.

**What it means.** Each GUI keeps its settings in
`~/.config/<tool>/config.json`, created the first time it starts. Some older
releases seeded a database path with a literal from the server they were
developed on. Once that value is written to your config it stays: a later
release that removes the literal only changes what a **new** config gets, because
every tool's `load_config()` fills in defaults for keys that are *missing*, not
for keys that are already there. So the stale path outlives the tool update, the
pin bump and the environment rebuild that were supposed to cure it.

Depending on the tool, the run then either falls back to a bundled default and
logs a warning nobody can act on, or silently does nothing.

**Run this:**

```bash
bin/bdtools check-paths
```

**Reading the output.** It prints this machine's roots, then any configured
value that is an absolute path, does not exist here, has no part of its parent
tree here either, and is under none of those roots. All four have to hold, so a
projects directory you have not created yet is left alone, and a site that
really does own `/srv/<site>` keeps its settings even while a mount is away.

**The fix.**

```bash
bin/bdtools check-paths --apply
```

The key is emptied rather than deleted — every tool reads empty as "not
configured" and falls back to its own default — and the old value is kept under
`_bdtools_foreign_paths` in the same file. Launching a tool from the dashboard
does this automatically and records it in the run log, and `bdtools doctor`
reports what was removed.

### Windows browser cannot reach a WSL tool

Full text in the browser: `This site can't be reached` / `ERR_CONNECTION_REFUSED`
at `http://localhost:8080` (or a tool's port).

**What it means.** The backend is healthy inside WSL; the Windows-side
localhost port forwarding is what broke — typically after Windows sleep/resume
or a WSL update. The suite opens your browser through Windows, so this lands as
"the dashboard never opens" even though nothing on the Linux side failed.

**Run this** inside WSL to prove the Linux half:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
```

**Reading the output.** An HTTP status (e.g. `200` or `403`) means the backend
answers inside WSL and the fault is Windows-side forwarding. Connection refused
means the dashboard is not running — start it with `bin/bdtools dashboard`.

**The fix.** From **PowerShell** (not WSL):

```powershell
wsl --shutdown
```

Then reopen your WSL terminal and relaunch. Persistent fix on Windows 11: set
`networkingMode=mirrored` in `%UserProfile%\.wslconfig`. Bypass while broken:
browse from a WSL-side browser, or use the machine's LAN IP.

---

## How this class of failure works

**The file being present is not the file being able to run.** An install has
three layers: the *records* (what conda-meta says was installed), the *disk*
(the bytes actually there), and *execution* (what the loader does at exec
time). An interrupted conda transaction can leave the layers disagreeing —
conda's rollback restores the records, not the disk — so a check that reads
records, or even one that reads bytes, can pass while nothing runs. That is why
doctor now tests all three: it compares records to the env's platform, reads
the magic bytes of what is on disk, and actually executes each in-env
interpreter and loads a native module through it.

**The canonical example (macOS, August 2026).** An env's perl was a universal
binary (x86_64 + arm64) while its compiled modules were arm64-only. macOS picks
which slice of a fat binary runs from the launching process tree's inherited
preference — and the dashboard was running Rosetta-translated, so everything
below it preferred x86_64. The same kraken2 command worked from an arm64 shell
and died under the dashboard, minutes apart, while every file-level check —
existence, on-disk architecture, records, library roots, PATH order — read
healthy, because a fat binary matches every host and every env by construction.
The fix pins every launch to the env's recorded platform, with no host-arch
guard: `uname -m` reports x86_64 *inside* a translated process, false exactly
when the pin is needed. `bdtools diagnose` prints whether the current process
is Rosetta-translated — the one fact that, printed on day one, would have named
the cause.

**The Linux twins**, each the same shape — the environment silently redirects
what loads:

- **LD_LIBRARY_PATH / LD_PRELOAD** override which libraries every program
  loads, exactly as the slice preference does on macOS. `module load` is the
  usual source; conda binaries locate their libraries by RPATH and need neither.
- **glibc floors**: a binary encodes the minimum system glibc it was built
  against, and an older host refuses it at load time. File, architecture, and
  records are all correct — only execution shows it.
- **noexec**: a mount flag makes every file on that filesystem non-executable,
  whatever its permission bits say. `ls -l` looks perfect; only exec fails.

## WSL: the four rules

1. **Keep `BDTOOLS_HOME` inside the Linux filesystem — never under `/mnt/c`.**
   Windows drives are mounted into WSL through drvfs/9p: symlinks, hardlinks,
   and file locking are all degraded, and everything is many times slower. The
   default (`~/.local/share/bdtools`) is safe. Reach results from Windows via
   `\\wsl$\<distro>\home\<user>\...` instead of installing on `C:`.

2. **Set `git core.autocrlf=false`.** CRLF line endings in a checked-out script
   make the kernel look for an interpreter named `perl\r` (see the
   bad-interpreter entry above).

   ```bash
   git config --global core.autocrlf false
   ```

3. **Windows PATH shims can shadow tools.** WSL appends the Windows PATH by
   default, so `/mnt/c` copies of node, python, or conda can win over the Linux
   ones. Never install node or conda on the Windows side of a WSL workflow. To
   cut Windows PATH entries out entirely, add to `/etc/wsl.conf`:

   ```
   [interop]
   appendWindowsPath=false
   ```

   then from PowerShell run `wsl --shutdown` and reopen the terminal.

4. **WSL2, not WSL1.** WSL1's 4.4-era kernel emulation predates what current
   conda-forge builds assume; the suite is only supported on WSL2. Check which
   you have:

   ```bash
   grep -qi microsoft-standard /proc/sys/kernel/osrelease && echo WSL2 || echo WSL1
   ```

   Convert (data is preserved) from PowerShell:

   ```powershell
   wsl --set-version <distro> 2
   ```

## Reporting a problem

Run diagnose and send the file it names:

```bash
bin/bdtools diagnose <tool>
```

It is read-only — it changes nothing — and writes one plain-text report to
`$BDTOOLS_HOME/diagnose-<YYYYMMDD-HHMMSS>.txt` (default `BDTOOLS_HOME` is
`~/.local/share/bdtools`). That one file carries the host facts, how the tool
resolves, what is running, and the deep doctor findings — it is the fastest
path to a fix and the end of every incident this document is built from.

When the problem is a build or update rather than a launch, also include the
failing tool's build log:

```
$BDTOOLS_HOME/state/build-logs/<tool>.log
```
