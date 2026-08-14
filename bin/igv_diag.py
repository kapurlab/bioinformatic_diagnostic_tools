#!/usr/bin/env python3
"""Why won't a SNP-table cell open its alignment in IGV?

Checks, on disk, every assumption the viewer makes between a SNP cell and a
rendered read pileup. Standard library only; run it on the machine holding the
project.

    python3 bin/igv_diag.py <project_dir> [sample]

A copy of vsnp_gui's scripts/igv_diag.py, kept here because this is the repo
that is checked out and pulled on the clusters — the tool's own repo sits at
whatever tag the pin names, which may predate the script. The two differ only
in how they locate the renderer; if they ever disagree on anything else, the
copy in vsnp_gui is the one to trust.
"""
import glob
import gzip
import os
import re
import sys
import struct
import zipfile

def hdr(t): print(f"\n=== {t}")
def ok(m):  print(f"  OK    {m}")
def bad(m): print(f"  FAIL  {m}")
def note(m):print(f"        {m}")

def bam_contigs(path):
    """Contig names from a BAM header, without pysam."""
    try:
        with gzip.open(path, "rb") as fh:
            magic = fh.read(4)
            if magic != b"BAM\x01":
                return None
            l_text = struct.unpack("<i", fh.read(4))[0]
            fh.read(l_text)
            n_ref = struct.unpack("<i", fh.read(4))[0]
            out = []
            for _ in range(n_ref):
                l_name = struct.unpack("<i", fh.read(4))[0]
                out.append(fh.read(l_name).rstrip(b"\0").decode("utf-8", "replace"))
                fh.read(4)
            return out
    except Exception as e:
        note(f"could not read BAM header: {type(e).__name__}: {e}")
        return None

def _cell_text(cell_xml, shared):
    """The text of one <c> element.

    Two encodings have to be handled or the answer is a false alarm: a shared
    string (`t="s"` plus an index into sharedStrings.xml, which is what vsnp3's
    writer emits) and an inline string (`t="inlineStr"` with the text in an
    `<is><t>` child, which openpyxl emits). Reading only the first made this
    report "no CHROM:POS headers recognised" for a perfectly good table.
    """
    t = re.search(r'\bt="(\w+)"', cell_xml)
    kind = t.group(1) if t else ""
    if kind == "inlineStr":
        m = re.search(r"<is>(.*?)</is>", cell_xml, re.S)
        return re.sub(r"<[^>]+>", "", m.group(1)) if m else ""
    v = re.search(r"<v>(.*?)</v>", cell_xml, re.S)
    if not v:
        return ""
    val = v.group(1)
    if kind == "s":
        i = int(val)
        return shared[i] if i < len(shared) else ""
    return val


def _shared_strings(z):
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
    return [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<si>(.*?)</si>", sx, re.S)]


def _sheet_xml(z):
    name = next((n for n in z.namelist()
                 if re.fullmatch(r"xl/worksheets/sheet1\.xml", n)), None)
    if not name:
        # Some writers number differently; take the first worksheet there is.
        name = next((n for n in sorted(z.namelist())
                     if n.startswith("xl/worksheets/") and n.endswith(".xml")), None)
    return z.read(name).decode("utf-8", "replace") if name else ""


def table_loci(xlsx, limit=400):
    """Position headers (CHROM:POS) from row 1 of the first sheet."""
    try:
        with zipfile.ZipFile(xlsx) as z:
            shared = _shared_strings(z)
            xml = _sheet_xml(z)
            row1 = re.search(r'<row[^>]*\br="1"[^>]*>(.*?)</row>', xml, re.S)
            if not row1:
                return []
            out = []
            for c in re.findall(r"<c\b[^>]*>.*?</c>", row1.group(1), re.S):
                val = _cell_text(c, shared).strip()
                if re.fullmatch(r"\S+:\d+", val):
                    out.append(val)
                if len(out) >= limit:
                    break
            return out
    except Exception as e:
        note(f"could not read table: {type(e).__name__}: {e}")
        return []


def table_row_labels(xlsx, limit=400):
    """First-column values from every row of the first sheet."""
    try:
        with zipfile.ZipFile(xlsx) as z:
            shared = _shared_strings(z)
            xml = _sheet_xml(z)
            out = []
            for row in re.findall(r"<row\b[^>]*>(.*?)</row>", xml, re.S):
                c = re.search(r'<c\b[^>]*r="A\d+"[^>]*>.*?</c>', row, re.S)
                if not c:
                    continue
                val = _cell_text(c.group(0), shared).strip()
                if val:
                    out.append(val)
                if len(out) >= limit:
                    break
            return out
    except Exception as e:
        note(f"could not read row labels: {type(e).__name__}: {e}")
        return []


class _FallbackResolver:
    """A stand-in for xlsx_html when it cannot be imported.

    Mirrors _strip_vcf_suffix / _canonical_stem / ambiguous_stems /
    _NON_SAMPLE_LABELS. Kept deliberately small and side by side with the real
    thing; if the two ever disagree, the real one is right.
    """
    _NON_SAMPLE_LABELS = {"root", "mq", "annotation", "no annotations", "reference"}

    @staticmethod
    def _strip_vcf_suffix(s):
        for suf in (".vcf.gz", ".vcf"):
            if s.endswith(suf):
                s = s[: -len(suf)]
        return s[:-3] if s.endswith("_zc") else s

    @staticmethod
    def _flatten(s):
        return s.replace("_", "-")

    def _canonical_stem(self, label, *stem_sets):
        known = set()
        for st in stem_sets:
            if st:
                known |= st
        if not known or label in known:
            return label
        best = None
        for s in known:
            if label.startswith(s + "_") and (best is None or len(s) > len(best)):
                best = s
        if best is not None:
            return best
        flat = self._flatten(label)
        for s in known:
            fs = self._flatten(s)
            if flat == fs or flat.startswith(fs + "-"):
                if best is None or len(s) > len(best):
                    best = s
        return best if best is not None else label

    @staticmethod
    def ambiguous_stems(pairs):
        reached = {}
        for raw, stem in pairs:
            if not stem:
                continue
            if _FallbackResolver._strip_vcf_suffix(str(raw).strip()) == stem:
                continue
            reached.setdefault(stem, set()).add(str(raw).strip())
        return {k: sorted(v) for k, v in reached.items() if len(v) > 1}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    proj = os.path.abspath(sys.argv[1].rstrip("/"))
    want = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"project: {proj}")

    step1 = os.path.join(proj, "step1")
    if not os.path.isdir(step1):
        bad(f"no step1/ under {proj}")
        return 1
    samples = sorted(d for d in os.listdir(step1)
                     if os.path.isdir(os.path.join(step1, d)) and not d.startswith((".", "_")))
    if not samples:
        bad("step1/ has no sample directories")
        return 1
    sample = want or samples[0]
    if sample not in samples:
        bad(f"{sample} not in step1/ (have e.g. {samples[:3]})")
        return 1
    note(f"{len(samples)} step1 samples; inspecting {sample}")

    # --- the BAM the viewer will load -------------------------------------
    hdr("1. alignment directory")
    bams = sorted(glob.glob(os.path.join(step1, sample, "**", f"{sample}_nodup.bam"),
                            recursive=True), key=lambda p: os.stat(p).st_mtime)
    if not bams:
        # Distinguish "never aligned" from "aligned but the BAM is missing".
        # They look identical in the GUI and are completely different problems:
        # the first is a project part-way through Step 1, the second is a run
        # that failed.
        aligns = [d for d in os.listdir(os.path.join(step1, sample))
                  if d == "alignment" or d.startswith("alignment_")]
        if aligns:
            bad(f"alignment dir(s) {aligns} exist but hold no {sample}_nodup.bam "
                "-> the alignment did not finish")
            return 1
        bad(f"{sample} has NO alignment directory — Step 1 has not aligned it")
        note("There are reads here and nothing else, so IGV has nothing to show.")
        note("This is not a viewer fault: the Reference column for this row in")
        note("Step 1 Results is empty, and the IGV button is disabled for it.")
        note("")
        note("Samples in this project that DO have an alignment:")
        shown = 0
        for s in samples:
            if glob.glob(os.path.join(step1, s, "alignment*", f"{s}_nodup.bam")):
                note(f"  {s}")
                shown += 1
                if shown >= 5:
                    break
        if shown:
            note(f"  try one of those: python3 {sys.argv[0]} {proj} <sample>")
        else:
            note("  (none — no sample in this project has been aligned)")
        return 1
    bam = bams[-1]
    align = os.path.dirname(bam)
    ok(f"BAM: {bam}")
    if not os.path.exists(bam + ".bai"):
        bad(f"missing index {os.path.basename(bam)}.bai -> igv.js cannot read the BAM")
    else:
        ok("BAM index present")
    for f in sorted(os.listdir(align)):
        p = os.path.join(align, f)
        link = f" -> {os.readlink(p)}" if os.path.islink(p) else ""
        note(f"{f}{link}  ({os.stat(p).st_size if os.path.exists(p) else '?'} bytes)")

    # --- the reference the viewer will pick -------------------------------
    hdr("2. reference FASTA (the viewer takes the FIRST *.fasta, alphabetically)")
    fastas = sorted(glob.glob(os.path.join(align, "*.fasta")))
    if not fastas:
        bad("no *.fasta in the alignment dir -> the viewer reports 'no reference'")
        return 1
    chosen = fastas[0]
    if len(fastas) > 1:
        bad(f"{len(fastas)} FASTA files present — the viewer picks only the first")
        for f in fastas:
            note(("  chosen -> " if f == chosen else "           ") + os.path.basename(f))
    else:
        ok(f"exactly one: {os.path.basename(chosen)}")

    fai = chosen + ".fai"
    if not os.path.exists(fai):
        bad(f"missing {os.path.basename(fai)} — the viewer assumes it exists and "
            "never checks; without it igv.js cannot build the genome "
            "('IGV failed to load')")
        fai_contigs = []
    else:
        ok(f"index present: {os.path.basename(fai)}")
        with open(fai) as fh:
            fai_contigs = [ln.split("\t")[0] for ln in fh if ln.strip()]
    fa_contigs = []
    with open(chosen) as fh:
        for ln in fh:
            if ln.startswith(">"):
                fa_contigs.append(ln[1:].split()[0])
    note(f"{len(fa_contigs)} contigs in the FASTA, {len(fai_contigs)} in the .fai")
    if len(fa_contigs) > 1:
        note("multi-contig reference: igv.js opens such a genome on its \"all\" "
             "pseudo-contig, which cannot draw reads — from v0.4.57 the viewer "
             "navigates to the first contig instead")

    # --- do the BAM and the reference agree? ------------------------------
    hdr("3. BAM header vs reference")
    bc = bam_contigs(bam)
    if bc is None:
        note("skipped")
    elif set(bc) == set(fa_contigs):
        ok(f"identical contig sets ({len(bc)})")
    else:
        bad("BAM and reference disagree on contigs")
        for n in sorted(set(bc) - set(fa_contigs))[:6]:
            note(f"in BAM only : {n}")
        for n in sorted(set(fa_contigs) - set(bc))[:6]:
            note(f"in FASTA only: {n}")

    # --- the decisive check ----------------------------------------------
    hdr("4. do the SNP table's loci exist in that reference?")
    tables = sorted(glob.glob(os.path.join(proj, "step2", "**", "*_table-*.xlsx"),
                              recursive=True), key=lambda p: os.stat(p).st_mtime)
    if not tables:
        bad("no step2 *_table-*.xlsx found")
        return 1
    table = tables[-1]
    note(f"newest table: {table}")
    loci = table_loci(table)
    if not loci:
        bad("no CHROM:POS headers recognised in row 1 -> no cell can be clickable")
        return 1
    contigs = sorted({l.rsplit(":", 1)[0] for l in loci})
    ok(f"{len(loci)} position headers, {len(contigs)} distinct contigs")
    known = set(fai_contigs or fa_contigs)
    missing = [c for c in contigs if c not in known]
    if missing:
        bad(f"{len(missing)} of {len(contigs)} table contigs are NOT in the "
            "reference the viewer loads")
        for c in missing[:8]:
            note(f"missing: {c}")
        note("=> igv.js cannot resolve those loci; v0.4.54 reports "
             "'did not resolve to a contig in this reference'")
    else:
        ok("every table contig exists in the reference -> loci are resolvable")
    for c in contigs[:8]:
        note(f"table contig: {c}{'' if c in known else '   <-- NOT IN REFERENCE'}")

    hdr("5. why each row is or is not clickable")
    # The gate that decides hover. Run with the tool's own python so the real
    # backend code answers, rather than a re-implementation that could differ
    # from it in exactly the way that matters.
    # The renderer lives in the vsnp_gui repo. This script ships in both that
    # repo (scripts/) and the suite umbrella (bin/), so look for it relative to
    # here AND in the usual deployed checkouts; failing all of that, the
    # fallback resolver below answers just as well.
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(os.path.dirname(here), "backend", "app"),
        os.path.join(os.path.dirname(here), "vsnp_gui", "backend", "app"),
        os.path.expanduser("~/.local/share/bdtools/checkouts/vsnp_gui/backend/app"),
    ):
        if os.path.isdir(cand):
            sys.path.insert(0, cand)
    try:
        import xlsx_html as X                      # the real thing
        note("using the renderer's own resolver")
    except Exception:
        # openpyxl is not importable under a bare python3, and needing the
        # tool's env was enough friction to stop this being run at all. Mirror
        # the three rules instead, and say which was used so the answer is not
        # mistaken for the renderer's word.
        X = _FallbackResolver()
        note("renderer not importable here — using a copy of its rules "
             "(same three: exact, S_ prefix, then dash/underscore-insensitive)")

    # Exactly the sets preview_xlsx builds.
    bams, vcfs = set(), set()
    for s in samples:
        d = os.path.join(step1, s)
        if glob.glob(os.path.join(d, "alignment*", f"{s}_nodup.bam")):
            bams.add(s)
        if "_" in s:
            pre = s.split("_")[0]
            if pre and glob.glob(os.path.join(d, "alignment*", f"{pre}_nodup.bam")):
                bams.add(pre)
    for p in glob.glob(os.path.join(proj, "step2", "vcf_database", "*")):
        n = os.path.basename(p)
        for suf in ("_zc.vcf.gz", "_zc.vcf", ".vcf.gz", ".vcf"):
            if n.endswith(suf):
                vcfs.add(n[: -len(suf)])
                break
    note(f"{len(bams)} samples with a BAM, {len(vcfs)} with a VCF")

    labels = table_row_labels(table)
    if not labels:
        bad("could not read the table's first column")
        return 1
    resolved, dead, live = [], 0, 0
    for lbl in labels:
        if lbl.lower() in X._NON_SAMPLE_LABELS:
            continue
        stem = X._canonical_stem(X._strip_vcf_suffix(lbl), bams, vcfs)
        resolved.append((lbl, stem))
        if stem in bams or stem in vcfs:
            live += 1
        else:
            dead += 1
    print(f"        {live} row(s) resolve to a sample, {dead} do not")
    amb = X.ambiguous_stems(resolved)
    if dead:
        bad(f"{dead} row(s) match no Step 1 folder and no VCF -> those cells are "
            "not clickable and show NO hover")
        for lbl, stem in resolved:
            if stem not in bams and stem not in vcfs:
                note(f"row {lbl!r} -> {stem!r}  (no such sample)")
                break
        note("the label in the table and the folder under step1/ must correspond;")
        note("compare the two lists above")
    if amb:
        bad(f"{len(amb)} Step 1 folder(s) are reached by more than one row label "
            "-> ALL those rows are de-linked, deliberately, because a click "
            "could open the wrong specimen")
        for k, v in list(amb.items())[:3]:
            note(f"folder {k!r} <- {len(v)} labels, e.g. {v[:2]}")
        note("the page shows an 'IGV links are off for N rows' banner when this")
        note("happens; if you see no banner AND no hover, it is the case above")
    if not dead and not amb:
        ok("every sample row resolves uniquely -> all variant cells clickable")

    hdr("6. step2 runs (what the results drop-down lists)")
    runs = sorted(glob.glob(os.path.join(proj, "step2", "*", "run_metadata.json")))
    if runs:
        ok(f"{len(runs)} run(s) with metadata")
        for r in runs[-3:]:
            note(os.path.basename(os.path.dirname(r)))
    else:
        groups = [d for d in glob.glob(os.path.join(proj, "step2", "*"))
                  if os.path.isdir(d) and os.path.basename(d) != "vcf_database"]
        bad("no run_metadata.json under step2/ — the drop-down lists timestamped "
            "runs, and this project has none")
        note(f"{len(groups)} directory(ies) directly under step2/: "
             f"{[os.path.basename(d) for d in groups[:4]]}")
        note("those are served as a single synthetic 'legacy' entry instead")
    return 0

if __name__ == "__main__":
    sys.exit(main())
