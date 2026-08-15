"""Unit da camada de storage: mapeamento de erros e contrato da facade.

O cliente MinIO é stubado (monkeypatch) — o comportamento contra um MinIO
REAL é coberto em tests/integration/test_minio.py.
"""

from typing import Any

import pytest

from app.core.config import Settings
from app.shared import storage
from app.shared.exceptions import NotFoundError, UnavailableError
from app.shared.storage import (
    StorageObjectNotFoundError,
    StorageUnavailableError,
)
from tests.shared.conftest import make_s3_error, reset_storage_singleton


class _StubClient:
    """Cliente mínimo: bucket existe; get/put/remove configuráveis."""

    def __init__(self, *, raise_code: str | None = None) -> None:
        self._raise_code = raise_code

    def bucket_exists(self, bucket: str) -> bool:
        return True

    def _maybe_raise(self) -> None:
        if self._raise_code is not None:
            raise make_s3_error(self._raise_code)

    def put_object(self, **kwargs: Any) -> None:
        self._maybe_raise()

    def get_object(self, bucket: str, key: str) -> Any:
        self._maybe_raise()

        class _Resp:
            def read(self) -> bytes:
                return b"conteudo"

            def close(self) -> None:
                pass

            def release_conn(self) -> None:
                pass

        return _Resp()

    def remove_object(self, bucket: str, key: str) -> None:
        self._maybe_raise()


@pytest.fixture(autouse=True)
def _reset_storage_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # Limpa o singleton/cache entre testes (estado de módulo).
    reset_storage_singleton(monkeypatch)


def _use_stub(monkeypatch: pytest.MonkeyPatch, stub: _StubClient) -> None:
    monkeypatch.setattr(storage, "_get_client", lambda: stub)


# --- hierarquia: erros do storage SÃO AppException (handler global cobre) ---


def test_erros_do_storage_herdam_da_hierarquia_compartilhada() -> None:
    assert issubclass(StorageObjectNotFoundError, NotFoundError)
    assert issubclass(StorageUnavailableError, UnavailableError)
    assert StorageObjectNotFoundError("x").status_code == 404
    assert StorageUnavailableError("x").status_code == 503


# --- mapeamento de erros do SDK ---


async def test_get_inexistente_vira_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_stub(monkeypatch, _StubClient(raise_code="NoSuchKey"))
    with pytest.raises(StorageObjectNotFoundError):
        await storage.get_object(bucket="b", key="nao-existe")


async def test_erro_de_conectividade_vira_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_stub(monkeypatch, _StubClient(raise_code="InternalError"))
    with pytest.raises(StorageUnavailableError):
        await storage.get_object(bucket="b", key="k")


async def test_delete_de_inexistente_e_idempotente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pós-condição desejada é "não existe": deletar o ausente é sucesso.
    _use_stub(monkeypatch, _StubClient(raise_code="NoSuchKey"))
    await storage.delete_object(bucket="b", key="ja-foi")  # não levanta


async def test_put_erro_vira_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_stub(monkeypatch, _StubClient(raise_code="AccessDenied"))
    with pytest.raises(StorageUnavailableError):
        await storage.put_object(
            bucket="b", key="k", data=b"x", content_type="text/plain"
        )


async def test_get_devolve_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_stub(monkeypatch, _StubClient())
    data = await storage.get_object(bucket="b", key="k")
    assert data == b"conteudo"


def test_minio_settings_defaults() -> None:
    # Defaults coerentes com o compose (rede interna, hostname `minio`).
    settings = Settings()
    assert settings.minio_endpoint == "minio:9000"
    assert settings.minio_use_ssl is False
    assert settings.minio_bucket == "myapp-files"
