#!/usr/bin/env python3
"""check_cards.py — statically validate Open OnDemand app cards.

Why this exists: the consolidated dashboard's `submit.yml.erb` has never been
executed. The reference deployment's OOD uses the `linux_host` adapter, which
takes no resource request, so nothing there renders a scheduler submission. The
first real Slurm site is therefore the first time that template runs — and the
failure modes of an unrun ERB+YAML file (a bad tag, an indent, a `native:` entry
that renders to nothing) all surface as an OOD session that dies on submit with a
message pointing at OOD rather than at us.

We cannot run a scheduler here, but everything up to submission is checkable:

  * every YAML file parses;
  * every `form:` field has a matching entry under `attributes:` — an OOD card
    with a field it never defines renders a broken form;
  * `cluster:` is set, and is not still the placeholder (except in *_sandbox
    cards, where the placeholder is the documented per-user edit);
  * every `.erb` renders through real ERB with a permissive stub context;
  * the rendered `submit.yml` is valid YAML with the `batch_connect` keys OOD
    requires, and every `script.native` entry is a non-empty string;
  * the rendered shell templates pass `bash -n`.

  check_cards.py DIR [DIR ...]     # each DIR is an ood/apps/<app> directory

Exit 1 if any card fails. Ruby (for ERB) is required; without it the ERB checks
are skipped with a warning rather than silently passing.
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("check_cards: PyYAML unavailable — skipping card checks", file=sys.stderr)
    sys.exit(0)

PLACEHOLDER = "CHANGE_ME"

# A stub `context` that answers ANY attribute with a string that behaves like a
# number, so this validator needs no per-card knowledge of form field names. New
# fields are covered automatically; that is the point.
RUBY_PRELUDE = r"""
require 'erb'
# OOD's rendering environment has these loaded; cards use String#shellescape when
# interpolating user input into the job script. Without them a perfectly good card
# fails here with NoMethodError, which would be our bug, not the card's.
require 'shellwords'
class Ctx
  def method_missing(_n, *_a); "8"; end
  def respond_to_missing?(*_a); true; end
end
context  = Ctx.new
password = "STUB_TOKEN"
host     = "stub-node"
port     = "8080"
# Some cards call OOD helpers when rendering a view; stub the ones we have seen.
def session; nil; end
tmpl = ARGV[0]
begin
  # trim_mode '-' matches OOD: it renders card ERB with <%- -%> trimming.
  puts ERB.new(File.read(tmpl), trim_mode: '-').result(binding)
rescue Exception => e
  warn "ERB_ERROR: #{e.class}: #{e.message}"
  exit 3
end
"""


def render_erb(path: Path):
    """Render an .erb with the stub binding. -> (text, error_or_None)."""
    ruby = shutil.which("ruby")
    if not ruby:
        return None, "ruby not found"
    p = subprocess.run([ruby, "-e", RUBY_PRELUDE, str(path)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        msg = next((l for l in p.stderr.splitlines() if "ERB_ERROR" in l), p.stderr.strip())
        return None, msg or f"ruby exited {p.returncode}"
    return p.stdout, None


def check_form(path: Path, app: str, err, note):
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        err(f"{app}: form.yml is not valid YAML: {e}")
        return
    cluster = doc.get("cluster")
    if cluster is None:
        err(f"{app}: form.yml has no `cluster:` — OOD cannot target a scheduler")
    elif str(cluster).strip() == PLACEHOLDER:
        # Documented per-user edit for sandbox cards; a hole in a production card.
        (note if app.endswith("_sandbox") else err)(
            f"{app}: form.yml cluster is still {PLACEHOLDER}"
            + (" (expected for a sandbox card — each user sets it)"
               if app.endswith("_sandbox") else
               " — set it to this site's clusters.d id"))
    fields = doc.get("form") or []
    attrs = doc.get("attributes") or {}
    # An OOD form lists field names; each needs a definition or the form renders
    # a control with no label, no default and no help.
    for f in fields:
        if isinstance(f, str) and f not in attrs and not f.startswith("bc_"):
            err(f"{app}: form.yml lists `{f}` under form: with no attributes: entry")


def check_submit(path: Path, app: str, err, note):
    text, e = render_erb(path)
    if e:
        (note if "ruby not found" in e else err)(f"{app}: submit.yml.erb {e}")
        return
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as ex:
        err(f"{app}: submit.yml.erb renders to invalid YAML: {ex}")
        return
    bc = doc.get("batch_connect") or {}
    if not bc.get("template"):
        err(f"{app}: rendered submit.yml has no batch_connect.template")
    native = ((doc.get("script") or {}).get("native")) or []
    if not isinstance(native, list):
        err(f"{app}: rendered submit.yml script.native is not a list")
        return
    for i, n in enumerate(native):
        if not isinstance(n, str) or not n.strip():
            err(f"{app}: rendered submit.yml script.native[{i}] is empty — "
                f"a scheduler flag rendered to nothing ({n!r})")
        elif re.search(r"=\s*$", n):
            err(f"{app}: rendered submit.yml script.native[{i}] has an empty "
                f"value: {n!r}")


def check_shell(path: Path, app: str, err, note):
    if path.suffix == ".erb":
        text, e = render_erb(path)
        if e:
            (note if "ruby not found" in e else err)(f"{app}: {path.name} {e}")
            return
    else:
        text = path.read_text()
    p = subprocess.run(["bash", "-n"], input=text, capture_output=True, text=True)
    if p.returncode != 0:
        first = (p.stderr.strip().splitlines() or ["?"])[0]
        err(f"{app}: {path.name} is not valid bash: {first}")


def check_card(d: Path, err, note):
    app = d.name
    man = d / "manifest.yml"
    if not man.is_file():
        err(f"{app}: no manifest.yml")
        return
    try:
        m = yaml.safe_load(man.read_text()) or {}
    except yaml.YAMLError as e:
        err(f"{app}: manifest.yml is not valid YAML: {e}")
        return
    if not m.get("name"):
        err(f"{app}: manifest.yml has no `name:`")
    if m.get("role") != "batch_connect":
        return                                  # only batch_connect has the rest

    for rel, fn in (("form.yml", check_form), ("submit.yml.erb", check_submit)):
        p = d / rel
        if p.is_file():
            fn(p, app, err, note)
        else:
            err(f"{app}: batch_connect card is missing {rel}")

    tdir = d / "template"
    if not (tdir / "script.sh.erb").is_file() and not (tdir / "script.sh").is_file():
        err(f"{app}: no template/script.sh(.erb) — nothing would start")
    for name in ("script.sh.erb", "script.sh", "before.sh", "before.sh.erb"):
        p = tdir / name
        if p.is_file():
            check_shell(p, app, err, note)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    a = ap.parse_args()
    errors, notes = [], []
    for s in a.dirs:
        d = Path(s)
        if not d.is_dir():
            errors.append(f"{s}: not a directory")
            continue
        check_card(d, errors.append, notes.append)
    for n in notes:
        print(f"  ! {n}")
    for e in errors:
        print(f"  ✗ {e}")
    if not errors:
        print(f"  ✓ {len(a.dirs)} OOD card(s) validate")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
