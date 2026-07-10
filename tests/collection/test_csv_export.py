"""CSV export + image downloader tests (DM-10).

Naming: test_<method>_<scenario>_<expected>.
Uses the db_session fixture from tests/conftest.py for data seeding.
Image downloader tests mock httpx + shutil.disk_usage.
"""
from __future__ import annotations

import asyncio
import csv
import shutil
import unittest.mock
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from semilabs_hone.modules.collection.export.csv_exporter import (
    export_csv,
    export_empty_db,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _seed_basic(db_session):
    """Insert one post + two comments."""
    from semilabs_hone.core.models.post import CollectionItem
    from semilabs_hone.core.models.comment import CollectionComment
    from semilabs_hone.core.models.keyword import Keyword
    from semilabs_hone.core.models.task import CollectionTask

    task = CollectionTask(id=1, account_id=1, platform="xiaohongshu", status="completed")
    kw = Keyword(id=1, text="test_kw", platform="xiaohongshu")
    post = CollectionItem(
        id=1, platform="xiaohongshu", platform_id="note_001",
        task_id=1, keyword_id=1, url="https://xhs.link/note_001",
        title="Test Post", author_name="AuthorA", content="Hello world",
        tags="tag1|tag2", post_type="video", likes=100, collects=20,
        comments_count=2, shares=5, published_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        image_count=3, scraped_at=datetime(2025, 7, 1, tzinfo=timezone.utc),
    )
    c1 = CollectionComment(id=1, post_id=1, author_name="UserX", content="Great!", likes=50,
                 sub_comment_count=2, is_author_liked=True, rank=1)
    c2 = CollectionComment(id=2, post_id=1, author_name="UserY", content="Nice", likes=30,
                 sub_comment_count=0, is_author_liked=False, rank=2)
    for obj in [task, kw, post, c1, c2]:
        db_session.add(obj)
    db_session.commit()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ── export_csv: AI mode ──────────────────────────────────────────────────

class TestExportCsvAi:

    def test_export_csv_ai_columns_and_data(self, db_session):
        """AI mode CSV has the expected column headers and row data."""
        _seed_basic(db_session)

        path = export_csv(task_id=None, keyword=None, fmt="ai")
        assert path.suffix == ".csv"
        assert path.exists()

        rows = _read_csv(path)
        assert len(rows) == 1
        row = rows[0]

        expected_cols = [
            "note_id", "url", "title", "author", "content", "tags",
            "post_type", "likes", "collects", "comments_count", "shares",
            "published_at", "keyword", "image_count", "top_comments", "scraped_at",
        ]
        assert list(row.keys()) == expected_cols
        assert row["note_id"] == "note_001"
        assert row["url"] == "https://xhs.link/note_001"
        assert row["title"] == "Test Post"
        assert row["author"] == "AuthorA"
        assert row["content"] == "Hello world"
        assert row["tags"] == "tag1｜tag2"  # fullwidth pipe: | is replaced to avoid CSV collision
        assert row["post_type"] == "video"
        assert row["likes"] == "100"
        assert row["collects"] == "20"
        assert row["comments_count"] == "2"
        assert row["shares"] == "5"
        assert row["keyword"] == "test_kw"
        assert row["image_count"] == "3"

    def test_export_csv_ai_top_comments_pipe_format(self, db_session):
        """top_comments = 'Author:Content(N likes)' pipe-separated, sorted by likes desc."""
        _seed_basic(db_session)

        path = export_csv(fmt="ai")
        rows = _read_csv(path)
        tc = rows[0]["top_comments"]

        # Should be sorted by likes desc: UserX (50) first, then UserY (30)
        parts = tc.split("|")
        assert len(parts) == 2
        assert parts[0] == "UserX:Great!(50 likes)"
        assert parts[1] == "UserY:Nice(30 likes)"

    def test_export_csv_ai_no_comments(self, db_session):
        """AI mode with post but no comments has empty top_comments."""
        from semilabs_hone.core.models.post import CollectionItem
        from semilabs_hone.core.models.keyword import Keyword
        from semilabs_hone.core.models.task import CollectionTask

        task = CollectionTask(id=2, account_id=1, platform="xiaohongshu", status="completed")
        kw = Keyword(id=2, text="no_comments", platform="xiaohongshu")
        post = CollectionItem(
            id=2, platform="xiaohongshu", platform_id="note_002",
            task_id=2, keyword_id=2, title="No comments post",
            author_name="AuthorB",
        )
        for obj in [task, kw, post]:
            db_session.add(obj)
        db_session.commit()

        path = export_csv(fmt="ai")
        rows = _read_csv(path)
        assert len(rows) == 1
        assert rows[0]["top_comments"] == ""


# ── export_csv: Excel mode ───────────────────────────────────────────────

class TestExportCsvExcel:

    def test_export_csv_excel_zip_has_two_csvs(self, db_session):
        """Excel mode returns a ZIP containing posts.csv + comments.csv."""
        _seed_basic(db_session)

        path = export_csv(fmt="excel")
        assert path.suffix == ".zip"
        assert path.exists()

        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        assert "posts.csv" in names
        assert "comments.csv" in names

    def test_export_csv_excel_posts_csv_content(self, db_session):
        """posts.csv in the ZIP has correct rows."""
        _seed_basic(db_session)

        path = export_csv(fmt="excel")
        with zipfile.ZipFile(path) as zf:
            data = zf.read("posts.csv").decode("utf-8-sig")
        reader = csv.DictReader(data.splitlines())
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["note_id"] == "note_001"

    def test_export_csv_excel_comments_csv_content(self, db_session):
        """comments.csv in the ZIP is linked by note_id."""
        _seed_basic(db_session)

        path = export_csv(fmt="excel")
        with zipfile.ZipFile(path) as zf:
            data = zf.read("comments.csv").decode("utf-8-sig")
        reader = csv.DictReader(data.splitlines())
        rows = list(reader)
        assert len(rows) == 2
        assert all(r["note_id"] == "note_001" for r in rows)


# ── export_csv: empty DB ─────────────────────────────────────────────────

class TestExportCsvEmptyDb:

    def test_export_empty_db_ai_no_crash(self, db_session):
        """AI mode export on empty DB returns a valid CSV with header only."""
        path = export_empty_db(fmt="ai")
        assert path.exists()
        rows = _read_csv(path)
        assert len(rows) == 0  # no data rows

    def test_export_empty_db_excel_no_crash(self, db_session):
        """Excel mode export on empty DB returns a valid ZIP with empty CSVs."""
        path = export_empty_db(fmt="excel")
        assert path.exists()
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            assert "posts.csv" in names
            assert "comments.csv" in names

            with zf.open("posts.csv") as pf:
                posts_data = pf.read().decode("utf-8-sig")
        reader = csv.DictReader(posts_data.splitlines())
        assert len(list(reader)) == 0


# ── export_csv: filtering ────────────────────────────────────────────────

class TestExportCsvFiltering:

    def test_export_csv_filter_by_task_id(self, db_session):
        """Only posts for the given task_id are exported."""
        from semilabs_hone.core.models.post import CollectionItem
        from semilabs_hone.core.models.keyword import Keyword
        from semilabs_hone.core.models.task import CollectionTask

        t1 = CollectionTask(id=10, account_id=1, platform="xiaohongshu", status="completed")
        t2 = CollectionTask(id=11, account_id=1, platform="xiaohongshu", status="completed")
        kw1 = Keyword(id=10, text="kw_a", platform="xiaohongshu")
        kw2 = Keyword(id=11, text="kw_b", platform="xiaohongshu")
        p1 = CollectionItem(id=10, platform="xiaohongshu", platform_id="n10",
                  task_id=10, keyword_id=10, title="Post for task 10",
                  author_name="A10")
        p2 = CollectionItem(id=11, platform="xiaohongshu", platform_id="n11",
                  task_id=11, keyword_id=11, title="Post for task 11",
                  author_name="A11")
        for obj in [t1, t2, kw1, kw2, p1, p2]:
            db_session.add(obj)
        db_session.commit()

        path = export_csv(task_id="10", fmt="ai")
        rows = _read_csv(path)
        assert len(rows) == 1
        assert rows[0]["note_id"] == "n10"

    def test_export_csv_filter_by_keyword(self, db_session):
        """Only posts for the given keyword text are exported."""
        from semilabs_hone.core.models.post import CollectionItem
        from semilabs_hone.core.models.keyword import Keyword
        from semilabs_hone.core.models.task import CollectionTask

        task = CollectionTask(id=20, account_id=1, platform="xiaohongshu", status="completed")
        kw1 = Keyword(id=20, text="alpha", platform="xiaohongshu")
        kw2 = Keyword(id=21, text="beta", platform="xiaohongshu")
        p1 = CollectionItem(id=20, platform="xiaohongshu", platform_id="n20",
                  task_id=20, keyword_id=20, title="Alpha post", author_name="Alpha")
        p2 = CollectionItem(id=21, platform="xiaohongshu", platform_id="n21",
                  task_id=20, keyword_id=21, title="Beta post", author_name="Beta")
        for obj in [task, kw1, kw2, p1, p2]:
            db_session.add(obj)
        db_session.commit()

        path = export_csv(keyword="alpha", fmt="ai")
        rows = _read_csv(path)
        assert len(rows) == 1
        assert rows[0]["title"] == "Alpha post"
        assert rows[0]["keyword"] == "alpha"


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
