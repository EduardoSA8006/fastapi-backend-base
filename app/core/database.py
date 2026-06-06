from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from starlette.requests import Request

from app.core.config import Settings
from app.core.config import settings as default_settings


class Base(DeclarativeBase):
    """Classe base declarativa para os modelos ORM."""


def build_engine(settings: Settings) -> Engine:
    """Cria a engine do SQLAlchemy a partir das configurações fornecidas."""
    # `check_same_thread` é necessário apenas para SQLite.
    connect_args = (
        {"check_same_thread": False}
        if settings.database_url.startswith("sqlite")
        else {}
    )
    return create_engine(settings.database_url, connect_args=connect_args)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Cria a fábrica de sessões ligada à engine."""
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Engine/sessão padrão (settings de módulo), para usos fora do ciclo do app.
engine = build_engine(default_settings)
SessionLocal = build_session_factory(engine)


def get_db(request: Request) -> Generator[Session, None, None]:
    """Dependência do FastAPI que fornece uma sessão por requisição.

    Usa a fábrica de sessões registrada em `app.state` por `create_app`
    (respeitando o `Settings` injetado); recorre à fábrica de módulo se ausente.
    """
    session_factory: sessionmaker[Session] = getattr(
        request.app.state, "db_sessionmaker", SessionLocal
    )
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
