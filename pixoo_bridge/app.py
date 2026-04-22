from __future__ import annotations

from typing import Any

from fastapi import Body, FastAPI, HTTPException

from . import __version__
from .bridge import BridgeService, InvalidPayloadError, TransportError


def create_app(service: BridgeService | None = None) -> FastAPI:
    bridge = service or BridgeService()
    app = FastAPI(
        title="Claude Code Pixoo Bridge",
        version=__version__,
        description=(
            "Receives Claude Code hooks and status snapshots, keeps a small "
            "per-source state cache in memory, derives a global Pixoo scene, "
            "and presents it through the configured transport."
        ),
    )

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return bridge.health()

    @app.get("/debug/state")
    def debug_state() -> dict[str, Any]:
        return bridge.snapshot()

    @app.get("/sessions", include_in_schema=False)
    def sessions_compat() -> dict[str, Any]:
        return bridge.snapshot()

    @app.post("/hooks")
    def hooks(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return bridge.ingest_hook(payload)
        except InvalidPayloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TransportError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/status")
    def status(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return bridge.ingest_status(payload)
        except InvalidPayloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TransportError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app
