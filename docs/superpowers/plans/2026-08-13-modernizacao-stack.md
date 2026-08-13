# Modernização da Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modernizar o template para Python 3.14, PostgreSQL 18, Redis 8, uv (no lugar de Poetry) e TaskIQ (no lugar de Celery), com todas as demais dependências no latest, sem regredir segurança nem o gate de qualidade.

**Architecture:** Entrega em 4 fases sequenciais com gate de verificação a cada uma (runtime+packaging → imagens de infra → deps latest → troca de fila). A troca Celery→TaskIQ fica por último, isolada dos bumps de versão, porque `taskiq-redis` exige `redis-py>=8` enquanto `celery/kombu` exige `redis-py<6.5` — os dois nunca coexistem.

**Tech Stack:** Python 3.14, uv, FastAPI, SQLAlchemy 2 + psycopg3, Alembic, PostgreSQL 18, Redis 8, TaskIQ + taskiq-redis, slowapi, MinIO, pytest/mypy/ruff, Docker Compose, testcontainers.

**Spec:** `docs/superpowers/specs/2026-08-13-modernizacao-stack-design.md`

## Global Constraints

- **Python:** `requires-python = ">=3.14,<3.15"`; ruff `target-version = "py314"`; mypy `python_version = "3.14"`.
- **Segurança não regride:** guards fail-closed preservados; serialização de fila **sem pickle** (JSON/ORJSON explícito); segmentação de rede mantida (fila isolada do rate-limit); healthchecks provam função, não só processo vivo.
- **Gate de qualidade:** `ruff check` + `ruff format --check` + `mypy --strict` + `pytest` com cobertura **>= 90%** devem passar; integração e e2e (Docker) verdes ao final de cada fase que os toca.
- **Comandos rodam via uv** a partir da Fase 1: `uv run <cmd>` (não `poetry run`).
- **Duas instâncias de servidor Redis** (rate-limit + fila) permanecem separadas por segurança; ambas em `redis:8-alpine`.
- **Trava de cliente:** `redis-py` fica em 6.4.x até a Fase 4; só sobe para 8.x quando o Celery sai e o TaskIQ entra (atômico).
- **Branch:** já em `modernizacao/stack-python314-pg18-uv-taskiq`. Commits pequenos e frequentes; cada task termina em commit.
- **Idioma:** comentários e docstrings em pt-BR, mantendo o tom do código existente.

---

## Mapa de arquivos

**Fase 1 (runtime + uv):** `pyproject.toml`, `uv.lock` (novo), `poetry.lock` (remover), `Dockerfile`, `.github/workflows/ci.yml`, `.python-version` (novo).
**Fase 2 (infra):** `docker-compose.yml`, `tests/integration/conftest.py`.
**Fase 3 (deps):** `pyproject.toml`, `uv.lock`.
**Fase 4 (TaskIQ):** `app/worker.py` (reescrito), `app/worker_healthcheck.py` (novo), `app/core/config.py`, `app/core/security_guards.py`, `app/main.py`, `pyproject.toml`, `uv.lock`, `docker-compose.yml`, `.env.example`, `tests/conftest.py`, `tests/test_celery.py`→`tests/test_taskiq.py`, `tests/integration/test_celery_broker.py`→`tests/integration/test_taskiq_broker.py`, `tests/integration/test_celery_beat.py`→`tests/integration/test_taskiq_scheduler.py`, `tests/e2e/conftest.py`, `tests/e2e/test_stack.py`, `tests/e2e/test_stack_prod.py`, `README.md`, `README.en.md`.

---

# FASE 1 — Python 3.14 + migração para uv

### Task 1.1: Ajustar `pyproject.toml` para Python 3.14 e uv

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `pyproject.toml` válido para uv (sem `[tool.poetry]`/`[build-system]` poetry), Python 3.14 como alvo.

- [ ] **Step 1: Editar `[project].requires-python`**

De `requires-python = ">=3.12,<3.15"` para:
```toml
requires-python = ">=3.14,<3.15"
```

- [ ] **Step 2: Converter as faixas de versão do formato Poetry para PEP 508**

Em `[project].dependencies`, trocar a sintaxe Poetry `"pkg (>=x,<y)"` pela padrão `"pkg>=x,<y"`. Resultado:
```toml
dependencies = [
    "fastapi>=0.136.3,<0.137.0",
    "uvicorn[standard]>=0.48.0,<0.49.0",
    "sqlalchemy>=2.0.50,<3.0.0",
    "alembic>=1.18.4,<2.0.0",
    "pydantic-settings>=2.14.1,<3.0.0",
    "psycopg[binary]>=3.3.4,<4.0.0",
    "slowapi>=0.1.9,<0.2.0",
    "redis>=6.4.0,<9.0.0",
    "celery[redis]>=5.6,<6.0",
    "minio>=7.2.20,<8.0.0",
]
```
(Bumps de versão vêm nas Fases 3 e 4 — aqui só muda a sintaxe.)

- [ ] **Step 3: Remover as seções específicas do Poetry**

Apagar o bloco `[tool.poetry]` (com `package-mode = false` e o comentário) e o bloco `[build-system]` (poetry-core). uv não empacota por default — sem `[build-system]`, o projeto é tratado como aplicação (equivalente a `package-mode = false`).

- [ ] **Step 4: Converter `[dependency-groups]` para o formato PEP 735**

Trocar a sintaxe Poetry pela padrão:
```toml
[dependency-groups]
dev = [
    "pytest>=9.0.3,<10.0.0",
    "pytest-asyncio>=1.4.0,<2.0.0",
    "httpx>=0.28.1,<0.29.0",
    "ruff>=0.15.15,<0.16.0",
    "pip-audit>=2.10.0,<3.0.0",
    "mypy>=2.1.0,<3.0.0",
    "pytest-cov>=7.1.0,<8.0.0",
    "testcontainers>=4.14.2,<5.0.0",
    "hypothesis>=6.155.2,<7.0.0",
    "mutmut>=3.5.0,<4.0.0",
]
```

- [ ] **Step 5: Atualizar alvos de ruff e mypy**

```toml
[tool.ruff]
line-length = 88
target-version = "py314"
```
```toml
[tool.mypy]
python_version = "3.14"
strict = true
files = ["app", "tests"]
exclude = ["^mutants/"]
plugins = ["pydantic.mypy"]
```

- [ ] **Step 6: Validar sintaxe do arquivo**

Run: `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('ok')"`
Expected: imprime `ok` (TOML válido).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml
git commit -m "chore(pyproject): Python 3.14 e formato uv/PEP 621+735"
```

---

### Task 1.2: Gerar `uv.lock` e remover `poetry.lock`

**Files:**
- Create: `uv.lock`
- Create: `.python-version`
- Delete: `poetry.lock`

**Interfaces:**
- Consumes: `pyproject.toml` da Task 1.1.
- Produces: ambiente resolvido pelo uv (`uv.lock`), pin de Python.

- [ ] **Step 1: Garantir o Python 3.14 disponível ao uv**

Run: `uv python install 3.14`
Expected: 3.14 instalado/disponível.

- [ ] **Step 2: Fixar a versão do projeto**

Run: `uv python pin 3.14` (cria/atualiza `.python-version` com `3.14`).

- [ ] **Step 3: Resolver e gerar o lock**

Run: `uv lock`
Expected: `uv.lock` criado sem erros de resolução.

- [ ] **Step 4: Sincronizar o venv (com grupo dev) e sanity-check**

Run: `uv sync` então `uv run python -c "import fastapi, sqlalchemy, celery; print('deps ok')"`
Expected: imprime `deps ok`.

- [ ] **Step 5: Rodar a suíte unitária no novo ambiente**

Run: `uv run pytest`
Expected: PASS, cobertura >= 90% (baseline preservado sob Python 3.14).

- [ ] **Step 6: Remover o lock do Poetry e commitar**

```bash
git rm poetry.lock
git add uv.lock .python-version
git commit -m "chore(uv): gera uv.lock, pin Python 3.14, remove poetry.lock"
```

---

### Task 1.3: Migrar o `Dockerfile` para uv + Python 3.14

**Files:**
- Modify: `Dockerfile`

**Interfaces:**
- Consumes: `pyproject.toml`, `uv.lock`.
- Produces: imagem runtime com `.venv` em `/app/.venv` (PATH já inclui) — inalterado para o entrypoint.

- [ ] **Step 1: Reescrever o estágio de build**

Substituir o estágio `builder` por um baseado em uv:
```dockerfile
# --- Estágio de build: resolve dependências num venv isolado via uv ---
# H2: fixe a base por DIGEST em produção (uma re-publicação da tag muda o
# conteúdo). Obtenha com `docker buildx imagetools inspect python:3.14-slim`.
FROM python:3.14-slim AS builder

# Copia o binário do uv de uma imagem oficial fixada (pin por tag; em produção
# prefira pin por digest, mantido via Renovate/Dependabot).
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Instala só as dependências primeiro (aproveita o cache de camadas): sem o
# código, --no-install-project evita reinstalar a cada mudança de fonte.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
```

- [ ] **Step 2: Ajustar o estágio de runtime**

Trocar a base para 3.14 e remover qualquer resíduo de Poetry (o runtime só copia o venv):
```dockerfile
# --- Estágio de runtime: slim, sem uv/pip/ferramentas de build ---
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN groupadd -r app && useradd -r -g app -d /app app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app . .

RUN chmod +x /app/docker/entrypoint.sh

USER app
EXPOSE 8000
```
(Mantém HEALTHCHECK, ENTRYPOINT e CMD existentes — inalterados.)

- [ ] **Step 3: Build da imagem**

Run: `docker build -t myapp-backend:uv .`
Expected: build conclui; sem menções a Poetry.

- [ ] **Step 4: Smoke test da imagem**

Run: `docker run --rm myapp-backend:uv python -c "import fastapi, celery; print('img ok')"`
Expected: imprime `img ok`.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile
git commit -m "chore(docker): Dockerfile em uv + Python 3.14-slim"
```

---

### Task 1.4: Migrar a CI para uv + Python 3.14

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `pyproject.toml`, `uv.lock`, `.python-version`.

- [ ] **Step 1: Resolver o SHA fixo da action `astral-sh/setup-uv`**

Run: `gh api repos/astral-sh/setup-uv/releases/latest --jq '.tag_name'` e depois
`gh api repos/astral-sh/setup-uv/git/refs/tags/<tag> --jq '.object.sha'`
Anote `<SHA>` e `<tag>` para o pin (coerente com a política de fixar actions por SHA).

- [ ] **Step 2: Substituir setup em TODOS os jobs (`test`, `integration`, `e2e`, `security`)**

Em cada job, trocar os passos "Instalar Poetry" + `actions/setup-python` por:
```yaml
      - name: Instalar uv
        uses: astral-sh/setup-uv@<SHA> # <tag>
        with:
          python-version: "3.14"
          enable-cache: true

      - name: Instalar dependências
        run: uv sync --frozen
```
(No job `security` só existe checkout + build/scan Docker — se não instalar deps, não precisa do uv; manter como está.)

- [ ] **Step 3: Trocar os comandos `poetry run` por `uv run` no job `test`**

```yaml
      - name: Lint (ruff)
        run: uv run ruff check .
      - name: Formatação (ruff format)
        run: uv run ruff format --check .
      - name: Checagem de tipos (mypy --strict)
        run: uv run mypy .
      - name: Testes + cobertura (pytest, gate 90%)
        run: uv run pytest
      - name: Auditoria de dependências (pip-audit)
        run: |
          uv run python -m pip install --upgrade pip
          uv run pip-audit
```

- [ ] **Step 4: Trocar os comandos nos jobs `integration` e `e2e`**

```yaml
      - name: Testes de integração (testcontainers)
        run: uv run pytest -m integration --no-cov
```
```yaml
      - name: Testes e2e (compose + httpx)
        run: uv run pytest -m e2e --no-cov
```

- [ ] **Step 5: Validar o YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"`
Expected: imprime `yaml ok`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: migra para setup-uv + uv run, Python 3.14"
```

---

### GATE 1 — verificação da Fase 1

- [ ] Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`
  Expected: tudo PASS, cobertura >= 90%.
- [ ] Run: `docker build -t myapp-backend:fase1 .`
  Expected: build OK.

---

# FASE 2 — Imagens de infra (PG18, Redis 8, MinIO latest)

### Task 2.1: Atualizar imagens no `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: stack em PG18/Redis8/MinIO-latest, com `PGDATA` fixado para preservar o mount.

- [ ] **Step 1: Confirmar o comportamento de `PGDATA` do PG18**

Run: `docker run --rm postgres:18-alpine sh -c 'echo default PGDATA: $PGDATA'`
Anote o valor. Se **não** for `/var/lib/postgresql/data`, o Step 2 (fixar `PGDATA`) é obrigatório para o volume `postgres_data:/var/lib/postgresql/data` continuar sendo o data dir.

- [ ] **Step 2: Atualizar o serviço `db`**

No serviço `db`: trocar a imagem e fixar `PGDATA` no bloco `environment`:
```yaml
  db:
    image: postgres:18-alpine
    ...
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-myapp}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-myapp}
      POSTGRES_DB: ${POSTGRES_DB:-myapp}
      # PG18 mudou o PGDATA default (inclui a major). Fixa no caminho do mount
      # para preservar o volume postgres_data:/var/lib/postgresql/data.
      PGDATA: /var/lib/postgresql/data
```

- [ ] **Step 3: Atualizar as imagens Redis (as 2 instâncias)**

Trocar `image: redis:7-alpine` por `image: redis:8-alpine` nos serviços `redis` e `redis-celery`.

- [ ] **Step 4: Atualizar a imagem do MinIO para a release mais recente**

Run: `curl -s https://quay.io/api/v1/repository/minio/minio/tag/?limit=5 | python3 -c "import sys,json;[print(t['name']) for t in json.load(sys.stdin)['tags'] if t['name'].startswith('RELEASE')]" | head -1`
Anote a tag `<RELEASE>` e trocar no serviço `minio`: `image: quay.io/minio/minio:<RELEASE>`.

- [ ] **Step 5: Validar o compose**

Run: `docker compose config >/dev/null && echo "compose ok"`
Expected: imprime `compose ok`.

- [ ] **Step 6: Subir infra do zero e checar saúde**

Run:
```bash
ENVIRONMENT=development docker compose down -v
ENVIRONMENT=development docker compose up -d db redis redis-celery minio
sleep 20 && docker compose ps
```
Expected: `db`, `redis`, `redis-celery`, `minio` como `healthy`.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml
git commit -m "chore(compose): PostgreSQL 18, Redis 8, MinIO latest (+ PGDATA fixo)"
```

---

### Task 2.2: Atualizar o Redis dos testes de integração

**Files:**
- Modify: `tests/integration/conftest.py:17-22`

**Interfaces:**
- Consumes: nada novo.
- Produces: `redis_container` em `redis:8-alpine`.

- [ ] **Step 1: Trocar a imagem e o comentário no helper `redis_container`**

```python
@contextmanager
def redis_container(password: str) -> Iterator[str]:
    """Redis 8 efêmero com --requirepass; produz a URL base (sem /db)."""
    container = (
        DockerContainer("redis:8-alpine")
        .with_command(f"redis-server --requirepass {password}")
        .with_exposed_ports(6379)
    )
```

- [ ] **Step 2: Rodar a integração (ainda no Celery) contra as imagens novas**

Run: `uv run pytest -m integration --no-cov`
Expected: PASS (drivers psycopg3/redis-py/minio ok contra PG18/Redis8/MinIO-latest).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "test(integration): Redis 8 no helper de container"
```

---

### GATE 2 — verificação da Fase 2

- [ ] Run: `uv run pytest -m integration --no-cov`  → PASS
- [ ] Run: `ENVIRONMENT=production ... uv run pytest -m e2e --no-cov` (o e2e gera credenciais fortes internamente) → PASS
- [ ] Run: `docker compose down -v` para limpar o volume PG16 antigo.

---

# FASE 3 — Dependências de app e dev para o latest

### Task 3.1: Bump das dependências de aplicação

**Files:**
- Modify: `pyproject.toml` (`[project.dependencies]`)
- Modify: `uv.lock`

**Interfaces:**
- Produces: deps de app no latest (exceto `redis-py`, que fica em 6.4.x pelo kombu).

- [ ] **Step 1: Atualizar as faixas em `[project.dependencies]`**

```toml
dependencies = [
    "fastapi>=0.141.1,<0.142.0",
    "uvicorn[standard]>=0.52.2,<0.53.0",
    "sqlalchemy>=2.0.52,<3.0.0",
    "alembic>=1.19.1,<2.0.0",
    "pydantic-settings>=2.15.0,<3.0.0",
    "psycopg[binary]>=3.3.4,<4.0.0",
    "slowapi>=0.1.10,<0.2.0",
    # redis: kombu (Celery) capa em <6.5; a interseção resolve para 6.4.x.
    # Sai da trava só na Fase 4 (TaskIQ exige redis>=8; Celery/kombu saem).
    "redis>=6.4.0,<9.0.0",
    "celery[redis]>=5.6,<6.0",
    "minio>=7.2.20,<8.0.0",
]
```

- [ ] **Step 2: Relock e sync**

Run: `uv lock && uv sync`
Expected: resolve sem conflito; `redis-py` permanece 6.4.x (confirme com `uv run python -c "import redis; print(redis.__version__)"`).

- [ ] **Step 3: Rodar lint/tipos/unit**

Run: `uv run ruff check . && uv run mypy . && uv run pytest`
Expected: PASS. Se houver falha nova (ex.: deprecação do FastAPI/SQLAlchemy), corrigir pontualmente sem afrouxar o `--strict` e re-rodar.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): app no latest (FastAPI 0.141, uvicorn 0.52, SQLAlchemy 2.0.52, ...)"
```

---

### Task 3.2: Bump das dependências de desenvolvimento

**Files:**
- Modify: `pyproject.toml` (`[dependency-groups].dev`)
- Modify: `uv.lock`

- [ ] **Step 1: Atualizar as faixas dev**

```toml
[dependency-groups]
dev = [
    "pytest>=9.1.1,<10.0.0",
    "pytest-asyncio>=1.4.0,<2.0.0",
    "httpx>=0.28.1,<0.29.0",
    "ruff>=0.16.2,<0.17.0",
    "pip-audit>=2.10.1,<3.0.0",
    "mypy>=2.3.0,<3.0.0",
    "pytest-cov>=7.1.0,<8.0.0",
    "testcontainers>=4.15.0,<5.0.0",
    "hypothesis>=6.165.0,<7.0.0",
    "mutmut>=3.7.0,<4.0.0",
]
```

- [ ] **Step 2: Relock, sync e rodar o gate completo**

Run: `uv lock && uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`
Expected: PASS. `ruff 0.16` pode ter regras novas — se apontar, corrigir o código (não desabilitar regra) e re-rodar.

- [ ] **Step 3: Rodar integração e e2e**

Run: `uv run pytest -m integration --no-cov && uv run pytest -m e2e --no-cov`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps-dev): ferramentas no latest (ruff 0.16, mypy 2.3, pytest 9.1, ...)"
```

---

### GATE 3 — verificação da Fase 3

- [ ] Gate completo verde: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest && uv run pytest -m integration --no-cov && uv run pytest -m e2e --no-cov`.

---

# FASE 4 — Celery → TaskIQ

> A partir daqui, Celery/kombu saem e TaskIQ entra; `redis-py` sobe para 8.x. As tasks das 4.1–4.6 alteram código de produção com a suíte unitária como rede; a 4.9–4.11 reescrevem os testes. Ordem: settings → guard → worker → main → healthcheck → deps, depois compose/env/testes/docs.

### Task 4.1: Renomear settings de Celery para TaskIQ

**Files:**
- Modify: `app/core/config.py:90-100`

**Interfaces:**
- Produces: `Settings.taskiq_broker_url: str`, `Settings.taskiq_result_backend: str`, `Settings.taskiq_in_memory: bool`, `Settings.taskiq_heartbeat_seconds: float`.

- [ ] **Step 1: Substituir o bloco de settings do Celery**

Trocar as linhas 90-100 (bloco `# Celery ...`) por:
```python
    # TaskIQ — broker e result backend numa instância Redis DEDICADA
    # (redis-taskiq), separada do Redis do rate-limit; em produção o guard
    # recusa apontar os dois para a mesma instância. DB 0 = broker (stream),
    # DB 1 = resultados (keyspaces separados facilitam inspeção/limpeza).
    taskiq_broker_url: str = "redis://:myapp@redis-taskiq:6379/0"
    taskiq_result_backend: str = "redis://:myapp@redis-taskiq:6379/1"
    # Execução in-process (InMemoryBroker, sem broker real) — só para testes.
    taskiq_in_memory: bool = False
    # Cadência do heartbeat agendado (core.ping). Retunável via env sem mudar
    # código; a integração do pipeline scheduler→broker→worker usa 1s.
    taskiq_heartbeat_seconds: float = 60.0
```

- [ ] **Step 2: Verificar que o módulo importa**

Run: `uv run python -c "from app.core.config import Settings; s=Settings(environment='development'); print(s.taskiq_broker_url, s.taskiq_in_memory)"`
Expected: `redis://:myapp@redis-taskiq:6379/0 False`.

- [ ] **Step 3: Commit** (a suíte ainda quebra aqui — commit intermediário coeso)

```bash
git add app/core/config.py
git commit -m "refactor(config): settings celery_* -> taskiq_*"
```

---

### Task 4.2: Renomear o guard de segurança para TaskIQ

**Files:**
- Modify: `app/core/security_guards.py`

**Interfaces:**
- Consumes: `Settings.taskiq_broker_url`, `Settings.taskiq_result_backend`.
- Produces: `validate_taskiq_security(settings: Settings) -> None` (mesmos invariantes do antigo `validate_celery_security`).

- [ ] **Step 1: Renomear a constante de schemes**

`_CELERY_ALLOWED_SCHEMES` → `_TASKIQ_ALLOWED_SCHEMES` (valor `{"redis", "rediss"}`; atualizar o comentário para citar `redis-taskiq`).

- [ ] **Step 2: Reescrever `validate_celery_security` como `validate_taskiq_security`**

Assinatura e corpo (mesma lógica; troca os campos e o texto das mensagens de "Celery" para "TaskIQ", e `redis-celery` → `redis-taskiq`):
```python
def validate_taskiq_security(settings: Settings) -> None:
    """Guards fail-closed do TaskIQ — só têm efeito em produção.

    Chamado pelo create_app (API) e no import de app.worker (worker/scheduler):
    qualquer processo que toque o broker valida a config antes de subir. Fora
    de produção não levanta (dev usa defaults fracos).
    """
    if not settings.is_production:
        return

    for label, url in (
        ("broker (TASKIQ_BROKER_URL)", settings.taskiq_broker_url),
        ("result backend (TASKIQ_RESULT_BACKEND)", settings.taskiq_result_backend),
    ):
        parsed = urlparse(url)
        if parsed.scheme not in _TASKIQ_ALLOWED_SCHEMES:
            raise ValueError(
                f"TaskIQ em produção exige redis:// ou rediss:// no {label}; "
                f"recebido scheme {parsed.scheme!r}."
            )
        if (parsed.password or "") in WEAK_PASSWORDS:
            raise ValueError(
                f"Senha do TaskIQ default/fraca (ou ausente) no {label} não é "
                "permitida em produção. Use uma senha forte "
                "(redis://:SENHA@redis-taskiq:6379/N)."
            )

    if settings.rate_limit_enabled and settings.rate_limit_storage_uri.startswith(
        "redis"
    ):
        rl_instance = _host_port(settings.rate_limit_storage_uri)
        for label, url in (
            ("broker", settings.taskiq_broker_url),
            ("result backend", settings.taskiq_result_backend),
        ):
            if _host_port(url) == rl_instance:
                raise ValueError(
                    f"O {label} do TaskIQ aponta para a mesma instância Redis "
                    "do rate-limit (host:porta iguais). Em produção use uma "
                    "instância dedicada (ex.: redis-taskiq) — a separação "
                    "contém o raio de explosão de um task comprometido."
                )
```

- [ ] **Step 3: Atualizar a chamada em `validate_production`**

Na função `validate_production`, trocar a linha `validate_celery_security(settings)` por `validate_taskiq_security(settings)` (e o comentário acima, "Broker/result backend do Celery" → "do TaskIQ").

- [ ] **Step 4: Atualizar o docstring do módulo**

No topo do arquivo, trocar "o worker do Celery (app.worker)" por "o worker/scheduler do TaskIQ (app.worker)".

- [ ] **Step 5: Sanity de import**

Run: `uv run python -c "from app.core.security_guards import validate_taskiq_security; print('ok')"`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add app/core/security_guards.py
git commit -m "refactor(guards): validate_celery_security -> validate_taskiq_security"
```

---

### Task 4.3: Reescrever `app/worker.py` com TaskIQ

**Files:**
- Modify: `app/worker.py` (reescrita completa)

**Interfaces:**
- Consumes: `get_settings()`, `validate_taskiq_security`, `Settings.taskiq_*`.
- Produces: `broker` (AsyncBroker), `scheduler` (TaskiqScheduler), `ping` (async task registrada), `HEARTBEAT_KEY: str`.

- [ ] **Step 1: Escrever o novo `app/worker.py`**

```python
"""Fila de tarefas assíncrona (TaskIQ) do MyApp — worker + scheduler.

SEGURANÇA: broker e result backend vivem numa instância Redis DEDICADA
(redis-taskiq), separada do Redis do rate-limit. O docker-compose reforça
isso por rede: worker/scheduler entram em data_net + taskiq_net e NÃO têm rota
para ratelimit_net. O guard abaixo torna a separação um invariante de config:
em produção, broker/backend na mesma instância do rate-limit recusam o boot.

Serialização JSON-only (sem pickle) nas mensagens e resultados — um broker ou
result backend comprometido não vira vetor de desserialização-RCE.
"""

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

    result_backend: RedisAsyncResultBackend[object] = RedisAsyncResultBackend(
        redis_url=settings.taskiq_result_backend,
        result_ex_time=86400,
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
    schedule=[{"interval": settings.taskiq_heartbeat_seconds}],
)
async def ping() -> str:
    """Task de debug/heartbeat: valida o pipeline scheduler→broker→worker.

    Idempotente. Além de retornar "pong", grava um marcador de heartbeat com
    TTL (3x a cadência) — o healthcheck do container scheduler usa o frescor
    desse marcador como sinal de vida do pipeline inteiro.
    """
    import redis.asyncio as aioredis

    client = aioredis.from_url(settings.taskiq_broker_url)
    try:
        ttl = max(int(settings.taskiq_heartbeat_seconds * 3), 1)
        await client.set(HEARTBEAT_KEY, "pong", ex=ttl)
    finally:
        await client.aclose()
    return "pong"
```

- [ ] **Step 2: Verificar import e registro da task**

Run: `TASKIQ_IN_MEMORY=true uv run python -c "from app.worker import broker, ping, scheduler; print(type(broker).__name__, ping.task_name)"`
Expected: `InMemoryBroker core.ping`.

- [ ] **Step 3: Commit**

```bash
git add app/worker.py
git commit -m "feat(worker): fila TaskIQ (RedisStreamBroker + scheduler), heartbeat com marcador"
```

---

### Task 4.4: Ligar o broker ao ciclo de vida da API

**Files:**
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `broker` de `app.worker`.
- Produces: `create_app` inicia/encerra o broker (a API despacha tasks via `.kiq()`).

- [ ] **Step 1: Escrever o teste do lifespan (TDD)**

O conftest já exporta `TASKIQ_IN_MEMORY=true` (Task 4.9 Step 1a), então o broker é InMemory. Criar `tests/test_app_lifespan.py`:
```python
def test_broker_iniciado_e_encerrado_no_ciclo_de_vida() -> None:
    # Entrar no contexto do TestClient dispara o lifespan; sair, o shutdown.
    # Sem erro => startup/shutdown do broker rodaram (InMemory na suíte).
    from app.worker import broker

    from tests.conftest import make_client

    with make_client():
        assert broker.is_worker_process is False


async def test_api_despacha_ping_com_broker_iniciado() -> None:
    # A API despacha via .kiq(); com InMemory, executa in-process e retorna.
    from app.worker import broker, ping

    await broker.startup()
    try:
        task = await ping.kiq()
        result = await task.wait_result(timeout=5)
        assert result.return_value == "pong"
    finally:
        await broker.shutdown()
```

- [ ] **Step 2: Rodar o teste (deve falhar)**

Run: `uv run pytest tests/test_app_lifespan.py -v --no-cov`
Expected: FAIL (lifespan ainda não gerencia o broker / import de `app.worker` em `main.py` ainda não existe).

- [ ] **Step 3: Adicionar o lifespan em `create_app`**

Importar no topo de `app/main.py`:
```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from app.worker import broker as taskiq_broker
```
Definir o lifespan e passá-lo ao `FastAPI(...)`:
```python
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # A API só DESPACHA tasks (.kiq()); inicia o broker no processo web,
        # nunca no worker (is_worker_process). InMemoryBroker no-op em testes.
        if not taskiq_broker.is_worker_process:
            await taskiq_broker.startup()
        yield
        if not taskiq_broker.is_worker_process:
            await taskiq_broker.shutdown()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )
```

- [ ] **Step 4: Rodar o teste (deve passar)**

Run: `uv run pytest tests/test_app_lifespan.py -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_app_lifespan.py
git commit -m "feat(main): inicia/encerra o broker TaskIQ no lifespan da API"
```

---

### Task 4.5: Healthcheck do worker (round-trip real)

**Files:**
- Create: `app/worker_healthcheck.py`

**Interfaces:**
- Consumes: `broker`, `ping` de `app.worker`.
- Produces: módulo executável `python -m app.worker_healthcheck` (exit 0 = worker consumindo; 1 = falha).

- [ ] **Step 1: Escrever o probe**

```python
"""Healthcheck do worker TaskIQ: round-trip REAL pelo broker.

Processo vivo != worker funcional. Este probe enfileira o core.ping e aguarda
o resultado no result backend com timeout curto — só passa se o loop de
consumo do worker estiver saudável (equivalente honesto ao `celery inspect
ping`). Usado no healthcheck do container worker.
"""

import asyncio
import sys

from app.worker import broker, ping

_TIMEOUT_S = 5.0


async def _probe() -> int:
    await broker.startup()
    try:
        task = await ping.kiq()
        result = await asyncio.wait_for(
            task.wait_result(timeout=_TIMEOUT_S), timeout=_TIMEOUT_S + 1
        )
        return 0 if result.return_value == "pong" else 1
    except (TimeoutError, asyncio.TimeoutError):
        return 1
    finally:
        await broker.shutdown()


def main() -> int:
    return asyncio.run(_probe())


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke local com InMemoryBroker**

Run: `TASKIQ_IN_MEMORY=true uv run python -m app.worker_healthcheck; echo "exit=$?"`
Expected: `exit=0` (InMemory executa o ping in-process).

- [ ] **Step 3: Commit**

```bash
git add app/worker_healthcheck.py
git commit -m "feat(worker): healthcheck round-trip real do worker TaskIQ"
```

---

### Task 4.6: Trocar as dependências para TaskIQ (Celery sai, redis-py sobe)

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: ambiente com `taskiq`, `taskiq-redis`, `redis>=8`; sem `celery`/`kombu`.

- [ ] **Step 1: Editar `[project.dependencies]`**

Remover `"celery[redis]>=5.6,<6.0"`; ajustar redis e adicionar TaskIQ:
```toml
    # redis-py 8.x: exigido pelo taskiq-redis (Celery/kombu, que capavam em
    # <6.5, saíram). Cliente único serve fila E rate-limit.
    "redis>=8.0.0,<9.0.0",
    "taskiq>=0.12.4,<0.13.0",
    "taskiq-redis>=1.2.3,<2.0.0",
```
Manter `orjson` disponível (dependência transitiva do taskiq; se o `import` da Task 4.3 exigir, adicionar `"orjson>=3.10,<4.0.0"` explicitamente).

- [ ] **Step 2: Atualizar overrides de mypy em `pyproject.toml`**

- Remover o override de `celery` (linhas com `module = ["celery.*", ...]` e `module = ["app.worker"]` referentes ao decorator Celery).
- Manter `testcontainers` no override de imports.
- Adicionar, se necessário (rodar mypy no Step 4 para confirmar):
```toml
[[tool.mypy.overrides]]
module = ["taskiq_redis.*"]
ignore_missing_imports = true
```

- [ ] **Step 3: Atualizar `[tool.mutmut].do_not_mutate` se preciso**

As tasks TaskIQ são async e o `broker` é de import-time. Manter `app/core/database.py` e `app/features/health/router.py`; reavaliar exclusão de `app/worker.py` (rodar mutmut é opcional — métrica local). Deixar comentário atualizado ("Celery" → "TaskIQ").

- [ ] **Step 4: Relock, sync e checagens**

Run: `uv lock && uv sync`
Run: `uv run python -c "import redis, taskiq, taskiq_redis; print(redis.__version__)"`
Expected: versão 8.x; `celery` não instalado (`uv run python -c "import celery" ` deve falhar).
Run: `uv run mypy .`
Expected: PASS (ajustar overrides se apontar `taskiq`/`taskiq_redis` sem tipos).

- [ ] **Step 5: Verificar slowapi/limits contra redis-py 8**

Run: `uv run pytest tests/core/test_limiter.py tests/test_app_rate_limit.py -v --no-cov`
Expected: PASS (rate-limit funciona com o cliente redis 8). Se falhar por incompatibilidade do `limits`, subir `slowapi`/`limits` para a versão que suporta redis-py 8 e re-rodar.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): remove Celery/kombu, adiciona TaskIQ, redis-py 8"
```

---

### Task 4.7: Migrar os serviços de fila no `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: serviço `redis-taskiq`, rede `taskiq_net`, serviços `worker`/`scheduler` em TaskIQ, envs `TASKIQ_*`.

- [ ] **Step 1: Renomear a rede**

No topo (comentário de segmentação) e na seção `networks:`, trocar `celery_net` por `taskiq_net`. Atualizar o comentário para citar worker/scheduler.

- [ ] **Step 2: Renomear o serviço `redis-celery` → `redis-taskiq`**

Trocar a chave do serviço, `container_name: myapp-redis-taskiq`, a env `CELERY_REDIS_PASSWORD` → `TASKIQ_REDIS_PASSWORD` (e o `$$CELERY_REDIS_PASSWORD` no `command`/healthcheck → `$$TASKIQ_REDIS_PASSWORD`). Manter todo o hardening (rename-command, maxmemory/noeviction, cap_drop, etc.) e `networks: [taskiq_net]`.

- [ ] **Step 3: Atualizar as envs de fila no serviço `api`**

Trocar:
```yaml
      TASKIQ_BROKER_URL: redis://:${TASKIQ_REDIS_PASSWORD:-myapp}@redis-taskiq:6379/0
      TASKIQ_RESULT_BACKEND: redis://:${TASKIQ_REDIS_PASSWORD:-myapp}@redis-taskiq:6379/1
```
(no lugar de `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`; remover o comentário "a api só DESPACHA .delay()" → ".kiq()"). Manter `depends_on` de `redis-celery` renomeado para `redis-taskiq`.

- [ ] **Step 4: Reescrever o serviço `worker`**

```yaml
  worker:
    build: .
    container_name: myapp-worker
    restart: unless-stopped
    command:
      ["taskiq", "worker", "app.worker:broker",
       "--ack-type", "when_executed", "--workers", "1", "--max-async-tasks", "2",
       "--log-level", "INFO"]
    depends_on:
      db:
        condition: service_healthy
      redis-taskiq:
        condition: service_healthy
    environment:
      ENVIRONMENT: ${ENVIRONMENT:?defina ENVIRONMENT ...}
      RUN_MIGRATIONS_ON_START: "false"
      POSTGRES_HOST: db
      POSTGRES_PORT: "5432"
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER:-myapp}:${POSTGRES_PASSWORD:-myapp}@db:5432/${POSTGRES_DB:-myapp}
      TASKIQ_BROKER_URL: redis://:${TASKIQ_REDIS_PASSWORD:-myapp}@redis-taskiq:6379/0
      TASKIQ_RESULT_BACKEND: redis://:${TASKIQ_REDIS_PASSWORD:-myapp}@redis-taskiq:6379/1
      MINIO_ENDPOINT: minio:9000
      MINIO_USE_SSL: "false"
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:-myapp}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-myapp-minio-dev}
      MINIO_BUCKET: ${MINIO_BUCKET:-myapp-files}
    healthcheck:
      # Round-trip real pelo broker (processo vivo != worker funcional).
      test: ["CMD-SHELL", "python -m app.worker_healthcheck"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    read_only: true
    tmpfs: ["/tmp"]
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    pids_limit: 256
    cpus: 1.0
    mem_limit: 512m
    networks: [data_net, taskiq_net]
```

- [ ] **Step 5: Substituir o serviço `beat` por `scheduler`**

```yaml
  scheduler:
    build: .
    container_name: myapp-scheduler
    restart: unless-stopped
    # EXATAMENTE 1 réplica: dois schedulers = disparo em dobro.
    command: ["taskiq", "scheduler", "app.worker:scheduler", "--log-level", "INFO"]
    depends_on:
      db:
        condition: service_healthy
      redis-taskiq:
        condition: service_healthy
    environment:
      ENVIRONMENT: ${ENVIRONMENT:?defina ENVIRONMENT ...}
      RUN_MIGRATIONS_ON_START: "false"
      POSTGRES_HOST: db
      POSTGRES_PORT: "5432"
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER:-myapp}:${POSTGRES_PASSWORD:-myapp}@db:5432/${POSTGRES_DB:-myapp}
      TASKIQ_BROKER_URL: redis://:${TASKIQ_REDIS_PASSWORD:-myapp}@redis-taskiq:6379/0
      TASKIQ_RESULT_BACKEND: redis://:${TASKIQ_REDIS_PASSWORD:-myapp}@redis-taskiq:6379/1
    healthcheck:
      # Sinal de vida do pipeline: a task agendada grava o marcador
      # myapp:taskiq:heartbeat (TTL 3x a cadência). Fresco => scheduler
      # publicou e o worker executou. TTL default = 180s.
      test:
        ["CMD-SHELL",
         "python -c \"import os,redis,sys; c=redis.from_url(os.environ['TASKIQ_BROKER_URL']); sys.exit(0 if c.get('myapp:taskiq:heartbeat') else 1)\""]
      interval: 60s
      timeout: 5s
      retries: 3
      start_period: 210s
    read_only: true
    tmpfs: ["/tmp"]
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    pids_limit: 128
    cpus: 0.25
    mem_limit: 256m
    networks: [data_net, taskiq_net]
```

- [ ] **Step 6: Validar o compose**

Run: `ENVIRONMENT=development docker compose config >/dev/null && echo ok`
Expected: `ok` (sem referências pendentes a `celery`/`redis-celery`/`celery_net`).

- [ ] **Step 7: Subir o stack e checar healthchecks**

Run:
```bash
ENVIRONMENT=development docker compose up -d --build
sleep 90 && docker compose ps
```
Expected: `myapp-worker` healthy (round-trip); `myapp-scheduler` running e, após ~1º disparo, healthy; `api` healthy.

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml
git commit -m "chore(compose): worker/scheduler TaskIQ, redis-taskiq, rede taskiq_net"
```

---

### Task 4.8: Atualizar `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Substituir o bloco `--- Celery ---`**

Trocar o cabeçalho e as três variáveis:
```dotenv
# --- TaskIQ (fila de tarefas assíncrona, rede interna do Docker) ---
# Broker e result backend numa instância Redis DEDICADA (redis-taskiq), sem
# porta publicada — SEPARADA do Redis do rate-limit. Worker/scheduler não têm
# rota de rede até o Redis do rate-limit (segmentação no docker-compose).
# redis:// ou rediss://; broker/backend NÃO podem apontar para a mesma
# instância do Redis do rate-limit (o guard de produção recusa).
TASKIQ_REDIS_PASSWORD=myapp
TASKIQ_BROKER_URL="redis://:myapp@redis-taskiq:6379/0"
TASKIQ_RESULT_BACKEND="redis://:myapp@redis-taskiq:6379/1"
```

- [ ] **Step 2: Atualizar menções a "Celery" no restante do arquivo**

Nos comentários dos guards (ex.: "a senha ... do Celery for default/fraca" e "o broker do Celery compartilhar a instância"), trocar "Celery" por "TaskIQ".

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs(env): variáveis TASKIQ_* no lugar de CELERY_*"
```

---

### Task 4.9: Reescrever os testes unitários da fila

**Files:**
- Modify: `tests/conftest.py:13` (env de teste) e `:65-66` (`make_prod_settings`)
- Delete: `tests/test_celery.py`
- Create: `tests/test_taskiq.py`

**Interfaces:**
- Consumes: `validate_taskiq_security`, `broker`, `ping`, `Settings.taskiq_*`.

- [ ] **Step 1a: Forçar `InMemoryBroker` na suíte unitária (conftest)**

O `lifespan` (Task 4.4) chama `broker.startup()` a cada `TestClient`; sem isso, a suíte tentaria conectar no `redis-taskiq` real (quebra na CI, que roda `uv run pytest` sem Redis). Logo abaixo de `os.environ.setdefault("ENVIRONMENT", "development")` no topo de `tests/conftest.py`, adicionar:
```python
# A suíte unitária roda a fila in-process (InMemoryBroker): o broker é
# construído no import de app.worker a partir de Settings.taskiq_in_memory —
# definir ANTES de qualquer import de app.worker. Integração/e2e sobem broker
# real (override explícito no env do subprocesso/compose).
os.environ.setdefault("TASKIQ_IN_MEMORY", "true")
```

- [ ] **Step 1b: Atualizar `make_prod_settings` no conftest**

Trocar as duas linhas de `celery_*` por:
```python
        "taskiq_broker_url": "redis://:S3nhaForteTaskiq123@redis-taskiq:6379/0",
        "taskiq_result_backend": "redis://:S3nhaForteTaskiq123@redis-taskiq:6379/1",
```

- [ ] **Step 2: Criar `tests/test_taskiq.py` (guards + defaults + config do broker)**

```python
from typing import Any

import pytest

from app.core.config import Settings
from app.core.security_guards import validate_taskiq_security

_STRONG_BACKEND = "redis://:S3nhaForteTaskiq123@redis-taskiq:6379/1"


def _prod(**overrides: Any) -> Settings:
    from tests.conftest import STRONG_REDIS_URI, make_prod_settings

    base: dict[str, Any] = {
        "rate_limit_enabled": True,
        "rate_limit_storage_uri": STRONG_REDIS_URI,
    }
    base.update(overrides)
    return make_prod_settings(**base)


def test_strong_and_separate_config_passes() -> None:
    validate_taskiq_security(_prod())


@pytest.mark.parametrize("weak", ["myapp", "password", ""])
def test_rejects_weak_broker_password(weak: str) -> None:
    cred = f":{weak}@" if weak else ""
    with pytest.raises(ValueError, match="TaskIQ"):
        validate_taskiq_security(
            _prod(taskiq_broker_url=f"redis://{cred}redis-taskiq:6379/0")
        )


def test_rejects_explicit_empty_password_with_at() -> None:
    with pytest.raises(ValueError, match="TaskIQ"):
        validate_taskiq_security(
            _prod(taskiq_broker_url="redis://:@redis-taskiq:6379/0")
        )


def test_rejects_broker_on_rate_limit_instance_with_implicit_port() -> None:
    with pytest.raises(ValueError, match="mesma instância"):
        validate_taskiq_security(
            _prod(taskiq_broker_url="redis://:S3nhaForteTaskiq123@redis/5")
        )


def test_rejects_weak_result_backend_password() -> None:
    with pytest.raises(ValueError, match="TaskIQ"):
        validate_taskiq_security(
            _prod(taskiq_result_backend="redis://:myapp@redis-taskiq:6379/1")
        )


@pytest.mark.parametrize(
    "url",
    ["memory://", "amqp://user:S3nhaForte123@rabbit:5672//", "nats://nats:4222"],
)
def test_rejects_non_redis_broker_scheme(url: str) -> None:
    with pytest.raises(ValueError, match="TaskIQ"):
        validate_taskiq_security(_prod(taskiq_broker_url=url))


def test_rejects_broker_on_rate_limit_redis_instance() -> None:
    with pytest.raises(ValueError, match="mesma instância"):
        validate_taskiq_security(
            _prod(taskiq_broker_url="redis://:S3nhaForteTaskiq123@redis:6379/5")
        )


def test_no_guard_outside_production() -> None:
    validate_taskiq_security(Settings(environment="development"))


def test_create_app_production_rejects_weak_taskiq_password() -> None:
    from app.main import create_app

    settings = Settings(
        environment="production",
        trusted_hosts=["api.test"],
        rate_limit_enabled=False,
        minio_root_user="myapp-svc-7f3a",
        minio_root_password="S3nhaForteMinio123",
        taskiq_broker_url="redis://:myapp@redis-taskiq:6379/0",
        taskiq_result_backend=_STRONG_BACKEND,
    )
    with pytest.raises(ValueError, match="TaskIQ"):
        create_app(settings)


def test_taskiq_settings_defaults() -> None:
    settings = Settings()
    assert settings.taskiq_broker_url.startswith("redis://")
    assert "redis-taskiq" in settings.taskiq_broker_url
    assert settings.taskiq_broker_url.endswith("/0")
    assert settings.taskiq_result_backend.endswith("/1")
    # taskiq_in_memory: o conftest exporta TASKIQ_IN_MEMORY=true para a suíte,
    # então uma instância lê True do ambiente. O DEFAULT do campo (o que vale
    # em produção sem a env) é False — é isso que garantimos aqui.
    assert Settings.model_fields["taskiq_in_memory"].default is False


def test_heartbeat_interval_e_configuravel() -> None:
    assert Settings().taskiq_heartbeat_seconds == 60.0
    assert Settings(taskiq_heartbeat_seconds=1.0).taskiq_heartbeat_seconds == 1.0


def test_ping_schedule_registrado() -> None:
    from app.worker import ping

    labels = ping.labels.get("schedule") or []
    assert any("interval" in s for s in labels), "core.ping sem schedule de intervalo"
    assert ping.task_name == "core.ping"


@pytest.mark.asyncio
async def test_ping_in_memory_returns_pong(monkeypatch: pytest.MonkeyPatch) -> None:
    # InMemoryBroker executa in-process; valida o round-trip sem Redis real.
    monkeypatch.setenv("TASKIQ_IN_MEMORY", "true")
    from app.worker import broker, ping

    await broker.startup()
    try:
        task = await ping.kiq()
        result = await task.wait_result(timeout=5)
        assert result.return_value == "pong"
    finally:
        await broker.shutdown()
```

- [ ] **Step 3: Remover o teste antigo**

Run: `git rm tests/test_celery.py`

- [ ] **Step 4: Rodar os testes unitários**

Run: `TASKIQ_IN_MEMORY=true uv run pytest tests/test_taskiq.py -v --no-cov`
Expected: PASS. (Ajustar `ping.labels`/`schedule` conforme a API real do TaskIQ verificada no ambiente — se `labels` diferir, inspecionar `ping.labels` e adequar a asserção.)

- [ ] **Step 5: Rodar a suíte unitária completa com cobertura**

Run: `TASKIQ_IN_MEMORY=true uv run pytest`
Expected: PASS, cobertura >= 90%.

- [ ] **Step 6: Commit**

```bash
git add tests/test_taskiq.py tests/conftest.py
git rm --cached tests/test_celery.py 2>/dev/null || true
git commit -m "test(taskiq): reescreve testes unitários da fila (guards, defaults, ping)"
```

---

### Task 4.10: Reescrever os testes de integração da fila

**Files:**
- Delete: `tests/integration/test_celery_broker.py`, `tests/integration/test_celery_beat.py`
- Create: `tests/integration/test_taskiq_broker.py`, `tests/integration/test_taskiq_scheduler.py`

**Interfaces:**
- Consumes: `redis_container` (Redis 8), `broker`/`ping` do TaskIQ.

- [ ] **Step 1: Criar `tests/integration/test_taskiq_broker.py`**

```python
"""Integração do TaskIQ com broker REAL (Redis 8 via testcontainers).

Round-trip de verdade: task publicada no RedisStreamBroker, consumida por um
worker embutido (broker.startup + listen) e resultado lido do result backend.
"""

from collections.abc import AsyncIterator

import pytest
from taskiq import AsyncBroker
from taskiq.serializers import ORJSONSerializer
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from tests.integration.conftest import redis_container

pytestmark = pytest.mark.integration

_PASSWORD = "S3nhaTesteTaskiq123"


@pytest.fixture
async def real_broker() -> AsyncIterator[AsyncBroker]:
    with redis_container(_PASSWORD) as base:
        backend: RedisAsyncResultBackend[object] = RedisAsyncResultBackend(
            redis_url=f"{base}/1", result_ex_time=3600
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
    import asyncio

    # Sobe o loop de consumo do worker em background e publica o ping.
    task_worker = asyncio.create_task(_consume_once(real_broker))
    ping = real_broker.find_task("core.ping")
    assert ping is not None
    kicked = await ping.kiq()
    result = await asyncio.wait_for(kicked.wait_result(timeout=30), timeout=35)
    assert result.return_value == "pong"
    task_worker.cancel()


async def _consume_once(broker: AsyncBroker) -> None:
    # Consome mensagens do broker e executa (ack when_executed manual via
    # broker API). Mantido simples: itera o listen e resolve cada mensagem.
    async for message in broker.listen():
        await broker.ack(message)  # ajustar à API real de listen/ack do TaskIQ
```
> Nota de implementação: a forma canônica de "worker embutido" no TaskIQ é usar `taskiq.receiver.Receiver` ou rodar `broker.listen()`; verificar a API instalada (`taskiq==0.12.x`) e adequar `_consume_once` — o objetivo do teste (publicar → consumir → ler resultado "pong") é o invariante.

- [ ] **Step 2: Criar `tests/integration/test_taskiq_scheduler.py`**

```python
"""Integração do pipeline: scheduler -> broker -> worker -> marcador.

Um `taskiq scheduler` real (subprocesso) publica core.ping na cadência de 1s
(TASKIQ_HEARTBEAT_SECONDS=1); um worker real consome e a task grava o marcador
myapp:taskiq:heartbeat no Redis — prova de que o ciclo fechou.
"""

import os
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
import redis as redis_lib

from tests.integration.conftest import redis_container

pytestmark = pytest.mark.integration

_PASSWORD = "S3nhaTesteScheduler123"


@pytest.fixture(scope="module")
def broker_base() -> Iterator[str]:
    with redis_container(_PASSWORD) as base:
        yield base


def test_scheduler_dispara_heartbeat_e_worker_grava_marcador(broker_base: str) -> None:
    env = {
        **os.environ,
        "TASKIQ_BROKER_URL": f"{broker_base}/0",
        "TASKIQ_RESULT_BACKEND": f"{broker_base}/1",
        "TASKIQ_HEARTBEAT_SECONDS": "1",
        "TASKIQ_IN_MEMORY": "false",
    }
    worker = subprocess.Popen(
        [sys.executable, "-m", "taskiq", "worker", "app.worker:broker",
         "--ack-type", "when_executed", "--workers", "1"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    sched = subprocess.Popen(
        [sys.executable, "-m", "taskiq", "scheduler", "app.worker:scheduler"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        client = redis_lib.from_url(f"{broker_base}/0", socket_timeout=5)
        deadline = time.monotonic() + 30
        seen = False
        while time.monotonic() < deadline:
            if client.get("myapp:taskiq:heartbeat") == b"pong":
                seen = True
                break
            time.sleep(0.5)
        client.close()
        assert seen, "scheduler->broker->worker não gravou o marcador de heartbeat"
    finally:
        for p in (sched, worker):
            p.terminate()
            p.wait(timeout=10)
```

- [ ] **Step 3: Remover os testes antigos**

Run: `git rm tests/integration/test_celery_broker.py tests/integration/test_celery_beat.py`

- [ ] **Step 4: Rodar a integração**

Run: `uv run pytest -m integration --no-cov`
Expected: PASS (ajustar a API de consumo do TaskIQ no broker test conforme a nota).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_taskiq_broker.py tests/integration/test_taskiq_scheduler.py
git commit -m "test(integration): broker + scheduler TaskIQ (Redis real)"
```

---

### Task 4.11: Atualizar os testes e2e

**Files:**
- Modify: `tests/e2e/conftest.py`
- Modify: `tests/e2e/test_stack.py`
- Modify: `tests/e2e/test_stack_prod.py`

**Interfaces:**
- Consumes: stack compose com `myapp-worker`/`myapp-scheduler`/`redis-taskiq`/`taskiq_net`.

- [ ] **Step 1: `tests/e2e/conftest.py`**

- Linha ~112-115: comentários `redis-celery` → `redis-taskiq`; "worker: healthcheck = celery inspect ping" → "worker: healthcheck = round-trip real do TaskIQ".
- Linha ~139: env `CELERY_REDIS_PASSWORD` → `TASKIQ_REDIS_PASSWORD` (mantém `secrets.token_urlsafe(24)`).
- Se houver espera por `myapp-beat`, trocar para `myapp-scheduler`.

- [ ] **Step 2: `tests/e2e/test_stack.py` — isolamento de rede**

- `test_worker_nao_alcanca_redis_do_rate_limit`: comentário `celery_net` → `taskiq_net`; a asserção (worker NÃO alcança `redis`) permanece.
- `test_worker_alcanca_o_broker_dedicado`: alvo `redis-celery` → `redis-taskiq` (worker DEVE alcançar o próprio broker).

- [ ] **Step 3: `test_stack.py` — healthchecks e heartbeat**

- `test_worker_e_minio_healthy_beat_rodando` → renomear para `..._scheduler_rodando`; checar `container_state("myapp-scheduler").get("Status") == "running"` (worker healthy = round-trip).
- Reescrever `_HEARTBEAT_CHECK` para ler o marcador em vez de varrer `celery-task-meta-*`:
```python
_HEARTBEAT_CHECK = (
    "import os, sys, redis\n"
    "c = redis.from_url(os.environ['TASKIQ_BROKER_URL'], socket_timeout=5)\n"
    "print('HEARTBEAT-OK' if c.get('myapp:taskiq:heartbeat') else 'SEM-HEARTBEAT')\n"
    "sys.exit(0 if c.get('myapp:taskiq:heartbeat') else 1)\n"
)
```
- `test_beat_heartbeat_real_no_stack` → renomear para `test_scheduler_heartbeat_real_no_stack`; executar o `_HEARTBEAT_CHECK` de dentro do `worker` (que está em `taskiq_net`); janela até 120s (1º disparo). Env consumida: `TASKIQ_BROKER_URL`.
- `test_worker_se_recupera_de_restart`: inalterado (usa `myapp-worker` + health).

- [ ] **Step 4: `tests/e2e/test_stack_prod.py`**

- Linha ~6: comentário "rate-limit, Celery, MinIO" → "rate-limit, TaskIQ, MinIO". Verificar qualquer env/serviço `celery` remanescente e trocar.

- [ ] **Step 5: Rodar o e2e**

Run: `uv run pytest -m e2e --no-cov`
Expected: PASS (worker healthy por round-trip; scheduler publica; isolamento `taskiq_net` intacto).

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/
git commit -m "test(e2e): stack TaskIQ (worker/scheduler, taskiq_net, marcador de heartbeat)"
```

---

### Task 4.12: Atualizar a documentação

**Files:**
- Modify: `README.md`
- Modify: `README.en.md`

- [ ] **Step 1: Substituir Poetry → uv**

Trocar comandos de setup/instalação/execução: `poetry install` → `uv sync`; `poetry run X` → `uv run X`; menções ao lock `poetry.lock` → `uv.lock`. Requisito de Python: 3.14.

- [ ] **Step 2: Substituir Celery → TaskIQ**

Seções de arquitetura da fila: worker/beat → worker/scheduler; `redis-celery` → `redis-taskiq`; variáveis `CELERY_*` → `TASKIQ_*`; comandos `celery -A ...` → `taskiq worker/scheduler ...`. Descrever os healthchecks novos (round-trip + marcador).

- [ ] **Step 3: Atualizar a matriz/menções de versões**

Python 3.14, PostgreSQL 18, Redis 8, MinIO (release nova).

- [ ] **Step 4: Verificar que não sobraram referências obsoletas**

Run: `grep -rn -i "poetry\|celery\|redis-celery\|celery_net\|beat" README.md README.en.md`
Expected: sem ocorrências (ou só históricas intencionais). Corrigir o que sobrar.

- [ ] **Step 5: Commit**

```bash
git add README.md README.en.md
git commit -m "docs: READMEs em uv + TaskIQ, versões atualizadas (Py3.14/PG18/Redis8)"
```

---

### GATE 4 (final) — verificação completa

- [ ] Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy .`  → PASS
- [ ] Run: `uv run pytest`  → PASS, cobertura >= 90%
- [ ] Run: `uv run pytest -m integration --no-cov`  → PASS
- [ ] Run: `uv run pytest -m e2e --no-cov`  → PASS
- [ ] Run: `docker build -t myapp-backend:final .`  → OK
- [ ] Run: `ENVIRONMENT=development docker compose up -d --build && sleep 120 && docker compose ps` → `api`/`worker` healthy, `scheduler` running/healthy; depois `docker compose down -v`
- [ ] Run: `grep -rn -i "celery\|kombu\|poetry" app/ docker-compose.yml pyproject.toml` → sem ocorrências ativas
- [ ] Confirmar `redis-py` 8.x: `uv run python -c "import redis; print(redis.__version__)"`

---

## Notas de verificação da API do TaskIQ

Alguns detalhes dependem da versão exata instalada (`taskiq==0.12.x`, `taskiq-redis==1.2.x`) e devem ser confirmados no ambiente durante a execução, ajustando o código do plano se a API divergir:

1. **`.with_serializer(ORJSONSerializer())`** — confirmar o caminho de import (`taskiq.serializers`); garantir que o serializer default **não** é pickle (objetivo de segurança).
2. **Schedule por label** — confirmar `@broker.task(schedule=[{"interval": <segundos|timedelta>}])`; `interval` aceita `int`/`timedelta`. Se exigir `timedelta`, usar `timedelta(seconds=settings.taskiq_heartbeat_seconds)`.
3. **`ping.labels` / `ping.task_name`** — confirmar como inspecionar o schedule registrado (Task 4.9 Step 4).
4. **Worker embutido nos testes de integração** — confirmar `Receiver`/`broker.listen()`/`broker.ack()` da versão instalada (Task 4.10 nota).
5. **`--ack-type when_executed`, `--workers`, `--max-async-tasks`** — confirmar os nomes das flags via `uv run taskiq worker --help`.
6. **`task.wait_result(timeout=...)`** — confirmar a assinatura do result backend Redis.
