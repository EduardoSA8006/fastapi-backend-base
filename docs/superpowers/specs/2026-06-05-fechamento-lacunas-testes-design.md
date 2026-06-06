# Fechamento das lacunas de teste

Data: 2026-06-05
Status: aprovado ("vamos fechar todos")

## Objetivo

Fechar as 7 lacunas identificadas na análise de cobertura, levando os três
níveis (unit/integração/e2e) a cobrirem tudo que é cobrível no estágio atual
(esqueleto sem domínio).

## Itens

### 1. Branches defensivos (unit)

Novos testes unitários para: fallback do limiter quando `_inject_headers`
(API privada do slowapi) falha e quando Retry-After não é numérico; o
`except` do access log (crash que escapa do ErrorBoundary — simulado com o
middleware isolado); re-raise mid-stream e passthrough não-http do
ErrorBoundary; fallback não-AppException do handler (chamada direta);
passthrough não-http do SecurityHeaders; branch restante de logging.py.

### 2. Camada de storage MinIO + testes nos 3 níveis

Design pré-aprovado no spec do MinIO (2026-06-02, seção "Fluxo de dados"):
- `app/shared/storage.py` — facade sobre minio-py (estilo portfolio):
  `put_object`, `get_object`, `delete_object`; SDK síncrono embrulhado em
  `asyncio.to_thread`; `_ensure_bucket` lazy com cache (sem acoplamento de
  startup); cliente singleton preguiçoso reconstruído se as settings mudarem.
- Erros tipados INTEGRADOS à hierarquia da refatoração:
  `StorageObjectNotFoundError(NotFoundError)` e
  `StorageUnavailableError(UnavailableError)` — o handler global já os
  converte (404/503) e loga 5xx.
- Dependência `minio` (main). Contrato de rede inalterado: proxy pelo
  backend, NUNCA presigned URL pública.
- Testes: unit (mapeamento de erros com cliente stubado); integração
  (MinIO real via testcontainers: put/get/delete/overwrite/404/bucket
  lazy); e2e (exec DENTRO do container api: storage contra o `minio`
  interno pela rede do compose com as credenciais reais).

### 3. Pipeline do beat (beat→broker→worker)

- Nova setting `celery_heartbeat_seconds: float = 60.0` usada no
  `beat_schedule` (operador pode retunar sem mudar código).
- Integração: beat REAL em subprocesso (`celery -A app.worker beat`) com
  heartbeat de 1s (via env) contra Redis efêmero + worker embutido — asserta
  que `core.ping` foi disparado pelo beat e executado. Fecha o único trecho
  do pipeline sem teste (beat→broker).

### 4. Migração baseline

- `alembic revision -m "baseline"` vazia (prática comum de baseline): o
  upgrade deixa de ser no-op — cria/grava `alembic_version`.
- Integração passa a assertar `alembic_version == head` após o upgrade.

### 5. E2E em modo production

- Segundo stack (`-p myapp-e2e-prod`, porta 18002), module-scoped e
  sequencial ao dev (container_name fixo impede paralelismo): roda com
  `ENVIRONMENT=production`, senhas fortes geradas para o teste, usuário
  MinIO não-óbvio, `TRUSTED_HOSTS=["api.e2e.test"]`,
  `HEALTHCHECK_HOST=api.e2e.test`.
- Valida os guards VIVOS: boot aceito com config forte; `/docs` e
  `/openapi.json` 404; Host errado 400; Host certo 200; headers.

### 6. Smoke de concorrência (e2e)

- Burst de 50 requisições concorrentes (httpx) → todas 200. Sanidade do
  `--limit-concurrency` sob carga leve. Slowloris real permanece
  responsabilidade da borda (README) — fora do escopo de suíte.

### 7. CI

- `ci.yml` ganha jobs `integration` (pytest -m integration) e `e2e`
  (pytest -m e2e) — runners do GitHub Actions têm Docker. O job atual
  (unit) permanece o gate rápido; os novos rodam em paralelo a ele.

## Fora de escopo

- Endpoints HTTP de upload/download (entram com a feature de domínio que
  consumir o storage).
- Testes de carga reais (k6/locust) e slowloris — borda/infra.
- E2E do beat com espera de 60s (coberto pela integração do item 3).
