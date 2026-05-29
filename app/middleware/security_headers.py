from collections.abc import Sequence

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Injeta cabeçalhos de segurança em todas as respostas.

    O Content-Security-Policy é omitido nos caminhos de documentação
    (Swagger/ReDoc), que carregam assets de CDN e quebrariam sob
    `default-src 'self'`. Os demais cabeçalhos são aplicados sempre.
    """

    def __init__(
        self,
        app,
        hsts_enabled: bool = False,
        csp: str = "default-src 'self'",
        csp_exempt_paths: Sequence[str] = ("/docs", "/redoc", "/openapi.json"),
    ) -> None:
        super().__init__(app)
        self.hsts_enabled = hsts_enabled
        self.csp = csp
        self.csp_exempt_paths = tuple(csp_exempt_paths)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if not request.url.path.startswith(self.csp_exempt_paths):
            response.headers["Content-Security-Policy"] = self.csp
        if self.hsts_enabled:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains"
            )
        return response
