"""Fixture e2e: sobe o stack compose REAL num projeto isolado (classup-e2e).

Isolamento: `-p classup-e2e` cria containers/volumes do projeto e2e
(classup-e2e_*); o teardown `down -v` remove SÓ esses volumes — o stack/dados
de dev do usuário não são tocados. Porta da API publicada em 18001 para não
colidir com o dev (8001).

Limitação consciente: o compose fixa container_name (classup-api etc.), que é
único por host — se o stack de DEV estiver rodando, o e2e é PULADO com
instrução, em vez de derrubar/conflitar com o ambiente do usuário.
"""

import json
import os
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest

PROJECT = "classup-e2e"
API_PORT = "18001"
BASE_URL = f"http://127.0.0.1:{API_PORT}"

_COMPOSE = ["docker", "compose", "-p", PROJECT]
_ENV = {**os.environ, "API_PORT": API_PORT}


def _run(
    args: list[str], timeout: int = 60, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, env=_ENV, capture_output=True, text=True, timeout=timeout, check=check
    )


def _health(container: str) -> str:
    result = _run(
        ["docker", "inspect", "-f", "{{.State.Health.Status}}", container],
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "absent"


def _wait_healthy(container: str, timeout_s: int) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = _health(container)
        if status == "healthy":
            return
        time.sleep(3)
    raise TimeoutError(
        f"{container} não ficou healthy em {timeout_s}s (status: {_health(container)})"
    )


def _dev_stack_running() -> bool:
    # container_name é único por host: se um classup-api já existe, distingue
    # pelo label de projeto do compose. Sobra do PRÓPRIO e2e (teardown que
    # falhou) é removida e o teste segue; stack de DEV em pé => skip (não
    # derrubamos o ambiente do usuário).
    result = _run(
        [
            "docker",
            "inspect",
            "-f",
            '{{index .Config.Labels "com.docker.compose.project"}}',
            "classup-api",
        ],
        check=False,
    )
    if result.returncode != 0:
        return False  # não existe container classup-api
    if result.stdout.strip() == PROJECT:
        # Sobra de uma execução e2e anterior — limpa e prossegue.
        _run([*_COMPOSE, "down", "-v", "--remove-orphans"], timeout=180, check=False)
        return False
    return True


@pytest.fixture(scope="session")
def stack() -> Iterator[str]:
    if _dev_stack_running():
        pytest.skip(
            "stack de dev (classup-api) em execução — pare-o "
            "(docker compose down) para rodar o e2e"
        )
    try:
        # Build + up: primeiro build é demorado (imagem + poetry install).
        _run([*_COMPOSE, "up", "-d", "--build"], timeout=900)
        # api healthy implica db/redis/redis-celery/minio healthy (depends_on).
        _wait_healthy("classup-api", timeout_s=300)
        # worker: healthcheck = celery inspect ping real pelo broker.
        _wait_healthy("classup-worker", timeout_s=180)
        yield BASE_URL
    finally:
        _run([*_COMPOSE, "down", "-v", "--remove-orphans"], timeout=180, check=False)


def compose_exec(service: str, *cmd: str, timeout: int = 30) -> tuple[int, str]:
    """Executa um comando dentro de um serviço do stack e2e."""
    result = _run(
        [*_COMPOSE, "exec", "-T", service, *cmd], timeout=timeout, check=False
    )
    return result.returncode, result.stdout + result.stderr


def container_state(container: str) -> dict[str, Any]:
    result = _run(["docker", "inspect", container], check=False)
    if result.returncode != 0:
        return {}
    state: dict[str, Any] = json.loads(result.stdout)[0]["State"]
    return state
