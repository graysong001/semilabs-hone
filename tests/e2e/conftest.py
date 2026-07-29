"""E2E fixtures: the local site + a recorded `localtest` platform spec.

The spec is written into the *user* platforms directory
(``data/collection/platforms/localtest/platform.yaml``) exactly like a
recording would, so the registry discovery path is exercised too
(USER_SOP G13).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.e2e.local_site import LocalSite

PLATFORM = "localtest"


def _spec(base_url: str) -> dict:
    """A platform.yaml for the local site, in the same shape a recording yields."""
    return {
        "platform": PLATFORM,
        "display_name": "本地测试站",
        "base_url": base_url,
        "login": {
            "type": "qrcode",
            "login_url": "/login",
            "success_detect": "url_change",
            "success_pattern": "^/$",
            "timeout": 30,
        },
        "flows": {
            "search": {
                "steps": [
                    {"type": "navigate", "url": "/search?keyword={keyword}&sort={sort}"},
                    {
                        "type": "wait_xhr",
                        "url_pattern": "/api/search",
                        "method": "POST",
                        "save_as": "search_resp",
                        "timeout_ms": 15000,
                    },
                    {
                        "type": "extract",
                        "from": "search_resp",
                        "group": "ItemRef",
                        "map": {
                            "item_id": "$.note_id",
                            "title": "$.display_title",
                            "author_name": "$.user.nickname",
                            "likes": "$.interact_info.liked_count",
                        },
                    },
                ]
            },
            "detail": {
                "steps": [
                    {"type": "navigate", "url": "/explore/{item_id}"},
                    {
                        "type": "wait_xhr",
                        "url_pattern": "/api/feed",
                        "method": "POST",
                        "save_as": "feed_resp",
                        "timeout_ms": 15000,
                    },
                    {
                        "type": "extract",
                        "from": "feed_resp",
                        "group": "Post.body",
                        "map": {
                            "platform_id": "$.note.note_id",
                            "title": "$.note.title",
                            "content": "$.note.desc",
                            "author_name": "$.note.user.nickname",
                            "post_type": "$.note.type",
                            "published_at": "$.note.time",
                            "image_urls": "$.note.image_list",
                            "tags": "$.note.tag_list",
                        },
                    },
                    {
                        "type": "extract",
                        "from": "feed_resp",
                        "group": "Post.interactions",
                        "map": {
                            "platform_id": "$.note.note_id",
                            "likes": "$.note.interact_info.liked_count",
                            "collects": "$.note.interact_info.collected_count",
                            "comments_count": "$.note.interact_info.comment_count",
                            "shares": "$.note.interact_info.share_count",
                        },
                    },
                ]
            },
            "comments": {
                "steps": [
                    {"type": "navigate", "url": "/explore/{item_id}"},
                    # Comments are lazy on this site, like on the real ones.
                    {"type": "scroll", "max_times": 2, "wait_ms": 400},
                    {
                        "type": "wait_xhr",
                        "url_pattern": "/api/comments",
                        "method": "GET",
                        "save_as": "cmt_resp",
                        "timeout_ms": 15000,
                    },
                    {
                        "type": "extract",
                        "from": "cmt_resp",
                        "group": "Comments",
                        "map": {
                            "platform_id": "$.id",
                            "author_name": "$.user.nickname",
                            "content": "$.content",
                            "likes": "$.like_count",
                        },
                    },
                ]
            },
        },
        "sort_values": {"general": "general", "time_descending": "latest"},
    }


@pytest.fixture
def local_site():
    """A real HTTP server standing in for a content platform."""
    with LocalSite() as site:
        yield site


@pytest.fixture
def localtest_platform(local_site, tmp_data_dir) -> str:
    """Register the local site as a user-recorded platform; returns its name."""
    from semilabs_hone.modules.collection.scrapers import registry

    spec_dir: Path = registry.user_platforms_dir() / PLATFORM
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "platform.yaml").write_text(
        yaml.safe_dump(_spec(local_site.base_url), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    registry.reset_cache()
    assert PLATFORM in registry.list_platforms()
    return PLATFORM
