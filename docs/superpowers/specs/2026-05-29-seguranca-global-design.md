# Design — Camada de Segurança Global

**Data:** 2026-05-29
**Projeto:** myapp-backend (FastAPI)
**Escopo:** Infraestrutura de segurança aplicada globalmente: rate-limit + proteções de borda.

## Objetivo

Estabelecer a primeira camada de infraestrutura do projeto, focada em segurança.
Implementar um rate-limit global e um conjunto de proteções aplicadas a toda a
aplicação, de forma isolada, configurável por ambiente e testável.

## Decisões tomadas

- **Biblioteca de rate-limit:** `slowapi` (sobre a lib `limits`).
- **Backend de armazenamento do rate-limit:** Redis (no Docker), com fallback
  `memory://` para desenvolvimento local e testes.
- **Estratégia de rate-limit:** limite global por IP, com suporte a override por
  rota via decorator.
- **Proteções globais incluídas:** security headers, CORS configurável, trusted
  hosts e limite de tamanho de corpo (body size).

## Estrutura de arquivos

```
app/
├── core/
│   ├── config.py            # estende Settings com campos de segurança
│   └── limiter.py           # instancia o Limiter do slowapi + key_func
├── middleware/
│   ├── __init__.py
│   ├── security_headers.py  # SecurityHeadersMiddleware
│   └── body_size_limit.py   # BodySizeLimitMiddleware (413)
└── main.py                  # wiring de tudo no create_app()
```

Cada middleware é uma unidade isolada com responsabilidade única e testável de
forma independente. `limiter.py` concentra a configuração do rate-limit; o
`create_app()` apenas faz o *wiring* dos componentes.

## Configuração (novos campos em `Settings`)

Carregados via variáveis de ambiente (`pydantic-settings`):

| Campo | Tipo | Default | Uso |
|-------|------|---------|-----|
| `rate_limit_enabled` | bool | `true` | liga/desliga o rate-limit (útil em testes) |
| `rate_limit_default` | str | `"100/minute"` | limite global por IP |
| `rate_limit_storage_uri` | str | `"memory://"` | `redis://redis:6379/0` no Docker |
| `cors_allow_origins` | list[str] | `[]` | origens permitidas |
| `cors_allow_credentials` | bool | `false` | permite cookies/credenciais no CORS |
| `cors_allow_methods` | list[str] | `["*"]` | métodos permitidos |
| `cors_allow_headers` | list[str] | `["*"]` | headers permitidos |
| `trusted_hosts` | list[str] | `["*"]` | hosts aceitos (restrito em produção) |
| `max_body_size` | int | `1048576` (1 MB) | limite de corpo da requisição em bytes |
| `hsts_enabled` | bool | `false` | HSTS — só faz sentido sob HTTPS |
| `trust_proxy` | bool | `false` | confiar em `X-Forwarded-For` para obter o IP |

### Segurança do IP atrás de proxy

O `X-Forwarded-For` é spoofável. O `key_func` do rate-limit só lê esse header
quando `trust_proxy=true` (cenário com proxy reverso conhecido na frente);
caso contrário usa `request.client.host`. Isso evita que um atacante forje
IPs para burlar o limite. Quando `trust_proxy=true`, considera-se o **último IP
(mais à direita)** da cadeia `X-Forwarded-For` — que é o endereço que o proxy
confiável acrescentou. A entrada mais à esquerda é controlável pelo cliente e
**não** deve ser usada (usá-la permitiria burlar o rate-limit forjando IPs).
Assume-se **um único** proxy confiável à frente.

O `rate_limit_storage_uri` default `memory://` permite rodar localmente sem
Redis; o `docker-compose` o aponta para o serviço Redis.

## Componentes e ordem dos middlewares

No Starlette, o **último middleware adicionado é o mais externo (executa
primeiro)**. A ordem é definida para que rejeições baratas ocorram cedo. Ordem
de **execução** (da borda para dentro):

1. **TrustedHostMiddleware** (Starlette) — rejeita `Host` inválido → `400`
2. **CORSMiddleware** (Starlette) — aplica origens/métodos/headers das settings
3. **BodySizeLimitMiddleware** (próprio) — corpo acima do limite → `413`
4. **SlowAPIMiddleware** (slowapi) — aplica o limite global por IP → `429`
5. **SecurityHeadersMiddleware** (próprio) — injeta headers na resposta de saída

### Security headers injetados

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `Cross-Origin-Opener-Policy: same-origin`
- `Content-Security-Policy: default-src 'self'` (base; ajustável depois)
- `Strict-Transport-Security` — somente quando `hsts_enabled=true`

## Rate-limit: global + override por rota

O `Limiter` é instanciado com `default_limits=[rate_limit_default]` e
`storage_uri=rate_limit_storage_uri`, aplicando o limite global via
`SlowAPIMiddleware`. Endpoints sensíveis declaram limites próprios:

```python
@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, ...): ...
```

O override por rota exige `request: Request` na assinatura do endpoint (exigência
do slowapi).

Quando `rate_limit_enabled=false`, o middleware e os handlers não são
registrados — útil em testes e cenários onde o RL fica a cargo de uma camada
externa.

## Tratamento de erros (respostas JSON consistentes)

- `429 Too Many Requests` → `{"detail": "Rate limit exceeded", "retry_after": <s>}`
  com header `Retry-After`.
- `413 Request Entity Too Large` → `{"detail": "Request body too large"}`.
- `400 Bad Request` → host não confiável (TrustedHostMiddleware).

## Infraestrutura (Docker)

- Adicionar serviço **`redis:7-alpine`** ao `docker-compose.yml`, com healthcheck
  (`redis-cli ping`).
- A API passa a depender do Redis (`depends_on: condition: service_healthy`) e
  recebe `RATE_LIMIT_STORAGE_URI=redis://redis:6379/0` via ambiente.
- `.env.example` atualizado com os novos campos.

## Testes

Usando `TestClient`, com `rate_limit_storage_uri="memory://"` para isolar do
Redis:

- estourar o limite global → `429` (com `Retry-After`)
- presença de todos os security headers nas respostas
- corpo acima de `max_body_size` → `413`
- preflight CORS respeitando as origens configuradas
- `Host` inválido com `trusted_hosts` restrito → `400`
- `key_func`: com `trust_proxy=false`, `X-Forwarded-For` é ignorado para a chave

## Fora de escopo (YAGNI)

- Autenticação e rate-limit por usuário autenticado (depende de auth, ainda
  inexistente).
- WAF/regras avançadas, bot detection.
- Rate-limit por rota além do mecanismo de override (limites específicos serão
  adicionados conforme os endpoints surgirem).

## Notas de implementação / desvios

Durante a execução (com análise de código profunda por tarefa + revisão de
segurança automatizada), o design foi ajustado nos seguintes pontos:

- **IP do proxy (correção de segurança):** o design original mencionava o
  primeiro IP de `X-Forwarded-For`; o correto e implementado é o **último (mais
  à direita)**, que o proxy confiável acrescenta. O primeiro é forjável e
  permitiria burlar o rate-limit.
- **Limite de corpo (escopo ampliado):** inicialmente apenas via
  `Content-Length` (chunked marcado como YAGNI). Após a revisão, o
  `BodySizeLimitMiddleware` foi reescrito como middleware **ASGI puro** que conta
  os **bytes reais do stream**, fechando o bypass via `Transfer-Encoding:
  chunked`. Também passou a rejeitar `Content-Length` negativo/inválido e a
  validar `max_body_size > 0`.
- **Ordem dos middlewares (correção de segurança):** o `SecurityHeadersMiddleware`
  ficou o **mais externo** (não o mais interno), para que TODAS as respostas —
  inclusive rejeições `400`/`413`/`429` dos demais middlewares — recebam os
  cabeçalhos de segurança.
- **CSP vs. Swagger:** o `Content-Security-Policy` é isento em `/docs`, `/redoc`
  e `/openapi.json` para não quebrar a UI de documentação.
- **Guarda de CORS:** `create_app` rejeita na inicialização a combinação
  insegura `cors_allow_credentials=true` + `cors_allow_origins=["*"]`.
- **Rate-limit headers:** o `Limiter` é criado com `headers_enabled=true` para
  emitir `Retry-After`/`X-RateLimit-*`; o handler 429 é **síncrono** (exigência
  do `SlowAPIMiddleware`) e resiliente à API privada do slowapi.
- **Redis:** exposto **apenas na rede interna** do Docker (sem mapeamento de
  porta no host), reduzindo a superfície de ataque.
- **Aviso de produção:** `create_app` emite um warning quando `trusted_hosts`
  é `["*"]` com `debug=false` (validação de Host efetivamente desativada).
