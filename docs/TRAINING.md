# 🎓 Training — a hands-on walkthrough of the Kapur Lab diagnostic tools

This guide teaches you to run every tool in the suite from start to finish, using
**real, public sequencing data** that you can copy and paste straight into each
tool. No prior command-line experience is assumed — everything happens in the
web dashboard.

Work through it top to bottom the first time. After that, each **module** stands
on its own, so you can jump to the tool you need.

**What you will learn**

| Module | Tool | You will… | Data used |
|---|---|---|---|
| 1 | **IRMA** | Assemble influenza genomes from raw reads and read the QC report | Influenza A reads |
| 2 | **GenoFLU** | Genotype an assembled H5 influenza genome (clade 2.3.4.4b) | IRMA output from Module 1 |
| 3 | **vSNP3 (intro)** | Learn the two-step SNP workflow on *M. tuberculosis*/*M. bovis* | TB reads |
| 4 | **vSNP3 (phylogeny)** | Build a SNP tree across the whole *M. tuberculosis* complex | MTBC lineage panel |
| 5 | **AMRFinderPlus** | Find antimicrobial-resistance genes in a bacterial isolate | *Pasteurella*, *E. coli* reads |
| 6 | **MLST** | Assign a sequence type (ST) to an isolate | *Pasteurella* reads |
| 7 | **kSNP** | Build a reference-free SNP tree from finished genomes | MTBC genome accessions |

> ⏱ **Time:** Modules 1, 5, 6, 7 take ~10–30 min each. vSNP3 (Modules 3–4)
> takes longer because it aligns every read — budget an hour, most of it
> unattended while the job runs.

---

## Part A — Before you begin (read once)

### A.1 Open the dashboard

Everything starts from the dashboard — a home page listing your installed tools.

- **macOS:** double-click **`Open Dashboard.command`** in the
  `bioinformatic_diagnostic_tools` folder, **or**
- **Any system (Terminal):**
  ```bash
  cd ~/bioinformatic_diagnostic_tools
  bin/bdtools dashboard
  ```

Your browser opens to **http://127.0.0.1:8080/**. Each tool is a card — click
**Launch** and the tool opens in a new tab. **Leave the small dashboard window
open while you work.**

> On an Open OnDemand cluster there is no local dashboard — your tools appear as
> cards in your institution's OOD portal instead. The in-tool steps below are
> identical.

### A.2 Two tools need reference databases first

Most tools are ready to go. Two need a one-time database download:

| Tool | Needs | Set up with |
|---|---|---|
| **vSNP3** (Modules 3–4) | vSNP3 reference set | `bin/bdtools setup-databases vsnp-refs vsnp-deps` |
| **AMRFinderPlus** (Module 5) | Kraken2 DB *(only for automatic organism detection — optional)* | `bin/bdtools setup-databases kraken` |

Run those before the relevant module. If you skip the Kraken2 DB, AMRFinderPlus
still works — you just pick the organism by hand. If a tool card shows a
**"needs setup"** badge, that's what it's telling you.

> Check anything at any time with `bin/bdtools doctor` — it lists each tool, what
> it needs, and the exact command to fix a gap.

### A.3 How every tool works — the shared layout

**All the tools share the same screen layout.** Learn it once here and every
module will feel familiar. From top to bottom you'll see:

1. **Settings** *(collapsible)* — database paths and where your projects are
   saved. Defaults are fine; you rarely touch this.
2. **Projects & Samples** — on the **left**, create and open a *project* (a named
   folder that holds your data and results). Expand a project to see its samples,
   each with a **checkbox**.
3. **Inputs** — on the **right**, this is how you get data *into* a project.
   Every tool offers the same four ways:
   - **Choose Files** / drag-and-drop — upload files from your computer.
   - **Link** — point at a folder already on the server (no copying).
   - **SRA Download** — **paste accession numbers (SRR/ERR/DRR) and the tool
     downloads the reads for you.** ← this is what you'll use in this guide.
   - *(kSNP only)* **Download genome FASTA by accession** — paste GenBank/RefSeq
     genome accessions (e.g. `NC_000962`).
4. **Run …** — pick your options and click **▶ Run selected (N)**, where N is the
   number of samples you checked.
5. **Pipeline Log** *(collapsible)* — live output while the job runs. It ends with
   `[DONE]`.
6. **Results** — tables, trees, reports, and download links appear here when the
   run finishes. Click a sample to load its results.

**The universal recipe** — every module below is a variation of these five steps:

> **① Create a project → ② paste accessions into SRA Download → ③ check the
> samples → ④ Run selected → ⑤ read the Results.**

### A.4 A note on the data in this guide

Every accession below is a **public** record from NCBI's Sequence Read Archive
(SRA) or GenBank. The tools download them for you — you never handle files by
hand. The very first download of a sample can take a few minutes depending on its
size and your connection; downloads are cached, so re-running is instant.

---

## Module 1 — IRMA: assemble an influenza genome

**What it does.** IRMA (the CDC's Iterative Refinement Meta-Assembler) takes raw
influenza sequencing reads and reconstructs the eight genome segments, reports
per-segment coverage and quality, predicts the subtype (e.g. H5N1), and can build
submission-ready FASTA headers.

**Input.** Raw reads (FASTQ) — exactly what SRA gives you.

### The data — copy this into IRMA's SRA Download box

```
SRR36749644
SRR36749648
SRR36749650
SRR36749472
SRR37580742
```

*Influenza A virus (IAV) whole-genome sequencing runs. We'll use the same set for
GenoFLU in Module 2.*

### Steps

1. On the dashboard, **Launch** **IRMA**.
2. In **Projects & Samples** (left), type a project name — e.g. `IAV_training` —
   and click **Create**.
3. In **Inputs** (right), make sure `IAV_training` is selected in the dropdown.
4. Find the **SRA Download** box, **paste the five accessions above** (one per
   line), and click **Download**. Watch the **Pipeline Log** — each run appears in
   the project when it finishes downloading.
5. *(Optional)* Open **Sample Metadata** and fill in host/state/collection year.
   This is only needed if you plan to submit sequences to NCBI; skip it for
   training.
6. In **Projects & Samples**, expand the project and **check the box** next to each
   sample you want to run.
7. In the **Run IRMA** section:
   - **IRMA module:** leave on **`FLU`** (it's `CoV` for SARS-CoV-2).
   - **Run GenoFLU genotyping:** ✅ **tick this on.** IRMA will assemble *and*
     genotype in one pass — a preview of Module 2.
8. Click **▶ Run selected (5)**. Assembly takes several minutes per sample; they
   run one after another.
9. When the log shows `[DONE]`, click a sample name to open its **Results**.

### How to read the output

- **Subtype banner** — e.g. **`H5N1`**. This is IRMA's call from the HA and NA
  segments.
- **Per-segment table** — one row per influenza segment (PB2, PB1, PA, HA, NP, NA,
  M, NS). The columns that matter:
  - **Coverage %** — how much of the segment was reconstructed. You want this
    high (near 100%).
  - **Read depth** — average reads stacked at each position. Higher = more
    confident. Thin coverage (low depth) means a shaky consensus.
  - **Verdict** — **PASS** (assembled well), **REVIEW** (present but unusual), or
    **FAIL** (missing/too low). A complete influenza genome is **8/8 segments
    PASS**.
- **Report (PDF)** — a shareable lab report: QC of the input reads, the
  per-segment table, and coverage diagrams (a depth plot per segment).
- **Submission FASTA** — the consensus sequences, one per segment.
- **GenoFLU genotype** (because you ticked it on) — the clade/genotype call; we
  interpret this in the next module.

> 🔎 **Teaching point.** If a sample has, say, 7/8 segments PASS and NA at 60%
> coverage, that's a *partial* genome — usable for subtyping but not for a
> confident whole-genome genotype. The verdict column tells you at a glance.

---

## Module 2 — GenoFLU: genotype an H5 influenza genome

**What it does.** GenoFLU (USDA) assigns a **genotype** to a North-American H5
influenza genome — it BLASTs each of the eight segments against curated
references and reports the clade/subclade (e.g. **`2.3.4.4b`**) plus a per-segment
lineage breakdown. It's how you tell whether a genome is the "cattle" H5N1
genotype **B3.13** vs. another constellation.

**Input.** An **assembled** genome (FASTA) — *not* raw reads. GenoFLU genotypes;
it does not assemble. So you must assemble first (Module 1).

### Two ways to run it

**The easy way (already done):** you ticked **Run GenoFLU genotyping** in Module 1,
so IRMA already produced a genotype for each sample. Open a sample's IRMA Results
and read the **GenoFLU genotype** section. For most work, this is all you need.

**The standalone way (this module):** run the dedicated GenoFLU tool on the FASTA
that IRMA produced. This is the pattern you'd use for genomes assembled elsewhere.

### Steps

1. **Launch** **GenoFLU** from the dashboard.
2. Create a project — e.g. `IAV_genotyping`.
3. Get an assembled FASTA into it. Use the IRMA output from Module 1:
   - In IRMA, download a sample's **Assembly FASTA** (or **Submission FASTA**) to
     your computer, then in GenoFLU use **Choose Files** to upload it; **or**
   - Use **Link** to point GenoFLU at the IRMA project folder on the server.
   > GenoFLU's own **SRA Download** box exists, but remember it only fetches *raw
   > reads* — you'd still have to assemble them in IRMA first. Feeding an assembly
   > is the whole point here.
4. Leave **Percent-identity threshold** at its default (**98%**, the USDA
   surveillance standard).
5. Check the sample and click **▶ Run selected (1)**.
6. Open **Results**.

### How to read the output

- **Genotype banner** — the headline call, e.g. **`B3.13`** or a clade like
  **`2.3.4.4b`**. A **green** badge means all 8 segments were confidently
  assigned; **amber** means the genome was incomplete, so the call is provisional.
- **Per-segment table** — for each segment: the assigned **lineage**, the
  **% identity** to the best reference, and mismatches. Reading down this column
  tells you if the genome is a "clean" constellation or a **reassortant** (segments
  from different lineages).
- **Report (PDF)** — the same information in a lab-report format.

> 🔎 **Teaching point.** The genotype is the *combination* of all eight segment
> lineages. That's why an incomplete assembly (amber badge) can't be given a firm
> genotype — a missing segment could change the constellation. Always check the
> IRMA verdict (8/8 PASS) before trusting a genotype.

---

## Module 3 — vSNP3: the two-step SNP workflow (introduction)

**What it does.** vSNP3 (USDA) is the suite's high-resolution SNP tool for
*Mycobacterium tuberculosis* complex (TB, *M. bovis*, *M. orygis*, …) and
*Brucella*. It answers *"how closely related are these isolates?"* — the core
question in outbreak and surveillance work.

**The two-step model — the single most important concept:**

- **Step 1 — one sample at a time.** Align a sample's reads to a reference genome
  and call its variants, producing a per-sample **VCF** file. Do this once per
  sample.
- **Step 2 — compare many samples.** Collect the Step 1 VCFs, build a **SNP table**
  (who differs from whom, and where) and a **phylogenetic tree**.

The power of this split: you run Step 1 once, then re-run Step 2 as often as you
like — adding samples, changing thresholds — **without re-aligning anything.**

### The data — copy this into vSNP3's SRA Download box

```
SRR12882448
SRR1173725
SRR998630
SRR7236232
SRR6797355
SRR10251192
SRR10251191
SRR10251185
SRR10251193
```

*A small TB / M. bovis complex set — enough to learn Step 1 → QC → Step 2.*

### Step 1 — align and call variants

1. **First-time only:** set up the reference databases (Part A.2):
   ```bash
   bin/bdtools setup-databases vsnp-refs vsnp-deps
   ```
2. **Launch** **vSNP3**. Click **Preflight** in Settings — it must go green before
   the Run buttons enable.
3. Create a project — e.g. `TB_intro`.
4. In **Inputs → SRA Download**, paste the nine accessions above and click
   **Download**.
5. In the **Step 1** section, click **Setup** (this organizes the downloaded reads
   into one folder per sample).
6. **Choose a reference.** Two ways:
   - **Auto-detect (best match)** — recommended; vSNP3 scans each sample's reads
     and picks the closest reference automatically.
   - **Manual** — the two built-in TB references are **`Mycobacterium_H37`**
     (*M. tuberculosis*) and **`Mycobacterium_AF2122`** (*M. bovis*).
7. Click **▶ Run**. Step 1 processes a few samples in parallel; use **Stop** to
   halt if needed. This is the slow part — reads are being aligned.

### Review quality before you go on

Open **QC Summary / Step 1 Results**. This is a mandatory checkpoint. Key columns:

| Metric | Healthy | Worry when… | Why it matters |
|---|---|---|---|
| **Avg Depth of Coverage** | ≥ 40× | < 20× | Low depth = unreliable SNP calls. |
| **% Reference with Zero Coverage** | < 5% | > 10% | High = wrong reference or poor library. |
| **R1/R2 % passing Q20** | > 50% | very low | Poor-quality sequencing run. |
| **Quality SNPs** | organism-typical | unusually high | A spike often means the *wrong reference* or contamination. |

Tick the **Exclude** box for any sample that fails, then click **Save
Exclusions** — Step 2 will automatically leave those samples out.

### Step 2 — build the SNP table and tree

1. Go to the **Step 2** section and choose **Use Step 1 only** (compare the samples
   you just processed).
2. Click **Setup**. vSNP3 links your Step 1 VCFs and confirms they all used the
   **same reference** (it refuses to mix references — that's correct behavior).
3. Click **▶ Run**.
4. When done, expand the timestamped run under **Results**.

### How to read the output

- **`…_sort_table.xlsx` (the cascading SNP table)** — rows are SNP positions,
  columns are samples. Cells show the base each sample carries. Samples that share
  the same pattern of SNPs are closely related. This is the table you'd put in a
  report.
- **`…_tree.tre` (phylogenetic tree)** — open it in a tree viewer (FigTree, or
  the built-in viewer). **Branch length ≈ number of SNPs.** Isolates on a tight
  cluster (a handful of SNPs apart) are plausibly linked; isolates far apart are
  not.
- **`step2_summary.html`** — an interactive summary tying it together.
- **IUPAC codes in the table** — a pure base (`A/C/G/T`) is a clean call. A letter
  like **`R`, `Y`, `M`, `K`, `S`, `W`** means a *mixed* position (two bases at
  once) — a red flag for contamination or co-infection. A **`-`** means no
  coverage there.

> 🔎 **Teaching point.** "How many SNPs apart is a real outbreak link?" depends on
> the organism, but for TB, isolates within ~5–12 SNPs are often considered
> potentially linked. The tree and the SNP table are two views of the same
> answer — always look at both.

---

## Module 4 — vSNP3: a whole-complex phylogeny

Now scale up. This module builds a tree spanning the **entire *M. tuberculosis*
complex** — every human lineage plus the animal-adapted members (*M. bovis*,
*M. caprae*, *M. orygis*, *M. microti*, and more). It's the same Step 1 → Step 2
workflow as Module 3, just with a broader, labeled panel so you can *see* the
lineage structure in the tree.

### The data — MTBC representation panel

Paste any subset (or all) of these into **SRA Download**. Each is labeled with the
lineage/species it represents, so you can confirm the tree groups them correctly.

```
ERR212113     Lineage 1        SRR671797     Lineage 2
ERR270648     Lineage 1        ERR2704702    Lineage 2
ERR2704680    Lineage 1        ERR221591     Lineage 3
ERR234100     Lineage 2        SRR6797316    Lineage 3
ERR553373     Lineage 2        SRR6964550    Lineage 3
ERR2704693    Lineage 3        ERR234198     Lineage 4
ERR270639     Lineage 4        SRR671750     Lineage 4
ERR2704709    Lineage 4        ERR234113     Lineage 5
ERR234680     Lineage 5        ERR2704812    Lineage 5
ERR2704686    Lineage 5        ERR234186     Lineage 6
ERR2383628    Lineage 6        ERR270805     Lineage 6
ERR2704681    Lineage 6        ERR1200604    Lineage 7
ERR181435     Lineage 7        ERR756345     Lineage 7
ERR2704711    Lineage 7        SRR10828835   Lineage 8
SRR1173725    Lineage 8        ERR181314     Lineage 9
ERR181315     Lineage 9        ERR4192384    Lineage 9
ERR2516384    Lineage 10       ERR2707158    Lineage 10
ERR212091     M. bovis         SRR5216728    M. bovis
SRR7983754    M. bovis         SRR1791695    M. bovis
ERR10430697   M. caprae        ERR10430698   M. caprae
ERR1462610    M. caprae        SRR7617662    M. caprae
ERR027295     M. microti       ERR553376     M. microti
ERR027297     M. microti       SRR3647357    M. microti
SRR3500411    M. mungi         ERR015582     M. orygis
ERR234682     M. orygis        SRR6797355    M. orygis
SRR5642711    M. orygis        SRR1239336    M. pinnipedii
SRR1239339    M. pinnipedii    SRR7693584    M. pinnipedii
ERR970409     M. suricattae    ERR970410     M. suricattae
ERR970412     M. suricattae    ERR266120     M. canettii
```

> 💡 **Start small.** This is a large panel — dozens of genomes, each aligned
> individually in Step 1, which is time-consuming. For a first pass, pick **~2
> samples per lineage** (say 12–16 total). Add the rest later and just re-run
> Step 2 — no re-alignment needed. That's the payoff of the two-step design.

### Steps

Identical to Module 3:

1. New project — e.g. `MTBC_phylogeny`.
2. **SRA Download** → paste your chosen accessions → **Download**.
3. **Step 1**: **Setup** → reference **Auto-detect (best match)** (essential here,
   since the panel spans many species) → **Run**.
4. **QC Summary**: review, exclude any failures, **Save Exclusions**.
5. **Step 2**: **Use Step 1 only** → **Setup** → **Run**.

> ⚠️ **Reference lock.** Because auto-detect may align different species to
> different references, Step 2 will only compare samples that share a reference. If
> you want a single tree across all of MTBC, align the whole panel to one common
> reference (e.g. **`Mycobacterium_H37`**) by selecting it manually in Step 1
> instead of auto-detect.

### How to read the output

Open the **tree** (`…_tree.tre`). Because you know each sample's true lineage from
the labels above, this is a self-checking exercise:

- Samples of the **same lineage should form clusters** (clades) together.
- The animal-adapted species (*M. bovis*, *M. caprae*, *M. orygis*, …) should sit
  within the Lineage-4-adjacent part of the complex, and *M. canettii* should sit
  well apart as the most divergent member.
- If a sample lands in the "wrong" clade, that's a teaching moment — usually a QC
  problem (low coverage, wrong reference) you can trace back in the Step 1 stats.

The **cascading SNP table** shows the shared-SNP patterns that *define* each
lineage — the columns of SNPs that all Lineage-2 samples share, for instance.

---

## Module 5 — AMRFinderPlus: antimicrobial-resistance genes

**What it does.** AMRFinderPlus (NCBI) scans a bacterial genome for **acquired
resistance genes** and **resistance-conferring point mutations**. The GUI adds two
conveniences: it **assembles** raw reads for you (so you can start from SRA), and
it **detects the organism** (via Kraken2) so it can screen for the right
species-specific mutations.

**Input.** Raw reads (FASTQ) or an assembled genome (FASTA). We'll start from
reads.

### The data

Two isolates — copy each into a separate project (or both into one).

*Pasteurella multocida* (also used for MLST in Module 6):
```
SRR28320745
```

*Escherichia coli*:
```
SRR39605045
```

### Steps

1. *(Recommended, first-time)* set up the Kraken2 DB for automatic organism
   detection: `bin/bdtools setup-databases kraken`. Without it, you'll just pick
   the organism manually — that's fine.
2. **Launch** **AMRFinderPlus**. Create a project — e.g. `AMR_training`.
3. **Inputs → SRA Download** → paste `SRR28320745` (and/or `SRR39605045`) →
   **Download**.
4. Expand the project and **check** the sample.
5. In **Run AMRFinderPlus**:
   - **Force organism** — leave on **Auto-detect (recommended)** if you set up
     Kraken2. Otherwise choose the species by hand (e.g. `Escherichia` for the
     *E. coli* run). The organism selection matters: it enables the correct
     **point-mutation** screen for that species.
   - **`--plus`** — leave ✅ on to also report virulence/stress/biocide genes.
6. Click **▶ Run selected (1)**. The tool assembles the reads, QCs the assembly,
   optionally types it (MLST), then runs AMRFinderPlus.
7. Open **Results**.

### How to read the output

The **resistance table** is the main output. Key columns:

| Column | What it tells you |
|---|---|
| **Element symbol** | The gene or mutation, e.g. `blaROB-1`, `tet(H)`, `gyrA_S83L`. |
| **Class / Subclass** | The drug class it affects, e.g. `BETA-LACTAM` / `Cephalosporin`. |
| **Method** | *How* it was found — see below. |
| **% Identity / % Coverage** | How well the hit matches the reference gene. Near 100% = a confident, full-length match. |

**The Method column is your confidence guide:**

- **`ALLELE` / `EXACT`** — an exact match to a known resistance gene. High
  confidence.
- **`POINT`** — a resistance-conferring **point mutation** (e.g. a fluoroquinolone
  mutation in `gyrA`). Only screened when the organism is known — hence the
  organism selection in step 5.
- **`PARTIAL` / `INTERNAL_STOP`** — a truncated or interrupted gene. Flag for
  review — it may not confer resistance.

> 🔎 **Teaching point.** A gene being *present* is not the same as resistance being
> *expressed*, and `% coverage < 100%` (a partial hit) deserves a second look.
> AMRFinderPlus reports the genotype; clinical interpretation is a further step.
> Compare the *E. coli* result (typically a rich set of acquired genes) with the
> *Pasteurella* result (usually sparser) to see how organism and resistance
> content vary.

---

## Module 6 — MLST: assign a sequence type

**What it does.** MLST (Multi-Locus Sequence Typing) reads the alleles at a fixed
set of ~7 housekeeping genes and looks up the combination in the PubMLST database
to assign a **Sequence Type (ST)** — a compact, portable label for a strain.
Identical STs across isolates suggest they're the same clone.

**Input.** Raw reads (assembled for you) or an assembled genome. It **auto-detects
the scheme** — you don't tell it the species.

### The data — same isolate as Module 5

*Pasteurella multocida*:
```
SRR28320745
```

Reusing the *Pasteurella* isolate lets you see two tools describe one organism:
its **resistance genes** (Module 5) and its **strain type** (here).

### Steps

1. **Launch** **MLST**. Create a project — e.g. `MLST_training`.
2. **Inputs → SRA Download** → paste `SRR28320745` → **Download**.
3. Expand the project, **check** the sample.
4. In **Run MLST**, leave **Force scheme** on **Autodetect (recommended)** — the
   tool picks the right scheme from the assembly.
5. Click **▶ Run selected (1)**.
6. Read the inline result / **Results** pane.

### How to read the output

- **Scheme** — the PubMLST scheme that matched, e.g. `pmultocida_rirdc` for
  *Pasteurella multocida*. This confirms the species.
- **ST** — the **Sequence Type**, e.g. `ST 13`. This is the headline result.
- **Allele numbers** — the allele called at each locus (e.g. `adk: 1`, `est: 2`,
  …). The combination *is* the ST.
- **Badges:**
  - **novel** — one or more alleles aren't in the database (marked `~`). Possibly
    a new variant; worth confirming.
  - **partial** — an allele call was incomplete (marked `?`), usually from an
    assembly gap — lower confidence.

> 🔎 **Teaching point.** MLST and AMRFinderPlus are complementary: MLST answers
> *"which strain is this?"* and AMRFinderPlus answers *"what resistance does it
> carry?"*. In fact AMRFinderPlus can call MLST internally to cross-check the
> organism — that's why the two tools share a project layout.

---

## Module 7 — kSNP: a reference-free SNP tree

**What it does.** kSNP4 builds a SNP matrix and phylogenetic tree **without a
reference genome and without alignment** — it compares genomes by their k-mers
(short DNA words). Use it when you have *finished genomes* (assemblies) and want a
quick, alignment-free tree — for example, to place unknown isolates among known
references.

**Input.** **Assembled genomes (FASTA)** — not reads. Conveniently, kSNP can
**download genomes directly by accession number.**

### The data — MTBC reference genomes (by accession)

kSNP takes **GenBank/RefSeq genome accessions**, not SRA runs. Paste these into
kSNP's **"Download genome FASTA by accession"** box (not the SRA box):

```
CP041800     Lineage 1
CP017920     Lineage 2
CP046309     Lineage 3
NC_000962    Lineage 4    (M. tuberculosis H37Rv — the classic reference)
CP020381     Lineage 4
CP089775     Lineage 4
CP041837     Lineage 4
CP069067     Lineage 5
NZ_CP014617  Lineage 6
CP041791     Lineage 7
CP048071     Lineage 8
NC_002945    M. bovis
CP109681     M. bovis BCG
CP016401     M. caprae
LR882497     M. microti
CP063804     M. orygis
```

### Steps

1. **Launch** **kSNP**. Create a project — e.g. `MTBC_ksnp`.
2. In **Inputs**, find **"Download genome FASTA by accession"**, paste the
   accessions above (the labels are just notes — paste the accession column), and
   keep **"Name files by organism / strain metadata (recommended)"** ✅ ticked so
   the tree labels are readable. Click **Fetch FASTA**.
3. *(Optional but recommended)* In the file list, click the **✎ (pencil)** next to
   each genome to give it a short, clear label (e.g. `L4_H37Rv`, `M_bovis`). **This
   label becomes the name in the tree** — clean names now save confusion later.
4. Expand the project and make sure the genomes you want are **checked** (kSNP
   needs at least 2).
5. In **Run kSNP4**, the defaults are the validated standard:
   - **k-mer size** — leave **blank** so *Kchooser4* picks the optimum
     automatically.
   - **Core-SNP analysis**, **Maximum-likelihood tree**, **Per-SNP VCF** — leave
     ✅ on.
   - **min_frac** — leave at **0.8**.
6. Click **▶ Run kSNP4**.
7. Open **Results**.

### How to read the output

The **Results** pane gives you three SNP counts and a quality verdict:

- **Core SNPs** — SNPs present in **every** genome. **The core-SNP tree is the one
  to trust** — no missing data.
- **All (pan) SNPs** — every SNP found in any genome. Most detail, but some
  positions are missing in some genomes.
- **Majority SNPs** — a middle ground (present in ≥ `min_frac` of genomes).

Then the trees:

- Open **`tree.core_SNPs.parsimony.tre`** first — the most reliable. Because you
  loaded labeled MTBC lineages, the genomes should again group by lineage, with
  *M. bovis*/BCG/*M. caprae*/*M. orygis* clustering in the animal-adapted part.
- **FCK (Fraction of Core K-mers)** in the metrics strip is your sanity check:
  **≥ 0.1** means the genomes share enough sequence for reliable SNP detection.
  A very low FCK means the set is too divergent for kSNP to resolve well.

> 🔎 **Teaching point.** kSNP (reference-free, from assemblies) and vSNP3
> (reference-based, from reads) answer the same *"how related?"* question by
> different routes. kSNP is fast and needs no reference — great for a quick look or
> when no good reference exists. vSNP3 is the validated, reference-anchored
> workflow for diagnostic reporting. Running the same organisms through both
> (Modules 3–4 vs. here) is a great way to understand the trade-offs.

---

## Wrap-up — what you've learned

| You started with… | Tool | You produced… |
|---|---|---|
| Influenza reads (SRA) | IRMA | Assembled genome, subtype, QC report |
| An influenza assembly | GenoFLU | A genotype call (clade/constellation) |
| TB reads (SRA) | vSNP3 | Per-sample VCFs → SNP table + tree |
| An MTBC panel | vSNP3 | A whole-complex phylogeny |
| Bacterial reads | AMRFinderPlus | A resistance-gene profile |
| A bacterial isolate | MLST | A sequence type (ST) |
| Genome accessions | kSNP | A reference-free SNP tree |

**The one pattern to remember:** *create a project → paste accessions → check
samples → Run → read the Results.* Every tool in the suite follows it.

### Where to go next

- **Something won't run?** `bin/bdtools doctor` explains every gap and its fix.
- **Reference databases:** [the README's Reference databases section](../README.md#-reference-databases).
- **Validate your install** against known-good results: `bin/bdtools test all`.
- **Per-tool detail:** each tool has its own README and `docs/` in its repository
  (e.g. vSNP3 ships a comprehensive user guide under `docs/`).

> ⚠️ **A note on interpretation.** These tools report genotypes, resistance
> genotypes, and genetic distances. Turning those into a *diagnostic conclusion*
> is a professional judgment that combines this output with clinical and
> epidemiological context. Two tools in the suite (Bovine MHC Typer, NCBI Submit)
> carry development-status notices on their dashboard cards — heed them.
