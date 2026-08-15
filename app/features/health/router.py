import time

import redis
from fastapi import APIRouter
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import REDIS_PROBE_TIMEOUT_SECONDS, Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Liveness: indica apenas que o processo está no ar.

    Propositalmente RASO — não toca dependências. Serve para o orquestrador
    decidir se reinicia o container (um processo travado). Para saber se a app
    consegue atender (banco/Redis de pé), use /ready.
    """
    return {"status": "ok"}


@router.get("/ready")
def readiness(request: Request) -> JSONResponse:
    """Readiness: verifica as dependências externas (banco e, se aplicável, Redis).

    Retorna 200 só quando todas respondem; caso contrário 503, para o
    orquestrador tirar a réplica do balanceador sem reiniciá-la (diferente do
    liveness). Cada checagem tem timeout curto para não pendurar o probe.

    Anti-amplificação: o resultado é cacheado por readiness_cache_seconds —
    o probe é isento de rate-limit e sem auth, então sem cache cada chamada
    viraria SELECT 1 + PING (carga não-autenticada contra banco/Redis se o
    path vazar para a borda). Rajadas custam <= 1 round-trip por janela.
    """
    # Settings deste app (respeita override de testes); fallback ao global.
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()

    cache_ttl = settings.readiness_cache_seconds
    cached: tuple[float, JSONResponse] | None = getattr(
        request.app.state, "readiness_cache", None
    )
    if cache_ttl > 0 and cached is not None:
        cached_at, cached_response = cached
        if time.monotonic() - cached_at < cache_ttl:
            return cached_response

    checks: dict[str, str] = {}
    ready = True

    # Banco: um SELECT 1 confirma conectividade e que o pool responde.
    try:
        engine = request.app.state.db_engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
        ready = False

    # Redis: só quando é o store do rate-limit (memory:// não tem o que checar).
    if settings.rate_limit_enabled and settings.rate_limit_storage_uri.startswith(
        "redis"
    ):
        client = None
        try:
            client = redis.from_url(
                settings.rate_limit_storage_uri,
                socket_connect_timeout=REDIS_PROBE_TIMEOUT_SECONDS,
                socket_timeout=REDIS_PROBE_TIMEOUT_SECONDS,
            )
            client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error"
            ready = False
        finally:
            if client is not None:
                client.close()

    status_code = 200 if ready else 503
    response = JSONResponse(
        {"status": "ready" if ready else "not ready", "checks": checks},
        status_code=status_code,
    )
    # Cacheia também falhas: protege o backend de rajadas mesmo degradado
    # (recuperação aparece em <= TTL — atraso aceitável para o orquestrador).
    if cache_ttl > 0:
        request.app.state.readiness_cache = (time.monotonic(), response)
    return response
