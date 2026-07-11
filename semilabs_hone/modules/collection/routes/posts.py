"""Post browsing routes — list with filters/pagination + detail.

Design: docs/skim_design.md §13.1, PRD §4.6.1.
GET /posts — filter by platform, pagination; ordered by likes desc (PRD §4.6.1)
GET /posts/{id} — post detail with comments (Top20 by like_count desc)

[契约变更 2026-07-11 S7] L03 收口：读取已全部切到 PRD §6.2/§6.3 列
（content_text / metrics_json / publish_time / item_id / like_count）。
旧 keyword 过滤删去（PRD §4.6.1 数据预览无 keyword 维度，按 likes 排序）；
``metrics_map`` 在路由侧解一次 metrics_json 供模板渲染互动数。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


def _templates():
    """Get shared templates from dashboard module."""
    from semilabs_hone.core.ui.routes import dashboard as dash_mod
    return dash_mod._templates


def _metrics_of(item) -> dict:
    """Parse metrics_json for one item (PRD §6.4 TEXT)."""
    from semilabs_hone.core.models.repository import unpack_metrics
    return unpack_metrics(getattr(item, "metrics_json", None))


def _comments_fragment(item_id: str) -> str:
    """Render the master-detail child row (PRD §5.4.2).

    Returns `<tr id="detail-<id>"><td colspan="7">…评论子表…</td></tr>` inserted
    after the clicked main row (hx-swap=afterend). 0 评论 → 置灰文案。
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.comment import CollectionComment

    sess = get_session()
    try:
        comments = (
            sess.query(CollectionComment)
            .filter(CollectionComment.item_id == item_id)
            .order_by(CollectionComment.like_count.desc())
            .all()
        )
    except Exception:
        comments = []
    finally:
        sess.close()

    if not comments:
        return (
            f'<tr id="detail-{item_id}"><td colspan="7" '
            f'style="color: var(--pico-muted-color); text-align: center;">'
            f'该笔记暂无评论数据</td></tr>'
        )

    rows = []
    for c in comments:
        author = (c.author_name or "匿名")[:30]
        text = (c.content_text or "")[:200]
        rows.append(
            f'<tr><td colspan="7"><small>'
            f'<strong>{author}</strong> · ❤ {c.like_count or 0}<br>'
            f'{text}</small></td></tr>'
        )
    body = "".join(rows)
    return (
        f'<tr id="detail-{item_id}"><td colspan="7">'
        f'<table><tbody>{body}</tbody></table>'
        f'</td></tr>'
    )


@router.get("/api/items/{item_id}/comments")
async def api_item_comments(item_id: str) -> HTMLResponse:
    """GET /api/items/{id}/comments — master-detail child-row fragment (PRD §5.4.2).

    Clicked from posts.html main row (hx-get, hx-swap=afterend). Returns the
    `<tr>` to insert below the row, or a 置灰 row when no comments exist.
    """
    return HTMLResponse(_comments_fragment(item_id))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/posts", response_class=HTMLResponse)
async def page_posts(
    request: Request,
    platform: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> HTMLResponse:
    """GET /posts — browse scraped posts, ordered by likes desc (PRD §4.6.1)."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.post import CollectionItem

    sess = get_session()
    try:
        q = sess.query(CollectionItem)
        if platform:
            q = q.filter(CollectionItem.platform == platform)
        items = q.all()
    except Exception:
        items = []
    finally:
        sess.close()

    # PRD §4.6.1: order by likes desc. likes lives in metrics_json (TEXT),
    # so sort in Python — MVP data volumes make this fine.
    metrics_map = {it.id: _metrics_of(it) for it in items}
    items_sorted = sorted(
        items,
        key=lambda it: int(metrics_map[it.id].get("likes", 0) or 0),
        reverse=True,
    )

    total = len(items_sorted)
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    page_items = items_sorted[start:start + per_page]

    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "posts.html",
        {
            "posts": page_items,
            "metrics_map": metrics_map,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "filter_platform": platform,
        },
    )


@router.get("/posts/{post_id}", response_class=HTMLResponse)
async def page_post_detail(request: Request, post_id: str) -> HTMLResponse:
    """GET /posts/{id} — post detail with comments (Top20 by like_count desc)."""
    from fastapi import HTTPException
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.post import CollectionItem
    from semilabs_hone.core.models.comment import CollectionComment

    sess = get_session()
    try:
        post = sess.query(CollectionItem).filter(CollectionItem.id == post_id).first()
        if post is None:
            raise HTTPException(status_code=404, detail="Post not found")
        comments = (
            sess.query(CollectionComment)
            .filter(CollectionComment.item_id == post.id)
            .order_by(CollectionComment.like_count.desc())
            .all()
        )
    except HTTPException:
        raise
    except Exception:
        post = None  # type: ignore[assignment]
        comments = []
    finally:
        sess.close()

    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "post_detail.html",
        {
            "post": post,
            "metrics": _metrics_of(post) if post is not None else {},
            "comments": comments,
        },
    )
