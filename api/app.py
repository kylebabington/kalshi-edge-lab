"""FastAPI HTTP adapter for kalshi-edge-lab research outputs.

Imports research.weather.service (plain Python). No research math here.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import health, weather

app = FastAPI(
    title="Kalshi Edge Lab API",
    description="Research-only weather probability API. No trading execution.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(weather.router, prefix="/api")
