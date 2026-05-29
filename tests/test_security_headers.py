from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.security_headers import SecurityHeadersMiddleware


def _build_app(hsts_enabled: bool = False) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=hsts_enabled)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ping": "pong"}

    return app


def test_security_headers_present() -> None:
    client = TestClient(_build_app())
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"


def test_hsts_disabled_by_default() -> None:
    client = TestClient(_build_app(hsts_enabled=False))
    response = client.get("/ping")
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_enabled() -> None:
    client = TestClient(_build_app(hsts_enabled=True))
    response = client.get("/ping")
    assert "Strict-Transport-Security" in response.headers
