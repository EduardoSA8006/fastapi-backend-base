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
    # O invariante que até aqui só existia em config: worker/scheduler ficam
    # em data_net + taskiq_net, SEM rota até o `redis` (ratelimit_net). Um task
    # comprometido não pode ler/flushar as chaves do rate-limit.
    code, output = compose_exec(
        "worker", "python", "-c", _TCP_CHECK, "redis", timeout=30
    )
    assert code != 0, f"worker alcançou o redis do rate-limit: {output}"


def test_worker_alcanca_o_broker_dedicado(stack: str) -> None:
    # Contraprova: a falha acima não é um worker sem rede — o broker dele
    # (redis-taskiq, em taskiq_net) é alcançável normalmente.
    code, output = compose_exec(
        "worker", "python", "-c", _TCP_CHECK, "redis-taskiq", timeout=30
    )
    assert code == 0, f"worker não alcançou o próprio broker: {output}"


# --- Saúde real dos serviços ---


def test_worker_e_minio_healthy_scheduler_rodando(stack: str) -> None:
    # worker healthy = round-trip real do TaskIQ respondeu pelo broker.
    # minio healthy = `mc ready local` OK. scheduler: running (o healthy dele
    # depende do marcador fresco = scheduler_start + cadência — fora do
    # orçamento do e2e; o pipeline é coberto pelo teste de heartbeat abaixo).
    worker = container_state("myapp-worker")
    minio = container_state("myapp-minio")
    scheduler = container_state("myapp-scheduler")
    assert worker.get("Health", {}).get("Status") == "healthy"
    assert minio.get("Health", {}).get("Status") == "healthy"
    assert scheduler.get("Status") == "running"


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

# O scheduler grava o marcador `myapp:taskiq:heartbeat` (valor b"pong", TTL
# 3x a cadência) no broker (DB 0, TASKIQ_BROKER_URL) toda vez que a task
# agendada `ping` executa. Um único GET do marcador prova o pipeline inteiro
# scheduler→broker→worker — sem varrer chaves (o redis-taskiq tem KEYS
# desabilitado por hardening, mas aqui nem precisamos de SCAN).
_HEARTBEAT_CHECK = (
    "import os, time, sys\n"
    "import redis\n"
    "c = redis.from_url(os.environ['TASKIQ_BROKER_URL'], socket_timeout=5)\n"
    "deadline = time.monotonic() + 120\n"
    "while time.monotonic() < deadline:\n"
    "    if c.get('myapp:taskiq:heartbeat'):\n"
    "        print('HEARTBEAT-OK'); sys.exit(0)\n"
    "    time.sleep(3)\n"
    "print('SEM-HEARTBEAT'); sys.exit(1)\n"
)


def test_scheduler_heartbeat_real_no_stack(stack: str) -> None:
    # O pipeline COMPLETO no stack deployado: o scheduler (container) publica a
    # task `ping` a cada 60s, o worker a executa e grava o marcador
    # `myapp:taskiq:heartbeat` no broker. Espera até 120s (1º disparo =
    # scheduler_start + 60s) — o trecho que a integração cobre com cadência
    # curta, aqui na cadência real. Verificado de DENTRO do worker (o
    # redis-taskiq não é alcançável do host, por design).
    code, output = compose_exec("worker", "python", "-c", _HEARTBEAT_CHECK, timeout=140)
    assert code == 0, f"heartbeat do scheduler não chegou ao broker: {output}"
    assert "HEARTBEAT-OK" in output


def test_worker_se_recupera_de_restart(stack: str) -> None:
    # Resiliência: worker reiniciado (deploy/OOM/evict) volta a healthy —
    # reconecta ao broker e passa no round-trip do TaskIQ sem intervenção.
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


def test_scheduler_morto_marcador_expira_fica_unhealthy(stack: str) -> None:
    """Scheduler travado -> marcador expira (TTL) -> healthcheck reprova.

    `myapp-scheduler` fica `unhealthy` quando o marcador `myapp:taskiq:
    heartbeat` fica obsoleto; `myapp-worker` (pipeline independente) segue
    `healthy` — congela o processo sem matar o container.

    Por que SIGSTOP e não `docker kill` no container inteiro: o Docker, ao
    perceber a MORTE de um container com healthcheck configurado, marca
    Health.Status=unhealthy IMEDIATAMENTE (log sintético com o exit code do
    processo, confirmado empiricamente) — isso provaria só essa mecânica
    genérica do Docker, não que o PIPELINE real (scheduler -> marcador ->
    healthcheck) detecta staleness. Por isso congelamos só o PROCESSO do
    scheduler com SIGSTOP (`docker kill --signal=STOP`): o container continua
    `running` (o healthcheck, um `exec` em processo novo, roda normalmente),
    mas o processo parado não publica mais a task `ping` agendada — o
    marcador no broker envelhece de verdade até o TTL (3x
    TASKIQ_HEARTBEAT_SECONDS, default 180s) e o healthcheck do scheduler
    (interval 60s, retries 3 — fixos no compose, que não muda aqui) reprova 3
    vezes seguidas de forma orgânica.

    Timing: sem tocar app/ nem docker-compose.yml, não dá para encurtar
    interval/retries/start_period do healthcheck (fixos no compose), nem a
    cadência do heartbeat sem mexer no conftest de um jeito que quebraria o
    teste de "cadência real" (test_scheduler_heartbeat_real_no_stack, que
    depende do default de 60s). Aceita-se a janela bounded (deadlines
    generosos, ~3-6min no total) em vez de sleep indefinido.
    """
    import subprocess
    import time

    # Pré-condição: o scheduler precisa estar saudável (marcador fresco)
    # antes de congelá-lo — senão provaríamos só um estado transitório de
    # boot. Bounded pelo start_period do compose (210s) + folga; na prática
    # deve resolver quase de imediato, pois os testes anteriores do módulo já
    # consumiram bastante tempo de parede desde o start do scheduler.
    scheduler_pronto = False
    deadline = time.monotonic() + 250
    while time.monotonic() < deadline:
        health = container_state("myapp-scheduler").get("Health", {}).get("Status")
        if health == "healthy":
            scheduler_pronto = True
            break
        time.sleep(5)
    if not scheduler_pronto:
        raise AssertionError("scheduler não ficou healthy a tempo do teste")

    subprocess.run(
        ["docker", "kill", "--signal=STOP", "myapp-scheduler"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    try:
        # TTL (180s) + até 3 checks reprovando (60s cada) para o Docker
        # acumular falhas suficientes e virar unhealthy.
        ficou_unhealthy = False
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline:
            health = container_state("myapp-scheduler").get("Health", {}).get("Status")
            if health == "unhealthy":
                ficou_unhealthy = True
                break
            time.sleep(5)
        if not ficou_unhealthy:
            raise AssertionError(
                "scheduler não ficou unhealthy após o marcador expirar"
            )

        # Pipeline independente: o healthcheck do worker é o round-trip real
        # do TaskIQ pelo broker — não depende do scheduler estar de pé.
        worker_health = container_state("myapp-worker").get("Health", {}).get("Status")
        assert worker_health == "healthy"
    finally:
        # Descongela o processo (best-effort, sem `return`/exceção aqui: não
        # pode mascarar uma falha do bloco `try` acima). O teardown do módulo
        # (`down -v`) cuida do resto de qualquer forma.
        subprocess.run(
            ["docker", "kill", "--signal=CONT", "myapp-scheduler"],
            check=False,
            capture_output=True,
            timeout=30,
        )

    # Fecha o ciclo: confirma que o SIGCONT realmente devolveu o pipeline ao ar
    # (risco de ordem-de-teardown que o `finally` acima, sozinho, não cobria).
    # Uma vez descongelado, o scheduler publica o heartbeat de novo na próxima
    # cadência (até taskiq_heartbeat_seconds, default 60s) e o healthcheck
    # seguinte (interval 60s) já enxerga o marcador fresco -> volta a
    # `healthy` — só precisa de 1 check bem-sucedido (diferente de virar
    # unhealthy, que exige `retries` falhas seguidas). Deadline bounded e
    # generoso (TTL de 180s + 2 intervalos de healthcheck de folga),
    # fail-fast, sem sleep indefinido. Só roda se o bloco `try` acima não
    # levantou exceção (senão o teste já falhou por outro motivo e este
    # assert extra não deveria mascarar aquela falha original).
    scheduler_recuperado = False
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        health = container_state("myapp-scheduler").get("Health", {}).get("Status")
        if health == "healthy":
            scheduler_recuperado = True
            break
        time.sleep(5)
    assert scheduler_recuperado, "scheduler não voltou a healthy após o SIGCONT"


def test_rate_limit_ponta_a_ponta_estoura_429(stack: str) -> None:
    """Rajada real contra endpoint NÃO isento (`/`) -> 429 com Retry-After.

    `/api/v1/health` e `/api/v1/ready` são isentos do rate-limit (ver
    `app/main.py`, registrados em `limiter._exempt_routes`); a rota raiz `/`
    não é. O compose sobe com RATE_LIMIT_DEFAULT default (100/minute) — a
    rajada abaixo (200 chamadas concorrentes) estoura essa cota com folga.
    """
    import concurrent.futures

    def _hit(_: int) -> httpx.Response:
        with httpx.Client(base_url=stack, timeout=10) as client:
            return client.get("/")

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(_hit, range(200)))

    statuses = [r.status_code for r in responses]
    assert 429 in statuses, f"rate-limit não disparou na rajada: {statuses}"
    limitada = next(r for r in responses if r.status_code == 429)
    assert "Retry-After" in limitada.headers
    assert limitada.json()["detail"] == "Rate limit exceeded"


def test_ready_degrada_quando_dependencia_cai(stack: str) -> None:
    """Derruba o Redis do rate-limit (o mesmo lido pelo `/ready`) -> 503 real,
    com o detalhe da dependência no corpo.

    Restaura a dependência ao final (`finally`, incondicional): o stack
    precisa seguir saudável para os outros testes e para o teardown do
    módulo — não deixamos o ambiente quebrado.
    """
    import subprocess
    import time

    subprocess.run(
        ["docker", "stop", "myapp-redis"], check=True, capture_output=True, timeout=30
    )
    try:
        # readiness_cache_seconds (default 3s) poderia devolver um /ready
        # cacheado de antes da queda — a janela de retry acima disso cobre a
        # corrida sem depender de um sleep fixo.
        response: httpx.Response | None = None
        deadline = time.monotonic() + 20
        with httpx.Client(base_url=stack, timeout=10) as client:
            while time.monotonic() < deadline:
                response = client.get("/api/v1/ready")
                if response.status_code == 503:
                    break
                time.sleep(1)
        assert response is not None
        assert response.status_code == 503, (
            f"esperado 503 com o redis fora; body={response.json()}"
        )
        body = response.json()
        assert body["checks"]["redis"] == "error"
        assert body["checks"]["database"] == "ok"
    finally:
        # Restauração incondicional (best-effort no wait de saudável, mas o
        # `docker start` em si precisa suceder — sem ele o stack fica
        # quebrado para o resto da suíte/teardown).
        subprocess.run(
            ["docker", "start", "myapp-redis"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        restaurado = False
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            health = container_state("myapp-redis").get("Health", {}).get("Status")
            if health == "healthy":
                restaurado = True
                break
            time.sleep(2)
        if not restaurado:
            raise AssertionError("myapp-redis não voltou a healthy após restauração")
