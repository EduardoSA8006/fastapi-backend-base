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
- PostgreSQL: exposto em `localhost:5432`

> A porta da API no host é **8001** por padrão (mapeada para a 8000 do
> container). Para usar outra porta, defina `API_PORT` no `.env` ou no ambiente.

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
  `Referrer-Policy`, `Cross-Origin-Opener-Policy`, `Content-Security-Policy`
  (isento em `/docs`, `/redoc`, `/openapi.json`) e `Strict-Transport-Security`
  (quando `HSTS_ENABLED=true`). Aplicados a todas as respostas, inclusive
  rejeições (`400`/`413`/`429`).
- **CORS**: origens/métodos/cabeçalhos vindos das settings. A combinação
  `CORS_ALLOW_CREDENTIALS=true` com `CORS_ALLOW_ORIGINS=["*"]` é bloqueada na
  inicialização por ser insegura.
- **Trusted hosts**: bloqueia `Host` headers não permitidos (`400`). **Em
  produção defina `TRUSTED_HOSTS`** com os hosts reais — o padrão `["*"]` desativa
  a validação.
- **Limite de tamanho de corpo**: rejeita corpos acima de `MAX_BODY_SIZE`
  (`413`), contando os bytes reais do stream (fecha o bypass via
  `Transfer-Encoding: chunked`).

### IP atrás de proxy

Por padrão o rate-limit usa o IP da conexão. Atrás de um proxy reverso confiável,
defina `TRUST_PROXY=true`: o IP do cliente será o **mais à direita** de
`X-Forwarded-For` (o que o proxy confiável acrescentou), evitando spoofing. A
configuração assume **um único** proxy confiável à frente.

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
