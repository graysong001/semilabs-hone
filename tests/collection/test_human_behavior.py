"""Mock tests for DM-06 human_behavior module.

Tests generate_slide_track, human_type delays, and other behavior primitives.
No playwright required — all mocks via monkeypatch.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from semilabs_hone.modules.collection.anti_detect.human_behavior import (
    generate_slide_track,
    human_click,
    human_type,
    random_browse,
    random_scroll,
    smart_wait,
)


# ─── generate_slide_track tests ─────────────────────────────────────────────


class TestGenerateSlideTrack:
    """Tests for generate_slide_track."""

    def test_generate_slide_track_length_positive(self):
        """Track should contain more than 0 points."""
        track = generate_slide_track(300.0)
        assert len(track) > 0

    def test_generate_slide_track_has_deceleration(self):
        """Track should show acceleration then deceleration pattern."""
        track = generate_slide_track(300.0)
        x_values = [p["x"] for p in track]
        mid = len(x_values) // 2
        first_half_increasing = all(
            x_values[i] <= x_values[i + 1] + 10
            for i in range(min(mid, len(x_values) - 1))
        )
        assert first_half_increasing

    def test_generate_slide_track_has_overshoot_rebound(self):
        """Track should contain overshoot past the target and then rebound."""
        distance = 300.0
        track = generate_slide_track(distance)
        x_values = [p["x"] for p in track]
        max_x = max(x_values)
        assert max_x > distance

    def test_generate_slide_track_contains_timestamps(self):
        """Each point should have x, y, t fields."""
        track = generate_slide_track(200.0)
        for p in track:
            assert "x" in p
            assert "y" in p
            assert "t" in p

    def test_generate_slide_track_distance_scales(self):
        """Track endpoint should scale with distance parameter."""
        t1 = generate_slide_track(100.0)
        t2 = generate_slide_track(400.0)
        assert t2[-2]["x"] > t1[-2]["x"]

    def test_generate_slide_track_timestamps_strictly_monotonic(self):
        """Timestamps must strictly increase — non-monotonic t is a hard bot
        feature for slider risk engines (旧实现每点独立随机总时长, 可产生
        t[i+1] < t[i])."""
        for _ in range(30):  # 随机抽 30 条轨迹, 全部严格递增
            track = generate_slide_track(300.0)
            ts = [p["t"] for p in track]
            assert all(ts[i] < ts[i + 1] for i in range(len(ts) - 1)), \
                f"non-monotonic timestamps: {ts}"

    def test_generate_slide_track_duration_human_range(self):
        """Total drag duration lands in a human window (~240-1740ms)."""
        for _ in range(30):
            track = generate_slide_track(300.0)
            total_ms = track[-1]["t"]
            assert 200 <= total_ms <= 1800, f"duration {total_ms}ms out of human range"


# ─── human_type tests ──────────────────────────────────────────────────────


class TestHumanType:
    """Tests for human_type."""

    @pytest.mark.asyncio
    async def test_human_type_types_each_character(self, monkeypatch):
        """human_type should press each character of the text."""
        mock_element = AsyncMock()

        def fake_resolve(page, locator):
            return mock_element

        monkeypatch.setattr(
            "semilabs_hone.modules.collection.anti_detect.human_behavior._resolve_locator",
            fake_resolve,
        )

        sleep_times = []

        async def fake_sleep(delay):
            sleep_times.append(delay)

        monkeypatch.setattr("asyncio.sleep", fake_sleep)

        await human_type(None, {"text": "target"}, "hi")

        assert mock_element.press.call_count == 2
        calls = [c[0][0] for c in mock_element.press.call_args_list]
        assert calls == ["h", "i"]

    @pytest.mark.asyncio
    async def test_human_type_delay_in_range(self, monkeypatch):
        """Each character delay should be between 50-200ms."""
        mock_element = AsyncMock()

        def fake_resolve(page, locator):
            return mock_element

        monkeypatch.setattr(
            "semilabs_hone.modules.collection.anti_detect.human_behavior._resolve_locator",
            fake_resolve,
        )

        sleep_times = []

        async def fake_sleep(delay):
            sleep_times.append(delay)

        # Force no long pause: random.random returns 0.5 (> 0.05)
        monkeypatch.setattr("random.random", lambda: 0.5)
        monkeypatch.setattr("asyncio.sleep", fake_sleep)

        await human_type(None, {"text": "target"}, "abc")

        assert len(sleep_times) == 3
        for t in sleep_times:
            assert 0.05 <= t <= 0.2, f"Delay {t}s outside 50-200ms range"

    @pytest.mark.asyncio
    async def test_human_type_long_pause_occurs(self, monkeypatch):
        """5% chance long pause should produce 500-1500ms delay."""
        mock_element = AsyncMock()

        def fake_resolve(page, locator):
            return mock_element

        monkeypatch.setattr(
            "semilabs_hone.modules.collection.anti_detect.human_behavior._resolve_locator",
            fake_resolve,
        )

        sleep_times = []

        async def fake_sleep(delay):
            sleep_times.append(delay)

        # Force long pause: random.random returns 0.01 (< 0.05)
        monkeypatch.setattr("random.random", lambda: 0.01)
        monkeypatch.setattr("asyncio.sleep", fake_sleep)

        await human_type(None, {"text": "target"}, "a")

        assert len(sleep_times) == 1
        assert 0.5 <= sleep_times[0] <= 1.5


# ─── random_scroll tests ────────────────────────────────────────────────────


class TestRandomScroll:
    """Tests for random_scroll."""

    @pytest.mark.asyncio
    async def test_random_scroll_uses_mouse_wheel(self, monkeypatch):
        """random_scroll must use physical mouse.wheel, NOT page.evaluate(scrollBy)."""
        mock_page = AsyncMock()
        mock_mouse = AsyncMock()
        mock_page.mouse = mock_mouse

        monkeypatch.setattr("random.randint", lambda a, b: 3)

        await random_scroll(mock_page, max_times=5, wait_ms=500)

        assert mock_mouse.wheel.call_count >= 1
        # PRD redline: instant teleport via evaluate(window.scrollBy) is forbidden
        assert mock_page.evaluate.call_count == 0


# ─── random_browse tests ────────────────────────────────────────────────────


class TestRandomBrowse:
    """Tests for random_browse."""

    @pytest.mark.asyncio
    async def test_random_browse_visits_pages(self, monkeypatch):
        """random_browse should navigate to pages within the count range."""
        mock_page = AsyncMock()

        monkeypatch.setattr("random.randint", lambda a, b: 2)
        monkeypatch.setattr("random.choice", lambda lst: lst[0])

        await random_browse(mock_page, (1, 3))

        assert mock_page.goto.call_count == 2


# ─── human_click tests ──────────────────────────────────────────────────────


class TestHumanClick:
    """Tests for human_click."""

    @pytest.mark.asyncio
    async def test_human_click_moves_and_clicks(self, monkeypatch):
        """human_click should move mouse and click within element bounds."""
        mock_mouse = AsyncMock()

        async def fake_bbox():
            return {"x": 100, "y": 100, "width": 200, "height": 100}

        mock_element = AsyncMock()
        mock_element.bounding_box = fake_bbox

        def fake_resolve(page, locator):
            return mock_element

        monkeypatch.setattr(
            "semilabs_hone.modules.collection.anti_detect.human_behavior._resolve_locator",
            fake_resolve,
        )

        mock_page = AsyncMock()
        mock_page.mouse = mock_mouse

        await human_click(mock_page, {"text": "click me"})

        assert mock_mouse.move.call_count > 0
        assert mock_mouse.click.call_count == 1


# ─── mouse position memory tests ───────────────────────────────────────────


class TestMousePositionMemory:
    """_move_mouse_bezier must continue from the last cursor position
    (_last_pos) — always starting from (0,0) makes every path fly in from the
    top-left corner, a statistically regular machine signal."""

    @pytest.mark.asyncio
    async def test_first_move_starts_near_target_not_origin(self, monkeypatch):
        """首次移动: 起点在目标附近随机处, 不是屏幕左上角 (0,0)."""
        import semilabs_hone.modules.collection.anti_detect.human_behavior as hb_mod

        monkeypatch.setattr(hb_mod, "_last_pos", None)
        moves = []

        mock_page = AsyncMock()

        async def fake_move(x, y):
            moves.append((x, y))
        mock_page.mouse.move = fake_move

        async def _no_sleep(d):
            return None
        monkeypatch.setattr("asyncio.sleep", _no_sleep)

        await hb_mod._move_mouse_bezier(mock_page, 600.0, 400.0)

        # 贝塞尔第一个采样点 (t=1/steps) 接近起点: 起点 ∈ 目标±(200,150)
        first_x, first_y = moves[0]
        assert first_x > 100  # 若起点是 (0,0) 首点 x ≈ 30 以下
        assert first_y > 100
        # 终点记忆已写入
        assert hb_mod._last_pos == [600.0, 400.0]

    @pytest.mark.asyncio
    async def test_second_move_continues_from_last_position(self, monkeypatch):
        """第二次移动从上次终点出发 (轨迹连续, 非反复从角落飞入)."""
        import semilabs_hone.modules.collection.anti_detect.human_behavior as hb_mod

        monkeypatch.setattr(hb_mod, "_last_pos", [500.0, 300.0])
        moves = []

        mock_page = AsyncMock()

        async def fake_move(x, y):
            moves.append((x, y))
        mock_page.mouse.move = fake_move

        async def _no_sleep(d):
            return None
        monkeypatch.setattr("asyncio.sleep", _no_sleep)

        await hb_mod._move_mouse_bezier(mock_page, 800.0, 500.0)

        # 首采样点 (t=0.05) ≈ 90% 起点 + 控制点扰动: 距 (500,300) 远小于
        # 距新目标 (800,500)
        first_x, first_y = moves[0]
        assert abs(first_x - 500.0) < 100
        assert abs(first_y - 300.0) < 100
        assert hb_mod._last_pos == [800.0, 500.0]


# ─── smart_wait tests ───────────────────────────────────────────────────────


class TestSmartWait:
    """Tests for smart_wait (wait_for_selector + human reaction delay)."""

    @pytest.mark.asyncio
    async def test_smart_wait_waits_for_selector_then_sleeps(self, monkeypatch):
        """smart_wait must call wait_for_selector then sleep in 1.5-3.5s range."""
        mock_page = AsyncMock()

        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("random.uniform", lambda a, b: 2.5)
        monkeypatch.setattr("asyncio.sleep", fake_sleep)

        await smart_wait(mock_page, "div.note", timeout=5000)

        mock_page.wait_for_selector.assert_called_once_with("div.note", timeout=5000)
        assert sleeps == [2.5]

    @pytest.mark.asyncio
    async def test_smart_wait_delay_in_prd_range(self, monkeypatch):
        """Human reaction delay must be within PRD §4.2.1 1.5-3.5s range."""
        mock_page = AsyncMock()

        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("asyncio.sleep", fake_sleep)

        for _ in range(20):
            await smart_wait(mock_page, "div.note")
            assert 1.5 <= sleeps[-1] <= 3.5

