# Raster-Quellen

Dieser Ordner ist fuer Raster-/Metalink-Quellen, z. B. die Bayern ALKIS-Parzellarkarte.

Lege Metalink-Dateien als `<key>.meta4` ab. Der Dateiname ohne `.meta4` ist der
Import-Key. Wenn die Datei beim Containerstart neu oder geaendert ist, laedt die
App die referenzierten Rasterdaten erneut herunter. Vorhandene erfolgreich
importierte lokale Daten bleiben erhalten, falls ein neuer Download fehlschlaegt.

## Bayern

Quelle:

```text
https://geodaten.bayern.de/opengeodata/OpenDataDetail.html?pn=parzellarkarte&active=MASSENDOWNLOAD
```

Empfohlenes Format:

```text
Metalink .meta4
```

Beispiel:

```text
bayern-parzellarkarte.meta4
```
