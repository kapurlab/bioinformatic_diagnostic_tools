#!/usr/bin/env python3
"""Draw the bdtools launcher icon and package it for every desktop we ship to.

One artwork, three containers. macOS needs a multi-resolution .icns (Finder will
not take a PNG for a bundle icon), Linux takes plain PNGs named by size, Windows
(a WSL install surfaced as a Start Menu shortcut) takes a .ico. Generating all
three from the same master is the point: the alternative is three files that drift
until the Mac icon is a version behind everyone else's.

    bin/make-icons.py                 # regenerate templates/launcher/icons/
    bin/make-icons.py --out DIR       # write somewhere else

Run this only when the artwork changes. The generated files are committed, so a
user machine never needs Pillow — which is why this is a developer script and not
part of `bdtools make-launcher`.

Requires Pillow (any tool env has it: <tool>/env/bin/python bin/make-icons.py).
"""
import argparse
import math
import os
import struct
import sys

try:
    from PIL import Image, ImageDraw, ImageFilter
except ModuleNotFoundError:
    sys.exit("this script needs Pillow — run it with a tool env python, "
             "e.g. ~/.local/share/bdtools/checkouts/mlst_gui/env/bin/python")

# The suite's palette, taken from the dashboard's own CSS so the icon and the page
# it opens are recognisably the same product: deep slate ground, terracotta accent
# (--accent), sage secondary (--accent2), warm parchment for the strands (--bg).
GROUND_TOP = (26, 42, 52)
GROUND_BOTTOM = (14, 24, 31)
STRAND_A = (219, 138, 108)      # terracotta
STRAND_B = (118, 174, 131)      # sage
RUNG = (246, 243, 238, 138)     # parchment, semi-transparent
SNP = (255, 214, 138)           # the highlighted base — a SNP, which is the point

SS = 4                          # supersample factor; the curves need it
MASTER = 1024

# macOS icon type -> pixel size. ic04/ic05 are the small 16/32 slots that Finder
# list view and the menu bar actually use; without them macOS downsamples 512px
# artwork and it looks muddy at 16px.
ICNS_TYPES = [
    (b"icp4", 16), (b"icp5", 32), (b"icp6", 64),
    (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
    (b"ic10", 1024), (b"ic11", 32), (b"ic12", 64),
    (b"ic13", 256), (b"ic14", 512),
]
PNG_SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=radius, fill=255)
    return mask


def _ground(size):
    """Vertical gradient inside a macOS-proportioned rounded square."""
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel((0, y), tuple(
            round(GROUND_TOP[i] + (GROUND_BOTTOM[i] - GROUND_TOP[i]) * t)
            for i in range(3)
        ))
    base = grad.resize((size, size), Image.BICUBIC).convert("RGBA")
    base.putalpha(_rounded_mask(size, radius=int(size * 0.225)))
    return base


def _ribbon(draw, pts, width_at, fill, outline=None, outline_w=0):
    """Fill one strand arc as a smooth ribbon.

    A ribbon, not a run of stroked segments. Stroking each segment at its own
    width leaves a visible stair on both edges — at 1024px it reads as fur. Here
    the two edges are computed as offset curves and filled as a single polygon, so
    the silhouette is as smooth as the sampling.
    """
    left, right = [], []
    for (x, y, d) in pts:
        w = width_at(d) / 2
        left.append((x - w, y))
        right.append((x + w, y))
    poly = left + right[::-1]
    if outline:
        wide = ([(x - outline_w, y) for (x, y) in left]
                + [(x + outline_w, y) for (x, y) in right][::-1])
        draw.polygon(wide, fill=outline)
    draw.polygon(poly, fill=fill)
    # Cap both ends; a polygon ends square and the flat cut is obvious.
    for (x, y, d) in (pts[0], pts[-1]):
        r = width_at(d) / 2
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def _helix(size):
    """Two strands and their rungs — a double helix seen side-on.

    The strands must actually weave: at each half turn one passes in front of the
    other. That is done by cutting both strands at their crossings and drawing the
    resulting arcs far-to-near, each near arc carrying a thin ground-coloured
    outline so it reads as passing over the one behind it. Depth also drives width.
    """
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx = size / 2
    top, bottom = size * 0.17, size * 0.83
    span = bottom - top
    amp = size * 0.185
    turns = 2.25
    steps = 720
    base_w = size * 0.058

    def width_at(d):
        return base_w * (1.0 + 0.30 * d)

    def strand(phase):
        out = []
        for i in range(steps + 1):
            t = i / steps
            ang = 2 * math.pi * turns * t + phase
            out.append((cx + amp * math.sin(ang), top + span * t, math.cos(ang)))
        return out

    a, b = strand(0.0), strand(math.pi)

    # Rungs: behind both strands, so a crossing hides them the way it should.
    # Skipped near a crossing, where the two strands are nearly co-linear and a
    # rung would be a smear rather than a rung.
    rungs = []
    for i in range(0, steps + 1, steps // 11):
        (x0, y0, d0), (x1, y1, _) = a[i], b[i]
        if abs(x1 - x0) < size * 0.07:
            continue
        rungs.append((i, x0, y0, x1, y1, d0))
    for (_, x0, y0, x1, y1, d0) in rungs:
        w = max(1, int(size * (0.014 if d0 > 0 else 0.010)))
        draw.line([(x0, y0), (x1, y1)], fill=RUNG, width=w)

    # Cut each strand at its crossings (sign changes in depth), then draw the arcs
    # far first. Overlap order is what creates the weave.
    arcs = []
    for pts, colour in ((a, STRAND_A), (b, STRAND_B)):
        start = 0
        for i in range(1, len(pts) + 1):
            end = i == len(pts)
            if end or (pts[i][2] >= 0) != (pts[start][2] >= 0):
                seg = pts[start:i + (0 if end else 1)]
                if len(seg) > 1:
                    depth = sum(p[2] for p in seg) / len(seg)
                    arcs.append((depth, seg, colour))
                start = i
    for depth, seg, colour in sorted(arcs, key=lambda x: x[0]):
        _ribbon(draw, seg, width_at, colour,
                outline=(*GROUND_BOTTOM, 235) if depth > 0 else None,
                outline_w=size * 0.011)

    # One base pair called out in gold: the single-nucleotide difference this whole
    # suite exists to find. Drawn last so it survives every overlap above.
    mid = min(rungs, key=lambda r: abs(r[0] - steps / 2))
    _, x0, y0, x1, y1, _ = mid
    draw.line([(x0, y0), (x1, y1)], fill=(*SNP, 255),
              width=max(2, int(size * 0.026)))
    for (x, y) in ((x0, y0), (x1, y1)):
        r = size * 0.030
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(*SNP, 255))
    return layer


def master(size=MASTER):
    """The artwork, at `size`, supersampled and composited."""
    big = size * SS
    base = _ground(big)
    helix = _helix(big)
    # A soft drop shadow under the helix separates it from the ground without a
    # hard outline, which would alias badly when downsampled to 16px.
    shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 110), (0, int(big * 0.012)), helix.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(big * 0.012))
    out = Image.alpha_composite(base, shadow)
    out = Image.alpha_composite(out, helix)
    return out.resize((size, size), Image.LANCZOS)


def write_icns(img, path):
    """Write an .icns containing PNG payloads for each slot macOS asks for.

    The format is a header plus TOC-free type/length/data chunks, and every modern
    macOS reads PNG payloads, so this needs no iconutil and therefore no Mac.
    """
    chunks = []
    for kind, size in ICNS_TYPES:
        from io import BytesIO
        buf = BytesIO()
        img.resize((size, size), Image.LANCZOS).save(buf, format="PNG")
        data = buf.getvalue()
        chunks.append(kind + struct.pack(">I", len(data) + 8) + data)
    body = b"".join(chunks)
    with open(path, "wb") as fh:
        fh.write(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.join(here, "templates/launcher/icons"))
    args = ap.parse_args()
    out = args.out
    os.makedirs(out, exist_ok=True)

    img = master()
    img.save(os.path.join(out, "bdtools-dashboard.png"))          # the master
    write_icns(img, os.path.join(out, "bdtools-dashboard.icns"))  # macOS
    for size in PNG_SIZES:                                        # Linux
        img.resize((size, size), Image.LANCZOS).save(
            os.path.join(out, f"bdtools-dashboard-{size}.png"))
    img.resize((256, 256), Image.LANCZOS).save(                   # Windows / WSL
        os.path.join(out, "bdtools-dashboard.ico"),
        sizes=[(s, s) for s in ICO_SIZES])
    print(f"wrote icons to {out}")


if __name__ == "__main__":
    main()
