import logging

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
    response = _client().get(
        "/api/v1/health", headers={"X-Request-ID": "fixed-id-123"}
    )
    assert response.headers.get("X-Request-ID") == "fixed-id-123"


def test_rejection_is_logged_as_warning(caplog) -> None:
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
    assert any(
        "-> 400" in record.getMessage() for record in caplog.records
    )
