from collections.abc import Iterator

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.body_size_limit import BodySizeLimitMiddleware


def _build_app(max_body_size: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, max_body_size=max_body_size)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        body = await request.body()
        return {"size": len(body)}

    @app.get("/nobody")
    async def nobody() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_body_within_limit_passes() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=b"x" * 50)
    assert response.status_code == 200
    assert response.json() == {"size": 50}


def test_body_over_limit_rejected() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=b"x" * 200)
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_invalid_content_length_rejected() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post(
        "/echo", content=b"x", headers={"Content-Length": "abc"}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Content-Length"}


def test_negative_content_length_rejected() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post(
        "/echo", content=b"x", headers={"Content-Length": "-1"}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Content-Length"}


def test_body_at_exact_limit_passes() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=b"x" * 100)
    assert response.status_code == 200


def test_body_one_byte_over_limit_rejected() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=b"x" * 101)
    assert response.status_code == 413


def test_request_without_content_length_passes() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.get("/nobody")
    assert response.status_code == 200


def test_chunked_body_over_limit_rejected() -> None:
    # Conteúdo via iterador faz o httpx usar Transfer-Encoding: chunked,
    # sem Content-Length — exercitando a contagem real de bytes.
    def gen() -> Iterator[bytes]:
        yield b"x" * 200

    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=gen())
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_chunked_body_within_limit_passes() -> None:
    def gen() -> Iterator[bytes]:
        yield b"x" * 50

    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=gen())
    assert response.status_code == 200
    assert response.json() == {"size": 50}


def test_constructor_rejects_nonpositive_limit() -> None:
    import pytest

    with pytest.raises(ValueError):
        BodySizeLimitMiddleware(app=None, max_body_size=0)


def test_chunked_multi_chunk_accumulation_rejected() -> None:
    # Nenhum chunk isolado excede o limite, mas a soma sim — exercita a
    # contagem acumulada de bytes (cenário central do hardening).
    def gen() -> Iterator[bytes]:
        yield b"x" * 40
        yield b"x" * 40
        yield b"x" * 40  # total = 120 > limite = 100

    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=gen())
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_chunked_multi_chunk_within_limit_passes() -> None:
    def gen() -> Iterator[bytes]:
        yield b"x" * 30
        yield b"x" * 30  # total = 60 <= limite = 100

    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=gen())
    assert response.status_code == 200
    assert response.json() == {"size": 60}


def test_empty_body_passes() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=b"")
    assert response.status_code == 200
    assert response.json() == {"size": 0}


def test_whitespace_content_length_rejected() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post(
        "/echo", content=b"x", headers={"Content-Length": " 5 "}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Content-Length"}


def test_constructor_rejects_negative_limit() -> None:
    import pytest

    with pytest.raises(ValueError):
        BodySizeLimitMiddleware(app=None, max_body_size=-10)


async def test_non_http_scope_passes_through() -> None:
    # Scopes não-HTTP (lifespan, websocket) devem passar sem alteração.
    called = {"value": False}

    async def dummy_app(scope, receive, send) -> None:
        called["value"] = True

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message) -> None:
        pass

    middleware = BodySizeLimitMiddleware(app=dummy_app, max_body_size=100)
    await middleware({"type": "lifespan"}, receive, send)
    assert called["value"] is True
