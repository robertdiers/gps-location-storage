from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import db


RASTERS_DIR = Path(os.getenv("RASTERS_DIR", "/rasters"))
RASTER_CACHE_DIR = db.DATA_DIR / "raster-cache"
DOWNLOAD_TIMEOUT = int(os.getenv("RASTER_DOWNLOAD_TIMEOUT", "60"))
TILE_ZOOMS = os.getenv("RASTER_TILE_ZOOMS", "16-20")

_bounds_cache: dict[str, tuple[float, tuple[float, float, float, float] | None]] = {}
_count_cache: dict[str, tuple[float, int]] = {}


def prepare_rasters() -> list[str]:
    messages = []
    if not RASTERS_DIR.exists():
        print(f"[rasters] source directory missing: {RASTERS_DIR}", flush=True)
        return messages

    RASTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for meta4 in sorted(RASTERS_DIR.glob("*.meta4")):
        messages.append(_prepare_meta4(meta4))
    if not messages:
        print(f"[rasters] no .meta4 files found in {RASTERS_DIR}", flush=True)
    return messages


def start_background_tiling() -> asyncio.Task[None]:
    return asyncio.create_task(_tile_all_pending())


def tile_path(key: str, z: int, x: int, y: int) -> Path:
    return RASTER_CACHE_DIR / key / "tiles" / str(z) / str(x) / f"{y}.png"


@lru_cache(maxsize=1)
def tile_zooms() -> tuple[int, ...]:
    return tuple(_parse_zoom_range(TILE_ZOOMS))


def raster_status() -> list[dict[str, Any]]:
    zooms = tile_zooms()
    sources = db.list_raster_sources()
    for source in sources:
        source.update(_raster_progress(source))
        bounds = _wgs84_bounds(RASTER_CACHE_DIR / source["key"] / "mosaic.vrt")
        source["bounds"] = list(bounds) if bounds else None
        source["min_zoom"] = zooms[0] if zooms else None
        source["max_zoom"] = zooms[-1] if zooms else None
    return sources


def _raster_progress(source: dict[str, Any]) -> dict[str, Any]:
    tile_status = source.get("tile_status")
    if tile_status == "ready":
        return {"tiles_done": None, "tiles_total": None, "progress_percent": 100}
    if tile_status != "tiling":
        return {"tiles_done": None, "tiles_total": None, "progress_percent": 0 if tile_status == "pending" else None}

    cache_dir = RASTER_CACHE_DIR / source["key"]
    done = _count_tiles(cache_dir / "tiles.tmp")
    bounds = _wgs84_bounds(cache_dir / "mosaic.vrt")
    total = 0
    if bounds is not None:
        total = sum(_tile_count_for_zoom(bounds, z) for z in _parse_zoom_range(TILE_ZOOMS))
    percent = None
    if total > 0:
        percent = min(99, max(0, int(done * 100 / total)))
    return {"tiles_done": done, "tiles_total": total or None, "progress_percent": percent}


def _count_tiles(directory: Path) -> int:
    if not directory.exists():
        return 0
    cache_key = str(directory)
    now = time.monotonic()
    cached = _count_cache.get(cache_key)
    if cached is not None and now - cached[0] < 2.0:
        return cached[1]
    count = 0
    for _root, _dirs, files in os.walk(directory):
        count += sum(1 for name in files if name.endswith(".png"))
    _count_cache[cache_key] = (now, count)
    return count


def _wgs84_bounds(vrt_path: Path) -> tuple[float, float, float, float] | None:
    try:
        mtime = vrt_path.stat().st_mtime
    except OSError:
        return None
    cache_key = str(vrt_path)
    cached = _bounds_cache.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    bounds = None
    try:
        proc = subprocess.run(
            ["gdalinfo", "-json", str(vrt_path)],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if proc.returncode == 0:
            info = json.loads(proc.stdout)
            points = [point for ring in info.get("wgs84Extent", {}).get("coordinates", []) for point in ring]
            if points:
                lons = [float(point[0]) for point in points]
                lats = [float(point[1]) for point in points]
                bounds = (min(lons), min(lats), max(lons), max(lats))
    except Exception as exc:  # noqa: BLE001 - progress is optional
        print(f"[rasters] WARNING could not determine raster extent for progress: {exc}", flush=True)
    _bounds_cache[cache_key] = (mtime, bounds)
    return bounds


def _tile_count_for_zoom(bounds: tuple[float, float, float, float], zoom: int) -> int:
    min_lon, min_lat, max_lon, max_lat = bounds
    limit = 2**zoom

    def tile_x(lon: float) -> int:
        return max(0, min(limit - 1, int((lon + 180.0) / 360.0 * limit)))

    def tile_y(lat: float) -> int:
        lat = max(-85.05112878, min(85.05112878, lat))
        value = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * limit
        return max(0, min(limit - 1, int(value)))

    x_min, x_max = tile_x(min_lon), tile_x(max_lon)
    y_min, y_max = tile_y(max_lat), tile_y(min_lat)
    return (x_max - x_min + 1) * (y_max - y_min + 1)


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


def _prepare_meta4(meta4: Path) -> str:
    key = meta4.stem
    source_hash = _sha256(meta4)
    current = db.get_raster_source(key)
    source_dir = RASTER_CACHE_DIR / key / "source"

    if (
        current
        and current.get("source_hash") == source_hash
        and current.get("source_status") in {"downloaded", "ready"}
        and source_dir.exists()
    ):
        message = f"[rasters] {key}: unchanged, using cached source files"
        print(message, flush=True)
        return message

    print(f"[rasters] {key}: importing {meta4.name}", flush=True)
    try:
        entries = _read_meta4(meta4)
        if not entries:
            raise RuntimeError("no downloadable files found in meta4")

        tmp_dir = RASTER_CACHE_DIR / key / "source.tmp"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        raster_files = []
        for entry in entries:
            target = tmp_dir / entry["filename"]
            print(f"[rasters] {key}: downloading {entry['filename']}", flush=True)
            _download(entry["url"], target)
            raster_files.append({**entry, "local_path": str(source_dir / entry["filename"])})

        if source_dir.exists():
            shutil.rmtree(source_dir)
        tmp_dir.rename(source_dir)

        db.replace_raster_files(key, raster_files)
        db.upsert_raster_source(
            key,
            {
                "source_file": str(meta4),
                "source_hash": source_hash,
                "source_status": "downloaded",
                "tile_status": "pending",
                "source_message": f"Downloaded {len(raster_files)} raster files",
                "tile_message": "Waiting for background tiling",
                "source_path": str(source_dir),
                "tiles_path": str(RASTER_CACHE_DIR / key / "tiles"),
                "imported_at": db.utc_now(),
                "tiled_at": current.get("tiled_at") if current else None,
            },
        )
        message = f"[rasters] {key}: downloaded {len(raster_files)} files"
        print(message, flush=True)
        return message
    except Exception as exc:  # noqa: BLE001 - keep app startup resilient
        message = f"[rasters] {key}: WARNING download failed, keeping existing cache if present: {exc}"
        print(message, flush=True)
        old_hash = current.get("source_hash", "") if current else ""
        db.upsert_raster_source(
            key,
            {
                "source_file": str(meta4),
                "source_hash": old_hash,
                "source_status": "failed_download",
                "source_message": message,
                "tile_status": current.get("tile_status", "missing") if current else "missing",
                "tile_message": current.get("tile_message", "") if current else "",
                "source_path": str(source_dir) if source_dir.exists() else "",
                "tiles_path": current.get("tiles_path", "") if current else "",
                "imported_at": current.get("imported_at") if current else None,
                "tiled_at": current.get("tiled_at") if current else None,
            },
        )
        return message


async def _tile_all_pending() -> None:
    for source in db.list_raster_sources():
        if source["source_status"] not in {"downloaded", "ready", "failed_download"}:
            continue
        if source["tile_status"] == "ready" and Path(source["tiles_path"]).exists():
            print(f"[rasters] {source['key']}: tiles unchanged, skipping", flush=True)
            continue
        if not source["source_path"] or not Path(source["source_path"]).exists():
            print(f"[rasters] {source['key']}: no source TIFFs available for tiling", flush=True)
            continue
        await asyncio.to_thread(_tile_source, source)


def _tile_source(source: dict[str, Any]) -> None:
    key = source["key"]
    source_dir = Path(source["source_path"])
    cache_dir = RASTER_CACHE_DIR / key
    tiles_dir = cache_dir / "tiles"
    tmp_tiles = cache_dir / "tiles.tmp"
    georef_dir = cache_dir / "georef.tmp"
    vrt_path = cache_dir / "mosaic.vrt"
    raster_files = db.list_raster_files(key)
    if not raster_files:
        print(f"[rasters] {key}: no TIFF files found for tiling", flush=True)
        return

    print(f"[rasters] {key}: background tiling started with {len(raster_files)} TIFFs", flush=True)
    db.upsert_raster_source(key, {**source, "tile_status": "tiling", "tile_message": "GDAL tiling in progress"})
    try:
        if tmp_tiles.exists():
            shutil.rmtree(tmp_tiles)
        if georef_dir.exists():
            shutil.rmtree(georef_dir)
        tmp_tiles.mkdir(parents=True, exist_ok=True)
        georef_dir.mkdir(parents=True, exist_ok=True)

        georef_tiffs = []
        for item in raster_files:
            source_tiff = Path(item["local_path"])
            georef_tiff = georef_dir / source_tiff.name
            _run(
                [
                    "gdal_translate",
                    "-a_srs",
                    item["crs"],
                    "-a_ullr",
                    str(item["min_x"]),
                    str(item["max_y"]),
                    str(item["max_x"]),
                    str(item["min_y"]),
                    str(source_tiff),
                    str(georef_tiff),
                ],
                key,
            )
            georef_tiffs.append(str(georef_tiff))

        _run(["gdalbuildvrt", str(vrt_path), *georef_tiffs], key)
        _run(["gdal2tiles.py", "--xyz", "-z", TILE_ZOOMS, "-w", "none", str(vrt_path), str(tmp_tiles)], key)
        if tiles_dir.exists():
            shutil.rmtree(tiles_dir)
        tmp_tiles.rename(tiles_dir)
        if georef_dir.exists():
            shutil.rmtree(georef_dir)
        db.upsert_raster_source(
            key,
            {
                **source,
                "source_status": "ready",
                "tile_status": "ready",
                "tile_message": f"Tiles ready in {tiles_dir}",
                "tiles_path": str(tiles_dir),
                "tiled_at": db.utc_now(),
            },
        )
        print(f"[rasters] {key}: background tiling finished", flush=True)
    except Exception as exc:  # noqa: BLE001 - old tiles must remain usable
        message = f"GDAL tiling failed, keeping existing tiles if present: {exc}"
        tile_status = "ready" if tiles_dir.exists() else "failed_tiles"
        db.upsert_raster_source(key, {**source, "tile_status": tile_status, "tile_message": message})
        print(f"[rasters] {key}: WARNING {message}", flush=True)
        if tmp_tiles.exists():
            shutil.rmtree(tmp_tiles)
        if georef_dir.exists():
            shutil.rmtree(georef_dir)


def _run(command: list[str], key: str) -> None:
    print(f"[rasters] {key}: running {' '.join(command)}", flush=True)
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    if proc.stdout:
        print(proc.stdout, flush=True)
    if proc.stderr:
        print(proc.stderr, flush=True)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed with exit code {proc.returncode}: {' '.join(command)}")


def _read_meta4(meta4: Path) -> list[dict[str, Any]]:
    ns = {"m": "urn:ietf:params:xml:ns:metalink"}
    root = ET.parse(meta4).getroot()
    entries = []
    for file_node in root.findall("m:file", ns):
        filename = file_node.attrib.get("name", "").strip()
        url_node = file_node.find("m:url", ns)
        if not filename or url_node is None or not url_node.text:
            continue
        url = url_node.text.strip()
        bbox = _bbox_from_url(url)
        entries.append(
            {
                "filename": filename,
                "url": url,
                "min_x": bbox[0],
                "min_y": bbox[1],
                "max_x": bbox[2],
                "max_y": bbox[3],
                "crs": _crs_from_url(url),
            }
        )
    return entries


def _bbox_from_url(url: str) -> tuple[float, float, float, float]:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    bbox = query.get("BBOX") or query.get("bbox")
    if not bbox:
        raise RuntimeError(f"missing BBOX in URL: {url}")
    values = [float(part) for part in bbox[0].split(",")]
    if len(values) != 4:
        raise RuntimeError(f"invalid BBOX in URL: {url}")
    return values[0], values[1], values[2], values[3]


def _crs_from_url(url: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    value = (query.get("SRS") or query.get("srs") or query.get("CRS") or query.get("crs") or ["EPSG:25832"])[0]
    return value.upper()


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "gps-location-storage/1.0"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        with target.open("wb") as out:
            shutil.copyfileobj(response, out)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
