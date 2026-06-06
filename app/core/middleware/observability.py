import logging
import re
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.client_ip import resolve_client_ip

logger = logging.getLogger("myapp.access")

# X-Request-ID aceito do cliente: apenas caracteres seguros e tamanho limitado.
# Evita CRLF/controle (log injection) e IDs absurdamente longos.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestContextMiddleware:
    """Atribui um X-Request-ID a cada requisição e loga o resultado.

    Middleware ASGI puro, posicionado como o mais externo:
    - reaproveita o `X-Request-ID` recebido ou gera um novo;
    - injeta o header na resposta;
    - loga toda requisição com método, caminho, status, id e IP do cliente.
      Rejeições (4xx) saem como WARNING e erros (5xx) como ERROR, facilitando a
      detecção de abuso/ataque.
    """

    def __init__(
        self,
        app: ASGIApp,
        log_client_ip: bool = True,
        trust_proxy: bool = False,
        num_trusted_proxies: int = 1,
    ) -> None:
        self.app = app
        self.log_client_ip = log_client_ip
        self.trust_proxy = trust_proxy
        self.num_trusted_proxies = num_trusted_proxies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                candidate = value.decode("latin-1")
                # Só aceita o ID do cliente se for seguro; senão, ignora e gera
                # um novo (impede log injection via CR/LF e IDs gigantes).
                if _REQUEST_ID_RE.match(candidate):
                    incoming = candidate
                break
        request_id = incoming or uuid4().hex
        # Publica o id no scope (Request.state nos handlers; lido também pelo
        # ErrorBoundary) — correlaciona qualquer log interno com a access line.
        scope.setdefault("state", {})["request_id"] = request_id

        # IP é dado pessoal (LGPD/GDPR): só registra se habilitado.
        if self.log_client_ip:
            client = scope.get("client")
            connection_ip = client[0] if client else "-"
            # Atrás de proxy confiável, loga o IP real (mesma derivação do
            # rate-limit) para que log e limite concordem sobre o cliente.
            forwarded = None
            if self.trust_proxy:
                for name, value in scope.get("headers", []):
                    if name == b"x-forwarded-for":
                        forwarded = value.decode("latin-1")
                        break
            client_host = resolve_client_ip(
                forwarded, connection_ip, self.trust_proxy, self.num_trusted_proxies
            )
        else:
            client_host = "-"
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

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            # Crash que escapou da pilha (o ErrorBoundary cobre o miolo, mas
            # não a si mesmo nem ao SecurityHeaders): a requisição NÃO pode
            # sumir do access log — registra como 500 e propaga.
            if status_code == 0:
                status_code = 500
            raise
        finally:
            message = (
                f"{method} {path} -> {status_code} id={request_id} client={client_host}"
            )
            # Campos estruturados (correlação em SIEM); o JsonFormatter escapa.
            extra = {
                "method": method,
                "path": path,
                "status": status_code,
                "request_id": request_id,
                "client": client_host,
            }
            if status_code >= 500:
                logger.error(message, extra=extra)
            elif status_code >= 400:
                logger.warning(message, extra=extra)
            else:
                logger.info(message, extra=extra)
