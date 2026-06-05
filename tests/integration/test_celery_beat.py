"""Integração do pipeline COMPLETO: beat → broker → worker → result backend.

A integração do broker (test_celery_broker) cobre publicar→consumir; aqui o
PRÓPRIO BEAT é quem publica: um processo `celery beat` real, com heartbeat de
1s (CELERY_HEARTBEAT_SECONDS), dispara core.ping contra um Redis efêmero e um
worker embutido o executa — fecha o único trecho sem teste do pipeline.
"""

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import redis as redis_lib

from tests.integration.conftest import redis_container

pytestmark = pytest.mark.integration

_PASSWORD = "S3nhaTesteBeat123"


@pytest.fixture(scope="module")
def broker_base() -> Iterator[str]:
    with redis_container(_PASSWORD) as base:
        yield base


def test_beat_dispara_heartbeat_e_worker_executa(
    broker_base: str, tmp_path: Path
) -> None:
    from celery.contrib.testing.worker import start_worker

    from app.worker import celery_app

    broker_url = f"{broker_base}/0"
    backend_url = f"{broker_base}/1"

    original = {
        "broker_url": celery_app.conf.broker_url,
        "result_backend": celery_app.conf.result_backend,
        "task_always_eager": celery_app.conf.task_always_eager,
    }
    celery_app.conf.update(
        broker_url=broker_url, result_backend=backend_url, task_always_eager=False
    )

    # Beat REAL em subprocesso: importa app.worker do zero com heartbeat de
    # 1s via env — o beat_schedule dele aponta para o broker efêmero.
    beat = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.worker",
            "beat",
            "--loglevel=warning",
            f"--schedule={tmp_path / 'beat-schedule'}",
        ],
        env={
            **os.environ,
            "CELERY_BROKER_URL": broker_url,
            "CELERY_RESULT_BACKEND": backend_url,
            "CELERY_HEARTBEAT_SECONDS": "1",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with start_worker(
            celery_app, concurrency=1, perform_ping_check=False, loglevel="warning"
        ):
            # Resultado de task executada aparece no backend (DB 1) como
            # celery-task-meta-<id> com "pong" — prova de que o ciclo
            # beat→broker→worker→backend fechou.
            # type-ignore: redis-py 6.x (faixa do kombu) não tipa from_url.
            backend = redis_lib.from_url(  # type: ignore[no-untyped-call]
                backend_url, socket_timeout=5
            )
            deadline = time.monotonic() + 30
            executed = False
            while time.monotonic() < deadline:
                keys = backend.keys("celery-task-meta-*")
                # get() pode voltar None (TOCTOU: chave expira/some entre keys
                # e get) — guarda contra `in None`.
                value = backend.get(keys[0]) if keys else None
                if value is not None and b'"pong"' in value:
                    executed = True
                    break
                time.sleep(0.5)
            backend.close()
            assert executed, "beat não disparou (ou worker não executou) o core.ping"
    finally:
        beat.terminate()
        beat.wait(timeout=10)
        celery_app.conf.update(**original)
