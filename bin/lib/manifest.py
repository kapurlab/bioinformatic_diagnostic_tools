#!/usr/bin/env python3
"""manifest.py — read tools.yml without requiring PyYAML.

A deliberately tiny parser for the constrained schema in tools.yml (a top-level
`suite_version` scalar and a `tools:` list of flat dicts whose values are scalars
or inline `[a, b]` lists). Keeping it dependency-free means the umbrella CLI runs
on a bare system python3 — no pip install, no conda env — which matters for the
first-touch install experience.

Commands:
  manifest.py <file> suite_version          -> prints the suite version
  manifest.py <file> names                   -> one tool name per line
  manifest.py <file> get <name> <field>      -> field value (lists space-joined)
  manifest.py <file> set <name> <field> <v>  -> rewrites the field in place
"""
import sys


def parse(path):
    suite_version = ""
    tools = []
    cur = None
    in_tools = False
    with open(path) as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].rstrip("\n")
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            stripped = line.strip()
            if indent == 0:
                in_tools = False
                if stripped.startswith("suite_version:"):
                    suite_version = _scalar(stripped.split(":", 1)[1])
                elif stripped.startswith("tools:"):
                    in_tools = True
                continue
            if not in_tools:
                continue
            if stripped.startswith("- "):
                cur = {}
                tools.append(cur)
                stripped = stripped[2:].strip()
                if not stripped:
                    continue
            if cur is not None and ":" in stripped:
                k, v = stripped.split(":", 1)
                cur[k.strip()] = _value(v.strip())
    return suite_version, tools


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


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: manifest.py <file> <command> [args]")
    path, cmd = sys.argv[1], sys.argv[2]
    suite_version, tools = parse(path)

    if cmd == "suite_version":
        print(suite_version)
    elif cmd == "names":
        for t in tools:
            print(t.get("name", ""))
    elif cmd == "get":
        name, field = sys.argv[3], sys.argv[4]
        for t in tools:
            if t.get("name") == name:
                val = t.get(field, "")
                print(" ".join(val) if isinstance(val, list) else val)
                return
        sys.exit(f"manifest: no tool named {name!r}")
    elif cmd == "set":
        name, field, newval = sys.argv[3], sys.argv[4], sys.argv[5]
        out, in_tools, in_target = [], False, False
        with open(path) as fh:
            for raw in fh:
                body = raw.split("#", 1)[0]
                indent = len(body) - len(body.lstrip())
                s = body.strip()
                if indent == 0:
                    in_tools = s.startswith("tools:")
                    in_target = False
                elif in_tools and s.startswith("- "):
                    in_target = (_value(s[2:].split(":", 1)[1]) == name
                                 if s[2:].startswith("name:") else False)
                elif in_tools and s.startswith("name:"):
                    in_target = (_value(s.split(":", 1)[1]) == name)
                if in_target and s.startswith(f"{field}:"):
                    raw = raw[:indent] + f"{field}: {newval}\n"
                out.append(raw)
        with open(path, "w") as fh:
            fh.writelines(out)
    else:
        sys.exit(f"manifest: unknown command {cmd!r}")


if __name__ == "__main__":
    main()
