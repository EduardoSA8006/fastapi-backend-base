"""Integração do pipeline completo: scheduler -> broker -> worker -> marcador.

Um `taskiq scheduler` REAL (subprocesso) publica core.ping na cadência de 1s
(TASKIQ_HEARTBEAT_SECONDS=1); um `taskiq worker` REAL (subprocesso) consome e a
task grava o marcador `myapp:taskiq:heartbeat` no Redis do broker (DB 0) — prova
de que o ciclo fechou de ponta a ponta.

Subprocessos (e não broker in-process) porque a suíte força InMemoryBroker
(TASKIQ_IN_MEMORY=true): cada subprocesso recebe um env EXPLÍCITO com
TASKIQ_IN_MEMORY=false + URLs apontando para o container, então importam
`app.worker` e constroem o broker Redis REAL a partir desse env. É a task real
do app (com a escrita do marcador, que só ocorre quando `not taskiq_in_memory`)
que exercitamos aqui.
"""

import os
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
import redis as redis_lib

from tests.integration.conftest import redis_container

pytestmark = pytest.mark.integration

_PASSWORD = "S3nhaTesteScheduler123"


@pytest.fixture(scope="module")
def broker_base() -> Iterator[str]:
    with redis_container(_PASSWORD) as base:
        yield base


def _spawn(args: list[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "taskiq", *args],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_scheduler_dispara_heartbeat_e_worker_grava_marcador(broker_base: str) -> None:
    # Env explícito: liga o broker REAL (fura o TASKIQ_IN_MEMORY=true da suíte)
    # e aponta broker/backend para o container. ENVIRONMENT vem herdado do
    # os.environ (development), então os guards de segurança não disparam.
    env = {
        **os.environ,
        "TASKIQ_BROKER_URL": f"{broker_base}/0",
        "TASKIQ_RESULT_BACKEND": f"{broker_base}/1",
        "TASKIQ_HEARTBEAT_SECONDS": "1",
        "TASKIQ_IN_MEMORY": "false",
    }

    worker = _spawn(
        [
            "worker",
            "app.worker:broker",
            "--ack-type",
            "when_executed",
            "--workers",
            "1",
        ],
        env,
    )
    sched = _spawn(["scheduler", "app.worker:scheduler"], env)

    try:
        client = redis_lib.from_url(f"{broker_base}/0", socket_timeout=5)
        deadline = time.monotonic() + 30
        seen = False
        while time.monotonic() < deadline:
            # Se algum subprocesso morreu (ex.: import quebrado), falha cedo
            # com contexto em vez de esperar o deadline inteiro.
            for name, proc in (("worker", worker), ("scheduler", sched)):
                if proc.poll() is not None:
                    pytest.fail(
                        f"subprocesso {name} morreu (exit={proc.returncode}) "
                        "antes de gravar o marcador de heartbeat"
                    )
            if client.get("myapp:taskiq:heartbeat") == b"pong":
                seen = True
                break
            time.sleep(0.5)
        client.close()
        assert seen, "scheduler->broker->worker não gravou o marcador de heartbeat"
    finally:
        for proc in (sched, worker):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
