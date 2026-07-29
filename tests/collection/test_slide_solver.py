"""Slide solver coverage — dependency-missing + element-missing branches.

cv2/numpy/playwright are not installed in the test env, so the ImportError
branches are exercised directly; the dependency-available branches inject
fake modules via sys.modules.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from semilabs_hone.modules.collection.captcha import slide_solver


def _fake_module(name, **attrs):
    mod = type(sys)(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class TestSolveSlideMissingDeps:
    async def test_no_opencv_returns_false(self):
        # cv2 is not installed in this env.
        assert await slide_solver.solve_slide(MagicMock()) is False

    async def test_no_numpy_returns_false(self, monkeypatch):
        # cv2 available, but numpy import fails.
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "cv2":
                return _fake_module("cv2")
            if name == "numpy":
                raise ImportError("no numpy")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert await slide_solver.solve_slide(MagicMock()) is False

    async def test_no_playwright_returns_false(self, monkeypatch):
        # cv2 + numpy available, playwright import fails.
        import builtins
        real_import = builtins.__import__

        np = _fake_module("numpy", uint8=MagicMock(), frombuffer=MagicMock(return_value=MagicMock()))

        def fake_import(name, *a, **k):
            if name == "cv2":
                return _fake_module("cv2")
            if name == "numpy":
                return np
            if "playwright" in name:
                raise ImportError("no playwright")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert await slide_solver.solve_slide(MagicMock()) is False


class TestSolveSlideElementsMissing:
    async def test_elements_not_found_returns_false(self, monkeypatch):
        np = _fake_module("numpy", uint8=MagicMock(),
                          frombuffer=MagicMock(return_value=MagicMock()))
        cv2 = _fake_module(
            "cv2",
            imdecode=MagicMock(return_value=MagicMock(shape=(10, 10))),
            IMREAD_GRAYSCALE=0,
            absdiff=MagicMock(),
            threshold=MagicMock(return_value=(None, MagicMock())),
            THRESH_BINARY=0,
            findContours=MagicMock(return_value=([], None)),
            RETR_EXTERNAL=0, CHAIN_APPROX_SIMPLE=0,
            matchTemplate=MagicMock(),
            TM_CCOEFF_NORMED=0,
            minMaxLoc=MagicMock(return_value=(0, 0, 0, (0, 0))),
            boundingRect=MagicMock(return_value=(0, 0, 0, 0)),
            resize=MagicMock(),
        )

        class _Page:
            async def query_selector(self, sel):
                return None  # no slider/bg/piece elements

        monkeypatch.setitem(sys.modules, "cv2", cv2)
        monkeypatch.setitem(sys.modules, "numpy", np)

        # playwright available: inject a fake module with Page.
        pw = _fake_module("playwright")
        pw_async = _fake_module("playwright.async_api", Page=object)
        monkeypatch.setitem(sys.modules, "playwright", pw)
        monkeypatch.setitem(sys.modules, "playwright.async_api", pw_async)

        assert await slide_solver.solve_slide(_Page()) is False


class TestExecuteSlide:
    async def test_bounding_box_none_falls_back_to_click(self):
        page = MagicMock()
        page.mouse.move = AsyncMock()
        page.mouse.down = AsyncMock()
        page.mouse.up = AsyncMock()
        btn = MagicMock()
        btn.bounding_box = AsyncMock(return_value=None)
        btn.click = AsyncMock()

        await slide_solver._execute_slide(page, btn, [{"x": 10, "y": 0, "t": 100}])
        btn.click.assert_called_once()

    async def test_bounding_box_present_drags_track(self):
        page = MagicMock()
        page.mouse.move = AsyncMock()
        page.mouse.down = AsyncMock()
        page.mouse.up = AsyncMock()
        btn = MagicMock()
        btn.bounding_box = AsyncMock(return_value={"x": 0, "y": 0, "width": 40, "height": 40})

        track = [{"x": 0, "y": 0, "t": 0}, {"x": 50, "y": 0, "t": 100}]
        await slide_solver._execute_slide(page, btn, track)
        page.mouse.down.assert_called_once()
        page.mouse.up.assert_called_once()
        assert page.mouse.move.call_count >= 2
