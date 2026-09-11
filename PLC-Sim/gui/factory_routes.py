"""GUI routes for the fourth feature: CSV to Uni-Lab-OS device package."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
try:
    from ..simulation_factory import SimulationFactory
    from ..common import runtime_data_dir
except ImportError:
    from simulation_factory import SimulationFactory
    from common import runtime_data_dir

router = APIRouter(prefix="/api/factory", tags=["device-package-factory"])
factory = SimulationFactory()

class SourceReq(BaseModel):
    source: str

class BuildReq(BaseModel):
    source: str
    output: str | None = None
    patch: dict[str, Any] | None = None

@router.post("/inspect")
def inspect(req: SourceReq):
    try: return factory.inspect(req.source)
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc)) from exc

@router.post("/build")
def build(req: BuildReq):
    output = Path(req.output).expanduser() if req.output else runtime_data_dir() / "factory" / Path(req.source).stem
    try: return factory.build(req.source, output, req.patch)
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc)) from exc

@router.post("/validate")
def validate(req: SourceReq):
    try: return factory.validate(req.source)
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc)) from exc
