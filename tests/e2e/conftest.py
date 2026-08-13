"""Fixtures e2e: sobem o stack compose REAL em projetos isolados.

Dois stacks, SEQUENCIAIS (module-scoped — container_name é fixo e único por
host, então nunca coexistem):
- `stack` (myapp-e2e, porta 18001): modo development — o stack como sobe
  em dev.
- `stack_prod` (myapp-e2e-prod, porta 18002): ENVIRONMENT=production com
  credenciais fortes geradas para o teste — valida os guards VIVOS.

Isolamento: cada projeto tem containers/volumes próprios (myapp-e2e_*);
o teardown `down -v` remove SÓ os volumes do projeto — o stack/dados de dev
do usuário não são tocados. Sobra de execução e2e anterior (teardown que
falhou) é detectada pelo label de projeto do compose e removida; stack de
DEV em execução => skip (não derrubamos o ambiente do usuário).
"""

import json
import os
import secrets
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest

PROJECT_DEV = "myapp-e2e"
PROJECT_PROD = "myapp-e2e-prod"
_E2E_PROJECTS = {PROJECT_DEV, PROJECT_PROD}

PROD_HOST = "api.e2e.test"


def _compose(project: str) -> list[str]:
    return ["docker", "compose", "-p", project]


def _run(
    args: list[str],
    env: dict[str, str] | None = None,
    timeout: int = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
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
        if _health(container) == "healthy":
            return
        time.sleep(3)
    raise TimeoutError(
        f"{container} não ficou healthy em {timeout_s}s (status: {_health(container)})"
    )


def _existing_stack_project() -> str | None:
    """Projeto compose dono do container myapp-api, se ele existir."""
    result = _run(
        [
            "docker",
            "inspect",
            "-f",
            '{{index .Config.Labels "com.docker.compose.project"}}',
            "myapp-api",
        ],
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _ensure_no_conflicting_stack(env: dict[str, str]) -> None:
    project = _existing_stack_project()
    if project is None:
        return
    if project in _E2E_PROJECTS:
        # Sobra de QUALQUER execução e2e anterior — limpa e prossegue.
        _run(
            [*_compose(project), "down", "-v", "--remove-orphans"],
            env=env,
            timeout=180,
            check=False,
        )
        return
    pytest.skip(
        f"stack '{project}' (dev?) em execução com os mesmos container_name — "
        "pare-o (docker compose down) para rodar o e2e"
    )


def _stack_lifecycle(project: str, env: dict[str, str]) -> Iterator[str]:
    _ensure_no_conflicting_stack(env)
    try:
        # Build + up: primeiro build é demorado (imagem + poetry install).
        _run([*_compose(project), "up", "-d", "--build"], env=env, timeout=900)
        # api healthy implica db/redis/redis-taskiq/minio healthy (depends_on).
        _wait_healthy("myapp-api", timeout_s=300)
        # worker: healthcheck = round-trip real do TaskIQ pelo broker.
        _wait_healthy("myapp-worker", timeout_s=180)
        yield f"http://127.0.0.1:{env['API_PORT']}"
    finally:
        _run(
            [*_compose(project), "down", "-v", "--remove-orphans"],
            env=env,
            timeout=180,
            check=False,
        )


# Ambientes dos dois stacks (module-level para compose_exec usar o certo).
# ENVIRONMENT é obrigatório no compose (${ENVIRONMENT:?...}) — sem default.
_DEV_ENV = {"API_PORT": "18001", "ENVIRONMENT": "development"}


def _prod_env() -> dict[str, str]:
    # Credenciais fortes geradas por execução: os guards de produção exigem
    # senhas não-fracas e usuário MinIO não-óbvio — aqui eles rodam DE VERDADE.
    return {
        "API_PORT": "18002",
        "ENVIRONMENT": "production",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "REDIS_PASSWORD": secrets.token_urlsafe(24),
        "TASKIQ_REDIS_PASSWORD": secrets.token_urlsafe(24),
        "MINIO_ROOT_USER": "myapp-svc-e2e",
        "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(24),
        "TRUSTED_HOSTS": f'["{PROD_HOST}"]',
        "HEALTHCHECK_HOST": PROD_HOST,
    }


@pytest.fixture(scope="module")
def stack() -> Iterator[str]:
    """Stack em modo development (como em dev)."""
    yield from _stack_lifecycle(PROJECT_DEV, _DEV_ENV)


@pytest.fixture(scope="module")
def stack_prod() -> Iterator[str]:
    """Stack em modo production (guards vivos, credenciais fortes)."""
    yield from _stack_lifecycle(PROJECT_PROD, _prod_env())


def compose_exec(
    service: str, *cmd: str, project: str = PROJECT_DEV, timeout: int = 30
) -> tuple[int, str]:
    """Executa um comando dentro de um serviço do stack e2e."""
    result = _run(
        [*_compose(project), "exec", "-T", service, *cmd],
        timeout=timeout,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


def container_state(container: str) -> dict[str, Any]:
    result = _run(["docker", "inspect", container], check=False)
    if result.returncode != 0:
        return {}
    state: dict[str, Any] = json.loads(result.stdout)[0]["State"]
    return state
