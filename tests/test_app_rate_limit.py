"""Rate-limit no app real: 429, fail-closed, isenções e anti-spoofing."""

from tests.conftest import make_client, make_rl_client


def test_rate_limit_returns_429_when_exceeded() -> None:
    client = make_rl_client(rate_limit_default="3/minute")
    codes = [client.get("/_rl").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    last = client.get("/_rl")
    assert last.status_code == 429
    assert last.json()["detail"] == "Rate limit exceeded"
    assert "retry_after" in last.json()
    # Contrato completo: o header HTTP acompanha o corpo (clientes que só
    # leem headers também recebem o sinal de backoff).
    assert "Retry-After" in last.headers


def test_rate_limit_fail_closed_when_store_unavailable() -> None:
    # Escolha consciente (F4): com o Redis indisponível, o rate-limit é
    # fail-closed (500), não fail-open. Regressão contra mudança acidental de
    # swallow_errors / in_memory_fallback. Porta 6399 não tem Redis -> connection
    # refused (rápido); socket_connect_timeout=2 é apenas o teto.
    client = make_rl_client(
        raise_server_exceptions=False,
        rate_limit_enabled=True,
        rate_limit_default="100/minute",
        rate_limit_storage_uri="redis://127.0.0.1:6399/0",
    )
    # /_rl não é isento do rate-limit (os probes são) — exercita o fail-closed.
    # Contrato completo: o 500 nasce no ErrorBoundary — JSON padronizado,
    # blindado, sem vazar a causa (erro de conexão Redis) ao cliente.
    response = client.get("/_rl")
    assert response.status_code == 500
    assert response.json() == {"detail": "Erro interno."}
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert "redis" not in response.text.lower()


def test_rate_limit_disabled_allows_all() -> None:
    client = make_client(rate_limit_enabled=False, rate_limit_default="1/minute")
    codes = [client.get("/api/v1/health").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 200, 200]


def test_spoofed_xff_ignored_when_proxy_untrusted() -> None:
    # Com trust_proxy=False (padrão), X-Forwarded-For forjado é ignorado:
    # todas as requisições compartilham o bucket do IP da conexão, então o
    # rate-limit dispara mesmo variando o cabeçalho — não dá para burlar.
    client = make_rl_client(rate_limit_default="2/minute", trust_proxy=False)
    r1 = client.get("/_rl", headers={"X-Forwarded-For": "1.1.1.1"})
    r2 = client.get("/_rl", headers={"X-Forwarded-For": "2.2.2.2"})
    r3 = client.get("/_rl", headers={"X-Forwarded-For": "3.3.3.3"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


def test_probes_sao_isentos_do_rate_limit() -> None:
    # Garantia operacional direta: com limite de 1/min, os probes seguem 200
    # em rajada (sem isso, orquestrador gastaria a cota e mataria a réplica).
    client = make_client(rate_limit_default="1/minute")
    for _ in range(5):
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/ready").status_code == 200
