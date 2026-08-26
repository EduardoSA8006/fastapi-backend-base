# Refatoração + Cobertura 100% + Infra — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elevar a qualidade do template — cobertura 100% combinada (unit+integração), refatoração alvo (dedup/helpers) e padronização do hardening de infra — sem alterar comportamento nem regredir segurança.

**Architecture:** 3 fases independentes, ordem A→B→C. A: infra de cobertura combinada + fechar gaps + cenários novos → 100%. B: refatoração alvo sob a rede de 100%. C: padronizar hardening do docker-compose + digest-pins.

**Tech Stack:** Python 3.14, uv, pytest + pytest-cov + coverage.py, FastAPI, TaskIQ, SQLAlchemy/psycopg, MinIO, Docker Compose, testcontainers.

**Spec:** `docs/superpowers/specs/2026-08-14-refatoracao-cobertura-100-design.md`

## Global Constraints

- **Comportamento inalterado.** Refatoração é interna; mensagens de erro dos guards ficam LITERAIS (testes casam por `match=`).
- **100% = statement coverage combinada de unit + integração** (ambos rodam app in-process). e2e NÃO conta para cobertura (app roda em container). `# pragma: no cover` só para linhas comprovadamente inalcançáveis, com comentário justificando.
- **Sem mocks novos** para fechar gaps — usar testes in-process contra containers reais (testcontainers) ou InMemoryBroker onde já é o padrão.
- Gate rápido de dev `uv run pytest` (unit, `--cov-fail-under=90`) permanece; o gate autoritativo de 100% é o script combinado + job de CI.
- `mypy --strict` e `ruff` limpos; pt-BR nos comentários/docstrings.
- **Fase C:** todo serviço com imagem pinada por digest, `mem_limit`, `cpus`, `pids_limit`, `no-new-privileges`, `cap_drop: ALL` (+ `cap_add` mínimo onde a imagem exigir, validado empíricamente). `maxmemory-policy noeviction` no redis rate-limit. Nunca deixar um serviço `unhealthy` — se um endurecimento quebra o boot, reverter aquele item e documentar.
- Branch: `qualidade/refatoracao-cobertura-100`. Commits pequenos; cada task termina em commit.

## Mapa de arquivos

**Fase A:** `pyproject.toml` (`[tool.coverage.*]`), `scripts/coverage.sh` (novo), `.github/workflows/ci.yml` (job `coverage`); testes novos em `tests/test_taskiq.py`, `tests/test_worker_healthcheck.py` (novo), `tests/integration/test_taskiq_scheduler.py`, `tests/integration/test_minio.py`, `tests/integration/test_redis.py`, `tests/integration/test_taskiq_broker.py`, `tests/e2e/test_stack.py`; `# pragma: no cover` pontuais em `app/`.
**Fase B:** `app/core/security_guards.py`, `app/core/config.py`, `app/core/limiter.py`, `app/features/health/router.py`; `tests/core/conftest.py` (novo), `tests/shared/conftest.py` (novo), `tests/conftest.py`, e os arquivos de teste com dedup.
**Fase C:** `docker-compose.yml`, `Dockerfile`, `README.md`/`README.en.md` (nota Renovate).

---

# FASE A — Cobertura combinada 100% + cenários

### Task A1: Infra de cobertura combinada (config + script + CI)

**Files:**
- Modify: `pyproject.toml`
- Create: `scripts/coverage.sh`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `scripts/coverage.sh` que roda unit+integração com coverage e reporta combinado; `[tool.coverage.*]` config.

- [ ] **Step 1: Adicionar config de coverage ao `pyproject.toml`**

Após a seção `[tool.pytest.ini_options]`, adicionar:
```toml
[tool.coverage.run]
source = ["app"]
# parallel não é necessário: o script usa --cov-append num único .coverage.

[tool.coverage.report]
show_missing = true
# fail_under NÃO fica aqui (senão a run unit rápida passaria a exigir 100%);
# o gate de 100% é aplicado só no script combinado, via CLI --fail-under=100.
exclude_also = [
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```
(Manter o `addopts` do pytest como está — a run unit rápida segue em `--cov-fail-under=90`.)

- [ ] **Step 2: Criar `scripts/coverage.sh`**

```bash
#!/usr/bin/env bash
# Cobertura COMBINADA (unit + integração), gate 100%.
# unit e integração rodam código de app in-process → coverage.py mede ambos.
# e2e NÃO entra (app roda em container separado, fora do processo de teste).
set -euo pipefail
export ENVIRONMENT="${ENVIRONMENT:-development}"

uv run coverage erase
# -o addopts="" limpa o addopts do pytest (marker/fail-under/report) para
# controlar cada run explicitamente.
uv run pytest -o addopts="" --cov=app --cov-report= -m "not integration and not e2e"
uv run pytest -o addopts="" --cov=app --cov-append --cov-report= -m integration
uv run coverage report --show-missing --fail-under=100
```

- [ ] **Step 3: Tornar executável e rodar (baseline, ainda < 100%)**

Run: `chmod +x scripts/coverage.sh && ENVIRONMENT=development bash scripts/coverage.sh`
Expected: roda unit + integração, imprime o relatório combinado com as linhas faltantes; **falha no `--fail-under=100`** (esperado agora — os gaps são fechados nas tasks A2–A5). Anote o % combinado e as linhas Missing no report da task.

- [ ] **Step 4: Adicionar job `coverage` na CI**

Em `.github/workflows/ci.yml`, adicionar um job (usa o mesmo SHA de `astral-sh/setup-uv` já presente no arquivo; ubuntu-latest tem Docker para os testcontainers):
```yaml
  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
      - name: Instalar uv
        uses: astral-sh/setup-uv@ae62891fec2bb8e7d6c99fc78c9fec3a63790f8d # v10.0.0
        with:
          python-version: "3.14"
          enable-cache: true
      - name: Instalar dependências
        run: uv sync --frozen
      - name: Cobertura combinada (unit+integração, gate 100%)
        run: bash scripts/coverage.sh
```

- [ ] **Step 5: Validar YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml scripts/coverage.sh .github/workflows/ci.yml
git commit -m "test(cov): infra de cobertura combinada unit+integração (script + job CI)"
```

---

### Task A2: Fechar gaps unit-cobríveis (worker `_build_broker`, worker_healthcheck)

**Files:**
- Create: `tests/test_worker_healthcheck.py`
- Modify: `tests/test_taskiq.py`
- Modify: `app/worker_healthcheck.py` (pragma no `__main__` já coberto pelo exclude_also — verificar)

**Interfaces:**
- Consumes: `app.worker._build_broker`, `app.worker_healthcheck.main`/`_probe`, `healthcheck_ping`.

- [ ] **Step 1: Ler `app/worker.py` e confirmar o contrato de `_build_broker`**

Confirmar que `_build_broker()` não recebe argumentos e lê `settings` de módulo (import-time), e qual atributo expõe o serializer no `RedisStreamBroker` (candidato `broker.serializer`). Isso define os `monkeypatch`/asserts do Step 2.

- [ ] **Step 2: Escrever o teste do path real (só construção, sem Redis)**

Adicionar a `tests/test_taskiq.py` (o conftest força `TASKIQ_IN_MEMORY=true`, então força-se `False` por monkeypatch no objeto `settings` de módulo):
```python
def test_build_broker_real_path_usa_orjson(monkeypatch: pytest.MonkeyPatch) -> None:
    # taskiq_in_memory=False → RedisStreamBroker + result backend ORJSON.
    # Só constrói objetos (não conecta). Cobre app/worker.py:51-56.
    import app.worker as worker_mod
    from taskiq.serializers import ORJSONSerializer
    from taskiq_redis import RedisStreamBroker

    monkeypatch.setattr(worker_mod.settings, "taskiq_in_memory", False)
    broker = worker_mod._build_broker()
    assert isinstance(broker, RedisStreamBroker)
    assert isinstance(broker.serializer, ORJSONSerializer)
```
Run: `uv run pytest tests/test_taskiq.py::test_build_broker_real_path_usa_orjson -v --no-cov`
Expected: PASS (cobre `app/worker.py:51-56`). Se o atributo do serializer diferir de `broker.serializer` (Step 1), ajustar o assert ao real.

- [ ] **Step 3: Teste do `worker_healthcheck` via InMemory (round-trip in-process)**

Criar `tests/test_worker_healthcheck.py`:
```python
"""worker_healthcheck sob InMemoryBroker (round-trip in-process, sem Redis)."""


def test_healthcheck_main_retorna_0_com_broker_saudavel() -> None:
    # TASKIQ_IN_MEMORY=true (conftest) → broker InMemory; healthcheck_ping
    # executa in-process e retorna "pong" → main() == 0.
    from app import worker_healthcheck

    assert worker_healthcheck.main() == 0


async def test_probe_retorna_1_em_timeout(monkeypatch: "pytest.MonkeyPatch") -> None:
    # Se o resultado nunca chega, _probe degrada para 1 (TimeoutError).
    import asyncio

    from app import worker_healthcheck

    async def _never(*_a: object, **_k: object) -> object:
        await asyncio.sleep(10)

    # força timeout curto e um wait_result que pendura
    monkeypatch.setattr(worker_healthcheck, "_TIMEOUT_S", 0.01)

    class _Kicked:
        async def wait_result(self, *_a: object, **_k: object) -> object:
            await asyncio.sleep(10)

    async def _kiq() -> _Kicked:
        return _Kicked()

    monkeypatch.setattr(worker_healthcheck.healthcheck_ping, "kiq", _kiq)
    assert await worker_healthcheck._probe() == 1
```
Run: `uv run pytest tests/test_worker_healthcheck.py -v --no-cov`
Expected: PASS (cobre `worker_healthcheck.py:20-35`; o `if __name__ == "__main__":` já está em `exclude_also`).

- [ ] **Step 4: Verificar via cobertura combinada que worker.py/worker_healthcheck fecharam**

Run: `ENVIRONMENT=development bash scripts/coverage.sh` (ainda falha em 100% por outros gaps; confirmar que as linhas de worker.py:51-56 e worker_healthcheck saíram do Missing).

- [ ] **Step 5: Commit**

```bash
git add tests/test_taskiq.py tests/test_worker_healthcheck.py
git commit -m "test(cov): fecha gaps unit de _build_broker e worker_healthcheck"
```

---

### Task A3: Fechar gaps de integração (ping marker in-process, storage, readiness redis)

**Files:**
- Modify: `tests/integration/test_taskiq_scheduler.py` (ou novo `test_taskiq_ping.py`)
- Modify: `tests/integration/test_minio.py`
- Modify: `tests/integration/test_redis.py`

**Interfaces:**
- Consumes: `app.worker.ping`, `app.worker.HEARTBEAT_KEY`, `app.shared.storage`, `/api/v1/ready`.

- [ ] **Step 1: Teste in-process da task `ping` gravando o marcador (cobre worker.py:89-96)**

Adicionar a `tests/integration/test_taskiq_scheduler.py` (marcado `integration`), executando a FUNÇÃO da task in-process contra um `redis_container` real com `taskiq_in_memory=False`:
```python
async def test_ping_grava_marcador_in_process(monkeypatch: "pytest.MonkeyPatch") -> None:
    # Executa o corpo de app.worker.ping in-process (não via subprocess) contra
    # um Redis real → cobre a escrita do marcador (worker.py:89-96) na medição.
    import redis as redis_sync

    import app.worker as worker_mod
    from tests.integration.conftest import redis_container

    with redis_container("S3nhaPingMarker123") as base:
        monkeypatch.setattr(worker_mod.settings, "taskiq_in_memory", False)
        monkeypatch.setattr(worker_mod.settings, "taskiq_broker_url", f"{base}/0")
        monkeypatch.setattr(worker_mod.settings, "taskiq_heartbeat_seconds", 1.0)
        # chama a função subjacente da task (não .kiq) — roda in-process aqui
        result = await worker_mod.ping.original_func()  # ajustar ao atributo real
        assert result == "pong"
        client = redis_sync.from_url(f"{base}/0")
        assert client.get(worker_mod.HEARTBEAT_KEY) == b"pong"
        client.close()
```
> Nota: confirmar como chamar o corpo puro da task no taskiq 0.12.x (`ping.original_func` / `ping.__wrapped__` / atributo equivalente); ajustar. O invariante: rodar o corpo in-process e ver o marcador no Redis real.

Run: `uv run pytest tests/integration/test_taskiq_scheduler.py -m integration --no-cov -k marcador_in_process -v`
Expected: PASS.

- [ ] **Step 2: Ampliar test_minio para cobrir `_spec`/`_get_client`/op faltante**

Ler `app/shared/storage.py:64-86,160-164` e `tests/integration/test_minio.py`. Garantir que o teste exercita: (a) rebuild do client quando as settings mudam (`_get_client` com spec diferente) e (b) a operação não coberta (linhas 160-164 — identificar: provável `list`/`stat`/`delete`). Adicionar asserções in-process chamando as funções de `app.shared.storage` contra o MinIO container.

Run: `uv run pytest tests/integration/test_minio.py -m integration --no-cov -v`
Expected: PASS.

- [ ] **Step 3: Cobrir o ramo redis do `/ready` (health/router.py:75)**

Em `tests/integration/test_redis.py` (ou `test_failure_scenarios.py`), garantir um caso que bate em `/api/v1/ready` com o Redis do rate-limit **indisponível** (container parado / porta fechada) e assere `checks["redis"] == "error"` e HTTP 503. Usa `create_app(make_settings(rate_limit_enabled=True, rate_limit_storage_uri=<redis morto>))` + `TestClient`.

Run: `uv run pytest tests/integration/test_redis.py -m integration --no-cov -v`
Expected: PASS (cobre o ramo de erro).

- [ ] **Step 4: Verificar via cobertura combinada**

Run: `ENVIRONMENT=development bash scripts/coverage.sh`
Expected: report combinado — confirmar que storage/worker/health saíram do Missing (pode ainda faltar linhas defensivas → pragma na Task A6).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/
git commit -m "test(cov): fecha gaps de integração (marcador ping, storage, readiness redis)"
```

---

### Task A4: Cenários novos de integração (confiança)

**Files:**
- Modify: `tests/integration/test_taskiq_broker.py` (redeliver, TTL)
- Modify: `tests/integration/test_minio.py` (credencial/bucket inválido)

- [ ] **Step 1: Redeliver — task que falha é reentregue**

Em `tests/integration/test_taskiq_broker.py`, adicionar um teste (`integration`) que registra uma task que falha na 1ª execução e sucede na 2ª; com `RedisStreamBroker` + `--ack-type when_executed` (ack após execução), a mensagem da 1ª falha é reentregue. Consumir via `run_receiver_task` (padrão já usado no arquivo) e assertar que a 2ª execução completa. Bounded por deadline.

- [ ] **Step 2: TTL do result backend**

Adicionar teste que configura `RedisAsyncResultBackend(..., result_ex_time=1)`, publica, lê o resultado, espera > 1s e confirma que o resultado expirou (leitura falha/None).

- [ ] **Step 3: Storage — credencial/bucket inválido mapeia erro**

Em `tests/integration/test_minio.py`, adicionar caso com credencial inválida (ou bucket inexistente sem auto-create) e assertar o erro esperado de `app.shared.storage` (mapeamento correto, não vazamento de exceção crua).

- [ ] **Step 4: Rodar integração**

Run: `uv run pytest -m integration --no-cov -v`
Expected: PASS (todos, incl. os novos).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/
git commit -m "test(integration): cenários novos — redeliver, TTL do result, storage inválido"
```

---

### Task A5: Cenários novos de e2e (resiliência)

**Files:**
- Modify: `tests/e2e/test_stack.py`

- [ ] **Step 1: Scheduler morto → marcador expira → container `unhealthy`**

Adicionar teste `e2e` que: mata o container `myapp-scheduler` (`docker kill myapp-scheduler`), espera > TTL do marcador (default 180s → usar cadência menor se o stack e2e permitir, ou aceitar a janela), e assere `container_state("myapp-scheduler")` health `unhealthy` **e** `myapp-worker` ainda `healthy` (transforma a prova manual do review anterior em teste). Bounded/fail-fast; marcado `e2e`.

- [ ] **Step 2: Rate-limit ponta-a-ponta → 429**

Adicionar teste `e2e` que faz uma rajada de requisições a um endpoint rate-limitado (não isento) via httpx contra o stack e assere que alguma resposta é 429 com `Retry-After`.

- [ ] **Step 3: `/ready` degradado → 503**

Adicionar teste `e2e` que derruba uma dependência (ex.: `docker stop myapp-redis`) e assere `/api/v1/ready` → 503 com o detalhe da dependência; restaurar depois.

- [ ] **Step 4: Rodar e2e**

Run: `uv run pytest -m e2e --no-cov -v`
Expected: PASS. Garantir teardown limpo (sem containers `myapp-e2e*`).

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/
git commit -m "test(e2e): cenários novos — scheduler morto→unhealthy, 429, /ready 503"
```

---

### Task A6: Fechar em 100% (pragmas para inalcançável) + ligar o gate

**Files:**
- Modify: `app/` (pragmas pontuais), `scripts/coverage.sh` já tem `--fail-under=100`.

- [ ] **Step 1: Rodar o combinado e listar o que ainda falta**

Run: `ENVIRONMENT=development bash scripts/coverage.sh`
Expected: relatório com as linhas Missing remanescentes.

- [ ] **Step 2: Para cada linha faltante, decidir: teste ou pragma**

Se for código genuinamente exercitável → adicionar teste (unit/integração conforme onde roda in-process). Se for defensivo/inalcançável (ex.: branch de erro de API privada, guard de import) → `# pragma: no cover` COM comentário curto justificando por que é inalcançável. Não usar pragma para fugir de teste possível.

- [ ] **Step 3: Rodar até verde**

Run: `ENVIRONMENT=development bash scripts/coverage.sh`
Expected: `Total ... 100%` e o comando sai 0 (`--fail-under=100` satisfeito).

- [ ] **Step 4: Commit**

```bash
git add app/ scripts/coverage.sh
git commit -m "test(cov): 100% combinado (pragmas justificados para inalcançável)"
```

---

### GATE A — verificação da Fase A

- [ ] Run: `uv run pytest` (unit rápido) → PASS.
- [ ] Run: `ENVIRONMENT=development bash scripts/coverage.sh` → **100%**, sai 0.
- [ ] Run: `uv run pytest -m e2e --no-cov` → PASS. Teardown limpo.

---

# FASE B — Refatoração alvo (sob a rede de 100%)

### Task B1: App — helper de senha fraca + constante de timeout do Redis

**Files:**
- Modify: `app/core/security_guards.py`, `app/core/config.py`, `app/core/limiter.py`, `app/features/health/router.py`

**Interfaces:**
- Produces: `security_guards._reject_if_weak_password(password: str, message: str) -> None`; `config.REDIS_PROBE_TIMEOUT_SECONDS: int = 2`.

- [ ] **Step 1: Extrair `_reject_if_weak_password` em `security_guards.py`**

Adicionar (perto do topo, após `WEAK_PASSWORDS`):
```python
def _reject_if_weak_password(password: str, message: str) -> None:
    """Levanta ValueError(message) se a senha estiver na blocklist de fracas."""
    if password in WEAK_PASSWORDS:
        raise ValueError(message)
```
Substituir os 4 sítios `if <senha> in WEAK_PASSWORDS: raise ValueError(<msg>)` por chamadas, **mantendo as mensagens literais**:
- TaskIQ (dentro do loop, `validate_taskiq_security`): `_reject_if_weak_password(parsed.password or "", f"Senha do TaskIQ default/fraca (ou ausente) no {label} não é permitida em produção. Use uma senha forte (redis://:SENHA@redis-taskiq:6379/N).")`
- rate-limit Redis (`validate_production`, ~144-150): `_reject_if_weak_password(urlparse(settings.rate_limit_storage_uri).password or "", "Senha do Redis default/fraca (ou ausente) não é permitida em produção. Use uma senha forte na RATE_LIMIT_STORAGE_URI (redis://:SENHA@host:porta/db).")`
- MinIO (~151-155): `_reject_if_weak_password(settings.minio_root_password, "Senha do MinIO default/fraca (ou ausente) não é permitida em produção. Defina MINIO_ROOT_PASSWORD com uma senha forte.")`
- banco (~165-170): `_reject_if_weak_password(db_password, "Senha de banco default/fraca não é permitida em produção. Use uma senha forte na DATABASE_URL.")`

- [ ] **Step 2: Constante `REDIS_PROBE_TIMEOUT_SECONDS` em `config.py`**

No topo de `app/core/config.py` (após os imports, antes da classe): `REDIS_PROBE_TIMEOUT_SECONDS = 2  # timeout curto p/ probes Redis (limiter e /ready) não pendurarem a request`.
- `app/core/limiter.py:45`: usar `{"socket_timeout": REDIS_PROBE_TIMEOUT_SECONDS, "socket_connect_timeout": REDIS_PROBE_TIMEOUT_SECONDS}` (importar de `app.core.config`).
- `app/features/health/router.py:71-72`: usar a constante nos dois timeouts.

- [ ] **Step 3: Verificar tipos + guards + cobertura**

Run: `uv run ruff check . && uv run mypy . && uv run pytest tests/test_taskiq.py tests/test_production_guards.py tests/core/test_limiter.py -v --no-cov`
Expected: PASS (mensagens idênticas → `match=` continua casando).

- [ ] **Step 4: Commit**

```bash
git add app/
git commit -m "refactor(app): _reject_if_weak_password + REDIS_PROBE_TIMEOUT_SECONDS"
```

---

### Task B2: Testes — helper de passthrough de scope não-HTTP

**Files:**
- Create: `tests/core/conftest.py`
- Modify: `tests/core/test_error_boundary.py`, `tests/core/test_observability.py`, `tests/core/test_security_headers.py`

- [ ] **Step 1: Criar o helper parametrizável**

`tests/core/conftest.py`:
```python
"""Helpers compartilhados dos testes de middleware."""
from typing import Any


async def assert_non_http_scope_passthrough(middleware_cls: type, **mw_kwargs: Any) -> None:
    """Um middleware ASGI deve repassar scopes não-HTTP intactos (scope/receive/send)."""
    called: dict[str, Any] = {}
    expected_scope = {"type": "lifespan"}

    async def _inner(scope: Any, receive: Any, send: Any) -> None:
        called["scope"] = scope
        called["receive"] = receive
        called["send"] = send

    async def _receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def _send(_message: Any) -> None:
        return None

    mw = middleware_cls(_inner, **mw_kwargs)
    await mw(expected_scope, _receive, _send)
    assert called["scope"] is expected_scope
    assert called["receive"] is _receive
    assert called["send"] is _send
```
> Nota: alinhar a assinatura (`**mw_kwargs`) ao construtor de cada middleware — `SecurityHeadersMiddleware(hsts_enabled=...)`, `BodySizeLimitMiddleware(max_body_size=...)`, `ErrorBoundaryMiddleware()`, `RequestContextMiddleware(...)`. Passar os kwargs mínimos por caso.

- [ ] **Step 2: Substituir os 3 testes verbatim por chamadas ao helper**

Em `test_error_boundary.py:109-133`, `test_observability.py:157-180`, `test_security_headers.py:116-138`, trocar o corpo pelo `await assert_non_http_scope_passthrough(<Middleware>, **kwargs)`. Manter o nome do teste. (test_body_size_limit tem variante própria não-verbatim — deixar, ou alinhar se trivial.)

- [ ] **Step 3: Rodar**

Run: `uv run pytest tests/core/test_error_boundary.py tests/core/test_observability.py tests/core/test_security_headers.py -v --no-cov`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/core/
git commit -m "test(refactor): helper assert_non_http_scope_passthrough (dedup 3 middlewares)"
```

---

### Task B3: Testes — helpers de storage compartilhados

**Files:**
- Create: `tests/shared/conftest.py`
- Modify: `tests/shared/test_storage.py`, `tests/shared/test_storage_internals.py`, `tests/integration/test_minio.py`, `tests/integration/test_failure_scenarios.py`

- [ ] **Step 1: Criar `tests/shared/conftest.py` com os dois helpers**

```python
"""Helpers compartilhados dos testes de storage."""
from typing import Any

import pytest
from minio.error import S3Error

from app.shared import storage


def reset_storage_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zera o singleton preguiçoso do storage (client/spec/cache de buckets)."""
    monkeypatch.setattr(storage, "_client", None)
    monkeypatch.setattr(storage, "_client_spec", None)
    storage._known_buckets.clear()


def make_s3_error(code: str) -> S3Error:
    """Constrói um S3Error stub com o código dado (para simular falhas do MinIO)."""
    return S3Error(
        code=code, message="stub", resource="/x",
        request_id="r", host_id="h", response=None,  # type: ignore[arg-type]
    )
```

- [ ] **Step 2: Usar nos 4 sítios**

- `tests/shared/test_storage.py`: fixture autouse chamando `reset_storage_singleton(monkeypatch)`; trocar `_s3_error` local por `make_s3_error`.
- `tests/shared/test_storage_internals.py`: idem.
- `tests/integration/test_minio.py`: dentro da fixture `_wire_storage`, chamar `reset_storage_singleton(monkeypatch)` (mantém o patch de `get_settings`).
- `tests/integration/test_failure_scenarios.py`: substituir o reset inline por `reset_storage_singleton(monkeypatch)`.

- [ ] **Step 3: Rodar unit + integração de storage**

Run: `uv run pytest tests/shared/ -v --no-cov && uv run pytest tests/integration/test_minio.py tests/integration/test_failure_scenarios.py -m integration --no-cov -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/shared/ tests/integration/test_minio.py tests/integration/test_failure_scenarios.py
git commit -m "test(refactor): reset_storage_singleton + make_s3_error compartilhados"
```

---

### Task B4: Testes — dedups menores (make_settings, _client, parametrize, imports)

**Files:**
- Modify: `tests/core/test_body_size_limit.py`, `tests/conftest.py`, `tests/core/test_observability.py`, `tests/features/health/test_health.py`, `tests/integration/test_postgres.py`, `tests/test_app_host_cors.py`, `tests/core/test_error_boundary.py`, `tests/shared/test_exceptions.py`, `tests/test_production_guards.py`

- [ ] **Step 1: Helper `_client()` em `test_body_size_limit.py`**

Adicionar `def _client(max_body_size: int = 100) -> TestClient: return TestClient(_build_app(max_body_size=max_body_size))` e trocar as 15 ocorrências de `TestClient(_build_app(max_body_size=100))` por `_client()`.

- [ ] **Step 2: Adotar `make_settings` nos 11 sítios diretos**

Trocar `Settings(rate_limit_storage_uri="memory://", trusted_hosts=[...], **rest)` por `make_settings(**rest)` (importando `from tests.conftest import make_settings`) em: `test_observability.py` (14-17,55-58,75-78,100-106), `features/health/test_health.py` (37-41,62-66,72-76,81-84,90-94), `integration/test_postgres.py` (59-64,81-86), `test_app_host_cors.py` (47-54). Ajustar overrides que diferem do default do `make_settings` (ex.: `trusted_hosts` específico) passando-os como kwargs.

- [ ] **Step 3: Unificar helper de "rota que levanta"**

Em `tests/conftest.py`, adicionar:
```python
def client_with_raising_route(exc: BaseException, **overrides: Any) -> TestClient:
    """App real com uma rota /_boom que levanta `exc`; server exceptions não propagam."""
    from app.main import create_app

    app = create_app(make_settings(**overrides))

    @app.get("/_boom")
    def _boom() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)
```
Trocar `_crashing_client` (`test_error_boundary.py`) e `_client_with_raising_route` (`test_exceptions.py`) por `client_with_raising_route(...)`, ajustando o path/rota nos testes.

- [ ] **Step 4: Parametrizar os 2 testes de guard Redis + remover imports mortos**

Em `tests/test_production_guards.py`: fundir `test_production_rejects_weak_redis_password` e `test_production_rejects_redis_without_password` num `@pytest.mark.parametrize("url", ["redis://:myapp@redis:6379/0", "redis://redis:6379/0"])` (espelha o padrão MinIO já existente). Remover os 3 `import pytest` locais (linhas ~17,24,35 — já há `import pytest` no topo).

- [ ] **Step 5: Rodar as suítes afetadas**

Run: `uv run pytest tests/core/test_body_size_limit.py tests/core/test_observability.py tests/features/health/test_health.py tests/test_app_host_cors.py tests/core/test_error_boundary.py tests/shared/test_exceptions.py tests/test_production_guards.py -v --no-cov && uv run pytest tests/integration/test_postgres.py -m integration --no-cov -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "test(refactor): _client, make_settings, parametrize guard redis, imports mortos"
```

---

### GATE B — verificação da Fase B

- [ ] Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy .` → PASS.
- [ ] Run: `ENVIRONMENT=development bash scripts/coverage.sh` → **100%** (a rede prova que a refatoração não regrediu).
- [ ] Run: `uv run pytest -m e2e --no-cov` → PASS.

---

# FASE C — Padronização de hardening da infra

### Task C1: `redis` (rate-limit) → paridade com `redis-taskiq`

**Files:**
- Modify: `docker-compose.yml` (serviço `redis`)

- [ ] **Step 1: Reescrever o serviço `redis` com o mesmo hardening do `redis-taskiq`**

Trocar o `command` simples pelo formato `sh -c` com senha via env + maxmemory/rename, e adicionar os limites/segurança. Manter rede `ratelimit_net` e o propósito (rate-limit):
```yaml
  redis:
    image: redis:8-alpine   # (digest pin vem na Task C3)
    container_name: myapp-redis
    restart: unless-stopped
    command:
      - sh
      - -c
      - >
        exec redis-server
        --requirepass "$$REDIS_PASSWORD"
        --rename-command FLUSHALL ""
        --rename-command FLUSHDB ""
        --rename-command KEYS ""
        --rename-command CONFIG ""
        --rename-command DEBUG ""
        --maxmemory 64mb
        --maxmemory-policy noeviction
        --appendonly no
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD:-myapp}
    expose:
      - "6379"
    healthcheck:
      test:
        [
          "CMD-SHELL",
          'REDISCLI_AUTH="$$REDIS_PASSWORD" redis-cli ping | grep -q PONG',
        ]
      interval: 5s
      timeout: 3s
      retries: 5
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    pids_limit: 128
    cpus: 0.5
    mem_limit: 128m
    networks:
      - ratelimit_net
```

- [ ] **Step 2: Validar config + subir só o redis + healthy**

Run: `ENVIRONMENT=development docker compose config >/dev/null && echo ok`
Run: `ENVIRONMENT=development docker compose up -d redis && sleep 8 && docker inspect --format '{{.State.Health.Status}}' myapp-redis`
Expected: `ok` e `healthy`. Depois `ENVIRONMENT=development docker compose down`.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "chore(compose): redis rate-limit em paridade de hardening com redis-taskiq"
```

---

### Task C2: `db` (postgres) → hardening + `cap_add` mínimo (empírico)

**Files:**
- Modify: `docker-compose.yml` (serviço `db`)

- [ ] **Step 1: Adicionar limites + no-new-privileges (sem cap ainda)**

No serviço `db`, adicionar `security_opt: [no-new-privileges:true]`, `cpus: 1.0`, `mem_limit: 256m`, `pids_limit: 128`. Subir e confirmar healthy:
Run: `ENVIRONMENT=development docker compose down -v && ENVIRONMENT=development docker compose up -d db && sleep 15 && docker inspect --format '{{.State.Health.Status}}' myapp-db`
Expected: `healthy`.

- [ ] **Step 2: Determinar o `cap_add` mínimo empiricamente**

Adicionar `cap_drop: [ALL]` + `cap_add` com o superconjunto candidato e ir reduzindo:
```yaml
    cap_drop: [ALL]
    cap_add: [CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID]
```
Run (para cada configuração): `ENVIRONMENT=development docker compose down -v && ENVIRONMENT=development docker compose up -d db && sleep 15 && docker inspect --format '{{.State.Health.Status}}' myapp-db && docker logs myapp-db 2>&1 | tail -5`
Remover uma capability por vez e re-testar; ficar com o **menor conjunto** que sobe `healthy`. Documentar o conjunto final num comentário no compose explicando por que cada cap é necessária (initdb chown/perm + gosu setuid/setgid).
> Fallback (se `cap_drop: ALL` for inviável): manter `no-new-privileges` + limites sem `cap_drop`, com comentário justificando (o entrypoint oficial do Postgres exige troca de usuário). Nunca deixar `unhealthy`.

- [ ] **Step 3: Verificar o stack completo ainda sobe (db integrado)**

Run: `ENVIRONMENT=development docker compose down -v && ENVIRONMENT=development docker compose up -d --build && sleep 90 && docker compose ps`
Expected: 7/7 healthy/running. Depois `down -v`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "chore(compose): hardening do Postgres (limites + cap_drop com cap_add mínimo)"
```

---

### Task C3: `pids_limit` faltante + digest-pin das imagens do compose

**Files:**
- Modify: `docker-compose.yml` (`redis-taskiq`, `minio`, `db`, `redis`)

- [ ] **Step 1: `pids_limit` onde falta**

Adicionar `pids_limit: 128` a `redis-taskiq` e `minio` (já têm cpus/mem; falta pids). `db`/`redis` já receberam nas tasks C1/C2.

- [ ] **Step 2: Resolver e fixar digests**

Para cada imagem do compose (`postgres:18-alpine`, `redis:8-alpine`, `quay.io/minio/minio:RELEASE...`), resolver o digest atual:
Run: `for img in postgres:18-alpine redis:8-alpine quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772; do docker pull -q "$img" >/dev/null && docker inspect --format='{{index .RepoDigests 0}}' "$img"; done`
Fixar no compose como `image: <repo>:<tag>@sha256:<digest>` mantendo a tag legível. Ex.: `image: postgres:18-alpine@sha256:...`. Adicionar comentário: `# digest pin — atualizar via Renovate/Dependabot`.

- [ ] **Step 3: Validar config + stack healthy**

Run: `ENVIRONMENT=development docker compose config >/dev/null && echo ok`
Run: `ENVIRONMENT=development docker compose down -v && ENVIRONMENT=development docker compose up -d --build && sleep 90 && docker compose ps`
Expected: `ok` e 7/7 healthy/running. Depois `down -v`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "chore(compose): pids_limit uniforme + digest-pin de todas as imagens"
```

---

### Task C4: Digest-pin da base do Dockerfile + nota de manutenção

**Files:**
- Modify: `Dockerfile`, `README.md`, `README.en.md`

- [ ] **Step 1: Resolver e pinar as imagens do Dockerfile**

Resolver digests de `python:3.14-slim` e `ghcr.io/astral-sh/uv:0.12`:
Run: `docker pull -q python:3.14-slim >/dev/null && docker inspect --format='{{index .RepoDigests 0}}' python:3.14-slim; docker buildx imagetools inspect ghcr.io/astral-sh/uv:0.12 --format '{{.Manifest.Digest}}'`
No `Dockerfile`: `FROM python:3.14-slim@sha256:... AS builder` e `... AS runtime` (mesmo digest) e `COPY --from=ghcr.io/astral-sh/uv:0.12@sha256:... /uv /uvx /bin/`. Manter os comentários existentes (que já recomendam pin por digest) atualizados.

- [ ] **Step 2: Build + confirmar trivy ainda verde**

Run: `docker build -t myapp-backend:pin . 2>&1 | tail -2`
Run: `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --no-progress --skip-version-check --exit-code 1 myapp-backend:pin; echo "trivy exit=$?"`
Expected: build ok; `trivy exit=0` (o pin fixa a MESMA imagem já limpa; o fix de pip/setuptools + apt upgrade continuam).

- [ ] **Step 3: Nota de manutenção nos READMEs**

Adicionar (seção de infra/segurança) uma linha em `README.md` e `README.en.md`: imagens são pinadas por digest; atualizar via Renovate/Dependabot (Docker digest updates).

- [ ] **Step 4: Commit**

```bash
git add Dockerfile README.md README.en.md
git commit -m "chore(docker): digest-pin da base (python/uv) + nota de manutenção (Renovate)"
```

---

### GATE C — verificação da Fase C

- [ ] Run: `ENVIRONMENT=development docker compose config >/dev/null && echo ok` → `ok`.
- [ ] Run: `ENVIRONMENT=development docker compose down -v && ENVIRONMENT=development docker compose up -d --build && sleep 120 && docker compose ps` → 7/7 healthy/running; depois `down -v`.
- [ ] Run: `uv run pytest -m e2e --no-cov` → PASS (o stack em modo produção sobe com o hardening novo).
- [ ] Run: `docker build -t myapp-backend:final . && docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest image --severity HIGH,CRITICAL --ignore-unfixed --no-progress --skip-version-check --exit-code 1 myapp-backend:final` → trivy exit 0.

---

## Notas de verificação (APIs a confirmar na implementação)

1. **Corpo puro da task TaskIQ** (A3 Step 1): confirmar o atributo em taskiq 0.12.x para chamar a função subjacente in-process (`ping.original_func` / `.__wrapped__`); ajustar.
2. **`_build_broker` assinatura** (A2): confirmar que lê `settings` de módulo (monkeypatch de `worker.settings`).
3. **`broker.serializer`** (A2): confirmar o atributo que expõe o serializer no RedisStreamBroker.
4. **pytest-cov × `--cov-append`** (A1): validar que a 2ª run acumula no `.coverage`; se não, usar `[tool.coverage.run] parallel=true` + `coverage combine` antes do `report`.
5. **`cap_add` do Postgres** (C2): conjunto mínimo determinado empiricamente; documentar.
6. **storage.py:160-164** (A3 Step 2): identificar a op exata não coberta lendo o arquivo.
