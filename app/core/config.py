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

    # Banco de dados — padrão SQLite local para desenvolvimento.
    database_url: str = "sqlite:///./classup.db"


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância única de configurações (cacheada)."""
    return Settings()


settings = get_settings()
