"""after_run: the run's summary and report, after the last sample."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import addon_sdk as sdk  # noqa: E402


def main():
    ctx = sdk.load_context()
    rows = ctx.get("samples") or []
    report = ctx.out_dir / "run_report.html"
    report.write_text(sdk.html_page("Run report", "<h1>Run report</h1>" + sdk.table_html(
        ["Sample", "Reference", "Level"],
        [[r.get("sample"), (r.get("metrics") or {}).get("reference"), (r.get("flags") or {}).get("level")]
         for r in rows])), encoding="utf-8")
    sdk.write_summary(ctx, [{"sample": r.get("sample"), "level": (r.get("flags") or {}).get("level")} for r in rows],
                      files=[sdk.file_entry(report, "report", "Run report")],
                      text=f"{len(rows)} sample(s)")


if __name__ == "__main__":
    main()
