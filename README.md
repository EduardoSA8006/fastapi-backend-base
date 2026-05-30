# ClassUp Backend

Backend da aplicação ClassUp construído com **FastAPI**, **SQLAlchemy** e **Alembic**, gerenciado com **Poetry**.

## Requisitos

- Python `>=3.11,<3.15`
- [Poetry](https://python-poetry.org/) `>=2.0`

## Instalação

```bash
poetry install
```

Copie o arquivo de exemplo de variáveis de ambiente:

```bash
cp .env.example .env
```

## Executando a aplicação

```bash
poetry run uvicorn app.main:app --reload
```

A API ficará disponível em `http://127.0.0.1:8000`.

- Documentação interativa (Swagger): `http://127.0.0.1:8000/docs`
- Healthcheck: `http://127.0.0.1:8000/api/v1/health`

## Executando com Docker (API + PostgreSQL)

O `docker-compose.yml` sobe dois serviços: a **API** e o **PostgreSQL 16**.
Na inicialização, o container da API aguarda o banco, aplica as migrações
(`alembic upgrade head`) e então inicia o Uvicorn.

```bash
docker compose up -d --build
```

- API: `http://localhost:8001` (Swagger em `/docs`, healthcheck em `/api/v1/health`)
- PostgreSQL e Redis: **sem porta no host** — acessíveis apenas pela rede
  interna do Docker, exclusivamente através da API (hostnames `db` e `redis`).

> A **API é o único serviço exposto ao host**. Postgres e Redis não publicam
> portas; toda comunicação com eles passa obrigatoriamente pela API. A porta da
> API no host é **8001** por padrão (mapeada para a 8000 do container); para
> trocar, defina `API_PORT` no `.env` ou no ambiente.
>
> Para inspecionar o banco/redis manualmente, use `docker compose exec db ...`
> ou `docker compose exec redis redis-cli` (dentro da rede), não uma conexão
> direta do host.

Variáveis configuráveis (com valores padrão): `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_DB`, `API_PORT`. Veja `.env.example`.

Comandos úteis:

```bash
docker compose logs -f api     # acompanha os logs da API
docker compose exec api bash   # shell dentro do container da API
docker compose down            # para os containers
docker compose down -v         # para e remove o volume do banco
```

## Segurança

A aplicação aplica um conjunto de proteções globais, configuráveis por variáveis
de ambiente (veja `.env.example`):

- **Rate limit** (slowapi): limite global por IP (`RATE_LIMIT_DEFAULT`, padrão
  `100/minute`), com armazenamento no Redis em Docker (`RATE_LIMIT_STORAGE_URI`)
  e respostas `429` com `Retry-After`. Pode ser desligado com
  `RATE_LIMIT_ENABLED=false`. Endpoints específicos podem ter limites próprios
  via `@limiter.limit(...)`.
- **Security headers**: `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`,
  `Permissions-Policy`, `Content-Security-Policy` (com `frame-ancestors 'none'`;
  isento em `/docs`, `/redoc`, `/openapi.json`) e `Strict-Transport-Security`
  (quando `HSTS_ENABLED=true`). Aplicados a todas as respostas, inclusive
  rejeições (`400`/`413`/`429`). Obs.: a CSP tem valor prático maior em páginas
  HTML do que em respostas JSON consumidas por `fetch`.
- **CORS**: origens/métodos/cabeçalhos vindos das settings. A combinação
  `CORS_ALLOW_CREDENTIALS=true` com `CORS_ALLOW_ORIGINS=["*"]` é bloqueada na
  inicialização por ser insegura.
- **Trusted hosts**: bloqueia `Host` headers não permitidos (`400`). **Em
  produção defina `TRUSTED_HOSTS`** com os hosts reais — o padrão `["*"]` desativa
  a validação.
- **Limite de tamanho de corpo**: rejeita corpos acima de `MAX_BODY_SIZE`
  (`413`), contando os bytes reais do stream (fecha o bypass via
  `Transfer-Encoding: chunked`). Premissa: a contagem do stream **efetiva-se
  quando o corpo é consumido** pelo handler; o caso com `Content-Length`
  declarado é coberto imediatamente pelo fast-path (rejeição antes da leitura).
- **Observabilidade**: cada requisição recebe um `X-Request-ID` (reaproveitado
  se enviado pelo cliente) e é logada com método, caminho, status e IP;
  rejeições (`4xx`) saem como WARNING e erros (`5xx`) como ERROR.
- **Fingerprint**: o servidor sobe com `--no-server-header` (não emite
  `Server: uvicorn`).

### IP atrás de proxy

Por padrão o rate-limit usa o IP da conexão. Atrás de proxy(ies) reverso(s)
confiável(is), defina `TRUST_PROXY=true` e `NUM_TRUSTED_PROXIES` com o número de
saltos confiáveis (ex.: LB + nginx = `2`). O IP do cliente é extraído contando
esses saltos a partir da **direita** de `X-Forwarded-For` (as entradas que os
proxies confiáveis acrescentaram), evitando spoofing.

> `TRUST_PROXY=true` só é seguro se houver de fato `NUM_TRUSTED_PROXIES` proxies
> reescrevendo o header à frente. Habilitá-lo sem isso permite que o cliente
> controle o valor e burle o rate-limit.

### Produção (ENVIRONMENT=production)

Defina `ENVIRONMENT=production`. Nesse modo a aplicação **falha ao iniciar** se
houver configuração insegura, forçando o operador a corrigir:

- `TRUSTED_HOSTS` contém `"*"` → erro (defina os hosts reais).
- `RATE_LIMIT_STORAGE_URI` é `memory://` → erro (use `redis://...`; em múltiplos
  workers/réplicas o `memory://` torna o limite inefetivo).
- `DEBUG=true` → erro (evita vazar stack traces).

Além disso, `/docs`, `/redoc` e `/openapi.json` ficam **desligados** em produção
(não expõem a superfície da API). Para deploys com múltiplas réplicas, defina
`RUN_MIGRATIONS_ON_START=false` e rode `alembic upgrade head` como etapa separada
de deploy (evita corrida entre containers).

### TLS

A aplicação não termina TLS. No deploy de produção:

- Faça a **terminação TLS no proxy reverso** (nginx/traefik/ALB) e redirecione
  `HTTP → HTTPS`.
- Com HTTPS ativo, defina `HSTS_ENABLED=true`.
- Para Postgres externo/gerenciado, exija TLS na conexão:
  `DATABASE_URL=postgresql+psycopg://.../classup?sslmode=require`.

### Defesa em profundidade no proxy/borda

O limite de tamanho de corpo da aplicação é, por natureza, **tardio**: a contagem
real de bytes só dispara quando o handler **consome** o corpo. Uma requisição
`Transfer-Encoding: chunked` para um endpoint que não lê o corpo não é contada
pela app (o fast-path por `Content-Length` cobre apenas o caso declarado).
Portanto, o limite **precisa existir também na borda**:

- nginx: `client_max_body_size 1m;` · traefik/ALB: limite equivalente.
- Timeouts e limites de conexão no proxy (anti slowloris), além dos do uvicorn
  (`--limit-concurrency`, `--timeout-keep-alive`).

### Riscos residuais e roadmap de segurança

- **Rate-limit apenas por IP**: mitiga rajadas simples, mas é contornável/impreciso
  sob **rotação de IP / botnet** e penaliza usuários atrás de **NAT compartilhado**
  (mesmo IP). Quando houver autenticação, adicionar limite **por conta** e
  **backoff exponencial** em falhas de login (anti credential-stuffing).
- **Rate-limit fail-open**: se o Redis ficar indisponível, o `slowapi`/`limits`
  tende a **liberar** as requisições (fail-open) — derrubar o Redis equivale a
  desligar o rate-limit. **Monitore** a disponibilidade do store; para endpoints
  sensíveis (login, futuramente) avalie um comportamento **fail-closed**.
- **`NUM_TRUSTED_PROXIES` incorreto**: se o valor for **maior** que o número real
  de proxies (ou `TRUST_PROXY=true` sem proxy reescrevendo o header), o IP
  extraído cai numa entrada **controlável pelo cliente** → spoofing e bypass do
  rate-limit. O fallback do app só cobre o caso de entradas _de menos_; o número
  correto de saltos é responsabilidade do operador. Configure com cuidado.
- **`Cache-Control: no-store`** deve ser aplicado nos endpoints sensíveis quando
  existirem (dados de usuário, tokens), evitando cache por intermediários.
- **Privacidade dos logs (LGPD/GDPR)**: o log de acesso registra o IP do cliente
  (dado pessoal). Defina **retenção** e **base legal** para esses logs; use
  `LOG_CLIENT_IP=false` para não registrar o IP. O caminho logado **não inclui
  query string**, mas evite **tokens em path** (ex.: `/reset/<token>`) — eles
  apareceriam no log; prefira tokens no corpo/header.
- **Validação de env (typos)**: `Settings` usa `extra="ignore"` porque o `.env` é
  **compartilhado** com o docker-compose/entrypoint (`POSTGRES_*`,
  `REDIS_PASSWORD`, `RUN_MIGRATIONS_ON_START`), e `extra="forbid"` quebraria
  `cp .env.example .env && uvicorn`. Mitigação: as settings críticas têm guards
  de produção ou caem em defaults **seguros** (ex.: `CORS_ALLOW_ORIGINS` errado →
  `[]` bloqueia tudo). Para adotar `extra="forbid"` (falhar em typos), separe o
  `.env` da app das variáveis de infraestrutura.
- **Swagger UI via CDN (apenas dev/staging)**: quando os docs estão ligados, o
  Swagger carrega assets de CDN (e a CSP é isenta nesses paths) — se o CDN for
  comprometido, há risco de XSS na página de docs. Em **produção os docs estão
  desligados**, então não há exposição; em dev/staging, considere servir os
  assets localmente ou aplicar SRI se quiser fechar isso.
- **Credenciais**: em produção a aplicação recusa o boot com senha de banco
  default/fraca; o Redis sobe com `--requirepass`. Use segredos fortes
  (`POSTGRES_PASSWORD`, `REDIS_PASSWORD`) — nunca os defaults de desenvolvimento.
- **API privada do slowapi**: o handler de `429` usa `Limiter._inject_headers`
  (método privado) para reproduzir os cabeçalhos `Retry-After`/`X-RateLimit-*`.
  Está protegido por `try/except` (degrada para um `429` limpo) e o `slowapi`
  está pinado em `<0.2.0` — **reavaliar a cada bump de versão**.
- **CI/segurança**: o pipeline (`.github/workflows/ci.yml`) roda `ruff` (lint +
  SAST via regras `S`/bandit), `ruff format`, `mypy --strict`, `pytest`
  (cobertura ≥90%), `pip-audit` (CVEs em deps), **gitleaks** (secret scanning) e
  **trivy** (CVEs da imagem Docker) em cada push/PR.

## Migrações de banco de dados (Alembic)

Gerar uma nova migração a partir dos modelos:

```bash
poetry run alembic revision --autogenerate -m "descrição da mudança"
```

Aplicar as migrações:

```bash
poetry run alembic upgrade head
```

## Testes

```bash
poetry run pytest        # roda os testes com cobertura (gate mínimo de 90%)
```

A cobertura de `app/` é medida por `pytest-cov` e o build falha abaixo de 90%
(`--cov-fail-under=90`, configurado em `pyproject.toml`).

## Qualidade de código

```bash
poetry run ruff check .          # lint (regras fortes: E,F,I,UP,B,S,SIM,PT,...)
poetry run ruff format .         # formatação
poetry run mypy .                # checagem de tipos (--strict)
poetry run pip-audit             # auditoria de CVEs nas dependências
```

Todos esses passos rodam no CI (`.github/workflows/ci.yml`) em cada push/PR.

## Estrutura do projeto

```
app/
├── main.py            # Criação da app FastAPI
├── core/
│   ├── config.py      # Configurações (pydantic-settings)
│   └── database.py    # Engine, sessão e Base do SQLAlchemy
├── models/            # Modelos ORM
├── schemas/           # Schemas Pydantic
└── api/
    ├── router.py      # Roteador agregador
    └── routes/        # Endpoints
alembic/               # Migrações de banco
tests/                 # Testes
```
