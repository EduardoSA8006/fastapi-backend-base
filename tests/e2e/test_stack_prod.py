"""E2E em MODO PRODUCTION: os guards rodando vivos, não só em unit.

O stack sobe com ENVIRONMENT=production e credenciais fortes geradas por
execução — o simples fato de a api ficar healthy já prova que uma config de
produção válida passa por TODOS os guards de boot (senhas, hosts, store do
rate-limit, TaskIQ, MinIO). Os testes validam o comportamento exclusivo de
produção pela rede real.
"""

import httpx
import pytest

from tests.e2e.conftest import PROD_HOST

pytestmark = pytest.mark.e2e


def _client(base_url: str) -> httpx.Client:
    # TRUSTED_HOSTS restrito: toda requisição legítima envia o Host permitido.
    return httpx.Client(base_url=base_url, timeout=10, headers={"Host": PROD_HOST})


def test_boot_de_producao_passa_pelos_guards(stack_prod: str) -> None:
    # api healthy (fixture) = create_app aceitou a config de produção e o
    # healthcheck (com HEALTHCHECK_HOST) responde 200 — guards e probes vivos.
    with _client(stack_prod) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_em_producao(stack_prod: str) -> None:
    with _client(stack_prod) as client:
        response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "redis": "ok"}


def test_docs_desligadas_em_producao(stack_prod: str) -> None:
    # Em produção a superfície da API não é anunciada: /docs, /redoc e
    # /openapi.json fora do ar (em dev o e2e os tem — contraste real).
    with _client(stack_prod) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_host_desconhecido_rejeitado_com_400(stack_prod: str) -> None:
    # TrustedHost DE VERDADE: Host fora da lista é barrado antes de qualquer
    # rota — inclusive o loopback (sem header, httpx envia 127.0.0.1:18002).
    with httpx.Client(base_url=stack_prod, timeout=10) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 400
    # Rejeição também sai blindada (SecurityHeaders fora do TrustedHost).
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


def test_seguranca_de_headers_em_producao(stack_prod: str) -> None:
    with _client(stack_prod) as client:
        response = client.get("/api/v1/health")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert "server" not in response.headers
