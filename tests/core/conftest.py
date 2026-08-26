"""Helpers compartilhados dos testes de middleware."""

from typing import Any


async def assert_non_http_scope_passthrough(
    middleware_cls: type, **mw_kwargs: Any
) -> None:
    """Middleware ASGI deve repassar scopes não-HTTP intactos (scope/receive/send)."""
    called: dict[str, Any] = {}
    expected_scope = {"type": "lifespan"}

    async def _inner(scope: Any, receive: Any, send: Any) -> None:
        called["scope"] = scope
        called["receive"] = receive
        called["send"] = send

    async def _receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def _send(_message: Any) -> None:
        return None

    mw = middleware_cls(_inner, **mw_kwargs)
    await mw(expected_scope, _receive, _send)
    assert called["scope"] is expected_scope
    assert called["receive"] is _receive
    assert called["send"] is _send
