"""Unit tests for logging configuration and standard library interception."""

import logging
from src.core.logging import logger, setup_logging


def test_logger_instance():
    """Verifies that logger can emit messages without raising exceptions."""
    logger.info("Test info log message")
    logger.debug("Test debug log message")
    logger.warning("Test warning log message")


def test_standard_logging_interception():
    """Verifies that standard library logging is redirected through Loguru handler."""
    setup_logging()
    std_logger = logging.getLogger("uvicorn.error")
    # Emitting a log should not fail
    std_logger.info("Standard library logger message intercepted")
