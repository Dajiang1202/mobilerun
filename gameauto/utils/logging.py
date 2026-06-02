"""Multi-level logging for game automation.

Levels:
  DEBUG   — per-frame details (ROI crops, confidence scores, intermediate steps)
  INFO    — key events (state changes, actions, decisions)
  GAME    — human-readable game log (for post-game review)
  WARNING — recoverable anomalies
  ERROR   — critical failures (device disconnect, model crash)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

GAME_LOG_LEVEL = 25  # between INFO (20) and WARNING (30)
logging.addLevelName(GAME_LOG_LEVEL, "GAME")


def game_log(self, message, *args, **kwargs):
    """Log at GAME level — human-readable decision descriptions."""
    if self.isEnabledFor(GAME_LOG_LEVEL):
        self._log(GAME_LOG_LEVEL, message, args, **kwargs)


logging.Logger.game = game_log  # type: ignore[attr-defined]


class GameLogFilter(logging.Filter):
    """Only pass GAME-level records to the game log file."""
    def filter(self, record):
        return record.levelno == GAME_LOG_LEVEL


def setup_logging(
    session_dir: str | Path,
    console_level: str = "INFO",
) -> logging.Logger:
    """Configure multi-level logging for a game session.

    Returns the root gameauto logger.
    """
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("gameauto")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # Console: colored summary (dev use)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S",
    ))
    logger.addHandler(console)

    # File: full structured log (troubleshooting)
    file_handler = logging.FileHandler(session_dir / "debug.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    ))
    logger.addHandler(file_handler)

    # Game log: human-readable decisions only (review)
    game_handler = logging.FileHandler(session_dir / "game.log", encoding="utf-8")
    game_handler.setLevel(GAME_LOG_LEVEL)
    game_handler.addFilter(GameLogFilter())
    game_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    logger.addHandler(game_handler)

    return logger
