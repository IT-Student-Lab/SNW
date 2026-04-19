# -*- coding: utf-8 -*-
"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)


@dataclass
class Settings:
    # Auth
    app_username: str = field(
        default_factory=lambda: os.getenv("APP_USERNAME", "admin")
    )
    app_password: str = field(
        default_factory=lambda: os.getenv("APP_PASSWORD", "changeme")
    )

    # Output
    output_dir: str = field(
        default_factory=lambda: os.getenv("OUTPUT_DIR", "output_onderlegger")
    )

    # Template
    template_dir: str = field(
        default_factory=lambda: os.getenv("TEMPLATE_DIR", "templates")
    )

    # Cleanup
    cleanup_max_age_hours: float = field(
        default_factory=lambda: float(os.getenv("CLEANUP_MAX_AGE_HOURS", "0"))
    )
    cleanup_interval_minutes: int = field(
        default_factory=lambda: int(os.getenv("CLEANUP_INTERVAL_MINUTES", "30"))
    )

    # Logging
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )

    # Future API keys (PDOK is open, but ready for future use)
    pdok_api_key: str = field(
        default_factory=lambda: os.getenv("PDOK_API_KEY", "")
    )
    pexels_api_key: str = field(
        default_factory=lambda: os.getenv("PEXELS_API_KEY", "")
    )

    # OpenAI (for AI-powered quickscan analysis)
    openai_api_key: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", "")
    )

    # JWT (for FastAPI auth)
    jwt_secret_key: str = field(
        default_factory=lambda: os.getenv("JWT_SECRET_KEY", "change-me-in-production")
    )
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = field(
        default_factory=lambda: int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
    )

    # ODA File Converter path (for .dwt/.dwg support)
    oda_fc_path: str = field(
        default_factory=lambda: os.getenv("ODA_FILE_CONVERTER", "")
    )


settings = Settings()

# Configure ezdxf to find ODA File Converter
if settings.oda_fc_path:
    import ezdxf
    ezdxf.options.set("odafc-addon", "win_exec_path", settings.oda_fc_path)
else:
    # Auto-detect common Windows install locations
    _oda_candidates = list(Path(r"C:\Program Files\ODA").glob("ODAFileConverter*/ODAFileConverter.exe"))
    if _oda_candidates:
        import ezdxf
        ezdxf.options.set("odafc-addon", "win_exec_path", str(_oda_candidates[0]))
