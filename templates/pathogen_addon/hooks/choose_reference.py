"""choose_reference: which vsnp3 reference this sample is aligned to.

Runs before vsnp3, once per sample. Writes reference.json naming a reference
folder inside the library. If no reference fits, fail with the reason: the
sample is then not aligned, and Step 1 Results shows why.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import addon_sdk as sdk  # noqa: E402


def candidates(ctx):
    root = ctx.library / "reference_options"
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / f"{d.name}.fasta").is_file())


def main():
    ctx = sdk.load_context()
    refs = candidates(ctx)
    scope = ctx.scope or "all"
    if scope.startswith("genome:"):
        refs = [r for r in refs if r.name == scope.split(":", 1)[1]]
    if not refs:
        sdk.fail(f"no reference in scope {scope!r}", out=ctx.out_dir, name="reference.json")
    # TODO(add-on author): choose by the reads (ctx.sample["reads"]), e.g. a
    # competitive minimap2 vote over a marker panel; record the evidence
    # (runner-up, identity, flags) as keyword details.
    chosen = refs[0]
    sdk.log(f"chose {chosen.name} (scope {scope})")
    sdk.write_reference(ctx, chosen, method="template: first reference in scope", scope=scope)


if __name__ == "__main__":
    main()
