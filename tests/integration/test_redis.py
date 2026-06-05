"""Integração com Redis REAL (testcontainers).

Cobre o rate-limit contra um store de verdade (a suíte unitária usa
memory://) e o caminho feliz do check de Redis no /ready — hoje inalcançável
sem um Redis em pé.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from app.core.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration

_PASSWORD = "S3nhaTesteRedis123"


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    # Com --requirepass, como o compose: exercita autenticação de verdade.
    container = (
        DockerContainer("redis:7-alpine")
        .with_command(f"redis-server --requirepass {_PASSWORD}")
        .with_exposed_ports(6379)
    )
    with container:
        wait_for_logs(container, "Ready to accept connections", timeout=30)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://:{_PASSWORD}@{host}:{port}/0"


def _client(redis_url: str, **overrides: Any) -> TestClient:
    base: dict[str, Any] = {
        "rate_limit_enabled": True,
        "rate_limit_storage_uri": redis_url,
        "trusted_hosts": ["testserver"],
    }
    base.update(overrides)
    app = create_app(Settings(**base))

    @app.get("/_rl")
    def _rl() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_rate_limit_429_com_redis_real(redis_url: str) -> None:
    # O contador vive no Redis (não em memória do processo): valida storage,
    # autenticação e a resposta 429 com retry_after de ponta a ponta.
    client = _client(redis_url, rate_limit_default="3/minute")
    codes = [client.get("/_rl").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    body = client.get("/_rl").json()
    assert body["detail"] == "Rate limit exceeded"
    assert "retry_after" in body


def test_ready_redis_ok_com_redis_real(redis_url: str) -> None:
    # Caminho feliz do check de Redis no /ready (a suíte unitária só cobre o
    # caminho de erro, via porta sem ninguém ouvindo).
    client = _client(redis_url)
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["redis"] == "ok"
