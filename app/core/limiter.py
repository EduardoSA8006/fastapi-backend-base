from collections.abc import Callable

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import Settings


def build_key_func(settings: Settings) -> Callable[[Request], str]:
    """Cria a função de chave do rate-limit, respeitando a confiança em proxy.

    Quando trust_proxy=True, o IP real do cliente é extraído de X-Forwarded-For
    contando `num_trusted_proxies` saltos a partir da direita: as entradas mais à
    direita são as que os proxies confiáveis acrescentaram, então o cliente é a
    `num_trusted_proxies`-ésima entrada de trás para frente. As entradas mais à
    esquerda são controláveis pelo cliente e não devem ser usadas (permitiriam
    burlar o rate-limit forjando IPs).

    ATENÇÃO: trust_proxy=True só é seguro com `num_trusted_proxies` proxies reais
    reescrevendo X-Forwarded-For à frente. Habilitá-lo sem esse proxy permite que
    o cliente controle o valor e burle o limite.
    """

    def key_func(request: Request) -> str:
        if settings.trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                parts = [p.strip() for p in forwarded.split(",") if p.strip()]
                hops = settings.num_trusted_proxies
                # Só confia se há entradas suficientes para os saltos esperados;
                # caso contrário, recorre ao IP da conexão (seguro).
                if hops >= 1 and len(parts) >= hops:
                    return parts[-hops]
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
