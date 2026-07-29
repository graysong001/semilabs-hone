"""CSV flat-table export tests (DM-10 / PRD §4.6).

Naming: test_<method>_<scenario>_<expected>.
Uses the db_session fixture from tests/conftest.py for data seeding via the
PRD repository upserts (content_text / metrics_json / like_count).
Image downloader tests mock httpx + shutil.disk_usage.
"""
from __future__ import annotations

import asyncio
import csv
import shutil
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from semilabs_hone.modules.collection.export.csv_exporter import (
    HEADERS,
    EmptyExportError,
    export_csv,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _seed_item(db_session, *, platform_id, title="Test Post", content="Hello world",
               likes=100, comments_count=2, url=None, publish_time="2026-07-08 14:00:00",
               task_id=None):
    """Upsert one item via the PRD repository; returns the ORM row."""
    from semilabs_hone.core.models import repository as repo

    return repo.upsert_item(
        db_session,
        task_id=task_id,
        platform="xiaohongshu",
        platform_id=platform_id,
        url=url,
        title=title,
        content_text=content,
        author_name="AuthorA",
        metrics={"likes": likes, "comments_count": comments_count, "collects": 20, "shares": 5},
        publish_time=publish_time,
    )


def _seed_comment(db_session, *, item_id, platform_comment_id, author="UserX",
                  content="Great!", likes=50):
    from semilabs_hone.core.models import repository as repo

    return repo.upsert_comment(
        db_session,
        item_id=item_id,
        platform_comment_id=platform_comment_id,
        author_name=author,
        content_text=content,
        like_count=likes,
    )


def _read_csv(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Return (raw_text, rows). raw_text keeps the BOM for assertion."""
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    return raw.decode("utf-8"), rows


# ── headers + BOM ────────────────────────────────────────────────────────

class TestExportHeaders:

    def test_headers_are_ten_chinese_in_order(self):
        """PRD §4.6.3: exactly 10 Chinese headers in the mandated order."""
        assert HEADERS == [
            "平台", "笔记ID", "笔记标题", "笔记正文", "笔记点赞数",
            "笔记发布时间", "笔记链接", "评论者昵称", "评论内容", "评论点赞数",
        ]

    def test_export_csv_has_bom_and_chinese_header(self, db_session):
        """File is utf-8-sig (BOM) with the 10 Chinese headers (PRD §4.6.2)."""
        _seed_item(db_session, platform_id="note_001")
        path = export_csv()
        raw, _ = _read_csv(path)
        assert raw.startswith("﻿")  # BOM present (utf-8-sig)
        assert ",".join(HEADERS) in raw


# ── left-join flat logic (PRD §4.6.2) ────────────────────────────────────

class TestExportLeftJoin:

    def test_one_note_two_comments_two_rows_note_cols_repeat(self, db_session):
        """1 note × 2 comments → 2 rows; note columns identical across rows."""
        item = _seed_item(db_session, platform_id="note_001", content="正文A", likes=10)
        _seed_comment(db_session, item_id=item.id, platform_comment_id="c1",
                      author="UserX", content="Great!", likes=50)
        _seed_comment(db_session, item_id=item.id, platform_comment_id="c2",
                      author="UserY", content="Nice", likes=30)

        _, rows = _read_csv(export_csv())
        assert len(rows) == 2
        # note columns repeat
        assert rows[0]["平台"] == rows[1]["平台"] == "xiaohongshu"
        assert rows[0]["笔记ID"] == rows[1]["笔记ID"] == "note_001"
        assert rows[0]["笔记正文"] == rows[1]["笔记正文"] == "正文A"
        # comments map to distinct rows, ordered by likes desc (c1=50 first)
        assert rows[0]["评论者昵称"] == "UserX"
        assert rows[0]["评论内容"] == "Great!"
        assert rows[0]["评论点赞数"] == "50"
        assert rows[1]["评论者昵称"] == "UserY"
        assert rows[1]["评论点赞数"] == "30"

    def test_zero_comments_one_row_comment_cols_empty(self, db_session):
        """0 comments → 1 row with comment columns empty (PRD §4.6.2)."""
        _seed_item(db_session, platform_id="note_002")
        _, rows = _read_csv(export_csv())
        assert len(rows) == 1
        assert rows[0]["笔记ID"] == "note_002"
        assert rows[0]["评论者昵称"] == ""
        assert rows[0]["评论内容"] == ""
        assert rows[0]["评论点赞数"] == ""

    def test_likes_read_from_metrics_json(self, db_session):
        """笔记点赞数 is parsed from metrics_json (PRD §6.4 TEXT)."""
        _seed_item(db_session, platform_id="note_003", likes=1500, comments_count=8)
        _, rows = _read_csv(export_csv())
        assert rows[0]["笔记点赞数"] == "1500"

    def test_publish_time_and_url_columns(self, db_session):
        """笔记发布时间 + 笔记链接 come from publish_time / url."""
        _seed_item(db_session, platform_id="note_004", url="https://xhs.link/note_004",
                   publish_time="2026-07-08 14:00:00")
        _, rows = _read_csv(export_csv())
        assert rows[0]["笔记发布时间"] == "2026-07-08 14:00:00"
        assert rows[0]["笔记链接"] == "https://xhs.link/note_004"

    def test_ordered_by_likes_desc(self, db_session):
        """Notes are ordered by 笔记点赞数 descending (PRD §4.6.1)."""
        _seed_item(db_session, platform_id="lo", likes=5)
        _seed_item(db_session, platform_id="hi", likes=9999)
        _seed_item(db_session, platform_id="mid", likes=50)
        _, rows = _read_csv(export_csv())
        ids = [r["笔记ID"] for r in rows]
        assert ids == ["hi", "mid", "lo"]


# ── escaping (PRD §8.6 场景6.1) ───────────────────────────────────────────

class TestExportEscaping:

    def test_comma_quote_emoji_escaped_correctly(self, db_session):
        """csv.DictWriter quotes fields with comma/quote; emoji survives intact."""
        tricky = '你好,世界｜"冒号"emoji😀'
        item = _seed_item(db_session, platform_id="note_tricky", content=tricky, likes=1)
        _seed_comment(db_session, item_id=item.id, platform_comment_id="ct",
                      content="评论,带逗号", likes=1)

        raw, rows = _read_csv(export_csv())
        # 1 note × 1 comment → 1 row, no 错行 from the embedded comma/quote
        assert len(rows) == 1
        assert rows[0]["笔记正文"] == tricky  # emoji + comma + quote field survives
        assert rows[0]["评论内容"] == "评论,带逗号"
        # verify round-trip via csv reader already proved escaping correctness


# ── empty defense (PRD §4.6: 0 条 → 拦截) ────────────────────────────────

class TestExportEmpty:

    def test_zero_notes_raises_empty_export_error(self, db_session):
        """Exporter raises EmptyExportError when 0 notes match."""
        with pytest.raises(EmptyExportError):
            export_csv()

    def test_filter_task_id_no_match_raises(self, db_session):
        """task_id filter matching nothing also raises."""
        _seed_item(db_session, platform_id="note_001", task_id="t-1")
        with pytest.raises(EmptyExportError):
            export_csv(task_id="t-does-not-exist")


# ── task_id filter ────────────────────────────────────────────────────────

class TestExportFiltering:

    def test_filter_by_task_id_keeps_only_matching(self, db_session):
        """Only notes for the given task_id are exported."""
        _seed_item(db_session, platform_id="n_a", task_id="t-1")
        _seed_item(db_session, platform_id="n_b", task_id="t-2")
        _, rows = _read_csv(export_csv(task_id="t-1"))
        assert len(rows) == 1
        assert rows[0]["笔记ID"] == "n_a"


# ── route layer (PRD §4.6: 0 条 → 拦截 + Toast) ──────────────────────────

def _make_export_app():
    app = FastAPI()
    from semilabs_hone.modules.collection.routes import export as exp_mod
    app.include_router(exp_mod.router)
    return app


class TestExportRoute:

    def test_get_export_with_data_returns_csv(self, db_session):
        """GET /api/export with data → 200 text/csv file download."""
        _seed_item(db_session, platform_id="note_001")
        client = TestClient(_make_export_app())
        resp = client.get("/api/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        # Chinese headers present in the streamed body
        assert "笔记ID" in resp.text

    def test_get_export_empty_returns_400_json(self, db_session):
        """GET /api/export with 0 notes → 400 JSON for frontend Toast."""
        client = TestClient(_make_export_app())
        resp = client.get("/api/export")
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]


# ── image_downloader: download_images ────────────────────────────────────

class TestDownloadImages:

    @pytest.mark.asyncio
    async def test_download_images_success(self, db_session, tmp_path, monkeypatch):
        """Successful downloads return list of Paths."""
        import config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_WARN_GB", 999, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_STOP_GB", None, raising=False)

        # Mock httpx
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.content = b"fake_image_data"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_httpx = MagicMock()
        fake_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            from semilabs_hone.core.utils import image_downloader as img_dl
            # Force reimport to pick up mocked httpx
            import importlib
            importlib.reload(img_dl)

            urls = ["http://example.com/img1.jpg", "http://example.com/img2.png"]
            results = await img_dl.download_images(urls, "test_note")

            assert len(results) == 2
            for r in results:
                assert r.exists()

    @pytest.mark.asyncio
    async def test_download_images_single_failure_not_blocking(
        self, db_session, tmp_path, monkeypatch
    ):
        """One failed download doesn't block others."""
        import config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_WARN_GB", 999, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_STOP_GB", None, raising=False)

        ok_response = MagicMock()
        ok_response.raise_for_status = MagicMock()
        ok_response.content = b"ok_image"

        async def _get(url):
            if "fail" in url:
                raise Exception("network error")
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.content = b"ok_image"
            return resp

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_get)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_httpx = MagicMock()
        fake_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            from semilabs_hone.core.utils import image_downloader as img_dl
            import importlib
            importlib.reload(img_dl)

            urls = [
                "http://example.com/ok.jpg",
                "http://example.com/fail.jpg",
                "http://example.com/ok2.png",
            ]
            results = await img_dl.download_images(urls, "partial_note")
            assert len(results) == 2  # 2 succeed, 1 fails

    @pytest.mark.asyncio
    async def test_download_images_disk_stop_raises(self, db_session, tmp_path, monkeypatch):
        """When disk is full, DiskFullError is raised."""
        import config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_WARN_GB", 0, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_STOP_GB", 0, raising=False)

        # Ensure images dir is empty but pretend it has files
        images_dir = tmp_path / "collection" / "images" / "fake_note"
        images_dir.mkdir(parents=True)

        fake_httpx = MagicMock()

        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            from semilabs_hone.core.utils import image_downloader as img_dl
            import importlib
            importlib.reload(img_dl)

            from semilabs_hone.core.utils.retry import DiskFullError
            with pytest.raises(DiskFullError):
                await img_dl.download_images(["http://x.com/a.jpg"], "fake_note")


# ── image_downloader: check_disk ─────────────────────────────────────────

class TestCheckDisk:

    @pytest.mark.asyncio
    async def test_check_disk_empty(self, db_session, tmp_path, monkeypatch):
        """Empty images dir returns warn=False, stop=False."""
        import config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_WARN_GB", 30, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_STOP_GB", None, raising=False)

        from semilabs_hone.core.utils import image_downloader as img_dl
        import importlib
        importlib.reload(img_dl)

        status = await img_dl.check_disk()
        assert status.warn is False
        assert status.stop is False
        assert status.total_gb == 0

    @pytest.mark.asyncio
    async def test_check_disk_warn_over_30gb(self, db_session, tmp_path, monkeypatch):
        """Directory > 30 GB triggers warn=True."""
        import config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_WARN_GB", 30, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_STOP_GB", None, raising=False)

        images_dir = tmp_path / "collection" / "images"
        images_dir.mkdir(parents=True)

        def fake_size(directory):
            return 31 * 1024 ** 3  # 31 GB

        from semilabs_hone.core.utils import image_downloader as img_dl
        import importlib
        importlib.reload(img_dl)

        with patch.object(img_dl, "_dir_size_bytes", fake_size):
            status = await img_dl.check_disk()
            assert status.warn is True
            assert status.stop is False

    @pytest.mark.asyncio
    async def test_check_disk_stop_over_configurable(self, db_session, tmp_path, monkeypatch):
        """Directory >= stop threshold triggers stop=True."""
        import config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_WARN_GB", 30, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_STOP_GB", 50, raising=False)

        images_dir = tmp_path / "collection" / "images"
        images_dir.mkdir(parents=True)

        def fake_size(directory):
            return 51 * 1024 ** 3  # 51 GB

        from semilabs_hone.core.utils import image_downloader as img_dl
        import importlib
        importlib.reload(img_dl)

        with patch.object(img_dl, "_dir_size_bytes", fake_size):
            status = await img_dl.check_disk()
            assert status.stop is True
            assert status.warn is True

    @pytest.mark.asyncio
    async def test_check_disk_low_free_space_warn(self, db_session, tmp_path, monkeypatch):
        """Partition free space < 2 GB triggers warn."""
        import config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_WARN_GB", 999, raising=False)
        monkeypatch.setattr(cfg, "IMAGE_DISK_STOP_GB", None, raising=False)

        images_dir = tmp_path / "collection" / "images"
        images_dir.mkdir(parents=True)

        fake_usage = unittest.mock.Mock(total=100_000_000_000, used=99_000_000_000, free=1_000_000_000)

        from semilabs_hone.core.utils import image_downloader as img_dl
        import importlib
        importlib.reload(img_dl)

        with patch("shutil.disk_usage", return_value=fake_usage):
            status = await img_dl.check_disk()
            assert status.warn is True
