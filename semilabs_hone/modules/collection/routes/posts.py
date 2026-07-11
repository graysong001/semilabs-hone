"""Post browsing routes — list with filters/pagination + detail.

Design: docs/skim_design.md §13.1.
GET /posts — filter by platform/keyword, pagination
GET /posts/{id} — post detail with comments

[契约变更 2026-07-10] S3: model renamed Post→CollectionItem / Comment→
CollectionComment; PK is now a UUID str so path params are str. Reads the
retained legacy columns (content/likes/post_id/rank/keyword_id/...) which
S4/S7 will migrate onto the PRD §6.2/§6.3 columns.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()


def _templates():
    """Get shared templates from dashboard module."""
    from semilabs_hone.core.ui.routes import dashboard as dash_mod
    return dash_mod._templates


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
        text = (c.content_text or c.content or "")[:200]
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
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> HTMLResponse:
    """GET /posts — browse scraped posts with filters and pagination."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.post import CollectionItem
    from semilabs_hone.core.models.keyword import Keyword

    sess = get_session()
    try:
        q = sess.query(CollectionItem)

        if platform:
            q = q.filter(CollectionItem.platform == platform)

        if keyword:
            kw_ids = (
                sess.query(Keyword.id)
                .filter(Keyword.text.ilike(f"%{keyword}%"))
                .all()
            )
            kw_id_list = [k.id for k in kw_ids]
            if kw_id_list:
                q = q.filter(CollectionItem.keyword_id.in_(kw_id_list))

        total = q.count()
        posts = (
            q.order_by(CollectionItem.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
    except Exception:
        posts = []
        total = 0
    finally:
        sess.close()

    # Pagination helpers
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Resolve keyword text for display
    keyword_map: dict[int, str] = {}
    if posts:
        sess2 = get_session()
        try:
            kw_ids = {p.keyword_id for p in posts if p.keyword_id}
            if kw_ids:
                kws = sess2.query(Keyword).filter(Keyword.id.in_(kw_ids)).all()
                keyword_map = {kw.id: kw.text for kw in kws}
        finally:
            sess2.close()

    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "posts.html",
        {
            "posts": posts,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "keyword_map": keyword_map,
            "filter_platform": platform,
            "filter_keyword": keyword,
        },
    )


@router.get("/posts/{post_id}", response_class=HTMLResponse)
async def page_post_detail(request: Request, post_id: str) -> HTMLResponse:
    """GET /posts/{id} — post detail with comments."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.post import CollectionItem
    from semilabs_hone.core.models.comment import CollectionComment

    sess = get_session()
    try:
        post = sess.query(CollectionItem).filter(CollectionItem.id == post_id).first()
        if post is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Post not found")

        comments = (
            sess.query(CollectionComment)
            .filter(CollectionComment.post_id == post.id)
            .order_by(CollectionComment.rank)
            .all()
        )
    except Exception:
        post = None
        comments = []
    finally:
        sess.close()

    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "post_detail.html",
        {"post": post, "comments": comments},
    )
