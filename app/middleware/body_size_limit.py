from collections import deque

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _ContentTooLarge(Exception):
    """Sinaliza que o corpo da requisição excedeu o limite durante a leitura."""


class BodySizeLimitMiddleware:
    """Rejeita requisições cujo corpo excede max_body_size (bytes).

    O controle é autoritativo e independe de o handler consumir o corpo:

    - Com `Content-Length`: fast-path — rejeita já pelo header declarado.
    - Sem `Content-Length` (ex.: `Transfer-Encoding: chunked`): o corpo é
      drenado proativamente ANTES de invocar a aplicação, cortando no limite.
      Isso fecha o bypass em que um endpoint que não lê o corpo (ex.: /health)
      nunca dispararia a contagem. Como o corte é exatamente no limite, nunca
      bufferizamos um stream gigante/infinito — não reintroduz o DoS.

    O corpo com tamanho exatamente igual ao limite é aceito. Slowloris (corpo
    enviado lentamente) continua sendo responsabilidade de timeouts na borda /
    no uvicorn — está fora do escopo de um limite de *tamanho*.
    """

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        if max_body_size <= 0:
            raise ValueError(f"max_body_size must be positive, got {max_body_size}")
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            # RFC 9110: Content-Length é um inteiro não-negativo, sem espaços.
            if not content_length.isdigit():
                await self._reject(scope, receive, send, 400, "Invalid Content-Length")
                return
            if int(content_length) > self.max_body_size:
                # Não drenamos: o tamanho já é conhecido e excede o limite —
                # ler os bytes reintroduziria o DoS. O servidor encerra a
                # conexão após o 413.
                await self._reject(scope, receive, send, 413, "Request body too large")
                return
            # Content-Length válido e dentro do limite: o tamanho já está
            # delimitado pelo header. Conta de forma lazy (defesa extra caso os
            # bytes reais excedam o Content-Length declarado).
            await self._serve_with_lazy_count(scope, receive, send)
            return

        # Sem Content-Length: a contagem lazy não bastaria, pois um handler que
        # não lê o corpo nunca chamaria receive(). Drena proativamente.
        await self._serve_with_eager_drain(scope, receive, send)

    async def _serve_with_lazy_count(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Conta bytes conforme a aplicação os lê (caso Content-Length presente)."""
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
                # Headers já foram enviados; não há como trocar o status para
                # 413. Encerra o corpo da resposta de forma limpa, sem corromper
                # o protocolo ASGI.
                await send(
                    {"type": "http.response.body", "body": b"", "more_body": False}
                )
                return
            await self._reject(scope, receive, send, 413, "Request body too large")

    async def _serve_with_eager_drain(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Drena o corpo até o limite antes de invocar a app, então faz replay.

        Cobre o caso sem Content-Length (chunked) de forma autoritativa,
        inclusive para endpoints que não consomem o corpo.
        """
        buffered: deque[Message] = deque()
        total = 0
        more_body = True

        while more_body:
            message = await receive()
            if message["type"] != "http.request":
                # http.disconnect (ou outro evento): repassa e encerra o pré-leitura.
                buffered.append(message)
                break
            total += len(message.get("body", b""))
            if total > self.max_body_size:
                await self._reject(scope, receive, send, 413, "Request body too large")
                return
            buffered.append(message)
            more_body = message.get("more_body", False)

        async def replay_receive() -> Message:
            # Reentrega os chunks já lidos; depois delega ao receive real
            # (ex.: para futuros http.disconnect).
            if buffered:
                return buffered.popleft()
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(
        scope: Scope, receive: Receive, send: Send, status_code: int, detail: str
    ) -> None:
        response = JSONResponse({"detail": detail}, status_code=status_code)
        await response(scope, receive, send)
