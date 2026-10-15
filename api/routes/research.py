"""Read-only Research Hub endpoints.

Handlers only read tracked protocols, small summary artifacts and the cached sports presentation layer.
No upstream fetches, model runs, report writing or full evidence scans.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from api.schemas import ResearchExperimentDetailResponse, ResearchExperimentListResponse
from research.hub import catalog

router = APIRouter(prefix="/research", tags=["research"])


@router.get("/experiments", response_model=ResearchExperimentListResponse)
def research_experiments(category: Literal["weather", "sports"] = Query(...)) -> dict:
    return catalog.list_experiments(category)


@router.get("/experiments/{experiment_id}", response_model=ResearchExperimentDetailResponse)
def research_experiment(experiment_id: str) -> dict:
    found = catalog.get_experiment(experiment_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Unknown experiment {experiment_id}")
    return found
