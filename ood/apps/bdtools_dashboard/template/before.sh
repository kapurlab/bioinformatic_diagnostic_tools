#!/usr/bin/env bash
# Runs in the OOD parent process before script.sh is forked.
# Allocates the dashboard's $port; the per-session secret ($password) is injected
# into script.sh.erb / view.html.erb by the batch_connect "basic" template.

source_helpers

port=$(find_port)
export port

echo "Port — dashboard:${port}"

# OOD renders script.sh.erb without execute permission; fix that.
chmod +x ./script.sh 2>/dev/null || true
