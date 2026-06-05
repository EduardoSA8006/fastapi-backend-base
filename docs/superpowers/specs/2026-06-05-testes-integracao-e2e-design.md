# Testes de integração e e2e

Data: 2026-06-05
Status: aprovado ("pode fazer agora, o que acha de usarmos httpx para teste e2e?")

## Objetivo

Adicionar os dois níveis de teste que faltam (ver memória `estrategia-de-testes`):
**integração** (componentes contra infra real) e **e2e** (stack Docker completo,
atacado de fora com httpx). A suíte unitária atual permanece o gate rápido.

## Decisões

- **httpx no e2e** (proposta do usuário, acatada): cliente HTTP real sobre a
  rede contra a porta publicada do compose — diferente do TestClient, que fala
  ASGI in-process e nunca atravessa uvicorn/container/rede.
- **testcontainers-python na integração**: ciclo de vida por teste/módulo,
  containers efêmeros de Postgres/Redis; funciona no CI (GitHub Actions tem
  Docker).
- **Markers + exclusão por default**: `integration` e `e2e` registrados no
  pyproject; `addopts` ganha `-m "not integration and not e2e"` — `poetry run
  pytest` continua rápido e o CI atual não muda. Execução manual:
  `pytest -m integration --no-cov` / `pytest -m e2e --no-cov`.
- **E2E em projeto compose isolado**: `docker compose -p classup-e2e` com
  `API_PORT=18001` — volumes/containers próprios; `down -v` no teardown não
  toca o stack/volumes de dev do usuário.
- Cobertura: o gate de 90% segue sendo responsabilidade da suíte unitária;
  integração/e2e rodam com `--no-cov`.

## Escopo

### tests/integration/ (marker `integration`)

1. **Postgres real** (`PostgresContainer("postgres:16-alpine")`, driver psycopg):
   - `alembic upgrade head` roda num banco limpo (migrações nunca tinham sido
     testadas);
   - `/ready` com `checks["database"] == "ok"` via app real apontando para o
     container;
   - banco derrubado (stop) → `/ready` 503 com `checks["database"] == "error"`
     (cobre o branch do router que SQLite nunca exercita).
2. **Redis real** (redis:7-alpine com `--requirepass`):
   - rate-limit DE VERDADE: estoura o limite → 429 com Retry-After;
   - `/ready` com `checks["redis"] == "ok"` (caminho feliz hoje descoberto).
3. **Celery com broker real** (mesmo Redis): worker embutido
   (`celery.contrib.testing.worker.start_worker`) + `ping.delay().get()` ==
   "pong" — round-trip beat-less broker→worker→result backend com a config
   endurecida (json-only etc.).

### tests/e2e/ (marker `e2e`)

Fixture de sessão: `docker compose -p classup-e2e up -d --build` (API_PORT
18001), espera api/worker/minio ficarem `healthy` (poll), teardown
`down -v` do projeto isolado. Testes (httpx contra `http://127.0.0.1:18001`):

1. `/api/v1/health` 200 e `/api/v1/ready` 200 com database+redis ok — pela
   rede real, atravessando uvicorn/entrypoint/migração de boot.
2. Security headers + X-Request-ID presentes na resposta real.
3. Contrato de erro: rota inexistente → 404 JSON `{"detail": ...}`.
4. **Invariante de isolamento de rede** (segurança — antes garantido só por
   config): `docker compose exec worker` → conexão TCP ao `redis` (rate-limit)
   DEVE falhar (sem rota/DNS); ao `redis-celery` DEVE funcionar.
5. Saúde dos serviços: worker `healthy` (implica `celery inspect ping` real OK
   via healthcheck), minio `healthy` (implica `mc ready` OK), beat `running`.
   (Não espera o healthy do beat: start_period de 210s tornaria o e2e lento.)

## Dependências

- `testcontainers` (grupo dev).

## Fora de escopo (YAGNI)

- Job de CI para integração/e2e (CI atual fica intacto; adicionar depois).
- Teste do heartbeat de 60s do beat no e2e (espera de >60s; o pipeline
  broker→worker já é coberto na integração).
- MinIO na integração (não há cliente de storage ainda — entra com a feature).
- Testes de carga/slowloris.
