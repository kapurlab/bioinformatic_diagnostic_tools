"""addon_sdk.py — the small library every pathogen add-on's hooks use.

Vendored into each add-on by `bdtools new-addon` (one copy per add-on, so an
add-on never depends on another repository at run time). It speaks contract 1
of the vsnp_gui add-on contract (docs/BUILDING_AN_ADDON.md in bdtools; the
checker is vsnp_gui's app.addon_check). Stdlib only.

A hook is a script vsnp_gui runs with this add-on's env python:

    python hooks/<hook>.py --context <file.json>

with its output folder as the working directory. Read the context with
`load_context()`, do the work, write the hook's one output with the matching
writer below, and exit 0. Exit non-zero (or call `fail(...)`) when the hook
could not do its job; vsnp_gui decides what that means for the sample.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CONTRACT = 1
LEVELS = ("pass", "review", "fail")


class Context(dict):
    """The context file, as a dict, with helpers for the common fields."""

    @property
    def hook(self) -> str:
        return self.get("hook", "")

    @property
    def library(self) -> Optional[Path]:
        return Path(self["library"]) if self.get("library") else None

    @property
    def project_dir(self) -> Path:
        return Path(self["project"]["dir"])

    @property
    def scope(self) -> str:
        return (self.get("project") or {}).get("scope") or ""

    @property
    def sample(self) -> Dict[str, Any]:
        return self.get("sample") or {}

    @property
    def out_dir(self) -> Path:
        """Where this hook writes: the sample's folder, the pathogen folder for
        after_run, or the run folder for an action."""
        if self.get("run_dir"):
            return Path(self["run_dir"])
        if self.sample.get("out_dir"):
            return Path(self.sample["out_dir"])
        return Path(self["project"]["pathogen_dir"])

    @property
    def threads(self) -> int:
        return int(self.get("threads") or 1)

    @property
    def params(self) -> Dict[str, Any]:
        return self.get("params") or {}


def load_context(argv: Optional[List[str]] = None) -> Context:
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)
    a = ap.parse_args(argv)
    with open(a.context, encoding="utf-8") as fh:
        ctx = Context(json.load(fh))
    if ctx.get("contract") != CONTRACT:
        fail(f"this add-on speaks contract {CONTRACT}; vsnp_gui sent {ctx.get('contract')!r}")
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    return ctx


def log(msg: str) -> None:
    """A line in the sample's run_step1.log (or the job log for an action)."""
    print(msg, flush=True)


def fail(reason: str, code: int = 1, out: Optional[Path] = None, name: Optional[str] = None) -> None:
    """Stop the hook with a reason vsnp_gui can show. For choose_reference,
    pass out=ctx.out_dir and name="reference.json" so the reason reaches
    Step 1 Results."""
    if out is not None and name:
        _write(out / name, {"error": reason})
    sys.stderr.write(reason.rstrip() + "\n")
    raise SystemExit(code)


def _write(path: Path, data: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def file_entry(path: Path, kind: str, label: Optional[str] = None) -> Dict[str, str]:
    """One output file for a row or an action. kind is how vsnp_gui opens it:
    report (html/pdf), tree (newick .tre/.nwk), table (.xlsx, opened in the
    table preview), fasta, json, text."""
    return {"path": str(Path(path).resolve()), "name": Path(path).name, "kind": kind,
            "label": label or Path(path).name}


# ---- the four hooks' outputs ---------------------------------------------

def write_reference(ctx: Context, reference_dir: Path, **details: Any) -> Path:
    """choose_reference: the vsnp3 reference folder for this sample. It must be
    inside the library (or <pathogen>/refs in the project), named like its
    FASTA (<name>/<name>.fasta). Extra keyword details are kept in provenance."""
    return _write(ctx.out_dir / "reference.json", {"reference_dir": str(Path(reference_dir).resolve()), **details})


def write_reads(ctx: Context, reads: Iterable[Path], **details: Any) -> Path:
    """prepare_reads: the reads vsnp3 should align, the same number as given."""
    return _write(ctx.out_dir / "reads.json", {"reads": [str(Path(r).resolve()) for r in reads], **details})


def write_result(ctx: Context, metrics: Dict[str, Any], level: str = "pass",
                 reasons: Optional[List[str]] = None, files: Optional[List[Dict[str, str]]] = None,
                 status: str = "ok") -> Path:
    """after_sample: this sample's Results row. `metrics` keys are the column
    keys addon.json declares; `level` joins the sample's QC verdict (the more
    severe wins) with `reasons` shown beside it."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    return _write(ctx.out_dir / "result.json", {
        "sample": ctx.sample.get("name"), "status": status, "metrics": metrics,
        "flags": {"level": level, "reasons": list(reasons or [])}, "files": list(files or [])})


def write_summary(ctx: Context, rows: List[Dict[str, Any]], files: Optional[List[Dict[str, str]]] = None,
                  text: str = "") -> Path:
    """after_run: the run's summary (and, in files, its report)."""
    return _write(ctx.out_dir / "summary.json", {"rows": rows, "files": list(files or []), "text": text,
                                                 "written_at": time.time()})


def write_action(ctx: Context, files: List[Dict[str, str]], summary: str = "", status: str = "ok") -> Path:
    """An action: what it made (trees open in the tree viewer, tables in the
    table preview, reports in the browser) and one line saying what it did."""
    return _write(ctx.out_dir / "action.json", {"status": status, "files": files, "summary": summary})


# ---- small helpers ---------------------------------------------------------

def html_page(title: str, body_html: str) -> str:
    """A complete, self-contained HTML page (no external files: vsnp_gui
    serves reports as single files)."""
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title)}</title><style>"
            "body{font:15px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:900px;margin:24px auto;padding:0 16px}"
            "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}"
            "</style></head><body>" + body_html + "</body></html>")


def table_html(header: List[str], rows: List[List[Any]]) -> str:
    h = "".join(f"<th>{html.escape(str(c))}</th>" for c in header)
    b = "".join("<tr>" + "".join(f"<td>{html.escape('' if v is None else str(v))}</td>" for v in r) + "</tr>"
                for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"
