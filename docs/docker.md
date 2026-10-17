# Docker Deployment

OpenMontage ships containerized CPU and GPU runtimes. Both serve the Backlot
board on port 4750 and persist work through bind mounts.

## Images

| Image (target)      | Base                                              | Use for                                     |
|---------------------|---------------------------------------------------|---------------------------------------------|
| `Dockerfile`        | `python:3.10-slim` + FFmpeg + Node 22             | Backlot board, cloud-provider pipelines     |
| `Dockerfile.gpu`    | `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04` + torch `cu128` | Local video generation (wan/hunyuan/ltx2/cogvideo) |

The GPU base tracks the same CUDA 12.8 torch wheels the host
`make install-gpu` flow installs. Both images run as a non-root `appuser`
with a writable home directory.

## Services (`docker-compose.yml`)

| Service   | Profile | GPU | Use for                          |
|-----------|---------|-----|----------------------------------|
| `app`     | default | no  | Board + cloud providers          |
| `app-gpu` | `gpu`   | yes (`gpus: all`) | Board + local GPU generation |

## Makefile targets

All Docker operations are available as `make` targets:

| Target             | Action                                          |
|--------------------|-------------------------------------------------|
| `docker-build`     | Build the CPU image (`openmontage:latest`)      |
| `docker-build-gpu` | Build the GPU image (`openmontage:gpu`)         |
| `docker-up`        | Start the CPU service (detached, with build)    |
| `docker-up-gpu`    | Start the GPU service (detached, with build)    |
| `docker-down`      | Stop and remove containers                      |
| `docker-logs`      | Tail container logs                             |
| `docker-clean`     | Stop containers and remove images + volumes     |

Equivalent to the raw compose commands:

```bash
# CPU
docker compose up -d --build app

# GPU (opt-in profile)
docker compose --profile gpu up -d --build app-gpu
```

## Prerequisites

- Docker Engine + Compose v2.
- For `app-gpu`: the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  on the host and a CUDA-12.8-capable driver.

## Configuration

- The container runs as your host user's UID/GID (auto-detected by the
  Makefile via `id -u` / `id -g`). Files written to `./projects`, `./output`,
  and `./music_library` are owned by your user — no root or mismatched-UID
  files. Override with `make docker-up UID=1234 GID=1234` if needed.
- `BACKLOT_PORT` (default `4750`) sets the served port.
- `.env` is picked up automatically when present (`required: false`, so a
  keyless checkout still works). Every key is optional.
- The server binds `0.0.0.0` inside the container (Backlot defaults to
  loopback, which is unreachable from outside the container network
  namespace). `BACKLOT_HOST` overrides this if you need something else.

## Volumes

| Host path         | Container path      | Notes                          |
|-------------------|---------------------|--------------------------------|
| `./projects`      | `/app/projects`     | Productions (created if missing)|
| `./output`        | `/app/output`       | Renders (created if missing)   |
| `./music_library` | `/app/music_library`| Read-only                      |

## Local-only mode (`docker-compose.local.yml`)

To guarantee no cloud calls even when `.env` holds live keys, stack the
override — it blanks every remote credential with empty values:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml --profile gpu up -d --build app-gpu
```

Deliberately preserved by the override:

- `HF_TOKEN` — used for gated model-weight downloads (local inference input,
  not a cloud API call).
- `VIDEO_GEN_LOCAL_*` — local engine selection
  (`VIDEO_GEN_LOCAL_ENABLED=true` plus `VIDEO_GEN_LOCAL_MODEL`, e.g.
  `wan2.1-14b`).
- `DOUBAO_SPEECH_VOICE_TYPE` — plain voice-id config, not a credential.

The file contains no secrets, only empty values, so it is safe to commit.

## Health and endpoints

- `GET /api/health` → `{"ok":true,"app":"backlot"}`. Both services define a
  Compose `healthcheck` against it with a 20s start period.
- `GET /` → Backlot board (read-only production monitor).
- `GET /api/projects`, `/api/project/{id}/state`, SSE event streams, `/thumb/...`,
  `/media/...` — same API as the host run.

## First GPU run

Local engines download model weights on first use. Expect a slow, large
first generation; subsequent runs reuse the cached weights. (Mounting a
persistent Hugging Face cache into the container avoids re-downloading when
the container is recreated — not wired by default.)

## Verifying inside the images

The images ship the app dependencies; install dev dependencies at test time
and run the suite in the image's own Python:

```bash
docker build -f Dockerfile -t openmontage:cpu-test .
docker run --rm openmontage:cpu-test \
  bash -lc "pip install -q -r requirements-dev.txt && python -m pytest tests/ -q"

docker build -f Dockerfile.gpu -t openmontage:gpu-test .
docker run --rm --gpus all openmontage:gpu-test \
  bash -lc "pip install -q -r requirements-dev.txt && python -m pytest tests/ -q"
```

`tests/docker/test_docker_config.py` covers the static config (both
Dockerfiles, compose services, local override semantics) plus live
`docker compose config` validation where a Docker CLI is available.
