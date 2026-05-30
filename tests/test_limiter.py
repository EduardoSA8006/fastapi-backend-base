from typing import cast

from starlette.requests import Request

from app.core.config import Settings
from app.core.limiter import build_key_func


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    def __init__(self, host: str, forwarded_for: str | None = None) -> None:
        self.client = _FakeClient(host)
        self.headers: dict[str, str] = {}
        if forwarded_for is not None:
            self.headers["X-Forwarded-For"] = forwarded_for


def _fake_request(host: str, forwarded_for: str | None = None) -> Request:
    """Stand-in mínimo de Request para o key_func (só usa .client e .headers)."""
    return cast(Request, _FakeRequest(host, forwarded_for))


def test_key_func_uses_client_host_when_proxy_untrusted() -> None:
    settings = Settings(trust_proxy=False)
    key_func = build_key_func(settings)
    request = _fake_request(host="10.0.0.1", forwarded_for="1.2.3.4")
    assert key_func(request) == "10.0.0.1"


def test_key_func_uses_rightmost_forwarded_for_when_proxy_trusted() -> None:
    # Com um proxy confiável, o IP real é o que o proxy acrescentou (à direita).
    # A entrada à esquerda é forjável pelo cliente e deve ser ignorada.
    settings = Settings(trust_proxy=True)
    key_func = build_key_func(settings)
    request = _fake_request(host="10.0.0.1", forwarded_for="1.2.3.4, 5.6.7.8")
    assert key_func(request) == "5.6.7.8"


def test_key_func_falls_back_to_client_host_without_forwarded_for() -> None:
    settings = Settings(trust_proxy=True)
    key_func = build_key_func(settings)
    request = _fake_request(host="10.0.0.1")
    assert key_func(request) == "10.0.0.1"


def test_key_func_ignores_empty_forwarded_for() -> None:
    settings = Settings(trust_proxy=True)
    key_func = build_key_func(settings)
    request = _fake_request(host="10.0.0.1", forwarded_for="")
    assert key_func(request) == "10.0.0.1"


def test_key_func_ignores_whitespace_forwarded_for() -> None:
    settings = Settings(trust_proxy=True)
    key_func = build_key_func(settings)
    request = _fake_request(host="10.0.0.1", forwarded_for="   ")
    assert key_func(request) == "10.0.0.1"


def test_key_func_single_proxy_entry() -> None:
    settings = Settings(trust_proxy=True)
    key_func = build_key_func(settings)
    request = _fake_request(host="10.0.0.1", forwarded_for="203.0.113.7")
    assert key_func(request) == "203.0.113.7"


def test_key_func_two_trusted_proxies_uses_correct_hop() -> None:
    # Com 2 proxies (ex.: LB + nginx), XFF = "cliente, ip_lb"; o IP real do
    # cliente é o penúltimo (2 saltos a partir da direita).
    settings = Settings(trust_proxy=True, num_trusted_proxies=2)
    key_func = build_key_func(settings)
    request = _fake_request(host="10.0.0.1", forwarded_for="9.9.9.9, 172.16.0.2")
    assert key_func(request) == "9.9.9.9"


def test_key_func_falls_back_when_fewer_entries_than_trusted_proxies() -> None:
    # Menos entradas que os saltos esperados → não dá para confiar; usa o IP
    # da conexão (evita aceitar XFF forjado e incompleto).
    settings = Settings(trust_proxy=True, num_trusted_proxies=2)
    key_func = build_key_func(settings)
    request = _fake_request(host="10.0.0.1", forwarded_for="1.1.1.1")
    assert key_func(request) == "10.0.0.1"
