"""Registry: load platform.yaml files, discover platforms, resolve adapters."""

from __future__ import annotations

import glob
import importlib
import logging
from pathlib import Path
from typing import Any

import yaml

from semilabs_hone.modules.collection.scrapers.base import BasePlatformScraper
from semilabs_hone.modules.collection.scrapers.spec import PlatformSpec

logger = logging.getLogger(__name__)

# Cache for loaded registry
_registry_cache: dict[str, tuple[PlatformSpec, type | None]] | None = None


def _scrapers_dir() -> Path:
    """Return the scrapers/ directory path."""
    return Path(__file__).parent


def load_registry(
    force: bool = False,
) -> dict[str, tuple[PlatformSpec, type[BasePlatformScraper] | None]]:
    """Load all platform.yaml files from platforms/*/platform.yaml.

    Returns:
        {platform_name: (PlatformSpec, adapter_class_or_None)}

    Scans platforms/*/platform.yaml, validates each against PlatformSpec,
    and optionally loads adapter.py from each platform directory.
    """
    global _registry_cache

    if _registry_cache is not None and not force:
        return _registry_cache

    registry: dict[str, tuple[PlatformSpec, type[BasePlatformScraper] | None]] = {}
    base = _scrapers_dir()
    pattern = str(base / "platforms" / "*" / "platform.yaml")
    yaml_files = glob.glob(pattern)

    for yaml_path in yaml_files:
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                continue

            spec = PlatformSpec(**data)
            platform_name = spec.platform

            # Try to load adapter.py from the same directory
            adapter_cls = _load_adapter(yaml_path)

            registry[platform_name] = (spec, adapter_cls)
            logger.info("Registered platform: %s (%s)", platform_name, spec.display_name)
        except Exception as e:
            logger.warning("Failed to load platform from %s: %s", yaml_path, e)

    _registry_cache = registry
    return registry


def _load_adapter(yaml_path: str) -> type[BasePlatformScraper] | None:
    """Try to load adapter.py from the platform directory.

    Returns the adapter class if found, None otherwise.
    """
    platform_dir = Path(yaml_path).parent
    adapter_file = platform_dir / "adapter.py"

    if not adapter_file.exists():
        return None

    try:
        module_name = adapter_file.stem
        # Construct a module path relative to the scrapers package
        rel = adapter_file.relative_to(_scrapers_dir())
        full_module = "semilabs_hone.modules.collection.scrapers." + str(rel).replace("/", ".")[:-3]

        mod = importlib.import_module(full_module)
        # Look for a class named <PlatformName>Scraper or Adapter
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BasePlatformScraper)
                and attr is not BasePlatformScraper
            ):
                return attr
    except Exception as e:
        logger.warning("Failed to load adapter from %s: %s", yaml_path, e)

    return None


def list_platforms() -> list[str]:
    """Return sorted list of registered platform names (for UI dropdown)."""
    return sorted(load_registry().keys())


def get(platform: str) -> tuple[PlatformSpec, type[BasePlatformScraper] | None]:
    """Get (PlatformSpec, adapter_class) for a platform name.

    Raises KeyError if platform not found.
    """
    registry = load_registry()
    if platform not in registry:
        raise KeyError(f"Platform '{platform}' not found in registry. Available: {list(registry.keys())}")
    return registry[platform]
