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

### Riscos residuais e roadmap de segurança

- **Rate-limit apenas por IP**: mitiga rajadas simples, mas é contornável/impreciso
  sob **rotação de IP / botnet** e penaliza usuários atrás de **NAT compartilhado**
  (mesmo IP). Quando houver autenticação, adicionar limite **por conta** e
  **backoff exponencial** em falhas de login (anti credential-stuffing).
- **`Cache-Control: no-store`** deve ser aplicado nos endpoints sensíveis quando
  existirem (dados de usuário, tokens), evitando cache por intermediários.
- **CI/SCA**: o pipeline (`.github/workflows/ci.yml`) roda `ruff`, `pytest` e
  `pip-audit` (CVEs em dependências) em cada push/PR.

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
poetry run pytest
```

## Lint / formatação

```bash
poetry run ruff check .
poetry run ruff format .
```

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
