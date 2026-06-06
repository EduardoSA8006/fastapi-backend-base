"""Guards fail-closed de produção (ENVIRONMENT=production) e warnings.

Cada teste quebra UM aspecto do baseline válido (make_prod_settings) para
exercitar o guard alvo. Os guards do Celery vivem em tests/test_celery.py.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import STRONG_REDIS_URI, make_prod_settings


def test_production_rejects_wildcard_trusted_hosts() -> None:
    import pytest

    with pytest.raises(ValueError, match="trusted_hosts"):
        create_app(make_prod_settings(trusted_hosts=["*"]))


def test_production_rejects_memory_rate_limit_store() -> None:
    import pytest

    with pytest.raises(ValueError, match="store compartilhado"):
        create_app(
            make_prod_settings(
                rate_limit_enabled=True, rate_limit_storage_uri="memory://"
            )
        )


def test_production_rejects_debug_true() -> None:
    import pytest

    with pytest.raises(ValueError, match="DEBUG"):
        create_app(make_prod_settings(debug=True))


def test_production_disables_docs() -> None:
    client = TestClient(create_app(make_prod_settings()))
    # O Host precisa ser permitido para chegar à rota.
    headers = {"host": "api.test"}
    assert client.get("/docs", headers=headers).status_code == 404
    assert client.get("/openapi.json", headers=headers).status_code == 404
    assert client.get("/api/v1/health", headers=headers).status_code == 200


def test_production_valid_config_boots() -> None:
    # Configuração de produção válida (redis + hosts reais + sem debug) sobe.
    app = create_app(
        make_prod_settings(
            rate_limit_enabled=True,
            rate_limit_storage_uri=STRONG_REDIS_URI,
        )
    )
    assert app is not None


def test_production_rejects_weak_redis_password() -> None:
    with pytest.raises(ValueError, match="Redis"):
        create_app(
            make_prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri="redis://:classup@redis:6379/0",
            )
        )


def test_production_rejects_redis_without_password() -> None:
    # Senha ausente (URL sem credencial) também é barrada — paridade com o banco.
    with pytest.raises(ValueError, match="Redis"):
        create_app(
            make_prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri="redis://redis:6379/0",
            )
        )


def test_production_rejects_weak_db_password() -> None:
    with pytest.raises(ValueError, match="fraca"):
        create_app(
            make_prod_settings(
                database_url="postgresql+psycopg://classup:classup@db:5432/classup"
            )
        )


def test_production_accepts_strong_db_password() -> None:
    app = create_app(
        make_prod_settings(
            database_url=(
                "postgresql+psycopg://classup:S3nhaForteAleatoria123@db:5432/classup"
            )
        )
    )
    assert app is not None


def test_production_without_trust_proxy_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="classup"):
        create_app(
            make_prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri=STRONG_REDIS_URI,
                trust_proxy=False,
            )
        )
    assert any("colapsa num único bucket" in r.getMessage() for r in caplog.records)


def test_production_with_trust_proxy_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # O risco simétrico: TRUST_PROXY=true sem proxy real permite spoofing de XFF.
    # Não falha o boot (config legítima atrás de LB), mas avisa explicitamente.
    with caplog.at_level(logging.WARNING, logger="classup"):
        create_app(
            make_prod_settings(
                rate_limit_enabled=True,
                rate_limit_storage_uri=STRONG_REDIS_URI,
                trust_proxy=True,
            )
        )
    assert any("forja o IP" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("weak", ["classup", "classup-minio-dev"])
def test_production_rejects_weak_minio_password(weak: str) -> None:
    # Paridade com Redis/DB: senha default/fraca do MinIO não pode ir a produção.
    # Inclui o default de dev "classup-minio-dev" (público no repositório).
    with pytest.raises(ValueError, match="MinIO"):
        create_app(make_prod_settings(minio_root_password=weak))


def test_production_rejects_minio_without_password() -> None:
    # Senha ausente (vazia) também é barrada — paridade com o banco/Redis.
    with pytest.raises(ValueError, match="MinIO"):
        create_app(make_prod_settings(minio_root_password=""))


def test_production_accepts_strong_minio_password() -> None:
    # Senha forte de MinIO passa pelo guard (configuração de produção válida).
    app = create_app(make_prod_settings(minio_root_password="OutraS3nhaForte456"))
    assert app is not None


@pytest.mark.parametrize("weak_user", ["classup", "admin", "minio", "root", ""])
def test_production_rejects_predictable_minio_user(weak_user: str) -> None:
    # Defesa-em-profundidade: usuário admin previsível do MinIO não vai a
    # produção (facilita enumeração se a porta vazar). Senha forte na base.
    with pytest.raises(ValueError, match="MinIO"):
        create_app(make_prod_settings(minio_root_user=weak_user))


def test_production_accepts_non_obvious_minio_user() -> None:
    # Usuário não-óbvio (e senha forte) passa pelo guard.
    app = create_app(make_prod_settings(minio_root_user="classup-svc-9b2c"))
    assert app is not None
