"""S9a L13 — worker spawner: best-effort Popen, heartbeat-gated, never raises.

The spawner is only attached to app.state when config.WORKER_AUTOSPAWN is on
(tested in test_routes_s9a); this file exercises the spawner callable itself.
"""
from __future__ import annotations

import pytest

from semilabs_hone.core.ipc import worker_spawner as sp


class _FakeProc:
    def __init__(self, pid=999):
        self.pid = pid
        self._alive = True

    def poll(self):
        return None if self._alive else 0


class TestMakeDefaultSpawner:
    def test_stale_heartbeat_spawns_once(self, monkeypatch):
        spawned: list = []

        def _fake_popen(cmd, **kw):
            spawned.append(cmd)
            return _FakeProc()

        monkeypatch.setattr(sp.subprocess, "Popen", _fake_popen)

        # heartbeat_age returns None (no worker alive → stale).
        from semilabs_hone.core.ipc import paths as paths_mod
        monkeypatch.setattr(paths_mod, "heartbeat_age", lambda now=None: None)

        spawn = sp.make_default_spawner()
        spawn(5)
        spawn(5)  # second call: prior proc still alive (poll None) → skip
        assert len(spawned) == 1
        assert "--account" in spawned[0]
        assert "5" in spawned[0]

    def test_fresh_heartbeat_skips_spawn(self, monkeypatch):
        spawned: list = []
        monkeypatch.setattr(sp.subprocess, "Popen", lambda *a, **k: spawned.append(a) or _FakeProc())
        from semilabs_hone.core.ipc import paths as paths_mod
        monkeypatch.setattr(paths_mod, "heartbeat_age", lambda now=None: 5.0)  # fresh (<30s)

        spawn = sp.make_default_spawner()
        spawn(6)
        assert spawned == []

    def test_popen_failure_never_raises(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("fork failed")
        monkeypatch.setattr(sp.subprocess, "Popen", _boom)
        from semilabs_hone.core.ipc import paths as paths_mod
        monkeypatch.setattr(paths_mod, "heartbeat_age", lambda now=None: None)

        spawn = sp.make_default_spawner()
        spawn(7)  # must not raise — best-effort (watchdog reaps zombie later)
        spawn(7)  # and again
