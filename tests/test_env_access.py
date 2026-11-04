#!/usr/bin/env python3
"""A shared env stays readable by everyone it is shared with.

Ames HPC, 2026-11-04: vSNP opened for tstuber and died for Reed.Woyda with
"libssl.so.3: cannot open shared object file: No such file or directory". The
2026-10-29 vsnp3 3.36 -> 3.37 update had run as tstuber through a package cache
in his home; conda copied each new file into the shared env on /project, and the
copy carried its NFSv4 ACL, so every file it installed granted his home group
("AAP VS NVSL Home Tstuber") instead of the cluster's users ("AAP VS NCAH HPC
Users"). The ACLs below are the ones nfs4_getfacl printed there.

What is pinned:
  * those files are found, named by what they grant instead and who owns them,
    and given byte for byte the ACL their same-kind siblings carry — never a
    drifted majority's, and nothing when no sibling of that kind is right;
  * vsnp3's reference list, installed by the same transaction, is repaired like
    any other file: every vSNP user must read it and, the site's decision of
    2026-11-04, be able to update it. env_config_save's .bdtools-kept link is
    left alone, and a symlink out of the tree to a place the env's readers
    cannot reach is named, not followed;
  * after a transaction only what it wrote is repaired, older drift is reported;
  * on a filesystem without ACLs the mode bits answer the same question, and an
    env only its owner can read is nobody else's business;
  * every conda transaction settles access (the env_config bracket), and an
    update that cannot leaves a FAILED line rather than "Done";
  * launch-check says FIX for the Ames env instead of OK.
"""
import errno
import importlib.util
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
REL = "dependencies/reference_options_paths.txt"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EA = load("bdtools_env_access", ROOT / "bin/lib/env_access.py")

A, D = EA.ALLOW, EA.DENY
G = EA.IDENTIFIER_GROUP
FD = EA.FILE_INHERIT | EA.DIRECTORY_INHERIT
# The masks nfs4_getfacl printed: rwadxtTnNcoy, rwadxtTnNcCoy, rxtncy, rwaxtTnNcy.
FULL, FULL_C, RX, OWNER_M = 0x1B01BF, 0x1F01BF, 0x1200A9, 0x1201BF
NCAH = "AAP VS NCAH HPC Users@usda.net"
HOME = "AAP VS NVSL Home Tstuber@usda.net"


def acl(group, flags=0):
    return EA.encode_acl([
        (A, G | flags, FULL, group), (A, G | flags, FULL_C, "0"), (A, G | flags, RX, "0"),
        (A, flags, OWNER_M, "OWNER@"), (A, G | flags, 0, "GROUP@"), (A, flags, 0, "EVERYONE@")])


GOOD_DIR, GOOD_FILE = acl(NCAH, FD), acl(NCAH)
BAD_DIR, BAD_FILE = acl(HOME, FD), acl(HOME)
OWNER_ONLY = EA.encode_acl([(A, 0, OWNER_M, "OWNER@"), (A, 0, 0, "EVERYONE@")])


class Xattrs(object):
    """An NFSv4 mount, as far as env_access can tell."""

    def __init__(self):
        self.acls, self.writes = {}, []

    def get(self, path, name, follow_symlinks=False):
        p = os.path.realpath(path) if follow_symlinks else os.path.abspath(path)
        if name != EA.XATTR or p not in self.acls:
            raise OSError(errno.ENODATA, "No data available", path)
        return self.acls[p]

    def set(self, path, name, value, follow_symlinks=False):
        self.writes.append(path)
        self.acls[os.path.abspath(path)] = value


class XdrAndReaders(unittest.TestCase):
    def test_round_trip(self):
        aces = EA.decode_acl(BAD_FILE)
        self.assertEqual(aces[0], (A, G, FULL, HOME))
        self.assertEqual(EA.encode_acl(aces), BAD_FILE)

    def test_who_reads_the_ames_files(self):
        need = EA.READ_DATA
        self.assertEqual(EA.acl_readers(EA.decode_acl(GOOD_FILE), need), {"group " + NCAH, "group 0"})
        self.assertEqual(EA.acl_readers(EA.decode_acl(BAD_FILE), need), {"group " + HOME, "group 0"})

    def test_order_decides_and_inherit_only_does_not_count(self):
        deny_first = [(D, G, EA.READ_DATA, NCAH), (A, G, FULL, NCAH)]
        self.assertEqual(EA.acl_readers(deny_first, EA.READ_DATA), set())
        later_deny = [(A, G, FULL, NCAH), (D, G, EA.READ_DATA, NCAH)]
        self.assertEqual(EA.acl_readers(later_deny, EA.READ_DATA), {"group " + NCAH})
        inherit_only = [(A, G | EA.FILE_INHERIT | EA.INHERIT_ONLY, FULL, NCAH)]
        self.assertEqual(EA.acl_readers(inherit_only, EA.READ_DATA), set())

    def test_a_directory_must_also_be_enterable(self):
        read_only = [(A, G, EA.READ_DATA, NCAH)]
        self.assertEqual(EA.acl_readers(read_only, EA.READ_DATA | EA.EXECUTE), set())

    def test_a_user_and_a_group_of_one_name_are_different_principals(self):
        self.assertEqual(EA.acl_readers([(A, 0, FULL, NCAH)], EA.READ_DATA), {"user " + NCAH})


class AmesEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = os.path.realpath(os.path.join(self.tmp.name, "vsnp3"))
        self.x = Xattrs()
        old = EA.getxattr, EA.setxattr
        EA.getxattr, EA.setxattr = self.x.get, self.x.set
        self.addCleanup(setattr, EA, "getxattr", old[0])
        self.addCleanup(setattr, EA, "setxattr", old[1])
        self.mk("", GOOD_DIR, d=True)
        for d, a in (("bin", GOOD_DIR), ("lib", GOOD_DIR), ("lib/python3.13", GOOD_DIR),
                     ("dependencies", GOOD_DIR), ("lib/openssl-new", BAD_DIR)):
            self.mk(d, a, d=True)
        # Built in August by the site admin: the cluster's users can read them.
        self.mk("bin/python3.13", GOOD_FILE, mode=0o700)
        self.mk("bin/samtools", GOOD_FILE, mode=0o700)
        self.mk("lib/python3.13/os.py", GOOD_FILE, mode=0o600)
        # Installed by the 3.37 update through a home package cache.
        self.mk("lib/libssl.so.3", BAD_FILE, mode=0o700)
        self.mk("bin/openssl", BAD_FILE, mode=0o700)
        self.mk("conda-meta-openssl.json", BAD_FILE, mode=0o600)
        # vsnp3's reference list came in with the same transaction.
        self.mk(REL, BAD_FILE, mode=0o600)
        self.mk("dependencies/.reference_options_paths.txt.bdtools-kept", BAD_FILE, mode=0o600)
        os.symlink("libssl.so.3", os.path.join(self.env, "lib/libssl.so"))
        # vsnp_gui's vSNP_reference_options: a link into one person's folder.
        self.personal = os.path.realpath(os.path.join(self.tmp.name, "home_refs"))
        os.makedirs(self.personal)
        self.x.acls[self.personal] = OWNER_ONLY
        os.symlink(self.personal, os.path.join(self.env, "vSNP_reference_options"))

    def mk(self, rel, raw, d=False, mode=0o700):
        p = os.path.join(self.env, rel) if rel else self.env
        if d:
            os.makedirs(p, exist_ok=True)
        else:
            Path(p).write_text("x")
            os.chmod(p, mode)
        self.x.acls[os.path.abspath(p)] = raw

    def acl_of(self, rel):
        return self.x.acls[os.path.join(self.env, rel)]

    def test_check_finds_what_the_update_installed_and_names_it(self):
        rep = EA.scan(self.env)
        self.assertEqual(rep.scheme, "nfs4")
        self.assertEqual(rep.expected, {"group " + NCAH, "group 0"})
        self.assertEqual(sorted(os.path.relpath(i["path"], self.env) for i in rep.drift),
                         ["bin/openssl", "conda-meta-openssl.json", REL,
                          "lib/libssl.so.3", "lib/openssl-new"])
        text = "\n".join(EA.check_lines(rep))
        self.assertIn("FIX", text)
        self.assertIn("not readable by group %s" % NCAH, text)
        self.assertIn("they grant group %s instead" % HOME, text)

    def test_fix_copies_each_kinds_good_acl(self):
        rep = EA.scan(self.env)
        fixed, left = EA.repair(rep)
        self.assertEqual((len(fixed), left), (5, []))
        self.assertEqual(self.acl_of("lib/libssl.so.3"), GOOD_FILE)        # executable kind
        self.assertEqual(self.acl_of("bin/openssl"), GOOD_FILE)
        self.assertEqual(self.acl_of("conda-meta-openssl.json"), GOOD_FILE)  # plain-file kind
        self.assertEqual(self.acl_of("lib/openssl-new"), GOOD_DIR)          # keeps inheritance
        self.assertEqual(EA.scan(self.env).drift, [])

    def test_the_reference_list_gets_its_siblings_access(self):
        # Reed's runs need to read it, and every vSNP user may update it.
        EA.repair(EA.scan(self.env))
        self.assertEqual(self.acl_of(REL), GOOD_FILE)
        self.assertEqual(EA.acl_readers(EA.decode_acl(self.acl_of(REL)), EA.READ_DATA),
                         {"group " + NCAH, "group 0"})

    def test_the_kept_link_and_symlinks_are_left_alone(self):
        EA.repair(EA.scan(self.env))
        self.assertEqual(self.acl_of("dependencies/.reference_options_paths.txt.bdtools-kept"), BAD_FILE)
        self.assertEqual(self.x.acls[self.personal], OWNER_ONLY)

    def test_a_link_to_a_place_they_cannot_reach_is_named(self):
        rep = EA.scan(self.env)
        self.assertEqual([os.path.relpath(n["path"], self.env) for n in rep.links],
                         ["vSNP_reference_options"])
        text = "\n".join(EA.check_lines(rep))
        self.assertIn("note: vSNP_reference_options is a link to %s" % self.personal, text)
        self.assertIn("not repaired from here", text)

    def test_dry_run_writes_nothing(self):
        rep = EA.scan(self.env)
        fixed, left = EA.repair(rep, dry_run=True)
        self.assertEqual((len(fixed), left), (5, []))
        self.assertEqual(self.x.writes, [])

    def test_a_drifted_majority_is_never_the_reference(self):
        for n in range(6):
            self.mk("bin/new%d" % n, BAD_FILE, mode=0o700)
        rep = EA.scan(self.env)
        EA.repair(rep)
        self.assertEqual(self.acl_of("bin/new0"), GOOD_FILE)

    def test_no_good_sibling_of_that_kind_means_no_guess(self):
        os.remove(os.path.join(self.env, "lib/python3.13/os.py"))
        rep = EA.scan(self.env)
        fixed, left = EA.repair(rep)
        self.assertEqual(sorted(os.path.relpath(i["path"], self.env) for i in left),
                         ["conda-meta-openssl.json", REL])
        self.assertIn("no other file", left[0]["why"])
        self.assertEqual(self.acl_of("conda-meta-openssl.json"), BAD_FILE)

    def test_settle_repairs_what_the_change_wrote_and_reports_the_rest(self):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = EA.cmd_settle(self.env, 0, "vsnp_gui", [])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("gave them the access the rest of the env has", out.getvalue())
        self.assertEqual(self.acl_of("lib/libssl.so.3"), GOOD_FILE)

    def test_settle_leaves_drift_older_than_the_change_to_the_admin(self):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = EA.cmd_settle(self.env, 4102444800, "vsnp_gui", [])   # since 2100
        self.assertEqual(rc, 0)
        self.assertEqual(self.x.writes, [])
        self.assertIn("older path(s)", err.getvalue())
        self.assertIn("bin/bdtools access vsnp_gui", err.getvalue())

    def test_settle_fails_loud_when_it_cannot_repair(self):
        def refuse(path, name, value, follow_symlinks=False):
            raise OSError(errno.EPERM, "Operation not permitted", path)
        EA.setxattr = refuse
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = EA.cmd_settle(self.env, 0, "vsnp_gui", [])
        self.assertEqual(rc, 1)
        self.assertIn("so the tool will fail for them", err.getvalue())
        self.assertIn("Operation not permitted", err.getvalue())
        self.assertIn("Repair: bin/bdtools access vsnp_gui --fix", err.getvalue())

    def test_launch_check_names_the_library(self):
        checkout = os.path.join(self.tmp.name, "vsnp_gui")
        os.makedirs(os.path.join(checkout, "backend", "app"))
        Path(checkout, "backend", "app", "main.py").write_text("")
        for p in (checkout, os.path.join(checkout, "backend"), os.path.join(checkout, "backend", "app")):
            self.x.acls[os.path.realpath(p)] = GOOD_DIR
        self.x.acls[os.path.realpath(os.path.join(checkout, "backend", "app", "main.py"))] = GOOD_FILE
        plan = {"tool": "vsnp_gui", "env_dir": self.env, "python": os.path.join(self.env, "bin/python3.13"),
                "dir": checkout, "cwd": os.path.join(checkout, "backend")}
        msg = EA.launch_problem(plan)
        self.assertIn("lib/libssl.so.3", msg)
        self.assertIn("granted to group %s instead; owner" % HOME, msg)
        self.assertIn("bin/bdtools access vsnp_gui --fix", msg)
        EA.repair(EA.scan(self.env))
        self.assertEqual(EA.launch_problem(plan), "")


class ModeBits(unittest.TestCase):
    """No ACLs (a Mac, local disk, NFSv3): the mode bits answer instead."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = EA.getxattr
        EA.getxattr = None
        self.addCleanup(setattr, EA, "getxattr", old)
        self.env = os.path.join(self.tmp.name, "env")
        os.makedirs(os.path.join(self.env, "lib", "sub"))
        os.chmod(self.env, 0o755)

    def f(self, rel, mode):
        p = os.path.join(self.env, rel)
        Path(p).write_text("x")
        os.chmod(p, mode)
        return p

    def mode(self, p):
        return stat.S_IMODE(os.stat(p).st_mode)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads anything")
    def test_owner_only_files_in_a_shared_env_are_opened_up(self):
        lib = self.f("lib/libssl.so.3", 0o700)
        data = self.f("lib/data.json", 0o600)
        ok = self.f("lib/ok.py", 0o644)
        sub = os.path.join(self.env, "lib", "sub")
        os.chmod(sub, 0o700)
        rep = EA.scan(self.env)
        self.assertEqual(len(rep.drift), 3)
        fixed, left = EA.repair(rep)
        self.assertEqual(left, [])
        self.assertEqual((self.mode(lib), self.mode(data), self.mode(ok), self.mode(sub)),
                         (0o755, 0o644, 0o644, 0o755))

    def test_an_env_only_its_owner_can_read_is_left_alone(self):
        os.chmod(self.env, 0o700)
        self.f("lib/libssl.so.3", 0o700)
        rep = EA.scan(self.env)
        self.assertEqual((rep.expected, rep.drift), (set(), []))
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            self.assertEqual(EA.cmd_settle(self.env, 0, "mlst_gui", []), 0)
        self.assertEqual(out.getvalue(), "")

    def test_a_healthy_env_settles_silently(self):
        self.f("lib/ok.py", 0o644)
        self.f("lib/tool", 0o755)
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(EA.cmd_settle(self.env, 0, "mlst_gui", []), 0)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))


@unittest.skipUnless(BASH, "bash is required")
class EveryTransactionSettles(unittest.TestCase):
    """The env_config bracket every conda call site already runs between."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = Path(self.tmp.name) / "env"
        (self.env / "bin").mkdir(parents=True)
        os.chmod(self.env, 0o755)
        py = self.env / "bin" / "python"
        py.write_text("#!/bin/sh\nexit 0\n")
        py.chmod(0o755)

    def sh(self, body, **env):
        prelude = f'set -euo pipefail\nsource "{ROOT}/bin/lib/common.sh"\n'
        e = {**os.environ, "BDTOOLS_HOME": str(Path(self.tmp.name) / "home"),
             "BDTOOLS_RUN_ID": "run-1", "DRY_RUN": "0", **env}
        return subprocess.run([BASH, "-c", prelude + body], capture_output=True, text=True, env=e)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads anything")
    def test_a_file_the_transaction_wrote_owner_only_is_opened_up(self):
        lib = self.env / "lib.so"
        out = self.sh(f'env_config_save mlst_gui "{self.env}"\n'
                      f'printf x > "{lib}"; chmod 600 "{lib}"\n'
                      f'env_config_restore mlst_gui "{self.env}"\n'
                      'echo "problem=[${ENV_ACCESS_PROBLEM}]"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(stat.S_IMODE(lib.stat().st_mode), 0o644)
        self.assertIn("gave them the access the rest of the env has", out.stdout)
        self.assertIn("problem=[]", out.stdout)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads anything")
    def test_the_reference_list_is_settled_like_everything_else(self):
        # vsnp_gui declares it under env_config; that keeps its CONTENTS across
        # a transaction, and no longer exempts it from being readable.
        (self.env / "dependencies").mkdir()
        ref = self.env / REL
        ref.write_text("/project/shared/refs/vsnp3\n")
        ref.chmod(0o600)
        out = self.sh(f'env_config_save vsnp_gui "{self.env}"\n'
                      f'env_config_restore vsnp_gui "{self.env}"\n')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(ref.read_text(), "/project/shared/refs/vsnp3\n")
        self.assertEqual(stat.S_IMODE(ref.stat().st_mode), 0o644)
        self.assertEqual(sorted(p.name for p in (self.env / "dependencies").iterdir()),
                         ["reference_options_paths.txt"])

    def test_dry_run_settles_nothing(self):
        lib = self.env / "lib.so"
        lib.write_text("x")
        lib.chmod(0o600)
        out = self.sh(f'env_config_save mlst_gui "{self.env}"\nenv_config_restore mlst_gui "{self.env}"\n',
                      DRY_RUN="1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(stat.S_IMODE(lib.stat().st_mode), 0o600)

    def test_a_settle_that_fails_is_reported_not_swallowed(self):
        fake = Path(self.tmp.name) / "py"
        fake.write_text("#!/bin/sh\nexit 1\n")
        fake.chmod(0o755)
        out = self.sh(f'env_config_save mlst_gui "{self.env}"\n'
                      f'env_config_restore mlst_gui "{self.env}"\n'
                      'echo "problem=[${ENV_ACCESS_PROBLEM}]"\n', BDTOOLS_PYTHON=str(fake))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("cannot be read by everyone the env is shared with", out.stdout)

    def test_package_updates_do_not_end_done_over_it(self):
        for script in ("bin/update-packages.sh", "bin/complete-env.sh"):
            s = (ROOT / script).read_text(encoding="utf-8")
            i = s.index('env_config_restore "${tool}" "${envdir}"')
            self.assertLess(s.index("ENV_ACCESS_PROBLEM", i), s.index("ok \"  installed", i), script)
            self.assertIn("bin/bdtools access ${tool} --fix", s, script)


if __name__ == "__main__":
    unittest.main()
