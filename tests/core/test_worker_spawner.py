"""S9a L13 — worker spawner: best-effort Popen, heartbeat-gated, never raises.

The spawner is only attached to app.state when config.WORKER_AUTOSPAWN is on
(tested in test_routes_s9a); this file exercises the spawner callable itself
plus the merged supervisor capabilities (FIX_PLAN F2: per-(module,account)
registry, manifest WORKER_ENTRY launch, dead-entry relaunch, stop/shutdown).
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from semilabs_hone.core.ipc import worker_spawner as sp


@pytest.fixture(autouse=True)
def _clean_registry():
    sp._WORKERS.clear()
    yield
    sp._WORKERS.clear()


@pytest.fixture(autouse=True)
def _real_ensure_worker(monkeypatch, no_worker_launch):
    """This module tests the spawner itself — undo the global autouse mock."""
    original = getattr(no_worker_launch, "original", None)
    if original is not None:
        monkeypatch.setattr(sp, "ensure_worker", original)


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


def _fake_proc(alive: bool = True) -> MagicMock:
    proc = MagicMock()
    proc.poll.return_value = None if alive else 1
    proc.pid = 4242
    return proc


class TestEnsureWorker:
    """Merged supervisor capabilities (FIX_PLAN F2, merge plan §4.4)."""

    def test_ensure_worker_launches_with_manifest_entry(self, tmp_data_dir, monkeypatch):
        """First call launches `python -u -m <WORKER_ENTRY> --account N`.

        ``-u`` keeps the worker's log from being lost to stdio buffering when
        it is terminated mid-run (USER_SOP G33).
        """
        launched = {}

        def fake_popen(cmd, **kwargs):
            launched["cmd"] = cmd
            launched["kwargs"] = kwargs
            return _fake_proc(alive=True)

        monkeypatch.setattr(sp.subprocess, "Popen", fake_popen)
        proc = sp.ensure_worker("collection", 7)

        assert proc is not None
        cmd = launched["cmd"]
        assert cmd[0] == sys.executable
        assert cmd[1:4] == ["-u", "-m", "semilabs_hone.modules.collection.browser.worker_main"]
        assert cmd[4:] == ["--account", "7"]
        assert launched["kwargs"]["env"]["PYTHONUNBUFFERED"] == "1"

    def test_ensure_worker_reuses_live_process(self, tmp_data_dir, monkeypatch):
        """Second call with a live worker does not relaunch."""
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append(cmd)
            return _fake_proc(alive=True)

        monkeypatch.setattr(sp.subprocess, "Popen", fake_popen)
        p1 = sp.ensure_worker("collection", 1)
        p2 = sp.ensure_worker("collection", 1)

        assert p1 is p2
        assert len(calls) == 1

    def test_ensure_worker_relaunches_dead_process(self, tmp_data_dir, monkeypatch):
        """A dead registry entry is reaped and relaunched."""
        procs = [_fake_proc(alive=False), _fake_proc(alive=True)]
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append(cmd)
            return procs[len(calls) - 1]

        monkeypatch.setattr(sp.subprocess, "Popen", fake_popen)
        dead = sp.ensure_worker("collection", 1)
        live = sp.ensure_worker("collection", 1)

        assert live is not dead
        assert len(calls) == 2

    def test_is_alive_reflects_registry(self, tmp_data_dir, monkeypatch):
        """is_alive is True only for a registered, running worker."""
        monkeypatch.setattr(
            sp.subprocess, "Popen", lambda cmd, **kw: _fake_proc(alive=True)
        )
        assert sp.is_alive("collection", 3) is False  # never launched
        sp.ensure_worker("collection", 3)
        assert sp.is_alive("collection", 3) is True

    def test_stop_worker_terminates_and_deregisters(self, tmp_data_dir, monkeypatch):
        """stop_worker terminates the process and removes the registry entry."""
        proc = _fake_proc(alive=True)
        monkeypatch.setattr(sp.subprocess, "Popen", lambda cmd, **kw: proc)
        sp.ensure_worker("collection", 5)

        sp.stop_worker("collection", 5)

        proc.terminate.assert_called_once()
        assert ("collection", 5) not in sp._WORKERS

    def test_shutdown_all_stops_every_worker(self, tmp_data_dir, monkeypatch):
        """shutdown_all terminates all registered workers (USER_SOP G12)."""
        procs = {}

        def fake_popen(cmd, **kwargs):
            p = _fake_proc(alive=True)
            procs[tuple(cmd)] = p
            return p

        monkeypatch.setattr(sp.subprocess, "Popen", fake_popen)
        sp.ensure_worker("collection", 1)
        sp.ensure_worker("collection", 2)

        sp.shutdown_all()

        assert sp._WORKERS == {}
        for p in procs.values():
            p.terminate.assert_called_once()

    def test_worker_entry_missing_raises(self, tmp_data_dir):
        """A module without WORKER_ENTRY in manifest raises."""
        with pytest.raises((ValueError, ModuleNotFoundError)):
            sp.ensure_worker("nonexistent_module", 1)


def test_worker_entry_is_runnable_as_a_module():
    """`python -m <WORKER_ENTRY>` must actually run main().

    USER_SOP G31: the module had no __main__ guard, so every spawned worker
    imported itself and exited 0 without ever serving a request. Asking for
    --help proves the entry point executes (and needs no browser).
    """
    import subprocess

    import config
    from semilabs_hone.modules.collection import manifest

    result = subprocess.run(
        [sys.executable, "-m", manifest.WORKER_ENTRY, "--help"],
        cwd=str(config.REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--account" in result.stdout
