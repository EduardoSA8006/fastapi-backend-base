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

# Copia o código da aplicação.
COPY . .

# Garante que o entrypoint seja executável.
RUN chmod +x /app/docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
# --no-server-header reduz fingerprint (não emite "Server: uvicorn").
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
