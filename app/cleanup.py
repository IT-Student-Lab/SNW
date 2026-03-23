# -*- coding: utf-8 -*-
"""Automatic cleanup of old generated output files."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from app.config import settings
from app.core.log_config import get_logger

logger = get_logger(__name__)


def cleanup_old_files() -> int:
    """Remove output directories older than the configured max age.

    Returns the number of items removed.
    """
    output_dir = Path(settings.output_dir)
    if not output_dir.exists():
        return 0

    max_age_seconds = settings.cleanup_max_age_hours * 3600
    now = time.time()
    removed = 0

    for item in output_dir.iterdir():
        try:
            age = now - item.stat().st_mtime
            if age > max_age_seconds:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                logger.info("Cleaned up old item: %s (age %.1fh)", item.name, age / 3600)
                removed += 1
        except Exception as e:
            logger.warning("Failed to clean up %s: %s", item, e)

    return removed


def start_cleanup_scheduler() -> None:
    """Start a daemon thread that periodically cleans up old files."""
    interval = settings.cleanup_interval_minutes

    def _run() -> None:
        while True:
            try:
                n = cleanup_old_files()
                if n:
                    logger.info("Cleanup cycle complete: %d items removed", n)
            except Exception as e:
                logger.error("Cleanup error: %s", e)
            time.sleep(interval * 60)

    t = threading.Thread(target=_run, daemon=True, name="cleanup-scheduler")
    t.start()
    logger.info(
        "Cleanup scheduler started (interval=%dm, max_age=%sh)",
        interval,
        settings.cleanup_max_age_hours,
    )
