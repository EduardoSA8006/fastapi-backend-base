"""Cenários de FALHA e recuperação contra infra real.

A suíte de integração feliz prova que funciona; estes provam o
comportamento sob degradação — onde decisões de segurança (fail-closed)
realmente aparecem.
"""

import asyncio
import socket

import pytest
from fastapi.testclient import TestClient
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from app.main import create_app
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

_PASSWORD = "S3nhaTesteFalha123"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _redis_on_port(port: int) -> DockerContainer:
    return (
        DockerContainer("redis:7-alpine")
        .with_command(f"redis-server --requirepass {_PASSWORD}")
        .with_bind_ports(6379, port)
    )


def test_rate_limit_fail_closed_quando_redis_cai_no_meio() -> None:
    # Transição viva: rate-limit funcionando -> Redis morre -> fail-closed
    # (500 padronizado, não fail-open) e /ready reporta o store degradado.
    # Porta aleatória do Docker (sem TOCTOU) — porta fixa só é necessária no
    # teste de recuperação, onde "voltar na MESMA porta" é a semântica.
    container = (
        DockerContainer("redis:7-alpine")
        .with_command(f"redis-server --requirepass {_PASSWORD}")
        .with_exposed_ports(6379)
    )
    container.start()
    stopped = False
    try:
        wait_for_logs(container, "Ready to accept connections", timeout=30)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        app = create_app(
            make_settings(
                rate_limit_enabled=True,
                rate_limit_default="100/minute",
                rate_limit_storage_uri=f"redis://:{_PASSWORD}@{host}:{port}/0",
                readiness_cache_seconds=0.0,
            )
        )

        @app.get("/_rl")
        def _rl() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/_rl").status_code == 200
        assert client.get("/api/v1/ready").json()["checks"]["redis"] == "ok"

        container.stop()
        stopped = True

        # Fail-closed: a rota limitada NÃO abre; o 500 sai padronizado e
        # blindado (ErrorBoundary), sem vazar o erro de conexão.
        response = client.get("/_rl")
        assert response.status_code == 500
        assert response.json() == {"detail": "Erro interno."}
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        # Probes continuam diagnosticando: liveness vivo, readiness 503.
        assert client.get("/api/v1/health").status_code == 200
        ready = client.get("/api/v1/ready")
        assert ready.status_code == 503
        assert ready.json()["checks"]["redis"] == "error"
    finally:
        if not stopped:
            container.stop()


def test_rate_limit_se_recupera_quando_redis_volta() -> None:
    # Recuperação: Redis morre e VOLTA (mesma porta) — o pool do limiter
    # reconecta sozinho e a contagem volta a funcionar, sem reboot da app.
    port = _free_port()
    first = _redis_on_port(port)
    first.start()
    second = None
    first_stopped = False
    try:
        wait_for_logs(first, "Ready to accept connections", timeout=30)
        app = create_app(
            make_settings(
                rate_limit_enabled=True,
                rate_limit_default="2/minute",
                rate_limit_storage_uri=f"redis://:{_PASSWORD}@127.0.0.1:{port}/0",
            )
        )

        @app.get("/_rl")
        def _rl() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/_rl").status_code == 200
        first.stop()
        first_stopped = True
        assert client.get("/_rl").status_code == 500  # degradado (fail-closed)

        second = _redis_on_port(port)
        second.start()
        wait_for_logs(second, "Ready to accept connections", timeout=30)

        # Reconectado: volta a contar do zero (store novo) — 2 passam, 3º 429.
        codes = [client.get("/_rl").status_code for _ in range(3)]
        assert codes == [200, 200, 429]
    finally:
        if not first_stopped:
            first.stop()
        if second is not None:
            second.stop()


def test_storage_integro_sob_concorrencia(monkeypatch: pytest.MonkeyPatch) -> None:
    # N writes paralelos (chaves distintas) no MinIO real: nenhum corrompe,
    # nenhum se perde — o caminho asyncio.to_thread + SDK é seguro sob
    # concorrência do mesmo processo.
    from app.core.config import Settings
    from app.shared import storage

    user, password = "myapp-svc-test", "S3nhaTesteMinio123"
    container = (
        DockerContainer("quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z")
        .with_env("MINIO_ROOT_USER", user)
        .with_env("MINIO_ROOT_PASSWORD", password)
        .with_command("server /data")
        .with_exposed_ports(9000)
    )
    with container:
        wait_for_logs(container, "API:", timeout=60)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9000)
        settings = Settings(
            minio_endpoint=f"{host}:{port}",
            minio_use_ssl=False,
            minio_root_user=user,
            minio_root_password=password,
        )
        monkeypatch.setattr(storage, "get_settings", lambda: settings)
        monkeypatch.setattr(storage, "_client", None)
        monkeypatch.setattr(storage, "_client_spec", None)
        storage._known_buckets.clear()
        try:

            async def _hammer() -> None:
                bucket = "concurrency-test"
                n = 25
                await asyncio.gather(
                    *(
                        storage.put_object(
                            bucket=bucket,
                            key=f"k/{i}",
                            data=f"conteudo-{i}".encode(),
                            content_type="text/plain",
                        )
                        for i in range(n)
                    )
                )
                results = await asyncio.gather(
                    *(storage.get_object(bucket=bucket, key=f"k/{i}") for i in range(n))
                )
                assert [r.decode() for r in results] == [
                    f"conteudo-{i}" for i in range(n)
                ]

            asyncio.run(_hammer())
        finally:
            storage._known_buckets.clear()
