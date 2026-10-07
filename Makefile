IMAGE ?= gps-location-storage-app
CONTAINER ?= gps-location-storage-app-local
PORT ?= 8000
DATA_DIR ?= $(CURDIR)/data
SHAPES_DIR ?= $(CURDIR)/shapes
RASTERS_DIR ?= $(CURDIR)/rasters
TILES_DIR ?= $(CURDIR)/tiles
OSM_CACHE_TTL_SECONDS ?= 86400
OSM_PREFETCH_RADIUS_M ?= 250
OSM_PREFETCH_ZOOMS ?= 16-19
OSM_DOWNLOAD_TIMEOUT ?= 20
OSM_TILE_USER_AGENT ?= gps-location-storage/1.0

.PHONY: build up down restart logs test clean

build:
	docker build -t $(IMAGE) .

up: build
	mkdir -p $(DATA_DIR)
	mkdir -p $(SHAPES_DIR)
	mkdir -p $(RASTERS_DIR)
	mkdir -p $(TILES_DIR)
	docker rm -f $(CONTAINER) >/dev/null 2>&1 || true
	docker run -d \
		--name $(CONTAINER) \
		-p $(PORT):8000 \
		-e DB_PATH=/data/app.sqlite \
		-e SHAPES_DIR=/shapes \
		-e RASTERS_DIR=/rasters \
		-e TILES_DIR=/tiles \
		-e OSM_CACHE_TTL_SECONDS=$(OSM_CACHE_TTL_SECONDS) \
		-e OSM_PREFETCH_RADIUS_M=$(OSM_PREFETCH_RADIUS_M) \
		-e OSM_PREFETCH_ZOOMS=$(OSM_PREFETCH_ZOOMS) \
		-e OSM_DOWNLOAD_TIMEOUT=$(OSM_DOWNLOAD_TIMEOUT) \
		-e OSM_TILE_USER_AGENT=$(OSM_TILE_USER_AGENT) \
		-v $(DATA_DIR):/data \
		-v $(SHAPES_DIR):/shapes:ro \
		-v $(RASTERS_DIR):/rasters:ro \
		-v $(TILES_DIR):/tiles \
		$(IMAGE)
	@printf 'App läuft lokal auf http://localhost:%s\n' '$(PORT)'

down:
	docker rm -f $(CONTAINER) >/dev/null 2>&1 || true

restart: down up

logs:
	docker logs -f $(CONTAINER)

test:
	$(MAKE) up OSM_PREFETCH_ZOOMS=
	@for i in 1 2 3 4 5; do \
		curl -fs http://localhost:$(PORT)/api/stones >/dev/null 2>&1 && break; \
		sleep 1; \
	done
	curl -fsS http://localhost:$(PORT)/api/stones >/dev/null
	curl -fsS http://localhost:$(PORT)/api/boundaries/status >/dev/null
	curl -fsS http://localhost:$(PORT)/api/rasters/status >/dev/null
	@stone_id=$$(curl -fsS -X POST http://localhost:$(PORT)/api/stones \
		-H 'Content-Type: application/json' \
		-d '{"name":"Makefile Test","latitude":50.1,"longitude":11.2,"accuracy_m":5,"source":"test","notes":"temporary"}' \
		| python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'); \
		photo_id=$$(curl -fsS -X POST http://localhost:$(PORT)/api/stones/$$stone_id/photos \
			-F 'caption=Testfoto' \
			-F 'photo=@/etc/hosts;type=image/png;filename=test.png' \
			| python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'); \
		curl -fsS http://localhost:$(PORT)/api/stones/$$stone_id/photos >/dev/null; \
		curl -fsS http://localhost:$(PORT)/api/photos/$$photo_id >/dev/null; \
		curl -fsS -X DELETE http://localhost:$(PORT)/api/photos/$$photo_id >/dev/null; \
		curl -fsS -X DELETE http://localhost:$(PORT)/api/stones/$$stone_id >/dev/null
	@printf 'OK: http://localhost:%s/api/stones antwortet\n' '$(PORT)'

clean: down
	docker rmi $(IMAGE) >/dev/null 2>&1 || true
