#!/bin/sh
# OpenMontage container entrypoint.
# Creates a `.env` from `.env.example` when neither the file nor a mounted
# env exists, then execs the container CMD (Backlot server by default).
set -eu

if [ ! -f /app/.env ] && [ -f /app/.env.example ] && [ -z "${FAL_KEY:-}${GOOGLE_API_KEY:-}${OPENAI_API_KEY:-}${ELEVENLABS_API_KEY:-}" ]; then
	cp /app/.env.example /app/.env 2>/dev/null || true
fi

exec "$@"
