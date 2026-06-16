#!/usr/bin/env python3
"""amr_summarize.py — reduce an amr_plus_gui run to a small comparable JSON.

  amr_summarize.py <run_outdir>

amr_plus_gui writes its AMR calls to <outdir>/amrfinder.tsv and its organism
decision to <outdir>/organism_detection.json — neither is a single JSON the
validation harness can diff directly. This collapses them into
<outdir>/amr_result.json:

  { "organism": "<resolved token>",        # from organism_detection.json
    "amr_gene_count": <int>,               # rows with Type == AMR
    "genes": ["blaKPC", "blaSHV", ...] }   # unique AMR element symbols, sorted

Header names drift across AMRFinderPlus DB versions, so columns are matched by a
normalized (lowercased, alnum-only) name rather than an exact string.
"""
import csv
import json
import sys
from pathlib import Path


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _organism(outdir: Path):
    p = outdir / "organism_detection.json"
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text())
    except (ValueError, OSError):
        return None
    for key in ("organism_token", "resolved_organism", "dominant_species", "organism"):
        if d.get(key):
            return d[key]
    return None


def _amr_genes(outdir: Path):
    p = outdir / "amrfinder.tsv"
    if not p.is_file():
        return []
    with p.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fields = reader.fieldnames or []
        norm = {f: _norm(f) for f in fields}
        sym_col = next((f for f in fields if norm[f] in ("elementsymbol", "genesymbol", "gene")
                        or "symbol" in norm[f]), None)
        type_col = next((f for f in fields if norm[f] == "type"), None)
        genes = []
        for row in reader:
            kind = (row.get(type_col) or "").strip().upper() if type_col else "AMR"
            if kind and kind != "AMR":
                continue
            sym = (row.get(sym_col) or "").strip() if sym_col else ""
            if sym:
                genes.append(sym)
    # unique, stable order
    return sorted(set(genes))


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: amr_summarize.py <run_outdir>")
    outdir = Path(sys.argv[1])
    genes = _amr_genes(outdir)
    result = {
        "organism": _organism(outdir),
        "amr_gene_count": len(genes),
        "genes": genes,
    }
    (outdir / "amr_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {outdir/'amr_result.json'}: organism={result['organism']} "
          f"amr_gene_count={result['amr_gene_count']}")


if __name__ == "__main__":
    main()
