#!/usr/bin/env python3
"""readspec.py — read one top-level key from a tests/<tool>/test.yml spec.

Same dependency-free philosophy as bin/lib/manifest.py: the spec is a flat map
of `key: value` lines (scalars, or inline `[a, b]` lists). No nesting. This lets
bin/test.sh stay a plain bash orchestrator on a bare system python3.

  readspec.py <test.yml> <key>     -> prints the value ("" if absent; lists space-joined)
"""
import sys


def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
        return v[1:-1]
    return v


def _value(v):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    return _scalar(v)


def parse(path):
    spec = {}
    with open(path) as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].rstrip("\n")
            if not line.strip() or (len(line) - len(line.lstrip())) != 0:
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                spec[k.strip()] = _value(v)
    return spec


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: readspec.py <test.yml> <key>")
    spec = parse(sys.argv[1])
    val = spec.get(sys.argv[2], "")
    print(" ".join(val) if isinstance(val, list) else val)


if __name__ == "__main__":
    main()
