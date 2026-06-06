from collections import deque
from collections.abc import Iterator
from typing import cast

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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

    @app.post("/sink")
    async def sink() -> dict[str, str]:
        # Handler que NÃO consome o corpo — o caso em que a contagem lazy falharia.
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
    response = client.post("/echo", content=b"x", headers={"Content-Length": "abc"})
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Content-Length"}


def test_negative_content_length_rejected() -> None:
    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/echo", content=b"x", headers={"Content-Length": "-1"})
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


def test_chunked_unread_body_over_limit_rejected() -> None:
    # Caminho perigoso: corpo chunked (sem Content-Length) grande para um
    # handler que NÃO lê o corpo. A contagem lazy nunca dispararia (o handler
    # jamais chama receive()); o eager-drain precisa rejeitar mesmo assim.
    def gen() -> Iterator[bytes]:
        yield b"x" * 200

    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/sink", content=gen())
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_chunked_unread_body_within_limit_passes() -> None:
    # Mesmo cenário, dentro do limite: o eager-drain repassa o corpo e o
    # handler responde normalmente.
    def gen() -> Iterator[bytes]:
        yield b"x" * 50

    client = TestClient(_build_app(max_body_size=100))
    response = client.post("/sink", content=gen())
    assert response.status_code == 200
    assert response.json() == {"ok": "yes"}


def test_constructor_rejects_nonpositive_limit() -> None:
    import pytest

    with pytest.raises(ValueError, match="max_body_size"):
        BodySizeLimitMiddleware(app=cast(ASGIApp, None), max_body_size=0)


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
    response = client.post("/echo", content=b"x", headers={"Content-Length": " 5 "})
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Content-Length"}


def test_constructor_rejects_negative_limit() -> None:
    import pytest

    with pytest.raises(ValueError, match="max_body_size"):
        BodySizeLimitMiddleware(app=cast(ASGIApp, None), max_body_size=-10)


# --- Testes ASGI de baixo nível (ramos não exercitáveis via TestClient) ---


def _http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {"type": "http", "method": "POST", "path": "/", "headers": headers or []}


async def _run(
    mw: BodySizeLimitMiddleware, scope: Scope, messages: list[Message]
) -> list[Message]:
    """Roda o middleware com uma fila de mensagens de receive; coleta os sends."""
    queue = deque(messages)
    sent: list[Message] = []

    async def receive() -> Message:
        if queue:
            return queue.popleft()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await mw(scope, receive, send)
    return sent


async def test_lazy_count_rejects_when_real_body_exceeds_declared() -> None:
    # Content-Length declarado (5) está dentro do limite, mas os bytes reais
    # (200) excedem: a contagem lazy precisa pegar o Content-Length mentiroso.
    async def drain_app(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            msg = await receive()
            if msg["type"] == "http.request" and not msg.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    mw = BodySizeLimitMiddleware(app=drain_app, max_body_size=100)
    scope = _http_scope([(b"content-length", b"5")])
    sent = await _run(
        mw, scope, [{"type": "http.request", "body": b"x" * 200, "more_body": False}]
    )

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413


async def test_lazy_count_truncates_when_response_already_started() -> None:
    # A app começa a responder ANTES de ler o corpo que estoura o limite: como
    # os headers já foram enviados, o middleware encerra o corpo limpo (sem 413).
    async def respond_then_read(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while True:
            msg = await receive()
            if msg["type"] == "http.request" and not msg.get("more_body", False):
                break
        await send({"type": "http.response.body", "body": b"late", "more_body": False})

    mw = BodySizeLimitMiddleware(app=respond_then_read, max_body_size=100)
    scope = _http_scope([(b"content-length", b"5")])
    sent = await _run(
        mw, scope, [{"type": "http.request", "body": b"x" * 200, "more_body": False}]
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 200
    # O 'late' nunca é enviado; encerra com corpo vazio.
    assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}


async def test_eager_drain_handles_disconnect() -> None:
    # Sem Content-Length e com http.disconnect imediato: o pré-drain repassa o
    # evento e invoca a app normalmente.
    received: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        msg = await receive()
        received.append(msg["type"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    mw = BodySizeLimitMiddleware(app=app, max_body_size=100)
    sent = await _run(mw, _http_scope(), [{"type": "http.disconnect"}])

    assert received == ["http.disconnect"]
    assert any(m["type"] == "http.response.start" and m["status"] == 204 for m in sent)


async def test_eager_drain_replay_delegates_after_buffer_exhausted() -> None:
    # A app lê mais vezes do que há chunks bufferizados: após esgotar o buffer,
    # o replay delega ao receive real (que entrega o http.disconnect).
    types: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        types.append((await receive())["type"])  # chunk bufferizado
        types.append((await receive())["type"])  # buffer vazio -> receive real
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    mw = BodySizeLimitMiddleware(app=app, max_body_size=100)
    sent = await _run(
        mw,
        _http_scope(),
        [{"type": "http.request", "body": b"x" * 10, "more_body": False}],
    )

    assert types == ["http.request", "http.disconnect"]
    assert any(m["type"] == "http.response.start" and m["status"] == 200 for m in sent)


async def test_non_http_scope_passes_through() -> None:
    # Scopes não-HTTP (lifespan, websocket) devem passar sem alteração.
    called = {"value": False}

    async def dummy_app(scope: Scope, receive: Receive, send: Send) -> None:
        called["value"] = True

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        pass

    middleware = BodySizeLimitMiddleware(app=dummy_app, max_body_size=100)
    await middleware({"type": "lifespan"}, receive, send)
    assert called["value"] is True
