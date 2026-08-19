# --- Estágio de build: resolve dependências num venv isolado via uv ---
# H2: base fixada por DIGEST (uma re-publicação da tag não muda o conteúdo
# usado no build). Digest mantido via Renovate/Dependabot (Docker digest
# updates); para atualizar manualmente, use
# `docker buildx imagetools inspect python:3.14-slim`.
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS builder

# Copia o binário do uv de uma imagem oficial fixada por digest (mesma
# manutenção via Renovate/Dependabot).
COPY --from=ghcr.io/astral-sh/uv:0.12@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Instala só as dependências primeiro (aproveita o cache de camadas): sem o
# código, --no-install-project evita reinstalar a cada mudança de fonte.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


# --- Estágio de runtime: slim, sem uv/pip/ferramentas de build ---
# H2: mesma base do builder, fixada pelo MESMO digest (garante a mesma imagem
# em ambos os estágios).
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # O venv copiado do builder entra no PATH; nada de uv/pip no runtime.
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Aplica patches de segurança de OS da base: a imagem base costuma ficar atrás
# das últimas correções do Debian, e o trivy reprova CVEs de OS com correção
# publicada ("fixed"). Rodar o upgrade torna o gate determinístico,
# independente do digest exato da base (ex.: openssl CVE-2026-45447, libcap2
# CVE-2026-4878). `--no-install-recommends` e limpeza do apt mantêm a imagem enxuta.
RUN apt-get update \
    && apt-get -y --no-install-recommends upgrade \
    && rm -rf /var/lib/apt/lists/*

# Usuário sem privilégios: limita o impacto de uma eventual RCE na aplicação.
RUN groupadd -r app && useradd -r -g app -d /app app

# Copia apenas o venv (dependências) do builder e, depois, o código.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app . .

RUN chmod +x /app/docker/entrypoint.sh

# Remove pip/setuptools/wheel que a imagem base traz embutidos em /usr/local.
# O app roda EXCLUSIVAMENTE de /app/.venv (que não os contém, pois `uv sync
# --no-dev` não instala ferramentas de build) — pip/setuptools no runtime são
# superfície morta. Removê-los elimina CVEs de ferramentas de build que não têm
# o que fazer numa imagem de produção (ex.: setuptools CVE-2025-47273; msgpack
# vendorizado dentro do pip) e enxuga a imagem, de forma determinística
# (independe de qual digest da base o build pegou).
RUN set -eux; \
    for d in /usr/local/lib/python3.*/site-packages; do \
      rm -rf "$d"/pip "$d"/pip-* "$d"/setuptools "$d"/setuptools-* \
             "$d"/pkg_resources "$d"/wheel "$d"/wheel-*; \
    done; \
    rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

USER app

EXPOSE 8000

# Healthcheck da própria API. O probe envia um header Host configurável via
# HEALTHCHECK_HOST (default 127.0.0.1, que funciona em dev com TRUSTED_HOSTS=["*"]).
# Em produção com TRUSTED_HOSTS restrito, defina HEALTHCHECK_HOST com um host
# permitido (ex.: api.myapp.com), senão o TrustedHostMiddleware responde 400 e
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
