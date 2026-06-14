"""Logging utilities for HiSmart Provision."""

import logging
import sys

_logger: logging.Logger | None = None


def get_logger(name: str = "hismart") -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger("hismart")
        _logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s %(message)s",
            datefmt="%H:%M:%S",
        ))
        _logger.handlers.clear()
        _logger.addHandler(handler)
    return _logger
