from __future__ import annotations

import math
import os
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path

from . import db


PROVIDER = "osm"
TEMP_CACHE_DIR = db.DATA_DIR / "osm-cache"
PERMANENT_TILES_DIR = Path(os.getenv("TILES_DIR", "/tiles")) / "osm"
OSM_TILE_URL = os.getenv("OSM_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
OSM_TILE_USER_AGENT = os.getenv("OSM_TILE_USER_AGENT", "gps-location-storage/1.0")
OSM_CACHE_TTL_SECONDS = int(os.getenv("OSM_CACHE_TTL_SECONDS", str(24 * 60 * 60)))
OSM_PREFETCH_RADIUS_M = float(os.getenv("OSM_PREFETCH_RADIUS_M", "250"))
OSM_PREFETCH_ZOOMS = os.getenv("OSM_PREFETCH_ZOOMS", "16-19")
OSM_DOWNLOAD_TIMEOUT = int(os.getenv("OSM_DOWNLOAD_TIMEOUT", "20"))


def tile_response_path(z: int, x: int, y: int) -> Path | None:
    _validate_tile(z, x, y)
    permanent = _permanent_path(z, x, y)
    if permanent.exists():
        db.touch_map_tile(PROVIDER, z, x, y, "permanent")
        return permanent

    temporary = _temporary_path(z, x, y)
    if temporary.exists() and _is_fresh(temporary):
        db.touch_map_tile(PROVIDER, z, x, y, "temporary")
        return temporary

    try:
        _download_tile(z, x, y, temporary)
        _record_tile(z, x, y, temporary, "temporary", None)
        return temporary
    except Exception as exc:  # noqa: BLE001 - old cached tiles are better than no map
        print(f"[osm] WARNING download failed for {z}/{x}/{y}, using stale tile if present: {exc}", flush=True)
        if temporary.exists():
            db.touch_map_tile(PROVIDER, z, x, y, "temporary")
            return temporary
        return None


def prefetch_tiles_for_stone(stone: dict) -> None:
    try:
        lat = float(stone["latitude"])
        lon = float(stone["longitude"])
        stone_id = int(stone["id"])
        for z in _parse_zoom_range(OSM_PREFETCH_ZOOMS):
            for x, y in _tiles_for_radius(lat, lon, z, OSM_PREFETCH_RADIUS_M):
                path = _permanent_path(z, x, y)
                if path.exists():
                    _record_tile(z, x, y, path, "permanent", stone_id)
                    continue
                try:
                    _download_tile(z, x, y, path)
                    _record_tile(z, x, y, path, "permanent", stone_id)
                except Exception as exc:  # noqa: BLE001 - do not fail point creation because map storage failed
                    print(f"[osm] WARNING permanent prefetch failed for stone {stone_id} tile {z}/{x}/{y}: {exc}", flush=True)
        print(f"[osm] permanent prefetch finished for stone {stone_id}", flush=True)
    except Exception as exc:  # noqa: BLE001 - background jobs must not crash request handling
        print(f"[osm] WARNING permanent prefetch failed: {exc}", flush=True)


def _temporary_path(z: int, x: int, y: int) -> Path:
    return TEMP_CACHE_DIR / str(z) / str(x) / f"{y}.png"


def _permanent_path(z: int, x: int, y: int) -> Path:
    return PERMANENT_TILES_DIR / str(z) / str(x) / f"{y}.png"


def _download_tile(z: int, x: int, y: int, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    url = OSM_TILE_URL.format(z=z, x=x, y=y)
    request = urllib.request.Request(url, headers={"User-Agent": OSM_TILE_USER_AGENT})
    with urllib.request.urlopen(request, timeout=OSM_DOWNLOAD_TIMEOUT) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for {url}")
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            shutil.copyfileobj(response, tmp)
            tmp_path = Path(tmp.name)
    tmp_path.replace(target)


def _record_tile(z: int, x: int, y: int, path: Path, storage: str, stone_id: int | None) -> None:
    db.upsert_map_tile(
        PROVIDER,
        z,
        x,
        y,
        {
            "storage": storage,
            "path": str(path),
            "status": "ready",
            "stone_id": stone_id,
        },
    )


def _is_fresh(path: Path) -> bool:
    return time.time() - path.stat().st_mtime <= OSM_CACHE_TTL_SECONDS


def _validate_tile(z: int, x: int, y: int) -> None:
    if z < 0 or z > 22:
        raise ValueError("invalid zoom")
    limit = 2**z
    if x < 0 or y < 0 or x >= limit or y >= limit:
        raise ValueError("invalid tile coordinates")


def _parse_zoom_range(value: str) -> list[int]:
    zooms: set[int] = set()
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            start, end = [int(piece) for piece in item.split("-", 1)]
            zooms.update(range(start, end + 1))
        else:
            zooms.add(int(item))
    return sorted(z for z in zooms if 0 <= z <= 22)


def _tiles_for_radius(lat: float, lon: float, z: int, radius_m: float) -> set[tuple[int, int]]:
    lat_delta = radius_m / 111_320
    lon_delta = radius_m / (111_320 * max(math.cos(math.radians(lat)), 0.01))
    min_x, max_y = _lat_lon_to_tile(lat - lat_delta, lon - lon_delta, z)
    max_x, min_y = _lat_lon_to_tile(lat + lat_delta, lon + lon_delta, z)
    limit = 2**z
    tiles = set()
    for x in range(max(0, min_x), min(limit - 1, max_x) + 1):
        for y in range(max(0, min_y), min(limit - 1, max_y) + 1):
            tiles.add((x, y))
    return tiles


def _lat_lon_to_tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    lon = max(min(lon, 180), -180)
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)
