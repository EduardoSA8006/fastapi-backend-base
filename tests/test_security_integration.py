import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _client(**overrides: Any) -> TestClient:
    base: dict[str, Any] = {
        "rate_limit_storage_uri": "memory://",
        "trusted_hosts": ["testserver"],
    }
    base.update(overrides)
    return TestClient(create_app(Settings(**base)))


def _rl_client(*, raise_server_exceptions: bool = True, **overrides: Any) -> TestClient:
    """Cliente com uma rota neutra `/_rl` NÃO isenta do rate-limit.

    Os probes (/health, /ready) são isentos e a rota "/" só existe no `app` de
    módulo (não em apps de create_app), então os testes de rate-limit precisam
    de um endpoint próprio para exercitar o limite.
    """
    base: dict[str, Any] = {
        "rate_limit_storage_uri": "memory://",
        "trusted_hosts": ["testserver"],
    }
    base.update(overrides)
    app = create_app(Settings(**base))

    @app.get("/_rl")
    def _rl() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_security_headers_on_real_app() -> None:
    client = _client()
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert (
        response.headers["Content-Security-Policy"]
        == "default-src 'self'; frame-ancestors 'none'"
    )


def test_rate_limit_returns_429_when_exceeded() -> None:
    client = _rl_client(rate_limit_default="3/minute")
    codes = [client.get("/_rl").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    last = client.get("/_rl")
    assert last.status_code == 429
    assert last.json()["detail"] == "Rate limit exceeded"
    assert "retry_after" in last.json()


def test_rate_limit_fail_closed_when_store_unavailable() -> None:
    # Escolha consciente (F4): com o Redis indisponível, o rate-limit é
    # fail-closed (500), não fail-open. Regressão contra mudança acidental de
    # swallow_errors / in_memory_fallback. Porta 6399 não tem Redis -> connection
    # refused (rápido); socket_connect_timeout=2 é apenas o teto.
    client = _rl_client(
        raise_server_exceptions=False,
        rate_limit_enabled=True,
        rate_limit_default="100/minute",
        rate_limit_storage_uri="redis://127.0.0.1:6399/0",
    )
    # /_rl não é isento do rate-limit (os probes são) — exercita o fail-closed.
    assert client.get("/_rl").status_code == 500


def test_rate_limit_disabled_allows_all() -> None:
    client = _client(rate_limit_enabled=False, rate_limit_default="1/minute")
    codes = [client.get("/api/v1/health").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 200, 200]


def test_body_size_limit_rejects_large_payload() -> None:
    client = _client(max_body_size=10)
    response = client.post("/api/v1/health", content=b"x" * 50)
    assert response.status_code == 413


def test_body_size_limit_rejects_chunked_payload_on_unread_endpoint() -> None:
    # Cenário do bypass: corpo chunked (sem Content-Length) para /health, que
    # não consome o corpo. Sem o eager-drain isso passaria pela app.
    from collections.abc import Iterator

    def gen() -> Iterator[bytes]:
        yield b"x" * 50

    client = _client(max_body_size=10)
    response = client.post("/api/v1/health", content=gen())
    assert response.status_code == 413


def test_trusted_host_rejects_unknown_host() -> None:
    client = _client(trusted_hosts=["example.com"])
    response = client.get("/api/v1/health")
    assert response.status_code == 400


def test_healthcheck_host_must_be_trusted() -> None:
    # Contrato do healthcheck (achado 7): com TRUSTED_HOSTS restrito, o probe
    # precisa enviar HEALTHCHECK_HOST com um host permitido. Um host permitido
    # passa; o loopback (fora da lista) é rejeitado — por isso HEALTHCHECK_HOST.
    client = _client(trusted_hosts=["api.classup.com"])
    assert (
        client.get("/api/v1/health", headers={"host": "api.classup.com"}).status_code
        == 200
    )
    assert (
        client.get("/api/v1/health", headers={"host": "127.0.0.1"}).status_code == 400
    )


def test_cors_preflight_allows_configured_origin() -> None:
    client = _client(cors_allow_origins=["http://allowed.test"])
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://allowed.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://allowed.test"


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
    # /_rl (não isento) para forçar o 429; os probes não passam pelo rate-limit.
    client = _rl_client(rate_limit_default="1/minute")
    client.get("/_rl")
    response = client.get("/_rl")
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

    with pytest.raises(ValueError, match="cors_allow_credentials"):
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
    client = _rl_client(rate_limit_default="2/minute", trust_proxy=False)
    r1 = client.get("/_rl", headers={"X-Forwarded-For": "1.1.1.1"})
    r2 = client.get("/_rl", headers={"X-Forwarded-For": "2.2.2.2"})
    r3 = client.get("/_rl", headers={"X-Forwarded-For": "3.3.3.3"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


# --- Guards de produção (ENVIRONMENT=production) ---


def _prod_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "production",
        "debug": False,
        "trusted_hosts": ["api.test"],
        "rate_limit_enabled": False,  # evita exigir redis nestes testes
        "rate_limit_storage_uri": "memory://",
    }
    base.update(overrides)
    return Settings(**base)


def test_production_rejects_wildcard_trusted_hosts() -> None:
    import pytest

    with pytest.raises(ValueError, match="trusted_hosts"):
        create_app(_prod_settings(trusted_hosts=["*"]))


def test_production_rejects_memory_rate_limit_store() -> None:
    import pytest

    with pytest.raises(ValueError, match="store compartilhado"):
        create_app(
            _prod_settings(rate_limit_enabled=True, rate_limit_storage_uri="memory://")
        )


def test_production_rejects_debug_true() -> None:
    import pytest

    with pytest.raises(ValueError, match="DEBUG"):
        create_app(_prod_settings(debug=True))


def test_production_disables_docs() -> None:
    client = TestClient(create_app(_prod_settings()))
    # O Host precisa ser permitido para chegar à rota.
    headers = {"host": "api.test"}
    assert client.get("/docs", headers=headers).status_code == 404
    assert client.get("/openapi.json", headers=headers).status_code == 404
    assert client.get("/api/v1/health", headers=headers).status_code == 200


# URL de Redis com senha forte, para os testes de produção que precisam de um
# store válido (o guard de produção barra Redis sem senha / com senha fraca).
_STRONG_REDIS_URI = "redis://:S3nhaForteRedis123@redis:6379/0"


def test_production_valid_config_boots() -> None:
    # Configuração de produção válida (redis + hosts reais + sem debug) sobe.
    app = create_app(
        _prod_settings(
            rate_limit_enabled=True,
            rate_limit_storage_uri=_STRONG_REDIS_URI,
        )
    )
    assert app is not None


def test_production_rejects_weak_redis_password() -> None:
    with pytest.raises(ValueError, match="Redis"):
        create_app(
            _prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri="redis://:classup@redis:6379/0",
            )
        )


def test_production_rejects_redis_without_password() -> None:
    # Senha ausente (URL sem credencial) também é barrada — paridade com o banco.
    with pytest.raises(ValueError, match="Redis"):
        create_app(
            _prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri="redis://redis:6379/0",
            )
        )


def test_production_rejects_weak_db_password() -> None:
    with pytest.raises(ValueError, match="fraca"):
        create_app(
            _prod_settings(
                database_url="postgresql+psycopg://classup:classup@db:5432/classup"
            )
        )


def test_production_accepts_strong_db_password() -> None:
    app = create_app(
        _prod_settings(
            database_url=(
                "postgresql+psycopg://classup:S3nhaForteAleatoria123@db:5432/classup"
            )
        )
    )
    assert app is not None


def test_production_without_trust_proxy_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="classup"):
        create_app(
            _prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri=_STRONG_REDIS_URI,
                trust_proxy=False,
            )
        )
    assert any("colapsa num único bucket" in r.getMessage() for r in caplog.records)


def test_production_with_trust_proxy_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # O risco simétrico: TRUST_PROXY=true sem proxy real permite spoofing de XFF.
    # Não falha o boot (config legítima atrás de LB), mas avisa explicitamente.
    with caplog.at_level(logging.WARNING, logger="classup"):
        create_app(
            _prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri=_STRONG_REDIS_URI,
                trust_proxy=True,
            )
        )
    assert any("forja o IP" in r.getMessage() for r in caplog.records)


# --- Validação fail-closed do ENVIRONMENT ---


@pytest.mark.parametrize(
    "bad",
    [
        "prod",
        "prd",
        "produção",
        "dev",
        "",
        "prod\u200b",  # zero-width space: strip() NÃO remove → continua inválido
    ],
)
def test_environment_rejects_unknown_values(bad: str) -> None:
    # Typos plausíveis em deploy não podem virar "modo dev silencioso": o
    # Settings deve falhar na construção em vez de cair no fallback inseguro.
    with pytest.raises(ValueError, match="ENVIRONMENT inválido"):
        Settings(environment=bad)


@pytest.mark.parametrize(
    ("raw", "expected_prod"),
    [
        ("production", True),
        ("  PRODUCTION  ", True),
        ("Production", True),
        ("development", False),
        ("staging", False),
    ],
)
def test_environment_normalized_and_is_production(
    raw: str, expected_prod: bool
) -> None:
    settings = Settings(environment=raw)
    assert settings.environment == raw.strip().lower()
    assert settings.is_production is expected_prod
