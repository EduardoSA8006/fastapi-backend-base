# Última refatoração: global de settings + split do test_security_integration

Data: 2026-06-06
Status: aprovado

## A — Remover o global `settings` de `app/core/config.py`

`settings = get_settings()` executa no IMPORT — último estado de módulo com
efeito colateral (mesmo footgun removido do database.py). Único consumidor:
`alembic/env.py`.

- Remover a linha; `alembic/env.py` chama `get_settings()` no ponto de uso.
- Teste de regressão: módulo `app.core.config` sem atributo `settings`
  (espelha o guard do database).
- Efeito positivo: importar o módulo deixa de exigir ENVIRONMENT — só a
  instanciação de Settings exige.

## B — Dividir `tests/test_security_integration.py` (~470 linhas, 8 assuntos)

Movimentação PURA (asserções intactas), arquivos na raiz de `tests/`
(composição create_app, regra do spec feature-first):

- `test_app_headers.py` — headers em respostas reais + 413/429/400 + docs
  sem CSP
- `test_app_rate_limit.py` — 429, fail-closed, disabled, XFF spoof, probes
  isentos
- `test_app_body_size.py` — 413 Content-Length + chunked
- `test_app_host_cors.py` — trusted host, healthcheck host, CORS preflight
  + guard universal de CORS
- `test_app_root.py` — rota raiz (injetado/produção) + /redoc + título
- `test_production_guards.py` — guards fail-closed + warnings de proxy
- `test_environment.py` — validator fail-closed + variável obrigatória

`test_security_integration.py` deixa de existir.

## Critérios de aceite

- Mesma contagem de testes (180 unit) e mesmas asserções; gate completo
  verde (ruff/format/mypy/pytest); integração intocada.
