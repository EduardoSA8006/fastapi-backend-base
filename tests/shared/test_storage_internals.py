"""Comportamento interno da facade de storage (cliente gravador).

Nascidos do mutation testing: os internals (_ensure_bucket, kwargs do put)
eram exercitados só pela integração — a suíte unitária (killer dos mutantes)
não fixava o comportamento. Aqui um stub GRAVADOR pina as interações.
"""

from typing import Any

import pytest
from minio.error import S3Error

from app.shared import storage
from app.shared.storage import StorageUnavailableError


def _s3_error(code: str) -> S3Error:
    return S3Error(
        code=code,
        message="stub",
        resource="/x",
        request_id="r",
        host_id="h",
        response=None,  # type: ignore[arg-type]
    )


class _RecordingClient:
    """Stub que grava todas as chamadas; configurável por cenário."""

    def __init__(
        self, *, bucket_exists: bool = True, make_bucket_code: str | None = None
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._bucket_exists = bucket_exists
        self._make_bucket_code = make_bucket_code

    def bucket_exists(self, bucket: str) -> bool:
        self.calls.append(("bucket_exists", (bucket,), {}))
        return self._bucket_exists

    def make_bucket(self, bucket: str) -> None:
        self.calls.append(("make_bucket", (bucket,), {}))
        if self._make_bucket_code is not None:
            raise _s3_error(self._make_bucket_code)

    def put_object(self, **kwargs: Any) -> None:
        self.calls.append(("put_object", (), kwargs))


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "_client", None)
    monkeypatch.setattr(storage, "_client_spec", None)
    storage._known_buckets.clear()


def _wire(monkeypatch: pytest.MonkeyPatch, client: _RecordingClient) -> None:
    monkeypatch.setattr(storage, "_get_client", lambda: client)


def test_ensure_bucket_cria_quando_falta_e_cacheia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RecordingClient(bucket_exists=False)
    _wire(monkeypatch, client)
    storage._ensure_bucket("meu-bucket")
    assert ("bucket_exists", ("meu-bucket",), {}) in client.calls
    assert ("make_bucket", ("meu-bucket",), {}) in client.calls
    assert "meu-bucket" in storage._known_buckets

    # Segunda chamada: cache — NENHUMA ida nova ao servidor.
    client.calls.clear()
    storage._ensure_bucket("meu-bucket")
    assert client.calls == []


def test_ensure_bucket_existente_nao_cria_mas_cacheia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RecordingClient(bucket_exists=True)
    _wire(monkeypatch, client)
    storage._ensure_bucket("b")
    assert ("make_bucket", ("b",), {}) not in client.calls
    assert "b" in storage._known_buckets


@pytest.mark.parametrize(
    "code", ["BucketAlreadyOwnedByYou", "BucketAlreadyOwnedByUs", "BucketAlreadyExists"]
)
def test_ensure_bucket_tolera_corrida_de_criacao(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    # Corrida: outro processo/thread criou entre o exists e o make. O código
    # REAL do MinIO é BucketAlreadyOwnedByYou (bug achado pela integração).
    client = _RecordingClient(bucket_exists=False, make_bucket_code=code)
    _wire(monkeypatch, client)
    storage._ensure_bucket("b")  # não levanta
    assert "b" in storage._known_buckets


def test_ensure_bucket_outro_erro_vira_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RecordingClient(bucket_exists=False, make_bucket_code="AccessDenied")
    _wire(monkeypatch, client)
    with pytest.raises(StorageUnavailableError):
        storage._ensure_bucket("b")
    assert "b" not in storage._known_buckets  # falha NÃO contamina o cache


async def test_put_envia_kwargs_corretos(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingClient(bucket_exists=True)
    _wire(monkeypatch, client)
    await storage.put_object(
        bucket="b", key="docs/a.txt", data=b"abc", content_type="text/plain"
    )
    puts = [c for c in client.calls if c[0] == "put_object"]
    assert len(puts) == 1
    kwargs = puts[0][2]
    assert kwargs["bucket_name"] == "b"
    assert kwargs["object_name"] == "docs/a.txt"
    assert kwargs["length"] == 3
    assert kwargs["content_type"] == "text/plain"
    assert kwargs["data"].read() == b"abc"
