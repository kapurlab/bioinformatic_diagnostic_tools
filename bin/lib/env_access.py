#!/usr/bin/env python3
"""env_access.py — a shared env stays readable by everyone it is shared with.

THE 2026-11-04 AMES REPORT. vSNP opened from the dashboard for tstuber and died
for Reed.Woyda, in the same kind of session, with

    ImportError: libssl.so.3: cannot open shared object file: No such file or directory

The library was there. The 2026-10-29 vsnp3 3.36 -> 3.37 package update had run
as tstuber, and every file it installed arrived granting tstuber's
home-directory group ("AAP VS NVSL Home Tstuber") where the rest of the env
grants the cluster's users ("AAP VS NCAH HPC Users"). That ACL can only have
come from his home: conda unpacked the packages into a cache there, and /home
and /project are separate NFS exports, so it could not hard-link them into the
shared env and copied them instead. Its copy (conda/gateways/disk/create.py,
shutil.copystat) carries extended attributes, and on NFSv4 the ACL is one. The
update worked for the account that ran it and for nobody outside that group, and
nothing could see it: the files exist, the mode bits read 0700 on good and bad
files alike, plain getfacl shows no entries, and the loader reports a library it
may not read as "No such file" because the last place it searched had none.

So who can read an env is now something bdtools checks, and puts back after it
changes one:

  readers   who may read a path. From the NFSv4 ACL (system.nfs4_acl) where the
            filesystem has one, otherwise from the mode bits (the owning group,
            everyone).
  expected  the readers of the env's (or checkout's) top directory, less its
            owner: who the site shares it with.
  drift     a path one of those cannot read (or, for a directory, enter),
            unless everyone can.
  repair    give the path the access its siblings have. On NFSv4, the exact ACL
            that the most same-kind paths (directory / executable / other file)
            carry among those every expected reader CAN use, copied byte for
            byte; never a majority that includes drifted paths, and when no
            sibling of that kind is right, nothing is guessed. On mode bits, the
            missing group/other bits.

The configuration files tools.yml lists under env_config get the same
treatment as everything else. vsnp3's reference list was on the Ames list too
(dependencies/reference_options_paths.txt, installed by the same transaction),
and every vSNP user has to read it — and, by the site's decision of 2026-11-04,
be able to update it. Skipped: the .<name>.bdtools-kept links env_config_save
leaves for the length of a transaction, .git, and symlinks, which are not
repaired; one that leads out of the tree to something the env's readers cannot
reach is named (vsnp_gui's vSNP_reference_options link into a personal folder).

Standard library only, python 3.6+ (the HPC login nodes). CLI:

  env_access.py check  DIR [--exclude REL ...]              exit 1 on drift
  env_access.py fix    DIR [--exclude REL ...] [--dry-run]   exit 1 if any is left
  env_access.py settle DIR --since EPOCH [--tool T] [--exclude REL ...]
        after a conda transaction: repair what changed since EPOCH, report any
        older drift with the command that repairs it. Exit 1 if what changed is
        left unreadable.
  env_access.py tools  [--fix] [--dry-run] [tool ...]
        each tool's env and checkout, as a dashboard session launches it
"""
import argparse
import errno
import glob
import os
import stat
import struct
import sys
import time
from collections import Counter

XATTR = "system.nfs4_acl"
ALLOW, DENY = 0, 1
FILE_INHERIT, DIRECTORY_INHERIT, INHERIT_ONLY = 0x1, 0x2, 0x8
IDENTIFIER_GROUP = 0x40
READ_DATA, EXECUTE = 0x1, 0x20
EVERYONE = "EVERYONE@"
_SPECIAL = ("OWNER@", "GROUP@", EVERYONE)
KIND_WORDS = {"dir": "directory", "exe": "executable file", "file": "file"}

# Module-level so a test can stand in for an NFSv4 mount (macOS has no
# os.getxattr at all, which is also how a Mac ends up on the mode-bit path).
getxattr = getattr(os, "getxattr", None)
setxattr = getattr(os, "setxattr", None)
_NO_ACL = {getattr(errno, n) for n in ("ENODATA", "ENOATTR", "ENOTSUP", "EOPNOTSUPP")
           if hasattr(errno, n)}


# ---- the ACL itself ---------------------------------------------------------

def decode_acl(raw):
    """[(type, flag, mask, who)] from the XDR an NFSv4 client hands back as
    system.nfs4_acl: a count, then per entry type, flag, mask and a
    length-prefixed principal padded to 4 bytes (RFC 7530 nfsace4)."""
    (count,) = struct.unpack_from(">I", raw, 0)
    off, aces = 4, []
    for _ in range(count):
        typ, flag, mask, n = struct.unpack_from(">IIII", raw, off)
        off += 16
        aces.append((typ, flag, mask, raw[off:off + n].decode("utf-8", "replace")))
        off += (n + 3) & ~3
    return aces


def encode_acl(aces):
    out = [struct.pack(">I", len(aces))]
    for typ, flag, mask, who in aces:
        w = who.encode("utf-8")
        out.append(struct.pack(">IIII", typ, flag, mask, len(w)) + w + b"\0" * (-len(w) % 4))
    return b"".join(out)


def _principal(flag, who):
    # A user and a group may share a name; the flag is what tells them apart.
    if who in _SPECIAL:
        return who
    return ("group " if flag & IDENTIFIER_GROUP else "user ") + who


def acl_readers(aces, need):
    """Principals this ACL lets do everything in `need` to the object itself.

    Evaluated the way NFSv4 does, in order, the first entry that speaks to a bit
    deciding it; EVERYONE@ entries speak for every principal. Inherit-only
    entries are for what will be created inside, not for this object. OWNER@ is
    left out: who owns each file is not what makes it shared."""
    live = [(t, _principal(f, w), m) for t, f, m, w in aces if not f & INHERIT_ONLY]
    out = set()
    for p in {k for t, k, _m in live if t == ALLOW}:
        if p == "OWNER@":
            continue
        allowed = denied = 0
        for t, k, m in live:
            if k != p and k != EVERYONE:
                continue
            if t == ALLOW:
                allowed |= m & ~denied
            elif t == DENY:
                denied |= m & ~allowed
        if allowed & need == need:
            out.add(p)
    return out


def mode_readers(st):
    """The same question asked of the mode bits."""
    m, is_dir = st.st_mode, stat.S_ISDIR(st.st_mode)
    out = set()
    if m & stat.S_IRGRP and (not is_dir or m & stat.S_IXGRP):
        out.add("group:%d" % st.st_gid)
    if m & stat.S_IROTH and (not is_dir or m & stat.S_IXOTH):
        out.add(EVERYONE)
    return out


def raw_acl(path, follow=False):
    if getxattr is None:
        return None
    try:
        return getxattr(path, XATTR, follow_symlinks=follow)
    except OSError as exc:
        if exc.errno in _NO_ACL:
            return None
        raise


def readers(path, st, follow=False):
    """(raw ACL or None, readers) for one path."""
    raw = raw_acl(path, follow)
    if raw is None:
        return None, mode_readers(st)
    need = READ_DATA | EXECUTE if stat.S_ISDIR(st.st_mode) else READ_DATA
    return raw, acl_readers(decode_acl(raw), need)


def kind_of(st):
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    return "exe" if st.st_mode & stat.S_IXUSR else "file"


def _missing(expected, got):
    return set() if EVERYONE in got else expected - got


# ---- one tree ---------------------------------------------------------------

class Report(object):
    def __init__(self, root):
        self.root = root
        self.scheme = "mode"         # or "nfs4"
        self.expected = set()
        self.checked = 0
        self.drift = []              # one dict per path
        self.good = {k: Counter() for k in KIND_WORDS}   # raw ACL -> how many carry it
        self.errors = []             # (path, why) the check could not read
        self.links = []              # symlinks out of the tree that the readers cannot follow
        self.seconds = 0.0


def _skip_set(root, exclude):
    return {os.path.normpath(os.path.join(root, rel)) for rel in exclude or ()}


def _is_kept_link(name):
    # env_config_save's hard link to the original, renamed back over the
    # package's copy when the transaction ends (common.sh). Same inode as the
    # file itself, which is checked under its own name.
    return name.startswith(".") and name.endswith(".bdtools-kept")


def _link_note(rep, p):
    """A symlink leading out of the tree to something the env's readers cannot
    reach, as a dict for rep.links; None when it is fine or stays inside (its
    target is then checked where it lives)."""
    target = os.path.realpath(p)
    real_root = os.path.realpath(rep.root)
    if target == real_root or target.startswith(real_root + os.sep):
        return None
    try:
        st = os.stat(p)
        _raw, got = readers(p, st, follow=True)
    except OSError as exc:
        return {"path": p, "target": target, "why": exc.strerror or str(exc)}
    missing = _missing(rep.expected, got)
    if missing:
        return {"path": p, "target": target, "why": "not readable by %s" % _names(missing)}
    return None


def scan(root, exclude=(), since=None, skip_names=(".git",)):
    """Every path under `root` that someone `root` is shared with cannot read.

    `since` (epoch seconds) marks each drifted path as recent or not by its
    ctime, which any create, rename, chmod or ACL write moves (a copied file's
    mtime is the package's, so mtime would not do)."""
    t0 = time.time()
    rep = Report(root)
    top_raw, top = readers(root, os.stat(root), follow=True)
    rep.scheme = "nfs4" if top_raw is not None else "mode"
    rep.expected = top
    if not rep.expected:
        # Readable by its owner alone: a personal env, nobody else to serve.
        rep.seconds = time.time() - t0
        return rep
    skip = _skip_set(root, exclude)

    def onerror(exc):
        rep.errors.append((getattr(exc, "filename", None) or "?", exc.strerror or str(exc)))

    for d, dirs, files in os.walk(root, onerror=onerror):
        dirs[:] = [n for n in dirs if n not in skip_names and os.path.join(d, n) not in skip]
        for name in dirs + files:
            p = os.path.join(d, name)
            if p in skip or _is_kept_link(name):
                continue
            try:
                st = os.lstat(p)
                if stat.S_ISLNK(st.st_mode):
                    note = _link_note(rep, p)
                    if note:
                        rep.links.append(note)
                    continue
                raw, got = readers(p, st)
            except OSError as exc:
                rep.errors.append((p, exc.strerror or str(exc)))
                continue
            if (raw is not None) != (rep.scheme == "nfs4"):
                continue     # a different kind of filesystem mounted inside: not comparable
            rep.checked += 1
            k = kind_of(st)
            missing = _missing(rep.expected, got)
            if missing:
                rep.drift.append({"path": p, "kind": k, "readers": got, "missing": missing,
                                  "uid": st.st_uid,
                                  "recent": since is None or st.st_ctime >= since})
            elif raw is not None:
                rep.good[k][raw] += 1
    rep.seconds = time.time() - t0
    return rep


def _repair_one(rep, item, dry_run):
    p, k = item["path"], item["kind"]
    if rep.scheme == "nfs4":
        common = rep.good[k].most_common(1)
        if not common:
            return False, ("no other %s in %s is readable by them to copy access from"
                           % (KIND_WORDS[k], rep.root))
        if dry_run:
            return True, "would copy the ACL %d other %s(s) carry" % (common[0][1], KIND_WORDS[k])
        if setxattr is None:
            return False, "this python cannot write ACLs"
        try:
            setxattr(p, XATTR, common[0][0], follow_symlinks=False)
        except OSError as exc:
            return False, exc.strerror or str(exc)
    else:
        st = os.lstat(p)
        x = k in ("dir", "exe")
        add, gid = 0, None
        for who in item["missing"]:
            if who == EVERYONE:
                add |= stat.S_IROTH | (stat.S_IXOTH if x else 0)
            elif who.startswith("group:"):
                want = int(who.split(":", 1)[1])
                if st.st_gid != want:
                    gid = want
                add |= stat.S_IRGRP | (stat.S_IXGRP if x else 0)
        if dry_run:
            return True, "would add the missing group/other read bits"
        try:
            if gid is not None:
                os.chown(p, -1, gid)
            os.chmod(p, stat.S_IMODE(st.st_mode) | add)
        except OSError as exc:
            return False, exc.strerror or str(exc)
    try:
        _raw, got = readers(p, os.lstat(p))
    except OSError as exc:
        return False, exc.strerror or str(exc)
    left = _missing(rep.expected, got)
    if left:
        return False, "still not readable by %s after the change" % _names(left)
    return True, ""


def repair(rep, dry_run=False, only_recent=False):
    """(fixed, left) — the drifted paths given their siblings' access, and the rest."""
    fixed, left = [], []
    for item in rep.drift:
        if only_recent and not item["recent"]:
            continue
        ok, why = _repair_one(rep, item, dry_run)
        item["why"] = why
        (fixed if ok else left).append(item)
    return fixed, left


# ---- saying it --------------------------------------------------------------

def _display(key):
    if key == EVERYONE:
        return "everyone"
    if key.startswith("group:"):
        gid = int(key.split(":", 1)[1])
        try:
            import grp
            return "group %s" % grp.getgrgid(gid).gr_name
        except (ImportError, KeyError):
            return "group %d" % gid
    return key


def _names(keys):
    return ", ".join(sorted(_display(k) for k in keys)) or "no one else"


def _owner(uid):
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return str(uid)


def _rel(rep, p):
    return os.path.relpath(p, rep.root)


def describe_drift(rep, items, limit=4, indent="    "):
    """Lines grouping drifted paths by what they grant instead, and who owns them."""
    groups = {}
    for i in items:
        key = (frozenset(i["missing"]), frozenset(i["readers"] - rep.expected), i["uid"])
        groups.setdefault(key, []).append(i)
    lines = []
    for (missing, extra, uid), its in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if extra:
            what = "they grant %s instead (owner %s)" % (_names(extra), _owner(uid))
        else:
            what = "readable by their owner (%s) only" % _owner(uid)
        lines.append("%s%s path(s) not readable by %s — %s, e.g."
                     % (indent, format(len(its), ","), _names(missing), what))
        for i in its[:limit]:
            lines.append("%s    %s" % (indent, _rel(rep, i["path"])))
    return lines


def _error_lines(rep, indent="    "):
    out = []
    for n in rep.links:
        out.append("%snote: %s is a link to %s (%s) — not repaired from here"
                   % (indent, _rel(rep, n["path"]), n["target"], n["why"]))
    if rep.errors:
        p, why = rep.errors[0]
        out.append("%sthis account could not look at %s path(s), so the check is incomplete (first: %s: %s)"
                   % (indent, format(len(rep.errors), ","), p, why))
    return out


def _headline(rep):
    if not rep.expected:
        return "OK", "%s: readable by its owner only — not shared, nothing to compare" % rep.root
    if not rep.drift:
        return "OK", ("%s: all %s paths readable by %s (%.0fs)"
                      % (rep.root, format(rep.checked, ","), _names(rep.expected), rep.seconds))
    return "FIX", ("%s: %s of %s paths cannot be read by everyone it is shared with (%s)"
                   % (rep.root, format(len(rep.drift), ","), format(rep.checked, ","),
                      _names(rep.expected)))


def check_lines(rep, label=""):
    """OK/FIX headline in launch-check's layout, then what drifted."""
    status, text = _headline(rep)
    head = "%-5s%s%s" % (status, label + " " if label else "", text)
    return [head] + (describe_drift(rep, rep.drift) if rep.drift else []) + _error_lines(rep)


def fix_lines(rep, fixed, left, dry_run):
    verb = "would give" if dry_run else "gave"
    out = []
    if fixed:
        out.append("  %s %s path(s) the access the rest of %s has"
                   % (verb, format(len(fixed), ","), rep.root))
    if left:
        out.append("  could NOT repair %s path(s):" % format(len(left), ","))
        whys = Counter(i["why"] for i in left)
        for why, n in whys.most_common():
            ex = next(i for i in left if i["why"] == why)
            out.append("    %s × %s (e.g. %s)" % (format(n, ","), why, _rel(rep, ex["path"])))
        owners = sorted({_owner(i["uid"]) for i in left})
        out.append("    The files' owner (%s) or an administrator can run the same command." % ", ".join(owners))
    return out


# ---- the commands -----------------------------------------------------------

def cmd_check(dirs, exclude):
    bad = 0
    for d in dirs:
        rep = scan(d, exclude)
        print("\n".join(check_lines(rep)))
        bad += bool(rep.drift)
    return 1 if bad else 0


def cmd_fix(dirs, exclude, dry_run):
    bad = 0
    for d in dirs:
        rep = scan(d, exclude)
        print("\n".join(check_lines(rep)))
        if rep.drift:
            fixed, left = repair(rep, dry_run=dry_run)
            print("\n".join(fix_lines(rep, fixed, left, dry_run)))
            bad += bool(left)
    return 1 if bad else 0


def cmd_settle(root, since, tool, exclude):
    """After a conda transaction on `root`: what it wrote must be readable by
    everyone the env is shared with. Silent when it is."""
    if not os.path.isdir(root):
        return 0
    rep = scan(root, exclude, since=since)
    if not rep.drift:
        return 0
    fixit = "bin/bdtools access %s --fix" % tool if tool else "%s fix %s" % (sys.argv[0], root)
    recent = [i for i in rep.drift if i["recent"]]
    older = [i for i in rep.drift if not i["recent"]]
    rc = 0
    if recent:
        fixed, left = repair(rep, only_recent=True)
        if fixed:
            print("  ok this change wrote %s path(s) into %s that %s could not read — "
                  "gave them the access the rest of the env has"
                  % (format(len(fixed), ","), root, _names(rep.expected)))
            for line in describe_drift(rep, fixed, limit=2):
                print(line)
        if left:
            rc = 1
            sys.stderr.write("  !! %s path(s) this change wrote into %s cannot be read by %s, "
                             "so the tool will fail for them:\n"
                             % (format(len(left), ","), root, _names(rep.expected)))
            sys.stderr.write("\n".join(describe_drift(rep, left)) + "\n")
            sys.stderr.write("\n".join(fix_lines(rep, [], left, False)[1:]) + "\n")
            sys.stderr.write("     Repair: %s\n" % fixit)
    if older:
        sys.stderr.write("  !! %s older path(s) in %s cannot be read by %s either (not from this change). "
                         "See: %s\n" % (format(len(older), ","), root, _names(rep.expected),
                                        fixit.replace(" --fix", "")))
    return rc


def _tool_trees(names):
    """(tool, label, dir, exclude, skip_names) for each tool's env and checkout,
    resolved the way the dashboard launches it."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import tool_launch  # noqa: E402  (sibling module, stdlib-only)
    import manifest  # noqa: E402
    _, entries = manifest.parse(tool_launch._MANIFEST)
    conf = {t.get("name"): t for t in entries if t.get("name")}
    # The cheap lookup resolve() already has for its sibling scan: env and
    # checkout only, without sweeping anyone's config.
    tool_launch._SCANNING_SIBLINGS = True
    out = []
    for name in names or list(conf):
        try:
            plan = tool_launch.resolve(name, 0)
        except Exception as exc:  # not installed, or a site env that is missing
            out.append((name, "", "", (), (), (str(exc).splitlines() or [type(exc).__name__])[0]))
            continue
        env = plan.get("env_dir") or ""
        checkout = plan.get("dir") or ""
        if os.path.isdir(env):
            out.append((name, "env", env, (), (".git",), ""))
        if os.path.isdir(checkout):
            inside = os.path.relpath(env, checkout) if env.startswith(checkout + os.sep) else ""
            out.append((name, "checkout", checkout, (inside,) if inside else (),
                        (".git", "node_modules"), ""))
    return out


def cmd_tools(names, fix, dry_run):
    bad = 0
    for name, label, d, exclude, skip_names, problem in _tool_trees(names):
        if problem:
            print("--   %s: %s" % (name, problem))
            continue
        rep = scan(d, exclude, skip_names=skip_names)
        print("\n".join(check_lines(rep, "%s %s" % (name, label))))
        if rep.drift and (fix or dry_run):
            fixed, left = repair(rep, dry_run=dry_run)
            print("\n".join(fix_lines(rep, fixed, left, dry_run)))
            bad += bool(left)
        elif rep.drift:
            bad += 1
            print("     Repair: bin/bdtools access %s --fix   (preview: --dry-run)" % name)
    return 1 if bad else 0


# ---- what launch-check asks -------------------------------------------------

_LAUNCH_GLOBS = ("lib/libpython3*", "lib/libssl*", "lib/libcrypto*",
                 "lib/python3*/lib-dynload/_ssl*",
                 "lib/python3*/site-packages/uvicorn/__init__.py",
                 "lib/python3*/site-packages/fastapi/__init__.py")


def launch_problem(plan):
    """One sentence when a file a launch loads is unreadable by someone the env
    is shared with, else "". A handful of files, not the tree: launch-check is
    meant to answer in seconds; `bdtools access` walks everything."""
    env, checkout = plan.get("env_dir") or "", plan.get("dir") or ""
    want = []
    if os.path.isdir(env):
        files = {os.path.realpath(plan.get("python") or "")}
        for pat in _LAUNCH_GLOBS:
            files.update(os.path.realpath(p) for p in glob.glob(os.path.join(env, pat)))
        want.append((env, sorted(f for f in files if f.startswith(os.path.realpath(env) + os.sep))))
    main = os.path.join(plan.get("cwd") or "", "app", "main.py")
    if os.path.isdir(checkout) and os.path.isfile(main):
        want.append((checkout, [os.path.realpath(main)]))
    for root, paths in want:
        rep = Report(root)
        try:
            _raw, rep.expected = readers(root, os.stat(root), follow=True)
        except OSError:
            continue
        if not rep.expected:
            continue
        for p in paths:
            try:
                st = os.stat(p)
                _raw, got = readers(p, st)
            except OSError:
                continue
            missing = _missing(rep.expected, got)
            if missing:
                rep.drift.append({"path": p, "kind": kind_of(st), "readers": got,
                                  "missing": missing, "uid": st.st_uid, "recent": True})
        if rep.drift:
            first = rep.drift[0]
            return ("%s is shared with %s, but they cannot read %s (granted to %s instead; owner %s), "
                    "so it will not start for them. Fix: bin/bdtools access %s --fix "
                    "(as the files' owner or an administrator)"
                    % (root, _names(rep.expected),
                       ", ".join(_rel(rep, i["path"]) for i in rep.drift[:3])
                       + (" and %d more" % (len(rep.drift) - 3) if len(rep.drift) > 3 else ""),
                       _names(first["readers"] - rep.expected), _owner(first["uid"]),
                       plan.get("tool", "<tool>")))
    return ""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="env_access.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    for name in ("check", "fix"):
        p = sub.add_parser(name)
        p.add_argument("dirs", nargs="+")
        p.add_argument("--exclude", action="append", default=[])
        if name == "fix":
            p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("settle")
    p.add_argument("dir")
    p.add_argument("--since", type=float, required=True)
    p.add_argument("--tool", default="")
    p.add_argument("--exclude", action="append", default=[])
    p = sub.add_parser("tools")
    p.add_argument("names", nargs="*")
    p.add_argument("--fix", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        return cmd_check(a.dirs, a.exclude)
    if a.cmd == "fix":
        return cmd_fix(a.dirs, a.exclude, a.dry_run)
    if a.cmd == "settle":
        return cmd_settle(a.dir, a.since, a.tool, a.exclude)
    if a.cmd == "tools":
        return cmd_tools(a.names, a.fix, a.dry_run)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
