#!/usr/bin/env python3
"""kraken_top.py — reduce a kraken_id_parse_gui run to a small comparable JSON.

  kraken_top.py <run_outdir>

kraken_id_parse_gui writes a standard Kraken2 report (6 tab-separated columns:
%clade, clade_reads, taxon_reads, rank_code, taxid, name) under the run dir, but
not a single JSON headline. This finds that report and emits
<outdir>/kraken_top.json:

  { "top_species": "Escherichia coli",   # rank S with the most clade reads
    "top_species_pct": 96.8,             # its % of all reads
    "classified_pct": 99.1 }             # 100 - unclassified%

The report file name varies (kraken/ or kraken2/ subdir, *_reportkraken.txt,
*.kreport, ...), so we scan the run dir for any file that parses as a Kraken2
report rather than hard-coding a path.
"""
import json
import sys
from pathlib import Path


def _parse_kraken_report(path):
    """Return (rows, unclassified_pct) or (None, None) if not a kraken report."""
    rows = []
    unclassified_pct = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 6:
                    return None, None  # not a 6-col kraken report
                try:
                    pct = float(cols[0])
                    clade_reads = int(cols[1])
                except ValueError:
                    return None, None
                rank = cols[3].strip()
                name = cols[5].strip()
                rows.append({"pct": pct, "clade_reads": clade_reads,
                             "rank": rank, "name": name})
                if rank == "U":
                    unclassified_pct = pct
    except OSError:
        return None, None
    # must look like a kraken report: have rank codes and a root/unclassified
    if not rows or not any(r["rank"] in ("U", "R", "D", "S") for r in rows):
        return None, None
    return rows, unclassified_pct


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: kraken_top.py <run_outdir>")
    outdir = Path(sys.argv[1])

    best_rows, best_uncl, best_file = None, None, None
    for p in sorted(outdir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".txt", ".tsv", ".kreport", ".report"):
            continue
        rows, uncl = _parse_kraken_report(p)
        if rows and (best_rows is None or len(rows) > len(best_rows)):
            best_rows, best_uncl, best_file = rows, uncl, p

    if best_rows is None:
        sys.exit(f"kraken_top.py: no Kraken2 report found under {outdir}")

    def top_at(rank):
        rows = [r for r in best_rows if r["rank"] == rank]
        return max(rows, key=lambda r: r["clade_reads"]) if rows else None

    top_s = top_at("S")    # species (can fragment for near-identical complexes)
    top_g = top_at("G")    # genus (robust)
    result = {
        "report_file": str(best_file.relative_to(outdir)),
        "top_genus": top_g["name"] if top_g else None,
        "top_genus_pct": round(top_g["pct"], 1) if top_g else None,
        "top_species": top_s["name"] if top_s else None,
        "top_species_pct": round(top_s["pct"], 1) if top_s else None,
        "classified_pct": round(100.0 - best_uncl, 1) if best_uncl is not None else None,
    }
    (outdir / "kraken_top.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {outdir/'kraken_top.json'}: top_genus={result['top_genus']} "
          f"({result['top_genus_pct']}%), classified={result['classified_pct']}%")


if __name__ == "__main__":
    main()
