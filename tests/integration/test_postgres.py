"""Integração com Postgres REAL (testcontainers).

Cobre o que a suíte unitária (SQLite) não consegue: o caminho psycopg, as
migrações Alembic num banco limpo e os dois desfechos do /ready para o banco.
"""

import os
import subprocess
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer

from app.core.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        yield pg.get_connection_url()


def test_alembic_upgrade_head_em_banco_limpo(pg_url: str) -> None:
    # Roda em subprocesso (como o entrypoint do container) porque o env.py lê
    # settings.database_url do ambiente no import — o processo atual já tem
    # settings cacheado.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": pg_url},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"alembic falhou:\n{result.stderr}"

    # Não é no-op: a baseline grava alembic_version == head — máquina de
    # migração completa (conexão, transação, version table) exercitada.
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    engine = create_engine(pg_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version"))
            assert row.scalar_one() == head
    finally:
        engine.dispose()


def test_ready_database_ok_com_postgres_real(pg_url: str) -> None:
    # Caminho feliz do /ready contra Postgres de verdade (pool + psycopg).
    app = create_app(
        Settings(
            database_url=pg_url,
            rate_limit_storage_uri="memory://",
            trusted_hosts=["testserver"],
        )
    )
    client = TestClient(app)
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "ok"


def test_ready_503_quando_banco_cai() -> None:
    # Branch que o SQLite nunca exercita: banco fora -> database=error -> 503.
    # Container dedicado e gerenciado manualmente (sem `with`): ele é parado
    # NO MEIO do teste, e o stop() do testcontainers remove o container — um
    # segundo stop no __exit__ falharia com 404.
    pg = PostgresContainer("postgres:16-alpine", driver="psycopg")
    pg.start()
    stopped = False
    try:
        app = create_app(
            Settings(
                database_url=pg.get_connection_url(),
                rate_limit_storage_uri="memory://",
                trusted_hosts=["testserver"],
                # Sem cache do /ready: o teste derruba o banco NO MEIO e
                # precisa ver a falha na chamada seguinte.
                readiness_cache_seconds=0.0,
            )
        )
        client = TestClient(app)
        assert client.get("/api/v1/ready").status_code == 200
        pg.stop()
        stopped = True
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not ready"
        assert body["checks"]["database"] == "error"
    finally:
        if not stopped:
            pg.stop()
