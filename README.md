# gps-location-storage

Lokale Web-App zum Speichern von GPS-Koordinaten fuer Grenzsteine.

## Funktionen

- Leaflet-Karte mit OpenStreetMap-Hintergrund
- OpenStreetMap-Tiles werden ueber den eigenen Server geschleust
- GPS-Position im Handy-Browser ermitteln
- als installierbare PWA nutzbar und fuer Offline-Starts zwischengespeichert
- neue Punkte mit Foto offline im Browser speichern und spaeter synchronisieren
- Button **Grenzstein hier speichern**
- Punkte manuell per Karte oder Koordinatenformular anlegen
- Punkte bearbeiten, verschieben und loeschen
- mehrere Fotos pro Grenzstein speichern, z. B. Detail- und Umgebungsfotos
- optionale Flurstuecksgrenzen aus lokalen ALKIS-Shape-ZIPs anzeigen
- lokale SQLite-Datenbank unter `./data/app.sqlite`
- Export als GeoJSON, CSV und GPX
- Docker Compose mit FastAPI-App und Caddy HTTPS-Proxy
- Caddy holt Zertifikate per DuckDNS DNS-01 Challenge

## Start mit Docker Compose

1. Konfiguration anlegen:

```bash
cp .env.example .env
```

2. `.env` bearbeiten:

```env
APP_DOMAIN=deinname.duckdns.org
DUCKDNS_TOKEN=dein-duckdns-token
```

3. Container starten:

```bash
docker compose up -d --build
```

4. App im VPN/Netz aufrufen:

```text
https://deinname.duckdns.org
```

## Lokaler HTTP-Test ohne Caddy

Fuer Entwicklung oder einen schnellen Test kann die App direkt laufen:

```bash
DB_PATH=./data/app.sqlite uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Dann aufrufen:

```text
http://localhost:8000
```

Hinweis: Handy-GPS funktioniert in vielen Browsern nur ueber HTTPS. Fuer den echten Handy-Einsatz ist daher Caddy/HTTPS sinnvoll.

## Offline-Erfassung auf dem Handy

Die App kann auf dem Handy installiert werden, z. B. in Chrome/Android ueber `Zum Startbildschirm hinzufuegen` oder `App installieren`. Dafuer muss sie mindestens einmal mit erreichbarem Server und HTTPS geoeffnet worden sein.

Offline funktioniert:

- App starten, wenn sie vorher gecacht wurde
- GPS-Position ermitteln
- neuen Punkt speichern
- optional ein Foto direkt im Formular `Foto fuer neuen Punkt` miterfassen

Neue Punkte werden immer zuerst in einer lokalen IndexedDB-Warteschlange gespeichert. Danach versucht die App automatisch zu synchronisieren; alternativ kann `Sync` gedrueckt werden. Wenn der Server, das VPN oder das Internet nicht erreichbar ist, bleiben die Eintraege lokal erhalten. Der Status steht in der Toolbar, z. B. `Offline: 2 warten` oder `2 warten auf Sync`.

Einschraenkungen:

- Kartenkacheln sind offline nur sichtbar, wenn sie vorher im Browsercache lagen.
- Bearbeiten oder Loeschen bestehender Server-Punkte ist offline nicht synchronisiert.
- Browserdaten loeschen entfernt auch noch nicht synchronisierte Offline-Eintraege.

## OpenStreetMap-Tile-Proxy

Die Karte laedt OSM-Tiles nicht direkt von `tile.openstreetmap.org`, sondern ueber den App-Server:

```text
GET /osm/<z>/<x>/<y>.png
```

Der Server verwendet zwei getrennte Speicherorte:

- `./data/osm-cache`: normaler 24h-Cache fuer angesehene Tiles
- `./tiles/osm`: dauerhafte punktnahe Tiles fuer langfristige Aufbewahrung

Wenn ein neuer Punkt erfolgreich auf dem Server angelegt wurde, speichert die App automatisch einen kleinen Umkreis dauerhaft in `./tiles/osm`. Standardwerte:

- Radius: `250m`
- Zooms: `16-19`

Konfigurierbar per Environment:

```env
OSM_CACHE_TTL_SECONDS=86400
OSM_PREFETCH_RADIUS_M=250
OSM_PREFETCH_ZOOMS=16-19
OSM_DOWNLOAD_TIMEOUT=20
OSM_TILE_URL=https://tile.openstreetmap.org/{z}/{x}/{y}.png
OSM_TILE_USER_AGENT=gps-location-storage/1.0
```

Bei einem Tile-Request prueft der Server zuerst `./tiles/osm`, dann den frischen 24h-Cache. Wenn OSM nicht erreichbar ist, aber ein altes Cache-Tile vorhanden ist, wird das alte Tile trotzdem ausgeliefert.

Hinweis: Die dauerhaften Tiles in `./tiles` sollten in Backups aufgenommen werden. Der 24h-Cache unter `./data/osm-cache` ist dagegen nur ein technischer Cache.

## Daten

Die SQLite-Datei liegt standardmaessig hier:

```text
./data/app.sqlite
```

Fotos liegen unter:

```text
./data/photos/<stone_id>/
```

Dauerhaft gespeicherte Kartenkacheln liegen unter:

```text
./tiles/osm/<z>/<x>/<y>.png
```

Gespeichert werden:

- Name
- Breite und Laenge in WGS84
- GPS-Genauigkeit in Metern
- Quelle, z. B. `phone_gps` oder `manual`
- Notiz
- Erstell- und Aenderungszeitpunkt

Fotos werden als Anhaenge zum Grenzstein gespeichert. Die Grenzstein-Koordinate bleibt unveraendert, auch wenn das Foto aus einigen Metern Entfernung aufgenommen wird.

## Flurstuecksgrenzen

Optional koennen ALKIS-Shape-ZIPs in `./shapes` abgelegt werden:

```text
shapes/
  ALKIS_4444-002_shp.zip
  ALKIS_4444-003_shp.zip
```

Der Ordner `shapes/` wird nicht versioniert und beim Containerstart read-only nach `/shapes` gemountet.

Quelle Thueringen:

```text
https://geoportal.thueringen.de/gdi-th/download-offene-geodaten/download-liegschaftskataster
```

Format fuer Thueringen:

```text
Shape / SHP als ZIP
```

Beim Start der App werden alle `*.zip` in `/shapes` gelesen. Aus jeder ZIP wird der Layer `*_Flurstueck.shp` gesucht, nach WGS84 transformiert und in SQLite-Tabellen neu geladen. Vorher werden nur die Grenzlinien-Tabellen geleert; gespeicherte Grenzsteine und Fotos bleiben erhalten.

Wenn keine ZIPs vorhanden sind, laeuft die App normal ohne Flurstuecksgrenzen.

ZIP austauschen oder entfernen:

```bash
docker compose restart app
```

oder lokal:

```bash
make restart
```

Status pruefen:

```text
GET /api/boundaries/status
```

Grenzen fuer den sichtbaren Kartenausschnitt:

```text
GET /api/boundaries?bbox=minLon,minLat,maxLon,maxLat
```

## Raster-Quellen

Raster-/Metalink-Quellen werden in `./rasters` abgelegt:

```text
rasters/
  bayern-parzellarkarte.meta4
```

Der Dateiname ohne `.meta4` ist der Import-Key. Im Beispiel ist der Key:

```text
bayern-parzellarkarte
```

Quelle Bayern ALKIS-Parzellarkarte:

```text
https://geodaten.bayern.de/opengeodata/OpenDataDetail.html?pn=parzellarkarte&active=MASSENDOWNLOAD
```

Beim Containerstart passiert automatisch:

- alle `rasters/*.meta4` werden gelesen
- der SHA256-Hash wird mit dem letzten erfolgreichen Import verglichen
- unveraenderte Quellen werden uebersprungen
- neue/geaenderte Quellen werden synchron heruntergeladen
- bei Downloadfehlern bleiben vorhandene lokale Daten erhalten
- Warnungen und Fortschritt erscheinen in `docker logs`
- die Umwandlung zu Kartenkacheln laeuft danach asynchron im Hintergrund mit GDAL

Die App nutzt fertige lokale Tiles automatisch als Kartenoverlay. Wenn Kachelung fehlschlaegt, bleiben alte funktionierende Tiles erhalten, sofern vorhanden.

Status pruefen:

```text
GET /api/rasters/status
```

Docker-Logs anzeigen:

```bash
docker logs -f <container-name>
```

Lokal mit Makefile:

```bash
make logs
```

## Exporte

- `GET /api/export.geojson`
- `GET /api/export.csv`
- `GET /api/export.gpx`

## DuckDNS und VPN

Die App muss nicht oeffentlich erreichbar sein. Caddy nutzt DNS-01 und setzt fuer Let's Encrypt nur temporaer TXT-Records per DuckDNS-API.

Wenn deine Firewall nur VPN-Zugriff auf Port 443 erlaubt, bleibt die App praktisch intern. Der DNS-A-Record darf trotzdem auf deine erreichbare IP zeigen, wenn dich das nicht stoert.

## Katasterdaten

Flurstuecksgrenzen und Flurstuecksnummern sind in Version 1 bewusst nicht enthalten. Sie koennen spaeter als optionale WMS/WMTS-Layer ergaenzt werden, falls oeffentlich nutzbare Dienste fuer Bayern/Thueringen gefunden werden.
