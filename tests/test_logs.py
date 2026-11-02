"""Log formatting and setup.

What matters here is that the fields a reader needs survive the formatter: an
ingest failure is only searchable if `run_id` and `package` are keys rather
than words inside a sentence.
"""

import io
import json
import logging

import pytest

from depwatch import logs
from depwatch.logs import LOGGER_NAME, JsonFormatter, configure


@pytest.fixture(autouse=True)
def restore_logging():
    """configure() mutates a process-wide logger; put it back afterwards."""
    logger = logging.getLogger(LOGGER_NAME)
    before = (list(logger.handlers), logger.level, logger.propagate)
    yield
    logger.handlers, logger.level, logger.propagate = before


def _record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="depwatch.ingest", level=logging.WARNING, pathname=__file__,
        lineno=1, msg="record quarantined", args=(), exc_info=None,
    )
    record.__dict__.update(extra)
    return record


def test_extra_fields_become_json_fields():
    line = JsonFormatter().format(_record(run_id=7, package="left-pad", reason="no version"))
    payload = json.loads(line)

    assert payload["message"] == "record quarantined"
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "depwatch.ingest"
    assert payload["run_id"] == 7
    assert payload["package"] == "left-pad"
    assert payload["reason"] == "no version"


def test_standard_record_attributes_are_not_dumped():
    """Otherwise every line carries pathname, lineno, thread id and the rest."""
    payload = json.loads(JsonFormatter().format(_record(run_id=1)))
    for noise in ("pathname", "lineno", "args", "msg", "levelno", "thread"):
        assert noise not in payload


def test_an_unserialisable_extra_does_not_break_the_logger():
    """A logging call must never be the thing that takes the process down."""
    payload = json.loads(JsonFormatter().format(_record(client=object())))
    assert "object object at" in payload["client"]


def test_exceptions_are_included():
    try:
        raise ValueError("registry said no")
    except ValueError:
        import sys
        record = _record()
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: registry said no" in payload["exception"]


def test_configure_json_writes_one_object_per_line():
    stream = io.StringIO()
    configure(level="INFO", fmt="json", stream=stream)
    logging.getLogger("depwatch.ingest").info("ingest run finished", extra={"run_id": 3})

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["run_id"] == 3


def test_configure_text_is_the_default():
    stream = io.StringIO()
    configure(level="INFO", fmt=None, stream=stream)
    logging.getLogger("depwatch.ingest").info("hello")
    assert "hello" in stream.getvalue()
    assert not stream.getvalue().startswith("{")


def test_calling_configure_twice_does_not_double_the_output():
    stream = io.StringIO()
    configure(level="INFO", fmt="json", stream=stream)
    configure(level="INFO", fmt="json", stream=stream)
    logging.getLogger("depwatch.ingest").info("once")

    assert len([x for x in stream.getvalue().splitlines() if x.strip()]) == 1


def test_records_do_not_propagate_to_the_root_logger():
    """uvicorn configures root as well; propagating would print every line twice."""
    configure(level="INFO", fmt="json", stream=io.StringIO())
    assert logging.getLogger(LOGGER_NAME).propagate is False


def test_level_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.setenv("LOG_FORMAT", "json")
    stream = io.StringIO()
    logs.configure(stream=stream)

    logging.getLogger("depwatch.ingest").info("suppressed")
    logging.getLogger("depwatch.ingest").warning("kept")

    assert "suppressed" not in stream.getvalue()
    assert "kept" in stream.getvalue()
