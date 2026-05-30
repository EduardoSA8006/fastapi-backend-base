import logging
import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _client() -> TestClient:
    return TestClient(
        create_app(
            Settings(
                rate_limit_storage_uri="memory://",
                trusted_hosts=["testserver"],
            )
        )
    )


def test_request_id_generated_when_absent() -> None:
    response = _client().get("/api/v1/health")
    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    assert len(request_id) > 0


def test_request_id_echoed_when_provided() -> None:
    response = _client().get("/api/v1/health", headers={"X-Request-ID": "fixed-id-123"})
    assert response.headers.get("X-Request-ID") == "fixed-id-123"


def test_invalid_request_id_is_replaced_by_safe_uuid() -> None:
    # IDs com caracteres inseguros (espaços, controle) são descartados e um
    # uuid seguro é gerado — base do fix de log injection (CRLF não passa).
    bad = "id com espacos e ; simbolos"
    response = _client().get("/api/v1/health", headers={"X-Request-ID": bad})
    returned = response.headers["X-Request-ID"]
    assert returned != bad
    assert re.fullmatch(r"[0-9a-f]{32}", returned) is not None


def test_overlong_request_id_is_replaced() -> None:
    response = _client().get("/api/v1/health", headers={"X-Request-ID": "a" * 200})
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"]) is not None


def test_rejection_is_logged_as_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(
        create_app(
            Settings(
                rate_limit_storage_uri="memory://",
                trusted_hosts=["example.com"],  # rejeita o host "testserver"
            )
        )
    )
    with caplog.at_level(logging.WARNING, logger="classup.access"):
        response = client.get("/api/v1/health")
    assert response.status_code == 400
    assert any("-> 400" in record.getMessage() for record in caplog.records)
