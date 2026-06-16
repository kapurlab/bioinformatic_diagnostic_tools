#!/usr/bin/env python3
"""compare.py — assert a tool's result JSON against a curated expected JSON.

  compare.py <actual_result.json> <expected.json>

`expected.json` holds ONLY the headline fields we validate (not the tool's whole
output). Each key is a dotted path into the actual result; each value is an
expected spec interpreted as:

  exact      "saureus", 8, true            -> equality (numbers compared numerically)
  threshold  ">0", ">=10", "<100", "<=5"   -> numeric comparison
  range      "0.5..0.8"                     -> inclusive numeric range
  approx     "~4400000"                     -> within 5% (or +/-1 for small ints)
  regex      "/2\\.3\\.4\\.4b/"             -> regex search on the string value
  contains   ["mecA", "blaZ"]              -> actual (list or string) must contain each

Exit 0 = all pass; 1 = at least one FAIL; 2 = usage / missing-key error.
Prints one PASS/FAIL line per key so the runner can surface the diff.
"""
import json
import re
import sys

MISSING = object()


def dig(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return MISSING
    return cur


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def check(actual, spec):
    """Return (ok, detail) for one field."""
    # list spec -> containment
    if isinstance(spec, list):
        hay = actual if isinstance(actual, list) else [actual]
        hay_s = [str(h) for h in hay]
        missing = [s for s in spec
                   if s not in hay and not any(str(s) in h for h in hay_s)]
        return (not missing, f"missing {missing}" if missing else "contains all")

    # non-string scalars -> exact
    if not isinstance(spec, str):
        if isinstance(spec, bool):
            return (actual == spec, f"expected {spec}")
        a, s = _num(actual), _num(spec)
        if a is not None and s is not None:
            return (a == s, f"expected {spec}")
        return (actual == spec, f"expected {spec!r}")

    s = spec.strip()
    a = _num(actual)

    for op in (">=", "<=", ">", "<"):
        if s.startswith(op):
            target = _num(s[len(op):])
            if a is None or target is None:
                return (False, f"non-numeric for {op}")
            return ({">": a > target, ">=": a >= target,
                     "<": a < target, "<=": a <= target}[op],
                    f"need {op}{target}")
    if ".." in s:
        lo, hi = (_num(x) for x in s.split("..", 1))
        if a is None or lo is None or hi is None:
            return (False, "non-numeric range")
        return (lo <= a <= hi, f"need {lo}..{hi}")
    if s.startswith("~"):
        target = _num(s[1:])
        if a is None or target is None:
            return (False, "non-numeric approx")
        tol = max(abs(target) * 0.05, 1.0)
        return (abs(a - target) <= tol, f"need ~{target} (+/-{tol:g})")
    if len(s) >= 2 and s.startswith("/") and s.endswith("/"):
        return (re.search(s[1:-1], str(actual)) is not None, f"regex {s}")

    # plain exact (numeric if both numeric, else string)
    sn = _num(s)
    if a is not None and sn is not None:
        return (a == sn, f"expected {s}")
    return (str(actual) == s, f"expected {s!r}")


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: compare.py <actual_result.json> <expected.json>")
    try:
        actual = json.load(open(sys.argv[1]))
    except Exception as e:
        print(f"FAIL  could not read actual result: {e}")
        sys.exit(1)
    expected = json.load(open(sys.argv[2]))

    fails = 0
    for key, spec in expected.items():
        if key.startswith("__"):
            continue
        val = dig(actual, key)
        if val is MISSING:
            print(f"FAIL  {key}: not present in result")
            fails += 1
            continue
        ok, detail = check(val, spec)
        print(f"{'PASS' if ok else 'FAIL'}  {key} = {val!r}  ({detail})")
        fails += 0 if ok else 1

    print(f"--- {len(expected)} checks, {fails} failed ---")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
