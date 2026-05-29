from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.limiter import create_limiter, rate_limit_exceeded_handler
from app.middleware.body_size_limit import BodySizeLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Cria e configura a instância da aplicação FastAPI."""
    settings = settings or get_settings()

    app = FastAPI(title=settings.app_name, debug=settings.debug)

    # Rate-limit (slowapi): estado + handler + middleware.
    if settings.rate_limit_enabled:
        limiter = create_limiter(settings)
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # A ORDEM importa: o último adicionado é o mais externo (executa primeiro).
    # Adiciona-se na ordem inversa da execução desejada.
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=settings.hsts_enabled)

    if settings.rate_limit_enabled:
        app.add_middleware(SlowAPIMiddleware)

    app.add_middleware(
        BodySizeLimitMiddleware, max_body_size=settings.max_body_size
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()


@app.get("/")
def root() -> dict[str, str]:
    """Rota raiz com informações básicas da API."""
    settings = get_settings()
    return {"app": settings.app_name, "docs": "/docs"}
