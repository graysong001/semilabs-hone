"""Async image downloader with disk-space checks.

- ``download_images`` saves to ``data/collection/images/<note_id>/``.
- ``check_disk`` reports total size and warns/stops based on config thresholds.

``httpx`` is lazily imported so the module is importable even without it.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class DiskStatus:
    """Result of a disk check."""

    total_bytes: int
    total_gb: float
    warn: bool
    stop: bool
    message: str


def _images_dir() -> Path:
    from config import DATA_DIR
    return DATA_DIR / "collection" / "images"


def _dir_size_bytes(directory: Path) -> int:
    """Sum file sizes under *directory* (du equivalent)."""
    total = 0
    if not directory.exists():
        return 0
    for p in directory.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


async def check_disk() -> DiskStatus:
    """Check disk usage of the images directory.

    - If total size >= ``config.IMAGE_DISK_WARN_GB`` (default 30 GB), return
      with ``warn=True`` (does **not** interrupt).
    - If total size >= ``config.IMAGE_DISK_STOP_GB`` (default ``None``=off),
      ``stop=True`` is set; the caller should raise ``DiskFullError``.
    - If the underlying partition free space < 2 GB, also warn.
    """
    from config import DATA_DIR, IMAGE_DISK_WARN_GB, IMAGE_DISK_STOP_GB

    images = _images_dir()
    total_bytes = _dir_size_bytes(images)
    total_gb = total_bytes / (1024 ** 3)

    warn = False
    stop = False
    messages: list[str] = []

    # 30 GB directory warning
    if total_gb >= IMAGE_DISK_WARN_GB:
        warn = True
        messages.append(
            f"Images directory is {total_gb:.1f} GB (warn threshold: {IMAGE_DISK_WARN_GB} GB). "
            "Consider cleaning up."
        )

    # Hard stop threshold (default off)
    if IMAGE_DISK_STOP_GB is not None and total_gb >= IMAGE_DISK_STOP_GB:
        stop = True
        messages.append(
            f"Images directory reached {total_gb:.1f} GB (stop threshold: {IMAGE_DISK_STOP_GB} GB)."
        )

    # Free space check on the partition
    try:
        usage = shutil.disk_usage(str(DATA_DIR))
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 2:
            warn = True
            messages.append(
                f"Partition free space is {free_gb:.1f} GB (< 2 GB). "
                "Images download may fail."
            )
    except OSError:
        pass

    return DiskStatus(
        total_bytes=total_bytes,
        total_gb=total_gb,
        warn=warn,
        stop=stop,
        message=" ".join(messages),
    )


async def _download_one(client: Any, url: str, dest: Path) -> Path | None:
    """Download a single image. Returns the destination path on success."""
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        logger.debug(f"Downloaded image: {dest.name}")
        return dest
    except Exception as exc:
        logger.warning(f"Failed to download {url}: {exc}")
        return None


async def download_images(
    urls: list[str],
    note_id: str,
    max_concurrency: int = 4,
) -> list[Path]:
    """Download a list of image URLs concurrently.

    Args:
        urls: list of image URLs to download.
        note_id: note identifier (used as subdirectory name).
        max_concurrency: max parallel downloads (default 4).

    Returns:
        List of successfully downloaded file paths.
        Single-image failures do not block the batch; they are logged as warnings.
    """
    # Lazy import so the module is importable without httpx.
    import httpx

    images = _images_dir() / note_id
    images.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[Path | None] = [None] * len(urls)

    async def _limited(idx: int, url: str) -> None:
        async with semaphore:
            ext = ".jpg"  # default
            # Try to extract extension from URL
            url_lower = url.lower().split("?")[0]
            for candidate in [".webp", ".png", ".gif", ".jpg", ".jpeg", ".bmp"]:
                if url_lower.endswith(candidate):
                    ext = candidate
                    break
            dest = images / f"{idx:04d}{ext}"
            results[idx] = await _download_one(client, url, dest)

    # Check disk before downloading
    status = await check_disk()
    if status.stop:
        from semilabs_hone.core.utils.retry import DiskFullError
        raise DiskFullError(status.message)

    if status.warn:
        logger.warning(status.message)

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            asyncio.create_task(_limited(i, u))
            for i, u in enumerate(urls)
        ]
        await asyncio.gather(*tasks)

    downloaded = [r for r in results if r is not None]
    failed_count = len(urls) - len(downloaded)
    if failed_count:
        logger.warning(
            f"download_images: {failed_count}/{len(urls)} images failed for note_id={note_id}"
        )

    return downloaded
