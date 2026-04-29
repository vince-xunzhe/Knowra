import logging
from pathlib import Path

from path_utils import DATA_DIR


LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "backend.log"


def configure_app_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("knowra")
    if logger.handlers:
        return LOG_FILE

    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return LOG_FILE


def get_logger(name: str) -> logging.Logger:
    configure_app_logging()
    return logging.getLogger(f"knowra.{name}")
