from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .boundaries import as_feature_collection, reload_boundaries
from .exports import as_csv, as_geojson, as_gpx
from .osm_tiles import prefetch_tiles_for_stone, tile_response_path
from .rasters import prepare_rasters, raster_status, start_background_tiling, tile_path


class StoneIn(BaseModel):
    name: str = ""
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    source: str = "manual"
    notes: str = ""


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    reload_boundaries()
    prepare_rasters()
    tiling_task = start_background_tiling()
    yield
    tiling_task.cancel()


app = FastAPI(title="GPS Location Storage", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse("app/static/sw.js", media_type="application/javascript")


@app.get("/")
def index():
    return HTMLResponse(
        """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>gps-location</title>
  <meta name="theme-color" content="#2f6f3e">
  <link rel="manifest" href="/static/manifest.webmanifest">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <header>
    <h1>gps-location</h1>
    <div class="exports">
      <a class="button secondary" href="/api/export.geojson">GeoJSON</a>
      <a class="button secondary" href="/api/export.csv">CSV</a>
      <a class="button secondary" href="/api/export.gpx">GPX</a>
    </div>
  </header>
  <main>
    <section class="map-panel">
      <div id="map"></div>
      <div class="toolbar">
        <button id="locate-button" type="button">Aktuelle Position</button>
        <button id="save-here-button" type="button" class="primary">Grenzstein hier speichern</button>
        <label class="inline-toggle"><input id="boundaries-toggle" type="checkbox" checked> Flurstücksgrenzen</label>
        <span id="gps-status">Noch keine Position ermittelt</span>
        <span id="sync-status" class="sync-status">Online</span>
      </div>
    </section>
    <aside class="side-panel">
      <form id="stone-form">
        <input type="hidden" id="stone-id">
        <label>Name <input id="name" name="name" placeholder="z. B. Grenzstein 1"></label>
        <div class="grid">
          <label>Breite <input id="latitude" name="latitude" inputmode="decimal" required></label>
          <label>Laenge <input id="longitude" name="longitude" inputmode="decimal" required></label>
        </div>
        <div class="grid">
          <label>Genauigkeit m <input id="accuracy_m" name="accuracy_m" inputmode="decimal"></label>
          <label>Quelle <input id="source" name="source" value="manual"></label>
        </div>
        <label>Notiz <textarea id="notes" name="notes" rows="4"></textarea></label>
        <label>Foto fuer neuen Punkt <input id="new-photo-file" name="new-photo" type="file" accept="image/*" capture="environment"></label>
        <label>Fotonotiz <input id="new-photo-caption" name="new-photo-caption" placeholder="z. B. Blick vom Weg Richtung Norden"></label>
        <div class="actions">
          <button type="submit" class="primary">Speichern</button>
          <button id="sync-button" type="button">Sync</button>
          <button id="new-button" type="button">Neu</button>
          <button id="delete-button" type="button" class="danger">Loeschen</button>
        </div>
      </form>
      <section id="photos-section" class="photos-section" hidden>
        <h2>Fotos</h2>
        <form id="photo-form">
          <label>Foto <input id="photo-file" name="photo" type="file" accept="image/*" capture="environment" required></label>
          <label>Fotonotiz <input id="photo-caption" name="caption" placeholder="z. B. Blick vom Weg Richtung Norden"></label>
          <button type="submit" class="primary">Foto hinzufügen</button>
        </form>
        <div id="photo-list" class="photo-list"></div>
      </section>
      <h2>Gespeicherte Punkte</h2>
      <div id="stone-list"></div>
    </aside>
  </main>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="/static/app.js"></script>
</body>
</html>"""
    )


@app.get("/api/stones")
def api_list_stones():
    return db.list_stones()


@app.post("/api/stones", status_code=201)
def api_create_stone(stone: StoneIn, background_tasks: BackgroundTasks):
    created = db.create_stone(stone.model_dump())
    background_tasks.add_task(prefetch_tiles_for_stone, created)
    return created


@app.put("/api/stones/{stone_id}")
def api_update_stone(stone_id: int, stone: StoneIn):
    updated = db.update_stone(stone_id, stone.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Stone not found")
    return updated


@app.delete("/api/stones/{stone_id}", status_code=204)
def api_delete_stone(stone_id: int):
    photos = db.list_photos(stone_id)
    if not db.delete_stone(stone_id):
        raise HTTPException(status_code=404, detail="Stone not found")
    for photo in photos:
        path = db.photo_path(photo)
        if path.exists():
            path.unlink()
    return Response(status_code=204)


@app.get("/api/stones/{stone_id}/photos")
def api_list_photos(stone_id: int):
    if db.get_stone(stone_id) is None:
        raise HTTPException(status_code=404, detail="Stone not found")
    return db.list_photos(stone_id)


@app.post("/api/stones/{stone_id}/photos", status_code=201)
async def api_create_photo(
    stone_id: int,
    photo: UploadFile = File(...),
    caption: str = Form(default=""),
):
    if db.get_stone(stone_id) is None:
        raise HTTPException(status_code=404, detail="Stone not found")
    content_type = photo.content_type or "application/octet-stream"
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image uploads are allowed")

    created = db.create_photo(stone_id, photo.filename or "", content_type, caption)
    target = db.photo_path(created)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as out:
        while chunk := await photo.read(1024 * 1024):
            out.write(chunk)
    return created


@app.get("/api/photos/{photo_id}")
def api_get_photo(photo_id: int):
    photo = db.get_photo(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    path = db.photo_path(photo)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Photo file not found")
    return FileResponse(path, media_type=photo["content_type"], filename=photo["original_filename"] or photo["filename"])


@app.delete("/api/photos/{photo_id}", status_code=204)
def api_delete_photo(photo_id: int):
    photo = db.delete_photo(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    path = db.photo_path(photo)
    if path.exists():
        path.unlink()
    return Response(status_code=204)


@app.get("/api/boundaries")
def api_boundaries(bbox: str = Query(..., pattern=r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?,-?\d+(\.\d+)?,-?\d+(\.\d+)?$")):
    min_lon, min_lat, max_lon, max_lat = [float(part) for part in bbox.split(",")]
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=400, detail="Invalid bbox")
    return as_feature_collection(db.list_boundaries(min_lon, min_lat, max_lon, max_lat))


@app.get("/api/boundaries/status")
def api_boundaries_status():
    return {"count": db.boundary_count()}


@app.get("/api/rasters/status")
def api_rasters_status():
    return raster_status()


@app.get("/tiles/{key}/{z}/{x}/{y}.png")
def api_raster_tile(key: str, z: int, x: int, y: int):
    path = tile_path(key, z, x, y)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Tile not found")
    return FileResponse(path, media_type="image/png")


@app.get("/osm/{z}/{x}/{y}.png")
def api_osm_tile(z: int, x: int, y: int):
    try:
        path = tile_response_path(z, x, y)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="OSM tile not available")
    return FileResponse(path, media_type="image/png")


@app.get("/api/export.geojson")
def export_geojson():
    return Response(
        as_geojson(db.list_stones()),
        media_type="application/geo+json; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=grenzsteine.geojson"},
    )


@app.get("/api/export.csv")
def export_csv():
    return Response(
        as_csv(db.list_stones()),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=grenzsteine.csv"},
    )


@app.get("/api/export.gpx")
def export_gpx():
    return Response(
        as_gpx(db.list_stones()),
        media_type="application/gpx+xml; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=grenzsteine.gpx"},
    )
