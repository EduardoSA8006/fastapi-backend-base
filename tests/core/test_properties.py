"""Testes property-based (Hypothesis) dos alvos críticos de segurança.

Diferente dos exemplos fixos, cada propriedade vale para ENTRADAS GERADAS —
incluindo adversariais (unicode, controle, tamanhos absurdos) que exemplos
manuais não enumeram. Sem max_examples custom: o default (100) mantém a
suíte rápida.
"""

import json
import logging
import string

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from app.core.client_ip import resolve_client_ip
from app.core.config import _VALID_ENVIRONMENTS, Settings
from app.core.logging import JsonFormatter
from app.core.middleware.observability import _REQUEST_ID_RE
from app.core.security_guards import _host_port

# --- resolve_client_ip: nunca explode; sem trust, sempre o IP da conexão ---


@given(
    forwarded=st.one_of(st.none(), st.text(max_size=512)),
    connection_ip=st.text(min_size=1, max_size=64),
    trust=st.booleans(),
    hops=st.integers(min_value=-3, max_value=10),
)
def test_resolve_client_ip_nunca_levanta(
    forwarded: str | None, connection_ip: str, trust: bool, hops: int
) -> None:
    # Propriedade: header controlado pelo cliente JAMAIS derruba a resolução
    # (que roda no caminho de TODA requisição, no rate-limit e no log).
    result = resolve_client_ip(forwarded, connection_ip, trust, hops)
    assert isinstance(result, str)


@given(
    forwarded=st.one_of(st.none(), st.text(max_size=512)),
    connection_ip=st.text(min_size=1, max_size=64),
    hops=st.integers(min_value=-3, max_value=10),
)
def test_sem_trust_proxy_o_header_e_irrelevante(
    forwarded: str | None, connection_ip: str, hops: int
) -> None:
    # Propriedade de segurança central: com trust_proxy=False, NENHUM valor
    # de X-Forwarded-For muda o resultado — spoofing impossível.
    assert resolve_client_ip(forwarded, connection_ip, False, hops) == connection_ip


@given(
    forwarded=st.text(max_size=512),
    connection_ip=st.text(min_size=1, max_size=64),
)
def test_com_trust_o_resultado_vem_do_header_ou_da_conexao(
    forwarded: str, connection_ip: str
) -> None:
    # O resultado é sempre uma entrada do header (strip) ou o IP da conexão —
    # nunca um valor inventado/concatenado.
    result = resolve_client_ip(forwarded, connection_ip, True, 1)
    valid = {p.strip() for p in forwarded.split(",") if p.strip()}
    valid.add(connection_ip)
    assert result in valid


# --- X-Request-ID: regex anti log-injection ---


@given(candidate=st.text(max_size=256))
def test_request_id_aceito_nunca_contem_controle(candidate: str) -> None:
    # Propriedade: tudo que a regex aceita é seguro para logs — sem CR/LF,
    # sem caracteres de controle, tamanho limitado.
    if _REQUEST_ID_RE.match(candidate):
        assert len(candidate) <= 128
        allowed = set(string.ascii_letters + string.digits + "._-")
        assert all(c in allowed for c in candidate)


@given(
    prefix=st.text(alphabet=string.ascii_letters, min_size=1, max_size=10),
    evil=st.sampled_from(["\r", "\n", "\r\n", "\x00", "\x1b", " ", "\t"]),
)
def test_request_id_com_controle_e_sempre_rejeitado(prefix: str, evil: str) -> None:
    assert not _REQUEST_ID_RE.match(f"{prefix}{evil}forjado")


# --- Validator de ENVIRONMENT: aceita exatamente o conjunto conhecido ---


@given(value=st.text(max_size=64))
def test_environment_aceita_exatamente_o_conjunto_valido(value: str) -> None:
    normalized = value.strip().lower()
    if normalized in _VALID_ENVIRONMENTS:
        settings = Settings(environment=value, _env_file=None)
        assert settings.environment == normalized
    else:
        with pytest.raises(ValueError, match="ENVIRONMENT"):
            Settings(environment=value, _env_file=None)


# --- _host_port: parsing de URL nunca explode; porta default normalizada ---


@given(url=st.text(max_size=256))
def test_host_port_nunca_levanta_com_url_arbitraria(url: str) -> None:
    # O guard roda no BOOT: uma URL malformada deve falhar nos guards de
    # conteúdo (mensagem clara), nunca num traceback de parsing.
    try:
        _, port = _host_port(url)
    except ValueError:
        return  # urlparse levanta ValueError p/ portas inválidas — aceitável
    assert isinstance(port, int)


@given(
    host=st.from_regex(r"[a-z][a-z0-9-]{0,20}", fullmatch=True),
    db=st.integers(min_value=0, max_value=15),
)
def test_host_port_normaliza_porta_ausente(host: str, db: int) -> None:
    # redis://host/N e redis://host:6379/N são a MESMA instância.
    assert _host_port(f"redis://{host}/{db}") == _host_port(f"redis://{host}:6379/{db}")


# --- JsonFormatter: qualquer extra vira UMA linha JSON válida ---


@given(payload=st.text(max_size=512))
def test_json_formatter_sempre_uma_linha_json_valida(payload: str) -> None:
    # Propriedade anti log-injection: o conteúdo controlado pelo cliente sai
    # escapado — uma única linha, JSON parseável, payload preservado.
    record = logging.LogRecord(
        name="classup.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="m",
        args=(),
        exc_info=None,
    )
    record.request_id = payload
    output = JsonFormatter().format(record)
    assert "\n" not in output
    assert "\r" not in output
    assert json.loads(output)["request_id"] == payload


# --- Body-size: fronteira exata sob corpo chunked (fuzz) ---

_LIMIT = 64
_bs_client: "TestClient | None" = None


def _body_size_client() -> "TestClient":
    # Um único app/cliente para todos os exemplos (stateless entre requests).
    global _bs_client
    if _bs_client is None:
        from tests.conftest import make_client

        _bs_client = make_client(max_body_size=_LIMIT)
    return _bs_client


@given(size=st.integers(min_value=0, max_value=_LIMIT * 2))
def test_fronteira_do_body_size_com_chunked(size: int) -> None:
    # Propriedade: corpo chunked (sem Content-Length) de N bytes passa sse
    # N <= limite — exatamente na fronteira, sem off-by-one (o bypass
    # original do chunked vivia aqui).
    from collections.abc import Iterator

    def gen() -> Iterator[bytes]:
        if size:
            yield b"x" * size

    response = _body_size_client().post("/api/v1/health", content=gen())
    if size > _LIMIT:
        assert response.status_code == 413
    else:
        # Sob o limite o corpo ATRAVESSA o middleware e chega à rota
        # (GET-only -> 405). Nunca 413 dentro do limite: sem off-by-one.
        assert response.status_code == 405
