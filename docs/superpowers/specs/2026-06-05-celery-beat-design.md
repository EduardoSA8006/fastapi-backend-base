# Celery + Celery Beat — fila de tarefas assíncronas (máxima segurança)

Data: 2026-06-05
Branch alvo: `seguranca/hardening-config-ratelimit`
Status: aprovado

## Objetivo

Adicionar processamento assíncrono (Celery worker) e agendamento periódico
(Celery Beat) ao MyApp, com a postura de segurança mais forte possível —
indo além do padrão do `portfolio-monorepo` onde houver oportunidade de
reforço. Tudo roda em containers na rede interna do Docker, sem nada exposto
ao host.

## Escopo

Infra + skeleton do app Celery + 1 task de debug (`ping`). SEM tasks de
domínio (ainda não há domínio). O `beat_schedule` agenda apenas o `ping`
(heartbeat do pipeline). Tasks reais entram com as features.

## Decisões de design (e por quê)

Referência de partida: `portfolio-monorepo` (`app/worker.py`,
`docker-compose.yml` — serviços `celery`, `celery-beat`, `redis_celery`).
O usuário pediu para NÃO seguir o portfolio cegamente: implementar o mais
seguro possível, reforçando o que o portfolio não tem.

### Reforços ALÉM do portfolio

1. **Isolamento do Redis do rate-limit em relação aos tasks.** O portfolio
   separa o Redis do Celery; aqui, além disso, o worker/beat NÃO têm rota
   de rede para o Redis do rate-limit (segmentação em 3 redes).
2. **`result_accept_content=["json"]`** — bloqueia desserialização não-JSON
   (pickle) também na LEITURA do result backend, não só nos tasks.
3. **Guard que força a separação de instâncias**: em produção, o boot falha
   se o broker do Celery apontar para o mesmo host:port do
   `rate_limit_storage_uri` — a separação vira invariante arquitetural.
4. **Guards rodam também no worker** (no import de `app.worker`), não só na
   API — o worker não chama `create_app`, então validação compartilhada.
5. **`REDISCLI_AUTH` no healthcheck do redis-celery** — senha fora do
   `argv`/`ps` (o `redis` atual do myapp ainda usa `-a`; corrigir o
   existente fica fora de escopo).
6. **Worker sem chatter peer-a-peer**: `--without-gossip --without-mingle
   --without-heartbeat` (um único worker; menos tráfego de controle no
   broker = superfície menor). O control mailbox (pidbox) permanece, pois o
   healthcheck `celery inspect ping` depende dele.

## Arquitetura de rede (3 redes nomeadas)

| Rede | Membros | Justificativa |
|------|---------|---------------|
| `data_net` | api, worker, beat, db, minio | tasks legítimos usam banco/storage |
| `ratelimit_net` | api, redis | worker/beat SEM rota — task comprometido não alcança chaves do rate-limit |
| `celery_net` | api, worker, beat, redis-celery | broker/backend isolado; api entra só para despachar `.delay()` |

Nenhum serviço novo publica porta no host. O compose passa a declarar
`networks:` explicitamente (a rede default deixa de ser usada).

## Mudanças

### 1. `docker-compose.yml`

**`redis-celery`** (novo): broker + result backend dedicados.
- `redis:7-alpine` (mesma imagem do redis atual).
- `command` via `sh -c` com `exec redis-server` para expandir a senha:
  `--requirepass "$CELERY_REDIS_PASSWORD"`, comandos destrutivos renomeados
  para vazio (`FLUSHALL`, `FLUSHDB`, `KEYS`, `CONFIG`, `DEBUG`),
  `--maxmemory 128mb --maxmemory-policy noeviction` (broker não descarta
  mensagens silenciosamente; produção deve monitorar uso),
  `--appendonly no` (dados transitórios).
- Sem `ports:`; `expose: ["6379"]`; rede `celery_net` apenas.
- Healthcheck com `REDISCLI_AUTH` (senha fora do argv).
- Hardening: `no-new-privileges`, `cap_drop: ALL`, limites de recursos.

**`worker`** (novo): `build: .` (mesma imagem da api).
- `command: ["celery", "-A", "app.worker", "worker", "--loglevel=info",
  "--concurrency=2", "--without-gossip", "--without-mingle",
  "--without-heartbeat"]`.
- Entrypoint reutilizado com `RUN_MIGRATIONS_ON_START: "false"` (espera o
  DB, não migra — migração é responsabilidade da api/etapa de deploy).
- `depends_on`: db e redis-celery saudáveis.
- Healthcheck: `celery -A app.worker inspect ping -d celery@$HOSTNAME -t 5`
  (round-trip real pelo broker; processo vivo ≠ worker funcional).
- Hardening idêntico à api: `read_only` + `tmpfs /tmp`, `no-new-privileges`,
  `cap_drop: ALL`, `pids_limit`, `cpus`/`mem_limit`.
- Redes: `data_net` + `celery_net` (NUNCA `ratelimit_net`).

**`beat`** (novo): mesma imagem.
- `command: ["celery", "-A", "app.worker", "beat", "--loglevel=info",
  "--schedule=/tmp/celerybeat-schedule"]` — schedule em tmpfs (efêmero por
  design; o beat reconstrói do `beat_schedule` no boot).
- EXATAMENTE 1 réplica (2 beats = tasks duplicados; se a topologia escalar,
  migrar para celery-redbeat com lock distribuído — documentado).
- Healthcheck por frescor do arquivo de schedule (mtime < 120s): o `ping`
  agendado a cada 60s mantém o arquivo fresco; beat travado = stale = unhealthy.
- Mesmo hardening e redes do worker.

**`api`**: ganha `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` no environment
e entra nas 3 redes. `db`, `redis`, `minio` migram para as redes nomeadas
conforme a tabela.

### 2. `app/worker.py` (novo) — Celery app endurecido

```python
celery_app = Celery("myapp", broker=..., backend=...)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_accept_content=["json"],   # reforço além do portfolio
    event_serializer="json",
    timezone="UTC", enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    result_expires=86400,
    task_soft_time_limit=60, task_time_limit=90,
    worker_hijack_root_logger=False,
    broker_connection_retry_on_startup=True,
    task_always_eager=settings.celery_task_always_eager,  # testes
)
```

- `validate_celery_security(settings)` chamado no import (fail-closed
  também no processo do worker).
- Task `ping` (retorna `"pong"`) — valida o pipeline e serve de heartbeat.
- `beat_schedule = {"celery-pipeline-heartbeat": {task: ping, schedule: 60.0}}`
  com comentário de que é placeholder/heartbeat, substituível por tasks reais.

### 3. `app/core/config.py` — settings novas (lowercase)

```python
celery_broker_url: str = "redis://:myapp@redis-celery:6379/0"
celery_result_backend: str = "redis://:myapp@redis-celery:6379/1"
celery_task_always_eager: bool = False
```

(DB 0 = broker, DB 1 = results — keyspaces separados na instância dedicada.)

### 4. Guard compartilhado — `validate_celery_security(settings)`

Vive em `app/core/security_guards.py` (novo módulo), que também recebe as
constantes `WEAK_PASSWORDS` e `WEAK_MINIO_USERS` movidas de `app/main.py`
(refactor mínimo: `main.py` passa a importá-las de lá — um único lugar para a
política de credenciais fracas, importável por `main.py` e `app/worker.py`
sem ciclo). Em produção, levanta `ValueError` se:
- broker ou backend não começam com `redis://`/`rediss://`;
- senha do broker/backend em `WEAK_PASSWORDS`;
- broker host:port == rate_limit_storage_uri host:port (mesma instância —
  viola a separação).

Chamado em: `app/worker.py` (import) e `app/main.py` (bloco
`is_production` do `create_app`).

### 5. `pyproject.toml`

`celery[redis] (>=5.6,<6.0)` nas dependências main.

### 6. `.env.example`

Bloco Celery: `CELERY_REDIS_PASSWORD`, `CELERY_BROKER_URL`,
`CELERY_RESULT_BACKEND`, nota de produção (senha forte obrigatória,
instância separada do rate-limit obrigatória, 1 réplica de beat).

## Testes (TDD — sem containers reais)

- `validate_celery_security`: rejeita senha fraca/ausente; rejeita scheme
  não-redis; rejeita broker == instância do rate-limit; aceita config forte
  e separada. (Fora de produção: não levanta.)
- `create_app` produção: config de Celery fraca → boot falha (espelha os
  testes de guard existentes; `_prod_settings` ganha celery URLs fortes).
- `celery_app.conf`: json-only (task, result, event), acks_late, prefetch=1,
  result_expires, time limits — trava regressão das flags de segurança.
- Task `ping` eager retorna `"pong"`.

## Fora de escopo (YAGNI)

- Tasks de domínio e schedules reais.
- celery-redbeat / múltiplas réplicas de beat.
- Flower ou qualquer UI de monitoramento (superfície extra).
- TLS (rediss://) na rede interna do mesmo host — o guard já aceita rediss://
  se um dia o broker cruzar a fronteira de host.
- Corrigir o healthcheck do `redis` existente para REDISCLI_AUTH (melhoria
  registrada, mas é mudança em serviço já entregue — fazer em tarefa própria).
