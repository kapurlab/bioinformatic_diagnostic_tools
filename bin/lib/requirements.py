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
notes. `os` gates a tool to a platform — use it only when the tool genuinely has
no build for the others, not merely when a build is awkward to obtain.

`binary_format_probes` names compiled binaries whose *executable format* must
match the host, checked from their magic bytes. This exists because "resolves on
PATH" is not "can run": ksnp_gui's kSNP4 payload is a hand-downloaded SourceForge
archive, and a host that fetched the Linux zip but runs macOS passed every check
here and then died at the first exec with "[Errno 8] Exec format error". Only
worth declaring for binaries conda did NOT install — conda already solves for the
right subdir.

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
# `multipart` (pip name python-multipart) belongs here because every backend
# has UploadFile routes, and FastAPI refuses to even import an app that
# declares one without it — an env can report every analysis version
# correctly and still not start. Probed as `multipart` (the import name every
# python-multipart release has answered to, old and new).
_WEB = ["fastapi", "uvicorn", "pydantic", "multipart"]
# ...and the GUIs whose upload/log streaming goes through aiofiles (all but
# vsnp_gui, which does its file IO synchronously).
_WEB_AIO = _WEB + ["aiofiles"]

REQUIREMENTS = {
    "kraken_id_parse_gui": {
        "modules": _WEB_AIO + ["humanize", "Bio", "pandas", "allel", "numpy",
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
             # vsnp3 locates references through this file at run time, so it is
             # the authority — checked before the config key, which can hold a
             # stale path from an earlier install on the same machine.
             "paths_file": "dependencies/reference_options_paths.txt",
             "default": "${VSNP_GUI_SITE_ROOT:-/srv/kapurlab}/refs/vsnp3/reference_options",
             "fix": "bin/bdtools setup-databases vsnp-refs vsnp-deps"},
        ],
    },
    "mlst_gui":   {"modules": _WEB_AIO, "binaries": ["mlst"]},
    "amr_plus_gui": {"modules": _WEB_AIO, "binaries": ["amrfinder", "mlst"]},
    "genoflu_gui": {"modules": _WEB_AIO, "binaries": ["seqkit"]},
    "irma_gui":   {"modules": _WEB_AIO, "binaries": ["IRMA", "seqkit"]},
    # kSNP4 is NOT a conda package — deploy/install.sh downloads the kSNP4.1
    # package for the host OS from SourceForge into vendor/kSNP4-bin and prepends
    # that to PATH. So its binaries are invisible to an <env>/bin search, which is
    # why doctor used to report ksnp_gui green while every run exited 127.
    # Declaring `asset_dirs` lets the check look where the binaries actually live.
    # The generic "rebuilds the env" fix is wrong here — the env never contained kSNP.
    #
    # No `os` gate: SourceForge publishes both a Linux and a Mac package. It was
    # gated to linux on the belief that kSNP4 was Linux-only, which made doctor SKIP
    # ksnp_gui on macOS entirely — so the one check that could have caught a
    # wrong-OS payload never ran there. `binary_format_probes` is that check.
    "ksnp_gui": {
        "modules": _WEB_AIO,
        "binaries": ["seqkit", "kSNP4", "Kchooser4", "MakeKSNP4infile"],
        "asset_dirs": ["vendor/kSNP4-bin"],
        # kSNP4 itself is a bash script and says nothing about the payload's
        # architecture — probe compiled members.
        "binary_format_probes": ["MakeKSNP4infile", "Kchooser4"],
        "fix": "bin/bdtools install ksnp_gui   # downloads the kSNP4.1 package for this OS into vendor/",
    },
    "ncbi_submit_gui": {"modules": _WEB_AIO, "binaries": []},
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
