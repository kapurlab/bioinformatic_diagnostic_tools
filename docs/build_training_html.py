#!/usr/bin/env python3
"""Build docs/TRAINING.html — a standalone, shareable copy of docs/TRAINING.md.

The output is one self-contained file: no images, no scripts, no local assets.
It links a web font for polish but declares full fallback stacks, so it renders
correctly with no network at all. Hand it to someone by email or on a shared
drive and it just opens.

    pip install "markdown-it-py[linkify]" mdit-py-plugins
    python3 docs/build_training_html.py

Uses a CommonMark parser so the output matches what GitHub renders. (The older
python-markdown needs four-space list continuations; this guide uses three, the
width of "1. ", which GitHub accepts — under python-markdown that silently
flattened numbered steps into prose and turned one FASTA defline into a link.)

Run it after editing TRAINING.md. Paths resolve from this file's location, so it
works from any working directory.
"""
import re
import sys
import html
from pathlib import Path

try:
    from markdown_it import MarkdownIt
    from mdit_py_plugins.anchors import anchors_plugin
except ModuleNotFoundError as exc:
    sys.exit(f"Missing dependency ({exc.name}). Install with:  "
             f'pip install "markdown-it-py[linkify]" mdit-py-plugins')

REPO = "https://github.com/kapurlab/bioinformatic_diagnostic_tools"
HERE = Path(__file__).resolve().parent
SRC = HERE / "TRAINING.md"
OUT = HERE / "TRAINING.html"


def gh_slug(text):
    """GitHub's heading slugs: lowercase, drop punctuation and emoji, each space becomes a hyphen."""
    t = text.strip().lower()
    t = re.sub(r"[^\w\s-]", "", t, flags=re.UNICODE)
    return re.sub(r"\s", "-", t)


md_text = SRC.read_text(encoding="utf-8")

# the pointer to this very file is for the Markdown reader only
md_text = re.sub(r"<!-- html-copy-note:start -->.*?<!-- html-copy-note:end -->\n?",
                 "", md_text, flags=re.S)

# the H1 becomes the page header rather than part of the flow
m = re.match(r"#\s+(.*)\n", md_text)
title_raw = m.group(1).strip() if m else "Training"
if m:
    md_text = md_text[m.end():]

md = MarkdownIt("gfm-like").use(anchors_plugin, max_level=3, slug_func=gh_slug, permalink=False)
body = md.render(md_text)

# a standalone file must not depend on repo-relative paths (docs, slides, PDFs)
body = body.replace('href="../README.md#', f'href="{REPO}/blob/main/README.md#')
body = re.sub(r'href="(?!https?:|#|mailto:)([^"]*?\.(?:md|html|pdf))(#[^"]*)?"',
              lambda mm: f'href="{REPO}/blob/main/docs/{mm.group(1)}{mm.group(2) or ""}"', body)
# wide content scrolls in its own box
body = body.replace("<table>", '<div class="tablewrap"><table>').replace("</table>", "</table></div>")


def strip_tags(t):
    return re.sub(r"<[^>]+>", "", t)


toc_items = [(strip_tags(t).strip(), i)
             for i, t in re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', body, flags=re.S)]

nav = "\n".join(
    f'      <li><a href="#{i}">{html.escape(n)}</a></li>' for n, i in toc_items)

title_txt = strip_tags(title_raw)
doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title_txt)}</title>
<meta name="description" content="Hands-on training for the Kapur Lab bioinformatic diagnostic tools (bdtools): a standalone copy you can share as a single file.">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{
  --ground:#EDF1F0; --surface:#FBFDFC; --surface-2:#E6EBEA; --surface-3:#F2F6F5;
  --ink:#1E2E2D; --ink-2:#566867; --hair:#CBD5D2;
  --accent:#146D6A; --signal:#A06A20; --warn:#9C4B31; --ok:#357F55;
  --r:5px;
}}
@media (prefers-color-scheme: dark){{
  :root:not([data-theme="light"]){{
    --ground:#0F1A1C; --surface:#17262A; --surface-2:#1F3034; --surface-3:#1B2C30;
    --ink:#DFE8E6; --ink-2:#9BADAC; --hair:#2E4145;
    --accent:#6FC5BE; --signal:#E0A75C; --warn:#DE9683; --ok:#6FC28E;
  }}
}}
:root[data-theme="dark"]{{
  --ground:#0F1A1C; --surface:#17262A; --surface-2:#1F3034; --surface-3:#1B2C30;
  --ink:#DFE8E6; --ink-2:#9BADAC; --hair:#2E4145;
  --accent:#6FC5BE; --signal:#E0A75C; --warn:#DE9683; --ok:#6FC28E;
}}
*{{box-sizing:border-box}}
html{{-webkit-text-size-adjust:100%}}
body{{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:"IBM Plex Sans","Helvetica Neue",Helvetica,Arial,sans-serif;
  font-size:17px; line-height:1.62; -webkit-font-smoothing:antialiased;
}}
.wrap{{max-width:1000px; margin:0 auto; padding:0 24px 96px}}
header.page{{
  border-bottom:1px solid var(--hair); margin-bottom:38px;
  padding:44px 0 30px; background:var(--surface);
  border-radius:0 0 var(--r) var(--r);
}}
header.page .inner{{max-width:1000px; margin:0 auto; padding:0 24px}}
.kicker{{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:12px;
  letter-spacing:.19em; text-transform:uppercase; color:var(--accent); margin:0 0 18px;
}}
h1{{
  font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif; font-weight:600;
  font-size:clamp(30px,4.4vw,46px); line-height:1.08; letter-spacing:-.004em; margin:0 0 14px;
}}
.standalone{{
  margin:22px 0 0; padding:14px 18px; border:1px solid var(--hair);
  border-left:4px solid var(--accent); border-radius:var(--r);
  background:var(--surface-3); font-size:15px; color:var(--ink-2);
}}
.standalone b{{color:var(--ink)}}
nav.toc{{
  background:var(--surface); border:1px solid var(--hair); border-radius:var(--r);
  padding:20px 24px; margin:0 0 42px;
}}
nav.toc p{{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:12px; letter-spacing:.15em;
  text-transform:uppercase; color:var(--ink-2); margin:0 0 12px;
}}
nav.toc ul{{list-style:none; margin:0; padding:0; columns:2; column-gap:32px}}
nav.toc li{{margin:0 0 7px; break-inside:avoid}}
nav.toc a{{color:var(--ink); text-decoration:none; border-bottom:1px solid transparent; font-size:15.5px}}
nav.toc a:hover{{border-bottom-color:var(--accent); color:var(--accent)}}
h2{{
  font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif; font-weight:600;
  font-size:29px; line-height:1.16; margin:54px 0 16px; padding-top:20px;
  border-top:1px solid var(--hair);
}}
h3{{font-weight:600; font-size:20px; line-height:1.25; margin:34px 0 10px}}
h4{{font-weight:600; font-size:17px; margin:26px 0 8px; color:var(--ink-2)}}
p{{margin:0 0 16px; max-width:74ch}}
ul,ol{{margin:0 0 18px; padding-left:26px; max-width:74ch}}
li{{margin:0 0 8px}}
li>ul,li>ol{{margin-top:8px}}
li::marker{{color:var(--accent)}}
a{{color:var(--accent); text-decoration:none; border-bottom:1px solid var(--hair); word-break:break-word}}
a:hover{{border-bottom-color:var(--accent)}}
a:focus-visible{{outline:2px solid var(--accent); outline-offset:2px}}
strong{{font-weight:600; color:var(--ink)}}
code{{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:.88em;
  background:var(--surface-2); padding:.14em .38em; border-radius:3px;
}}
pre{{
  margin:0 0 20px; background:var(--surface-3); border:1px solid var(--hair);
  border-left:4px solid var(--accent); border-radius:var(--r);
  padding:14px 18px; overflow-x:auto;
}}
pre code{{background:none; padding:0; font-size:14px; line-height:1.55; white-space:pre}}
pre.highlight{{margin:0 0 20px}}
blockquote{{
  margin:0 0 20px; padding:2px 0 2px 18px; border-left:4px solid var(--signal);
  color:var(--ink-2); max-width:78ch;
}}
blockquote p{{margin:0 0 10px}}
blockquote :last-child{{margin-bottom:0}}
blockquote strong{{color:var(--ink)}}
.tablewrap{{overflow-x:auto; margin:0 0 22px; border:1px solid var(--hair); border-radius:var(--r)}}
table{{border-collapse:collapse; width:100%; font-size:15px}}
th,td{{text-align:left; padding:9px 14px; border-bottom:1px solid var(--hair); vertical-align:top}}
th{{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-weight:500; font-size:12px;
  letter-spacing:.09em; text-transform:uppercase; color:var(--ink-2);
  background:var(--surface-3); white-space:nowrap;
}}
tr:last-child td{{border-bottom:none}}
td code{{white-space:nowrap}}
hr{{border:none; border-top:1px solid var(--hair); margin:44px 0}}
img{{max-width:100%; height:auto}}
footer.page{{
  border-top:1px solid var(--hair); margin-top:56px; padding-top:22px;
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:12px;
  letter-spacing:.1em; text-transform:uppercase; color:var(--ink-2);
}}
#top{{
  position:fixed; right:18px; bottom:18px; background:var(--surface);
  border:1px solid var(--hair); border-radius:var(--r); padding:8px 12px;
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:11px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--ink-2); text-decoration:none;
}}
#top:hover{{color:var(--accent); border-color:var(--accent)}}
@media (max-width:700px){{
  body{{font-size:16px}}
  nav.toc ul{{columns:1}}
  h2{{font-size:24px}}
}}
@media print{{
  :root, :root[data-theme="dark"]{{
    --ground:#fff; --surface:#fff; --surface-2:#EEF2F1; --surface-3:#F6F9F8;
    --ink:#1A2A29; --ink-2:#556666; --hair:#C2CCC9;
    --accent:#12615E; --signal:#8A5A18; --warn:#8C4630; --ok:#2C6A47;
  }}
  body{{font-size:11pt}}
  #top, nav.toc{{display:none}}
  header.page{{border-radius:0}}
  h2{{break-before:page; page-break-before:always}}
  h2:first-of-type{{break-before:auto; page-break-before:auto}}
  h2,h3,h4{{break-after:avoid}}
  pre,blockquote,.tablewrap,table{{break-inside:avoid}}
  a{{border-bottom:none; color:inherit}}
}}
</style>
</head>
<body id="pagetop">
<header class="page">
  <div class="inner">
    <p class="kicker">Kapur Lab - Penn State &middot; USDA &middot; Bioinformatic Diagnostic Tools (bdtools)</p>
    <h1>{title_raw}</h1>
    <p class="standalone"><b>This is a standalone copy.</b> Everything is in this one file &mdash; save it,
    email it, or put it on a shared drive and it will open in any browser with no network and nothing to
    install. The living version is
    <a href="{REPO}/blob/main/docs/TRAINING.md">docs/TRAINING.md</a> in the bdtools repository; check there
    for updates.</p>
  </div>
</header>

<div class="wrap">
  <nav class="toc" aria-label="On this page">
    <p>On this page</p>
    <ul>
{nav}
    </ul>
  </nav>

  <main>
{body}
  </main>

  <footer class="page">
    vSNP3 &middot; Kapur Lab &middot; Penn State &middot; USDA &middot;
    <a href="{REPO}">{REPO.replace('https://','')}</a>
  </footer>
</div>
<a id="top" href="#pagetop">&uarr; Top</a>
</body>
</html>
"""
OUT.write_text(doc, encoding="utf-8")
print(f"wrote {OUT} ({len(doc)//1024} KB, {len(toc_items)} sections)")
