from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from starlette.requests import Request

from app.core.config import Settings


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


# Deliberadamente NÃO há engine/SessionLocal de módulo: tudo nasce em
# create_app e vive em app.state — um repository que importasse a fábrica de
# módulo furaria o override de Settings silenciosamente (footgun). Uso fora
# do ciclo do app (scripts, alembic) constrói a própria engine via
# build_engine(Settings()).


def get_db(request: Request) -> Generator[Session]:
    """Dependência do FastAPI: uma sessão POR REQUISIÇÃO, com transação.

    Padrão de transação da casa (definido antes do primeiro repository):
    caso de uso terminou sem exceção => COMMIT; exceção => ROLLBACK (nada
    parcial persiste) e propaga (vira 500 pelo ErrorBoundary); sempre CLOSE.
    Services/repositories NÃO chamam commit — usam flush quando precisarem
    de IDs antes do fim da requisição.

    A fábrica vem de `app.state.db_sessionmaker` (registrada por create_app,
    respeitando o Settings injetado) — sem fallback de módulo.
    """
    session_factory: sessionmaker[Session] = request.app.state.db_sessionmaker
    db = session_factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    else:
        db.commit()
    finally:
        db.close()
