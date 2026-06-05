"""Integração com MinIO REAL (testcontainers).

Exercita a facade de storage de ponta a ponta: bucket lazy, put/get/delete,
overwrite e 404 — contra o servidor de verdade (autenticação, S3 API).
"""

from collections.abc import Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from app.core.config import Settings
from app.shared import storage
from app.shared.storage import StorageObjectNotFoundError

pytestmark = pytest.mark.integration

_USER = "classup-svc-test"
_PASSWORD = "S3nhaTesteMinio123"
_BUCKET = "classup-test-files"


@pytest.fixture(scope="module")
def minio_settings() -> Iterator[Settings]:
    container = (
        DockerContainer("quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z")
        .with_env("MINIO_ROOT_USER", _USER)
        .with_env("MINIO_ROOT_PASSWORD", _PASSWORD)
        .with_command("server /data")
        .with_exposed_ports(9000)
    )
    with container:
        wait_for_logs(container, "API:", timeout=60)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9000)
        yield Settings(
            minio_endpoint=f"{host}:{port}",
            minio_use_ssl=False,
            minio_root_user=_USER,
            minio_root_password=_PASSWORD,
            minio_bucket=_BUCKET,
        )


@pytest.fixture(autouse=True)
def _wire_storage(minio_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    # A facade lê settings de get_settings(); aponta para o container efêmero
    # e zera o singleton/cache para reconstruir com o endpoint do teste.
    monkeypatch.setattr(storage, "get_settings", lambda: minio_settings)
    monkeypatch.setattr(storage, "_client", None)
    monkeypatch.setattr(storage, "_client_spec", None)
    storage._known_buckets.clear()


async def test_put_get_delete_round_trip() -> None:
    # Bucket criado lazy no primeiro uso (não existe no container recém-nato).
    await storage.put_object(
        bucket=_BUCKET, key="docs/ola.txt", data=b"ola mundo", content_type="text/plain"
    )
    assert await storage.get_object(bucket=_BUCKET, key="docs/ola.txt") == b"ola mundo"
    await storage.delete_object(bucket=_BUCKET, key="docs/ola.txt")
    with pytest.raises(StorageObjectNotFoundError):
        await storage.get_object(bucket=_BUCKET, key="docs/ola.txt")


async def test_put_sobrescreve_chave_existente() -> None:
    await storage.put_object(
        bucket=_BUCKET, key="k", data=b"v1", content_type="text/plain"
    )
    await storage.put_object(
        bucket=_BUCKET, key="k", data=b"v2", content_type="text/plain"
    )
    assert await storage.get_object(bucket=_BUCKET, key="k") == b"v2"


async def test_get_de_chave_inexistente_da_404_tipado() -> None:
    with pytest.raises(StorageObjectNotFoundError):
        await storage.get_object(bucket=_BUCKET, key="nunca/existiu")


async def test_delete_idempotente_contra_servidor_real() -> None:
    await storage.delete_object(bucket=_BUCKET, key="nunca/existiu")  # não levanta
