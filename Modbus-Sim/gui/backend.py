"""FastAPI application for the local Modbus-Sim engineering workbench."""

from __future__ import annotations

import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from serial.tools import list_ports

from .. import __version__
from ..config import ConfigError, TransportMode, load_config, load_config_text
from ..runtime import SimulatorRuntime


STATIC_DIR = Path(__file__).resolve().parent / "static"


class StartRequest(BaseModel):
    transport: str | None = None


class AreaSizesRequest(BaseModel):
    coils: int = Field(default=16, ge=1, le=65536)
    discrete_inputs: int = Field(default=16, ge=1, le=65536)
    holding_registers: int = Field(default=16, ge=1, le=65536)
    input_registers: int = Field(default=16, ge=1, le=65536)


class DeviceRequest(BaseModel):
    unit_id: int = Field(ge=1, le=247)
    name: str = ""
    sizes: AreaSizesRequest = Field(default_factory=AreaSizesRequest)


class PointDefinitionRequest(BaseModel):
    value: Any
    alias: str = ""
    description: str = ""
    format: str = "uint16"


class LiveValueRequest(BaseModel):
    value: Any


def create_app(config_path: str | Path | None = None) -> FastAPI:
    runtime = SimulatorRuntime(load_config(config_path))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if runtime.running:
            await runtime.stop()

    app = FastAPI(
        title="Modbus-Sim",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(ConfigError)
    async def config_error_handler(_request: Request, exc: ConfigError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/version")
    async def version():
        return {"version": __version__, "product": "Modbus-Sim"}

    @app.get("/api/health")
    async def health():
        return {"ok": True, "running": runtime.running}

    @app.get("/api/state")
    async def state():
        return runtime.state()

    @app.get("/api/config")
    async def get_config():
        return runtime.config_payload()

    @app.put("/api/config")
    async def put_config(payload: dict[str, Any] = Body(...)):
        await runtime.replace_config(payload)
        return {"ok": True, "config": runtime.config_payload()}

    @app.get("/api/config/yaml")
    async def export_config():
        headers = {"Content-Disposition": 'attachment; filename="modbus-sim.yaml"'}
        return PlainTextResponse(runtime.config_yaml(), media_type="application/yaml", headers=headers)

    @app.put("/api/config/yaml")
    async def import_config(request: Request):
        text = (await request.body()).decode("utf-8")
        await runtime.replace_config(_payload_from_text(text))
        return {"ok": True, "config": runtime.config_payload()}

    @app.post("/api/transport/{mode}")
    async def choose_transport(mode: str):
        await runtime.select_transport(TransportMode.parse(mode))
        return {"ok": True, "config": runtime.config_payload()}

    @app.get("/api/serial-ports")
    async def serial_ports():
        return {
            "ports": [
                {
                    "device": item.device,
                    "description": item.description,
                    "manufacturer": item.manufacturer,
                    "serial_number": item.serial_number,
                }
                for item in list_ports.comports()
            ]
        }

    @app.post("/api/start")
    async def start(request: StartRequest):
        return await runtime.start(request.transport)

    @app.post("/api/stop")
    async def stop():
        return await runtime.stop()

    @app.get("/api/registers")
    async def registers(
        unit_id: int = Query(ge=1, le=247),
        area: str = Query(...),
    ):
        return await runtime.register_rows(unit_id, area)

    @app.put("/api/devices/{unit_id}/areas/{area}/points/{address}")
    async def update_point(unit_id: int, area: str, address: int, request: PointDefinitionRequest):
        await runtime.update_point_definition(unit_id, area, address, request.model_dump())
        return await runtime.register_rows(unit_id, area)

    @app.put("/api/devices/{unit_id}/areas/{area}/values/{address}")
    async def write_value(unit_id: int, area: str, address: int, request: LiveValueRequest):
        await runtime.write_live_value(unit_id, area, address, request.value)
        return await runtime.register_rows(unit_id, area)

    @app.post("/api/devices")
    async def create_device(request: DeviceRequest):
        await runtime.add_device(request.unit_id, request.name, request.sizes.model_dump())
        return {"ok": True, "config": runtime.config_payload()}

    @app.put("/api/devices/{unit_id}")
    async def edit_device(unit_id: int, request: DeviceRequest):
        await runtime.update_device(unit_id, request.unit_id, request.name, request.sizes.model_dump())
        return {"ok": True, "config": runtime.config_payload()}

    @app.delete("/api/devices/{unit_id}")
    async def delete_device(unit_id: int):
        await runtime.remove_device(unit_id)
        return {"ok": True, "config": runtime.config_payload()}

    @app.get("/api/traffic")
    async def traffic(after: int = Query(0, ge=0)):
        return runtime.traffic(after)

    @app.delete("/api/traffic")
    async def clear_traffic():
        runtime.clear_traffic()
        return {"ok": True}

    return app


def _payload_from_text(text: str) -> dict[str, Any]:
    """Parse and normalize YAML once, then pass a plain payload to the runtime."""
    from ..config import config_to_dict

    return config_to_dict(load_config_text(text))


def run_gui(
    *,
    host: str = "127.0.0.1",
    port: int = 18865,
    config_path: str | Path | None = None,
    open_browser: bool = True,
) -> None:
    app = create_app(config_path)
    if open_browser:
        url_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        timer = threading.Timer(0.8, webbrowser.open, args=(f"http://{url_host}:{port}/",))
        timer.daemon = True
        timer.start()
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> int:
    run_gui()
    return 0
