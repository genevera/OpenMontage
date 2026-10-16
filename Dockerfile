# syntax=docker/dockerfile:1

# OpenMontage — containerized runtime (Backlot server).
#
# Best-practice notes (Docker official docs via Context7):
# - slim base for the final runtime, dependency manifests copied before
#   source so layer cache survives source edits.
# - non-privileged runtime user (UID 10001, `appuser`).
# - BuildKit cache mounts for pip / npm to speed rebuilds.

FROM python:3.10-slim-bookworm AS final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=0 \
    BACKLOT_PORT=4750 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# System runtime deps: ffmpeg (compose/thumbnails), Node 22 (Remotion +
# HyperFrames; HyperFrames requires Node >= 22), curl (nodesource setup).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && node --version \
    && ffmpeg -version | head -1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (cache-friendly layer).
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install -r requirements.txt

# Remotion composer deps first, then source (cache-friendly layer).
COPY remotion-composer/package.json remotion-composer/package-lock.json* ./remotion-composer/
RUN --mount=type=cache,target=/root/.npm \
    sh -c 'cd remotion-composer && (npm ci || npm install)'

# Full source last — edits here do not invalidate the layers above.
COPY . .

# Best-effort HyperFrames CLI cache-warm; offline or npm failure must not
# break the image build (first render fetches on demand instead).
RUN (npx --yes hyperframes --version || echo "[skip] hyperframes cache-warm failed") \
    && python -c "from tools.video.hyperframes_compose import HyperFramesCompose; print(HyperFramesCompose().execute({'operation':'doctor'}).data)" || true

# Non-privileged runtime user (Docker best practice: don't run as root).
# appuser gets a real writable home: tooling running as appuser (pip cache,
# npm/npx cache, huggingface_hub) needs $HOME to be writable; "/nonexistent"
# breaks all of them (pip dies with Errno 13).
ARG UID=10001
RUN adduser \
        --disabled-password \
        --gecos "" \
        --home "/home/appuser" \
        --shell "/bin/bash" \
        --uid "${UID}" \
        appuser \
    && mkdir -p /app/projects /app/output /app/music_library /app/.backlot \
    && chown -R appuser:appuser /app /home/appuser
USER appuser

EXPOSE 4750

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4750/api/health', timeout=5)"

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["python", "-m", "backlot", "serve", "--port", "4750"]
