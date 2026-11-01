"""Where the API's log lines go. Without this, Python drops every line below WARNING."""

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# The parent of every logger in this codebase, whose names all start with `src.`.
APP_LOGGER_NAME = "src"


def configure_logging(level: str) -> None:
    """Write log lines to stderr: this codebase's at `level`, other libraries' at WARNING.

    `httpx` writes a line for every request at INFO, so `level` is not applied to everything.
    """
    logging.basicConfig(level=logging.WARNING, format=LOG_FORMAT)
    logging.getLogger(APP_LOGGER_NAME).setLevel(level)
