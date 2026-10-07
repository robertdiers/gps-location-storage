# Dauerhafte Kartentiles

Dieser Ordner wird als `/tiles` in den App-Container gemountet.

Hier speichert die App punktnahe OpenStreetMap-Tiles dauerhaft unter:

```text
tiles/osm/<z>/<x>/<y>.png
```

Diese Dateien sind kein 24h-Cache. Sie sind fuer den langfristigen Erhalt kleiner Kartenbereiche rund um gespeicherte Punkte gedacht und sollten bei Backups mitgesichert werden.
