import pytest


def test_broker_iniciado_e_encerrado_no_ciclo_de_vida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Espiona startup/shutdown do broker: como o InMemory os torna no-op
    # idempotentes, só um espião DISCRIMINA se o lifespan de create_app
    # realmente chama os dois ao entrar/sair do contexto do TestClient.
    from app.worker import broker
    from tests.conftest import make_client

    calls: list[str] = []
    orig_startup = broker.startup
    orig_shutdown = broker.shutdown

    async def spy_startup() -> None:
        calls.append("startup")
        await orig_startup()

    async def spy_shutdown() -> None:
        calls.append("shutdown")
        await orig_shutdown()

    monkeypatch.setattr(broker, "startup", spy_startup)
    monkeypatch.setattr(broker, "shutdown", spy_shutdown)

    with make_client():
        # Ao entrar no contexto, o lifespan já rodou o startup (web process).
        assert calls == ["startup"]
        assert broker.is_worker_process is False
    # Ao sair, o shutdown do lifespan foi chamado.
    assert calls == ["startup", "shutdown"]


async def test_api_despacha_ping_com_broker_iniciado() -> None:
    # A API despacha via .kiq(); com InMemory, executa in-process e retorna.
    from app.worker import broker, ping

    await broker.startup()
    try:
        task = await ping.kiq()
        result = await task.wait_result(timeout=5)
        assert result.return_value == "pong"
    finally:
        await broker.shutdown()
