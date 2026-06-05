from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.shared.exceptions import (
    AppException,
    ConflictError,
    NotFoundError,
    UnavailableError,
)


def _client_with_raising_route(exc: AppException, **overrides: Any) -> TestClient:
    """App real (create_app) com uma rota que levanta a exceção dada."""
    base: dict[str, Any] = {
        "rate_limit_storage_uri": "memory://",
        "trusted_hosts": ["testserver"],
    }
    base.update(overrides)
    app = create_app(Settings(**base))

    @app.get("/_boom")
    def _boom() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def test_status_codes_da_hierarquia() -> None:
    # O contrato da hierarquia: cada classe carrega o status HTTP que vira
    # resposta — features especializam herdando (não inventam status novos).
    assert NotFoundError("x").status_code == 404
    assert ConflictError("x").status_code == 409
    assert UnavailableError("x").status_code == 503
    assert issubclass(NotFoundError, AppException)
    assert issubclass(ConflictError, AppException)
    assert issubclass(UnavailableError, AppException)


def test_handler_converte_not_found_em_404_padronizado() -> None:
    client = _client_with_raising_route(NotFoundError("Recurso não encontrado."))
    response = client.get("/_boom")
    assert response.status_code == 404
    assert response.json() == {"detail": "Recurso não encontrado."}


def test_handler_converte_conflict_em_409() -> None:
    client = _client_with_raising_route(ConflictError("Já existe."))
    response = client.get("/_boom")
    assert response.status_code == 409
    assert response.json() == {"detail": "Já existe."}


def test_handler_converte_unavailable_em_503() -> None:
    client = _client_with_raising_route(UnavailableError("Dependência fora."))
    response = client.get("/_boom")
    assert response.status_code == 503
    assert response.json() == {"detail": "Dependência fora."}


def test_handler_usa_detail_default_da_classe() -> None:
    # Sem mensagem explícita, vale o detail default da classe — nunca vaza
    # repr/args da exceção para o cliente.
    client = _client_with_raising_route(NotFoundError())
    response = client.get("/_boom")
    assert response.status_code == 404
    assert response.json() == {"detail": NotFoundError.detail}


def test_resposta_de_erro_preserva_security_headers() -> None:
    # Mesma garantia dos 400/413/429: o handler roda DENTRO da pilha de
    # middleware, então as respostas de erro também saem blindadas.
    client = _client_with_raising_route(NotFoundError("x"))
    response = client.get("/_boom")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
