import json
import logging

from app.core.logging import JsonFormatter


def _record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="classup.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_outputs_valid_json_with_fields() -> None:
    out = JsonFormatter().format(_record("access", request_id="abc", client="1.2.3.4"))
    data = json.loads(out)
    assert data["message"] == "access"
    assert data["level"] == "INFO"
    assert data["logger"] == "classup.access"
    assert data["request_id"] == "abc"
    assert data["client"] == "1.2.3.4"


def test_json_formatter_escapes_crlf() -> None:
    # Conteúdo com CR/LF é escapado dentro da string JSON — não forja linhas.
    out = JsonFormatter().format(_record("linha1\r\nFORJADA admin OK"))
    assert "\r\n" not in out
    assert json.loads(out)["message"] == "linha1\r\nFORJADA admin OK"
