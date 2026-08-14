"""Fila de tarefas assíncrona (TaskIQ) do MyApp — worker + scheduler.

SEGURANÇA: broker e result backend vivem numa instância Redis DEDICADA
(redis-taskiq), separada do Redis do rate-limit. O docker-compose reforça
isso por rede: worker/scheduler entram em data_net + taskiq_net e NÃO têm rota
para ratelimit_net. O guard abaixo torna a separação um invariante de config:
em produção, broker/backend na mesma instância do rate-limit recusam o boot.

Serialização JSON/ORJSON-only (sem pickle) nas mensagens e resultados — um
broker ou result backend comprometido não vira vetor de desserialização-RCE.
"""

from datetime import timedelta

from taskiq import AsyncBroker, InMemoryBroker, TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource
from taskiq.serializers import ORJSONSerializer
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app.core.config import get_settings
from app.core.security_guards import validate_taskiq_security

settings = get_settings()

# Fail-closed também no processo do worker/scheduler (que não chama create_app):
# senha fraca, scheme não-redis ou instância compartilhada com o rate-limit
# derrubam o boot em produção antes de tocar o broker.
validate_taskiq_security(settings)

# Chave do marcador de heartbeat (gravada pela task agendada; lida pelo
# healthcheck do scheduler para provar o pipeline scheduler→broker→worker).
HEARTBEAT_KEY = "myapp:taskiq:heartbeat"


def _build_broker() -> AsyncBroker:
    """Broker real (Redis Streams) ou InMemoryBroker (testes).

    RedisStreamBroker usa Streams + consumer group: o ack ocorre após a
    execução (via `--ack-type when_executed`), redelivrando em crash — o
    equivalente ao acks_late + reject_on_worker_lost do Celery. Resultados
    expiram em 24h (o Redis do broker roda com noeviction; TTL finito evita
    encher). ORJSON explícito bloqueia pickle nas duas pontas.
    """
    if settings.taskiq_in_memory:
        return InMemoryBroker().with_serializer(ORJSONSerializer())

    # serializer= EXPLÍCITO: o taskiq-redis usa PickleSerializer por default no
    # result backend e o .with_serializer() do broker NÃO propaga para cá —
    # sem isto, wait_result() rodaria pickle.loads() em bytes do Redis DB 1
    # (vetor de RCE). ORJSON força JSON-only também na leitura de resultados.
    result_backend: RedisAsyncResultBackend[object] = RedisAsyncResultBackend(
        redis_url=settings.taskiq_result_backend,
        result_ex_time=86400,
        serializer=ORJSONSerializer(),
    )
    return (
        RedisStreamBroker(url=settings.taskiq_broker_url)
        .with_result_backend(result_backend)
        .with_serializer(ORJSONSerializer())
    )


broker = _build_broker()

# Scheduler: publica as tasks marcadas com `schedule=` na cadência configurada.
# EXATAMENTE 1 réplica no compose (dois schedulers = disparo em dobro).
scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])


@broker.task(
    task_name="core.ping",
    schedule=[{"interval": timedelta(seconds=settings.taskiq_heartbeat_seconds)}],
)
async def ping() -> str:
    """Task AGENDADA de heartbeat: valida o pipeline scheduler→broker→worker.

    ÚNICA escritora do marcador `myapp:taskiq:heartbeat`. Idempotente. Além de
    retornar "pong", grava o marcador com TTL (3x a cadência) — o healthcheck do
    container scheduler usa o frescor desse marcador como sinal de vida do
    pipeline inteiro. Como só o disparo agendado renova o marcador, um scheduler
    morto (mesmo com worker vivo) deixa o marcador expirar e o healthcheck do
    scheduler falha — o probe do worker (core.healthcheck_ping) NÃO o mascara.

    Em modo in-memory (testes/CI) não há Redis real nem container scheduler,
    então o marcador não teria consumidor: pula a escrita. O round-trip do
    resultado ("pong") já prova o pipeline in-process.
    """
    if not settings.taskiq_in_memory:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.taskiq_broker_url)
        try:
            ttl = max(int(settings.taskiq_heartbeat_seconds * 3), 1)
            await client.set(HEARTBEAT_KEY, "pong", ex=ttl)
        finally:
            await client.aclose()
    return "pong"


@broker.task(task_name="core.healthcheck_ping")
async def healthcheck_ping() -> str:
    """Probe de liveness do WORKER: round-trip puro pelo broker, SEM marcador.

    Enfileirada pelo healthcheck do container worker (a cada 30s). Diferente do
    `ping` agendado, NÃO grava `myapp:taskiq:heartbeat`: assim o probe frequente
    do worker não renova o marcador do scheduler e não pode mascarar um scheduler
    morto. Prova exclusivamente que o loop de consumo do worker está saudável.
    """
    return "pong"
