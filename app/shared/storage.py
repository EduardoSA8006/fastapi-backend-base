"""Cliente de armazenamento de objetos (MinIO) — facade cross-feature.

Toda feature acessa o storage por aqui: bucket-lazy, ponte sync→async e
mapeamento de erros vivem em UM lugar. Contrato de rede (ver spec do MinIO):
o MinIO não tem porta publicada — só o backend o alcança, e o conteúdo é
servido pelo proxy do FastAPI. NUNCA exponha presigned URL a cliente público.

Decisões (espelham o padrão do portfolio, adaptadas à hierarquia do myapp):
- O SDK `minio` é síncrono; handlers são async → cada operação roda em
  `asyncio.to_thread`, uma única vez aqui (call sites ficam limpos).
- Bucket é garantido LAZY no primeiro uso (sem acoplamento de startup: MinIO
  fora do ar não impede a API de subir; o readiness é quem reporta infra).
- Erros do SDK viram exceções da hierarquia compartilhada
  (StorageObjectNotFoundError -> 404, StorageUnavailableError -> 503): o
  handler global já converte e loga — routers não importam minio.error.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings
from app.shared.exceptions import NotFoundError, UnavailableError

logger = logging.getLogger("myapp.storage")


class StorageObjectNotFoundError(NotFoundError):
    """Chave inexistente no bucket → 404 (via handler global)."""

    detail = "Arquivo não encontrado."


class StorageUnavailableError(UnavailableError):
    """MinIO inalcançável ou erro inesperado do S3 → 503 (via handler global)."""

    detail = "Armazenamento temporariamente indisponível."


# Códigos S3 que significam "a chave não existe" (não é falha de infra).
_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchObject"}


@dataclass(frozen=True)
class _ClientSpec:
    endpoint: str
    access_key: str
    secret_key: str
    secure: bool


_client: Minio | None = None
_client_spec: _ClientSpec | None = None
_known_buckets: set[str] = set()


def _spec() -> _ClientSpec:
    settings = get_settings()
    return _ClientSpec(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_root_user,
        secret_key=settings.minio_root_password,
        secure=settings.minio_use_ssl,
    )


def _get_client() -> Minio:
    """Singleton preguiçoso; reconstruído se as settings mudarem (testes)."""
    global _client, _client_spec
    spec = _spec()
    if _client is None or _client_spec != spec:
        _client = Minio(
            endpoint=spec.endpoint,
            access_key=spec.access_key,
            secret_key=spec.secret_key,
            secure=spec.secure,
        )
        _client_spec = spec
        _known_buckets.clear()
    return _client


def _ensure_bucket(bucket: str) -> None:
    """Cria o bucket se faltar. Idempotente; cacheado após a primeira ida."""
    if bucket in _known_buckets:
        return
    client = _get_client()
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info("storage.bucket.created", extra={"bucket": bucket})
        _known_buckets.add(bucket)
    except S3Error as exc:
        # Corrida de criação (réplicas no boot OU puts concorrentes no mesmo
        # processo) = sucesso. O código REAL do MinIO/S3 é
        # "BucketAlreadyOwnedByYou" (descoberto pelo teste de concorrência —
        # a grafia "...OwnedByUs" não existe e deixava a corrida explodir);
        # os demais cobrem variações de implementações S3-compat.
        if exc.code in {
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyOwnedByUs",
            "BucketAlreadyExists",
        }:
            _known_buckets.add(bucket)
            return
        logger.exception("storage.bucket.ensure_failed", extra={"bucket": bucket})
        raise StorageUnavailableError() from exc


def _put_sync(bucket: str, key: str, data: bytes, content_type: str) -> None:
    _ensure_bucket(bucket)
    try:
        _get_client().put_object(
            bucket_name=bucket,
            object_name=key,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
    except S3Error as exc:
        logger.exception(
            "storage.put.failed", extra={"bucket": bucket, "key": key, "code": exc.code}
        )
        raise StorageUnavailableError() from exc


def _get_sync(bucket: str, key: str) -> bytes:
    _ensure_bucket(bucket)
    response = None
    try:
        response = _get_client().get_object(bucket, key)
        return bytes(response.read())
    except S3Error as exc:
        if exc.code in _NOT_FOUND_CODES:
            raise StorageObjectNotFoundError() from exc
        logger.exception(
            "storage.get.failed", extra={"bucket": bucket, "key": key, "code": exc.code}
        )
        raise StorageUnavailableError() from exc
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _delete_sync(bucket: str, key: str) -> None:
    _ensure_bucket(bucket)
    try:
        _get_client().remove_object(bucket, key)
    except S3Error as exc:
        # Idempotente: a pós-condição ("não existe") já vale.
        if exc.code in _NOT_FOUND_CODES:
            return
        logger.exception(
            "storage.delete.failed",
            extra={"bucket": bucket, "key": key, "code": exc.code},
        )
        raise StorageUnavailableError() from exc


async def put_object(*, bucket: str, key: str, data: bytes, content_type: str) -> None:
    """Grava um objeto (sobrescreve se a chave existir).

    Validação de tamanho/conteúdo é responsabilidade do CHAMADOR (service da
    feature) — esta camada não sanitiza.
    """
    await asyncio.to_thread(_put_sync, bucket, key, data, content_type)


async def get_object(*, bucket: str, key: str) -> bytes:
    """Lê os bytes do objeto. 404 tipado para chave inexistente.

    Drena o stream dentro da thread e devolve bytes — adequado a payloads
    pequenos; uploads/downloads grandes pedirão interface de streaming.
    """
    return await asyncio.to_thread(_get_sync, bucket, key)


async def delete_object(*, bucket: str, key: str) -> None:
    """Remove o objeto. Chave inexistente = sucesso (idempotente)."""
    await asyncio.to_thread(_delete_sync, bucket, key)
