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
    """Configura o logging da aplicação como JSON (idempotente).

    Assume a POSSE do root logger: substitui handlers pré-existentes em vez
    de usar basicConfig — que é no-op quando o root já tem handler (cenário
    gunicorn/UvicornWorker, que configura logging antes do import da app).
    Sem isso, o JsonFormatter (e o escape anti-CRLF, defesa de log-injection)
    não se aplicaria e, com o level default WARNING do root, as access lines
    INFO seriam descartadas por completo.

    Não fechamos os handlers removidos: podem ser de terceiros (gunicorn) e
    compartilham streams (stderr) que não nos pertencem.
    """
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    _configured = True
