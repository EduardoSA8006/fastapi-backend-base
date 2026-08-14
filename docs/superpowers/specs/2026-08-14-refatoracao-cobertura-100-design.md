# Refatoração alvo + cobertura 100% (combinada) + cenários novos

- **Data:** 2026-08-14
- **Projeto:** fastapi-backend-base (template FastAPI, security-hardened, pós-modernização Python 3.14/uv/TaskIQ)
- **Status:** aprovado para escrita de plano de implementação

---

## 1. Objetivo

Elevar a qualidade do template sem alterar comportamento nem regredir segurança:

1. **Refatoração alvo (evidence-based):** remover duplicação real e extrair helpers onde há ganho concreto de clareza. Nada especulativo — o código já passou por revisão dupla e está limpo.
2. **Cobertura 100% combinada (unit + integração):** medir cobertura combinando as suítes que rodam código de `app/` in-process, e chegar a 100% de statements, com `# pragma: no cover` apenas para linhas genuinamente inalcançáveis/defensivas.
3. **Cenários novos (integração + e2e):** adicionar testes comportamentais reais (falha, resiliência, edge cases) além do mínimo para fechar cobertura.

Princípio-guia: **comportamento inalterado**. A refatoração é interna; a suíte a 100% é a rede que prova ausência de regressão.

## 2. Decisões tomadas (com justificativa)

| Decisão | Escolha | Justificativa |
|---|---|---|
| Definição de "100%" | Cobertura **combinada unit + integração**, statement coverage | Os "gaps" atuais são caminhos de infra testados por integração (que roda `--no-cov`). Combinar mede a verdade sem introduzir mocks. |
| e2e na conta de cobertura | **Não** | e2e ataca o stack de fora (app roda em container) → coverage.py não instrumenta aquele processo. e2e fica por confiança. |
| Branch coverage | Fora de escopo (opção futura) | Statement 100% já é a meta; branch inflaria o escopo. |
| Apetite de refatoração | **Alvo** | Código já limpo; só duplicação medida. |
| Mocks para fechar gaps | **Evitar** | Manter "testes reais > mocks"; fechar gaps com testes in-process contra containers reais. |
| Guard ASGI dos middlewares | **Não extrair** | 3 linhas idiomáticas; abstrair adiciona indireção e piora clareza (YAGNI). |
| Blocos `except S3Error` do storage | **Não unificar** | Cada um tem follow-up diferente (not-found, idempotência, drain). |

## 3. Estado atual (baseline)

- Cobertura unit: **96%** (`--cov=app`, gate `--cov-fail-under=90`, marcador exclui integração/e2e).
- Gaps unit (todos infra, cobertos por integração/e2e hoje):
  - `app/worker.py:51-56` (`_build_broker` path real — só constrói objetos), `:89-96` (task `ping` grava o marcador; roda no worker/subprocess).
  - `app/worker_healthcheck.py:21-31,35,39` (`_probe`/`main`; round-trip).
  - `app/shared/storage.py:64-65,76-86,160-164` (`_spec`/`_get_client`/uma op).
  - `app/features/health/router.py:75` (branch redis do `/ready`).
- Integração roda código de app in-process: `test_minio`→`app.shared.storage`; `test_redis`/`test_postgres`/`test_failure_scenarios`→`app.main.create_app`/`storage`.
- App: 1637 linhas, feature-first, middleware separado. Testes: 3650 linhas.
- Sem config de `[tool.coverage.*]`; cobertura vem só do `addopts` do pytest-cov.

## 4. Arquitetura da entrega — 2 fases com gate

Ordem: **rede de segurança primeiro, refatoração depois**.

| # | Fase | Gate |
|---|------|------|
| A | Infra de cobertura combinada + gaps + cenários novos → 100% combinado | unit + `coverage combine` (unit+integração) `--fail-under=100` + e2e |
| B | Refatoração alvo (app helpers + dedup de testes) sob a rede | ruff + mypy --strict + gate combinado 100% + e2e |

## 5. Detalhamento — Fase A (cobertura + cenários)

### 5.1 Infra de cobertura combinada

**`pyproject.toml`**
```toml
[tool.coverage.run]
source = ["app"]
parallel = true       # cada pytest escreve .coverage.<host>.<pid>

[tool.coverage.report]
fail_under = 100
show_missing = true
exclude_also = [
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```
- Manter o `addopts` do pytest para a **run unit rápida** de dev (gate 90 unit fica como está — dev loop rápido), mas o gate autoritativo passa a ser o combinado.
- **`scripts/coverage.sh`** (novo, executável): `coverage erase`; `uv run pytest --cov=app --cov-append -m "not integration and not e2e"`; `uv run pytest --cov=app --cov-append -m integration`; `uv run coverage combine`; `uv run coverage report --fail-under=100`. (Ajustar flags à interação pytest-cov × parallel na implementação; o invariante é: unit+integração instrumentados, combinados, report 100%.)

**`.github/workflows/ci.yml`**
- Novo job **`coverage`** (ubuntu-latest, Docker disponível — integração usa testcontainers): `uv sync --frozen` → roda `scripts/coverage.sh` (falha se < 100%). Roda em paralelo aos demais.
- O job `test` atual (unit rápido, gate 90) permanece como gate rápido; `integration`/`e2e` permanecem.

### 5.2 Fechamento dos gaps (testes direcionados, sem mocks)

- **`app/worker.py:51-56`** (`_build_broker` real): teste **unitário** que chama `_build_broker()` com `taskiq_in_memory=False` e assere que retorna `RedisStreamBroker` com `ORJSONSerializer` e result backend ORJSON — só construção, sem Redis. (Cobre 51-56 in-process.)
- **`app/worker.py:89-96`** (marcador do `ping`): teste de **integração** que executa a função da task `ping` **in-process** (não via subprocess) contra um `redis_container` real com `taskiq_in_memory=False`, e verifica o marcador `myapp:taskiq:heartbeat`. (Cobre 89-96 in-process; complementa o teste de subprocess existente.)
- **`app/worker_healthcheck.py`**: teste **unitário** que chama `worker_healthcheck.main()`/`_probe()` com `TASKIQ_IN_MEMORY=true` (InMemory round-trip in-process) → retorna 0; e um caso de timeout → 1. `# pragma: no cover` no `if __name__ == "__main__":` (já em `exclude_also`).
- **`app/shared/storage.py`** (`_spec`/`_get_client`/op faltante): fechar via **integração** `test_minio` in-process (já importa `app.shared.storage`) — ampliar para exercitar o caminho de construção/rebuild de client e a op não coberta.
- **`app/features/health/router.py:75`** (branch redis do `/ready`): coberto por integração (`test_redis`/`test_failure_scenarios` com Redis real up/down) — garantir um caso que exercite o ramo de erro.
- Após combinar, rodar `coverage report --show-missing` e aplicar `# pragma: no cover` **apenas** onde a linha for comprovadamente inalcançável (documentando o motivo no comentário).

### 5.3 Cenários novos (confiança)

Lista fechada (comportamentais, não só cobertura):
- **Integração:**
  - Redeliver: task que falha é reentregue (par `acks_late`/`ack-type when_executed`) — worker in-process consome, task levanta, mensagem volta e é reprocessada.
  - Result backend com TTL: resultado expira após `result_ex_time` curto.
  - Storage: bucket/credencial inválida → erro mapeado corretamente (amplia `test_minio`).
- **e2e:**
  - **Scheduler morto → marcador expira → container `unhealthy`** (transforma a prova manual em teste): `docker kill myapp-scheduler`, esperar > TTL, assert `unhealthy` no scheduler e `healthy` no worker.
  - **Rate-limit ponta-a-ponta:** rajada real retorna 429.
  - **`/ready` degradado:** com uma dependência derrubada, `/ready` responde 503 com o detalhe da dependência.

## 6. Detalhamento — Fase B (refatoração alvo)

Cada item preserva comportamento; a suíte a 100% valida.

**App:**
- `app/core/security_guards.py`: extrair `_reject_if_weak_password(password: str, message: str) -> None` (encapsula `if password in WEAK_PASSWORDS: raise ValueError(message)`), chamada nos 4 sítios (TaskIQ broker/backend, rate-limit Redis, MinIO, banco) mantendo as mensagens específicas (testes casam por `match=`).
- Constante `REDIS_PROBE_TIMEOUT_SECONDS = 2` em `app/core/config.py`; usar em `app/core/limiter.py` e `app/features/health/router.py`.

**Testes (dedup, sem mudar o que é testado):**
- `tests/core/conftest.py` (novo): `assert_non_http_scope_passthrough(mw_cls)` — substitui o teste idêntico em `test_error_boundary.py`, `test_observability.py`, `test_security_headers.py` (via `parametrize` sobre as classes).
- `tests/shared/conftest.py` (novo): `reset_storage_singleton(monkeypatch)` (usado por autouse em `test_storage.py`/`test_storage_internals.py` e direto em `test_minio.py`/`test_failure_scenarios.py`) e `_s3_error(code)` compartilhado.
- `tests/core/test_body_size_limit.py`: helper `_client(max_body_size=100)` no lugar do `TestClient(_build_app(...))` literal (15×).
- Trocar 11 `Settings(rate_limit_storage_uri="memory://", trusted_hosts=[...])` diretos por `make_settings(...)` em `test_observability.py`, `features/health/test_health.py`, `integration/test_postgres.py`, `test_app_host_cors.py`.
- Unificar `_crashing_client`/`_client_with_raising_route` num helper em `tests/conftest.py`.
- Parametrizar os 2 testes de guard Redis em `test_production_guards.py` (espelha o padrão MinIO); remover os 3 `import pytest` locais mortos.

**Não tocar:** guard ASGI dos 4 middlewares; blocos `except S3Error` do storage (ver §2).

## 7. Verificação

- **Fase A:** `uv run pytest` (unit) + `scripts/coverage.sh` (combinado, `--fail-under=100`) + `uv run pytest -m e2e --no-cov`. Docker disponível.
- **Fase B:** `uv run ruff check . && uv run ruff format --check . && uv run mypy .` + gate combinado 100% + e2e. Cada refator commitado pequeno; a suíte a 100% prova ausência de regressão.
- Trivy/gitleaks (job security) inalterados; a mudança não toca o Dockerfile.

## 8. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| pytest-cov × coverage `parallel` não combinar direto | Fixar o fluxo no script (erase/append/combine); validar localmente; alternativa `coverage run -m pytest`. |
| Perseguir 100% gerar teste sem valor | Fechar gaps com testes in-process reais; `pragma` só para inalcançável, com justificativa no comentário. |
| Refatoração alterar comportamento sutil (ex.: mensagens de guard) | Manter mensagens literais; a suíte a 100% (inclui `match=`) trava regressão. |
| Novo cenário e2e (kill scheduler) ser lento/flaky | Janela derivada do TTL, fail-fast se container morre; marcado e2e (fora do gate rápido). |
| Cobertura combinada exigir Docker na CI | Job `coverage` em runner com Docker (como integration/e2e). |

## 9. Fora de escopo

- Branch coverage (opção futura).
- Reescrever/re-arquitetar módulos limpos; renomeações estéticas.
- Mudanças de comportamento, novas features de domínio, novas dependências.
- Mudanças no Dockerfile/imagens/segurança de infra.

## 10. Rollback

Trabalho em branch dedicada (`qualidade/refatoracao-cobertura-100`); commits pequenos por item. Rollback = `git revert`/descartar branch. Sem migração de dados.
