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


def test_body_at_exact_limit_passes() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=b"x" * 100)
    assert response.status_code == 200


def test_body_one_byte_over_limit_rejected() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=b"x" * 101)
    assert response.status_code == 413


def test_negative_content_length_rejected() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post(
        "/echo", content=b"x", headers={"Content-Length": "-1"}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Content-Length"}


def test_request_without_content_length_passes() -> None:
    # GET sem corpo não envia Content-Length, exercitando o branch None.
    client = TestClient(_build_app(max_body_size=100))
    response = client.get("/nobody")
    assert response.status_code == 200
