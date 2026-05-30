from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    environment: str = "development"

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

    @property
    def is_production(self) -> bool:
        """Indica se a aplicação roda em ambiente de produção."""
        return self.environment.strip().lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância única de configurações (cacheada)."""
    return Settings()


settings = get_settings()
