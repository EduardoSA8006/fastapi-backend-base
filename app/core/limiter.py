from collections.abc import Callable

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.client_ip import resolve_client_ip
from app.core.config import Settings


def build_key_func(settings: Settings) -> Callable[[Request], str]:
    """Cria a função de chave do rate-limit, respeitando a confiança em proxy.

    A extração do IP real (X-Forwarded-For vs IP da conexão) é delegada a
    `resolve_client_ip`, a mesma usada pelo log de acesso — assim rate-limit e
    logs concordam sobre quem é o cliente.
    """

    def key_func(request: Request) -> str:
        return resolve_client_ip(
            request.headers.get("X-Forwarded-For"),
            get_remote_address(request),
            settings.trust_proxy,
            settings.num_trusted_proxies,
        )

    return key_func


def create_limiter(settings: Settings) -> Limiter:
    """Instancia o Limiter do slowapi a partir das configurações."""
    return Limiter(
        key_func=build_key_func(settings),
        default_limits=[settings.rate_limit_default],
        storage_uri=settings.rate_limit_storage_uri,
        enabled=settings.rate_limit_enabled,
        headers_enabled=True,
    )


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    """Resposta JSON consistente para o erro 429, com Retry-After.

    `exc` é tipado como Exception para casar com o contrato de handler do
    Starlette; não é usado (os dados do limite vêm de request.state).
    """
    limit_data = getattr(request.state, "view_rate_limit", None)
    headers: dict[str, str] = {}
    if limit_data is not None:
        try:
            tmp = JSONResponse(content={}, status_code=429)
            tmp = request.app.state.limiter._inject_headers(tmp, limit_data)
            for key in (
                "Retry-After",
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "X-RateLimit-Reset",
            ):
                if key in tmp.headers:
                    headers[key] = tmp.headers[key]
        except Exception:
            # _inject_headers é API privada do slowapi; em caso de mudança,
            # degrada para um 429 limpo sem os cabeçalhos extras.
            headers = {}

    body: dict[str, object] = {"detail": "Rate limit exceeded"}
    if "Retry-After" in headers:
        try:
            body["retry_after"] = int(headers["Retry-After"])
        except ValueError:
            body["retry_after"] = headers["Retry-After"]

    return JSONResponse(content=body, status_code=429, headers=headers)
