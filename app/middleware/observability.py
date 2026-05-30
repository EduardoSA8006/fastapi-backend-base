import logging
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("classup.access")


class RequestContextMiddleware:
    """Atribui um X-Request-ID a cada requisição e loga o resultado.

    Middleware ASGI puro, posicionado como o mais externo:
    - reaproveita o `X-Request-ID` recebido ou gera um novo;
    - injeta o header na resposta;
    - loga toda requisição com método, caminho, status, id e IP do cliente.
      Rejeições (4xx) saem como WARNING e erros (5xx) como ERROR, facilitando a
      detecção de abuso/ataque.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = incoming or uuid4().hex

        client = scope.get("client")
        client_host = client[0] if client else "-"
        method = scope.get("method", "-")
        path = scope.get("path", "-")
        status_code = 0

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)

        log = "%s %s -> %s id=%s client=%s"
        args = (method, path, status_code, request_id, client_host)
        if status_code >= 500:
            logger.error(log, *args)
        elif status_code >= 400:
            logger.warning(log, *args)
        else:
            logger.info(log, *args)
