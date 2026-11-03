#!/usr/bin/env python3
"""storage.py — is there room for what an install is about to write?

A conda transaction that runs out of space does not fail cleanly. It dies
half-way through extracting or linking, leaves a prefix that is neither the
old env nor the new one, and reports the problem as a ClobberError, a
post-link failure or a bare "Killed" several minutes in — on a shared HPC
filesystem, in an env every user launches from. So every install asks first,
and stops before writing anything when the answer is no:

    NOT ENOUGH STORAGE — <what> was not started. Nothing was changed.
      needed / where / available / short by, one block per filesystem

Exit status 28 (ENOSPC) means "stopped for space"; the shell callers stop on it
and the dashboards recognise the block by its first line (STORAGE_MARKER).

Commands:
  storage.py env --tool T --env DIR --mode new|update [--solve-log F]
                 [--conda EXE] [--disk-gb N] [--what TEXT] [--rerun CMD]
                 [--report-only]
      a conda operation on a tool's env: the env and conda's package cache
  storage.py need --what TEXT --need BYTES:PATH:LABEL [--need ...]
                  [--rerun CMD] [--report-only]
      anything else with a known size (the reference databases)
  storage.py download-bytes LOG
      the download total a `conda/mamba install --dry-run` printed, in bytes

How much an env needs is an estimate, and the numbers behind it are measured:
  * a conda archive expands about 4.3x when conda extracts it into its package
    cache (a 2,814-package cache on this suite's build Mac, 2026-11-03: 7.3 GB
    of archives, 31.6 GB extracted) — EXPANSION rounds that up;
  * an env links its files from that cache, so on the SAME filesystem it costs
    almost nothing more, and on a different one it is a full copy;
  * a new env's size is the tool's `disk_gb:` in tools.yml (measured from built
    envs), else DEFAULT_ENV_GB;
  * a change to an existing env costs what its solve downloads; with no solve
    to read, half a new env.
Already-cached packages are not subtracted, so a rebuild of what is already in
the cache is over-estimated. That is the safe direction: a refusal names the
exact shortfall, and running out half-way costs a broken env.

"Available" is what statvfs reports to this user (f_bavail — `df`'s Avail
column). A per-user or per-project quota can be tighter than the filesystem,
and nothing portable reports one; the block says so when it stops.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ENOSPC_EXIT = 28
STORAGE_MARKER = "NOT ENOUGH STORAGE"
MiB = 1024 ** 2
GiB = 1024 ** 3
EXPANSION = 4.5
HEADROOM = 512 * MiB
DEFAULT_ENV_GB = 3.0
UNSOLVED_UPDATE_FRACTION = 0.5


def human(n):
    """Bytes as df -h would say them (base 1024), with the unit spelled out."""
    n = float(max(n, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            break
        n /= 1024
    if unit == "GB" and n >= 1024:
        return f"{n / 1024:.1f} TB"
    return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"


def nearest_existing(path):
    """The path itself, or the closest ancestor that exists — where a directory
    that is about to be created will land."""
    p = Path(os.path.abspath(os.path.expanduser(str(path))))
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def mount_point(path):
    """The mount point of the filesystem holding `path` (which must exist)."""
    p = Path(os.path.realpath(path))
    dev = p.stat().st_dev
    while p != p.parent:
        try:
            if p.parent.stat().st_dev != dev:
                break
        except OSError:
            break
        p = p.parent
    return str(p)


def filesystem(path):
    """(device id, mount point, bytes available to this user) for `path`."""
    where = nearest_existing(path)
    st = os.statvfs(where)
    return where.stat().st_dev, mount_point(where), st.f_bavail * st.f_frsize


def plan(needs):
    """Group (bytes, path, label) needs by filesystem.

    Returns a list of dicts: mount, avail, need, items [(label, path, bytes)],
    probe (the existing path statvfs was asked about) — one per filesystem, in
    the order the needs were given."""
    groups = {}
    order = []
    for nbytes, path, label in needs:
        dev, mount, avail = filesystem(path)
        if dev not in groups:
            groups[dev] = {"mount": mount, "avail": avail, "need": 0, "items": [],
                           "probe": str(nearest_existing(path))}
            order.append(dev)
        g = groups[dev]
        g["need"] += int(nbytes)
        g["items"].append((label, str(path), int(nbytes)))
    return [groups[d] for d in order]


def report(groups, what, rerun="", report_only=False, out=None):
    """Print the verdict. Returns True when every filesystem has room."""
    out = out or sys.stdout
    short = [g for g in groups if g["need"] > g["avail"]]
    if not short:
        for g in groups:
            print(f"  ok storage: {what} needs about {human(g['need'])} on {g['mount']}"
                  f" — {human(g['avail'])} available", file=out)
        return True
    err = sys.stderr if out is sys.stdout else out
    lines = [f"{STORAGE_MARKER} — {what} was not started. Nothing was changed.", ""]
    for g in short:
        lines += [
            f"  On the filesystem mounted at {g['mount']}:",
            f"      needed:     {human(g['need'])}",
            f"      available:  {human(g['avail'])}",
            f"      short by:   {human(g['need'] - g['avail'])}",
            "    needed for:",
        ]
        width = max(len(label) for label, _, _ in g["items"])
        for label, path, nbytes in g["items"]:
            # 0 = an env on the cache's filesystem: hard links, counted above.
            size = human(nbytes) if nbytes else "(links)"
            lines.append(f"      {label.ljust(width)}  {size:>9}  {path}")
        lines.append("")
    for g in groups:
        if g not in short:
            lines.append(f"  ({g['mount']} has room: {human(g['need'])} needed,"
                         f" {human(g['avail'])} available)")
    if len(short) < len(groups):
        lines.append("")
    biggest = max(short, key=lambda g: g["need"] - g["avail"])
    lines.append(f"  Free at least {human(biggest['need'] - biggest['avail'])} on"
                 f" {biggest['mount']}" + (" (and on each filesystem above)" if len(short) > 1 else "")
                 + ", then run it again" + (":" if rerun else "."))
    if rerun:
        lines.append(f"      {rerun}")
    lines.append("  Available is what df reports for this account; a disk quota can be lower"
                 " (check with: quota -s, or your site's quota command).")
    if report_only:
        lines.append("  [dry-run] a real run would stop here.")
    print("\n".join(lines), file=err)
    return False


# ---- conda -----------------------------------------------------------------

_TOTAL = re.compile(r"Total(?:\s+download)?\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)\b", re.I)
_UNIT = {"B": 1, "KB": 1024, "MB": MiB, "GB": GiB, "TB": GiB * 1024}


def download_bytes(text):
    """Bytes a dry-run solve said it would download; 0 when it downloads nothing;
    None when the output is not a plan this can read.

    conda: the "The following packages will be downloaded" table ends in
    `Total:  2.6 MB`. mamba: `Total download: 12MB`. Nothing to download (every
    package already cached, or nothing to change) prints no total at all."""
    totals = []
    for m in _TOTAL.finditer(text):
        unit = m.group(2).upper().replace("IB", "B")
        totals.append(float(m.group(1)) * _UNIT.get(unit, 1))
    if totals:
        return int(max(totals))
    if re.search(r"All requested packages already installed|Nothing to do|"
                 r"The following (NEW )?packages will be|Package Plan|Transaction", text):
        return 0
    return None


def pkgs_dir(conda=""):
    """The package cache a conda run as this user will write: the first entry of
    pkgs_dirs it can write to (conda's own rule). A shared base install that this
    account cannot write sends conda to ~/.conda/pkgs — usually a small home
    quota, and exactly the place an HPC install runs out first."""
    dirs = []
    if conda:
        try:
            proc = subprocess.run([conda, "info", "--json"], capture_output=True,
                                  text=True, timeout=120)
            info = json.loads(proc.stdout or "{}")
            dirs = list(info.get("pkgs_dirs") or [])
        except (OSError, ValueError, subprocess.SubprocessError):
            dirs = []
    if not dirs:
        env = os.environ.get("CONDA_PKGS_DIRS", "")
        dirs = [d for d in env.split(",") if d]
        if conda:
            dirs.append(str(Path(conda).resolve().parent.parent / "pkgs"))
        dirs.append(os.path.expanduser("~/.conda/pkgs"))
    for d in dirs:
        if os.access(nearest_existing(d), os.W_OK):
            return d
    return dirs[0] if dirs else os.path.expanduser("~/.conda/pkgs")


def env_needs(env, mode, cache, disk_gb=None, solve_text=None):
    """The (bytes, path, label) needs of one conda operation on `env`."""
    footprint = int(float(disk_gb if disk_gb not in (None, "") else DEFAULT_ENV_GB) * GiB)
    downloaded = download_bytes(solve_text) if solve_text is not None else None
    if mode == "new":
        to_cache = int(footprint * (1 + 1 / EXPANSION))   # archives + extracted
        to_env = footprint
    elif downloaded is not None:
        to_cache = int(downloaded * (1 + EXPANSION))
        to_env = int(downloaded * EXPANSION)
    else:
        to_cache = int(footprint * (1 + 1 / EXPANSION) * UNSOLVED_UPDATE_FRACTION)
        to_env = int(footprint * UNSOLVED_UPDATE_FRACTION)
    same_fs = filesystem(env)[0] == filesystem(cache)[0]
    needs = [(to_cache + HEADROOM, cache, "conda's package cache")]
    # Same filesystem: the env's files are hard links into the cache, so it
    # adds nothing the cache line has not already counted.
    needs.append((0 if same_fs else to_env + HEADROOM, env,
                  "the new conda env" if mode == "new" else "the tool's conda env"))
    return needs


def _parse_need(spec):
    nbytes, _, rest = spec.partition(":")
    path, _, label = rest.partition(":")
    if not nbytes.isdigit() or not path:
        raise argparse.ArgumentTypeError(f"--need wants BYTES:PATH[:LABEL], got {spec!r}")
    return int(nbytes), path, label or path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="storage.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("env")
    e.add_argument("--tool", required=True)
    e.add_argument("--env", required=True)
    e.add_argument("--mode", choices=("new", "update"), required=True)
    e.add_argument("--solve-log", default="")
    e.add_argument("--conda", default="")
    e.add_argument("--disk-gb", default="")
    e.add_argument("--what", default="")
    e.add_argument("--rerun", default="")
    e.add_argument("--report-only", action="store_true")
    n = sub.add_parser("need")
    n.add_argument("--what", required=True)
    n.add_argument("--need", action="append", type=_parse_need, required=True)
    n.add_argument("--rerun", default="")
    n.add_argument("--report-only", action="store_true")
    d = sub.add_parser("download-bytes")
    d.add_argument("log")
    a = ap.parse_args(argv)

    if a.cmd == "download-bytes":
        try:
            got = download_bytes(Path(a.log).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            got = None
        if got is None:
            return 1
        print(got)
        return 0

    if a.cmd == "env":
        solve_text = None
        if a.solve_log:
            try:
                solve_text = Path(a.solve_log).read_text(encoding="utf-8", errors="replace")
            except OSError:
                solve_text = None
        needs = env_needs(a.env, a.mode, pkgs_dir(a.conda), a.disk_gb, solve_text)
        what = a.what or (f"{a.tool}: building its conda env" if a.mode == "new"
                          else f"{a.tool}: changing its conda env")
    else:
        needs, what = a.need, a.what
    ok = report(plan(needs), what, a.rerun, a.report_only)
    if ok or a.report_only:
        return 0
    return ENOSPC_EXIT


if __name__ == "__main__":
    sys.exit(main())
