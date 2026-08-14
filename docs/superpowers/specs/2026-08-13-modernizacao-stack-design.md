# Modernização da stack — Python 3.14, PostgreSQL 18, uv e TaskIQ

- **Data:** 2026-08-13
- **Projeto:** fastapi-backend-base (template de backend FastAPI, security-hardened)
- **Autor do design:** brainstorming assistido (Claude) + Eduardo
- **Status:** aprovado para escrita de plano de implementação

---

## 1. Objetivo

Modernizar toda a stack do template mantendo (e onde possível reforçando) as
garantias de segurança e o gate de qualidade existentes. Cinco frentes:

1. **Python 3.13 → 3.14** (fixar como alvo único).
2. **PostgreSQL 16 → 18**; Redis (servidor) 7 → 8; MinIO → release mais recente.
3. **Poetry → uv** como gerenciador de pacotes/venv/lock.
4. **Celery → TaskIQ** como fila de tarefas assíncrona.
5. Demais dependências de app e dev para as versões mais recentes compatíveis.

Princípio-guia: **não regredir** nenhuma garantia de segurança (guards
fail-closed, segmentação de rede, serialização sem pickle, "processo vivo ≠
funcional" nos healthchecks) nem o gate de cobertura de 90% / mypy `--strict`.

## 2. Decisões tomadas (com justificativa)

| Decisão | Escolha | Justificativa |
|---|---|---|
| Faixa de Python | `>=3.14,<3.15` | Alvo único simplifica o template; permite recursos de 3.14. Abandona 3.12/3.13 conscientemente. |
| Agressividade das deps | Latest de tudo | Template novo em 2026 deve nascer atual; a suíte forte torna seguro. |
| Packaging | uv | Resolução/instalação muito mais rápida (CI/build), tooling unificado; virou o default de mercado. |
| Fila de tarefas | TaskIQ | Async-native (fim do impedance mismatch sync do Celery), DI estilo FastAPI, e destrava o `redis-py` 8. |
| Verificação | Completa (Docker local) | Roda unit + integração + e2e localmente a cada camada. |
| Nomes `redis-celery`/`celery_net` | Renomear p/ `redis-taskiq`/`taskiq_net` | Churn cosmético, mas coerência do template. |

### 2.1 Esclarecimento dos "três redis"

Ponto de confusão recorrente — há três coisas distintas chamadas "redis":

| Camada | Componente | Hoje | Depois |
|---|---|---|---|
| Biblioteca cliente (pip) | `redis-py` | preso em 6.4.x | **8.x** |
| Transporte do Celery | `kombu` | presente (capa o `redis-py` em `<6.5`) | **removido** (é dep transitiva do Celery) |
| Servidor (imagem Docker) | `redis` | `redis:7-alpine` (2 instâncias) | `redis:8-alpine` (2 instâncias) |

- O `kombu` **não** é substituído separadamente: ele sai junto com o Celery.
- Não há terceira instância de servidor: as **duas** instâncias atuais existem
  por **segurança** (rate-limit isolado da fila), não por versão. A separação é
  mantida — só renomeia-se a da fila.
- O conflito `redis<6.5` (kombu) vs `redis>=8` (taskiq-redis) **nunca ocorre**:
  Celery e TaskIQ jamais coexistem — a troca é atômica na Camada 4.

## 3. Estado atual (baseline)

- Python: Docker/CI em 3.13; `requires-python = ">=3.12,<3.15"`; ruff
  `target-version = "py311"`; mypy `python_version = "3.11"` (defasados).
- Postgres `16-alpine`; Redis `7-alpine` (x2); MinIO `RELEASE.2025-09-07T16-13-09Z`.
- Poetry 2.4.1 (`package-mode = false`), `poetry.lock`.
- Fila: Celery 5.6 + kombu, worker + beat, `redis-celery` dedicado, rede
  `celery_net`. `app/worker.py` define `celery_app`, task `core.ping` e
  `beat_schedule`.
- Deps app: fastapi 0.136.3, uvicorn 0.48, sqlalchemy 2.0.50, alembic 1.18.4,
  pydantic-settings 2.14.1, psycopg 3.3.4, slowapi 0.1.9, redis 6.4–9 (capado
  pelo kombu), minio 7.2.20.
- Dev: pytest 9.0.3, pytest-asyncio 1.4.0, httpx 0.28.1, ruff 0.15.15,
  pip-audit 2.10.0, mypy 2.1.0, pytest-cov 7.1.0, testcontainers 4.14.2,
  hypothesis 6.155.2, mutmut 3.5.0.

## 4. Arquitetura da entrega — 4 camadas com gate

A troca de fila (a maior) fica por **último**, isolada dos bumps de versão, para
uma bisção limpa. Cada camada tem seu gate antes de avançar.

| # | Camada | Gate |
|---|--------|------|
| 1 | Runtime Python 3.14 + migração uv | lint + mypy + unit + build da imagem |
| 2 | Imagens de infra (PG18 + Redis 8 + MinIO latest) | integração + e2e (ainda no Celery) |
| 3 | Deps app + dev → latest (exceto redis-py) | gate completo |
| 4 | Celery → TaskIQ (+ redis-py 6.4→8) | gate completo incl. integração/e2e |

## 5. Detalhamento por camada

### Camada 1 — Python 3.14 + uv

**`pyproject.toml`**
- `requires-python = ">=3.14,<3.15"`.
- `[tool.ruff] target-version = "py314"`.
- `[tool.mypy] python_version = "3.14"`.
- Remover `[tool.poetry]` (`package-mode`) e `[build-system]` (poetry-core).
  uv não empacota por default (equivalente a `package-mode = false`).
- Dependências principais permanecem em `[project.dependencies]` (já PEP 621);
  o grupo dev já está em `[dependency-groups]` (PEP 735, lido pelo uv nativamente).
  As faixas de versão migram do formato Poetry (`(>=x,<y)`) para o formato
  padrão (`>=x,<y`) onde necessário.

**Lockfile**
- Gerar `uv.lock` (`uv lock`); remover `poetry.lock`.

**`Dockerfile`**
- Base `python:3.14-slim` (builder e runtime). Manter comentário do pin por
  digest (H2).
- Builder: obter o `uv` via `COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv`
  (ou tag fixa) em vez de `pip install poetry`; `uv sync --frozen --no-dev`
  gera o `.venv` no projeto.
- Runtime: copiar `/app/.venv` do builder (inalterado). Remover env vars do
  Poetry; manter `PATH="/app/.venv/bin:$PATH"`.

**`.github/workflows/ci.yml`** (todos os 4 jobs: test, integration, e2e, security)
- Trocar `pipx install poetry` + `actions/setup-python` por
  `astral-sh/setup-uv` (SHA-pinned, coerente com a política de pin por SHA);
  Python 3.14 gerenciado pelo uv (`uv python install 3.14` / `.python-version`).
- Comandos: `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy .`, `uv run pytest`, etc. `uv sync` no lugar de `poetry install`.
- `pip-audit`: `uv run pip-audit` (o upgrade do pip do venv continua coberto).

**`docker/entrypoint.sh`** — inalterado (usa binários do venv no PATH).

**Gate 1:** `uv run ruff check . && uv run ruff format --check . && uv run mypy .
&& uv run pytest` + `docker build`.

### Camada 2 — Imagens de infra (`docker-compose.yml`)

- `db`: `postgres:16-alpine` → `postgres:18-alpine`.
  - ⚠️ **Gotcha PG18**: a imagem oficial do PG18 mudou o `PGDATA` default para
    incluir a major. Para preservar o mount atual
    (`postgres_data:/var/lib/postgresql/data`), **fixar `PGDATA=/var/lib/postgresql/data`**
    via `environment`. Confirmar o comportamento exato ao implementar (inspecionar
    a imagem). Volume PG16 pré-existente é descartável no template
    (`docker compose down -v` no upgrade) — sem migração de dados real.
- `redis` e `redis-celery`: `redis:7-alpine` → `redis:8-alpine`.
- `minio`: bump para a release mais recente do quay.io (confirmar a tag ao
  implementar; hoje `RELEASE.2025-09-07T16-13-09Z`).

**Gate 2:** `uv run pytest -m integration --no-cov` + `uv run pytest -m e2e --no-cov`
(ainda com Celery — valida os drivers contra os servidores novos, isolando o
efeito das imagens da troca de fila).

### Camada 3 — Deps app + dev → latest

Bumps (respeitando as faixas major existentes):

| Pacote | De | Para |
|---|---|---|
| fastapi | 0.136.3 | 0.141.1 |
| uvicorn | 0.48 | 0.52.2 |
| sqlalchemy | 2.0.50 | 2.0.52 |
| alembic | 1.18.4 | 1.19.1 |
| pydantic-settings | 2.14.1 | 2.15.0 |
| slowapi | 0.1.9 | 0.1.10 |
| ruff | 0.15.15 | 0.16.2 |
| mypy | 2.1.0 | 2.3.0 |
| pytest | 9.0.3 | 9.1.1 |
| testcontainers | 4.14.2 | 4.15.0 |
| hypothesis | 6.155.2 | 6.165.x |
| mutmut | 3.5.0 | 3.7.0 |

- psycopg (3.3.4), minio (7.2.20), httpx (0.28.1), pytest-asyncio (1.4.0),
  pytest-cov (7.1.0), pip-audit (2.10.x): já no topo — apenas relock.
- **`redis-py` permanece em 6.4.x** nesta camada (kombu ainda presente).
- `uv lock` para regenerar; ajustar `ruff`/`mypy` se novas regras/erros
  surgirem (correções pontuais, sem afrouxar `--strict`).

**Gate 3:** gate completo (lint + format + mypy + unit + integração + e2e).

### Camada 4 — Celery → TaskIQ

**`app/worker.py`** (reescrito)
- `broker = RedisStreamBroker(url=settings.taskiq_broker_url, ...)` — Redis
  Streams + consumer group dão ack-após-execução nativo (equivalente ao
  `task_acks_late=True` + `task_reject_on_worker_lost`).
- `result_backend = RedisAsyncResultBackend(redis_url=settings.taskiq_result_backend,
  result_ex_time=<24h>)` — equivalente ao `result_expires=86400`.
  `broker.with_result_backend(result_backend)`.
- **Serialização sem pickle**: configurar explicitamente serializer JSON/orjson
  (paridade com o `json`-only do Celery — evita pickle-RCE via backend/broker
  comprometido). Validar o default do TaskIQ e forçar JSON se necessário.
- `scheduler = TaskiqScheduler(broker, sources=[LabelScheduleSource(broker)])`
  (ou `ListRedisScheduleSource` em Redis dedicado `/2`, decidido no plano).
- `@broker.task(...)` para `core.ping`; agendamento por label
  (`schedule=[{"interval"/"cron": ...}]`) com cadência de `taskiq_heartbeat_seconds`.
- **Testes**: quando `settings.taskiq_in_memory` (antigo `task_always_eager`),
  usar `InMemoryBroker()` (execução in-process, sem broker real).
- Guard de segurança chamado no import do módulo (fail-closed no processo do
  worker/scheduler), como hoje.

**Alocação de DBs no `redis-taskiq`**: `/0` broker, `/1` resultados,
`/2` schedule source (se `ListRedisScheduleSource`). Keyspaces separados.

**`app/core/config.py`**
- `celery_broker_url` → `taskiq_broker_url` (`redis://:myapp@redis-taskiq:6379/0`).
- `celery_result_backend` → `taskiq_result_backend` (`.../1`).
- `celery_task_always_eager` → `taskiq_in_memory` (bool, default False).
- `celery_heartbeat_seconds` → `taskiq_heartbeat_seconds` (float, default 60.0).
- Comentários atualizados (Celery → TaskIQ).

**`app/core/security_guards.py`**
- `validate_celery_security` → `validate_taskiq_security`, **mesmos invariantes**:
  scheme `redis`/`rediss`; senha forte (régua `WEAK_PASSWORDS`); broker/backend
  **não** podem apontar para a mesma instância (host:port) do Redis do rate-limit.
- Atualizar chamadas em `app/main.py` (via `validate_production`) e `app/worker.py`.
- `_CELERY_ALLOWED_SCHEMES` → `_TASKIQ_ALLOWED_SCHEMES` (mesmo conjunto).

**`docker-compose.yml`**
- Serviço `redis-celery` → `redis-taskiq`; rede `celery_net` → `taskiq_net`
  (mesmas propriedades de hardening: `rename-command`, `maxmemory`/`noeviction`,
  `no-new-privileges`, `cap_drop`, etc.).
- `worker`: comando `celery -A app.worker worker ...` →
  `taskiq worker app.worker:broker --ack-type when_executed` (+ flags de
  concorrência/log equivalentes). Env `CELERY_*` → `TASKIQ_*`.
- `beat` → serviço de scheduler: `celery -A app.worker beat ...` →
  `taskiq scheduler app.worker:scheduler`. Mantém EXATAMENTE 1 réplica.
- Env `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` (api/worker/beat) →
  `TASKIQ_BROKER_URL`/`TASKIQ_RESULT_BACKEND`; `CELERY_REDIS_PASSWORD` →
  `TASKIQ_REDIS_PASSWORD`.

**Healthchecks** (TaskIQ não tem `inspect ping` — preserva-se
"processo vivo ≠ funcional"):
- **Worker**: probe faz **round-trip real** — enfileira um `ping` interno e
  aguarda o resultado no result backend com timeout curto (~5s). Prova que o
  loop de consumo está saudável (equivalente honesto ao `celery inspect ping`).
  Implementado como módulo executável (ex.: `python -m app.worker_healthcheck`).
- **Scheduler**: a task agendada, ao executar, atualiza um marcador de heartbeat
  (chave Redis com TTL). O healthcheck do scheduler verifica o **frescor** desse
  marcador (janela = ~3× a cadência, espelhando a lógica do beat antigo:
  3 chances de sync antes de acusar unhealthy). Valida o pipeline
  scheduler→broker→worker.
- Mecanismo exato (round-trip vs. marcador, TTLs, timeouts) **finalizado no
  plano** e coberto por teste de integração.

**`.env.example`**
- Bloco `--- Celery ---` → `--- TaskIQ ---`: `TASKIQ_REDIS_PASSWORD`,
  `TASKIQ_BROKER_URL`, `TASKIQ_RESULT_BACKEND`. Comentários de segurança
  (instância dedicada, segmentação de rede) mantidos e atualizados.

**Dependências**
- Remover `celery[redis]` (traz `kombu` junto).
- Adicionar `taskiq` (0.12.x) e `taskiq-redis` (1.2.x).
- `redis-py` sobe para **8.x** (exigido pelo `taskiq-redis`). Verificar
  `slowapi`/`limits` contra redis-py 8 (o rate-limit também usa o cliente).
- Overrides de mypy: remover os de `celery`; adicionar os necessários para
  `taskiq`/`taskiq_redis` se não distribuírem `py.typed`.
- `[tool.mutmut]`: atualizar `do_not_mutate` se a assinatura introspectada
  mudar (as tasks TaskIQ são async; reavaliar exclusões).

**Testes**
- `tests/test_celery.py` → `tests/test_taskiq.py` (unit, InMemoryBroker).
- `tests/integration/test_celery_broker.py` → equivalente TaskIQ (broker real
  via testcontainers; enqueue + resultado).
- `tests/integration/test_celery_beat.py` → equivalente do scheduler
  (agendamento dispara a task; marcador/heartbeat).
- `tests/conftest.py`, `tests/test_production_guards.py`,
  `tests/test_environment.py`: renomear referências `celery_*` → `taskiq_*`,
  ajustar o guard testado (`validate_taskiq_security`).
- `tests/e2e/*`: atualizar env/serviços (`redis-taskiq`, comandos worker/scheduler,
  invariante de isolamento de rede `taskiq_net`).
- Manter o **gate de cobertura de 90%**.

**Gate 4:** gate completo (lint + format + mypy + unit + integração + e2e) +
`docker build` + subida do compose validando healthchecks do worker/scheduler.

## 6. Documentação

- `README.md` e `README.en.md`: substituir todas as menções a Poetry → uv
  (comandos de setup, instalação, execução dos testes) e Celery → TaskIQ
  (arquitetura da fila, worker/scheduler, variáveis de ambiente); atualizar a
  matriz de versões (Python 3.14, PG18, Redis 8).

## 7. Verificação final (com Docker)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest                       # unit + cobertura (gate 90%)
uv run pytest -m integration --no-cov
uv run pytest -m e2e --no-cov
docker build -t myapp-backend:modernizacao .
docker compose up -d && <checar healthchecks> && docker compose down -v
```

## 8. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| PG18 muda `PGDATA` e quebra o mount do volume | Fixar `PGDATA` via env; confirmar inspecionando a imagem; volume é descartável no template. |
| TaskIQ sem `inspect ping` → healthcheck fraco | Round-trip real (worker) + marcador de heartbeat (scheduler); coberto por integração. |
| TaskIQ pode usar serializer com pickle | Forçar serializer JSON/orjson explicitamente (paridade de segurança). |
| `redis-py` 8 quebra slowapi/`limits` | Verificar na Camada 4; se incompatível, reavaliar (fixar faixa ou patch). |
| Cobertura cair abaixo de 90% na reescrita dos testes | Reescrever testes de fila junto com o código; rodar o gate localmente. |
| Python 3.14 quebra alguma dep transitiva | uv resolve o lock; corrigir faixas pontualmente; a suíte pega regressões. |

## 9. Fora de escopo

- Trocar uvicorn (granian), mypy (ty) ou slowapi — avaliados, ganho não paga o
  custo agora (registrados como "futuro").
- Migrar a fila para Postgres (Procrastinate) — TaskIQ foi a escolha.
- Adicionar features de domínio ou novas tasks além do heartbeat existente.
- Mudanças no modelo de segurança além dos renomes (guards, redes, hardening
  permanecem equivalentes).

## 10. Rollback

Cada camada é um commit isolado (ou grupo pequeno). Como o trabalho é feito em
branch dedicada, o rollback é `git revert`/descartar a branch. O volume de dev
do Postgres é recriável (`down -v`); nenhum dado de produção é tocado.
