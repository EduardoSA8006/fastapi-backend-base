"""Integração do TaskIQ com broker REAL (Redis 8 via testcontainers).

Round-trip de verdade: task publicada no RedisStreamBroker (Redis Streams +
consumer group), consumida por um worker EMBUTIDO in-process
(`run_receiver_task`, o helper canônico do TaskIQ para rodar um Receiver
programaticamente) e o RESULTADO lido do result backend.

O alvo do teste é o invariante que o fix de segurança endureceu: publicar →
consumir → LER o resultado "pong" — exercitando o result backend e o
round-trip ORJSON (JSON-only, sem pickle) nas duas pontas.

NÃO usa `app.worker.broker`: sob a suíte a config força InMemoryBroker
(TASKIQ_IN_MEMORY=true), então o broker importado in-process não é Redis. Aqui
construímos um RedisStreamBroker STANDALONE apontando para o container.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest
from taskiq import AsyncBroker
from taskiq.api import run_receiver_task
from taskiq.serializers import ORJSONSerializer
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from tests.integration.conftest import redis_container

pytestmark = pytest.mark.integration

_PASSWORD = "S3nhaTesteTaskiq123"


@pytest.fixture
async def real_broker() -> AsyncIterator[AsyncBroker]:
    """RedisStreamBroker real (DB 0) + result backend real (DB 1), ORJSON.

    `startup()` declara o consumer group ANTES de qualquer publish — assim o
    Receiver (que lê `>` = mensagens nunca entregues ao group) recebe o ping,
    sem corrida de criação de grupo.
    """
    with redis_container(_PASSWORD) as base:
        backend: RedisAsyncResultBackend[object] = RedisAsyncResultBackend(
            redis_url=f"{base}/1",
            result_ex_time=3600,
            serializer=ORJSONSerializer(),
        )
        broker = (
            RedisStreamBroker(url=f"{base}/0")
            .with_result_backend(backend)
            .with_serializer(ORJSONSerializer())
        )

        @broker.task(task_name="core.ping")
        async def ping() -> str:
            return "pong"

        await broker.startup()
        try:
            yield broker
        finally:
            await broker.shutdown()


async def test_ping_round_trip_pelo_broker_real(real_broker: AsyncBroker) -> None:
    # Sobe o worker embutido em background (Receiver real consumindo o stream)
    # e publica o ping. O worker executa a task e grava o resultado no backend.
    worker = asyncio.create_task(run_receiver_task(real_broker, run_startup=False))
    try:
        ping = real_broker.find_task("core.ping")
        assert ping is not None, "task core.ping não registrada no broker"

        kicked = await ping.kiq()
        result = await asyncio.wait_for(kicked.wait_result(timeout=30), timeout=35)

        assert not result.is_err, f"task falhou: {result!r}"
        assert result.return_value == "pong"
    finally:
        # run_receiver_task propaga CancelledError por design ao encerrar.
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
