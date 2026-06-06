from collections.abc import Sequence

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Permissions-Policy restritivo: desliga APIs do browser que a API não usa.
DEFAULT_PERMISSIONS_POLICY = (
    "accelerometer=(), autoplay=(), camera=(), display-capture=(), "
    "encrypted-media=(), fullscreen=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), midi=(), payment=(), "
    "picture-in-picture=(), usb=()"
)

# frame-ancestors 'none' complementa o X-Frame-Options (impede enquadramento).
DEFAULT_CSP = "default-src 'self'; frame-ancestors 'none'"


class SecurityHeadersMiddleware:
    """Injeta cabeçalhos de segurança em todas as respostas.

    Implementado como middleware ASGI puro (não BaseHTTPMiddleware) para não
    bufferizar respostas — preservando streaming/SSE/BackgroundTask. Os headers
    são injetados na mensagem `http.response.start`.

    O Content-Security-Policy é omitido apenas nos caminhos de documentação
    listados em `csp_exempt_paths` (correspondência EXATA, não por prefixo), que
    carregam assets de CDN e quebrariam sob `default-src 'self'`. Os demais
    cabeçalhos são aplicados sempre.
    """

    def __init__(
        self,
        app: ASGIApp,
        hsts_enabled: bool = False,
        csp: str = DEFAULT_CSP,
        permissions_policy: str = DEFAULT_PERMISSIONS_POLICY,
        csp_exempt_paths: Sequence[str] = ("/docs", "/redoc", "/openapi.json"),
    ) -> None:
        self.app = app
        self.hsts_enabled = hsts_enabled
        self.csp = csp
        self.permissions_policy = permissions_policy
        self.csp_exempt_paths = frozenset(csp_exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Correspondência EXATA: evita isentar caminhos como /docs-admin.
        apply_csp = scope["path"] not in self.csp_exempt_paths

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Cross-Origin-Resource-Policy"] = "same-origin"
                headers["Permissions-Policy"] = self.permissions_policy
                if apply_csp:
                    headers["Content-Security-Policy"] = self.csp
                if self.hsts_enabled:
                    headers["Strict-Transport-Security"] = (
                        "max-age=63072000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)
