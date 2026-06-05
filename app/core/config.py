from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ambientes reconhecidos. Recusar valores desconhecidos é deliberado (fail-closed):
# um typo plausível em deploy ("prod", "produção", "Production " com espaço) não
# pode fazer a app subir silenciosamente em modo dev — o que desativaria os guards
# de produção (Host, store do rate-limit, DEBUG, senha de banco) e exporia /docs.
_VALID_ENVIRONMENTS = frozenset({"development", "staging", "production"})


class Settings(BaseSettings):
    """Configurações da aplicação carregadas de variáveis de ambiente."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ClassUp Backend"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Ambiente de execução. Em "production" os guards de segurança falham duro
    # (Host, store do rate-limit, debug) e a documentação interativa é desligada.
    # OBRIGATÓRIO (sem default): esta é a variável-mestra que governa todos os
    # guards — um default "development" seria fail-open para o caso de typo no
    # NOME da variável (ENVIRONMNET=production seria ignorada e a app subiria
    # em modo dev com /docs expostos e DEBUG aceito). Chave ausente = boot
    # recusado com ValidationError. Defina via .env (cp .env.example .env) ou
    # ambiente; o compose exige a variável (${ENVIRONMENT:?...}).
    environment: str

    # Banco de dados — padrão SQLite local para desenvolvimento.
    database_url: str = "sqlite:///./classup.db"

    # Rate limit
    rate_limit_enabled: bool = True
    rate_limit_default: str = "100/minute"
    rate_limit_storage_uri: str = "memory://"

    # CORS
    cors_allow_origins: list[str] = []
    cors_allow_credentials: bool = False
    cors_allow_methods: list[str] = ["*"]
    cors_allow_headers: list[str] = ["*"]

    # Trusted hosts
    trusted_hosts: list[str] = ["*"]

    # Body size (bytes)
    max_body_size: int = 1_048_576  # 1 MB

    # Security headers / proxy
    hsts_enabled: bool = False
    trust_proxy: bool = False
    # Quantidade de proxies reversos confiáveis à frente. Usado (quando
    # trust_proxy=true) para extrair o IP real do cliente de X-Forwarded-For:
    # o cliente é a entrada acrescentada pelo proxy mais externo. Ex.: LB +
    # nginx = 2. Só tem efeito com trust_proxy=true.
    num_trusted_proxies: int = 1

    # Observabilidade / privacidade: o IP do cliente é dado pessoal (LGPD/GDPR).
    # Permite desligar o registro do IP nos logs de acesso.
    log_client_ip: bool = True

    # TTL (s) do cache do /ready. O probe é isento de rate-limit e sem auth;
    # sem cache, cada chamada faz SELECT 1 + PING — um amplificador de carga
    # não-autenticado contra banco/Redis se o path vazar para a borda. Com o
    # TTL, rajadas custam <= 1 round-trip de backend por janela. 0 desliga
    # (testes de queda imediata). Mantém o probe SEM rate-limit de propósito:
    # re-sujeitá-lo trocaria o 503 granular por um 500 opaco com o store fora.
    readiness_cache_seconds: float = 3.0

    # MinIO (armazenamento de objetos) — roda na rede interna do Docker, SEM
    # porta publicada (igual ao Redis/Postgres). Só o backend fala com ele, via
    # o hostname `minio`. endpoint é host:port (sem scheme); o SDK monta a URL a
    # partir de minio_use_ssl.
    minio_endpoint: str = "minio:9000"
    minio_use_ssl: bool = False
    minio_root_user: str = "classup"
    # Default dev (espelha o compose). O MinIO RECUSA senhas < 8 caracteres
    # (por isso não é "classup"); o guard de produção recusa este valor por
    # estar em WEAK_PASSWORDS. noqa: não é segredo real.
    minio_root_password: str = "classup-minio-dev"  # noqa: S105
    minio_bucket: str = "classup-files"

    # Celery — broker e result backend numa instância Redis DEDICADA
    # (redis-celery), separada do Redis do rate-limit; em produção o guard
    # recusa apontar os dois para a mesma instância. DB 0 = broker,
    # DB 1 = resultados (keyspaces separados facilitam inspeção/limpeza).
    celery_broker_url: str = "redis://:classup@redis-celery:6379/0"
    celery_result_backend: str = "redis://:classup@redis-celery:6379/1"
    # Execução síncrona in-process (sem broker) — só para testes.
    celery_task_always_eager: bool = False
    # Cadência do heartbeat do beat (core.ping). Retunável via env sem mudar
    # código; a integração do pipeline beat→broker→worker usa 1s.
    celery_heartbeat_seconds: float = 60.0

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, value: str) -> str:
        """Normaliza e recusa ambientes desconhecidos (fail-closed)."""
        normalized = value.strip().lower()
        if normalized not in _VALID_ENVIRONMENTS:
            valid = ", ".join(sorted(_VALID_ENVIRONMENTS))
            raise ValueError(f"ENVIRONMENT inválido: {value!r}. Use um de: {valid}.")
        return normalized

    @property
    def is_production(self) -> bool:
        """Indica se a aplicação roda em ambiente de produção."""
        # O validator já normalizou (strip+lower) e garantiu valor conhecido.
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância única de configurações (cacheada)."""
    return Settings()


settings = get_settings()
