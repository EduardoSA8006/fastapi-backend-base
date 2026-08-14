from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import Settings, get_settings
from app.core.database import build_engine, build_session_factory
from app.core.limiter import create_limiter, rate_limit_exceeded_handler
from app.core.logging import configure_logging
from app.core.middleware.body_size_limit import BodySizeLimitMiddleware
from app.core.middleware.error_boundary import ErrorBoundaryMiddleware
from app.core.middleware.observability import RequestContextMiddleware
from app.core.middleware.security_headers import SecurityHeadersMiddleware
from app.core.security_guards import validate_production, validate_universal
from app.features.health import router as health
from app.shared.exceptions import register_exception_handlers
from app.worker import broker as taskiq_broker


def create_app(settings: Settings | None = None) -> FastAPI:
    """Cria e configura a instância da aplicação FastAPI.

    Composition root: SÓ compõe (guards → app → state → handlers →
    middlewares → routers). A política de segurança vive em
    core/security_guards — testável isoladamente.
    """
    settings = settings or get_settings()

    configure_logging(settings.debug)

    # Guards fail-closed: universais + os de produção (no-op fora dela).
    validate_universal(settings)
    validate_production(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # A API só DESPACHA tasks (.kiq()); inicia o broker no processo web,
        # nunca no worker (is_worker_process). InMemoryBroker no-op em testes.
        if not taskiq_broker.is_worker_process:
            await taskiq_broker.startup()
        yield
        if not taskiq_broker.is_worker_process:
            await taskiq_broker.shutdown()

    # Documentação interativa só fora de produção (não expõe a superfície da API).
    docs_enabled = not settings.is_production
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )

    # Settings deste app guardado no state, para que as rotas respeitem o
    # override injetado (não o get_settings() global cacheado por lru_cache).
    app.state.settings = settings

    # Erros de domínio (shared/exceptions): handler global converte
    # AppException em resposta HTTP padronizada {"detail": ...}.
    register_exception_handlers(app)

    # Engine/sessão do banco ligadas ao Settings deste app (respeita override).
    engine = build_engine(settings)
    app.state.db_engine = engine
    app.state.db_sessionmaker = build_session_factory(engine)

    # Rate-limit (slowapi): estado + handler + middleware.
    if settings.rate_limit_enabled:
        limiter = create_limiter(settings)
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
        # Probes (liveness/readiness) NÃO passam pelo rate-limit: são chamados de
        # forma recorrente (não devem gastar cota por IP) e, com o store fora, o
        # limiter é fail-closed (500) — isso derrubaria o liveness e levaria o
        # container a um restart-loop inútil (o Redis continua fora). O readiness,
        # então, é quem reporta o store degradado. Registramos os nomes em
        # _exempt_routes, o mesmo conjunto que o decorator público `limiter.exempt`
        # manipula (indisponível aqui, pois o limiter é criado por-app).
        for fn in (health.health_check, health.readiness):
            limiter._exempt_routes.add(f"{fn.__module__}.{fn.__name__}")

    # A ORDEM importa: o último adicionado é o mais EXTERNO (executa primeiro na
    # entrada e por último na saída). RequestContext fica o mais externo (envolve
    # tudo: atribui X-Request-ID e loga o resultado final); SecurityHeaders logo
    # abaixo, para que TODAS as respostas — inclusive rejeições (400/413/429) —
    # recebam os cabeçalhos de segurança. ErrorBoundary logo abaixo do
    # SecurityHeaders: o 500 que ele gera nasce DENTRO da pilha e sai blindado
    # (headers) e logado (access line) — em vez de escapar para o text/plain
    # do ServerErrorMiddleware do Starlette.
    # Ordem de execução na entrada:
    #   RequestContext -> SecurityHeaders -> ErrorBoundary -> TrustedHost
    #   -> CORS -> BodySize -> SlowAPI -> app
    if settings.rate_limit_enabled:
        app.add_middleware(SlowAPIMiddleware)

    app.add_middleware(BodySizeLimitMiddleware, max_body_size=settings.max_body_size)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(ErrorBoundaryMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=settings.hsts_enabled)
    app.add_middleware(
        RequestContextMiddleware,
        log_client_ip=settings.log_client_ip,
        trust_proxy=settings.trust_proxy,
        num_trusted_proxies=settings.num_trusted_proxies,
    )

    # Feature-first: cada feature expõe seu router e o composition root os
    # inclui aqui, sob o prefixo da API. (Sem agregador intermediário.)
    app.include_router(health.router, prefix=settings.api_v1_prefix)

    @app.get("/")
    def root(request: Request) -> dict[str, str]:
        """Rota raiz com informações básicas da API.

        Lê request.app.state.settings (não o get_settings() global cacheado):
        como toda rota, respeita o Settings injetado deste app — e, definida
        no composition root, existe em qualquer app de create_app, não só no
        singleton de módulo.
        """
        app_settings: Settings = request.app.state.settings
        body = {"app": app_settings.app_name}
        # Não anuncia /docs em produção (lá a documentação está desligada).
        if not app_settings.is_production:
            body["docs"] = "/docs"
        return body

    return app


app = create_app()
