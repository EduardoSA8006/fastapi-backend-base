from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app, create_app

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
            Settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri="redis://127.0.0.1:6399/0",
                trusted_hosts=["testserver"],
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
