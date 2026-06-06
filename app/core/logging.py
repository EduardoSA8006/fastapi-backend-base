import json
import logging
from typing import Any

_configured = False

# Atributos padrão de LogRecord — usados para separar os campos "extra".
_STANDARD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class JsonFormatter(logging.Formatter):
    """Formata logs como JSON (uma linha por registro).

    Além de facilitar correlação (ex.: por request_id) num SIEM, escapar os
    campos como JSON neutraliza injeção de CR/LF: qualquer conteúdo controlado
    pelo cliente vira string escapada, sem quebrar/forjar linhas de log.
    """

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Campos estruturados passados via `extra=...`.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                data[key] = value
        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging(debug: bool = False) -> None:
    """Configura o logging da aplicação como JSON (idempotente)."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        handlers=[handler],
    )
    _configured = True
