def test_broker_iniciado_e_encerrado_no_ciclo_de_vida() -> None:
    # Entrar no contexto do TestClient dispara o lifespan; sair, o shutdown.
    # Sem erro => startup/shutdown do broker rodaram (InMemory na suíte).
    from app.worker import broker
    from tests.conftest import make_client

    with make_client():
        assert broker.is_worker_process is False


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
