"""Security headers em respostas REAIS do app (composição create_app).

Inclui as rejeições (413/429/400) — toda resposta sai blindada — e a
isenção de CSP restrita aos docs.
"""

from tests.conftest import make_client, make_rl_client


def test_security_headers_on_real_app() -> None:
    client = make_client()
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
    )


def test_docs_renders_without_csp() -> None:
    client = make_client()
    response = client.get("/docs")
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers
    # A isenção é SÓ da CSP: as demais proteções permanecem nos docs.
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_security_headers_present_on_413() -> None:
    client = make_client(max_body_size=10)
    response = client.post("/api/v1/health", content=b"x" * 50)
    assert response.status_code == 413
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_security_headers_present_on_429() -> None:
    # /_rl (não isento) para forçar o 429; os probes não passam pelo rate-limit.
    client = make_rl_client(rate_limit_default="1/minute")
    client.get("/_rl")
    response = client.get("/_rl")
    assert response.status_code == 429
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


def test_security_headers_present_on_400_bad_host() -> None:
    client = make_client(trusted_hosts=["example.com"])
    response = client.get("/api/v1/health")
    assert response.status_code == 400
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
