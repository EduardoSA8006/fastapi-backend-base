import logging

_configured = False


def configure_logging(debug: bool = False) -> None:
    """Configura o logging da aplicação (idempotente)."""
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _configured = True
