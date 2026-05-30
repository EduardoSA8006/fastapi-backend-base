FROM python:3.13-slim AS base

# Evita arquivos .pyc e garante logs sem buffer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Instala o Poetry.
RUN pip install "poetry==${POETRY_VERSION}"

# Instala apenas as dependências primeiro, aproveitando o cache de camadas.
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main

# Usuário sem privilégios: limita o impacto de uma eventual RCE na aplicação.
RUN groupadd -r app && useradd -r -g app -d /app app

# Copia o código da aplicação já com dono não-root.
COPY --chown=app:app . .

# Garante que o entrypoint seja executável.
RUN chmod +x /app/docker/entrypoint.sh

USER app

EXPOSE 8000

# Healthcheck da própria API. Obs.: em produção com TRUSTED_HOSTS restrito, o
# host do probe (127.0.0.1) precisa estar permitido, ou use o probe do
# orquestrador com o Host correto.
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health',timeout=2).status==200 else 1)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
# --no-server-header reduz fingerprint; --limit-concurrency e --timeout-keep-alive
# mitigam slowloris/exaustão. Em produção, prefira gunicorn + UvicornWorker com
# múltiplos workers (o guard de produção já força redis:// p/ o rate-limit).
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-server-header", \
     "--limit-concurrency", "100", \
     "--timeout-keep-alive", "5"]
