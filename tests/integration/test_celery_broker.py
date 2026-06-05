"""Integração do Celery com broker REAL (Redis via testcontainers).

A suíte unitária usa task_always_eager (in-process, sem broker): as flags de
confiabilidade (acks_late, json-only, result backend) são apenas asserções de
config lá. Aqui o round-trip acontece de verdade: task publicada no broker,
consumida por um worker embutido e resultado lido do result backend.
"""

from collections.abc import Iterator

import pytest

from tests.integration.conftest import redis_container

pytestmark = pytest.mark.integration

_PASSWORD = "S3nhaTesteCelery123"


@pytest.fixture(scope="module")
def broker_base() -> Iterator[str]:
    with redis_container(_PASSWORD) as base:
        yield base


def test_ping_round_trip_pelo_broker_real(broker_base: str) -> None:
    from celery.contrib.testing.worker import start_worker

    from app.worker import celery_app, ping

    # Reaponta o app (criado no import com os defaults de dev) para o broker
    # efêmero — DB 0 broker, DB 1 results, como no compose. Restaura ao final:
    # o objeto é de módulo e outros testes (eager) o reutilizam.
    original = {
        "broker_url": celery_app.conf.broker_url,
        "result_backend": celery_app.conf.result_backend,
        "task_always_eager": celery_app.conf.task_always_eager,
    }
    celery_app.conf.update(
        broker_url=f"{broker_base}/0",
        result_backend=f"{broker_base}/1",
        task_always_eager=False,
    )
    try:
        with start_worker(
            celery_app,
            concurrency=1,
            perform_ping_check=False,
            loglevel="warning",
        ):
            result = ping.delay()
            # get() lê do result backend — valida também a serialização
            # json-only na volta (result_accept_content).
            assert result.get(timeout=30) == "pong"
    finally:
        celery_app.conf.update(**original)
