from fastapi.testclient import TestClient

from app.main import app, create_app
from tests.conftest import make_settings

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["docs"] == "/docs"


def test_readiness_ok_with_sqlite_and_memory_store() -> None:
    # Config de teste (sqlite + memory://): banco responde e não há Redis para
    # checar — readiness deve ser 200.
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert "redis" not in body["checks"]  # memory:// não é checado


def test_readiness_503_when_redis_unavailable() -> None:
    # Redis configurado como store mas inalcançável: readiness deve falhar (503),
    # mesmo com o banco de pé — diferente do /health (liveness), que segue 200.
    ready_client = TestClient(
        create_app(
            make_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri="redis://127.0.0.1:6399/0",
            )
        )
    )
    response = ready_client.get("/api/v1/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "error"
    # liveness permanece raso e verde mesmo com o Redis fora.
    assert ready_client.get("/api/v1/health").status_code == 200


def test_readiness_cache_limita_idas_ao_backend() -> None:
    # Anti-amplificação: o /ready é isento de rate-limit e sem auth — sem
    # cache, cada chamada vira SELECT 1 + PING (amplificador não-autenticado
    # contra banco/Redis). Com TTL, rajadas dentro da janela reusam o
    # resultado: derrubo o engine após a 1ª chamada e a 2ª (cacheada) ainda
    # responde 200; com TTL=0 (desligado), a falha aparece imediatamente.
    from app.core.database import build_engine

    settings = make_settings(readiness_cache_seconds=60.0)
    app_cached = create_app(settings)
    client_cached = TestClient(app_cached)
    assert client_cached.get("/api/v1/ready").status_code == 200
    # Backend "cai": engine trocado por um que aponta para porta morta.
    app_cached.state.db_engine = build_engine(
        make_settings(database_url="postgresql+psycopg://x:x@127.0.0.1:6399/x")
    )
    assert client_cached.get("/api/v1/ready").status_code == 200  # cacheado

    # TTL=0 desliga o cache: mesma sequência detecta a queda na hora.
    settings_off = make_settings(readiness_cache_seconds=0.0)
    app_off = create_app(settings_off)
    client_off = TestClient(app_off)
    assert client_off.get("/api/v1/ready").status_code == 200
    app_off.state.db_engine = build_engine(
        make_settings(database_url="postgresql+psycopg://x:x@127.0.0.1:6399/x")
    )
    assert client_off.get("/api/v1/ready").status_code == 503
