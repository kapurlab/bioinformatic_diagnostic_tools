"""Action: one table of every sample's add-on results (TSV and HTML)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import addon_sdk as sdk  # noqa: E402


def main():
    ctx = sdk.load_context()
    rows = ctx.get("samples") or []
    keys = sorted({k for r in rows for k in (r.get("metrics") or {})})
    tsv = ctx.out_dir / "summary.tsv"
    tsv.write_text("\t".join(["sample", *keys]) + "\n" + "".join(
        "\t".join([str(r.get("sample"))] + [str((r.get("metrics") or {}).get(k, "")) for k in keys]) + "\n"
        for r in rows), encoding="utf-8")
    page = ctx.out_dir / "summary.html"
    page.write_text(sdk.html_page("Summary", sdk.table_html(["sample", *keys], [
        [r.get("sample")] + [(r.get("metrics") or {}).get(k) for k in keys] for r in rows])), encoding="utf-8")
    sdk.write_action(ctx, [sdk.file_entry(page, "report", "Summary"), sdk.file_entry(tsv, "text", "Summary (TSV)")],
                     summary=f"{len(rows)} sample(s), {len(keys)} column(s)")


if __name__ == "__main__":
    main()
