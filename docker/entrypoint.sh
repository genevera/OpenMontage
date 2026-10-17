#!/bin/sh
# OpenMontage container entrypoint.
#
# Canonical Docker non-root pattern (per Docker official docs):
#   1. Run as root, initialize writable dirs / chown bind mounts
#   2. Drop privileges to the host user's UID/GID via `gosu`
#   3. Add a /etc/passwd entry so tools needing a real user (pip, npm, etc.) work
#
# The container starts as root (no USER instruction in the Dockerfile).
# The entrypoint detects the host UID/GID from the mounted projects dir,
# chowns the volume dirs to match, creates a matching user entry, then
# drops privileges with `gosu` before exec'ing the CMD.
#
# Env overrides: HOST_UID, HOST_GID (fall back to stat of /app/projects,
# then to build-time ARG defaults of 10001).
set -eu

# --- 1. Create .env from .env.example when needed (original behavior) ---
if [ ! -f /app/.env ] && [ -f /app/.env.example ] && [ -z "${FAL_KEY:-}${GOOGLE_API_KEY:-}${OPENAI_API_KEY:-}${ELEVENLABS_API_KEY:-}" ]; then
	cp /app/.env.example /app/.env 2>/dev/null || true
fi

# --- 2. Detect host UID/GID (canonical Docker pattern) ---
HOST_UID="${HOST_UID:-}"
HOST_GID="${HOST_GID:-}"

if [ -z "$HOST_UID" ] && [ -d /app/projects ]; then
	HOST_UID=$(stat -c '%u' /app/projects)
fi
if [ -z "$HOST_GID" ] && [ -d /app/projects ]; then
	HOST_GID=$(stat -c '%g' /app/projects)
fi

# Fall back to build-time ARG defaults (10001). Never default to UID 0
# (root) — if stat returned root (e.g. bind mount created by Docker on
# first run), use the safe default instead.
if [ -z "${HOST_UID:-}" ] || [ "$HOST_UID" = "0" ]; then
	HOST_UID=10001
fi
if [ -z "${HOST_GID:-}" ] || [ "$HOST_GID" = "0" ]; then
	HOST_GID=10001
fi

# --- 3. As root: chown volume dirs, create user, drop privileges ---
if [ "$(id -u)" = "0" ]; then
	# Create a matching group + user (idempotent)
	addgroup --gid "$HOST_GID" appuser-host 2>/dev/null || true
	adduser \
		--uid "$HOST_UID" \
		--gid "$HOST_GID" \
		--disabled-password \
		--gecos "" \
		--home /tmp \
		--shell /bin/bash \
		appuser-host 2>/dev/null || true

	# chown bind-mounted volume dirs so files are owned by the host user
	chown -R "$HOST_UID:$HOST_GID" /app/projects /app/output /app/music_library /app/.backlot 2>/dev/null || true

	export HOME=/tmp
	exec gosu appuser-host "$@"
fi

# Not root — already running as the right user (e.g. arbitrary UID in OpenShift)
exec "$@"
