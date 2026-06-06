# --- Estágio de build: instala dependências num venv isolado ---
# H2: fixe a base por DIGEST em produção (uma re-publicação da tag muda o
# conteúdo). Obtenha com `docker buildx imagetools inspect python:3.13-slim` ou
# `docker inspect --format='{{index .RepoDigests 0}}' python:3.13-slim` e use:
#   FROM python:3.13-slim@sha256:<digest> AS builder
# Mantenha atualizado via Renovate/Dependabot (updates de digest do Docker).
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.4.1 \
    PIP_NO_CACHE_DIR=1 \
    # venv no projeto, copiado para o estágio final (Poetry/pip NÃO vão p/ runtime).
    POETRY_VIRTUALENVS_CREATE=true \
    POETRY_VIRTUALENVS_IN_PROJECT=true

WORKDIR /app

RUN pip install "poetry==${POETRY_VERSION}"

# Instala só as dependências primeiro (aproveita o cache de camadas).
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main


# --- Estágio de runtime: slim, sem Poetry/pip/ferramentas de build ---
# H2: fixe também esta base por digest (mesmo digest do builder).
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # O venv copiado do builder entra no PATH; nada de Poetry no runtime.
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Usuário sem privilégios: limita o impacto de uma eventual RCE na aplicação.
RUN groupadd -r app && useradd -r -g app -d /app app

# Copia apenas o venv (dependências) do builder e, depois, o código.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app . .

RUN chmod +x /app/docker/entrypoint.sh

USER app

EXPOSE 8000

# Healthcheck da própria API. O probe envia um header Host configurável via
# HEALTHCHECK_HOST (default 127.0.0.1, que funciona em dev com TRUSTED_HOSTS=["*"]).
# Em produção com TRUSTED_HOSTS restrito, defina HEALTHCHECK_HOST com um host
# permitido (ex.: api.classup.com), senão o TrustedHostMiddleware responde 400 e
# o healthcheck falha. Não afrouxamos o TrustedHost para o loopback de propósito.
# Bate em /api/v1/health (liveness, isento do rate-limit). Para readiness das
# dependências (banco/Redis), use /api/v1/ready no orquestrador.
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import os,urllib.request,sys; h=os.getenv('HEALTHCHECK_HOST','127.0.0.1'); req=urllib.request.Request('http://127.0.0.1:8000/api/v1/health',headers={'Host':h}); sys.exit(0 if urllib.request.urlopen(req,timeout=2).status==200 else 1)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
# --no-server-header reduz fingerprint; --limit-concurrency e --timeout-keep-alive
# mitigam exaustão/keep-alive ocioso (slowloris exige timeouts de leitura na borda
# — ver README). Em produção, prefira gunicorn + UvicornWorker com múltiplos
# workers (o guard de produção já força redis:// p/ o rate-limit).
# H5: NÃO defina --forwarded-allow-ips aqui. A fonte da verdade do IP do cliente
# é o resolve_client_ip da app (TRUST_PROXY/NUM_TRUSTED_PROXIES). Mantido no
# default restrito (127.0.0.1), o uvicorn NÃO reescreve scope["client"] a partir
# do X-Forwarded-For — evita um caminho de confiança paralelo e conflitante.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-server-header", \
     "--limit-concurrency", "100", \
     "--timeout-keep-alive", "5"]
