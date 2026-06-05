# Refatoração final + reforço de testes

Data: 2026-06-05
Status: aprovado ("aprovado, pode fazer")

## Objetivo

Campanha em 6 fases: enxugar o composition root, consolidar a infra de
testes e elevar a qualidade das asserções com técnicas que medem/forçam
robustez (property-based, mutation testing, cenários de falha reais).

## Fases (ordem de execução)

### R1 — Composition root enxuto

Guards de produção saem do `create_app` para `core/security_guards.py`:
- `validate_universal(settings)` — guards de qualquer ambiente (CORS
  credentials + wildcard).
- `validate_production(settings)` — debug, trusted_hosts, store do
  rate-limit, senhas (Redis/DB/MinIO), usuário MinIO; chama
  `validate_celery_security`; emite os 2 warnings de proxy.
- `main.py` vira só composição (~80 linhas): validate → app → state →
  handlers → middlewares → routers → rota raiz.
- MESMAS mensagens de erro — os testes atuais (match=) são a rede de
  segurança e não mudam nesta fase.

### R2 — Helpers de teste consolidados

- `tests/conftest.py`: `make_settings`, `make_prod_settings`,
  `make_client`, `make_rl_client` (hoje duplicados em 4+ arquivos).
- `tests/integration/conftest.py`: fixture `redis_container`
  parametrizável por senha (hoje copiada 3×).
- Migração dos arquivos para as fixtures; asserções intactas.

### T3 — Asserções endurecidas

Passada nos testes existentes: status-code-só vira contrato completo
(corpo {"detail"}, security headers, X-Request-ID quando fizer sentido);
estilo padronizado. App intocada.

### T1 — Property-based (Hypothesis)

Dev-dependency `hypothesis`. Propriedades:
- `resolve_client_ip`/key_func: XFF arbitrário nunca levanta; com
  trust_proxy=False, sempre retorna o IP de conexão.
- `_REQUEST_ID_RE`: aceitos nunca contêm CR/LF/controle; <= 128 chars.
- Validator de ENVIRONMENT: aceita exatamente {development, staging,
  production} (mod strip/case); resto levanta.
- `_host_port`: nunca levanta com URL arbitrária; porta ausente => 6379.
- Body-size: corpo chunked de N bytes => 413 sse N > limite.
- `JsonFormatter`: qualquer extra string => exatamente 1 linha JSON válida.

### T4 — Novos cenários de integração/e2e

Integração: (a) Redis do rate-limit derrubado no MEIO (200 -> 500
fail-closed; /ready 503); (b) recuperação (Redis volta, contagem volta);
(c) storage sob concorrência (N puts paralelos no MinIO real, todos
íntegros). E2E: (d) heartbeat do beat REAL (espera ~75s, verifica
core.ping executado no stack); (e) resiliência (docker restart worker ->
healthy de novo). Custo aceito: e2e +~2min.

### T2 — Mutation testing (mutmut) — por último, mede tudo

- Dev-dependency `mutmut`, alvo `app/`, killer = suíte unitária.
- NÃO entra no CI (lento como gate); documentado no README como métrica
  local. Rodar agora, corrigir sobreviventes RELEVANTES (asserções fracas
  expostas), registrar falso-positivos aceitos neste spec (apêndice).

## Critérios de aceite

- Zero mudança de comportamento da app nas fases R (rotas/erros/headers
  idênticos; suíte passa sem editar asserções em R1).
- Gate completo (ruff/format/mypy/unit) verde após cada fase; integração
  e e2e completos ao final da campanha.
- Mutation score inicial documentado; sobreviventes relevantes mortos.

## Fora de escopo

- Mudanças de comportamento/feature da app.
- Mutation testing no CI.
- Testes de carga reais.

## Apêndice T2 — sobreviventes aceitos

(Preenchido na execução.)
