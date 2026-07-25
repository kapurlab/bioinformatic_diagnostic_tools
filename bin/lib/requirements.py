"""requirements.py — what each tool needs at run time, for `bdtools doctor`.

A plain dict (no PyYAML) so it runs on a bare system python3, like manifest.py.
This is the declarative contract `doctor` and the build-time self-check verify
against a real install: the modules the backend/pipeline import, the external
binaries they shell out to, and the reference databases they read (with the
config key the path lives under and the command that installs it).

Keep these honest — only list things the code genuinely requires. A wrong entry
produces a false alarm, which erodes trust in the check. Binary/module lists
were derived by grepping each tool's imports and subprocess calls; extend them
as tools change. `optional_binaries` are reported as non-failing integration
notes. `os` gates a tool to a platform (kSNP4 ships Linux-only ELF binaries, so
it can't run on macOS even under Rosetta).

`asset_dirs` lists vendored third-party payload dirs (relative to the tool dir)
that hold binaries NOT installed by conda — so a `<env>/bin` + PATH search would
miss them. They are resolved via tool_launch._resolve_asset_dir, i.e. exactly the
way the launcher builds PATH, so doctor and the launcher can never disagree.

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
    # kSNP4 is NOT a conda package — deploy/install.sh downloads the kSNP4.1 Linux
    # package from SourceForge into vendor/kSNP4-bin and prepends that to PATH. So
    # its binaries are invisible to an <env>/bin search, which is why doctor used to
    # report ksnp_gui green while every run exited 127. Declaring `asset_dirs` lets
    # the check look where the binaries actually live. The generic "rebuilds the
    # env" fix is wrong here — the env never contained kSNP.
    "ksnp_gui": {
        "modules": _WEB,
        "binaries": ["seqkit", "kSNP4", "Kchooser4", "MakeKSNP4infile"],
        "asset_dirs": ["vendor/kSNP4-bin"],
        "os": "linux",
        "fix": "bin/bdtools install ksnp_gui   # downloads the kSNP4.1 Linux package into vendor/",
    },
    "ncbi_submit_gui": {"modules": _WEB, "binaries": []},
    "mhc_gui": {
        "modules": _WEB + ["aiofiles", "openpyxl"],
        "binaries": ["nanoq", "minimap2", "samtools", "bcftools", "vsearch",
                     "spoa", "medaka_consensus", "blastn"],
        # These power optional integrations in the dashboard, not MHC typing.
        # Doctor reports their absence without declaring the core tool broken.
        "optional_binaries": ["amrfinder", "rclone"],
    },
}


def for_tool(name):
    return REQUIREMENTS.get(name, {"modules": _WEB, "binaries": []})
