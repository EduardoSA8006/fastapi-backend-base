"""Helpers compartilhados da camada de integração.

`redis_container` era copiado 3x (rate-limit, broker do Celery, beat) —
aqui é a única definição; cada módulo cria a própria fixture com a senha
e o escopo que quiser.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs


@contextmanager
def redis_container(password: str) -> Iterator[str]:
    """Redis 8 efêmero com --requirepass; produz a URL base (sem /db)."""
    container = (
        DockerContainer("redis:8-alpine")
        .with_command(f"redis-server --requirepass {password}")
        .with_exposed_ports(6379)
    )
    with container:
        wait_for_logs(container, "Ready to accept connections", timeout=30)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://:{password}@{host}:{port}"
