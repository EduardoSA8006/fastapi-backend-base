from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import build_engine, build_session_factory, get_db


def test_get_db_yields_session_from_app_state() -> None:
    # get_db deve usar a fábrica de sessões registrada em app.state (DI),
    # respeitando o Settings injetado em vez do engine de módulo.
    settings = Settings(database_url="sqlite://")  # SQLite em memória
    engine = build_engine(settings)

    app = FastAPI()
    app.state.db_sessionmaker = build_session_factory(engine)

    @app.get("/dbcheck")
    def dbcheck(db: Session = Depends(get_db)) -> dict[str, Any]:
        return {"result": db.execute(text("SELECT 1")).scalar_one()}

    client = TestClient(app)
    response = client.get("/dbcheck")
    assert response.status_code == 200
    assert response.json() == {"result": 1}


class _RecordingSession:
    """Sessão fake que registra o ciclo de transação."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    def commit(self) -> None:
        self._log.append("commit")

    def rollback(self) -> None:
        self._log.append("rollback")

    def close(self) -> None:
        self._log.append("close")


def _app_with_recording_factory(log: list[str]) -> FastAPI:
    app = FastAPI()
    app.state.db_sessionmaker = lambda: _RecordingSession(log)

    @app.get("/ok")
    def ok(db: Session = Depends(get_db)) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/boom")
    def boom(db: Session = Depends(get_db)) -> None:
        raise RuntimeError("falha no caso de uso")

    return app


def test_get_db_commita_no_sucesso_e_fecha() -> None:
    # Padrão de transação definido ANTES do primeiro repository: sucesso do
    # caso de uso => commit; sempre close. Sem isso, cada service teria que
    # lembrar de commitar (e o primeiro esquecimento perderia writes em
    # silêncio).
    log: list[str] = []
    client = TestClient(_app_with_recording_factory(log))
    assert client.get("/ok").status_code == 200
    assert log == ["commit", "close"]


def test_get_db_faz_rollback_em_excecao_e_propaga() -> None:
    # Exceção no caso de uso => rollback (nada parcial persiste) e a exceção
    # propaga (vira 500 pelo ErrorBoundary no app real).
    log: list[str] = []
    client = TestClient(_app_with_recording_factory(log), raise_server_exceptions=False)
    assert client.get("/boom").status_code == 500
    assert log == ["rollback", "close"]


def test_modulo_nao_cria_engine_no_import() -> None:
    # Footgun removido: nada de engine/SessionLocal de módulo — um repository
    # que os importasse furaria o override de app.state silenciosamente.
    import app.core.database as db_mod

    assert not hasattr(db_mod, "engine")
    assert not hasattr(db_mod, "SessionLocal")
