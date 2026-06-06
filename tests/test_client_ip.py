from app.core.client_ip import resolve_client_ip


def test_returns_connection_ip_when_proxy_untrusted() -> None:
    # XFF forjado é ignorado sem trust_proxy.
    assert resolve_client_ip("9.9.9.9", "10.0.0.1", False, 1) == "10.0.0.1"


def test_returns_xff_when_trusted_and_enough_hops() -> None:
    assert resolve_client_ip("1.1.1.1, 2.2.2.2", "10.0.0.1", True, 1) == "2.2.2.2"


def test_counts_hops_from_right() -> None:
    # 2 proxies confiáveis: o cliente é a 2ª entrada de trás para frente.
    assert (
        resolve_client_ip("1.1.1.1, 2.2.2.2, 3.3.3.3", "10.0.0.1", True, 2) == "2.2.2.2"
    )


def test_falls_back_when_not_enough_entries() -> None:
    # Menos entradas que os saltos esperados: recorre ao IP da conexão (seguro).
    assert resolve_client_ip("1.1.1.1", "10.0.0.1", True, 2) == "10.0.0.1"


def test_no_forwarded_header_uses_connection() -> None:
    assert resolve_client_ip(None, "10.0.0.1", True, 1) == "10.0.0.1"


def test_ignores_empty_entries() -> None:
    assert resolve_client_ip("1.1.1.1, , 2.2.2.2", "10.0.0.1", True, 1) == "2.2.2.2"


def test_zero_hops_falls_back_to_connection() -> None:
    assert resolve_client_ip("1.1.1.1", "10.0.0.1", True, 0) == "10.0.0.1"
