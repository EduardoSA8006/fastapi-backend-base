import logging
import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _client() -> TestClient:
    return TestClient(
        create_app(
            Settings(
                rate_limit_storage_uri="memory://",
                trusted_hosts=["testserver"],
            )
        )
    )


def test_request_id_generated_when_absent() -> None:
    response = _client().get("/api/v1/health")
    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    assert len(request_id) > 0


def test_request_id_echoed_when_provided() -> None:
    response = _client().get("/api/v1/health", headers={"X-Request-ID": "fixed-id-123"})
    assert response.headers.get("X-Request-ID") == "fixed-id-123"


def test_invalid_request_id_is_replaced_by_safe_uuid() -> None:
    # IDs com caracteres inseguros (espaços, controle) são descartados e um
    # uuid seguro é gerado — base do fix de log injection (CRLF não passa).
    bad = "id com espacos e ; simbolos"
    response = _client().get("/api/v1/health", headers={"X-Request-ID": bad})
    returned = response.headers["X-Request-ID"]
    assert returned != bad
    assert re.fullmatch(r"[0-9a-f]{32}", returned) is not None


def test_overlong_request_id_is_replaced() -> None:
    response = _client().get("/api/v1/health", headers={"X-Request-ID": "a" * 200})
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"]) is not None


def test_client_ip_not_logged_when_disabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(
        create_app(
            Settings(
                rate_limit_storage_uri="memory://",
                trusted_hosts=["testserver"],
                log_client_ip=False,
            )
        )
    )
    with caplog.at_level(logging.INFO, logger="classup.access"):
        client.get("/api/v1/health")
    access_lines = [r.getMessage() for r in caplog.records]
    assert any("client=-" in line for line in access_lines)


def test_client_ip_logged_from_xff_when_trust_proxy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Atrás de proxy confiável, o log usa o IP real do X-Forwarded-For (mesma
    # derivação do rate-limit), não o IP da conexão.
    client = TestClient(
        create_app(
            Settings(
                rate_limit_storage_uri="memory://",
                trusted_hosts=["testserver"],
                trust_proxy=True,
                num_trusted_proxies=1,
            )
        )
    )
    with caplog.at_level(logging.INFO, logger="classup.access"):
        client.get("/api/v1/health", headers={"X-Forwarded-For": "9.9.9.9"})
    assert any("client=9.9.9.9" in r.getMessage() for r in caplog.records)


def test_client_ip_ignores_xff_when_proxy_untrusted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Sem trust_proxy, um X-Forwarded-For forjado não deve aparecer no log.
    with caplog.at_level(logging.INFO, logger="classup.access"):
        _client().get("/api/v1/health", headers={"X-Forwarded-For": "9.9.9.9"})
    assert not any("client=9.9.9.9" in r.getMessage() for r in caplog.records)


def test_rejection_is_logged_as_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(
        create_app(
            Settings(
                rate_limit_storage_uri="memory://",
                trusted_hosts=["example.com"],  # rejeita o host "testserver"
            )
        )
    )
    with caplog.at_level(logging.WARNING, logger="classup.access"):
        response = client.get("/api/v1/health")
    assert response.status_code == 400
    assert any("-> 400" in record.getMessage() for record in caplog.records)


# --- Branch defensivo: crash que escapa do ErrorBoundary ---


async def test_access_log_registra_500_quando_excecao_escapa(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # O ErrorBoundary cobre o miolo da pilha, mas não a si mesmo nem ao
    # SecurityHeaders: se uma exceção escapar até o RequestContext, a access
    # line TEM de sair (status 500) e a exceção propaga.
    import logging as _logging

    from app.core.middleware.observability import RequestContextMiddleware

    async def _raising_app(scope: object, receive: object, send: object) -> None:
        raise RuntimeError("escapou do boundary")

    middleware = RequestContextMiddleware(_raising_app)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/_escape",
        "headers": [],
        "client": ("1.2.3.4", 1234),
    }

    from starlette.types import Message

    async def _receive() -> Message:
        return {"type": "http.request"}

    async def _send(message: Message) -> None:  # pragma: no cover
        pass

    with (
        caplog.at_level(_logging.ERROR, logger="classup.access"),
        pytest.raises(RuntimeError, match="escapou"),
    ):
        await middleware(scope, _receive, _send)
    records = [r for r in caplog.records if getattr(r, "status", None) == 500]
    assert records, "access line não saiu para o crash que escapou"
    assert records[0].path == "/_escape"  # type: ignore[attr-defined]


async def test_passa_direto_scope_nao_http() -> None:
    from starlette.types import Message

    from app.core.middleware.observability import RequestContextMiddleware

    called: dict[str, bool] = {}

    async def _inner(scope: object, receive: object, send: object) -> None:
        called["ok"] = True
        # Identidade dos argumentos: o passthrough repassa EXATAMENTE o que
        # recebeu (mutantes scope/receive/send -> None sobreviviam sem isto).
        assert scope is expected_scope
        assert receive is _receive
        assert send is _send

    async def _receive() -> Message:
        return {"type": "lifespan.startup"}

    async def _send(message: Message) -> None:
        pass

    expected_scope = {"type": "lifespan"}
    await RequestContextMiddleware(_inner)(expected_scope, _receive, _send)
    assert called == {"ok": True}
