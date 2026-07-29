"""launchagent.write_plist — macOS LaunchAgent plist generation (design §4.2).

MVP contract: generate the plist file on demand, never install/enable it.
"""
from __future__ import annotations

import plistlib


class TestWritePlist:
    def test_writes_valid_plist_with_worker_contract(self, tmp_data_dir):
        from semilabs_hone.modules.collection.browser.launchagent import write_plist

        path = write_plist(7)

        assert path.exists()
        assert path.name == "com.semilabs.collection-worker.7.plist"
        with open(path, "rb") as f:
            data = plistlib.load(f)

        assert data["Label"] == "com.semilabs.collection-worker"
        assert data["LimitLoadToSessionType"] == "Aqua"
        assert data["RunAtLoad"] is True and data["KeepAlive"] is True
        # Program launches the module CLI worker for the collection module.
        assert data["ProgramArguments"][1:] == [
            "-m", "semilabs_hone", "worker", "--module", "collection",
        ]
        # Worker stdout/stderr both land in the per-account log file.
        assert data["StandardOutPath"].endswith("collection-worker-7.log")
        assert data["StandardOutPath"] == data["StandardErrorPath"]

    def test_generate_only_never_installs(self, tmp_data_dir):
        """MVP redline: the plist stays inside data/, not ~/Library/LaunchAgents."""
        from semilabs_hone.modules.collection.browser.launchagent import write_plist

        path = write_plist(1)
        assert "LaunchAgents" not in str(path)
        assert str(tmp_data_dir) in str(path)
