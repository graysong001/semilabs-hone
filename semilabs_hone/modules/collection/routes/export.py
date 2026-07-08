"""Export routes — AI CSV or Excel ZIP.

Design: docs/skim_design.md §14.
GET /api/export?task_id=&keyword=&format=ai|excel
Calls DM-10 csv_exporter directly (reads SQLite, no worker needed).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()


@router.get("/api/export", response_model=None)
async def api_export(
    task_id: int | None = Query(default=None),
    keyword: str | None = Query(default=None),
    fmt: str = Query(default="ai", alias="format"),
) -> None:
    """GET /api/export — export scraped data as CSV or Excel ZIP.

    Args:
        task_id: filter by scrape task ID.
        keyword: filter by keyword text.
        fmt: "ai" (single CSV) or "excel" (ZIP with posts.csv + comments.csv).
    """
    if fmt not in ("ai", "excel"):
        return JSONResponse(
            {"ok": False, "error": "format must be 'ai' or 'excel'"},
            status_code=400,
        )

    try:
        from semilabs_hone.modules.collection.export.csv_exporter import export_csv
        out_path: Path = export_csv(task_id=task_id, keyword=keyword, fmt=fmt)
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

    filename = out_path.name
    media_type = "text/csv" if fmt == "ai" else "application/zip"

    return FileResponse(
        path=str(out_path),
        filename=filename,
        media_type=media_type,
    )
