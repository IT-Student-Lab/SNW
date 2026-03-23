#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CLI entry point — same functionality as the old pdok_cad_onderlegger.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.pipeline import cli_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(cli_main())
