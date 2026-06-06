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
