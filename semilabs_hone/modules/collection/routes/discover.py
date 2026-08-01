"""Platform Discover routes — probe URL, record XHR, generate platform.yaml.

Design: Task #7.
- GET /discover → render discover.html template
- POST /api/discover/start → submit discover IPC task
- GET /api/discover/{request_id}/result → read IPC result
- POST /api/discover/{request_id}/generate → generate YAML via llm_mapper
- POST /api/discover/{request_id}/apply → write YAML to platforms dir
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _templates():
    """Get shared templates from dashboard module."""
    from semilabs_hone.core.ui.routes import dashboard as dash_mod
    return dash_mod._templates


def _ipc_client():
    from semilabs_hone.core.ipc.client import IPCClient
    from semilabs_hone.core.ipc.protocol import IPCRequest
    return IPCClient, IPCRequest


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/discover")
async def page_discover(request: Request) -> HTMLResponse:
    """Render the platform discover page."""
    from semilabs_hone.modules.collection.scrapers.registry import list_platforms

    platform_param = request.query_params.get("platform", "")
    return_param = request.query_params.get("return", "")

    t = _templates()
    assert t is not None, "Templates not initialized"
    html = t.TemplateResponse(
        request,
        "discover.html",
        {
            "platforms": list_platforms(),
            "return_to": return_param,
            "prefill_platform": platform_param,
        },
    )
    return html


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.post("/api/discover/start")
async def api_discover_start(request: Request) -> JSONResponse:
    """POST /api/discover/start — submit a discover probe task via IPC.

    Body JSON: {target_url: str, platform_name?: str, flow_type?: str}
    Returns: {ok: true, request_id: str}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    target_url = (body.get("target_url") or "").strip()
    if not target_url or not target_url.startswith("http"):
        return JSONResponse(
            {"ok": False, "error": "target_url 必须是合法的 HTTP/HTTPS URL"},
            status_code=400,
        )

    platform_name = body.get("platform_name", "unknown")
    flow_type = body.get("flow_type", "search")

    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="discover",
        payload={
            "target_url": target_url,
            "platform_name": platform_name,
            "flow_type": flow_type,
            "request_id": request_id,
        },
    )

    client = IPCClient()
    client.submit(req)

    # Best-effort spawn worker (needs an active account for the browser)
    _ensure_worker(request)

    return JSONResponse({"ok": True, "request_id": request_id})


@router.get("/api/discover/{request_id}/result")
async def api_discover_result(request_id: str) -> JSONResponse:
    """GET /api/discover/{request_id}/result — poll IPC result file."""
    from semilabs_hone.core.ipc.paths import read_json_if_exists, result_path

    data = read_json_if_exists(result_path(request_id))
    if data is None:
        # Check progress for intermediate status
        from semilabs_hone.core.ipc.paths import progress_path
        prog = read_json_if_exists(progress_path(request_id))
        if prog:
            return JSONResponse({
                "ok": True,
                "status": "running",
                "progress": prog,
            })
        return JSONResponse({"ok": False, "status": "pending"}, status_code=202)

    status = data.get("status", "error")
    if status == "ok":
        return JSONResponse({"ok": True, "status": "ok", "data": data.get("data", {})})
    else:
        return JSONResponse({
            "ok": False,
            "status": status,
            "error": data.get("error", {}),
        }, status_code=500)


@router.post("/api/discover/{request_id}/generate")
async def api_discover_generate(request_id: str, request: Request) -> JSONResponse:
    """POST /api/discover/{request_id}/generate — generate platform.yaml from probe result.

    Reads the discover result, picks data APIs, calls llm_mapper to produce YAML.
    Returns {ok: true, yaml: str, validation: dict} on success.
    """
    from semilabs_hone.core.ipc.paths import read_json_if_exists, result_path

    # Read stored discover result
    data = read_json_if_exists(result_path(request_id))
    if data is None:
        # Also check if result was already consumed (progress may have data)
        from semilabs_hone.core.ipc.paths import progress_path
        prog = read_json_if_exists(progress_path(request_id))
        if prog and prog.get("data", {}).get("apis"):
            # Use cached progress data as fallback
            result_data = prog.get("data", {})
        else:
            return JSONResponse(
                {"ok": False, "error": "探测结果未找到，请先完成探测"},
                status_code=404,
            )
    else:
        result_data = data.get("data", {})

    apis = result_data.get("apis", [])
    containers = result_data.get("containers", [])
    page_title = result_data.get("page_title", "Unknown Platform")

    # Filter data APIs
    data_apis = [a for a in apis if a.get("category") in ("data_api", "list_data", "detail_data")]
    if not data_apis:
        return JSONResponse({
            "ok": False,
            "error": "未检测到数据 API，无法生成配置。请确认页面加载完整并触发了翻页/搜索",
        }, status_code=422)

    # Try to generate YAML via LLM
    try:
        from semilabs_hone.modules.collection.scrapers.llm_mapper import (
            build_platform_yaml,
            map_group,
        )
    except ImportError as exc:
        return JSONResponse(
            {"ok": False, "error": f"LLM mapper 模块不可用: {exc}"},
            status_code=500,
        )

    # Check anthropic availability
    try:
        from anthropic import AsyncAnthropic  # noqa: F401
        import os
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return JSONResponse({
                "ok": False,
                "error": "ANTHROPIC_API_KEY 未配置，无法调用 LLM 生成字段映射",
                "fix_hint": "在环境变量中设置 ANTHROPIC_API_KEY",
            }, status_code=503)
    except ImportError:
        return JSONResponse({
            "ok": False,
            "error": "anthropic 库未安装，无法生成映射",
            "fix_hint": "pip install anthropic",
        }, status_code=503)

    # Parse body for optional overrides
    try:
        body = await request.json()
    except Exception:
        body = {}

    platform_name = body.get("platform_name") or page_title or "unknown"

    # Pick first data API sample for mapping
    sample_api = data_apis[0]
    sample_json = sample_api.get("response_sample") or {}

    # Derive base_url from the target
    from urllib.parse import urlparse
    first_api_url = sample_api.get("url", "")
    parsed = urlparse(first_api_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else "https://unknown"

    # Standard ItemRef fields for search/list APIs
    field_specs = {
        "note_id": "笔记/帖子的唯一ID",
        "title": "标题文本",
        "author_name": "作者昵称",
        "author_id": "作者ID",
        "cover_url": "封面图片URL",
        "likes": "点赞数",
    }

    # Call LLM mapping
    try:
        field_map = await map_group(sample_json, "ItemRef", field_specs)
    except Exception as exc:
        return JSONResponse({
            "ok": False,
            "error": f"LLM 映射失败: {exc}",
        }, status_code=500)

    # Build flow steps from discovered data
    search_url_pattern = ""
    for api in data_apis:
        if api.get("category") in ("list_data", "data_api"):
            # Extract URL pattern (path without query)
            api_parsed = urlparse(api["url"])
            search_url_pattern = api_parsed.path
            break

    flows = {
        "search": {
            "steps": [
                {"type": "navigate", "url": "{base_url}/search?q={keyword}"},
                {"type": "wait_xhr", "url_pattern": search_url_pattern, "method": "GET", "save_as": "search"},
                {"type": "scroll", "max_times": 3, "wait_ms": 1500},
            ]
        }
    }
    maps = {"search": {"ItemRef": field_map}}

    # Build DOM fallback from containers
    dom_fallback = None
    if containers:
        dom_fallback = {
            "container_selector": containers[0].get("selector", ""),
            "item_count": containers[0].get("item_count", 0),
            "sample_fields": containers[0].get("sample_fields", {}),
        }

    # Generate YAML
    try:
        yaml_text = build_platform_yaml(
            display_name=platform_name,
            base_url=base_url,
            flows={"search": flows["search"]["steps"]},
            maps=maps,
        )
    except Exception as exc:
        return JSONResponse({
            "ok": False,
            "error": f"YAML 生成失败: {exc}",
        }, status_code=500)

    # Validate the field map
    from semilabs_hone.modules.collection.scrapers.llm_mapper import validate_map
    validation = validate_map(sample_json, field_map)

    return JSONResponse({
        "ok": True,
        "yaml": yaml_text,
        "platform_name": platform_name,
        "validation": validation,
        "field_map": field_map,
        "dom_fallback": dom_fallback,
        "api_count": len(data_apis),
    })


def _ensure_worker(request: Request) -> None:
    """Best-effort spawn of the collection worker for discover tasks."""
    spawner = getattr(request.app.state, "worker_spawner", None)
    if spawner is None:
        return
    # Discover does not strictly need an account, but the worker needs one
    # to launch Chrome. Find any active account.
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account

        sess = get_session()
        try:
            acct = (
                sess.query(Account)
                .filter(Account.status == "active")
                .order_by(Account.last_login_at.desc().nullslast())
                .first()
            )
            if acct:
                spawner(acct.id)
        finally:
            sess.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Apply endpoint
# ---------------------------------------------------------------------------

@router.post("/api/discover/{request_id}/apply")
async def api_discover_apply(request_id: str, request: Request) -> JSONResponse:
    """POST /api/discover/{request_id}/apply — write generated YAML to platforms dir.

    Body JSON: {platform_name: str, yaml_content: str}
    Returns: {ok: true, platform: str} on success.
    """
    from semilabs_hone.modules.collection.scrapers.registry import (
        list_platforms,
        load_registry,
        user_platforms_dir,
    )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    platform_name = (body.get("platform_name") or "").strip()
    yaml_content = (body.get("yaml_content") or "").strip()

    if not platform_name:
        return JSONResponse(
            {"ok": False, "error": "platform_name 为必填项"},
            status_code=400,
        )
    if not yaml_content:
        return JSONResponse(
            {"ok": False, "error": "yaml_content 为必填项"},
            status_code=400,
        )

    # Determine write path
    platform_dir = user_platforms_dir() / platform_name
    yaml_path = platform_dir / "platform.yaml"

    # Create directory and write YAML
    try:
        platform_dir.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(yaml_content, encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to write platform YAML to %s: %s", yaml_path, exc)
        return JSONResponse(
            {"ok": False, "error": f"写入文件失败: {exc}"},
            status_code=500,
        )

    # Force reload registry and verify
    try:
        load_registry(force=True)
        platforms = list_platforms()
    except Exception as exc:
        # Registry load itself failed — rollback
        logger.error("Registry reload failed after writing %s: %s", yaml_path, exc)
        yaml_path.unlink(missing_ok=True)
        return JSONResponse(
            {"ok": False, "error": f"YAML 格式错误，已回滚: {exc}"},
            status_code=422,
        )

    if platform_name not in platforms:
        # Platform didn't register (YAML parse error silently skipped)
        yaml_path.unlink(missing_ok=True)
        return JSONResponse(
            {"ok": False, "error": "平台未能成功注册，YAML 可能格式有误，已回滚"},
            status_code=422,
        )

    return JSONResponse({"ok": True, "platform": platform_name})
