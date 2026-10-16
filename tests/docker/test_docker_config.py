"""Docker deployment config tests (static, daemon-free, no network).

Validates the Dockerfile / Compose / .dockerignore / entrypoint contract
without requiring a Docker daemon: every test here parses files on disk.
A live `docker compose config` check runs only when the `docker` CLI is
present, otherwise it skips.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"


def _dockerfile_text() -> str:
    assert DOCKERFILE.is_file(), "Dockerfile is missing"
    return DOCKERFILE.read_text(encoding="utf-8")


def _compose() -> dict:
    assert COMPOSE_FILE.is_file(), "docker-compose.yml is missing"
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


class TestDockerfile:
    def test_base_image_python_310_slim(self):
        text = _dockerfile_text()
        assert "python:3.10-slim" in text

    def test_dependency_layer_caching_order(self):
        """Manifests (requirements, package.json) must be copied before source."""
        text = _dockerfile_text()
        req_copy = text.index("COPY requirements.txt")
        pkg_copy = text.index("remotion-composer/package.json")
        pip_install = text.index("pip install -r requirements.txt")
        src_copy = text.index("COPY . .")
        assert req_copy < pip_install < src_copy
        assert pkg_copy < src_copy

    def test_system_deps_ffmpeg_node22(self):
        text = _dockerfile_text()
        assert "ffmpeg" in text
        assert "node_22.x" in text or "node:22" in text or "nodejs" in text
        assert "remotion-composer" in text
        assert "hyperframes" in text.lower()

    def test_non_root_user(self):
        text = _dockerfile_text()
        assert "appuser" in text
        # A `USER appuser` (or `USER <uid>`) directive must exist.
        assert "\nUSER " in text

    def test_backlot_runtime_contract(self):
        text = _dockerfile_text()
        assert "EXPOSE 4750" in text
        assert "HEALTHCHECK" in text
        assert "backlot" in text
        assert "4750" in text

    def test_no_secrets_baked_in(self):
        text = _dockerfile_text()
        assert "COPY .env" not in text
        for secret in (
            "FAL_KEY=",
            "OPENAI_API_KEY=",
            "ELEVENLABS_API_KEY=",
            "GOOGLE_API_KEY=",
            "ATLASCLOUD_API_KEY=",
        ):
            assert secret not in text


class TestCompose:
    def test_service_and_build(self):
        cfg = _compose()
        assert "services" in cfg and "app" in cfg["services"]
        app = cfg["services"]["app"]
        assert app["build"]["dockerfile"] == "Dockerfile"
        assert app["build"]["context"] == "."

    def test_env_file_optional(self):
        """Long-form env_file with required:false (Context7 pattern)."""
        cfg = _compose()
        env_file = cfg["services"]["app"]["env_file"]
        assert {"path": ".env", "required": False} in env_file

    def test_volumes_cover_regenerables(self):
        cfg = _compose()
        targets = [
            v.get("target", "")
            for v in cfg["services"]["app"]["volumes"]
            if isinstance(v, dict)
        ]
        assert "/app/projects" in targets
        assert "/app/output" in targets
        assert "/app/music_library" in targets

    def test_backlot_port_and_healthcheck(self):
        cfg = _compose()
        app = cfg["services"]["app"]
        assert "4750:4750" in app["ports"]
        hc = app["healthcheck"]
        assert "test" in hc
        assert hc.get("start_period") is not None
        assert str(hc["test"]).find("4750") != -1 or "health" in str(hc["test"])

    def test_no_secrets_in_environment(self):
        cfg = _compose()
        env = cfg["services"]["app"].get("environment") or {}
        flat = " ".join(
            list(env.keys()) + [str(v) for v in env.values()]
            if isinstance(env, dict)
            else [str(v) for v in env]
        )
        for secret in (
            "FAL_KEY",
            "OPENAI_API_KEY",
            "ELEVENLABS_API_KEY",
            "ATLASCLOUD_API_KEY",
            "sk-",
        ):
            assert secret not in flat


class TestGpuVariant:
    DOCKERFILE_GPU = REPO_ROOT / "Dockerfile.gpu"

    def test_gpu_dockerfile_cuda_and_torch(self):
        assert self.DOCKERFILE_GPU.is_file(), "Dockerfile.gpu is missing"
        text = self.DOCKERFILE_GPU.read_text(encoding="utf-8")
        assert "nvidia/cuda" in text
        # Must track the cu128 torch wheels (see scripts/wan_disk_cleanup.sh):
        # CUDA 12.4 is also too old for Blackwell GPUs (RTX 50-series need 12.8+).
        assert "12.8" in text
        assert "requirements-gpu.txt" in text
        assert "diffusers" in text
        assert "COPY .env" not in text
        assert "\nUSER " in text

    def test_compose_gpu_profile_opt_in(self):
        cfg = _compose()
        assert "app-gpu" in cfg["services"]
        gpu = cfg["services"]["app-gpu"]
        assert "gpu" in gpu.get("profiles", [])
        assert gpu.get("gpus") == "all"
        assert gpu["build"]["dockerfile"] == "Dockerfile.gpu"
        env = gpu.get("environment") or {}
        flat = " ".join(
            list(env.keys()) + [str(v) for v in env.values()]
            if isinstance(env, dict)
            else [str(v) for v in env]
        )
        assert "VIDEO_GEN_LOCAL_ENABLED" in flat

    def test_cpu_dockerfile_appuser_has_writable_home(self):
        text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        gpu_text = (REPO_ROOT / "Dockerfile.gpu").read_text(encoding="utf-8")
        for name, body in (("Dockerfile", text), ("Dockerfile.gpu", gpu_text)):
            # Regression: --home /nonexistent breaks pip/npm/HF caches as
            # appuser (Errno 13), which killed in-image pytest runs.
            assert '--home "/nonexistent"' not in body, (
                f"{name} must not point appuser at /nonexistent"
            )
            assert "--no-create-home" not in body, f"{name} must create appuser's home"
            assert "/home/appuser" in body, f"{name} must give appuser a home"

    def test_cpu_service_has_no_gpus(self):
        cfg = _compose()
        assert "gpus" not in cfg["services"]["app"]

    def test_services_bind_all_interfaces(self):
        # Regression: Backlot defaults to 127.0.0.1, which is unreachable
        # from outside the container netns (host gets 502 via Caddy).
        cfg = _compose()
        for name in ("app", "app-gpu"):
            cmd = cfg["services"][name].get("command", [])
            flat = " ".join(str(v) for v in cmd)
            assert "0.0.0.0" in flat, f"{name} must bind 0.0.0.0"


class TestSupportFiles:
    def test_dockerignore_exists_and_excludes_env(self):
        assert DOCKERIGNORE.is_file(), ".dockerignore is missing"
        text = DOCKERIGNORE.read_text(encoding="utf-8")
        assert ".env" in text
        assert "node_modules" in text

    def test_entrypoint_exists_executable_and_execs_cmd(self):
        assert ENTRYPOINT.is_file(), "docker/entrypoint.sh is missing"
        mode = ENTRYPOINT.stat().st_mode
        assert mode & stat.S_IXUSR, "entrypoint.sh is not executable"
        text = ENTRYPOINT.read_text(encoding="utf-8")
        assert 'exec "$@"' in text


class TestLiveComposeConfig:
    def test_docker_compose_config_validates(self):
        docker = shutil.which("docker")
        if docker is None:
            pytest.skip("docker CLI not available")
        proc = subprocess.run(
            [docker, "compose", "-f", str(COMPOSE_FILE), "config"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO_ROOT),
        )
        assert proc.returncode == 0, f"docker compose config failed:\n{proc.stderr}"
        assert "4750" in proc.stdout

    def test_docker_compose_gpu_profile_config_validates(self):
        docker = shutil.which("docker")
        if docker is None:
            pytest.skip("docker CLI not available")
        proc = subprocess.run(
            [docker, "compose", "-f", str(COMPOSE_FILE), "--profile", "gpu", "config"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO_ROOT),
        )
        assert proc.returncode == 0, f"gpu profile config failed:\n{proc.stderr}"
        assert "app-gpu" in proc.stdout
