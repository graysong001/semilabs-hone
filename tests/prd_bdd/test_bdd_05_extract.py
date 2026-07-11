"""PRD §8.5 — 数据提取与清洗边界验收 (Data Extraction & Cleansing).

BDD acceptance tests for scenarios 5.1 (点赞清洗) and 5.2 (极端缺失 DOM 兜底).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from semilabs_hone.modules.collection.scrapers.field_extract import (
    parse_likes,
    title_fallback,
)


# ─── 场景 5.1：点赞/互动数据的格式清洗 ─────────────────────────────────

class TestScenario51LikesCleansing:
    """PRD §8.5 场景 5.1.

    Given 平台页面的点赞数 DOM 显示为 "1.5万"、"1.2w" 或 "赞".
    When  Worker 提取数据入库.
    Then  必须有专用的字符串清洗函数。"1.5万"/"1.5w" 转换为整型 15000，"赞" 转换为 0.
          入库的 metrics_json.likes 必须是合法的数值.
    """

    @pytest.mark.parametrize("raw,expected", [
        ("1.5万", 15000),
        ("1.5w", 15000),
        ("1.2w", 12000),
        ("赞", 0),
        ("1.2万", 12000),
        ("3.5k", 3500),
        ("2千", 2000),
        ("", 0),
        (None, 0),
    ])
    def test_parse_likes_yields_legal_int(self, raw, expected):
        """parse_likes converts Chinese/w/k/千 units + bare 赞 to ints.

        PRD 5.1 Then: "1.5万"/"1.5w"→15000, "赞"→0, 入库值必须是合法数值.
        """
        result = parse_likes(raw)
        # Must be a legal numeric (int), exactly the expected value.
        assert isinstance(result, int)
        assert result == expected

    def test_parse_likes_result_is_json_safe_number(self):
        """The cleaned value serializes cleanly into metrics_json.likes."""
        import json
        for raw in ["1.5万", "1.2w", "赞", "3.5k"]:
            val = parse_likes(raw)
            # metrics_json.likes must serialize without error (合法数值).
            assert json.loads(json.dumps({"likes": val}))["likes"] == val


# ─── 场景 5.2：极端缺失 DOM 的容错兜底 ─────────────────────────────────

class TestScenario52MissingDomFallback:
    """PRD §8.5 场景 5.2.

    Given 一篇笔记没有标题，没有配图，作者禁用了评论区.
    When  Worker 尝试提取数据.
    Then  标题取正文前 20 字符。评论区无法提取时不报错，主记录成功 UPSERT 入库，
          关联的 collection_comments 表无数据。单条数据成功流转，进度向前.
    """

    def test_title_fallback_takes_content_first_20_chars(self):
        """No title → title falls back to content[:20].

        PRD 5.2 Then: 标题取正文前 20 字符.
        """
        content = "这是一段很长的正文内容用于测试当标题缺失时的兜底逻辑行为"
        assert title_fallback("", content) == content[:20]
        assert title_fallback(None, content) == content[:20]
        # No content either → empty string, not an error.
        assert title_fallback(None, None) == ""

    async def test_comments_disabled_upsert_succeeds_zero_comments(self, db_session, tmp_data_dir):
        """A note with no title + comments disabled: upsert succeeds, 0 comments stored.

        PRD 5.2 Then: 评论区无法提取时不报错，主记录成功 UPSERT，collection_comments 无数据,
        进度向前.
        """
        from semilabs_hone.core.models.schemas import ScrapedPost
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.comment import CollectionComment
        from semilabs_hone.modules.collection.handlers import handler_scrape_task
        import semilabs_hone.modules.collection.handlers as h_mod
        from tests.prd_bdd.conftest import _patch_handler_env, _restore_handler_env, _make_task

        class FakeRef:
            def __init__(self, item_id):
                self.item_id = item_id

        long_content = "这是没有标题的笔记的正文内容应当被用作标题兜底"

        async def mock_search(keyword, sort):
            return [FakeRef("n_disabled")]

        async def mock_fetch_item(ref):
            # No title, no images, author disabled comments.
            return ScrapedPost(platform_id=ref.item_id, title=None,
                               content=long_content, image_urls=None)

        async def mock_fetch_comments(ref):
            # Comments section disabled → returns empty, no error.
            return []

        mock_engine = MagicMock()
        mock_engine.search = mock_search
        mock_engine.fetch_item = mock_fetch_item
        mock_engine.fetch_comments = mock_fetch_comments
        mock_engine.page = None

        task_id = _make_task(db_session, max_posts=5)
        orig = _patch_handler_env(h_mod, mock_engine)
        progress = []

        def cap(m, d=None):
            progress.append((m, d))

        try:
            result = await handler_scrape_task({
                "task_id": task_id, "platform": "xiaohongshu",
                "keywords": ["kw"], "sort": "general",
                "max_posts_per_keyword": 5, "download_images": True,
                "collect_comments": True, "account_id": 1,
                "request_id": "bdd-5-2",
            }, cap)

            # Then: task completes, the note was stored (progress forward).
            assert result["status"] == "ok"
            assert result["posts_scraped"] == 1

            sess = get_session()
            try:
                from semilabs_hone.core.models.post import CollectionItem
                item = sess.query(CollectionItem).filter(
                    CollectionItem.platform_id == "n_disabled").first()
                # Main record UPSERT succeeded; title fell back to content[:20].
                assert item is not None
                assert item.title == long_content[:20]
                # collection_comments table has no data for this item.
                cmts = sess.query(CollectionComment).filter(
                    CollectionComment.item_id == item.id).all()
                assert len(cmts) == 0
            finally:
                sess.close()
        finally:
            _restore_handler_env(h_mod, orig)
