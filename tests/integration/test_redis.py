"""Integração com Redis REAL (testcontainers).

Cobre o rate-limit contra um store de verdade (a suíte unitária usa
memory://) e o caminho feliz do check de Redis no /ready — hoje inalcançável
sem um Redis em pé.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.integration.conftest import redis_container

pytestmark = pytest.mark.integration

_PASSWORD = "S3nhaTesteRedis123"


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    # Com --requirepass, como o compose: exercita autenticação de verdade.
    with redis_container(_PASSWORD) as base:
        yield f"{base}/0"


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


def test_ready_redis_error_com_redis_indisponivel() -> None:
    # Contraparte de erro do check de Redis no /ready (health/router.py:75):
    # rate-limit ligado apontando para um endereço redis MORTO (porta fechada)
    # -> o ping estoura, o ramo de exceção marca checks["redis"]="error" e o
    # readiness responde 503. Sem container (endereço propositalmente inerte).
    app = create_app(
        Settings(
            rate_limit_enabled=True,
            rate_limit_storage_uri="redis://127.0.0.1:6399/0",
            trusted_hosts=["testserver"],
            readiness_cache_seconds=0,
        )
    )
    response = TestClient(app).get("/api/v1/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not ready"
    assert body["checks"]["redis"] == "error"
