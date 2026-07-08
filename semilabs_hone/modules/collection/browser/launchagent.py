"""macOS LaunchAgent plist generator for collection browser worker.

Design: docs/skim_design.md §4.2.
MVP: on-demand Popen only — this module provides plist generation without enabling.
"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import config


def write_plist(account_id: int) -> Path:
    """Generate a LaunchAgent plist for the collection worker.

    Returns the path to the written plist file.
    MVP: does NOT install/enable it — caller can inspect the file.

    Label: com.semilabs.collection-worker
    LimitLoadToSessionType: Aqua (GUI session)
    """
    plist_dir = config.DATA_DIR / "collection" / "launchagent"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"com.semilabs.collection-worker.{account_id}.plist"

    python_bin = sys.executable
    log_dir = config.DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_data = {
        "Label": "com.semilabs.collection-worker",
        "LimitLoadToSessionType": "Aqua",
        "ProgramArguments": [
            python_bin,
            "-m",
            "semilabs_hone",
            "worker",
            "--module",
            "collection",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_dir / f"collection-worker-{account_id}.log"),
        "StandardErrorPath": str(log_dir / f"collection-worker-{account_id}.log"),
    }

    with open(plist_path, "wb") as f:
        plistlib.dump(plist_data, f)

    return plist_path
