"""worker_healthcheck sob InMemoryBroker (round-trip in-process, sem Redis)."""

import asyncio

import pytest


def test_healthcheck_main_retorna_0_com_broker_saudavel() -> None:
    # TASKIQ_IN_MEMORY=true (conftest) → broker InMemory; healthcheck_ping
    # executa in-process e retorna "pong" → main() == 0.
    from app import worker_healthcheck

    assert worker_healthcheck.main() == 0


async def test_probe_retorna_1_em_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # Se o resultado nunca chega, _probe degrada para 1 (TimeoutError).
    from app import worker_healthcheck
    from app.worker import healthcheck_ping

    # força timeout curto e um wait_result que pendura
    monkeypatch.setattr(worker_healthcheck, "_TIMEOUT_S", 0.01)

    class _Kicked:
        async def wait_result(self, *_a: object, **_k: object) -> object:
            await asyncio.sleep(10)
            return None

    async def _kiq() -> _Kicked:
        return _Kicked()

    monkeypatch.setattr(healthcheck_ping, "kiq", _kiq)
    assert await worker_healthcheck._probe() == 1
