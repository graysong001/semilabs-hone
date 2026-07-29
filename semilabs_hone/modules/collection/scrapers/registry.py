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


def _builtin_platform_files() -> list[str]:
    """platform.yaml files shipped inside the package."""
    return sorted(glob.glob(str(_scrapers_dir() / "platforms" / "*" / "platform.yaml")))


def user_platforms_dir() -> Path:
    """Where recorded platforms live: ``data/collection/platforms/``.

    Recording a new site produces user data, not package code, so generated
    specs land under DATA_DIR and are discovered from there (USER_SOP G13,
    design §19). Config is read lazily so tests can redirect DATA_DIR.
    """
    import config

    return config.DATA_DIR / "collection" / "platforms"


def _user_platform_files() -> list[str]:
    """platform.yaml files recorded by the user."""
    try:
        return sorted(glob.glob(str(user_platforms_dir() / "*" / "platform.yaml")))
    except Exception as exc:  # unreadable DATA_DIR must not kill the registry
        logger.warning("Cannot scan user platforms dir: %s", exc)
        return []


def load_registry(
    force: bool = False,
) -> dict[str, tuple[PlatformSpec, type[BasePlatformScraper] | None]]:
    """Load every platform.yaml, built-in first and user-recorded second.

    Returns:
        {platform_name: (PlatformSpec, adapter_class_or_None)}

    A user-recorded spec with the same platform name overrides the built-in
    one, so a hand-tuned recording wins over the shipped default.
    """
    global _registry_cache

    if _registry_cache is not None and not force:
        return _registry_cache

    registry: dict[str, tuple[PlatformSpec, type[BasePlatformScraper] | None]] = {}

    for yaml_path in _builtin_platform_files() + _user_platform_files():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                continue

            spec = PlatformSpec(**data)
            registry[spec.platform] = (spec, _load_adapter(yaml_path))
            logger.info("Registered platform: %s (%s)", spec.platform, spec.display_name)
        except Exception as e:
            logger.warning("Failed to load platform from %s: %s", yaml_path, e)

    _registry_cache = registry
    return registry


def reset_cache() -> None:
    """Forget the loaded registry (after recording a new platform, or in tests)."""
    global _registry_cache
    _registry_cache = None


def _load_adapter(yaml_path: str) -> type[BasePlatformScraper] | None:
    """Load adapter.py sitting next to a built-in platform.yaml, if any.

    Only package-local platforms can carry an adapter: user-recorded specs
    live under data/ and are pure declarative YAML (no code execution).
    """
    platform_dir = Path(yaml_path).parent
    adapter_file = platform_dir / "adapter.py"

    if not adapter_file.exists():
        return None
    try:
        rel = adapter_file.relative_to(_scrapers_dir())
    except ValueError:
        logger.warning("Ignoring adapter.py outside the package: %s", adapter_file)
        return None

    try:
        full_module = "semilabs_hone.modules.collection.scrapers." + str(rel).replace("/", ".")[:-3]
        mod = importlib.import_module(full_module)
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
