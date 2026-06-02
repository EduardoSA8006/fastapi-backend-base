import logging
from urllib.parse import urlparse

from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.engine import make_url
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

logger = logging.getLogger("classup")

# Senhas notoriamente fracas/default que não podem ir para produção.
_WEAK_DB_PASSWORDS = {"classup", "postgres", "password", "changeme", "admin", ""}


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
        # Paridade com o guard do banco: senha default/fraca (ou ausente) no
        # Redis também é barrada. O Redis não fica exposto ao host, mas a
        # inconsistência não se justifica — e protege as chaves do rate-limit de
        # acesso/flush por um vizinho de rede comprometido. Cobre redis:// e
        # rediss:// (TLS); senha ausente vira "" (presente em _WEAK_DB_PASSWORDS).
        if settings.rate_limit_enabled and settings.rate_limit_storage_uri.startswith(
            "redis"
        ):
            redis_password = urlparse(settings.rate_limit_storage_uri).password or ""
            if redis_password in _WEAK_DB_PASSWORDS:
                raise ValueError(
                    "Senha do Redis default/fraca (ou ausente) não é permitida em "
                    "produção. Use uma senha forte na RATE_LIMIT_STORAGE_URI "
                    "(redis://:SENHA@host:porta/db)."
                )
        # Credenciais default/fracas de banco não podem ir para produção
        # (SQLite não tem senha, então é ignorado).
        if not settings.database_url.startswith("sqlite"):
            db_password = make_url(settings.database_url).password or ""
            if db_password in _WEAK_DB_PASSWORDS:
                raise ValueError(
                    "Senha de banco default/fraca não é permitida em produção. "
                    "Use uma senha forte na DATABASE_URL."
                )
        # Atrás de proxy reverso (cenário do deploy recomendado), sem trust_proxy
        # o IP de conexão é o do proxy — todos os clientes caem num único bucket
        # (rate-limit colapsado / auto-DoS). Avisa para o operador configurar.
        if settings.rate_limit_enabled and not settings.trust_proxy:
            logger.warning(
                "ENVIRONMENT=production com TRUST_PROXY=false: se houver proxy "
                "reverso à frente, o rate-limit colapsa num único bucket (IP do "
                "proxy). Defina TRUST_PROXY=true e NUM_TRUSTED_PROXIES corretamente."
            )
        # O risco simétrico: confiar no X-Forwarded-For sem proxy real à frente
        # deixa o cliente forjar o IP (cada requisição num bucket novo → rate-limit
        # inútil). A app não tem como detectar a topologia de rede com segurança,
        # então não falha o boot (TRUST_PROXY=true é legítimo atrás de LB); avisa
        # de forma explícita para o operador confirmar a exposição.
        elif settings.rate_limit_enabled and settings.trust_proxy:
            logger.warning(
                "ENVIRONMENT=production com TRUST_PROXY=true: confiar no "
                "X-Forwarded-For só é seguro atrás de EXATAMENTE "
                f"NUM_TRUSTED_PROXIES={settings.num_trusted_proxies} proxy(ies) "
                "reverso(s) que reescrevem o header. Se a app estiver exposta "
                "diretamente, o cliente forja o IP e burla o rate-limit. Confirme "
                "a topologia de rede."
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

    app.add_middleware(BodySizeLimitMiddleware, max_body_size=settings.max_body_size)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=settings.hsts_enabled)
    app.add_middleware(RequestContextMiddleware, log_client_ip=settings.log_client_ip)

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()


@app.get("/")
def root() -> dict[str, str]:
    """Rota raiz com informações básicas da API."""
    settings = get_settings()
    body = {"app": settings.app_name}
    # Não anuncia /docs em produção (lá a documentação está desligada).
    if not settings.is_production:
        body["docs"] = "/docs"
    return body
