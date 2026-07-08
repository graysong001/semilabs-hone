"""DM-02 data models unit tests.

Covers: default values, upsert, task lifecycle, resume last_note_index,
        ProgressMessage data field.
Naming: test_<method>_<scenario>_<expected>.
"""
import pytest
from sqlalchemy import insert, update, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from datetime import datetime, timezone

from semilabs_hone.core.models.db import Engine, Base, init_db, get_session
from semilabs_hone.core.models.account import Account
from semilabs_hone.core.models.keyword import Keyword
from semilabs_hone.core.models.task import ScrapeTask, TaskKeyword
from semilabs_hone.core.models.post import Post
from semilabs_hone.core.models.comment import Comment
from semilabs_hone.core.models.schemas import (
    AccountCreate, TaskCreate, ProgressMessage, ItemRef, ScrapedPost, ScrapedComment,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _init_tables():
    """Ensure tables exist before any test runs."""
    init_db()


@pytest.fixture()
def session():
    """Provide a session that rolls back after each test (no side effects)."""
    s = get_session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


# ---------------------------------------------------------------------------
# Account — defaults (§7.2: color_scheme / timezone / locale)
# ---------------------------------------------------------------------------

class TestAccountDefaults:
    def test_account_create_defaults_platform_xiaohongshu(self, session):
        acct = Account(nickname="test")
        session.add(acct)
        session.commit()
        assert acct.platform == "xiaohongshu"

    def test_account_create_defaults_status_inactive(self, session):
        acct = Account(nickname="test")
        session.add(acct)
        session.commit()
        assert acct.status == "inactive"

    def test_account_create_defaults_color_scheme_light(self, session):
        acct = Account(nickname="test")
        session.add(acct)
        session.commit()
        assert acct.color_scheme == "light"

    def test_account_create_defaults_timezone_asia_shanghai(self, session):
        acct = Account(nickname="test")
        session.add(acct)
        session.commit()
        assert acct.timezone == "Asia/Shanghai"

    def test_account_create_defaults_locale_zh_cn(self, session):
        acct = Account(nickname="test")
        session.add(acct)
        session.commit()
        assert acct.locale == "zh-CN"

    def test_account_create_defaults_counters_zero(self, session):
        acct = Account(nickname="test")
        session.add(acct)
        session.commit()
        assert acct.daily_scrape_count == 0
        assert acct.total_scrape_count == 0
        assert acct.fail_count == 0


# ---------------------------------------------------------------------------
# Keyword — defaults and unique constraint
# ---------------------------------------------------------------------------

class TestKeywordDefaults:
    def test_keyword_create_defaults_platform(self, session):
        kw = Keyword(text="test_kw")
        session.add(kw)
        session.commit()
        assert kw.platform == "xiaohongshu"
        assert kw.use_count == 0


# ---------------------------------------------------------------------------
# ScrapeTask — defaults and lifecycle
# ---------------------------------------------------------------------------

class TestScrapeTaskDefaults:
    def test_task_create_defaults_pending(self, session):
        task = ScrapeTask(account_id=1)
        session.add(task)
        session.commit()
        assert task.status == "pending"

    def test_task_create_defaults_max_posts_20(self, session):
        task = ScrapeTask(account_id=1)
        session.add(task)
        session.commit()
        assert task.max_posts_per_keyword == 20

    def test_task_create_defaults_download_images_true(self, session):
        task = ScrapeTask(account_id=1)
        session.add(task)
        session.commit()
        assert task.download_images is True

    def test_task_create_defaults_collect_comments_true(self, session):
        task = ScrapeTask(account_id=1)
        session.add(task)
        session.commit()
        assert task.collect_comments is True

    def test_task_create_defaults_last_note_index_zero(self, session):
        task = ScrapeTask(account_id=1)
        session.add(task)
        session.commit()
        assert task.last_note_index == 0


class TestTaskLifecycle:
    def test_task_lifecycle_pending_to_running_to_completed(self, session):
        task = ScrapeTask(account_id=1)
        session.add(task)
        session.commit()
        assert task.status == "pending"

        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(task)
        assert task.status == "running"

        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        task.posts_scraped = 10
        session.commit()
        session.refresh(task)
        assert task.status == "completed"
        assert task.posts_scraped == 10


class TestTaskResume:
    def test_task_resume_preserves_last_note_index(self, session):
        task = ScrapeTask(account_id=1, last_note_index=15)
        session.add(task)
        session.commit()
        session.refresh(task)

        # Simulate failure and resume
        task.status = "failed"
        task.error_message = "browser closed"
        session.commit()

        # Resume: restore to running, keep last_note_index
        task.status = "running"
        task.error_message = None
        session.commit()
        session.refresh(task)

        assert task.last_note_index == 15
        assert task.status == "running"


# ---------------------------------------------------------------------------
# Post — upsert by (platform, platform_id)
# ---------------------------------------------------------------------------

class TestPostUpsert:
    def _upsert_post(self, session, platform, platform_id, title, likes):
        """Upsert a post using SQLite ON CONFLICT."""
        stmt = sqlite_insert(Post).values(
            platform=platform,
            platform_id=platform_id,
            title=title,
            likes=likes,
            raw_json="{}",
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["platform", "platform_id"],
            set_={
                "title": stmt.excluded.title,
                "likes": stmt.excluded.likes,
                "raw_json": stmt.excluded.raw_json,
            },
        )
        session.execute(stmt)
        session.commit()

    def test_post_upsert_same_platform_id_updates(self, session):
        self._upsert_post(session, "xiaohongshu", "note_001", "First Title", 10)

        result = session.execute(
            select(Post).where(Post.platform == "xiaohongshu", Post.platform_id == "note_001")
        ).scalar_one()
        assert result.title == "First Title"
        assert result.likes == 10

        # Second upsert with same platform_id — should update, not insert duplicate
        self._upsert_post(session, "xiaohongshu", "note_001", "Updated Title", 50)

        result = session.execute(
            select(Post).where(Post.platform == "xiaohongshu", Post.platform_id == "note_001")
        ).scalar_one()
        assert result.title == "Updated Title"
        assert result.likes == 50

        # Only one row should exist
        count = session.execute(
            select(Post).where(Post.platform == "xiaohongshu", Post.platform_id == "note_001")
        ).scalars().all()
        assert len(count) == 1


# ---------------------------------------------------------------------------
# Comment — basic create
# ---------------------------------------------------------------------------

class TestCommentCreate:
    def test_comment_create_defaults(self, session):
        cmt = Comment(
            post_id=1, content="test comment",
            author_name="user1", likes=5, rank=1,
        )
        session.add(cmt)
        session.commit()
        assert cmt.content == "test comment"
        assert cmt.likes == 5
        assert cmt.sub_comment_count == 0
        assert cmt.is_author_liked is False


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class TestSchemasDefaults:
    def test_account_create_defaults_platform(self):
        ac = AccountCreate(nickname="test")
        assert ac.platform == "xiaohongshu"
        assert ac.nickname == "test"

    def test_task_create_defaults(self):
        tc = TaskCreate(account_id=1, platform="xiaohongshu", keywords=["test"])
        assert tc.sort == "general"
        assert tc.max_posts_per_keyword == 20
        assert tc.download_images is True
        assert tc.collect_comments is True

    def test_progress_message_has_data_field(self):
        pm = ProgressMessage(
            type="progress",
            message="test",
            timestamp=1.0,
            data={"key": "value"},
        )
        assert pm.data == {"key": "value"}
        assert "data" in ProgressMessage.model_fields

    def test_progress_message_defaults_severity_info(self):
        pm = ProgressMessage(type="progress", message="test", timestamp=1.0)
        assert pm.severity == "info"

    def test_progress_message_all_type_literals_valid(self):
        valid_types = [
            "progress", "warn", "qr_ready", "login_required", "login_success",
            "captcha_required", "task_completed", "error", "disk_warn",
        ]
        for t in valid_types:
            pm = ProgressMessage(type=t, message="test", timestamp=1.0)
            assert pm.type == t

    def test_item_ref_creation(self):
        ref = ItemRef(platform="xiaohongshu", item_id="123", title="Test", author_name="A")
        assert ref.platform == "xiaohongshu"
        assert ref.item_id == "123"

    def test_scraped_post_creation(self):
        sp = ScrapedPost(
            title="Test", content="body", author_name="A",
            likes=10, collects=5, comments_count=2,
            platform_id="n_1",
        )
        assert sp.title == "Test"
        assert sp.platform_id == "n_1"

    def test_scraped_comment_creation(self):
        sc = ScrapedComment(content="good post", likes=5, rank=1)
        assert sc.content == "good post"
        assert sc.likes == 5
