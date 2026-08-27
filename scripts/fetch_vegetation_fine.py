"""
Vegetazione e quota REALI (Corine Land Cover + Open-Meteo) alla risoluzione
FINE (0.15°, la stessa griglia usata dal frontend per raggruppare i
ritrovamenti storici — vedi FINE_GRID_STEP_DEG in web/app.js), calcolate
solo per le celle che contengono almeno un ritrovamento storico reale
(GBIF/iNaturalist).

Perché serve, oltre a weather_grid.geojson: quella griglia è a 0.5°
(~55km di passo), e per attribuire vegetazione/quota a un ritrovamento
storico il frontend prendeva la cella meteo più vicina — che poteva distare
fino a 30-40km. In montagna la vegetazione cambia bruscamente nel giro di
poche centinaia di metri, quindi quella distanza produceva attribuzioni
sbagliate: un ritrovamento reale di fondovalle poteva ereditare "nessun
bosco" dalla vetta alpina più vicina sulla griglia. Verificato: il 76% dei
3839 ritrovamenti storici finiva così su una cella senza vegetazione
significativa — in netto contrasto con l'evidenza (un fungo trovato implica
quasi certamente un albero vicino).

Una prima versione campionava Corine al centro geometrico di ogni cella
fine (0.15°, ~16km): meglio, ma ancora un punto astratto che può cadere
fuori da un poligono di bosco reale semplicemente perché il bosco non
copre l'intera cella. Questa versione campiona Corine direttamente sui
PUNTI DI RITROVAMENTO reali (arrotondati a ~1km per non rifare la stessa
richiesta più volte), e classifica ogni cella in base a cosa mostrano i
suoi punti reali: se anche solo uno dei ritrovamenti in quella cella cade
su un poligono di bosco, la cella riflette quel tipo — "nessun bosco"
resta riservato ai casi in cui davvero NESSUNO dei punti reali lo mostra.

Uso:
    .venv/bin/python scripts/fetch_occurrences.py       # se occurrences.geojson manca
    .venv/bin/python scripts/fetch_vegetation_fine.py
"""

import json
import math
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests

from fetch_weather_grid import (
    CLC_VEG_CLASS,
    elevation_suitability,
    fetch_clc_code,
    veg_class_score,
    with_retries,
)

FINE_GRID_STEP_DEG = 0.15
POINT_ROUND_DEG = 0.01  # ~1.1km: abbastanza fine da non perdere boschetti piccoli, senza rifare la stessa richiesta per punti quasi identici
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
BATCH_SIZE = 25

OCC_PATH = Path(__file__).resolve().parent.parent / "web" / "data" / "occurrences.geojson"
OUT_PATH = Path(__file__).resolve().parent.parent / "web" / "data" / "vegetation_fine.geojson"


def clamp01(x):
    return max(0.0, min(1.0, x))


def fine_cell_key(lat, lon):
    # stessa identica aritmetica di fineCellCenter() in web/app.js: le
    # chiavi devono combaciare esattamente per il lookup lato frontend
    lat_idx = math.floor(lat / FINE_GRID_STEP_DEG)
    lon_idx = math.floor(lon / FINE_GRID_STEP_DEG)
    return f"{lat_idx}_{lon_idx}", (lat_idx + 0.5) * FINE_GRID_STEP_DEG, (lon_idx + 0.5) * FINE_GRID_STEP_DEG


def fetch_elevation_batch(points):
    lats = ",".join(str(p[0]) for p in points)
    lons = ",".join(str(p[1]) for p in points)
    resp = requests.get(ELEVATION_URL, params={"latitude": lats, "longitude": lons}, timeout=90)
    resp.raise_for_status()
    return resp.json().get("elevation", [None] * len(points))


def main():
    if not OCC_PATH.exists():
        raise SystemExit(
            f"Ritrovamenti storici mancanti ({OCC_PATH}). Esegui prima: "
            ".venv/bin/python scripts/fetch_occurrences.py"
        )
    occurrences = json.loads(OCC_PATH.read_text(encoding="utf-8"))["features"]

    # raggruppa i ritrovamenti per cella fine, e dentro ogni cella per
    # punto reale arrotondato (più ritrovamenti vicinissimi tra loro
    # condividono un solo campione Corine)
    cells = {}  # key -> {"center": (lat, lon), "points": set of (round_lat, round_lon)}
    for f in occurrences:
        lon, lat = f["geometry"]["coordinates"]
        key, clat, clon = fine_cell_key(lat, lon)
        cell = cells.setdefault(key, {"center": (clat, clon), "points": set()})
        cell["points"].add((round(lat, 2), round(lon, 2)))

    all_points = sorted({p for c in cells.values() for p in c["points"]})
    print(f"Celle fini (0.15°) con ritrovamenti storici: {len(cells)}")
    print(f"Punti reali distinti da campionare (~1km): {len(all_points)}")

    # quota: un campione al centro geometrico di ogni cella basta — non
    # cambia in modo così brusco e localizzato quanto la presenza di bosco
    elevation_by_key = {}
    cell_items = list(cells.items())
    for i in range(0, len(cell_items), BATCH_SIZE):
        batch = cell_items[i : i + BATCH_SIZE]
        coords = [v["center"] for _, v in batch]
        print(f"Quota: batch {i // BATCH_SIZE + 1}/{(len(cell_items) - 1) // BATCH_SIZE + 1} ({len(batch)} celle)...")
        try:
            elevations = with_retries(fetch_elevation_batch, coords)
        except requests.RequestException as e:
            print(f"  Errore batch quota (dopo retry): {e}, salto.")
            elevations = [None] * len(batch)
        for (key, _), elev in zip(batch, elevations):
            elevation_by_key[key] = elev
        time.sleep(0.3)

    veg_by_point = {}
    print(f"Vegetazione (Corine Land Cover): {len(all_points)} punti reali, uno alla volta...")
    for idx, (lat, lon) in enumerate(all_points, 1):
        try:
            code = with_retries(fetch_clc_code, lat, lon)
        except requests.RequestException as e:
            print(f"  Errore Corine per ({lat}, {lon}) dopo retry: {e}, salto (nessun bosco).")
            code = None
        veg_by_point[(lat, lon)] = CLC_VEG_CLASS.get(code, "none")
        if idx % 100 == 0 or idx == len(all_points):
            print(f"  {idx}/{len(all_points)}...")
        time.sleep(0.12)

    features = []
    forest_cells = 0
    for key, info in cells.items():
        classes = [veg_by_point[p] for p in info["points"]]
        forest_classes = [c for c in classes if c != "none"]
        if forest_classes:
            veg_class = Counter(forest_classes).most_common(1)[0][0]
            forest_cells += 1
        else:
            veg_class = "none"

        elevation_m = elevation_by_key.get(key)
        habitat_score = round(clamp01(elevation_suitability(elevation_m) * veg_class_score(veg_class)), 3)
        clat, clon = info["center"]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [clon, clat]},
                "properties": {
                    "cell_key": key,
                    "elevation_m": round(elevation_m, 0) if elevation_m is not None else None,
                    "veg_class": veg_class,
                    "habitat_score": habitat_score,
                    "veg_sample_count": len(classes),
                },
            }
        )

    geojson = {
        "type": "FeatureCollection",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "grid_step_deg": FINE_GRID_STEP_DEG,
        "point_round_deg": POINT_ROUND_DEG,
        "features": features,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(geojson), encoding="utf-8")
    print(f"\nCelle scritte: {len(features)}")
    print(f"Di cui con bosco rilevato in almeno un punto reale: {forest_cells} ({100*forest_cells/len(features):.0f}%)")
    print(f"File: {OUT_PATH}")


if __name__ == "__main__":
    main()
