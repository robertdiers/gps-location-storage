const DEFAULT_CENTER = [50.8, 11.2];

let stones = [];
let markers = new Map();
let offlineMarkers = new Map();
let selectedMarker = null;
let currentPosition = null;
let currentPositionMarker = null;
let boundariesAvailable = false;
let rasterLayers = [];
let pendingEntries = [];
let syncInProgress = false;

const hasLeaflet = typeof L !== "undefined";
const map = hasLeaflet ? L.map("map").setView(DEFAULT_CENTER, 8) : createMapFallback();
if (hasLeaflet) {
  L.tileLayer("/osm/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
}
const boundariesLayer = hasLeaflet ? L.geoJSON(null, {
  style: {
    color: "#ffcc33",
    weight: 2,
    opacity: 0.95,
    fillOpacity: 0,
  },
  interactive: false,
}).addTo(map) : { clearLayers() {}, addData() {} };

const form = document.querySelector("#stone-form");
const photoForm = document.querySelector("#photo-form");
const photosSection = document.querySelector("#photos-section");
const photoList = document.querySelector("#photo-list");
const photoFile = document.querySelector("#photo-file");
const photoCaption = document.querySelector("#photo-caption");
const newPhotoFile = document.querySelector("#new-photo-file");
const newPhotoCaption = document.querySelector("#new-photo-caption");
const boundariesToggle = document.querySelector("#boundaries-toggle");
const syncStatus = document.querySelector("#sync-status");
const fields = {
  id: document.querySelector("#stone-id"),
  name: document.querySelector("#name"),
  latitude: document.querySelector("#latitude"),
  longitude: document.querySelector("#longitude"),
  accuracy_m: document.querySelector("#accuracy_m"),
  source: document.querySelector("#source"),
  notes: document.querySelector("#notes"),
};

document.querySelector("#locate-button").addEventListener("click", locate);
document.querySelector("#save-here-button").addEventListener("click", saveHere);
document.querySelector("#new-button").addEventListener("click", clearForm);
document.querySelector("#delete-button").addEventListener("click", deleteSelected);
document.querySelector("#sync-button").addEventListener("click", () => syncPendingEntries(true));
form.addEventListener("submit", saveForm);
photoForm.addEventListener("submit", uploadPhoto);
boundariesToggle.addEventListener("change", () => {
  if (boundariesToggle.checked) loadBoundaries();
  else boundariesLayer.clearLayers();
});
if (hasLeaflet) {
  map.on("moveend", () => {
    if (boundariesToggle.checked) loadBoundaries();
  });
  map.on("click", (event) => fillCoordinates(event.latlng.lat, event.latlng.lng, null, "manual"));
}

loadStones();
initBoundaries();
initRasters();
initOffline();
locate(false).catch(() => {});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch((error) => console.warn("Service worker registration failed", error));
}

window.addEventListener("online", () => syncPendingEntries(false));
window.addEventListener("offline", updateSyncStatus);

async function loadStones() {
  try {
    const response = await fetch("/api/stones");
    if (!response.ok) throw new Error(await response.text());
    stones = await response.json();
    renderMarkers();
    renderList();
  } catch (error) {
    console.warn("Stones could not be loaded", error);
    renderMarkers();
    renderList();
  }
}

function renderMarkers() {
  for (const marker of markers.values()) marker.remove();
  for (const marker of offlineMarkers.values()) marker.remove();
  markers.clear();
  offlineMarkers.clear();
  if (!hasLeaflet) return;

  for (const stone of stones) {
    const marker = L.marker([stone.latitude, stone.longitude], { draggable: true }).addTo(map);
    marker.bindPopup(createPopupContent(stone));
    marker.on("click", () => selectStone(stone.id));
    marker.on("dragend", () => {
      const pos = marker.getLatLng();
      selectStone(stone.id);
      fillCoordinates(pos.lat, pos.lng, stone.accuracy_m, stone.source);
    });
    markers.set(stone.id, marker);
  }

  for (const entry of pendingEntries) {
    const marker = L.circleMarker([entry.payload.latitude, entry.payload.longitude], {
      radius: 8,
      color: "#8a6a00",
      fillColor: "#f0c13d",
      fillOpacity: 0.9,
    }).addTo(map);
    marker.bindPopup(`Wartet auf Sync<br>${entry.payload.latitude.toFixed(6)}, ${entry.payload.longitude.toFixed(6)}`);
    offlineMarkers.set(entry.id, marker);
  }
}

function renderList() {
  const list = document.querySelector("#stone-list");
  list.innerHTML = "";
  if (!stones.length && !pendingEntries.length) {
    list.textContent = "Noch keine Punkte gespeichert.";
    return;
  }
  for (const entry of pendingEntries) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "stone-item is-pending";
    item.innerHTML = `<strong>${escapeHtml(entry.payload.name || "Offline-Punkt")}</strong><span>Wartet auf Sync · ${entry.payload.latitude.toFixed(6)}, ${entry.payload.longitude.toFixed(6)}</span>`;
    item.addEventListener("click", () => {
      map.setView([entry.payload.latitude, entry.payload.longitude], Math.max(map.getZoom(), 17));
      offlineMarkers.get(entry.id)?.openPopup();
    });
    list.appendChild(item);
  }
  for (const stone of stones) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "stone-item";
    item.innerHTML = `<strong>${escapeHtml(stone.name || `Grenzstein ${stone.id}`)}</strong><span>${stone.latitude.toFixed(6)}, ${stone.longitude.toFixed(6)}</span>`;
    item.addEventListener("click", () => {
      selectStone(stone.id);
      map.setView([stone.latitude, stone.longitude], Math.max(map.getZoom(), 17));
      markers.get(stone.id)?.openPopup();
    });
    list.appendChild(item);
  }
}

function createPopupContent(stone) {
  const container = document.createElement("div");
  const title = escapeHtml(stone.name || `Grenzstein ${stone.id}`);
  const acc = stone.accuracy_m == null ? "" : `<br>Genauigkeit: ${Number(stone.accuracy_m).toFixed(1)} m`;
  container.innerHTML = `<strong>${title}</strong><br>${stone.latitude.toFixed(6)}, ${stone.longitude.toFixed(6)}${acc}`;

  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Bearbeiten";
  button.addEventListener("click", () => {
    selectStone(stone.id, true);
  });
  container.appendChild(button);
  return container;
}

function selectStone(id, scrollToForm = false) {
  const stone = stones.find((entry) => entry.id === id);
  if (!stone) return;
  fields.id.value = stone.id;
  fields.name.value = stone.name || "";
  fields.latitude.value = stone.latitude;
  fields.longitude.value = stone.longitude;
  fields.accuracy_m.value = stone.accuracy_m ?? "";
  fields.source.value = stone.source || "manual";
  fields.notes.value = stone.notes || "";
  photosSection.hidden = false;
  loadPhotos(stone.id);
  if (scrollToForm) {
    form.scrollIntoView({ behavior: "smooth", block: "start" });
    form.classList.add("is-highlighted");
    setTimeout(() => form.classList.remove("is-highlighted"), 1400);
  }
}

function clearForm() {
  fields.id.value = "";
  fields.name.value = "";
  fields.latitude.value = "";
  fields.longitude.value = "";
  fields.accuracy_m.value = "";
  fields.source.value = "manual";
  fields.notes.value = "";
  photosSection.hidden = true;
  photoList.innerHTML = "";
  photoFile.value = "";
  photoCaption.value = "";
  newPhotoFile.value = "";
  newPhotoCaption.value = "";
}

function fillCoordinates(lat, lng, accuracy, source) {
  fields.latitude.value = Number(lat).toFixed(7);
  fields.longitude.value = Number(lng).toFixed(7);
  fields.accuracy_m.value = accuracy == null ? "" : Number(accuracy).toFixed(1);
  fields.source.value = source;
}

function locate(zoom = true) {
  const status = document.querySelector("#gps-status");
  if (!navigator.geolocation) {
    status.textContent = "GPS im Browser nicht verfuegbar.";
    return Promise.reject(new Error("GPS im Browser nicht verfuegbar."));
  }
  status.textContent = "Position wird ermittelt ...";
  return new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(
    (position) => {
      currentPosition = position;
      const { latitude, longitude, accuracy } = position.coords;
      status.textContent = `GPS: ${latitude.toFixed(6)}, ${longitude.toFixed(6)} (${Math.round(accuracy)} m)`;
      fillCoordinates(latitude, longitude, accuracy, "phone_gps");
      if (hasLeaflet) {
        if (currentPositionMarker) currentPositionMarker.remove();
        currentPositionMarker = L.circleMarker([latitude, longitude], {
          radius: 8,
          color: "#164cc2",
          fillColor: "#2d75ff",
          fillOpacity: 0.8,
        }).addTo(map).bindPopup("Aktuelle Position");
        if (zoom) map.setView([latitude, longitude], Math.max(map.getZoom(), 17));
      }
      resolve(position);
    },
    (error) => {
      status.textContent = `GPS nicht verfuegbar: ${error.message}`;
      reject(error);
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 },
  ));
}

async function saveHere() {
  if (!currentPosition) {
    try {
      await locate(true);
    } catch (_error) {
      alert("Keine GPS-Position vorhanden. Bitte GPS freigeben oder auf die Karte tippen.");
      return;
    }
  }
  if (!fields.latitude.value || !fields.longitude.value) {
    alert("Keine GPS-Position vorhanden. Bitte 'Aktuelle Position' verwenden oder auf die Karte tippen.");
    return;
  }
  fields.id.value = "";
  if (!fields.name.value) fields.name.value = `Grenzstein ${new Date().toLocaleString("de-DE")}`;
  await persist("POST", "/api/stones");
}

async function saveForm(event) {
  event.preventDefault();
  const id = fields.id.value;
  await persist(id ? "PUT" : "POST", id ? `/api/stones/${id}` : "/api/stones");
}

async function persist(method, url) {
  const payload = {
    name: fields.name.value,
    latitude: Number(fields.latitude.value),
    longitude: Number(fields.longitude.value),
    accuracy_m: fields.accuracy_m.value === "" ? null : Number(fields.accuracy_m.value),
    source: fields.source.value || "manual",
    notes: fields.notes.value,
  };
  if (method === "POST") {
    try {
      await enqueueOfflineEntry(payload);
      clearForm();
      await refreshPendingEntries();
      setTimeout(() => syncPendingEntries(false), 500);
    } catch (error) {
      console.warn("Offline queue failed", error);
      alert("Der Punkt konnte nicht lokal gespeichert werden. Bitte pruefen, ob Browser-Speicher erlaubt ist.");
    }
    return;
  }

  try {
    const response = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      alert(`Speichern fehlgeschlagen: ${await response.text()}`);
      return;
    }
    const saved = await response.json();
    await loadStones();
    selectStone(saved.id);
    map.setView([saved.latitude, saved.longitude], Math.max(map.getZoom(), 17));
  } catch (error) {
    console.warn("Update failed", error);
    alert("Speichern fehlgeschlagen. Aenderungen an bestehenden Punkten koennen nur online gespeichert werden.");
  }
}

async function deleteSelected() {
  const id = fields.id.value;
  if (!id) return;
  if (!confirm("Diesen Grenzstein loeschen?")) return;
  const response = await fetch(`/api/stones/${id}`, { method: "DELETE" });
  if (!response.ok) {
    alert(`Loeschen fehlgeschlagen: ${await response.text()}`);
    return;
  }
  clearForm();
  await loadStones();
}

async function loadPhotos(stoneId) {
  photoList.textContent = "Fotos werden geladen ...";
  const response = await fetch(`/api/stones/${stoneId}/photos`);
  if (!response.ok) {
    photoList.textContent = "Fotos konnten nicht geladen werden.";
    return;
  }
  const photos = await response.json();
  photoList.innerHTML = "";
  if (!photos.length) {
    photoList.textContent = "Noch keine Fotos gespeichert.";
    return;
  }

  for (const photo of photos) {
    const card = document.createElement("article");
    card.className = "photo-card";

    const image = document.createElement("img");
    image.src = `/api/photos/${photo.id}`;
    image.alt = photo.caption || photo.original_filename || "Grenzstein-Foto";
    card.appendChild(image);

    if (photo.caption) {
      const caption = document.createElement("p");
      caption.textContent = photo.caption;
      card.appendChild(caption);
    }

    const actions = document.createElement("div");
    actions.className = "photo-actions";

    const open = document.createElement("a");
    open.className = "button secondary";
    open.href = `/api/photos/${photo.id}`;
    open.target = "_blank";
    open.rel = "noopener";
    open.textContent = "Oeffnen";

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger";
    remove.textContent = "Loeschen";
    remove.addEventListener("click", () => deletePhoto(photo.id));

    actions.append(open, remove);
    card.appendChild(actions);
    photoList.appendChild(card);
  }
}

async function uploadPhoto(event) {
  event.preventDefault();
  const stoneId = fields.id.value;
  if (!stoneId) {
    alert("Bitte zuerst einen Grenzstein speichern oder auswaehlen.");
    return;
  }
  if (!photoFile.files.length) return;

  const data = new FormData();
  data.append("photo", photoFile.files[0]);
  data.append("caption", photoCaption.value);

  const response = await fetch(`/api/stones/${stoneId}/photos`, { method: "POST", body: data });
  if (!response.ok) {
    alert(`Foto-Upload fehlgeschlagen: ${await response.text()}`);
    return;
  }
  photoFile.value = "";
  photoCaption.value = "";
  await loadPhotos(stoneId);
}

async function deletePhoto(photoId) {
  if (!confirm("Dieses Foto loeschen?")) return;
  const response = await fetch(`/api/photos/${photoId}`, { method: "DELETE" });
  if (!response.ok) {
    alert(`Foto konnte nicht geloescht werden: ${await response.text()}`);
    return;
  }
  await loadPhotos(fields.id.value);
}

async function initBoundaries() {
  const response = await fetch("/api/boundaries/status");
  if (!response.ok) return;
  const status = await response.json();
  boundariesAvailable = status.count > 0;
  boundariesToggle.disabled = !boundariesAvailable;
  if (!boundariesAvailable) {
    boundariesToggle.checked = false;
    boundariesToggle.closest("label").title = "Keine Shape-ZIPs in /shapes geladen.";
    return;
  }
  boundariesToggle.checked = true;
  loadBoundaries();
}

async function loadBoundaries() {
  if (!boundariesAvailable || !hasLeaflet) return;
  const bounds = map.getBounds();
  const bbox = [
    bounds.getWest(),
    bounds.getSouth(),
    bounds.getEast(),
    bounds.getNorth(),
  ].map((value) => value.toFixed(7)).join(",");
  const response = await fetch(`/api/boundaries?bbox=${bbox}`);
  if (!response.ok) return;
  const data = await response.json();
  boundariesLayer.clearLayers();
  boundariesLayer.addData(data);
}

async function initRasters() {
  if (!hasLeaflet) return;
  const response = await fetch("/api/rasters/status");
  if (!response.ok) return;
  const rasters = await response.json();
  for (const raster of rasters) {
    if (raster.tile_status !== "ready") {
      if (raster.source_status === "downloaded" || raster.tile_status === "tiling" || raster.tile_status === "failed_tiles") {
        console.warn(`Raster ${raster.key}: ${raster.tile_message || raster.source_message}`);
      }
      continue;
    }
    const layer = L.tileLayer(`/tiles/${encodeURIComponent(raster.key)}/{z}/{x}/{y}.png`, {
      maxZoom: 22,
      opacity: 0.85,
      attribution: raster.key,
    }).addTo(map);
    rasterLayers.push(layer);
  }
}

async function initOffline() {
  await refreshPendingEntries();
  syncPendingEntries(false);
}

async function enqueueOfflineEntry(payload) {
  const file = newPhotoFile.files[0] || null;
  await idbPut({
    id: crypto.randomUUID(),
    payload,
    photo: file,
    photoName: file?.name || "",
    photoType: file?.type || "",
    photoCaption: newPhotoCaption.value,
    createdAt: new Date().toISOString(),
  });
}

async function refreshPendingEntries() {
  pendingEntries = await idbAll();
  renderMarkers();
  renderList();
  updateSyncStatus();
}

async function syncPendingEntries(showResult) {
  if (syncInProgress) return;
  syncInProgress = true;
  updateSyncStatus();
  try {
    const entries = await idbAll();
    for (const entry of entries) {
      try {
        let stoneId = entry.serverStoneId;
        if (!stoneId) {
          const response = await fetch("/api/stones", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(entry.payload),
          });
          if (!response.ok) throw new Error(await response.text());
          const saved = await response.json();
          stoneId = saved.id;
        }
        if (entry.photo) {
          await idbPut({ ...entry, serverStoneId: stoneId });
          const data = new FormData();
          data.append("photo", entry.photo, entry.photoName || "foto.jpg");
          data.append("caption", entry.photoCaption || "");
          const photoResponse = await fetch(`/api/stones/${stoneId}/photos`, { method: "POST", body: data });
          if (!photoResponse.ok) {
            await idbPut({ ...entry, serverStoneId: stoneId });
            throw new Error(await photoResponse.text());
          }
        }
        await idbDelete(entry.id);
      } catch (error) {
        console.warn("Sync failed", error);
        break;
      }
    }
    await refreshPendingEntries();
    await loadStones();
    if (showResult) {
      alert(pendingEntries.length ? "Sync nicht vollstaendig. Einige Eintraege warten weiter." : "Alle Offline-Eintraege sind synchronisiert.");
    }
  } finally {
    syncInProgress = false;
    updateSyncStatus();
  }
}

function updateSyncStatus() {
  const pending = pendingEntries.length;
  if (syncInProgress) {
    syncStatus.textContent = `Sync laeuft (${pending})`;
    syncStatus.className = "sync-status is-syncing";
  } else if (pending) {
    syncStatus.textContent = navigator.onLine ? `${pending} warten auf Sync` : `Offline: ${pending} warten`;
    syncStatus.className = "sync-status is-pending";
  } else if (navigator.onLine) {
    syncStatus.textContent = "Online";
    syncStatus.className = "sync-status";
  } else {
    syncStatus.textContent = "Offline";
    syncStatus.className = "sync-status is-offline";
  }
}

function createMapFallback() {
  const mapElement = document.querySelector("#map");
  mapElement.classList.add("map-offline");
  mapElement.textContent = "Karte offline nicht verfuegbar. GPS-Erfassung und Foto-Speicherung funktionieren weiterhin.";
  return {
    setView() { return this; },
    getZoom() { return 17; },
    getBounds() {
      return {
        getWest: () => 0,
        getSouth: () => 0,
        getEast: () => 0,
        getNorth: () => 0,
      };
    },
    on() {},
  };
}

function idbOpen() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("gps-location", 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore("pending", { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function idbPut(entry) {
  const database = await idbOpen();
  return idbRequest(database.transaction("pending", "readwrite").objectStore("pending").put(entry), database);
}

async function idbDelete(id) {
  const database = await idbOpen();
  return idbRequest(database.transaction("pending", "readwrite").objectStore("pending").delete(id), database);
}

async function idbAll() {
  const database = await idbOpen();
  return idbRequest(database.transaction("pending", "readonly").objectStore("pending").getAll(), database);
}

function idbRequest(request, database) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => {
      database.close();
      resolve(request.result);
    };
    request.onerror = () => {
      database.close();
      reject(request.error);
    };
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[char]));
}
