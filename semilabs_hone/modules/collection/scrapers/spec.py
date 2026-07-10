"""PlatformSpec pydantic models (platform.yaml schema, skim_design.md §8.2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Locator(BaseModel):
    """Selector for a page element (multi-strategy, runtime fallback)."""
    text: str | None = None
    css: str | None = None
    xpath: str | None = None
    aria_label: str | None = None
    role: str | None = None
    nth: int | None = None


class Step(BaseModel):
    """One step in a flow's step chain."""
    type: Literal[
        "navigate", "input", "click", "scroll", "scroll_collect", "go_back",
        "wait_xhr", "extract", "wait_selector"
    ]

    # navigate
    url: str | None = None

    # input
    locator: Locator | None = None
    text: str | None = None

    # click
    # (uses locator from above)

    # scroll / scroll_collect
    max_times: int = 3
    wait_ms: int = 800
    # scroll_collect: bounded incremental list collection (PRD §8.4 场景4.2).
    # Re-extracts `from_`/`group`/`map` after each wheel scroll, dedups new
    # item_ids, stops at `max_scrolls` or `empty_break` consecutive no-new.
    max_scrolls: int = 20
    empty_break: int = 5

    # wait_xhr
    url_pattern: str | None = None
    method: str | None = None
    save_as: str | None = None
    timeout_ms: int = 15000

    # extract
    from_: str | None = Field(default=None, alias="from")
    group: str | None = None
    map: dict[str, str] = Field(default_factory=dict)

    # wait_selector
    selector: str | None = None

    # extra fields (platform.yaml may have arbitrary keys)
    model_config = {"populate_by_name": True, "extra": "allow"}


class Flow(BaseModel):
    """A complete action chain (search / detail / comments)."""
    steps: list[Step] = Field(default_factory=list)


class LoginSpec(BaseModel):
    """Login configuration for a platform."""
    type: Literal["qrcode", "password", "sms", "oauth"] = "qrcode"
    login_url: str | None = None
    success_detect: str | None = None
    success_pattern: str | None = None
    timeout: int = 120


class PlatformSpec(BaseModel):
    """Full platform specification (the pydantic model for platform.yaml)."""
    platform: str
    display_name: str
    base_url: str
    login: LoginSpec = Field(default_factory=LoginSpec)
    flows: dict[str, Flow] = Field(default_factory=dict)
    sort_values: dict[str, str] = Field(default_factory=dict)
