"""FastAPI application factory for semilabs-hone.

create_app() builds the unified web shell:
- startup: init_db + setup_logger + scan modules/*/manifest.py to register ROUTES
- mount /static, Jinja2 templates, WS endpoint /ws
- global exception handler: SkimError -> JSON {error, category, fix_hint}

Design: docs/skim_design.md §13.1.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from semilabs_hone.core.ui.ws import ws_manager

# Module-scope state populated during startup
_module_registry: dict[str, dict] = {}


def _discover_modules() -> list[dict]:
    """Scan modules/*/manifest.py and return a list of module metadata dicts.

    Returns dicts with keys: name, module_id, routes, worker_entry.
    Fault-tolerant: missing manifest.py or failed ROUTE imports are skipped
    with a warning, never crash the app.
    """
    repo_root = Path(__file__).resolve().parents[3]
    modules_dir = repo_root / "semilabs_hone" / "modules"
    if not modules_dir.is_dir():
        return []

    results = []
    for mod_dir in sorted(modules_dir.iterdir()):
        if not mod_dir.is_dir() or not (mod_dir / "__init__.py").exists():
            continue
        manifest_path = mod_dir / "manifest.py"
        if not manifest_path.exists():
            continue

        try:
            mod_name = f"semilabs_hone.modules.{mod_dir.name}.manifest"
            manifest = importlib.import_module(mod_name)
        except Exception:
            logger.warning(f"Failed to load manifest for {mod_dir.name}, skipping")
            continue

        name = getattr(manifest, "NAME", mod_dir.name)
        module_id = getattr(manifest, "MODULE_ID", mod_dir.name)
        routes = getattr(manifest, "ROUTES", [])
        worker_entry = getattr(manifest, "WORKER_ENTRY", None)

        results.append({
            "name": name,
            "module_id": module_id,
            "routes": routes,
            "worker_entry": worker_entry,
        })

    return results


def _register_routes(app: FastAPI, modules: list[dict]) -> None:
    """Import each module's ROUTES and attach routers to app.

    Fault-tolerant: if a ROUTE module fails to import, skip with warning.
    """
    global _module_registry

    for mod in modules:
        _module_registry[mod["module_id"]] = mod
        for route_path in mod["routes"]:
            try:
                route_mod = importlib.import_module(route_path)
                router = getattr(route_mod, "router", None)
                if router is not None:
                    app.include_router(router)
                    logger.info(f"Registered router from {route_path}")
                else:
                    logger.warning(f"{route_path} has no 'router' attribute, skipping")
            except Exception:
                logger.warning(f"Failed to import route module {route_path}, skipping")


def create_app() -> FastAPI:
    """Build and configure the semilabs-hone FastAPI application."""
    app = FastAPI(title="semilabs-hone", version="0.1.0")

    @app.on_event("startup")
    async def _startup() -> None:
        from semilabs_hone.core.models.db import init_db
        from semilabs_hone.core.utils.logger import setup_logger

        init_db()
        setup_logger()

        modules = _discover_modules()
        _register_routes(app, modules)
        logger.info(f"Discovered {len(modules)} modules: {[m['name'] for m in modules]}")

    # Mount static files
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Jinja2 templates — shared via module-level variable
    templates_dir = Path(__file__).resolve().parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))
    templates.env.globals["get_modules"] = lambda: _module_registry

    # Make templates available to routes
    from semilabs_hone.core.ui.routes import dashboard as dash_mod
    dash_mod.set_templates(templates)

    # WS endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            await ws_manager.disconnect(ws)

    # Dashboard route
    app.include_router(dash_mod.router)

    # Global exception handler for SkimError
    from semilabs_hone.core.utils.retry import SkimError

    @app.exception_handler(SkimError)
    async def skim_error_handler(request: Request, exc: SkimError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": str(exc),
                "category": getattr(exc, "category", "unknown"),
                "fix_hint": getattr(exc, "fix_hint", ""),
            },
        )

    return app
