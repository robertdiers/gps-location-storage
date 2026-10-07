from __future__ import annotations

import csv
import io
import json
from html import escape
from typing import Any


def as_geojson(stones: list[dict[str, Any]]) -> str:
    features = []
    for stone in stones:
        properties = {key: value for key, value in stone.items() if key not in {"latitude", "longitude"}}
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [stone["longitude"], stone["latitude"]],
                },
                "properties": properties,
            }
        )
    return json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=2)


def as_csv(stones: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    fieldnames = ["id", "name", "latitude", "longitude", "accuracy_m", "source", "notes", "created_at", "updated_at"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(stones)
    return output.getvalue()


def as_gpx(stones: list[dict[str, Any]]) -> str:
    waypoints = []
    for stone in stones:
        name = escape(stone.get("name") or f"Punkt {stone['id']}")
        notes = escape(stone.get("notes") or "")
        time = escape(stone.get("created_at") or "")
        waypoints.append(
            f'  <wpt lat="{stone["latitude"]}" lon="{stone["longitude"]}">\n'
            f"    <name>{name}</name>\n"
            f"    <time>{time}</time>\n"
            f"    <desc>{notes}</desc>\n"
            f"  </wpt>"
        )
    joined = "\n".join(waypoints)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="gps-location-storage" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"{joined}\n"
        "</gpx>\n"
    )
