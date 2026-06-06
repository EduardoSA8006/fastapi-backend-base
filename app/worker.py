"""Aplicação Celery (worker + beat) do MyApp.

SEGURANÇA: o broker e o result backend vivem numa instância Redis DEDICADA
(redis-celery), separada do Redis do rate-limit. O docker-compose reforça
isso por rede: worker/beat entram em data_net + celery_net e NÃO têm rota
para ratelimit_net — um task comprometido não alcança as chaves do
rate-limit. O guard abaixo torna a separação um invariante também de config:
em produção, broker/backend na mesma instância do rate-limit recusam o boot.
"""

from celery import Celery

from app.core.config import get_settings
from app.core.security_guards import validate_celery_security

settings = get_settings()

# Fail-closed também no processo do worker/beat (que não chama create_app):
# senha fraca, scheme não-redis ou instância compartilhada com o rate-limit
# derrubam o boot em produção antes de tocar o broker.
validate_celery_security(settings)

celery_app = Celery(
    "myapp",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    # Serialização json-only nas TRÊS superfícies (tasks, resultados e
    # eventos). accept_content cobre o que o worker desserializa;
    # result_accept_content cobre o que o PRODUTOR desserializa ao ler
    # resultados — sem isso, um result backend comprometido vira vetor de
    # pickle-RCE no processo da API. event_serializer cobre o mailbox de
    # eventos/controle.
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_accept_content=["json"],
    event_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # ACK só DEPOIS de executar: um worker morto no meio do task (OOM, evict,
    # SIGKILL) redelivra a mensagem em vez de perdê-la. Exige tasks
    # idempotentes — regra da casa para qualquer task novo.
    task_acks_late=True,
    # Mensagem de um worker perdido volta para a fila (par do acks_late) em
    # vez de ficar presa como "em andamento" para sempre.
    task_reject_on_worker_lost=True,
    # 1 task por processo-filho por vez: escalonamento justo e raio de
    # explosão mínimo num crash (com o default 4, um pod morto redelivra
    # 4 x concurrency mensagens de uma vez).
    worker_prefetch_multiplier=1,
    # Resultados não vivem para sempre no backend (Redis com maxmemory +
    # noeviction PARARIA de aceitar escritas quando cheio — TTL finito evita
    # chegar lá). 24h é generoso para qualquer debug.
    result_expires=86400,
    # Tetos de relógio: um task pendurado (DNS/socket sem timeout) não pode
    # prender o slot para sempre. O soft levanta SoftTimeLimitExceeded dentro
    # do task (dá para limpar recursos); o hard é SIGKILL incondicional.
    task_soft_time_limit=60,
    task_time_limit=90,
    # Não sequestra o root logger — a app configura o próprio logging.
    worker_hijack_root_logger=False,
    # Comportamento explícito (default mudou no Celery 6): reconecta ao
    # broker no startup em vez de falhar de primeira (ordem de subida dos
    # containers não é determinística mesmo com depends_on).
    broker_connection_retry_on_startup=True,
    # Execução síncrona in-process — SÓ para testes (ver Settings).
    task_always_eager=settings.celery_task_always_eager,
)


@celery_app.task(name="core.ping")
def ping() -> str:
    """Task de debug/heartbeat: valida o pipeline beat → broker → worker.

    Sem efeitos colaterais e idempotente por construção. Agendado a cada 60s
    pelo beat — também mantém o arquivo de schedule fresco, o que o
    healthcheck do container beat usa como sinal de vida.
    """
    return "pong"


# Schedule do beat. Por enquanto só o heartbeat (não há tasks de domínio);
# schedules reais entram aqui junto com as features. Enquanto houver UMA
# réplica de beat, beat_schedule basta; se a topologia escalar, migrar para
# celery-redbeat (scheduler com lock distribuído) para não duplicar disparos.
celery_app.conf.beat_schedule = {
    "celery-pipeline-heartbeat": {
        "task": "core.ping",
        # Cadência configurável (CELERY_HEARTBEAT_SECONDS): o operador retuna
        # sem mudar código; a integração do pipeline usa 1s.
        "schedule": settings.celery_heartbeat_seconds,
        # Heartbeat atrasado não tem valor — descarta em vez de empilhar.
        "options": {"expires": settings.celery_heartbeat_seconds * 0.8},
    },
}
