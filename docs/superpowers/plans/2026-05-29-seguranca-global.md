# Camada de Segurança Global — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar rate-limit global (Redis) e proteções de borda (security headers, CORS, trusted hosts, limite de body) aplicadas a toda a API FastAPI.

**Architecture:** Cada proteção é um middleware isolado e testável. O rate-limit usa `slowapi` com storage configurável (Redis no Docker, `memory://` local/testes). O `create_app()` faz o *wiring* e aceita um `Settings` opcional para permitir testes com configuração customizada. A ordem dos middlewares garante que rejeições baratas ocorram na borda.

**Tech Stack:** FastAPI, Starlette, slowapi, redis, pydantic-settings, pytest, httpx (TestClient).

---

## Estrutura de arquivos

- `app/core/config.py` — **modificar**: novos campos de segurança em `Settings`.
- `app/core/limiter.py` — **criar**: `build_key_func`, `create_limiter`, `rate_limit_exceeded_handler`.
- `app/middleware/__init__.py` — **criar**: pacote.
- `app/middleware/security_headers.py` — **criar**: `SecurityHeadersMiddleware`.
- `app/middleware/body_size_limit.py` — **criar**: `BodySizeLimitMiddleware`.
- `app/main.py` — **modificar**: `create_app(settings=None)` faz o wiring de tudo.
- `tests/test_security_headers.py` — **criar**.
- `tests/test_body_size_limit.py` — **criar**.
- `tests/test_limiter.py` — **criar**.
- `tests/test_security_integration.py` — **criar**.
- `docker-compose.yml` — **modificar**: serviço Redis.
- `.env.example` / `README.md` — **modificar**.

---

## Task 1: Adicionar dependências

**Files:**
- Modify: `pyproject.toml` (via poetry)

- [ ] **Step 1: Adicionar slowapi e redis**

Run:
```bash
poetry add slowapi redis
```
Expected: instala `slowapi` e `redis`, atualiza `poetry.lock`.

- [ ] **Step 2: Verificar import**

Run:
```bash
poetry run python -c "import slowapi, redis; print('ok')"
```
Expected: imprime `ok`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml poetry.lock
git commit -m "build: adiciona slowapi e redis para rate-limit"
```

---

## Task 2: Estender Settings com campos de segurança

**Files:**
- Modify: `app/core/config.py`

- [ ] **Step 1: Adicionar os campos na classe `Settings`**

Adicionar, dentro da classe `Settings` (após `database_url`):

```python
    # Rate limit
    rate_limit_enabled: bool = True
    rate_limit_default: str = "100/minute"
    rate_limit_storage_uri: str = "memory://"

    # CORS
    cors_allow_origins: list[str] = []
    cors_allow_credentials: bool = False
    cors_allow_methods: list[str] = ["*"]
    cors_allow_headers: list[str] = ["*"]

    # Trusted hosts
    trusted_hosts: list[str] = ["*"]

    # Body size (bytes)
    max_body_size: int = 1_048_576  # 1 MB

    # Security headers / proxy
    hsts_enabled: bool = False
    trust_proxy: bool = False
```

- [ ] **Step 2: Verificar que instancia sem erro**

Run:
```bash
poetry run python -c "from app.core.config import Settings; s=Settings(); print(s.rate_limit_default, s.max_body_size, s.trusted_hosts)"
```
Expected: `100/minute 1048576 ['*']`

- [ ] **Step 3: Commit**

```bash
git add app/core/config.py
git commit -m "feat: adiciona campos de seguranca em Settings"
```

---

## Task 3: SecurityHeadersMiddleware

**Files:**
- Create: `app/middleware/__init__.py`
- Create: `app/middleware/security_headers.py`
- Test: `tests/test_security_headers.py`

- [ ] **Step 1: Criar o pacote middleware**

Criar `app/middleware/__init__.py` vazio.

- [ ] **Step 2: Escrever o teste que falha**

Criar `tests/test_security_headers.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.security_headers import SecurityHeadersMiddleware


def _build_app(hsts_enabled: bool = False) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=hsts_enabled)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ping": "pong"}

    return app


def test_security_headers_present() -> None:
    client = TestClient(_build_app())
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"


def test_hsts_disabled_by_default() -> None:
    client = TestClient(_build_app(hsts_enabled=False))
    response = client.get("/ping")
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_enabled() -> None:
    client = TestClient(_build_app(hsts_enabled=True))
    response = client.get("/ping")
    assert "Strict-Transport-Security" in response.headers
```

- [ ] **Step 3: Rodar o teste e confirmar a falha**

Run: `poetry run pytest tests/test_security_headers.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.middleware.security_headers'`

- [ ] **Step 4: Implementar o middleware**

Criar `app/middleware/security_headers.py`:

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Injeta cabeçalhos de segurança em todas as respostas."""

    def __init__(self, app, hsts_enabled: bool = False) -> None:
        super().__init__(app)
        self.hsts_enabled = hsts_enabled

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        if self.hsts_enabled:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains"
            )
        return response
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `poetry run pytest tests/test_security_headers.py -v`
Expected: PASS (3 testes)

- [ ] **Step 6: Commit**

```bash
git add app/middleware/__init__.py app/middleware/security_headers.py tests/test_security_headers.py
git commit -m "feat: adiciona SecurityHeadersMiddleware"
```

---

## Task 4: BodySizeLimitMiddleware

**Files:**
- Create: `app/middleware/body_size_limit.py`
- Test: `tests/test_body_size_limit.py`

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_body_size_limit.py`:

```python
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.body_size_limit import BodySizeLimitMiddleware


def _build_app(max_body_size: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, max_body_size=max_body_size)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        body = await request.body()
        return {"size": len(body)}

    return app


def test_body_within_limit_passes() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=b"x" * 50)
    assert response.status_code == 200
    assert response.json() == {"size": 50}


def test_body_over_limit_rejected() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=b"x" * 200)
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_invalid_content_length_rejected() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post(
        "/echo", content=b"x", headers={"Content-Length": "abc"}
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Rodar o teste e confirmar a falha**

Run: `poetry run pytest tests/test_body_size_limit.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.middleware.body_size_limit'`

- [ ] **Step 3: Implementar o middleware**

Criar `app/middleware/body_size_limit.py`:

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejeita requisições cujo corpo excede o limite, via Content-Length."""

    def __init__(self, app, max_body_size: int) -> None:
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                return JSONResponse(
                    {"detail": "Invalid Content-Length"}, status_code=400
                )
            if declared > self.max_body_size:
                return JSONResponse(
                    {"detail": "Request body too large"}, status_code=413
                )
        return await call_next(request)
```

> Nota: a checagem usa `Content-Length`, que cobre o caso comum. Corpos sem
> `Content-Length` (chunked) passam por aqui e ficam sujeitos aos limites do
> servidor ASGI. Endurecer o caso chunked está fora de escopo (YAGNI).

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `poetry run pytest tests/test_body_size_limit.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Commit**

```bash
git add app/middleware/body_size_limit.py tests/test_body_size_limit.py
git commit -m "feat: adiciona BodySizeLimitMiddleware"
```

---

## Task 5: Configuração do rate-limit (limiter.py)

**Files:**
- Create: `app/core/limiter.py`
- Test: `tests/test_limiter.py`

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_limiter.py`:

```python
from app.core.config import Settings
from app.core.limiter import build_key_func


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    def __init__(self, host: str, forwarded_for: str | None = None) -> None:
        self.client = _FakeClient(host)
        self.headers = {}
        if forwarded_for is not None:
            self.headers["X-Forwarded-For"] = forwarded_for


def test_key_func_uses_client_host_when_proxy_untrusted() -> None:
    settings = Settings(trust_proxy=False)
    key_func = build_key_func(settings)
    request = _FakeRequest(host="10.0.0.1", forwarded_for="1.2.3.4")
    assert key_func(request) == "10.0.0.1"


def test_key_func_uses_forwarded_for_when_proxy_trusted() -> None:
    settings = Settings(trust_proxy=True)
    key_func = build_key_func(settings)
    request = _FakeRequest(host="10.0.0.1", forwarded_for="1.2.3.4, 5.6.7.8")
    assert key_func(request) == "1.2.3.4"


def test_key_func_falls_back_to_client_host_without_forwarded_for() -> None:
    settings = Settings(trust_proxy=True)
    key_func = build_key_func(settings)
    request = _FakeRequest(host="10.0.0.1")
    assert key_func(request) == "10.0.0.1"
```

- [ ] **Step 2: Rodar o teste e confirmar a falha**

Run: `poetry run pytest tests/test_limiter.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.core.limiter'`

- [ ] **Step 3: Implementar o limiter**

Criar `app/core/limiter.py`:

```python
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings


def build_key_func(settings: Settings):
    """Cria a função de chave do rate-limit, respeitando a confiança em proxy."""

    def key_func(request: Request) -> str:
        if settings.trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                # IP mais à direita: o que o proxy confiável acrescentou.
                # O mais à esquerda é forjável pelo cliente — NÃO usar.
                client_ip = forwarded.split(",")[-1].strip()
                if client_ip:
                    return client_ip
        return get_remote_address(request)

    return key_func


def create_limiter(settings: Settings) -> Limiter:
    """Instancia o Limiter do slowapi a partir das configurações."""
    return Limiter(
        key_func=build_key_func(settings),
        default_limits=[settings.rate_limit_default],
        storage_uri=settings.rate_limit_storage_uri,
        enabled=settings.rate_limit_enabled,
    )


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> Response:
    """Resposta JSON consistente para o erro 429, com Retry-After."""
    limit_data = getattr(request.state, "view_rate_limit", None)
    headers: dict[str, str] = {}
    if limit_data is not None:
        tmp = JSONResponse(content={}, status_code=429)
        tmp = request.app.state.limiter._inject_headers(tmp, limit_data)
        for key in (
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ):
            if key in tmp.headers:
                headers[key] = tmp.headers[key]

    body: dict[str, object] = {"detail": "Rate limit exceeded"}
    if "Retry-After" in headers:
        body["retry_after"] = int(headers["Retry-After"])

    return JSONResponse(content=body, status_code=429, headers=headers)
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `poetry run pytest tests/test_limiter.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Commit**

```bash
git add app/core/limiter.py tests/test_limiter.py
git commit -m "feat: adiciona configuracao do rate-limit (slowapi)"
```

---

## Task 6: Wiring em create_app + testes de integração

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_security_integration.py`

- [ ] **Step 1: Escrever os testes de integração que falham**

Criar `tests/test_security_integration.py`:

```python
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _client(**overrides) -> TestClient:
    base = dict(
        rate_limit_storage_uri="memory://",
        trusted_hosts=["testserver"],
    )
    base.update(overrides)
    return TestClient(create_app(Settings(**base)))


def test_security_headers_on_real_app() -> None:
    client = _client()
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_rate_limit_returns_429_when_exceeded() -> None:
    client = _client(rate_limit_default="3/minute")
    codes = [client.get("/api/v1/health").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    last = client.get("/api/v1/health")
    assert last.status_code == 429
    assert last.json()["detail"] == "Rate limit exceeded"


def test_rate_limit_disabled_allows_all() -> None:
    client = _client(rate_limit_enabled=False, rate_limit_default="1/minute")
    codes = [client.get("/api/v1/health").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 200, 200]


def test_body_size_limit_rejects_large_payload() -> None:
    client = _client(max_body_size=10)
    response = client.post("/api/v1/health", content=b"x" * 50)
    # rota não aceita POST, mas o middleware de body roda antes -> 413
    assert response.status_code == 413


def test_trusted_host_rejects_unknown_host() -> None:
    client = _client(trusted_hosts=["example.com"])
    response = client.get("/api/v1/health")
    assert response.status_code == 400


def test_cors_preflight_allows_configured_origin() -> None:
    client = _client(cors_allow_origins=["http://allowed.test"])
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://allowed.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://allowed.test"
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `poetry run pytest tests/test_security_integration.py -v`
Expected: FAIL (create_app não aceita `settings`; middlewares ausentes)

- [ ] **Step 3: Reescrever `app/main.py` com o wiring completo**

Substituir o conteúdo de `app/main.py` por:

```python
from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.limiter import create_limiter, rate_limit_exceeded_handler
from app.middleware.body_size_limit import BodySizeLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Cria e configura a instância da aplicação FastAPI."""
    settings = settings or get_settings()

    app = FastAPI(title=settings.app_name, debug=settings.debug)

    # Rate-limit (slowapi): estado + handler + middleware.
    if settings.rate_limit_enabled:
        limiter = create_limiter(settings)
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # A ORDEM importa: o último adicionado é o mais externo (executa primeiro).
    # Adiciona-se na ordem inversa da execução desejada.
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=settings.hsts_enabled)

    if settings.rate_limit_enabled:
        app.add_middleware(SlowAPIMiddleware)

    app.add_middleware(
        BodySizeLimitMiddleware, max_body_size=settings.max_body_size
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()


@app.get("/")
def root() -> dict[str, str]:
    """Rota raiz com informações básicas da API."""
    settings = get_settings()
    return {"app": settings.app_name, "docs": "/docs"}
```

- [ ] **Step 4: Rodar a suíte de integração e confirmar que passa**

Run: `poetry run pytest tests/test_security_integration.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Rodar a suíte completa**

Run: `poetry run pytest -q && poetry run ruff check .`
Expected: todos os testes passam; ruff sem erros.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_security_integration.py
git commit -m "feat: aplica protecoes de seguranca globais no create_app"
```

---

## Task 7: Infra Docker (Redis) e documentação

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Adicionar o serviço Redis ao `docker-compose.yml`**

Adicionar ao bloco `services:` (antes de `api:`):

```yaml
  redis:
    image: redis:7-alpine
    container_name: classup-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
```

- [ ] **Step 2: Ligar a API ao Redis**

No serviço `api`, adicionar `redis` ao `depends_on` e a variável de ambiente:

```yaml
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      # ... variáveis existentes ...
      RATE_LIMIT_STORAGE_URI: redis://redis:6379/0
```

- [ ] **Step 3: Atualizar `.env.example`**

Adicionar ao final:

```env
# Rate limit
RATE_LIMIT_ENABLED=true
RATE_LIMIT_DEFAULT="100/minute"
# Local sem Docker use memory://; no Docker, o compose injeta redis://redis:6379/0
RATE_LIMIT_STORAGE_URI="memory://"

# CORS (formato JSON)
CORS_ALLOW_ORIGINS=[]
CORS_ALLOW_CREDENTIALS=false

# Trusted hosts (formato JSON)
TRUSTED_HOSTS=["*"]

# Limite de corpo da requisição (bytes)
MAX_BODY_SIZE=1048576

# Headers / proxy
HSTS_ENABLED=false
TRUST_PROXY=false
```

- [ ] **Step 4: Subir o stack e validar o rate-limit com Redis**

Run:
```bash
docker compose up -d --build
sleep 5
docker compose exec -T redis redis-cli ping
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/api/v1/health
```
Expected: `PONG` e `200`.

- [ ] **Step 5: Atualizar o README**

Acrescentar uma seção "Segurança" no `README.md` listando: rate-limit (Redis),
security headers, CORS, trusted hosts e limite de body, com as variáveis de
ambiente relevantes.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example README.md
git commit -m "feat: adiciona Redis ao compose e documenta seguranca"
```

---

## Self-review (preenchido pelo autor do plano)

- **Cobertura da spec:** rate-limit global (Task 5+6), override por rota
  (mecanismo via `limiter` exportado — uso documentado na spec, aplicado quando
  surgirem rotas), security headers (Task 3), CORS (Task 6), trusted hosts
  (Task 6), body size (Task 4), Redis no Docker (Task 7), `trust_proxy`/IP
  (Task 5), toggle `rate_limit_enabled` (Task 6), respostas JSON 429/413/400
  (Tasks 4/5/6). ✅
- **Placeholders:** nenhum — todo passo tem código/comando concreto. ✅
- **Consistência de tipos:** `create_limiter`/`build_key_func`/
  `rate_limit_exceeded_handler` usados em `main.py` batem com `limiter.py`;
  `Settings(**overrides)` usa os campos definidos na Task 2. ✅
```
