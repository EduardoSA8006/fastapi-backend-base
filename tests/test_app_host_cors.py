"""TrustedHost e CORS no app real (incl. o guard universal de CORS)."""

import pytest

from app.core.config import Settings
from app.main import create_app
from tests.conftest import make_client


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


def test_cors_credentials_with_wildcard_origin_is_rejected() -> None:

    with pytest.raises(ValueError, match="cors_allow_credentials"):
        create_app(
            Settings(
                cors_allow_credentials=True,
                cors_allow_origins=["*"],
                rate_limit_storage_uri="memory://",
                trusted_hosts=["testserver"],
            )
        )
