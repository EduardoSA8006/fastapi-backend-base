"""Helpers compartilhados dos testes de storage."""

import pytest
from minio.error import S3Error

from app.shared import storage


def reset_storage_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zera o singleton preguiçoso do storage (client/spec/cache de buckets)."""
    monkeypatch.setattr(storage, "_client", None)
    monkeypatch.setattr(storage, "_client_spec", None)
    storage._known_buckets.clear()


def make_s3_error(code: str) -> S3Error:
    """Constrói um S3Error stub com o código dado (para simular falhas do MinIO)."""
    return S3Error(
        code=code,
        message="stub",
        resource="/x",
        request_id="r",
        host_id="h",
        response=None,  # type: ignore[arg-type]
    )
