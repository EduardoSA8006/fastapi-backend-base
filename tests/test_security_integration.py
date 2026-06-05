import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import (
    STRONG_REDIS_URI,
    make_client,
    make_prod_settings,
    make_rl_client,
)


def test_security_headers_on_real_app() -> None:
    client = make_client()
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
    )


def test_rate_limit_returns_429_when_exceeded() -> None:
    client = make_rl_client(rate_limit_default="3/minute")
    codes = [client.get("/_rl").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    last = client.get("/_rl")
    assert last.status_code == 429
    assert last.json()["detail"] == "Rate limit exceeded"
    assert "retry_after" in last.json()
    # Contrato completo: o header HTTP acompanha o corpo (clientes que só
    # leem headers também recebem o sinal de backoff).
    assert "Retry-After" in last.headers


def test_rate_limit_fail_closed_when_store_unavailable() -> None:
    # Escolha consciente (F4): com o Redis indisponível, o rate-limit é
    # fail-closed (500), não fail-open. Regressão contra mudança acidental de
    # swallow_errors / in_memory_fallback. Porta 6399 não tem Redis -> connection
    # refused (rápido); socket_connect_timeout=2 é apenas o teto.
    client = make_rl_client(
        raise_server_exceptions=False,
        rate_limit_enabled=True,
        rate_limit_default="100/minute",
        rate_limit_storage_uri="redis://127.0.0.1:6399/0",
    )
    # /_rl não é isento do rate-limit (os probes são) — exercita o fail-closed.
    # Contrato completo: o 500 nasce no ErrorBoundary — JSON padronizado,
    # blindado, sem vazar a causa (erro de conexão Redis) ao cliente.
    response = client.get("/_rl")
    assert response.status_code == 500
    assert response.json() == {"detail": "Erro interno."}
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert "redis" not in response.text.lower()


def test_rate_limit_disabled_allows_all() -> None:
    client = make_client(rate_limit_enabled=False, rate_limit_default="1/minute")
    codes = [client.get("/api/v1/health").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 200, 200]


def test_body_size_limit_rejects_large_payload() -> None:
    client = make_client(max_body_size=10)
    response = client.post("/api/v1/health", content=b"x" * 50)
    assert response.status_code == 413
    # Contrato completo: corpo JSON padronizado, sem eco do payload.
    assert response.json() == {"detail": "Request body too large"}
    assert "x" * 10 not in response.text


def test_body_size_limit_rejects_chunked_payload_on_unread_endpoint() -> None:
    # Cenário do bypass: corpo chunked (sem Content-Length) para /health, que
    # não consome o corpo. Sem o eager-drain isso passaria pela app.
    from collections.abc import Iterator

    def gen() -> Iterator[bytes]:
        yield b"x" * 50

    client = make_client(max_body_size=10)
    response = client.post("/api/v1/health", content=gen())
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_trusted_host_rejects_unknown_host() -> None:
    client = make_client(trusted_hosts=["example.com"])
    response = client.get("/api/v1/health")
    assert response.status_code == 400
    # A rejeição não ecoa o Host recebido (sem reflexo de entrada).
    assert "testserver" not in response.text


def test_healthcheck_host_must_be_trusted() -> None:
    # Contrato do healthcheck (achado 7): com TRUSTED_HOSTS restrito, o probe
    # precisa enviar HEALTHCHECK_HOST com um host permitido. Um host permitido
    # passa; o loopback (fora da lista) é rejeitado — por isso HEALTHCHECK_HOST.
    client = make_client(trusted_hosts=["api.classup.com"])
    assert (
        client.get("/api/v1/health", headers={"host": "api.classup.com"}).status_code
        == 200
    )
    assert (
        client.get("/api/v1/health", headers={"host": "127.0.0.1"}).status_code == 400
    )


def test_cors_preflight_allows_configured_origin() -> None:
    client = make_client(cors_allow_origins=["http://allowed.test"])
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://allowed.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://allowed.test"


def test_docs_renders_without_csp() -> None:
    client = make_client()
    response = client.get("/docs")
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers
    # A isenção é SÓ da CSP: as demais proteções permanecem nos docs.
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_security_headers_present_on_413() -> None:
    client = make_client(max_body_size=10)
    response = client.post("/api/v1/health", content=b"x" * 50)
    assert response.status_code == 413
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_security_headers_present_on_429() -> None:
    # /_rl (não isento) para forçar o 429; os probes não passam pelo rate-limit.
    client = make_rl_client(rate_limit_default="1/minute")
    client.get("/_rl")
    response = client.get("/_rl")
    assert response.status_code == 429
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


def test_security_headers_present_on_400_bad_host() -> None:
    client = make_client(trusted_hosts=["example.com"])
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
    client = make_rl_client(rate_limit_default="2/minute", trust_proxy=False)
    r1 = client.get("/_rl", headers={"X-Forwarded-For": "1.1.1.1"})
    r2 = client.get("/_rl", headers={"X-Forwarded-For": "2.2.2.2"})
    r3 = client.get("/_rl", headers={"X-Forwarded-For": "3.3.3.3"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


# --- Guards de produção (ENVIRONMENT=production) ---


def test_production_rejects_wildcard_trusted_hosts() -> None:
    import pytest

    with pytest.raises(ValueError, match="trusted_hosts"):
        create_app(make_prod_settings(trusted_hosts=["*"]))


def test_production_rejects_memory_rate_limit_store() -> None:
    import pytest

    with pytest.raises(ValueError, match="store compartilhado"):
        create_app(
            make_prod_settings(
                rate_limit_enabled=True, rate_limit_storage_uri="memory://"
            )
        )


def test_production_rejects_debug_true() -> None:
    import pytest

    with pytest.raises(ValueError, match="DEBUG"):
        create_app(make_prod_settings(debug=True))


def test_production_disables_docs() -> None:
    client = TestClient(create_app(make_prod_settings()))
    # O Host precisa ser permitido para chegar à rota.
    headers = {"host": "api.test"}
    assert client.get("/docs", headers=headers).status_code == 404
    assert client.get("/openapi.json", headers=headers).status_code == 404
    assert client.get("/api/v1/health", headers=headers).status_code == 200


def test_production_valid_config_boots() -> None:
    # Configuração de produção válida (redis + hosts reais + sem debug) sobe.
    app = create_app(
        make_prod_settings(
            rate_limit_enabled=True,
            rate_limit_storage_uri=STRONG_REDIS_URI,
        )
    )
    assert app is not None


def test_production_rejects_weak_redis_password() -> None:
    with pytest.raises(ValueError, match="Redis"):
        create_app(
            make_prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri="redis://:classup@redis:6379/0",
            )
        )


def test_production_rejects_redis_without_password() -> None:
    # Senha ausente (URL sem credencial) também é barrada — paridade com o banco.
    with pytest.raises(ValueError, match="Redis"):
        create_app(
            make_prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri="redis://redis:6379/0",
            )
        )


def test_production_rejects_weak_db_password() -> None:
    with pytest.raises(ValueError, match="fraca"):
        create_app(
            make_prod_settings(
                database_url="postgresql+psycopg://classup:classup@db:5432/classup"
            )
        )


def test_production_accepts_strong_db_password() -> None:
    app = create_app(
        make_prod_settings(
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
            make_prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri=STRONG_REDIS_URI,
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
            make_prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri=STRONG_REDIS_URI,
                trust_proxy=True,
            )
        )
    assert any("forja o IP" in r.getMessage() for r in caplog.records)


# --- Guard de produção do MinIO (senha do storage) ---


@pytest.mark.parametrize("weak", ["classup", "classup-minio-dev"])
def test_production_rejects_weak_minio_password(weak: str) -> None:
    # Paridade com Redis/DB: senha default/fraca do MinIO não pode ir a produção.
    # Inclui o default de dev "classup-minio-dev" (público no repositório).
    with pytest.raises(ValueError, match="MinIO"):
        create_app(make_prod_settings(minio_root_password=weak))


def test_production_rejects_minio_without_password() -> None:
    # Senha ausente (vazia) também é barrada — paridade com o banco/Redis.
    with pytest.raises(ValueError, match="MinIO"):
        create_app(make_prod_settings(minio_root_password=""))


def test_production_accepts_strong_minio_password() -> None:
    # Senha forte de MinIO passa pelo guard (configuração de produção válida).
    app = create_app(make_prod_settings(minio_root_password="OutraS3nhaForte456"))
    assert app is not None


@pytest.mark.parametrize("weak_user", ["classup", "admin", "minio", "root", ""])
def test_production_rejects_predictable_minio_user(weak_user: str) -> None:
    # Defesa-em-profundidade: usuário admin previsível do MinIO não vai a
    # produção (facilita enumeração se a porta vazar). Senha forte na base.
    with pytest.raises(ValueError, match="MinIO"):
        create_app(make_prod_settings(minio_root_user=weak_user))


def test_production_accepts_non_obvious_minio_user() -> None:
    # Usuário não-óbvio (e senha forte) passa pelo guard.
    app = create_app(make_prod_settings(minio_root_user="classup-svc-9b2c"))
    assert app is not None


def test_minio_settings_defaults() -> None:
    # Defaults coerentes com o compose (rede interna, hostname `minio`).
    settings = Settings()
    assert settings.minio_endpoint == "minio:9000"
    assert settings.minio_use_ssl is False
    assert settings.minio_bucket == "classup-files"


# --- Validação fail-closed do ENVIRONMENT ---


def test_environment_e_obrigatorio_sem_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fail-closed também no NOME da variável: um typo (ENVIRONMNET=production)
    # significa chave ausente — o boot deve falhar com ValidationError, não
    # cair silenciosamente em development (que desligaria todos os guards).
    # _env_file=None isola de um .env local; delenv simula o typo.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(Exception, match="environment"):
        Settings(_env_file=None)


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


# --- Rota raiz respeita o Settings injetado (não o cache global) ---


def test_root_existe_em_apps_de_create_app_e_usa_settings_injetado() -> None:
    # A rota "/" pertence ao composition root: todo app de create_app a tem,
    # lendo app.state.settings — não o get_settings() global cacheado.
    client = make_client(app_name="App Injetado")
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["app"] == "App Injetado"


def test_root_em_producao_nao_anuncia_docs() -> None:
    client = TestClient(create_app(make_prod_settings(app_name="Prod App")))
    response = client.get("/", headers={"host": "api.test"})
    assert response.status_code == 200
    body = response.json()
    assert body["app"] == "Prod App"
    assert "docs" not in body  # produção não anuncia a documentação
