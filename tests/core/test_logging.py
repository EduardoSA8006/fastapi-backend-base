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


def test_json_formatter_inclui_stacktrace_de_exc_info() -> None:
    # Stacktrace serializado dentro do JSON (1 linha), não despejado cru.
    import json
    import logging
    import sys

    from app.core.logging import JsonFormatter

    try:
        raise ValueError("erro de teste")
    except ValueError:
        record = logging.LogRecord(
            name="classup.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="falhou",
            args=(),
            exc_info=sys.exc_info(),
        )
    data = json.loads(JsonFormatter().format(record))
    assert "exc_info" in data
    assert "ValueError" in data["exc_info"]
