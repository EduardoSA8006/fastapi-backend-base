from app.core.config import Settings
from app.core.limiter import build_key_func


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    def __init__(self, host: str, forwarded_for: str | None = None) -> None:
        self.client = _FakeClient(host)
        self.headers = {}
        if forwarded_for is not None:
            self.headers["X-Forwarded-For"] = forwarded_for


def test_key_func_uses_client_host_when_proxy_untrusted() -> None:
    settings = Settings(trust_proxy=False)
    key_func = build_key_func(settings)
    request = _FakeRequest(host="10.0.0.1", forwarded_for="1.2.3.4")
    assert key_func(request) == "10.0.0.1"


def test_key_func_uses_forwarded_for_when_proxy_trusted() -> None:
    settings = Settings(trust_proxy=True)
    key_func = build_key_func(settings)
    request = _FakeRequest(host="10.0.0.1", forwarded_for="1.2.3.4, 5.6.7.8")
    assert key_func(request) == "1.2.3.4"


def test_key_func_falls_back_to_client_host_without_forwarded_for() -> None:
    settings = Settings(trust_proxy=True)
    key_func = build_key_func(settings)
    request = _FakeRequest(host="10.0.0.1")
    assert key_func(request) == "10.0.0.1"
