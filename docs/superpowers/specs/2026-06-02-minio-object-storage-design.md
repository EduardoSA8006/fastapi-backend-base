# MinIO — armazenamento de objetos (infra + config)

Data: 2026-06-02
Branch alvo: `seguranca/hardening-config-ratelimit` (ou nova branch de feature)
Status: aprovado

## Objetivo

Adicionar o MinIO como serviço de armazenamento de arquivos do MyApp, rodando
em container Docker **sem nada exposto ao host** — toda comunicação acontece pela
rede interna do Docker, exatamente como o Redis e o Postgres já fazem. Nenhum
cliente ou serviço fala com o MinIO diretamente: tudo passa pelo backend, que é o
único que serve o conteúdo.

## Escopo

**Infra + config apenas.** Esta tarefa NÃO cria a camada de storage
(`StorageClient`/facade) nem endpoints HTTP de upload/download — eles virão junto
com a primeira feature de domínio que precisar de arquivos. O bucket é criado de
forma preguiçosa (lazy) no primeiro uso por essa camada futura, não no boot
(mesmo padrão do portfolio-monorepo de referência: sem acoplamento de startup
entre API e MinIO).

## Decisões de design (e por quê)

Referência: o padrão já testado em produção no `portfolio-monorepo`
(`portfolio-backend/app/shared/storage.py`, `core/config.py`, `docker-compose.yml`).

- **Lib: `minio` (minio-py).** `from minio import Minio`. SDK síncrono, leve,
  feito para object storage. (Adicionado ao `pyproject.toml` apenas quando a
  camada de storage for implementada — não agora, para não carregar dependência
  não usada.)
- **Credenciais root diretas, não conta de serviço com escopo.** O backend usa
  `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`. Criar conta com escopo + política é
  operação de admin (exige `mc` ou a API admin REST; o minio-py só faz operações
  de objeto), e o portfolio de referência usa root direto. O least-privilege vem
  do **isolamento de rede + padrão proxy**, não de uma conta MinIO restrita — e
  isso é consistente com o myapp, onde Redis/Postgres usam senha forte + guards
  de produção, sem contas com escopo.
- **Sem `ports:` — só `expose`, na mesma rede interna do db/redis.** Atende ao
  pedido literal: "via rede interna do docker igual ao redis e postgres". O
  console (porta 9001) é ligado mas nunca exposto (não há `expose` dele).
- **Isolamento é a fronteira de segurança.** Acesso público ao conteúdo flui
  (no futuro) pelo proxy do FastAPI, que faz streaming dos bytes. NUNCA expor
  presigned URL a um cliente público — isso anunciaria o backend de storage e
  permitiria acesso direto fora do backend.

## Mudanças

### 1. `docker-compose.yml` — serviço `minio`

Espelha o hardening do portfolio, adaptado ao estilo do myapp (bloco
`environment:` com `${VAR:-default}`, igual db/redis):

- `image: quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` (tag-pinned, como
  postgres/redis do myapp; nota para futuro digest-pin).
- `command: ["server", "/data", "--console-address", ":9001"]` — console num
  porto que nunca é publicado nem exposto.
- **Sem `ports:`**. Apenas `expose: ["9000"]`. Fica na rede default do compose
  (a mesma do db/redis/api) — alcançável só pelo hostname `minio`.
- `environment`: `MINIO_ROOT_USER: ${MINIO_ROOT_USER:-myapp}`,
  `MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-myapp}`. Mesma nota H3 já
  presente no compose: em produção, prefira docker secrets / `/run/secrets`.
- `volumes: ["minio_data:/data"]` (novo volume nomeado `minio_data`).
- `healthcheck: ["CMD-SHELL", "mc ready local || exit 1"]` (a imagem traz `mc`,
  não traz curl), com `start_period` para o tempo de subida.
- Hardening: `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`,
  `cpus`/`mem_limit` (sem `read_only`, pois o MinIO escreve em `/data`).

### 2. `api` no compose — ligação

- `depends_on` ganha `minio: { condition: service_healthy }`.
- `environment` ganha:
  - `MINIO_ENDPOINT: minio:9000`
  - `MINIO_USE_SSL: "false"`
  - `MINIO_ROOT_USER: ${MINIO_ROOT_USER:-myapp}`
  - `MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-myapp}`
  - `MINIO_BUCKET: ${MINIO_BUCKET:-myapp-files}`

### 3. `app/core/config.py` — novas settings (convenção lowercase do myapp)

```python
# MinIO (object storage, rede interna do Docker — sem porta publicada)
minio_endpoint: str = "minio:9000"   # host:port, sem scheme; SSL via flag abaixo
minio_use_ssl: bool = False
minio_root_user: str = "myapp"
minio_root_password: str = "myapp"
minio_bucket: str = "myapp-files"
```

(O mapeamento de env do pydantic-settings é case-insensitive: `MINIO_ENDPOINT`
→ `minio_endpoint`.)

### 4. `app/main.py` — guards fail-closed de produção

Espelham o guard existente do Redis/DB (`_WEAK_DB_PASSWORDS`). Dentro do bloco
`if settings.is_production:`:

- Se `minio_root_password` estiver em `_WEAK_DB_PASSWORDS` (default/fraca/vazia)
  → `raise ValueError` recusando o boot, com mensagem orientando senha forte.
- (Opcional, simétrico) validar que `minio_root_user` e `minio_endpoint` não
  estão vazios.

### 5. `.env.example` — bloco MinIO

Novo bloco com as mesmas notas de segurança dos outros: interno-only, sem porta
publicada, e aviso de que produção exige senha forte (boot recusado com
default/fraca) e idealmente docker secrets.

```dotenv
# --- MinIO (armazenamento de objetos, rede interna do Docker) ---
# Sem porta publicada: alcançável só pelo backend via hostname `minio`.
# PRODUÇÃO: senha forte obrigatória (boot recusado com default/fraca/vazia).
MINIO_ENDPOINT=minio:9000
MINIO_USE_SSL=false
MINIO_ROOT_USER=myapp
MINIO_ROOT_PASSWORD=myapp
MINIO_BUCKET=myapp-files
```

## Fluxo de dados (futuro, documentado — fora do escopo de implementação agora)

```
cliente → backend (FastAPI faz streaming dos bytes) → MinIO (rede interna)
```

O cliente nunca fala direto com o MinIO; nunca há presigned URL pública. A
camada `app/shared/storage.py` (ou `app/core/storage.py`) futura, sobre minio-py,
fará `put/get/delete` com `asyncio.to_thread` e `_ensure_bucket` lazy no primeiro
uso, mapeando erros do SDK para exceções tipadas.

## Testes

Sem subir container real (mesma abordagem dos testes de config/guards atuais):

- **Guard de produção**: `create_app(Settings(environment="production",
  minio_root_password="myapp", ...))` deve levantar `ValueError` (senha fraca
  de MinIO recusada). Variante com senha forte deve passar pelo guard de MinIO.
- **Defaults das settings**: `minio_endpoint == "minio:9000"`,
  `minio_use_ssl is False`, `minio_bucket == "myapp-files"`.

## Fora de escopo (YAGNI)

- Camada de storage / facade minio-py.
- Endpoints de upload/download e proxy de leitura.
- Dependência `minio` no `pyproject.toml` (entra com a camada de storage).
- Conta de serviço com escopo / container `mc` de provisionamento.
- Criação do bucket no boot (será lazy no primeiro uso).
- Rede dedicada `storage_net` (o myapp usa a rede default para db/redis; manter
  consistência — segmentar só se surgirem outros serviços não confiáveis).
