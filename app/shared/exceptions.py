"""Hierarquia de erros de domínio (app_exceptions) + handler global.

Contrato arquitetural (ver spec da refatoração feature-first): services
levantam exceções tipadas; UM handler global converte para HTTP com corpo
padronizado {"detail": ...}. Features especializam herdando das classes
abaixo (ex.: StorageObjectNotFoundError(NotFoundError)) — nunca inventam
status codes novos no router.

O handler roda dentro da pilha de middleware, então as respostas de erro
saem com os security headers, como qualquer outra (mesma garantia dos
400/413/429 já testada).
"""

import logging

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

logger = logging.getLogger("classup.errors")


class AppException(Exception):
    """Base dos erros de domínio. status_code + detail viram a resposta HTTP.

    O detail é a ÚNICA parte que chega ao cliente — mensagens devem ser
    seguras para exibição (sem internals, sem dados de outras contas).
    """

    status_code: int = 500
    detail: str = "Erro interno."

    def __init__(self, detail: str | None = None) -> None:
        # Sem mensagem explícita, vale o default da classe — nunca vaza
        # repr/args acidentais para o cliente.
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class NotFoundError(AppException):
    """Recurso inexistente (ou invisível para o chamador) → 404."""

    status_code = 404
    detail = "Recurso não encontrado."


class ConflictError(AppException):
    """Estado conflita com a operação (duplicidade, versão) → 409."""

    status_code = 409
    detail = "Conflito com o estado atual do recurso."


class UnavailableError(AppException):
    """Dependência externa indisponível (banco, storage, broker) → 503."""

    status_code = 503
    detail = "Serviço temporariamente indisponível."


async def app_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Converte AppException na resposta HTTP padronizada."""
    # A assinatura aceita Exception (contrato do Starlette); o registro por
    # tipo garante que só AppException chega aqui. O estreitamento usa `if`
    # (não assert): com PYTHONOPTIMIZE o assert sumiria e um exc inesperado
    # vazaria AttributeError — o fallback devolve 500 genérico sem internals.
    if not isinstance(exc, AppException):
        return JSONResponse({"detail": "Erro interno."}, status_code=500)
    # 4xx de domínio é fluxo normal (não loga — ruído); 5xx é infra degradada
    # e precisa deixar rastro com a causa, correlacionado pelo request_id.
    # Nível ERROR — alinhado com o access log e o ErrorBoundary (status >= 500
    # é ERROR em todos os loggers; alertas por nível não perdem o 503).
    if exc.status_code >= 500:
        request_id = getattr(request.state, "request_id", "-")
        logger.error(
            f"{type(exc).__name__} em {request.url.path}: {exc.detail} id={request_id}",
            extra={"request_id": request_id, "path": request.url.path},
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def register_exception_handlers(app: FastAPI) -> None:
    """Registra o handler global de AppException no app."""
    app.add_exception_handler(AppException, app_exception_handler)
