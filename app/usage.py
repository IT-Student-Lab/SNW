# -*- coding: utf-8 -*-
"""Usage tracking: logs each successful generation to a JSONL file."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.core.log_config import get_logger

logger = get_logger(__name__)

USAGE_LOG_PATH = Path(settings.output_dir) / "usage.jsonl"


def track_generation(
    *,
    job_id: str = "",
    user: str,
    address: str = "",
    x: float = 0.0,
    y: float = 0.0,
    radius: float = 0.0,
    success: bool = True,
) -> None:
    """Append a single usage entry to the usage log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "user": user,
        "address": address,
        "x": x,
        "y": y,
        "radius": radius,
        "success": success,
    }

    try:
        USAGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(USAGE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("Usage tracked: user=%s address=%s success=%s", user, address, success)
    except Exception as e:
        logger.error("Failed to track usage: %s", e)
