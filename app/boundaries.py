from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import shape as shapely_shape
from shapely.ops import transform as shapely_transform

from . import db


SHAPES_DIR = Path(os.getenv("SHAPES_DIR", "/shapes"))


def reload_boundaries() -> int:
    db.clear_boundaries()
    if not SHAPES_DIR.exists():
        return 0

    total = 0
    for archive in sorted(SHAPES_DIR.glob("*.zip")):
        total += _load_archive(archive)
    return total


def as_feature_collection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": json.loads(row["properties_json"]),
                "geometry": json.loads(row["geometry_json"]),
            }
            for row in rows
        ],
    }


def _load_archive(archive: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp_path)

        shp_files = [path for path in tmp_path.rglob("*.shp") if "flurstueck" in path.stem.lower()]
        if not shp_files:
            return 0

        source_id = db.create_boundary_source(archive.name)
        count = 0
        batch = []
        for shp_file in shp_files:
            transformer = _transformer_for(shp_file)
            reader = shapefile.Reader(str(shp_file), encoding=_encoding_for(shp_file))
            field_names = [field[0] for field in reader.fields[1:]]
            for record in reader.iterShapeRecords():
                geom = shapely_shape(record.shape.__geo_interface__)
                if geom.is_empty:
                    continue
                geom = shapely_transform(transformer.transform, geom)
                props = dict(zip(field_names, record.record))
                props["source"] = archive.name
                batch.append((source_id, json.dumps(props, ensure_ascii=False, default=str), json.dumps(geom.__geo_interface__), geom.bounds))
                count += 1

        db.insert_boundary_features(batch)
        return count


def _transformer_for(shp_file: Path) -> Transformer:
    prj_file = shp_file.with_suffix(".prj")
    if prj_file.exists():
        source_crs = CRS.from_wkt(prj_file.read_text(encoding="utf-8"))
    else:
        source_crs = CRS.from_epsg(25832)
    return Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)


def _encoding_for(shp_file: Path) -> str:
    cpg_file = shp_file.with_suffix(".cpg")
    if not cpg_file.exists():
        return "utf-8"
    value = cpg_file.read_text(encoding="ascii", errors="ignore").strip()
    return value or "utf-8"
