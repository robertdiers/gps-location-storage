# Shape-Quellen

Dieser Ordner ist fuer ALKIS-Shape-ZIPs mit echten Flurstuecksgeometrien.

Die App liest beim Start alle `*.zip` in diesem Ordner und sucht darin nach
`*_Flurstueck.shp`. Diese Geometrien werden in die lokale SQLite-Datenbank
geladen und als Flurstuecksgrenzen angezeigt.

## Thueringen

Quelle:

```text
https://geoportal.thueringen.de/gdi-th/download-offene-geodaten/download-liegschaftskataster
```

Empfohlenes Format:

```text
Shape / SHP als ZIP
```

Beispiel:

```text
ALKIS_4444-002_shp.zip
```
