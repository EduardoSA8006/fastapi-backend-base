# Refatoração: arquitetura feature-first + core + shared (MVVM semântico)

Data: 2026-06-05
Branch alvo: `seguranca/hardening-config-ratelimit`
Status: aprovado

## Objetivo

Reorganizar o esqueleto do MyApp para a arquitetura padrão do usuário
(feature-first + core + shared + MVVM), preparando o terreno para as features
de domínio — **sem mudar comportamento**: mesmas rotas, mesmos guards, mesma
config, compose intocado. Refatoração estrutural pura (git mv + ajuste de
imports), exceto pela adição da hierarquia de exceções (`shared/exceptions.py`),
que faz parte do contrato arquitetural.

## Decisões (aprovadas)

1. **MVVM semântico, não literal.** View = `router.py` (HTTP puro),
   ViewModel = `service.py` (caso de uso, devolve schemas), Model =
   `models.py` (SQLAlchemy) + `repository.py` (acesso a dados),
   DTOs = `schemas.py`. Nomes idiomáticos de FastAPI; MVVM em espírito.
2. **Erros: app_exceptions tipadas + handler global** (não Result[T, E]).
   Services levantam exceções tipadas; um handler converte para HTTP com
   corpo padronizado.
3. **`shared/`** (não `share/`) — consistente com o portfolio-monorepo.

## Estrutura alvo

```
app/
├── main.py                      # composition root: create_app() + guards
├── worker.py                    # entrypoint Celery (raiz, como main.py)
├── core/                        # infra transversal — ZERO regra de negócio
│   ├── config.py
│   ├── database.py
│   ├── logging.py
│   ├── limiter.py
│   ├── client_ip.py
│   ├── security_guards.py
│   └── middleware/
│       ├── observability.py
│       ├── security_headers.py
│       └── body_size_limit.py
├── shared/                      # código cross-feature de domínio
│   └── exceptions.py            # NOVO: AppException + handler global
└── features/
    └── health/
        └── router.py            # probes (sem service/repo — YAGNI)
```

### Anatomia de uma feature (contrato para features futuras)

```
features/<nome>/
├── router.py       # View: valida entrada, chama o service, monta resposta
├── service.py      # ViewModel: orquestra o caso de uso
├── repository.py   # Model (acesso a dados)
├── models.py       # Model (SQLAlchemy; registrado no metadata p/ alembic)
├── schemas.py      # DTOs de request/response (Pydantic)
├── exceptions.py   # erros da feature — herdam de shared.exceptions
└── tasks.py        # tasks Celery (adicionado ao include do app.worker)
```

Regras de dependência: features importam de `core` e `shared`; `shared`
importa de `core`; `core` não importa de ninguém acima. Feature NÃO importa
de outra feature (cross-feature passa por `shared`).

## Movimentos (git mv, sem reescrever lógica)

| De | Para |
|---|---|
| `app/middleware/observability.py` | `app/core/middleware/observability.py` |
| `app/middleware/security_headers.py` | `app/core/middleware/security_headers.py` |
| `app/middleware/body_size_limit.py` | `app/core/middleware/body_size_limit.py` |
| `app/api/routes/health.py` | `app/features/health/router.py` |
| `app/api/router.py` | removido — `main.py` inclui routers de feature direto |
| `app/models/`, `app/schemas/` (vazios) | removidos — models/schemas por feature |

Ajustes decorrentes:
- `app/main.py`: imports novos; `include_router` aponta para
  `features.health.router`; referência a `health.health_check`/`readiness`
  (exempt do rate-limit) segue o novo módulo; registra o handler de
  `AppException`.
- `alembic/env.py`: troca `import app.models` por comentário orientando a
  importar `app.features.<feature>.models` conforme forem criados (hoje não
  há nenhum model — o import era de pacote vazio).
- `app/api/` removido por inteiro.

## Novo: `app/shared/exceptions.py`

```python
class AppException(Exception):
    """Base dos erros de domínio. status_code + detail viram a resposta HTTP."""
    status_code: int = 500
    detail: str = "Erro interno."

class NotFoundError(AppException):      # 404
class ConflictError(AppException):      # 409
class UnavailableError(AppException):   # 503
```

- Handler global registrado em `create_app`:
  `JSONResponse({"detail": exc.detail}, status_code=exc.status_code)`.
- Roda dentro da pilha de middleware — security headers preservados nas
  respostas de erro (mesma garantia dos 400/413/429 atuais).
- Features especializam (ex.: `StorageObjectNotFoundError(NotFoundError)`).

## Testes

- Reorganizar espelhando a estrutura: `tests/core/`, `tests/shared/`,
  `tests/features/health/` — git mv + ajuste de imports, SEM alterar
  asserções existentes.
- Mapeamento: test_health → tests/features/health/; test_security_integration
  e test_celery FICAM na raiz de tests/ (cobrem a composição main+guards,
  cross-camada — não pertencem a uma pasta de camada);
  test_limiter/test_logging/test_database/test_client_ip/test_observability/
  test_security_headers/test_body_size_limit → tests/core/.
- NOVOS testes apenas para `shared/exceptions.py`: handler devolve o status
  e corpo corretos e mantém os security headers (TDD).

## Critérios de aceite

- `poetry run pytest` verde com as mesmas asserções (123 testes existentes
  passam; novos testes de exceções somam).
- ruff + format + mypy strict verdes.
- `docker compose config -q` ok (nada no compose referencia caminhos movidos;
  `app.worker` e `app.main:app` não mudam de caminho).
- Nenhuma mudança de comportamento HTTP (rotas, status, headers idênticos).

## Fora de escopo (YAGNI)

- Criar features de domínio, services ou repositories reais.
- `shared/schemas.py` ou utilitários compartilhados sem consumidor.
- Mudar config, compose, Dockerfile, CI.
- Result[T, E] (decisão registrada: exceções tipadas).
