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
from app.shared.storage import StorageObjectNotFoundError, StorageUnavailableError
from tests.shared.conftest import reset_storage_singleton

pytestmark = pytest.mark.integration

_USER = "myapp-svc-test"
_PASSWORD = "S3nhaTesteMinio123"
_BUCKET = "myapp-test-files"


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
    reset_storage_singleton(monkeypatch)


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


async def test_novas_settings_reconstroem_o_client() -> None:
    # Cobre o rebuild de _get_client/_spec (storage.py:64-86): a mesma facade,
    # apontada para um endpoint DIFERENTE, precisa descartar o singleton e
    # reconstruir o Minio com a nova spec (senão testes/reload vazariam client).
    first = storage._get_client()
    assert storage._get_client() is first  # spec estável -> mesmo objeto
    changed = storage._spec().__class__(
        endpoint="outro-host:9000",
        access_key=_USER,
        secret_key=_PASSWORD,
        secure=False,
    )
    storage._known_buckets.add(_BUCKET)
    storage._client_spec = changed  # força divergência de spec
    rebuilt = storage._get_client()
    assert rebuilt is not first  # spec mudou -> client reconstruído
    assert storage._known_buckets == set()  # cache de buckets zerado no rebuild


async def test_credencial_invalida_mapeia_para_storage_unavailable(
    minio_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Endpoint REAL, mas secret_key ERRADA: o MinIO responde 403
    # (assinatura inválida) num HEAD de bucket dentro de _ensure_bucket. É um
    # S3Error fora de _NOT_FOUND_CODES → a facade DEVE mapear para
    # StorageUnavailableError (503 via handler), sem vazar a S3Error crua do SDK.
    ruim = Settings(
        minio_endpoint=minio_settings.minio_endpoint,
        minio_use_ssl=False,
        minio_root_user=_USER,
        minio_root_password="senha-totalmente-errada-999",
        minio_bucket=_BUCKET,
    )
    monkeypatch.setattr(storage, "get_settings", lambda: ruim)
    reset_storage_singleton(monkeypatch)

    with pytest.raises(StorageUnavailableError):
        await storage.put_object(
            bucket="bucket-cred-invalida",
            key="k",
            data=b"x",
            content_type="text/plain",
        )


async def test_delete_erro_inesperado_do_s3_vira_503() -> None:
    # Cobre o ramo de erro NÃO-notfound do delete (storage.py:160-164): um
    # remove_object contra um bucket inexistente devolve S3Error 'NoSuchBucket'
    # (fora de _NOT_FOUND_CODES) -> StorageUnavailableError (503 via handler).
    # Pré-semeia _known_buckets para _ensure_bucket não criar o bucket antes.
    storage._known_buckets.add("bucket-que-nunca-existiu")
    with pytest.raises(StorageUnavailableError):
        await storage.delete_object(bucket="bucket-que-nunca-existiu", key="k")
