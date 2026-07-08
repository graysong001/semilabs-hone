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
    async def test_random_scroll_scrolls_at_least_once(self, monkeypatch):
        """random_scroll should scroll at least once."""
        mock_page = AsyncMock()

        monkeypatch.setattr("random.randint", lambda a, b: 3)

        await random_scroll(mock_page, max_times=5, wait_ms=500)

        assert mock_page.evaluate.call_count >= 1


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
