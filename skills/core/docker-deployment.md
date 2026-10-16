# Docker Deployment Skill

## When to Use

Use the containerized runtimes when the user asks to run OpenMontage in
Docker, expose the Backlot board on the network, or guarantee a local-only
deployment. Two services cover both needs — default to them instead of
inventing custom `docker run` invocations.

## Services (`docker-compose.yml`)

| Service   | Profile | GPU | Command                                                      |
|-----------|---------|-----|--------------------------------------------------------------|
| `app`     | default | no  | Backlot board + cloud-provider pipelines on `:4750`          |
| `app-gpu` | `gpu`   | yes (`gpus: all`) | Board + local GPU generation on `:4750`          |

```bash
# CPU
docker compose up -d --build app

# GPU (opt-in profile; needs NVIDIA Container Toolkit on the host)
docker compose --profile gpu up -d --build app-gpu
```

## Key Patterns

### Images track the host toolchains

- `Dockerfile` (CPU): `python:3.10-slim` + FFmpeg + Node 22.
- `Dockerfile.gpu`: `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04` + torch
  `cu128` — the same CUDA/torch combination the host `make install-gpu`
  flow installs. If `requirements-gpu.txt` moves to a newer CUDA, bump the
  base image to match.
- Both images run as non-root `appuser` **with a real writable home**
  (`/home/appuser`). Never point the home at `/nonexistent`: pip, npm/npx,
  and huggingface caches need a writable `$HOME`, and without one even
  dependency installs die with `Errno 13`.

### Backlot must bind 0.0.0.0 in containers

`backlot serve` defaults to loopback, which is unreachable from outside the
container network namespace (the host gets 502 while the in-container
healthcheck passes). Both compose services pass `--host 0.0.0.0`
(`BACKLOT_HOST` env also works). Any new service fronting Backlot must do
the same — `tests/docker/test_docker_config.py::test_services_bind_all_interfaces`
enforces it.

### Local-only override (`docker-compose.local.yml`)

Stack the override to blank every remote credential when `.env` holds live
keys — the file contains empty values only, no secrets:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml --profile gpu up -d --build app-gpu
```

Preserved on purpose: `HF_TOKEN` (gated weight downloads for local
inference), `VIDEO_GEN_LOCAL_*` (engine selection), `DOUBAO_SPEECH_VOICE_TYPE`
(voice-id config, not a credential).

### Persistence and health

- Bind mounts: `./projects` → `/app/projects`, `./output` → `/app/output`,
  `./music_library` → `/app/music_library` (read-only). Host paths are
  created if missing.
- `GET /api/health` → `{"ok":true,"app":"backlot"}`; both services define a
  Compose `healthcheck` against it with a 20s start period.
- First GPU generation downloads model weights — warn about a slow, large
  first run.

## Verifying

- Static + live config: `pytest tests/docker/test_docker_config.py` (live
  `compose config` checks skip where no Docker CLI exists).
- Full suite inside the image's own Python (install dev deps at test time):
  `pip install -q -r requirements-dev.txt && python -m pytest tests/ -q`.
- Human reference: `docs/docker.md`. User-facing summary: `README.md`
  "Run in Docker".
