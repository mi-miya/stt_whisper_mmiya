import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# app.log が際限なく肥大化しないよう、この大きさで app.log.1 〜 に退避する
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

def setup_logger(name: str = "local_dictation"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # File handler
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger

logger = setup_logger()
