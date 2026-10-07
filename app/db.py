from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path(os.getenv("DB_PATH", "/data/app.sqlite"))
DATA_DIR = DB_PATH.parent
PHOTOS_DIR = DATA_DIR / "photos"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db() -> None:
    with connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS stones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                accuracy_m REAL,
                source TEXT NOT NULL DEFAULT 'manual',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS boundary_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                loaded_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS boundary_features (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                properties_json TEXT NOT NULL,
                geometry_json TEXT NOT NULL,
                min_lon REAL NOT NULL,
                min_lat REAL NOT NULL,
                max_lon REAL NOT NULL,
                max_lat REAL NOT NULL,
                FOREIGN KEY (source_id) REFERENCES boundary_sources(id) ON DELETE CASCADE
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_boundary_bbox
            ON boundary_features (min_lon, min_lat, max_lon, max_lat)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS raster_sources (
                key TEXT PRIMARY KEY,
                source_file TEXT NOT NULL,
                source_hash TEXT NOT NULL DEFAULT '',
                source_status TEXT NOT NULL DEFAULT 'missing',
                tile_status TEXT NOT NULL DEFAULT 'missing',
                source_message TEXT NOT NULL DEFAULT '',
                tile_message TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                tiles_path TEXT NOT NULL DEFAULT '',
                imported_at TEXT,
                tiled_at TEXT
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS raster_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                filename TEXT NOT NULL,
                url TEXT NOT NULL,
                local_path TEXT NOT NULL,
                min_x REAL NOT NULL,
                min_y REAL NOT NULL,
                max_x REAL NOT NULL,
                max_y REAL NOT NULL,
                crs TEXT NOT NULL,
                FOREIGN KEY (source_key) REFERENCES raster_sources(key) ON DELETE CASCADE
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS stone_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stone_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                original_filename TEXT NOT NULL DEFAULT '',
                content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                caption TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (stone_id) REFERENCES stones(id) ON DELETE CASCADE
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS map_tiles (
                provider TEXT NOT NULL,
                z INTEGER NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                storage TEXT NOT NULL,
                path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready',
                stone_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL,
                PRIMARY KEY (provider, z, x, y, storage),
                FOREIGN KEY (stone_id) REFERENCES stones(id) ON DELETE SET NULL
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_map_tiles_lookup
            ON map_tiles (provider, z, x, y)
            """
        )


def list_stones() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM stones ORDER BY created_at DESC, id DESC").fetchall()
    return [dict(row) for row in rows]


def get_stone(stone_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM stones WHERE id = ?", (stone_id,)).fetchone()
    return dict(row) if row else None


def create_stone(data: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO stones (name, latitude, longitude, accuracy_m, source, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("name", "").strip(),
                float(data["latitude"]),
                float(data["longitude"]),
                _optional_float(data.get("accuracy_m")),
                data.get("source", "manual").strip() or "manual",
                data.get("notes", "").strip(),
                now,
                now,
            ),
        )
        stone_id = cur.lastrowid
    stone = get_stone(int(stone_id))
    assert stone is not None
    return stone


def update_stone(stone_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    if get_stone(stone_id) is None:
        return None
    with connect() as con:
        con.execute(
            """
            UPDATE stones
               SET name = ?, latitude = ?, longitude = ?, accuracy_m = ?, source = ?, notes = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                data.get("name", "").strip(),
                float(data["latitude"]),
                float(data["longitude"]),
                _optional_float(data.get("accuracy_m")),
                data.get("source", "manual").strip() or "manual",
                data.get("notes", "").strip(),
                utc_now(),
                stone_id,
            ),
        )
    return get_stone(stone_id)


def delete_stone(stone_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM stones WHERE id = ?", (stone_id,))
    return cur.rowcount > 0


def list_photos(stone_id: int) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM stone_photos WHERE stone_id = ? ORDER BY created_at DESC, id DESC",
            (stone_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_photo(photo_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM stone_photos WHERE id = ?", (photo_id,)).fetchone()
    return dict(row) if row else None


def create_photo(stone_id: int, original_filename: str, content_type: str, caption: str) -> dict[str, Any]:
    now = utc_now()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO stone_photos (stone_id, filename, original_filename, content_type, caption, created_at)
            VALUES (?, '', ?, ?, ?, ?)
            """,
            (stone_id, original_filename, content_type, caption.strip(), now),
        )
        photo_id = int(cur.lastrowid)
        filename = f"{photo_id}{_extension_for(content_type, original_filename)}"
        con.execute("UPDATE stone_photos SET filename = ? WHERE id = ?", (filename, photo_id))
    photo = get_photo(photo_id)
    assert photo is not None
    return photo


def delete_photo(photo_id: int) -> dict[str, Any] | None:
    photo = get_photo(photo_id)
    if photo is None:
        return None
    with connect() as con:
        con.execute("DELETE FROM stone_photos WHERE id = ?", (photo_id,))
    return photo


def photo_path(photo: dict[str, Any]) -> Path:
    return PHOTOS_DIR / str(photo["stone_id"]) / photo["filename"]


def clear_boundaries() -> None:
    with connect() as con:
        con.execute("DELETE FROM boundary_features")
        con.execute("DELETE FROM boundary_sources")


def create_boundary_source(filename: str) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO boundary_sources (filename, loaded_at) VALUES (?, ?)",
            (filename, utc_now()),
        )
        return int(cur.lastrowid)


def insert_boundary_features(features: list[tuple[int, str, str, tuple[float, float, float, float]]]) -> None:
    if not features:
        return
    with connect() as con:
        con.executemany(
            """
            INSERT INTO boundary_features
                (source_id, properties_json, geometry_json, min_lon, min_lat, max_lon, max_lat)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (source_id, properties_json, geometry_json, bounds[0], bounds[1], bounds[2], bounds[3])
                for source_id, properties_json, geometry_json, bounds in features
            ],
        )


def list_boundaries(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT properties_json, geometry_json
              FROM boundary_features
             WHERE max_lon >= ?
               AND min_lon <= ?
               AND max_lat >= ?
               AND min_lat <= ?
             ORDER BY id
            """,
            (min_lon, max_lon, min_lat, max_lat),
        ).fetchall()
    return [dict(row) for row in rows]


def boundary_count() -> int:
    with connect() as con:
        row = con.execute("SELECT COUNT(*) AS count FROM boundary_features").fetchone()
    return int(row["count"])


def get_raster_source(key: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM raster_sources WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def upsert_raster_source(key: str, data: dict[str, Any]) -> None:
    existing = get_raster_source(key)
    columns = [
        "source_file",
        "source_hash",
        "source_status",
        "tile_status",
        "source_message",
        "tile_message",
        "source_path",
        "tiles_path",
        "imported_at",
        "tiled_at",
    ]
    values = {column: data.get(column, existing.get(column, "") if existing else "") for column in columns}
    with connect() as con:
        con.execute(
            """
            INSERT INTO raster_sources
                (key, source_file, source_hash, source_status, tile_status, source_message,
                 tile_message, source_path, tiles_path, imported_at, tiled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                source_file = excluded.source_file,
                source_hash = excluded.source_hash,
                source_status = excluded.source_status,
                tile_status = excluded.tile_status,
                source_message = excluded.source_message,
                tile_message = excluded.tile_message,
                source_path = excluded.source_path,
                tiles_path = excluded.tiles_path,
                imported_at = excluded.imported_at,
                tiled_at = excluded.tiled_at
            """,
            (
                key,
                values["source_file"],
                values["source_hash"],
                values["source_status"],
                values["tile_status"],
                values["source_message"],
                values["tile_message"],
                values["source_path"],
                values["tiles_path"],
                values["imported_at"],
                values["tiled_at"],
            ),
        )


def replace_raster_files(key: str, files: list[dict[str, Any]]) -> None:
    with connect() as con:
        con.execute("DELETE FROM raster_files WHERE source_key = ?", (key,))
        con.executemany(
            """
            INSERT INTO raster_files
                (source_key, filename, url, local_path, min_x, min_y, max_x, max_y, crs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    key,
                    item["filename"],
                    item["url"],
                    item["local_path"],
                    item["min_x"],
                    item["min_y"],
                    item["max_x"],
                    item["max_y"],
                    item["crs"],
                )
                for item in files
            ],
        )


def list_raster_sources() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM raster_sources ORDER BY key").fetchall()
    return [dict(row) for row in rows]


def list_raster_files(key: str) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM raster_files WHERE source_key = ? ORDER BY filename", (key,)).fetchall()
    return [dict(row) for row in rows]


def get_map_tile(provider: str, z: int, x: int, y: int, storage: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute(
            """
            SELECT * FROM map_tiles
             WHERE provider = ? AND z = ? AND x = ? AND y = ? AND storage = ?
            """,
            (provider, z, x, y, storage),
        ).fetchone()
    return dict(row) if row else None


def upsert_map_tile(provider: str, z: int, x: int, y: int, data: dict[str, Any]) -> None:
    existing = get_map_tile(provider, z, x, y, data["storage"])
    now = utc_now()
    values = {
        "path": data["path"],
        "status": data.get("status", "ready"),
        "stone_id": data.get("stone_id", existing.get("stone_id") if existing else None),
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": data.get("updated_at", now),
        "last_accessed_at": data.get("last_accessed_at", now),
    }
    with connect() as con:
        con.execute(
            """
            INSERT INTO map_tiles
                (provider, z, x, y, storage, path, status, stone_id, created_at, updated_at, last_accessed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, z, x, y, storage) DO UPDATE SET
                path = excluded.path,
                status = excluded.status,
                stone_id = excluded.stone_id,
                updated_at = excluded.updated_at,
                last_accessed_at = excluded.last_accessed_at
            """,
            (
                provider,
                z,
                x,
                y,
                data["storage"],
                values["path"],
                values["status"],
                values["stone_id"],
                values["created_at"],
                values["updated_at"],
                values["last_accessed_at"],
            ),
        )


def touch_map_tile(provider: str, z: int, x: int, y: int, storage: str) -> None:
    with connect() as con:
        con.execute(
            """
            UPDATE map_tiles
               SET last_accessed_at = ?
             WHERE provider = ? AND z = ? AND x = ? AND y = ? AND storage = ?
            """,
            (utc_now(), provider, z, x, y, storage),
        )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _extension_for(content_type: str, original_filename: str) -> str:
    suffix = Path(original_filename).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type, ".bin")
