import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("classup.errors")

# Corpo fixo e pré-serializado: o caminho de erro não depende de json.dumps
# nem de nada que possa falhar de novo (um boundary que quebra não serve).
_BODY = b'{"detail":"Erro interno."}'


class ErrorBoundaryMiddleware:
    """Converte exceções não-tratadas em 500 JSON padronizado, de DENTRO da pilha.

    Sem isto, a exceção propaga até o ServerErrorMiddleware do Starlette (o
    mais externo de todos), que responde text/plain "Internal Server Error"
    FORA da nossa pilha — sem security headers, sem X-Request-ID e fora do
    contrato {"detail": ...}. Aqui a resposta nasce dentro da pilha, então
    sai pelo SecurityHeaders (blindada) e pelo RequestContext (access log).

    Também registra a causa (stacktrace) com o request_id — a única
    correlação entre o 500 visto pelo cliente e o erro real.

    Middleware ASGI puro (convenção do projeto: preserva streaming e evita
    os custos do BaseHTTPMiddleware).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_tracking(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking)
        except Exception:
            # request_id foi colocado no scope pelo RequestContextMiddleware
            # (mais externo) — correlaciona este log com a access line.
            request_id = scope.get("state", {}).get("request_id", "-")
            path = scope.get("path", "-")
            logger.exception(
                f"Exceção não tratada em {path} id={request_id}",
                extra={"request_id": request_id, "path": path},
            )
            if response_started:
                # A resposta já começou a ser enviada: não há como substituí-la
                # por um 500 limpo — propaga para a conexão ser encerrada.
                raise
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": _BODY})
