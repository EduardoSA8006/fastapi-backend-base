"""Configuração global da suíte.

ENVIRONMENT é OBRIGATÓRIO (sem default — fail-closed contra typo no nome da
variável; ver test_environment_e_obrigatorio_sem_fallback). A suíte roda em
development: define aqui, ANTES de qualquer import de app.*, para que os
módulos com Settings de import-time (app.core.config, app.worker) e todo
`Settings()` dos testes resolvam o ambiente sem precisar de um .env local.
O teste do próprio guard usa monkeypatch.delenv para simular a ausência.
"""

import os

os.environ.setdefault("ENVIRONMENT", "development")

# O pytest é o DONO dos handlers do root durante a suíte (caplog injeta os
# seus). configure_logging agora substitui handlers pré-existentes do root
# (correção do no-op sob gunicorn) — se rodasse aqui, removeria os handlers
# do pytest no primeiro create_app e quebraria o caplog daquele teste.
# Marcar como já-configurado preserva o comportamento efetivo anterior
# (sob pytest o basicConfig sempre foi no-op); o wiring real é coberto
# explicitamente por test_configure_logging_substitui_handlers_pre_existentes
# (que reseta a flag e restaura o estado do root).
import app.core.logging as _app_logging

_app_logging._configured = True

# ---------------------------------------------------------------------------
# Factories compartilhadas (importáveis: `from tests.conftest import ...`).
# Antes duplicadas em 4+ arquivos — aqui é a única definição.
# ---------------------------------------------------------------------------

from typing import Any  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402

# URL de Redis com senha forte para configs de produção válidas.
STRONG_REDIS_URI = "redis://:S3nhaForteRedis123@redis:6379/0"


def make_settings(**overrides: Any) -> Settings:
    """Settings de DEV para testes: rate-limit em memória, host de teste."""
    base: dict[str, Any] = {
        "rate_limit_storage_uri": "memory://",
        "trusted_hosts": ["testserver"],
    }
    base.update(overrides)
    return Settings(**base)


def make_prod_settings(**overrides: Any) -> Settings:
    """Settings de PRODUÇÃO totalmente VÁLIDOS (passam por todos os guards).

    Cada teste quebra UM aspecto via override para exercitar o guard alvo.
    """
    base: dict[str, Any] = {
        "environment": "production",
        "debug": False,
        "trusted_hosts": ["api.test"],
        "rate_limit_enabled": False,  # evita exigir redis na maioria dos testes
        "rate_limit_storage_uri": "memory://",
        "minio_root_user": "myapp-svc-7f3a",
        "minio_root_password": "S3nhaForteMinio123",
        "celery_broker_url": "redis://:S3nhaForteCelery123@redis-celery:6379/0",
        "celery_result_backend": "redis://:S3nhaForteCelery123@redis-celery:6379/1",
    }
    base.update(overrides)
    return Settings(**base)


def make_client(**overrides: Any) -> TestClient:
    """App real (create_app) com Settings de dev + TestClient."""
    from app.main import create_app

    return TestClient(create_app(make_settings(**overrides)))


def make_rl_client(
    *, raise_server_exceptions: bool = True, **overrides: Any
) -> TestClient:
    """Cliente com rota neutra `/_rl` NÃO isenta do rate-limit.

    Os probes são isentos, então testes de rate-limit precisam de um
    endpoint próprio para exercitar o limite.
    """
    from app.main import create_app

    app = create_app(make_settings(**overrides))

    @app.get("/_rl")
    def _rl() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=raise_server_exceptions)
