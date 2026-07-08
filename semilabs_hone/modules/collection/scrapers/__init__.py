"""Scrapers subpackage — re-exports for convenience."""

from semilabs_hone.modules.collection.scrapers.base import (
    GROUP_COMMENTS,
    GROUP_ITEM_REF,
    GROUP_POST_BODY,
    GROUP_POST_INTERACTIONS,
    BasePlatformScraper,
)
from semilabs_hone.modules.collection.scrapers.engine import GenericEngine
from semilabs_hone.modules.collection.scrapers.field_extract import (
    extract_api,
    extract_dom,
    render_template,
)
from semilabs_hone.modules.collection.scrapers.registry import get, list_platforms, load_registry
from semilabs_hone.modules.collection.scrapers.spec import Flow, LoginSpec, PlatformSpec, Step

__all__ = [
    "BasePlatformScraper",
    "GROUP_ITEM_REF",
    "GROUP_POST_BODY",
    "GROUP_POST_INTERACTIONS",
    "GROUP_COMMENTS",
    "Step",
    "Flow",
    "LoginSpec",
    "PlatformSpec",
    "extract_api",
    "extract_dom",
    "render_template",
    "GenericEngine",
    "load_registry",
    "list_platforms",
    "get",
]
