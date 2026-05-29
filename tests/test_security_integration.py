from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _client(**overrides) -> TestClient:
    base = dict(
        rate_limit_storage_uri="memory://",
        trusted_hosts=["testserver"],
    )
    base.update(overrides)
    return TestClient(create_app(Settings(**base)))


def test_security_headers_on_real_app() -> None:
    client = _client()
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"


def test_rate_limit_returns_429_when_exceeded() -> None:
    client = _client(rate_limit_default="3/minute")
    codes = [client.get("/api/v1/health").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    last = client.get("/api/v1/health")
    assert last.status_code == 429
    assert last.json()["detail"] == "Rate limit exceeded"
    assert "retry_after" in last.json()


def test_rate_limit_disabled_allows_all() -> None:
    client = _client(rate_limit_enabled=False, rate_limit_default="1/minute")
    codes = [client.get("/api/v1/health").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 200, 200]


def test_body_size_limit_rejects_large_payload() -> None:
    client = _client(max_body_size=10)
    response = client.post("/api/v1/health", content=b"x" * 50)
    assert response.status_code == 413


def test_trusted_host_rejects_unknown_host() -> None:
    client = _client(trusted_hosts=["example.com"])
    response = client.get("/api/v1/health")
    assert response.status_code == 400


def test_cors_preflight_allows_configured_origin() -> None:
    client = _client(cors_allow_origins=["http://allowed.test"])
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://allowed.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert (
        response.headers.get("access-control-allow-origin")
        == "http://allowed.test"
    )


def test_docs_renders_without_csp() -> None:
    client = _client()
    response = client.get("/docs")
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers


def test_security_headers_present_on_413() -> None:
    client = _client(max_body_size=10)
    response = client.post("/api/v1/health", content=b"x" * 50)
    assert response.status_code == 413
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_security_headers_present_on_429() -> None:
    client = _client(rate_limit_default="1/minute")
    client.get("/api/v1/health")
    response = client.get("/api/v1/health")
    assert response.status_code == 429
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


def test_security_headers_present_on_400_bad_host() -> None:
    client = _client(trusted_hosts=["example.com"])
    response = client.get("/api/v1/health")
    assert response.status_code == 400
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


def test_cors_credentials_with_wildcard_origin_is_rejected() -> None:
    import pytest

    from app.core.config import Settings
    from app.main import create_app

    with pytest.raises(ValueError):
        create_app(
            Settings(
                cors_allow_credentials=True,
                cors_allow_origins=["*"],
                rate_limit_storage_uri="memory://",
                trusted_hosts=["testserver"],
            )
        )
