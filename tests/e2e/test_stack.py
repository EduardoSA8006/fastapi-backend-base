"""E2E: stack compose completo atacado DE FORA com httpx.

Diferente do TestClient (ASGI in-process), o httpx atravessa a rede real:
uvicorn, container, entrypoint (espera de banco + migração de boot), redes do
compose e healthchecks. É a única camada que valida o sistema como deployado.
"""

import socket

import httpx
import pytest

from tests.e2e.conftest import compose_exec, container_state

pytestmark = pytest.mark.e2e


# --- API pela rede real ---


def test_health_e_ready_pela_rede(stack: str) -> None:
    # health: o processo está de pé (depois de entrypoint + migrações).
    # ready: banco E redis reais respondem — o stack inteiro funcional.
    with httpx.Client(base_url=stack, timeout=10) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        ready = client.get("/api/v1/ready")
        assert ready.status_code == 200
        body = ready.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"] == "ok"
        assert body["checks"]["redis"] == "ok"


def test_security_headers_e_request_id_na_resposta_real(stack: str) -> None:
    with httpx.Client(base_url=stack, timeout=10) as client:
        response = client.get(
            "/api/v1/health", headers={"X-Request-ID": "e2e-test-123"}
        )
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Request-ID") == "e2e-test-123"
    # uvicorn roda com --no-server-header (anti-fingerprint).
    assert "server" not in response.headers


def test_contrato_de_erro_404_json(stack: str) -> None:
    with httpx.Client(base_url=stack, timeout=10) as client:
        response = client.get("/api/v1/rota-que-nao-existe")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "detail" in response.json()


def test_porta_da_api_em_loopback_apenas(stack: str) -> None:
    # Secure-by-default do compose: a porta publicada faz bind em 127.0.0.1.
    # Numa interface não-loopback a conexão deve falhar.
    hostname_ip = socket.gethostbyname(socket.gethostname())
    if hostname_ip.startswith("127."):
        pytest.skip("host sem IP não-loopback resolvível")
    try:
        with socket.create_connection((hostname_ip, 18001), timeout=3):
            reachable = True
    except OSError:
        reachable = False
    assert not reachable, f"API alcançável fora do loopback ({hostname_ip}:18001)"


# --- Invariante de segurança: segmentação de rede ---

_TCP_CHECK = (
    "import socket,sys\n"
    "s=socket.socket(); s.settimeout(5)\n"
    "try:\n"
    "    s.connect((sys.argv[1], 6379)); print('CONECTOU')\n"
    "except Exception as e:\n"
    "    print(f'FALHOU: {type(e).__name__}'); sys.exit(1)\n"
)


def test_worker_nao_alcanca_redis_do_rate_limit(stack: str) -> None:
    # O invariante que até aqui só existia em config: worker/beat ficam em
    # data_net + celery_net, SEM rota até o `redis` (ratelimit_net). Um task
    # comprometido não pode ler/flushar as chaves do rate-limit.
    code, output = compose_exec(
        "worker", "python", "-c", _TCP_CHECK, "redis", timeout=30
    )
    assert code != 0, f"worker alcançou o redis do rate-limit: {output}"


def test_worker_alcanca_o_broker_dedicado(stack: str) -> None:
    # Contraprova: a falha acima não é um worker sem rede — o broker dele
    # (redis-celery, em celery_net) é alcançável normalmente.
    code, output = compose_exec(
        "worker", "python", "-c", _TCP_CHECK, "redis-celery", timeout=30
    )
    assert code == 0, f"worker não alcançou o próprio broker: {output}"


# --- Saúde real dos serviços ---


def test_worker_e_minio_healthy_beat_rodando(stack: str) -> None:
    # worker healthy = `celery inspect ping` real respondeu pelo broker.
    # minio healthy = `mc ready local` OK. beat: running (o healthy dele exige
    # start_period de 210s — fora do orçamento do e2e; o pipeline broker→worker
    # é coberto na integração).
    worker = container_state("myapp-worker")
    minio = container_state("myapp-minio")
    beat = container_state("myapp-beat")
    assert worker.get("Health", {}).get("Status") == "healthy"
    assert minio.get("Health", {}).get("Status") == "healthy"
    assert beat.get("Status") == "running"


# --- Storage MinIO pela rede interna real ---

_STORAGE_CHECK = (
    "import asyncio\n"
    "from app.shared import storage\n"
    "from app.core.config import get_settings\n"
    "async def main():\n"
    "    bucket = get_settings().minio_bucket\n"
    "    await storage.put_object(bucket=bucket, key='e2e/proof.txt',"
    " data=b'e2e-bytes', content_type='text/plain')\n"
    "    data = await storage.get_object(bucket=bucket, key='e2e/proof.txt')\n"
    "    assert data == b'e2e-bytes', data\n"
    "    await storage.delete_object(bucket=bucket, key='e2e/proof.txt')\n"
    "    print('STORAGE-OK')\n"
    "asyncio.run(main())\n"
)


def test_storage_round_trip_dentro_do_stack(stack: str) -> None:
    # Executa DENTRO do container api: facade real + credenciais reais +
    # hostname `minio` resolvido pela rede interna do compose (a porta 9000
    # não é alcançável do host — este é o único caminho, por design).
    code, output = compose_exec("api", "python", "-c", _STORAGE_CHECK, timeout=60)
    assert code == 0, f"storage falhou dentro do stack: {output}"
    assert "STORAGE-OK" in output


# --- Smoke de concorrência ---


def test_burst_de_50_requisicoes_concorrentes(stack: str) -> None:
    # Sanidade do --limit-concurrency do uvicorn sob carga leve: 50 chamadas
    # paralelas, todas respondem 200 (probes são isentos do rate-limit).
    # Slowloris/carga real são responsabilidade da borda (ver README) — fora
    # do escopo de suíte de testes.
    import concurrent.futures

    def _hit(_: int) -> int:
        with httpx.Client(base_url=stack, timeout=15) as client:
            return client.get("/api/v1/health").status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
        codes = list(pool.map(_hit, range(50)))
    assert codes == [200] * 50


# --- Cenários de falha/tempo real ---

# SCAN (scan_iter), NÃO keys(): o redis-celery do stack tem o comando KEYS
# desabilitado (rename-command — nosso próprio hardening); keys() falharia
# aqui mesmo com o heartbeat funcionando. SCAN não é renomeado.
_HEARTBEAT_CHECK = (
    "import os, time, sys\n"
    "import redis\n"
    "backend = redis.from_url(os.environ['CELERY_RESULT_BACKEND'],"
    " socket_timeout=5)\n"
    "deadline = time.monotonic() + 120\n"
    "while time.monotonic() < deadline:\n"
    "    for key in backend.scan_iter('celery-task-meta-*'):\n"
    "        value = backend.get(key)\n"
    "        if value is not None and b'\"pong\"' in value:\n"
    "            print('HEARTBEAT-OK'); sys.exit(0)\n"
    "    time.sleep(3)\n"
    "print('SEM-HEARTBEAT'); sys.exit(1)\n"
)


def test_beat_heartbeat_real_no_stack(stack: str) -> None:
    # O pipeline COMPLETO no stack deployado: o beat (container) publica o
    # core.ping a cada 60s, o worker executa e o resultado aparece no result
    # backend. Espera até 120s (1º disparo = beat_start + 60s) — o trecho
    # que a integração cobre com heartbeat de 1s, aqui na cadência real.
    # Verificado de DENTRO do worker (o redis-celery não é alcançável do
    # host, por design).
    code, output = compose_exec("worker", "python", "-c", _HEARTBEAT_CHECK, timeout=140)
    assert code == 0, f"heartbeat do beat não chegou ao backend: {output}"
    assert "HEARTBEAT-OK" in output


def test_worker_se_recupera_de_restart(stack: str) -> None:
    # Resiliência: worker reiniciado (deploy/OOM/evict) volta a healthy —
    # reconecta ao broker e responde ao inspect ping sem intervenção.
    import subprocess
    import time

    subprocess.run(
        ["docker", "restart", "myapp-worker"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if container_state("myapp-worker").get("Health", {}).get("Status") == "healthy":
            return
        time.sleep(5)
    raise AssertionError("worker não voltou a healthy após restart")
