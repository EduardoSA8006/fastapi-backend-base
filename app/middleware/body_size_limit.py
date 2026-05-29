from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejeita requisições cujo Content-Length excede max_body_size.

    O corpo com tamanho exatamente igual ao limite é aceito.
    """

    def __init__(self, app, max_body_size: int) -> None:
        if max_body_size <= 0:
            raise ValueError(
                f"max_body_size must be positive, got {max_body_size}"
            )
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                return JSONResponse(
                    {"detail": "Invalid Content-Length"}, status_code=400
                )
            if declared < 0:
                return JSONResponse(
                    {"detail": "Invalid Content-Length"}, status_code=400
                )
            if declared > self.max_body_size:
                return JSONResponse(
                    {"detail": "Request body too large"}, status_code=413
                )
        return await call_next(request)
