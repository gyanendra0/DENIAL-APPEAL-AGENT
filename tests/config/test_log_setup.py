"""The API's log lines reach stderr at the set level; other libraries stay at WARNING."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from src.config.log_setup import APP_LOGGER_NAME, configure_logging


@contextmanager
def bare_logging() -> Iterator[None]:
    """No root handler, as in a real start, then everything put back.

    Used inside the test, not as a fixture: pytest adds its own root handlers just before the
    test body, and `logging.basicConfig` does nothing while any handler is there.
    """
    root, app = logging.getLogger(), logging.getLogger(APP_LOGGER_NAME)
    handlers, root_level, app_level = root.handlers[:], root.level, app.level
    root.handlers[:] = []
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(root_level)
        app.setLevel(app_level)


def test_an_info_line_of_this_codebase_is_written(capsys: pytest.CaptureFixture[str]) -> None:
    with bare_logging():
        configure_logging("INFO")
        logging.getLogger("src.api.routes.me").info("account deleted: account %d", 7)
    assert " INFO src.api.routes.me: account deleted: account 7\n" in capsys.readouterr().err


def test_the_level_hides_lower_lines(capsys: pytest.CaptureFixture[str]) -> None:
    with bare_logging():
        configure_logging("WARNING")
        logging.getLogger("src.api.routes.me").info("made up")
    assert capsys.readouterr().err == ""


def test_another_library_writes_warnings_but_not_info(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with bare_logging():
        configure_logging("DEBUG")
        logging.getLogger("httpx").info("HTTP Request: made up")
        logging.getLogger("httpx").warning("a made-up warning")
    written = capsys.readouterr().err
    assert "a made-up warning" in written
    assert "HTTP Request" not in written


def test_a_second_call_does_not_write_each_line_twice(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with bare_logging():
        configure_logging("INFO")
        configure_logging("INFO")
        logging.getLogger("src.jobs").info("made up")
    assert capsys.readouterr().err.count("made up") == 1
