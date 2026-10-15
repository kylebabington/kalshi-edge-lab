"""FastAPI HTTP adapter for kalshi-edge-lab research outputs.

Imports research.weather.service (plain Python). No research math here.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import health, opportunities, sports, weather
from research.ai.config import load_env_file
from research.sports import service as sports_service

log = logging.getLogger(__name__)
load_env_file()


def _warm_sports() -> None:
    try:
        sports_service.warm()
    except Exception:  # a failed warm-up only means the first request does the work
        log.exception("sports index warm-up failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warm_sports, name="sports-warm", daemon=True).start()
    yield


app = FastAPI(
    title="Kalshi Edge Lab API",
    description="Research-only weather and sports probability API. No trading execution.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(weather.router, prefix="/api")
app.include_router(sports.router, prefix="/api")
app.include_router(opportunities.router, prefix="/api")
