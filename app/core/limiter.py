from collections.abc import Callable

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import Settings


def build_key_func(settings: Settings) -> Callable[[Request], str]:
    """Cria a função de chave do rate-limit, respeitando a confiança em proxy.

    Assume UM único proxy reverso confiável à frente quando trust_proxy=True:
    o IP real do cliente é a entrada mais à direita de X-Forwarded-For (a que o
    proxy acrescentou). A entrada mais à esquerda é controlável pelo cliente e
    não deve ser usada (permitiria burlar o rate-limit forjando IPs).
    """

    def key_func(request: Request) -> str:
        if settings.trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                client_ip = forwarded.split(",")[-1].strip()
                if client_ip:
                    return client_ip
        return get_remote_address(request)

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


def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Resposta JSON consistente para o erro 429, com Retry-After."""
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
