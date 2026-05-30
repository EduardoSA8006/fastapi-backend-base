from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import build_engine, build_session_factory
from app.core.limiter import create_limiter, rate_limit_exceeded_handler
from app.core.logging import configure_logging
from app.middleware.body_size_limit import BodySizeLimitMiddleware
from app.middleware.observability import RequestContextMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Cria e configura a instância da aplicação FastAPI."""
    settings = settings or get_settings()

    configure_logging(settings.debug)

    # Guard universal (vale em qualquer ambiente): CORS com credenciais + origem
    # curinga reflete origens arbitrárias com credenciais — sempre inseguro.
    if settings.cors_allow_credentials and "*" in settings.cors_allow_origins:
        raise ValueError(
            "cors_allow_credentials=True com cors_allow_origins=['*'] é "
            "inseguro: reflete origens arbitrárias com credenciais."
        )

    # Guards que falham duro em produção (ENVIRONMENT=production).
    if settings.is_production:
        if settings.debug:
            raise ValueError(
                "DEBUG=true não é permitido em produção (vaza stack traces)."
            )
        if "*" in settings.trusted_hosts:
            raise ValueError(
                "trusted_hosts=['*'] em produção desativa a validação de Host. "
                "Defina TRUSTED_HOSTS com os hosts reais."
            )
        if settings.rate_limit_enabled and settings.rate_limit_storage_uri.startswith(
            "memory://"
        ):
            raise ValueError(
                "Rate-limit em produção exige um store compartilhado "
                "(redis://...), não memory://."
            )

    # Documentação interativa só fora de produção (não expõe a superfície da API).
    docs_enabled = not settings.is_production
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Engine/sessão do banco ligadas ao Settings deste app (respeita override).
    engine = build_engine(settings)
    app.state.db_engine = engine
    app.state.db_sessionmaker = build_session_factory(engine)

    # Rate-limit (slowapi): estado + handler + middleware.
    if settings.rate_limit_enabled:
        limiter = create_limiter(settings)
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # A ORDEM importa: o último adicionado é o mais EXTERNO (executa primeiro na
    # entrada e por último na saída). RequestContext fica o mais externo (envolve
    # tudo: atribui X-Request-ID e loga o resultado final); SecurityHeaders logo
    # abaixo, para que TODAS as respostas — inclusive rejeições (400/413/429) —
    # recebam os cabeçalhos de segurança.
    # Ordem de execução na entrada:
    #   RequestContext -> SecurityHeaders -> TrustedHost -> CORS -> BodySize
    #   -> SlowAPI -> app
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
    app.add_middleware(
        SecurityHeadersMiddleware, hsts_enabled=settings.hsts_enabled
    )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()


@app.get("/")
def root() -> dict[str, str]:
    """Rota raiz com informações básicas da API."""
    settings = get_settings()
    return {"app": settings.app_name, "docs": "/docs"}
