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
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from redis.asyncio import Redis
from taskiq import AsyncBroker, TaskiqResult
from taskiq.api import run_receiver_task
from taskiq.serializers import ORJSONSerializer
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker
from taskiq_redis.exceptions import ResultIsMissingError

from tests.integration.conftest import redis_container

pytestmark = pytest.mark.integration

_PASSWORD = "S3nhaTesteTaskiq123"

# Nomes-padrão do RedisStreamBroker: a stream e o consumer group chamam-se
# ambos "taskiq" (defaults do broker) — usados para inspecionar o PEL (XPENDING).
_STREAM = "taskiq"
_GROUP = "taskiq"


async def _poll_ate(
    cond: Callable[[], Awaitable[bool]],
    *,
    deadline_s: float,
) -> bool:
    """Aguarda `cond()` virar True, com deadline (fail-fast, sem sleep infinito)."""
    fim = time.monotonic() + deadline_s
    while time.monotonic() < fim:
        if await cond():
            return True
        await asyncio.sleep(0.05)
    return False


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


async def test_task_que_falha_grava_erro_e_nao_reentrega_em_loop() -> None:
    """Confiabilidade sob task-exceção (modelo Streams do TaskIQ, NÃO Celery).

    Evidência empírica do TaskIQ 0.12.4 instalado: com `RedisStreamBroker` e o
    Receiver default (`ack_type=WHEN_SAVED`), uma task que simplesmente RAISE é
    *executada* (rodou, mesmo lançando), o erro é gravado no result backend e a
    mensagem é ACKED. NÃO há reentrega automática por exceção — o worker não
    fica em loop reprocessando a mesma falha.

    A garantia de confiabilidade REAL aqui não é "reexecuta até passar" (isso é
    Celery), e sim: o trabalho NÃO some em silêncio — a falha fica registrada
    (`result.is_err`) e o stream não acumula pendência (XPENDING zera).
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

        @broker.task(task_name="core.boom")
        async def boom() -> str:
            raise RuntimeError("estouro proposital na task")

        await broker.startup()
        worker = asyncio.create_task(run_receiver_task(broker, run_startup=False))
        try:
            task = broker.find_task("core.boom")
            assert task is not None, "task core.boom não registrada no broker"

            kicked = await task.kiq()
            result = await asyncio.wait_for(kicked.wait_result(timeout=30), timeout=35)

            # A falha foi CAPTURADA, não perdida: erro propagado ao backend.
            assert result.is_err, f"esperado is_err=True, veio {result!r}"
            assert result.return_value is None
            assert result.error is not None, "erro não deveria vazar como None"

            # A mensagem foi ACKED (WHEN_SAVED): sem reentrega pendente no PEL.
            inspector = Redis.from_url(f"{base}/0")
            try:

                async def _sem_pendencias() -> bool:
                    pending = await inspector.xpending(_STREAM, _GROUP)
                    return int(pending["pending"]) == 0

                acked = await _poll_ate(_sem_pendencias, deadline_s=10)
                assert acked, (
                    "mensagem da task-falha ficou pendente (reentrega em loop?)"
                )
            finally:
                await inspector.aclose()
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            await broker.shutdown()


async def test_mensagem_nao_ackada_fica_pendente_e_reclaimavel() -> None:
    """Modelo REAL de reentrega do TaskIQ Streams: worker morre no meio.

    Complemento do teste acima: a reentrega no modelo Streams NÃO acontece por
    exceção da task, e sim quando a mensagem fica *pendente* — worker leu e
    caiu antes do ack. Aqui simulamos isso no nível do Redis (o que o broker faz
    por baixo): um consumer lê pela consumer group e NUNCA ackar; a mensagem
    permanece no PEL do grupo e é RECLAMÁVEL por outro consumer (XAUTOCLAIM),
    exatamente o caminho de `listen()` do RedisStreamBroker. Ou seja: a fila NÃO
    perde trabalho quando um worker morre.
    """
    with redis_container(_PASSWORD) as base:
        redis = Redis.from_url(f"{base}/0")
        try:
            await redis.xgroup_create(_STREAM, _GROUP, id="0", mkstream=True)
            msg_id = await redis.xadd(_STREAM, {b"data": b"trabalho-critico"})

            # "Worker morto": lê a mensagem pela group, mas nunca dá ack.
            entregue = await redis.xreadgroup(
                _GROUP, "worker-morto", {_STREAM: ">"}, count=10
            )
            assert entregue, "mensagem não foi entregue ao consumer"

            # Trabalho preservado: continua PENDENTE no PEL do grupo.
            pending = await redis.xpending(_STREAM, _GROUP)
            assert int(pending["pending"]) == 1

            # E é RECLAMÁVEL por outro worker (min_idle_time=0 → reentrega já).
            _cursor, claimed, _deleted = await redis.xautoclaim(
                _STREAM, _GROUP, "worker-vivo", min_idle_time=0
            )
            assert [mid for mid, _fields in claimed] == [msg_id]
        finally:
            await redis.aclose()


async def test_result_backend_expira_apos_ttl() -> None:
    """TTL do result backend: `result_ex_time=1` → resultado some após ~1s.

    Publica um resultado com expiração de 1s, confirma leitura imediata e, após
    esperar > TTL (bounded, 1.5s), confirma que expirou: `is_result_ready` False
    e `get_result` levanta `ResultIsMissingError` (chave apagada pelo Redis).
    """
    with redis_container(_PASSWORD) as base:
        backend: RedisAsyncResultBackend[str] = RedisAsyncResultBackend(
            redis_url=f"{base}/1",
            result_ex_time=1,
            serializer=ORJSONSerializer(),
        )
        await backend.startup()
        try:
            task_id = "ttl-efemero-001"
            result: TaskiqResult[str] = TaskiqResult(
                is_err=False, return_value="efemero", execution_time=0.0
            )
            await backend.set_result(task_id, result)

            # Antes do TTL: presente e legível.
            assert await backend.is_result_ready(task_id)
            lido = await backend.get_result(task_id)
            assert lido.return_value == "efemero"

            # Espera > TTL (curto e determinístico).
            await asyncio.sleep(1.5)

            # Depois do TTL: sumiu.
            assert not await backend.is_result_ready(task_id)
            with pytest.raises(ResultIsMissingError):
                await backend.get_result(task_id)
        finally:
            await backend.shutdown()
