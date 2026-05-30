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


def test_spoofed_xff_ignored_when_proxy_untrusted() -> None:
    # Com trust_proxy=False (padrão), X-Forwarded-For forjado é ignorado:
    # todas as requisições compartilham o bucket do IP da conexão, então o
    # rate-limit dispara mesmo variando o cabeçalho — não dá para burlar.
    client = _client(rate_limit_default="2/minute", trust_proxy=False)
    r1 = client.get("/api/v1/health", headers={"X-Forwarded-For": "1.1.1.1"})
    r2 = client.get("/api/v1/health", headers={"X-Forwarded-For": "2.2.2.2"})
    r3 = client.get("/api/v1/health", headers={"X-Forwarded-For": "3.3.3.3"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


# --- Guards de produção (ENVIRONMENT=production) ---


def _prod_settings(**overrides) -> Settings:
    base = dict(
        environment="production",
        debug=False,
        trusted_hosts=["api.test"],
        rate_limit_enabled=False,  # evita exigir redis nestes testes
        rate_limit_storage_uri="memory://",
    )
    base.update(overrides)
    return Settings(**base)


def test_production_rejects_wildcard_trusted_hosts() -> None:
    import pytest

    with pytest.raises(ValueError):
        create_app(_prod_settings(trusted_hosts=["*"]))


def test_production_rejects_memory_rate_limit_store() -> None:
    import pytest

    with pytest.raises(ValueError):
        create_app(
            _prod_settings(
                rate_limit_enabled=True, rate_limit_storage_uri="memory://"
            )
        )


def test_production_rejects_debug_true() -> None:
    import pytest

    with pytest.raises(ValueError):
        create_app(_prod_settings(debug=True))


def test_production_disables_docs() -> None:
    client = TestClient(create_app(_prod_settings()))
    # O Host precisa ser permitido para chegar à rota.
    headers = {"host": "api.test"}
    assert client.get("/docs", headers=headers).status_code == 404
    assert client.get("/openapi.json", headers=headers).status_code == 404
    assert client.get("/api/v1/health", headers=headers).status_code == 200


def test_production_valid_config_boots() -> None:
    # Configuração de produção válida (redis + hosts reais + sem debug) sobe.
    app = create_app(
        _prod_settings(
            rate_limit_enabled=True,
            rate_limit_storage_uri="redis://redis:6379/0",
        )
    )
    assert app is not None
