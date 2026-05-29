from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _ContentTooLarge(Exception):
    """Sinaliza que o corpo da requisição excedeu o limite durante a leitura."""


class BodySizeLimitMiddleware:
    """Rejeita requisições cujo corpo excede max_body_size (bytes).

    Verifica o header Content-Length como fast-path e, de forma autoritativa,
    conta os bytes reais do stream — fechando o bypass via chunked/sem
    Content-Length. O corpo com tamanho exatamente igual ao limite é aceito.
    """

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        if max_body_size <= 0:
            raise ValueError(
                f"max_body_size must be positive, got {max_body_size}"
            )
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                await self._reject(scope, receive, send, 400, "Invalid Content-Length")
                return
            if declared < 0:
                await self._reject(scope, receive, send, 400, "Invalid Content-Length")
                return
            if declared > self.max_body_size:
                await self._reject(
                    scope, receive, send, 413, "Request body too large"
                )
                return

        total = 0
        response_started = False

        async def wrapped_receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_body_size:
                    raise _ContentTooLarge()
            return message

        async def wrapped_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        except _ContentTooLarge:
            if response_started:
                # Resposta já iniciada; não há como enviar 413 de forma limpa.
                raise
            await self._reject(scope, receive, send, 413, "Request body too large")

    @staticmethod
    async def _reject(
        scope: Scope, receive: Receive, send: Send, status_code: int, detail: str
    ) -> None:
        response = JSONResponse({"detail": detail}, status_code=status_code)
        await response(scope, receive, send)
