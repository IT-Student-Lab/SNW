# -*- coding: utf-8 -*-
"""FastAPI application entry point.

Run with:  uvicorn api.main:app --host 0.0.0.0 --port 8009 --reload
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routers import auth, files, generate, geocoding, jobs, preview, quickscan, templates
from app.cleanup import start_cleanup_scheduler
from app.core.log_config import setup_logging
from app.config import settings

_start_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    setup_logging(settings.log_level)
    start_cleanup_scheduler()
    yield


app = FastAPI(
    title="SNW CAD Onderlegger API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow React dev server + production
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev
        "http://localhost:3000",
        "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(geocoding.router)
app.include_router(generate.router)
app.include_router(preview.router)
app.include_router(files.router)
app.include_router(jobs.router)
app.include_router(quickscan.router)
app.include_router(templates.router)


@app.get("/health")
async def health():
    return {"status": "ok", "uptime_seconds": round(time.time() - _start_time, 1)}


# Serve React build in production (if the folder exists)
import os
_static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
