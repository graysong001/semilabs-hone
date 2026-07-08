"""Chrome profile directory management.

Design: docs/skim_design.md §4.3 — one account = one fixed profile dir.
"""
from __future__ import annotations

from pathlib import Path

import config


def profile_dir_for(account_id: int) -> Path:
    """Return the profile directory path for an account (lazy, no side effects).

    Path: data/collection/profiles/<account_id>/
    """
    return config.DATA_DIR / "collection" / "profiles" / str(account_id)


def ensure_profile(account_id: int) -> Path:
    """Create the profile directory if it does not exist, return the path."""
    path = profile_dir_for(account_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
