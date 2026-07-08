"""Post browsing routes — list with filters/pagination + detail.

Design: docs/skim_design.md §13.1.
GET /posts — filter by platform/keyword, pagination
GET /posts/{id} — post detail with comments
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()


def _templates():
    """Get shared templates from dashboard module."""
    from semilabs_hone.core.ui.routes import dashboard as dash_mod
    return dash_mod._templates


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
    from semilabs_hone.core.models.post import Post
    from semilabs_hone.core.models.keyword import Keyword

    sess = get_session()
    try:
        q = sess.query(Post)

        if platform:
            q = q.filter(Post.platform == platform)

        if keyword:
            kw_ids = (
                sess.query(Keyword.id)
                .filter(Keyword.text.ilike(f"%{keyword}%"))
                .all()
            )
            kw_id_list = [k.id for k in kw_ids]
            if kw_id_list:
                q = q.filter(Post.keyword_id.in_(kw_id_list))

        total = q.count()
        posts = (
            q.order_by(Post.id.desc())
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
async def page_post_detail(request: Request, post_id: int) -> HTMLResponse:
    """GET /posts/{id} — post detail with comments."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.post import Post
    from semilabs_hone.core.models.comment import Comment

    sess = get_session()
    try:
        post = sess.query(Post).filter(Post.id == post_id).first()
        if post is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Post not found")

        comments = (
            sess.query(Comment)
            .filter(Comment.post_id == post.id)
            .order_by(Comment.rank)
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
