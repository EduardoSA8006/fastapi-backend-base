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
    worker = container_state("classup-worker")
    minio = container_state("classup-minio")
    beat = container_state("classup-beat")
    assert worker.get("Health", {}).get("Status") == "healthy"
    assert minio.get("Health", {}).get("Status") == "healthy"
    assert beat.get("Status") == "running"
