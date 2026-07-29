"""Export route — PRD §4.6 left-join wide-table CSV.

Design: docs/skim_design.md §14, PRD §4.6.
GET /api/export?task_id=&  →  wide-table CSV (10 中文表头, utf-8-sig BOM).
Calls csv_exporter directly (reads SQLite, no worker needed). 0 records →
400 JSON so the frontend ``exportCsv`` shows a Toast (PRD §4.6: 0 条 → 拦截).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()


@router.get("/api/export", response_model=None)
async def api_export(
    task_id: str | None = Query(default=None),
) -> None:
    """GET /api/export — export scraped data as a left-join wide-table CSV.

    Args:
        task_id: filter by collection task ID (None = all tasks).
    """
    try:
        from semilabs_hone.modules.collection.export.csv_exporter import (
            export_csv,
            EmptyExportError,
        )
        out_path: Path = export_csv(task_id=task_id)
    except EmptyExportError as exc:
        # 0 条 → 拦截 + Toast (PRD §4.6)
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=400,
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=500,
        )

    if not out_path.exists():
        return JSONResponse(
            {"ok": False, "error": "Export file not found"},
            status_code=500,
        )

    return FileResponse(
        path=str(out_path),
        filename=out_path.name,
        media_type="text/csv",
    )
