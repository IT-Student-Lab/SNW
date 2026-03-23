# -*- coding: utf-8 -*-
"""
Root entry point for Streamlit Cloud.

Streamlit Cloud is configured to start 'app.py' at the repository root.
This file forwards execution to app/main.py so the package structure is
kept intact while the deployment configuration doesn't need changing.
"""

from pathlib import Path

_src = Path(__file__).resolve().parent / "app" / "main.py"
exec(compile(_src.read_text(encoding="utf-8"), str(_src), "exec"))
