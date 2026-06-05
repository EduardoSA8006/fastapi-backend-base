import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _crashing_client(**overrides: Any) -> TestClient:
    """App real (create_app) com uma rota que levanta exceção NÃO-tratada."""
    base: dict[str, Any] = {
        "rate_limit_storage_uri": "memory://",
        "trusted_hosts": ["testserver"],
    }
    base.update(overrides)
    app = create_app(Settings(**base))

    @app.get("/_crash")
    def _crash() -> None:
        raise RuntimeError("segredo interno: senha=abc123")

    return TestClient(app, raise_server_exceptions=False)


def test_500_inesperado_vira_json_padronizado() -> None:
    # Sem o boundary, o ServerErrorMiddleware do Starlette devolve text/plain
    # "Internal Server Error" — fora do contrato {"detail": ...} da API.
    client = _crashing_client()
    response = client.get("/_crash")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Erro interno."}


def test_500_inesperado_nao_vaza_detalhes_internos() -> None:
    client = _crashing_client()
    response = client.get("/_crash")
    assert "RuntimeError" not in response.text
    assert "segredo" not in response.text
    assert "abc123" not in response.text


def test_500_inesperado_sai_com_security_headers_e_request_id() -> None:
    # A resposta nasce DENTRO da pilha de middleware: SecurityHeaders e
    # RequestContext a veem — única resposta da API que antes saía sem nada.
    client = _crashing_client()
    response = client.get("/_crash")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Request-ID")


def test_crash_aparece_no_access_log_com_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Antes, a requisição que crashava era a ÚNICA que sumia do access log
    # (o log só disparava em http.response.start).
    client = _crashing_client()
    with caplog.at_level(logging.INFO, logger="classup.access"):
        client.get("/_crash", headers={"X-Request-ID": "crash-test-id-123"})
    records = [r for r in caplog.records if getattr(r, "status", None) == 500]
    assert records, "crash não apareceu no access log"
    assert getattr(records[0], "request_id", None) == "crash-test-id-123"


def test_excecao_logada_com_stacktrace_e_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A causa (stacktrace) é registrada no log estruturado, correlacionada
    # pelo mesmo request_id do access log.
    client = _crashing_client()
    with caplog.at_level(logging.ERROR, logger="classup.errors"):
        client.get("/_crash", headers={"X-Request-ID": "crash-test-id-456"})
    records = [r for r in caplog.records if r.name == "classup.errors"]
    assert records, "exceção não foi logada"
    record = records[0]
    assert record.exc_info is not None  # stacktrace presente
    assert getattr(record, "request_id", None) == "crash-test-id-456"
