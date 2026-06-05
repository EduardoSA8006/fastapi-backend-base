from typing import Any

import pytest

from app.core.config import Settings
from app.core.security_guards import validate_celery_security

# URLs fortes para os cenários que devem PASSAR pelos guards.
_STRONG_BROKER = "redis://:S3nhaForteCelery123@redis-celery:6379/0"
_STRONG_BACKEND = "redis://:S3nhaForteCelery123@redis-celery:6379/1"


def _prod(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "production",
        "celery_broker_url": _STRONG_BROKER,
        "celery_result_backend": _STRONG_BACKEND,
        "rate_limit_storage_uri": "redis://:S3nhaForteRedis123@redis:6379/0",
    }
    base.update(overrides)
    return Settings(**base)


# --- validate_celery_security: fail-closed em produção ---


def test_strong_and_separate_config_passes() -> None:
    # Configuração válida (senha forte, instância dedicada) não levanta.
    validate_celery_security(_prod())


@pytest.mark.parametrize("weak", ["classup", "password", ""])
def test_rejects_weak_broker_password(weak: str) -> None:
    cred = f":{weak}@" if weak else ""
    with pytest.raises(ValueError, match="Celery"):
        validate_celery_security(
            _prod(celery_broker_url=f"redis://{cred}redis-celery:6379/0")
        )


def test_rejects_explicit_empty_password_with_at() -> None:
    # Senha explicitamente vazia ("redis://:@host") difere de credencial
    # ausente no urlparse ("" vs None) — ambas devem ser barradas.
    with pytest.raises(ValueError, match="Celery"):
        validate_celery_security(
            _prod(celery_broker_url="redis://:@redis-celery:6379/0")
        )


def test_rejects_broker_on_rate_limit_instance_with_implicit_port() -> None:
    # Porta omitida = 6379 implícito: redis://host/0 e redis://host:6379/0
    # são a MESMA instância — o guard precisa normalizar antes de comparar.
    with pytest.raises(ValueError, match="mesma instância"):
        validate_celery_security(
            _prod(celery_broker_url="redis://:S3nhaForteCelery123@redis/5")
        )


def test_rejects_weak_result_backend_password() -> None:
    # O backend também carrega credencial — mesma régua do broker.
    with pytest.raises(ValueError, match="Celery"):
        validate_celery_security(
            _prod(celery_result_backend="redis://:classup@redis-celery:6379/1")
        )


@pytest.mark.parametrize(
    "url",
    [
        "memory://",
        "amqp://user:S3nhaForte123@rabbit:5672//",
        "sqla+sqlite:///celery.db",
    ],
)
def test_rejects_non_redis_broker_scheme(url: str) -> None:
    # Só redis:// / rediss:// são aceitos (broker dedicado da arquitetura).
    with pytest.raises(ValueError, match="Celery"):
        validate_celery_security(_prod(celery_broker_url=url))


def test_rejects_broker_on_rate_limit_redis_instance() -> None:
    # Invariante arquitetural: o broker NÃO pode ser a mesma instância
    # (host:port) do Redis do rate-limit — um task comprometido não pode
    # alcançar as chaves do rate-limit nem compartilhar o raio de explosão.
    with pytest.raises(ValueError, match="mesma instância"):
        validate_celery_security(
            _prod(celery_broker_url="redis://:S3nhaForteCelery123@redis:6379/5")
        )


def test_no_guard_outside_production() -> None:
    # Fora de produção os guards não levantam (dev usa defaults fracos).
    validate_celery_security(Settings(environment="development"))


# --- create_app chama o guard em produção ---


def test_create_app_production_rejects_weak_celery_password() -> None:
    from app.main import create_app

    settings = Settings(
        environment="production",
        trusted_hosts=["api.test"],
        rate_limit_enabled=False,
        minio_root_user="classup-svc-7f3a",
        minio_root_password="S3nhaForteMinio123",
        celery_broker_url="redis://:classup@redis-celery:6379/0",
        celery_result_backend=_STRONG_BACKEND,
    )
    with pytest.raises(ValueError, match="Celery"):
        create_app(settings)


# --- defaults das settings ---


def test_celery_settings_defaults() -> None:
    settings = Settings()
    assert settings.celery_broker_url.startswith("redis://")
    assert "redis-celery" in settings.celery_broker_url
    assert settings.celery_broker_url.endswith("/0")
    assert settings.celery_result_backend.endswith("/1")
    assert settings.celery_task_always_eager is False


# --- configuração de segurança do celery_app ---


def test_celery_app_hardened_conf() -> None:
    from app.worker import celery_app

    conf = celery_app.conf
    # Serialização json-only nas TRÊS superfícies (task, result, eventos):
    # bloqueia desserialização pickle (RCE clássico em filas Python).
    assert conf.task_serializer == "json"
    assert conf.result_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.result_accept_content == ["json"]
    assert conf.event_serializer == "json"
    # Confiabilidade: ack só após executar; redelivery em crash; 1 task por vez.
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    # Resultados não crescem para sempre; tasks não penduram o slot.
    assert conf.result_expires == 86400
    assert conf.task_soft_time_limit == 60
    assert conf.task_time_limit == 90
    assert conf.timezone == "UTC"
    assert conf.enable_utc is True


def test_celery_beat_schedules_heartbeat() -> None:
    from app.worker import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "celery-pipeline-heartbeat" in schedule
    assert schedule["celery-pipeline-heartbeat"]["schedule"] == 60.0


def test_heartbeat_interval_e_configuravel() -> None:
    # O operador retuna a cadência via env (CELERY_HEARTBEAT_SECONDS) sem
    # mudar código — usado também pela integração do beat (1s).
    assert Settings().celery_heartbeat_seconds == 60.0
    assert Settings(celery_heartbeat_seconds=1.0).celery_heartbeat_seconds == 1.0


# --- task de debug em modo eager ---


def test_ping_task_eager_returns_pong() -> None:
    from app.worker import celery_app, ping

    celery_app.conf.task_always_eager = True
    try:
        result = ping.delay()
        assert result.get(timeout=5) == "pong"
    finally:
        celery_app.conf.task_always_eager = False
