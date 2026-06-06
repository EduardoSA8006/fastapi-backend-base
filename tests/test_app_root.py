"""Rota raiz e wiring do FastAPI no composition root."""

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_client, make_prod_settings


def test_root_existe_em_apps_de_create_app_e_usa_settings_injetado() -> None:
    # A rota "/" pertence ao composition root: todo app de create_app a tem,
    # lendo app.state.settings — não o get_settings() global cacheado.
    client = make_client(app_name="App Injetado")
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["app"] == "App Injetado"


def test_root_em_producao_nao_anuncia_docs() -> None:
    client = TestClient(create_app(make_prod_settings(app_name="Prod App")))
    response = client.get("/", headers={"host": "api.test"})
    assert response.status_code == 200
    body = response.json()
    assert body["app"] == "Prod App"
    assert "docs" not in body  # produção não anuncia a documentação


def test_redoc_e_titulo_do_openapi_em_dev() -> None:
    # Pina o wiring do FastAPI no composition root (mutantes sobreviventes:
    # redoc_url=None e title mutado passavam despercebidos).
    client = make_client(app_name="ClassUp QA")
    assert client.get("/redoc").status_code == 200
    openapi = client.get("/openapi.json").json()
    assert openapi["info"]["title"] == "ClassUp QA"
