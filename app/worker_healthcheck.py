"""Healthcheck do worker TaskIQ: round-trip REAL pelo broker.

Processo vivo != worker funcional. Este probe enfileira o core.healthcheck_ping
(task DEDICADA de liveness, que NÃO grava o marcador do scheduler) e aguarda o
resultado no result backend com timeout curto — só passa se o loop de consumo do
worker estiver saudável (equivalente honesto ao `celery inspect ping`). Usa uma
task separada do `ping` agendado de propósito: o probe do worker roda a cada 30s
e, se escrevesse o marcador `myapp:taskiq:heartbeat`, o mascararia sempre fresco
e esconderia um scheduler morto. Usado no healthcheck do container worker.
"""

import asyncio
import sys

from app.worker import broker, healthcheck_ping

_TIMEOUT_S = 5.0


async def _probe() -> int:
    await broker.startup()
    try:
        task = await healthcheck_ping.kiq()
        result = await asyncio.wait_for(
            task.wait_result(timeout=_TIMEOUT_S), timeout=_TIMEOUT_S + 1
        )
        return 0 if result.return_value == "pong" else 1
    except TimeoutError:
        return 1
    finally:
        await broker.shutdown()


def main() -> int:
    return asyncio.run(_probe())


if __name__ == "__main__":
    sys.exit(main())
