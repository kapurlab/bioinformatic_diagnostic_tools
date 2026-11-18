"""after_sample: this sample's Results row and report, after vsnp3 finished.

Reads vsnp3's outputs (never writes to them) from ctx.sample["step1"] and the
sample's QC row from ctx.sample["qc"]. Writes result.json (the metrics the
manifest's columns name, a pass/review/fail level with reasons, the files)
and the report.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import addon_sdk as sdk  # noqa: E402


def main():
    ctx = sdk.load_context()
    s = ctx.sample
    ref = Path(s.get("reference_dir") or "").name
    qc = (s.get("qc") or {}).get("row") or {}
    depth = qc.get("Average Depth")
    level, reasons = "pass", []
    if not (s.get("step1") or {}).get("zc_vcf"):
        level, reasons = "review", ["vsnp3 wrote no zero-coverage VCF"]
    report = ctx.out_dir / "report.html"
    report.write_text(sdk.html_page(f"{s.get('name')} report", f"<h1>{s.get('name')}</h1>" + sdk.table_html(
        ["Reference", "Depth", "Level"], [[ref, depth, level]])), encoding="utf-8")
    sdk.write_result(ctx, {"reference": ref, "mean_depth": depth}, level=level, reasons=reasons,
                     files=[sdk.file_entry(report, "report", "Sample report")])


if __name__ == "__main__":
    main()
