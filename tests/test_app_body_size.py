"""Limite de corpo no app real: Content-Length e o caminho chunked."""

from tests.conftest import make_client


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
