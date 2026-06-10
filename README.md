# FastAPI Backend Base

[![CI](https://github.com/EduardoSA8006/fastapi-backend-base/actions/workflows/ci.yml/badge.svg)](https://github.com/EduardoSA8006/fastapi-backend-base/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[🇺🇸 English version](README.en.md)

Template de backend **FastAPI** com segurança fail-closed por padrão, arquitetura
feature-first e três níveis de teste prontos — pensado para iniciar projetos
novos já com a fundação que normalmente leva semanas para endurecer.

> Os nomes usam o placeholder **`myapp`/`MyApp`**: ao criar seu projeto,
> faça um find/replace global pelos nomes reais e está pronto.

## O que vem na base

- **Arquitetura feature-first + core/shared (MVVM semântico)** — contrato de
  feature documentado (`router`/`service`/`repository`/`models`/`schemas`/
  `exceptions`/`tasks`), regras de dependência claras (feature nunca importa
  feature) e composition root enxuto: toda a política de segurança vive em
  `core/security_guards.py`, testável isoladamente.
- **Guards fail-closed de produção** — em `ENVIRONMENT=production` o boot é
  RECUSADO com: hosts curinga, rate-limit em memória, `DEBUG=true`, senha
  fraca/default em banco/Redis/MinIO/Celery, usuário MinIO previsível ou
  broker do Celery na mesma instância do Redis do rate-limit. `ENVIRONMENT`
  é obrigatório (sem default — typo no nome da variável não cai em modo dev).
- **Camada de erros completa** — hierarquia `AppException` + handler global,
  `ErrorBoundary` ASGI (500 padronizado/blindado, nunca text/plain), access
  log JSON estruturado com `X-Request-ID` correlacionado até no crash.
- **Infra Docker segura por padrão** — Postgres, Redis (rate-limit),
  Redis dedicado do Celery (comandos destrutivos desativados), MinIO e
  worker/beat Celery **sem nenhuma porta publicada**; 3 redes segmentadas
  (worker não tem rota até o Redis do rate-limit); API só em loopback;
  containers endurecidos (read-only, cap_drop ALL, limites).
- **Celery endurecido** — serialização json-only nas três superfícies,
  `acks_late`, prefetch 1, time limits; task `core.ping` como heartbeat do
  beat; storage MinIO com facade async (`shared/storage.py`), bucket lazy e
  erros tipados.
- **Três níveis de teste prontos** (+ ferramentas de profundidade):

| Nível | Comando | O que cobre |
|---|---|---|
| Unitário | `poetry run pytest` | 181 testes, gate de cobertura 90%, property-based (Hypothesis) nos pontos críticos |
| Integração | `poetry run pytest -m integration --no-cov` | Postgres/Redis/MinIO/broker REAIS (testcontainers), migrações, cenários de falha e recuperação |
| E2E | `poetry run pytest -m e2e --no-cov` | stack compose completo via httpx, modo dev E production, invariante de isolamento de rede |
| Mutation | `poetry run mutmut run` | métrica local de força das asserções (config pronta) |

- **CI completo** (GitHub Actions, SHA-pinned, Node 24): lint (ruff+bandit),
  mypy `--strict`, os 3 níveis de teste, pip-audit, gitleaks e trivy.

## Como usar este template

1. **Use this template** no GitHub (ou clone) e renomeie:

```bash
# dentro do projeto novo
grep -rl "myapp" --exclude-dir=.git . | xargs sed -i 's/myapp/seuprojeto/g; s/MyApp/SeuProjeto/g'
```

2. Configure e rode:

```bash
cp .env.example .env   # ENVIRONMENT é obrigatório — o exemplo já traz development
poetry install
poetry run pytest      # 181 verdes antes de qualquer linha sua
```

3. Suba o stack completo (API + Postgres + Redis ×2 + MinIO + Celery):

```bash
docker compose up -d --build
# API: http://127.0.0.1:8001 (Swagger em /docs; /api/v1/health e /api/v1/ready)
```

4. Crie sua primeira feature em `app/features/<nome>/` seguindo o contrato
   (ver "Estrutura do projeto" abaixo) — models entram no `alembic/env.py`,
   tasks no `include` de `app/worker.py`.

## Requisitos

- Python `>=3.12,<3.15` · [Poetry](https://python-poetry.org/) `>=2.0`
- Docker + Docker Compose (stack local, integração e e2e)

## Serviços Docker

O `docker-compose.yml` sobe 7 serviços segmentados em 3 redes internas:

| Serviço | Imagem | Porta publicada | Redes | Propósito |
|---|---|---|---|---|
| `api` | build local | `127.0.0.1:8001` | data, ratelimit, celery | FastAPI + Uvicorn |
| `worker` | build local | — | data, celery | Celery worker (2 concorrências) |
| `beat` | build local | — | data, celery | Celery beat — scheduler periódico |
| `db` | `postgres:16-alpine` | — | data | PostgreSQL (dados persistentes) |
| `redis` | `redis:7-alpine` | — | ratelimit | Store do rate-limit (exclusivo) |
| `redis-celery` | `redis:7-alpine` | — | celery | Broker + result backend do Celery |
| `minio` | `quay.io/minio/minio` | — | data | Object storage S3-compatible |

**Redes internas** (isolamento least-privilege):

| Rede | Membros | Propósito |
|---|---|---|
| `data_net` | api, worker, beat, db, minio | Acesso ao banco e storage |
| `ratelimit_net` | api, redis | Exclusiva do rate-limit — worker/beat sem rota até essas chaves |
| `celery_net` | api, worker, beat, redis-celery | Broker e result backend |

Nenhum serviço além da `api` expõe porta ao host. A `api` liga em loopback (`127.0.0.1`) por padrão — ajuste `API_BIND` para expor em outras interfaces conscientemente.

---

A documentação abaixo cobre em profundidade as decisões de segurança e
operação da base — vale a leitura antes do primeiro deploy.

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
- Senha **fraca/default** (ou ausente) na `DATABASE_URL` → erro (use senha forte).
- Senha **fraca/default** (ou ausente) na `RATE_LIMIT_STORAGE_URI` do Redis →
  erro (paridade com o banco; use `redis://:SENHA@host:porta/db`).

Avisos (não falham o boot, mas exigem atenção): `TRUST_PROXY=false` com proxy à
frente (rate-limit colapsa) e `TRUST_PROXY=true` sem proxy real (XFF spoofável).

Além disso, `/docs`, `/redoc` e `/openapi.json` ficam **desligados** em produção
(não expõem a superfície da API). Para deploys com múltiplas réplicas, defina
`RUN_MIGRATIONS_ON_START=false` e rode `alembic upgrade head` como etapa separada
de deploy (evita corrida entre containers).

> **Healthcheck do container**: o probe do Docker envia `Host: 127.0.0.1` por
> padrão, que o `TrustedHostMiddleware` rejeitaria sob `TRUSTED_HOSTS` restrito
> (→ 400 → restart loop). Defina `HEALTHCHECK_HOST` com um host permitido (ex.:
> `HEALTHCHECK_HOST=api.myapp.com`). Não afrouxamos o `TrustedHost` para o
> loopback de propósito — o probe carrega o `Host` correto.

### TLS

A aplicação não termina TLS. No deploy de produção:

- Faça a **terminação TLS no proxy reverso** (nginx/traefik/ALB) e redirecione
  `HTTP → HTTPS`.
- Com HTTPS ativo, defina `HSTS_ENABLED=true`.
- Para Postgres externo/gerenciado, exija TLS na conexão:
  `DATABASE_URL=postgresql+psycopg://.../myapp?sslmode=require`.

### Defesa em profundidade no proxy/borda

O limite de **tamanho** de corpo é autoritativo na própria aplicação,
independentemente de o handler consumir o corpo: com `Content-Length` há um
fast-path pelo header; sem ele (`Transfer-Encoding: chunked`) o corpo é drenado
proativamente até o limite antes de chegar ao handler — fechando o bypass em que
um endpoint que não lê o corpo (ex.: `/health`) nunca dispararia a contagem.

> **Invariante de memória**: o caminho chunked bufferiza o corpo em memória (até
> `MAX_BODY_SIZE`) antes de reentregá-lo ao handler. O pico é ~`MAX_BODY_SIZE` ×
> requisições chunked concorrentes — um atacante pode forçá-lo deliberadamente.
> Mantenha **`MAX_BODY_SIZE × --limit-concurrency` confortavelmente abaixo do
> `mem_limit`** do container (defaults: 1 MB × 100 = ~100 MB < 512m), senão
> elevar `MAX_BODY_SIZE` (ex.: para uploads) leva a **OOM kill**. Para uploads
> grandes, prefira **streaming direto ao storage**, não bufferização no middleware.

O que **continua sendo responsabilidade da borda/uvicorn** (fora do escopo de um
limite de tamanho):

- **Slowloris / slow-POST** (corpo ou headers enviados byte a byte): **o uvicorn
  NÃO cobre** este caso. `--timeout-keep-alive` só fecha conexões keep-alive
  **ociosas** — uma conexão que envia 1 byte a cada poucos segundos continua
  "ativa". Com `--limit-concurrency 100`, ~100 conexões lentas esgotam a
  capacidade sem nunca completar o corpo (logo, sem tocar o rate-limit). Mitigar
  **exige reverse proxy** com timeouts de leitura: nginx `client_body_timeout` /
  `client_header_timeout` / `send_timeout` (ou equivalentes em ALB/Cloudflare).
  **É requisito de produção.**
- **Acesso direto à app (bypass do proxy)**: não publique a porta da app em todas
  as interfaces. Com `TRUST_PROXY=true`, alcançar a app direto (`:8001`) permite
  forjar `X-Forwarded-For` e furar o rate-limit por IP. O `docker-compose` faz
  bind em **loopback** por padrão (`API_BIND=127.0.0.1`); em produção, prefira
  não publicar a porta e expor só o proxy (app na rede interna via hostname `api`).
- **Redundância em profundidade**: mantenha também `client_max_body_size 1m;`
  (nginx) ou equivalente, além de `--limit-concurrency` no uvicorn.
- **Rejeições antes do rate-limit**: por design, validações baratas (Host
  inválido → 400, corpo grande → 413) ficam **fora** do rate-limit (mais
  interno), para não gastar uma operação no Redis com tráfego lixo — caso
  contrário um flood de requisições inválidas viraria DoS contra o próprio
  Redis. O custo é que esse caminho de rejeição não é limitado por IP; como cada
  rejeição é O(1) (sem DB/Redis), a barreira correta é o **limite de conexão/taxa
  na borda/uvicorn** (`--limit-concurrency`), não o rate-limit de aplicação.

### Riscos residuais e roadmap de segurança

- **Rate-limit apenas por IP**: mitiga rajadas simples, mas é contornável/impreciso
  sob **rotação de IP / botnet** e penaliza usuários atrás de **NAT compartilhado**
  (mesmo IP). Quando houver autenticação, adicionar limite **por conta** e
  **backoff exponencial** em falhas de login (anti credential-stuffing).
- **Rate-limit fail-closed quando o Redis cai** (escolha consciente): com o
  default do slowapi (`swallow_errors=False`, sem `in_memory_fallback`), um Redis
  indisponível faz as requisições rate-limitadas retornarem **500** — a API não
  fica desprotegida, mas sua **disponibilidade fica acoplada à do Redis**.
  Timeouts curtos de socket (`socket_timeout`/`socket_connect_timeout=2s`, só no
  Redis) evitam que um Redis lento pendure as requisições. Implicações operacionais:
  **monitore/alarme** a disponibilidade do Redis. Os **probes (`/health` e
  `/ready`) são isentos do rate-limit**, então um Redis fora **não** derruba o
  liveness (sem restart loop) — é o `/ready` que sinaliza o store degradado.
  Alternativas descartadas por ora: fail-open (`swallow_errors`, perde proteção
  contra abuso) e fallback em memória (`in_memory_fallback`, limite por-réplica).
  **Regra operacional**: probes são para o **orquestrador** — NÃO roteie
  `/api/v1/health` e `/api/v1/ready` no proxy público (sem auth + isentos de
  rate-limit, seriam um amplificador de carga contra banco/Redis). Mitigação
  em profundidade: o resultado do `/ready` é **cacheado**
  (`READINESS_CACHE_SECONDS`, default 3s) — rajadas custam no máximo 1
  round-trip de backend por janela. Não damos rate-limit próprio ao probe de
  propósito: com o store fora, o limiter fail-closed trocaria o 503 granular
  (com `checks`) por um 500 opaco.
- **`NUM_TRUSTED_PROXIES` incorreto / `TRUST_PROXY` mal configurado**: se o valor
  for **maior** que o número real de proxies (ou `TRUST_PROXY=true` sem proxy
  reescrevendo o header), o IP extraído cai numa entrada **controlável pelo
  cliente** → spoofing e bypass do rate-limit. O app **não consegue** detectar a
  topologia de rede, então não falha o boot; em produção emite um **warning
  explícito** tanto para `TRUST_PROXY=false` (rate-limit colapsa atrás de proxy)
  quanto para `TRUST_PROXY=true` (XFF spoofável se exposto direto). O número
  correto de saltos é responsabilidade do operador — configure com cuidado.
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
  `cp .env.example .env && uvicorn` — e, de toda forma, `extra="forbid"` NÃO
  pegaria typo no ambiente do SO (o pydantic-settings só consulta nomes
  conhecidos lá). Mitigação: a variável-mestra `ENVIRONMENT` é **obrigatória,
  sem default** (typo no nome = chave ausente = boot recusado, na app E no
  compose via `${ENVIRONMENT:?}`); as demais settings críticas têm guards de
  produção ou caem em defaults **seguros** (ex.: `CORS_ALLOW_ORIGINS` errado →
  `[]` bloqueia tudo).
- **Swagger UI via CDN (apenas dev/staging)**: quando os docs estão ligados, o
  Swagger carrega assets de CDN (e a CSP é isenta nesses paths) — se o CDN for
  comprometido, há risco de XSS na página de docs. Em **produção os docs estão
  desligados**, então não há exposição; em dev/staging, considere servir os
  assets localmente ou aplicar SRI se quiser fechar isso.
- **Segredos via variável de ambiente**: as senhas chegam ao container por env
  (`DATABASE_URL`/`RATE_LIMIT_STORAGE_URI`), visíveis em `docker inspect` e
  `/proc/<pid>/environ`. Aceitável no stack de dev. Em **produção**, prefira
  Docker/Swarm secrets ou um secrets manager montando a senha por **arquivo** —
  o `pydantic-settings` lê de `secrets_dir` (ex.: `/run/secrets`), evitando
  expor a credencial no ambiente do processo.
- **Imagem base fixada por tag, não por digest**: o `Dockerfile` usa
  `python:3.13-slim` (tag mutável). O CI fixa Actions por SHA; faça o mesmo com a
  base do Docker — fixe por `@sha256:<digest>` e atualize via Renovate/Dependabot
  (o procedimento para obter o digest está comentado no `Dockerfile`).
- **`--forwarded-allow-ips` do uvicorn**: deve permanecer no **default restrito**
  (`127.0.0.1`). A fonte da verdade do IP do cliente é o `resolve_client_ip` da
  app (`TRUST_PROXY`/`NUM_TRUSTED_PROXIES`). Habilitar `--forwarded-allow-ips="*"`
  faz o uvicorn reescrever `scope["client"]` com a própria lógica (mais ingênua)
  de XFF, criando um caminho de confiança paralelo e conflitante.
- **Redis em texto puro (`redis://`)**: ok enquanto Redis e API compartilham a
  rede interna do mesmo host. Se o Redis cruzar a fronteira de host (gerenciado,
  outro nó), troque para **`rediss://`** (TLS); o guard de senha de produção já
  cobre `rediss://`.
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

Níveis de teste e métricas extras (Docker necessário p/ integração/e2e):

```bash
poetry run pytest                          # unitários (gate de cobertura 90%)
poetry run pytest -m integration --no-cov  # infra real efêmera (testcontainers)
poetry run pytest -m e2e --no-cov          # stack compose completo + httpx
poetry run mutmut run && poetry run mutmut results  # mutation testing (métrica
                                           # LOCAL, não é gate de CI — ver spec)
```

Todos esses passos rodam no CI (`.github/workflows/ci.yml`) em cada push/PR.

## Estrutura do projeto

Arquitetura **feature-first + core + shared** (MVVM semântico):

```
app/
├── main.py                # Composition root: create_app() + guards de produção
├── worker.py              # Entrypoint Celery (worker e beat)
├── core/                  # Infraestrutura transversal — zero regra de negócio
│   ├── config.py          # Configurações (pydantic-settings)
│   ├── database.py        # Engine, sessão e Base do SQLAlchemy
│   ├── limiter.py         # Rate-limit (slowapi)
│   ├── logging.py         # Logs JSON estruturados
│   ├── client_ip.py       # Resolução do IP real (X-Forwarded-For)
│   ├── security_guards.py # Política de credenciais fracas + guards do Celery
│   └── middleware/        # Middlewares ASGI (headers, body-size, observabilidade)
├── shared/                # Código cross-feature de domínio
│   └── exceptions.py      # Hierarquia AppException + handler global
└── features/              # Uma pasta por feature (MVVM semântico)
    └── health/
        └── router.py      # Probes de liveness/readiness
alembic/                   # Migrações de banco
tests/                     # Espelham a estrutura (core/, shared/, features/)
```

Anatomia de uma feature: `router.py` (View — HTTP puro), `service.py`
(ViewModel — caso de uso), `repository.py` + `models.py` (Model),
`schemas.py` (DTOs), `exceptions.py` (herdam de `shared.exceptions`),
`tasks.py` (Celery). Regras de dependência: features → core/shared;
shared → core; feature nunca importa de outra feature.

## Referência de variáveis de ambiente

Todas as variáveis lidas pelo `Settings` (via `pydantic-settings`). Os defaults abaixo se aplicam quando a variável está ausente — exceto `ENVIRONMENT`, que é obrigatória.

### Aplicação

| Variável | Default | Descrição |
|---|---|---|
| `ENVIRONMENT` | — **obrigatório** | `development` \| `staging` \| `production` |
| `APP_NAME` | `MyApp Backend` | Nome exibido na rota `/` e na documentação |
| `DEBUG` | `false` | Modo debug — proibido em produção |
| `API_V1_PREFIX` | `/api/v1` | Prefixo de todas as rotas da API |

### Banco de dados

| Variável | Default | Descrição |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./myapp.db` | URL de conexão (SQLite em dev; Postgres no Docker) |

> No Docker o compose injeta `postgresql+psycopg://...@db:5432/myapp`.
> Em produção externa, acrescente `?sslmode=require`.

### Rate-limit

| Variável | Default | Descrição |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | Habilita o rate-limit global |
| `RATE_LIMIT_DEFAULT` | `100/minute` | Limite global por IP |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | Store do limiter — **use `redis://` em produção** |

### CORS

| Variável | Default | Descrição |
|---|---|---|
| `CORS_ALLOW_ORIGINS` | `[]` | Lista JSON de origens permitidas |
| `CORS_ALLOW_CREDENTIALS` | `false` | Permite cookies/auth — **nunca com `origins=["*"]`** |
| `CORS_ALLOW_METHODS` | `["*"]` | Métodos HTTP permitidos |
| `CORS_ALLOW_HEADERS` | `["*"]` | Headers permitidos |

### Segurança e proxy

| Variável | Default | Descrição |
|---|---|---|
| `TRUSTED_HOSTS` | `["*"]` | Hosts aceitos no header `Host` — **defina em produção** |
| `MAX_BODY_SIZE` | `1048576` | Limite de corpo em bytes (1 MB) |
| `HSTS_ENABLED` | `false` | Emite `Strict-Transport-Security` — só com HTTPS ativo |
| `TRUST_PROXY` | `false` | Extrai IP real de `X-Forwarded-For` |
| `NUM_TRUSTED_PROXIES` | `1` | Quantidade de proxies confiáveis à frente |
| `LOG_CLIENT_IP` | `true` | Registra IP nos logs (dado pessoal — LGPD/GDPR) |
| `READINESS_CACHE_SECONDS` | `3.0` | TTL do cache do `/ready` em segundos |

### MinIO

| Variável | Default | Descrição |
|---|---|---|
| `MINIO_ENDPOINT` | `minio:9000` | `host:porta` do MinIO (sem scheme) |
| `MINIO_USE_SSL` | `false` | TLS para conexão com o MinIO |
| `MINIO_ROOT_USER` | `myapp` | Usuário admin — **nome não-óbvio obrigatório em produção** |
| `MINIO_ROOT_PASSWORD` | `myapp-minio-dev` | Senha admin — **senha forte obrigatória em produção** |
| `MINIO_BUCKET` | `myapp-files` | Bucket padrão (criado automaticamente se ausente) |

### Celery

| Variável | Default | Descrição |
|---|---|---|
| `CELERY_BROKER_URL` | `redis://:myapp@redis-celery:6379/0` | URL do broker |
| `CELERY_RESULT_BACKEND` | `redis://:myapp@redis-celery:6379/1` | URL do result backend |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Execução síncrona in-process (apenas para testes) |
| `CELERY_HEARTBEAT_SECONDS` | `60.0` | Cadência do heartbeat do beat |

### Docker Compose (não lidas pelo `Settings`)

Variáveis usadas apenas pelo `docker-compose.yml` e pelo `entrypoint.sh`:

| Variável | Default | Descrição |
|---|---|---|
| `POSTGRES_USER` | `myapp` | Usuário criado no container do Postgres |
| `POSTGRES_PASSWORD` | `myapp` | Senha do Postgres |
| `POSTGRES_DB` | `myapp` | Banco criado no container |
| `REDIS_PASSWORD` | `myapp` | Senha do Redis do rate-limit |
| `CELERY_REDIS_PASSWORD` | `myapp` | Senha do Redis do Celery |
| `RUN_MIGRATIONS_ON_START` | `true` | Executa `alembic upgrade head` no boot do container |
| `HEALTHCHECK_HOST` | `127.0.0.1` | Header `Host` enviado pelo healthcheck do container |
| `API_BIND` | `127.0.0.1` | Interface de bind da porta da API |
| `API_PORT` | `8001` | Porta publicada no host |

---

## Licença

Distribuído sob a [licença MIT](LICENSE).
