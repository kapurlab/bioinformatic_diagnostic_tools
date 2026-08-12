#!/usr/bin/env bash
# Runs in the OOD parent process before script.sh is forked.
# Allocates the dashboard's $port and mints the per-session secret ($password).

source_helpers

port=$(find_port)
export port

# The per-session secret. This file used to claim the batch_connect "basic"
# template generates $password — it does not (only the vnc template does), so
# the token arrived empty, the dashboard's AuthMiddleware skipped its token
# wall entirely, and every session served 0.0.0.0 unauthenticated. Mint it
# here; create_passwd is the OOD helper (source_helpers above), with a
# /dev/urandom fallback so a helper-less environment still gets a secret.
if [[ -z "${password:-}" ]]; then
  password="$(create_passwd 2>/dev/null || tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 32)"
fi
export password

echo "Port — dashboard:${port}"

# OOD renders script.sh.erb without execute permission; fix that.
chmod +x ./script.sh 2>/dev/null || true
