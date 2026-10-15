"""Logging configuration for Kichobot using Loguru.

Configures structured console logging and rotating file logs with memory
and disk safety guards designed for Render Free container constraints.
Also intercepts standard library logging handlers so that uvicorn, aiogram,
and motor log through the unified pipeline.
"""

from typing import Any
import sys
import logging
from pathlib import Path
from loguru import logger

# Reconfigure stdout and stderr with UTF-8 to prevent charmap UnicodeEncodeError on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.core.config import settings


class InterceptHandler(logging.Handler):
    """Intercepts standard library logging and redirects to Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging() -> None:
    """Initializes and configures all log sinks."""
    logger.remove()

    # 1. Console Sink (stdout)
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> || "
        "<level>{level: <7}</level> || "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> || "
        "<level>{message}</level>"
    )
    console_level = "DEBUG" if settings.DEBUG else "INFO"
    logger.add(
        sys.stdout,
        level=console_level,
        format=log_format,
        colorize=True,
        enqueue=True,
    )

    # Ensure logs directory exists
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    # 2. Critical error file sink (5 MB max, 2 backups)
    logger.add(
        "error_logs.log",
        level="ERROR",
        rotation="5 MB",
        retention=2,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{line} | {message}",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )

    # 3. Rotating operational log file (10 MB, 14 days, compressed zip)
    logger.add(
        "logs/{time:YYYY-MM-DD}.log",
        level="WARNING",
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{line} | {message}",
        enqueue=True,
    )

    # 4. Intercept standard library loggers
    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "aiogram", "motor"):
        std_logger = logging.getLogger(logger_name)
        std_logger.handlers = [InterceptHandler()]
        std_logger.propagate = False


def get_logger(name: str = "") -> Any:
    """Returns a logger instance optionally bound with a module name."""
    if name:
        return logger.bind(name=name)
    return logger


# Auto-setup on import
setup_logging()

__all__ = ["logger", "get_logger", "setup_logging"]
