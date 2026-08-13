from typing import Any

import pytest

from app.core.config import Settings
from app.core.security_guards import validate_taskiq_security

_STRONG_BACKEND = "redis://:S3nhaForteTaskiq123@redis-taskiq:6379/1"


def _prod(**overrides: Any) -> Settings:
    from tests.conftest import STRONG_REDIS_URI, make_prod_settings

    base: dict[str, Any] = {
        "rate_limit_enabled": True,
        "rate_limit_storage_uri": STRONG_REDIS_URI,
    }
    base.update(overrides)
    return make_prod_settings(**base)


def test_strong_and_separate_config_passes() -> None:
    validate_taskiq_security(_prod())


@pytest.mark.parametrize("weak", ["myapp", "password", ""])
def test_rejects_weak_broker_password(weak: str) -> None:
    cred = f":{weak}@" if weak else ""
    with pytest.raises(ValueError, match="TaskIQ"):
        validate_taskiq_security(
            _prod(taskiq_broker_url=f"redis://{cred}redis-taskiq:6379/0")
        )


def test_rejects_explicit_empty_password_with_at() -> None:
    with pytest.raises(ValueError, match="TaskIQ"):
        validate_taskiq_security(
            _prod(taskiq_broker_url="redis://:@redis-taskiq:6379/0")
        )


def test_rejects_broker_on_rate_limit_instance_with_implicit_port() -> None:
    with pytest.raises(ValueError, match="mesma instância"):
        validate_taskiq_security(
            _prod(taskiq_broker_url="redis://:S3nhaForteTaskiq123@redis/5")
        )


def test_rejects_weak_result_backend_password() -> None:
    with pytest.raises(ValueError, match="TaskIQ"):
        validate_taskiq_security(
            _prod(taskiq_result_backend="redis://:myapp@redis-taskiq:6379/1")
        )


@pytest.mark.parametrize(
    "url",
    ["memory://", "amqp://user:S3nhaForte123@rabbit:5672//", "nats://nats:4222"],
)
def test_rejects_non_redis_broker_scheme(url: str) -> None:
    with pytest.raises(ValueError, match="TaskIQ"):
        validate_taskiq_security(_prod(taskiq_broker_url=url))


def test_rejects_broker_on_rate_limit_redis_instance() -> None:
    with pytest.raises(ValueError, match="mesma instância"):
        validate_taskiq_security(
            _prod(taskiq_broker_url="redis://:S3nhaForteTaskiq123@redis:6379/5")
        )


def test_no_guard_outside_production() -> None:
    validate_taskiq_security(Settings(environment="development"))


def test_create_app_production_rejects_weak_taskiq_password() -> None:
    from app.main import create_app

    settings = Settings(
        environment="production",
        trusted_hosts=["api.test"],
        rate_limit_enabled=False,
        minio_root_user="myapp-svc-7f3a",
        minio_root_password="S3nhaForteMinio123",
        taskiq_broker_url="redis://:myapp@redis-taskiq:6379/0",
        taskiq_result_backend=_STRONG_BACKEND,
    )
    with pytest.raises(ValueError, match="TaskIQ"):
        create_app(settings)


def test_taskiq_settings_defaults() -> None:
    settings = Settings()
    assert settings.taskiq_broker_url.startswith("redis://")
    assert "redis-taskiq" in settings.taskiq_broker_url
    assert settings.taskiq_broker_url.endswith("/0")
    assert settings.taskiq_result_backend.endswith("/1")
    # taskiq_in_memory: o conftest exporta TASKIQ_IN_MEMORY=true para a suíte,
    # então uma instância lê True do ambiente. O DEFAULT do campo (o que vale
    # em produção sem a env) é False — é isso que garantimos aqui.
    assert Settings.model_fields["taskiq_in_memory"].default is False


def test_heartbeat_interval_e_configuravel() -> None:
    assert Settings().taskiq_heartbeat_seconds == 60.0
    assert Settings(taskiq_heartbeat_seconds=1.0).taskiq_heartbeat_seconds == 1.0


def test_ping_schedule_registrado() -> None:
    from app.worker import ping

    labels = ping.labels.get("schedule") or []
    assert any("interval" in s for s in labels), "core.ping sem schedule de intervalo"
    assert ping.task_name == "core.ping"


@pytest.mark.asyncio
async def test_ping_in_memory_returns_pong(monkeypatch: pytest.MonkeyPatch) -> None:
    # InMemoryBroker executa in-process; valida o round-trip sem Redis real.
    monkeypatch.setenv("TASKIQ_IN_MEMORY", "true")
    from app.worker import broker, ping

    await broker.startup()
    try:
        task = await ping.kiq()
        result = await task.wait_result(timeout=5)
        assert result.return_value == "pong"
    finally:
        await broker.shutdown()
