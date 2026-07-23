#!/usr/bin/env python3
"""Safely signal an already-running local bdtools dashboard.

The dashboard writes a mode-0600 state file containing its PID, port, and
control token. This helper avoids broad ``pkill`` patterns and routes stop /
restart through the dashboard's active-analysis guard.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("state_file")
    parser.add_argument("action", choices=("stop", "restart"))
    args = parser.parse_args()

    try:
        with open(args.state_file, encoding="utf-8") as handle:
            state = json.load(handle)
        pid = int(state["pid"])
        port = int(state["port"])
        token = str(state["control_token"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return 3  # no usable running-dashboard record

    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        try:
            os.unlink(args.state_file)
        except OSError:
            pass
        return 3

    endpoint = "shutdown" if args.action == "stop" else "restart"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/{endpoint}",
        method="POST",
        headers={"X-Bdtools-Control": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {"error": body or str(exc)}
        print(payload.get("error", str(exc)), file=sys.stderr)
        for job in payload.get("active", []):
            print(
                f"  active: {job.get('tool')} — "
                f"{job.get('name') or job.get('id') or 'job'} ({job.get('status')})",
                file=sys.stderr,
            )
        for error in payload.get("errors", []):
            print(
                f"  could not verify: {error.get('tool')} — {error.get('error')}",
                file=sys.stderr,
            )
        return 2
    except (OSError, ValueError) as exc:
        print(f"could not contact the recorded dashboard: {exc}", file=sys.stderr)
        return 2

    if not (payload.get("stopping") or payload.get("restarting")):
        print("dashboard did not acknowledge the request", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
