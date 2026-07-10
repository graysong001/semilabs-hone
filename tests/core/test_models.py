"""DM-02 data models unit tests (PRD §6 aligned, S3).

Covers:
- CollectionTask / CollectionItem / CollectionComment: UUID PK, PRD columns,
  defaults, and UNIQUE-constraint upsert (ON CONFLICT DO UPDATE).
- repository.upsert_item / upsert_comment + metrics_json pack/unpack.
- TaskCreate validation (PRD §4.1/§6.1): http-prefix rule + expected_count
  clamp to [1, 200].
- Retained legacy-column defaults (handlers/routes still depend on them).
- ProgressMessage data field.

Naming: test_<method>_<scenario>_<expected>.

Uses conftest `db_session` fixture (temp DB via tmp_data_dir) for isolation.
"""
import pytest
from sqlalchemy import select
from datetime import datetime, timezone

from semilabs_hone.core.models.db import Base, init_db, get_session
from semilabs_hone.core.models.account import Account
from semilabs_hone.core.models.keyword import Keyword
from semilabs_hone.core.models.task import CollectionTask, TaskKeyword
from semilabs_hone.core.models.post import CollectionItem
from semilabs_hone.core.models.comment import CollectionComment
from semilabs_hone.core.models.repository import (
    upsert_item, upsert_comment, pack_metrics, unpack_metrics,
)
from semilabs_hone.core.models.schemas import (
    AccountCreate, TaskCreate, ProgressMessage, ItemRef, ScrapedPost, ScrapedComment,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def session(db_session):
    """Wrap conftest db_session with rollback-after-test for isolation."""
    try:
        yield db_session
    finally:
        db_session.rollback()
        db_session.close()


# ---------------------------------------------------------------------------
# Account — defaults (§7.2)
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


# ---------------------------------------------------------------------------
# Keyword — defaults and unique constraint
# ---------------------------------------------------------------------------

class TestKeywordDefaults:
    def test_keyword_create_defaults_platform(self, session):
        kw = Keyword(text="kw_defaults_01")
        session.add(kw)
        session.commit()
        assert kw.platform == "xiaohongshu"
        assert kw.use_count == 0


# ---------------------------------------------------------------------------
# CollectionTask — PRD §6.1 defaults + UUID PK
# ---------------------------------------------------------------------------

class TestCollectionTaskPrdDefaults:
    def test_task_create_defaults_pending(self, session):
        task = CollectionTask(platform="xiaohongshu", target_value="kw")
        session.add(task)
        session.commit()
        assert task.status == "pending"

    def test_task_create_defaults_uuid_pk_str36(self, session):
        task = CollectionTask(platform="xiaohongshu", target_value="kw")
        session.add(task)
        session.commit()
        # PRD §6.1: id is a UUID v4 string
        assert isinstance(task.id, str)
        assert len(task.id) == 36
        assert task.id.count("-") == 4  # canonical uuid hex form

    def test_task_create_defaults_prd_columns(self, session):
        task = CollectionTask(platform="xiaohongshu", target_value="kw")
        session.add(task)
        session.commit()
        assert task.task_type == "keyword_search"
        assert task.target_value == "kw"
        assert task.expected_count == 0
        assert task.actual_count == 0
        assert task.error_msg is None
        assert task.created_at is not None
        assert task.updated_at is not None

    def test_task_two_ids_are_distinct_uuids(self, session):
        t1 = CollectionTask(platform="xiaohongshu", target_value="a")
        t2 = CollectionTask(platform="xiaohongshu", target_value="b")
        session.add_all([t1, t2])
        session.commit()
        assert t1.id != t2.id


class TestCollectionTaskLegacyDefaults:
    """Retained legacy columns (handlers/routes depend on them until S4/S6)."""

    def test_task_create_defaults_max_posts_20(self, session):
        task = CollectionTask(platform="xiaohongshu", target_value="kw")
        session.add(task)
        session.commit()
        assert task.max_posts_per_keyword == 20

    def test_task_create_defaults_download_images_true(self, session):
        task = CollectionTask(platform="xiaohongshu", target_value="kw")
        session.add(task)
        session.commit()
        assert task.download_images is True

    def test_task_create_defaults_collect_comments_true(self, session):
        task = CollectionTask(platform="xiaohongshu", target_value="kw")
        session.add(task)
        session.commit()
        assert task.collect_comments is True

    def test_task_create_defaults_last_note_index_zero(self, session):
        task = CollectionTask(platform="xiaohongshu", target_value="kw")
        session.add(task)
        session.commit()
        assert task.last_note_index == 0


class TestTaskLifecycle:
    def test_task_lifecycle_pending_to_running_to_completed(self, session):
        task = CollectionTask(platform="xiaohongshu", target_value="kw")
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
        task.actual_count = 10
        session.commit()
        session.refresh(task)
        assert task.status == "completed"
        assert task.actual_count == 10


class TestTaskResume:
    def test_task_resume_preserves_last_note_index(self, session):
        task = CollectionTask(platform="xiaohongshu", target_value="kw", last_note_index=15)
        session.add(task)
        session.commit()
        session.refresh(task)

        # Simulate failure and resume
        task.status = "error"
        task.error_msg = "browser closed"
        session.commit()

        # Resume: restore to running, keep last_note_index
        task.status = "running"
        task.error_msg = None
        session.commit()
        session.refresh(task)

        assert task.last_note_index == 15
        assert task.status == "running"


# ---------------------------------------------------------------------------
# repository.upsert_item — PRD §6.2/§6.4 ON CONFLICT upsert
# ---------------------------------------------------------------------------

class TestUpsertItem:
    def test_upsert_item_insert_then_update_no_duplicate(self, session):
        # First insert
        row = upsert_item(
            session,
            task_id=None,
            platform="xiaohongshu",
            platform_id="note_001",
            url="https://xhs.link/note_001",
            title="First Title",
            content_text="body v1",
            author_name="AuthorA",
            metrics={"likes": 10, "comments_count": 2},
            publish_time="2025-06-01",
        )
        assert row.id is not None
        assert row.metrics_json == pack_metrics({"likes": 10, "comments_count": 2})

        # Second upsert — same (platform, platform_id) → UPDATE, not duplicate
        upsert_item(
            session,
            task_id=None,
            platform="xiaohongshu",
            platform_id="note_001",
            url="https://xhs.link/note_001",
            title="Updated Title",
            content_text="body v2",
            author_name="AuthorA2",
            metrics={"likes": 50, "comments_count": 5, "collects": 3},
            publish_time="2025-06-01",
        )

        rows = session.execute(
            select(CollectionItem).where(
                CollectionItem.platform == "xiaohongshu",
                CollectionItem.platform_id == "note_001",
            )
        ).scalars().all()
        assert len(rows) == 1  # dedup, no duplicate
        only = rows[0]
        assert only.title == "Updated Title"
        assert only.content_text == "body v2"
        assert only.author_name == "AuthorA2"
        # metrics_json round-trips
        assert unpack_metrics(only.metrics_json) == {"likes": 50, "comments_count": 5, "collects": 3}

    def test_upsert_item_different_platform_ids_two_rows(self, session):
        upsert_item(session, task_id=None, platform="xiaohongshu", platform_id="n_A",
                    title="A", metrics={"likes": 1})
        upsert_item(session, task_id=None, platform="xiaohongshu", platform_id="n_B",
                    title="B", metrics={"likes": 2})
        count = session.execute(select(CollectionItem)).scalars().all()
        assert len(count) == 2


# ---------------------------------------------------------------------------
# repository.upsert_comment — PRD §6.3 ON CONFLICT upsert
# ---------------------------------------------------------------------------

class TestUpsertComment:
    def test_upsert_comment_insert_then_update_no_duplicate(self, session):
        # item_id has no enforced FK parent (PRAGMA foreign_keys deferred); use a
        # stable fake id so the UNIQUE(item_id, platform_comment_id) dedup fires.
        upsert_comment(
            session,
            item_id="item-1",
            platform_comment_id="c1",
            author_name="UserX",
            content_text="Great!",
            like_count=5,
        )
        upsert_comment(
            session,
            item_id="item-1",
            platform_comment_id="c1",
            author_name="UserX2",
            content_text="Great! v2",
            like_count=50,
        )

        rows = session.execute(
            select(CollectionComment).where(
                CollectionComment.item_id == "item-1",
                CollectionComment.platform_comment_id == "c1",
            )
        ).scalars().all()
        assert len(rows) == 1
        only = rows[0]
        assert only.content_text == "Great! v2"
        assert only.like_count == 50
        assert only.author_name == "UserX2"


# ---------------------------------------------------------------------------
# metrics_json pack/unpack (PRD §6.4)
# ---------------------------------------------------------------------------

class TestMetricsJson:
    def test_pack_metrics_none_returns_empty_json(self):
        assert pack_metrics(None) == "{}"

    def test_pack_metrics_dict_serializes(self):
        s = pack_metrics({"likes": 10, "comments_count": 2})
        assert '"likes"' in s and '"comments_count"' in s

    def test_unpack_metrics_none_or_empty(self):
        assert unpack_metrics(None) == {}
        assert unpack_metrics("") == {}

    def test_unpack_metrics_bad_json_returns_empty(self):
        assert unpack_metrics("{not json") == {}

    def test_unpack_metrics_roundtrip(self):
        m = {"likes": 10, "collects": 3, "comments_count": 2, "shares": 1}
        assert unpack_metrics(pack_metrics(m)) == m


# ---------------------------------------------------------------------------
# Pydantic schemas — TaskCreate validation (PRD §4.1/§6.1)
# ---------------------------------------------------------------------------

class TestSchemasDefaults:
    def test_account_create_defaults_platform(self):
        ac = AccountCreate(nickname="test")
        assert ac.platform == "xiaohongshu"
        assert ac.nickname == "test"

    def test_task_create_defaults(self):
        tc = TaskCreate(target_value="keyword")
        assert tc.platform == "xiaohongshu"
        assert tc.task_type == "keyword_search"
        assert tc.target_value == "keyword"
        assert tc.expected_count == 20

    def test_task_create_clamps_count_below_one(self):
        # PRD §4.1/T15: expected_count 截断到 [1,200]
        tc = TaskCreate(target_value="kw", expected_count=0)
        assert tc.expected_count == 1

    def test_task_create_clamps_count_above_200(self):
        tc = TaskCreate(target_value="kw", expected_count=999)
        assert tc.expected_count == 200

    def test_task_create_keeps_count_in_range(self):
        tc = TaskCreate(target_value="kw", expected_count=50)
        assert tc.expected_count == 50

    def test_task_create_author_homepage_requires_http(self):
        with pytest.raises(Exception):
            TaskCreate(task_type="author_homepage", target_value="not-a-url", expected_count=10)

    def test_task_create_author_homepage_with_https_ok(self):
        tc = TaskCreate(task_type="author_homepage",
                        target_value="https://xhs.com/user/abc", expected_count=10)
        assert tc.task_type == "author_homepage"

    def test_task_create_empty_target_rejected(self):
        with pytest.raises(Exception):
            TaskCreate(target_value="")

    def test_progress_message_has_data_field(self):
        pm = ProgressMessage(
            type="progress", message="test", timestamp=1.0, data={"key": "value"},
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
            likes=10, collects=5, comments_count=2, platform_id="n_1",
        )
        assert sp.title == "Test"
        assert sp.platform_id == "n_1"

    def test_scraped_comment_creation(self):
        sc = ScrapedComment(content="good post", likes=5, rank=1)
        assert sc.content == "good post"
        assert sc.likes == 5
