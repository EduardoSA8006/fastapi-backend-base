from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings


def create_app() -> FastAPI:
    """Cria e configura a instância da aplicação FastAPI."""
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
    )
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()


@app.get("/")
def root() -> dict[str, str]:
    """Rota raiz com informações básicas da API."""
    return {"app": settings.app_name, "docs": "/docs"}
