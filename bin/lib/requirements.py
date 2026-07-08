"""requirements.py — what each tool needs at run time, for `bdtools doctor`.

A plain dict (no PyYAML) so it runs on a bare system python3, like manifest.py.
This is the declarative contract `doctor` and the build-time self-check verify
against a real install: the modules the backend/pipeline import, the external
binaries they shell out to, and the reference databases they read (with the
config key the path lives under and the command that installs it).

Keep these honest — only list things the code genuinely requires. A wrong entry
produces a false alarm, which erodes trust in the check. Binary/module lists
were derived by grepping each tool's imports and subprocess calls; extend them
as tools change. `os` gates a tool to a platform (kSNP4 ships Linux-only ELF
binaries, so it can't run on macOS even under Rosetta).

Database `kind`:
  dir          a directory that must exist and be non-empty
  dir_marker   a directory that must contain `marker` (e.g. kraken2's hash.k2d)
  file_prefix  a BLAST-style db prefix: at least one `<value>.*` file must exist
"""

# Shared by every GUI: the FastAPI/uvicorn web layer that serves the SPA.
_WEB = ["fastapi", "uvicorn", "pydantic"]

REQUIREMENTS = {
    "kraken_id_parse_gui": {
        "modules": _WEB + ["humanize", "Bio", "pandas", "allel", "numpy",
                           "pysam", "yaml", "svgwrite", "cairosvg", "PIL"],
        # playwright is an optional PDF renderer with a fallback — not installed
        # even in prod, so don't flag it as missing.
        "optional_imports": ["playwright"],
        "binaries": ["kraken2", "seqkit", "blastn", "bwa", "spades.py",
                     "bracken", "samtools", "picard", "freebayes", "pigz"],
        # bracken's osx-64 builds (<=2.6.1) predate python 3.10, so it cannot be
        # installed on Apple Silicon (osx-64 under Rosetta). Report it as a known
        # platform limitation, not a "rebuild the env" error a rebuild can't fix.
        # The Bracken abundance/pie-chart step won't run on macOS; the rest does.
        "platform_unavailable": {"macos": ["bracken"]},
        "fix": "bin/bdtools update kraken_id_parse_gui   # rebuilds the env",
        "databases": [
            {"label": "Kraken2 DB", "config_key": "kraken_db",
             "kind": "dir_marker", "marker": "hash.k2d",
             "default": "/srv/kapurlab/databases/kraken2/k2_standard_08gb",
             "fix": "bin/bdtools setup-databases kraken"},
            {"label": "BLAST ref_prok_rep_genomes", "config_key": "blast_db",
             "kind": "file_prefix",
             "default": "/srv/kapurlab/databases/blast/ref_prok_rep_genomes",
             "fix": "bin/bdtools setup-databases blast"},
        ],
    },
    "vsnp_gui": {
        "modules": _WEB,
        "binaries": ["vsnp3_step1.py", "vsnp3_step2.py", "snp-dists", "bcftools", "samtools"],
        "fix": "bin/bdtools update vsnp_gui   # rebuilds the vsnp3 env",
        "databases": [
            {"label": "vSNP reference options", "config_key": "vsnp3_reference_options_root",
             "kind": "dir",
             "default": "${VSNP_GUI_SITE_ROOT:-/srv/kapurlab}/refs/vsnp3/reference_options",
             "fix": "bin/bdtools setup-databases vsnp-refs vsnp-deps"},
        ],
    },
    "mlst_gui":   {"modules": _WEB, "binaries": ["mlst"]},
    "amr_plus_gui": {"modules": _WEB, "binaries": ["amrfinder", "mlst"]},
    "genoflu_gui": {"modules": _WEB, "binaries": ["seqkit"]},
    "irma_gui":   {"modules": _WEB, "binaries": ["IRMA", "seqkit"]},
    "ksnp_gui":   {"modules": _WEB, "binaries": ["seqkit"], "os": "linux"},
    "ncbi_submit_gui": {"modules": _WEB, "binaries": []},
}


def for_tool(name):
    return REQUIREMENTS.get(name, {"modules": _WEB, "binaries": []})
