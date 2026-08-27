"""
Scarica condizioni meteo recenti (pioggia, umidità aria, umidità del suolo),
quota e copertura forestale per una griglia di punti sull'Italia, e calcola:

  - weather_score: quanto le condizioni meteo attuali favoriscono la crescita
    (pioggia recente, umidità aria/suolo)
  - habitat_score: quanto il luogo è fisicamente adatto (quota + presenza di
    bosco), indipendentemente dal meteo del momento

La griglia viene ritagliata sul confine reale dell'Italia (da Nominatim/OSM,
vedi fetch_italy_boundary.py) così i punti in mare o in paesi confinanti
vengono esclusi a monte, invece di comparire come falsi "punti caldi".

Uso:
    .venv/bin/python scripts/fetch_italy_boundary.py   # una tantum / se il confine manca
    .venv/bin/python scripts/fetch_weather_grid.py
"""

import json
import math
import time
from datetime import date, datetime
from pathlib import Path

import requests
from shapely.geometry import Point, shape
from shapely.prepared import prep

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

LAT_MIN, LAT_MAX, LAT_STEP = 36.0, 47.2, 0.5
LON_MIN, LON_MAX, LON_STEP = 6.5, 18.8, 0.5

# le celle il cui centro cade fino a questa distanza (in gradi, ~ km/111) fuori
# dal confine vengono comunque incluse: senza un piccolo margine, la
# semplificazione del poligono perderebbe punti costieri legittimi. Un
# margine troppo largo però include ampie porzioni di mare aperto nei golfi
# (Taranto, Genova, ecc.), quindi lo teniamo piccolo e affidiamo il filtro
# fine al controllo di quota reale (vedi elevation_m <= 0 più sotto)
COASTAL_BUFFER_DEG = 0.03

PAST_DAYS = 16
BATCH_SIZE = 25

BOUNDARY_PATH = Path(__file__).resolve().parent.parent / "data" / "italy_boundary.geojson"
OUT_PATH = Path(__file__).resolve().parent.parent / "web" / "data" / "weather_grid.geojson"


def frange(start, stop, step):
    vals = []
    v = start
    while v <= stop + 1e-9:
        vals.append(round(v, 4))
        v += step
    return vals


def load_italy_polygon():
    if not BOUNDARY_PATH.exists():
        raise SystemExit(
            f"Confine Italia mancante ({BOUNDARY_PATH}). "
            "Esegui prima: .venv/bin/python scripts/fetch_italy_boundary.py"
        )
    geometry = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
    polygon = shape(geometry).simplify(0.01, preserve_topology=True)
    return polygon.buffer(COASTAL_BUFFER_DEG)


def build_grid(polygon):
    prepared = prep(polygon)
    grid = []
    for lat in frange(LAT_MIN, LAT_MAX, LAT_STEP):
        for lon in frange(LON_MIN, LON_MAX, LON_STEP):
            if prepared.contains(Point(lon, lat)):
                grid.append((lat, lon))
    return grid


def with_retries(fn, *args, retries=3, base_delay=2, **kwargs):
    last_err = None
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except requests.RequestException as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(base_delay * (attempt + 1))
    raise last_err


def fetch_weather_batch(points):
    lats = ",".join(str(p[0]) for p in points)
    lons = ",".join(str(p[1]) for p in points)
    params = {
        "latitude": lats,
        "longitude": lons,
        "daily": "precipitation_sum",
        "hourly": "relative_humidity_2m,soil_moisture_0_to_1cm",
        "past_days": PAST_DAYS,
        "forecast_days": 1,
        "timezone": "auto",
    }
    resp = requests.get(FORECAST_URL, params=params, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        data = [data]
    return data


def fetch_elevation_batch(points):
    lats = ",".join(str(p[0]) for p in points)
    lons = ",".join(str(p[1]) for p in points)
    resp = requests.get(ELEVATION_URL, params={"latitude": lats, "longitude": lons}, timeout=90)
    resp.raise_for_status()
    return resp.json().get("elevation", [None] * len(points))


# Copertura del suolo REALE da Corine Land Cover 2018 (Copernicus/EEA),
# interrogato punto per punto via il servizio ArcGIS REST pubblico
# dell'agenzia. Sostituisce la stima precedente basata solo su quota e
# latitudine: distingueva "zona boschiva sì/no" per fasce geografiche note,
# non il tipo di bosco cella per cella. Overpass/OSM è stato scartato come
# fonte perché il tag che distingue conifere da latifoglie (leaf_type)
# copre solo ~11% dei poligoni di bosco italiani (verificato campionando
# Toscana/Emilia) — troppo incompleto per filtrare per specie. Corine ha
# invece copertura completa, benché a risoluzione più bassa (unità minima
# cartografabile 25 ettari) e senza distinguere la specie esatta di albero.
CLC_URL = "https://image.discomap.eea.europa.eu/arcgis/rest/services/Corine/CLC2018_WM/MapServer/identify"

# codici Corine Land Cover rilevanti per la presenza di un vero bosco/margine
# boschivo; tutto il resto (agricolo, urbano, acqua, roccia/pascolo nudo...)
# non lo è e resta "none"
CLC_VEG_CLASS = {
    "311": "broadleaf",
    "312": "conifer",
    "313": "mixed",
    "324": "shrub",
}


def clamp01(x):
    return max(0.0, min(1.0, x))


def latlon_to_webmercator(lat, lon):
    x = lon * 20037508.34 / 180
    y = math.log(math.tan((90 + lat) * math.pi / 360)) / (math.pi / 180) * 20037508.34 / 180
    return x, y


def fetch_clc_code(lat, lon):
    x, y = latlon_to_webmercator(lat, lon)
    params = {
        "geometry": json.dumps({"x": x, "y": y, "spatialReference": {"wkid": 3857}}),
        "geometryType": "esriGeometryPoint",
        "sr": 3857,
        "layers": "all",
        "tolerance": 1,
        "mapExtent": "0,0,10,10",
        "imageDisplay": "10,10,96",
        "returnGeometry": "false",
        "f": "json",
    }
    resp = requests.get(CLC_URL, params=params, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    # preferiamo il layer vettoriale (poligoni, più preciso); il layer
    # raster fa da riserva se per qualche motivo il primo non risponde
    vector_code, raster_code = None, None
    for r in results:
        attrs = r.get("attributes", {})
        if r.get("layerId") == 0 and attrs.get("Code_18"):
            vector_code = attrs["Code_18"]
        elif r.get("layerId") == 1 and attrs.get("Raster.CODE_18"):
            raster_code = attrs["Raster.CODE_18"]
    return vector_code or raster_code


def veg_class_score(veg_class):
    return {"broadleaf": 1.0, "conifer": 1.0, "mixed": 1.0, "shrub": 0.5}.get(veg_class, 0.1)


def recency_weight(days_since_rain):
    if days_since_rain is None:
        return 0.05
    if days_since_rain <= 2:
        return 1.0
    if days_since_rain <= 4:
        return 0.8
    if days_since_rain <= 7:
        return 0.5
    if days_since_rain <= 10:
        return 0.3
    if days_since_rain <= 14:
        return 0.15
    return 0.05


def elevation_suitability(elevation_m):
    """Curva approssimativa di idoneità altimetrica per porcini/ovoli/gallinacci."""
    if elevation_m is None:
        return 0.6
    if elevation_m < 0:
        return 0.1  # sotto il livello del mare: quasi certamente errore/costa artificiale
    if elevation_m <= 1400:
        return 1.0 if elevation_m >= 50 else 0.4 + 0.6 * (elevation_m / 50)
    if elevation_m <= 1800:
        return 1.0 - 0.7 * ((elevation_m - 1400) / 400)
    return 0.15


def compute_weather(lat, lon, payload):
    daily = payload.get("daily", {})
    dates = daily.get("time", [])
    precip = daily.get("precipitation_sum", [])

    last_rain_date = None
    days_since_rain = None
    today = date.fromisoformat(dates[-1]) if dates else date.today()
    for d_str, mm in reversed(list(zip(dates, precip))):
        if mm is not None and mm >= 1.0:
            last_rain_date = d_str
            days_since_rain = (today - date.fromisoformat(d_str)).days
            break

    rain_7d_mm = sum(mm for mm in precip[-7:] if mm is not None)

    hourly = payload.get("hourly", {})
    humidity_series = [h for h in hourly.get("relative_humidity_2m", []) if h is not None]
    soil_series = [s for s in hourly.get("soil_moisture_0_to_1cm", []) if s is not None]
    humidity_pct = humidity_series[-1] if humidity_series else None
    soil_moisture = soil_series[-1] if soil_series else None

    recency = recency_weight(days_since_rain)
    amount_w = clamp01(rain_7d_mm / 30.0)
    soil_w = clamp01((soil_moisture - 0.05) / (0.40 - 0.05)) if soil_moisture is not None else 0.0
    humidity_w = clamp01(((humidity_pct or 0) - 40) / (95 - 40))

    weather_score = clamp01(0.40 * recency + 0.25 * amount_w + 0.20 * soil_w + 0.15 * humidity_w)

    return {
        "last_rain_date": last_rain_date,
        "days_since_rain": days_since_rain,
        "rain_7d_mm": round(rain_7d_mm, 1),
        "humidity_pct": round(humidity_pct, 0) if humidity_pct is not None else None,
        "soil_moisture": round(soil_moisture, 3) if soil_moisture is not None else None,
        "weather_score": round(weather_score, 3),
        # serie giornaliera grezza: il frontend la usa per calcolare la
        # "finestra di incubazione" specie per specie (ogni fungo ha una
        # soglia di pioggia e un ritardo di fruttificazione diversi — vedi
        # SPECIES_RAIN_PROFILE in web/app.js)
        "daily_dates": dates,
        "daily_precip_mm": [round(mm, 1) if mm is not None else 0.0 for mm in precip],
    }


def main():
    print("Carico il confine dell'Italia...")
    polygon = load_italy_polygon()

    grid = build_grid(polygon)
    print(f"Griglia (solo terraferma italiana): {len(grid)} punti ({LAT_STEP}° di passo)")

    weather_by_point = {}
    for i in range(0, len(grid), BATCH_SIZE):
        batch = grid[i : i + BATCH_SIZE]
        print(f"Meteo: batch {i // BATCH_SIZE + 1}/{(len(grid) - 1) // BATCH_SIZE + 1} ({len(batch)} punti)...")
        try:
            results = with_retries(fetch_weather_batch, batch)
        except requests.RequestException as e:
            print(f"  Errore batch meteo (dopo retry): {e}, salto.")
            continue
        for (lat, lon), payload in zip(batch, results):
            weather_by_point[(lat, lon)] = compute_weather(lat, lon, payload)
        time.sleep(0.3)

    elevation_by_point = {}
    for i in range(0, len(grid), BATCH_SIZE):
        batch = grid[i : i + BATCH_SIZE]
        print(f"Quota: batch {i // BATCH_SIZE + 1}/{(len(grid) - 1) // BATCH_SIZE + 1} ({len(batch)} punti)...")
        try:
            elevations = with_retries(fetch_elevation_batch, batch)
        except requests.RequestException as e:
            print(f"  Errore batch quota (dopo retry): {e}, salto.")
            elevations = [None] * len(batch)
        for (lat, lon), elev in zip(batch, elevations):
            elevation_by_point[(lat, lon)] = elev
        time.sleep(0.3)

    # solo i punti di terraferma: interrogare Corine anche per le celle di
    # mare residuo nel buffer costiero (scartate poco sotto) sarebbe tempo
    # sprecato — l'endpoint Corine risponde un punto alla volta, non a lotti
    land_points = [(lat, lon) for lat, lon in grid if not (elevation_by_point.get((lat, lon)) is not None and elevation_by_point[(lat, lon)] <= 0)]

    veg_class_by_point = {}
    print(f"Vegetazione (Corine Land Cover): {len(land_points)} punti, una richiesta alla volta...")
    for idx, (lat, lon) in enumerate(land_points, 1):
        try:
            code = with_retries(fetch_clc_code, lat, lon)
        except requests.RequestException as e:
            print(f"  Errore Corine per ({lat}, {lon}) dopo retry: {e}, salto (nessun bosco).")
            code = None
        veg_class_by_point[(lat, lon)] = CLC_VEG_CLASS.get(code, "none")
        if idx % 50 == 0 or idx == len(land_points):
            print(f"  {idx}/{len(land_points)}...")
        time.sleep(0.15)

    features = []
    skipped_sea = 0
    for lat, lon in grid:
        elevation_m = elevation_by_point.get((lat, lon))
        # Il confine bufferizzato è un'approssimazione: nei golfi (Taranto,
        # Genova, ecc.) può ancora includere celle di mare aperto. La quota
        # reale (Open-Meteo/Copernicus DEM) è un secondo filtro affidabile —
        # la terraferma italiana non è mai esattamente a livello del mare.
        # Se la quota non è disponibile (fetch fallito anche dopo i retry) la
        # cella NON viene scartata come mare: sarebbe uno scarto ingiustificato
        # di terraferma valida solo per un errore di rete transitorio.
        if elevation_m is not None and elevation_m <= 0:
            skipped_sea += 1
            continue

        weather = weather_by_point.get((lat, lon), {})
        veg_class = veg_class_by_point.get((lat, lon), "none")
        veg_score = veg_class_score(veg_class)
        elev_score = elevation_suitability(elevation_m)
        habitat_score = round(clamp01(elev_score * veg_score), 3)

        properties = {
            "lat": lat,
            "lon": lon,
            "elevation_m": round(elevation_m, 0) if elevation_m is not None else None,
            "veg_class": veg_class,
            "vegetation_score": round(veg_score, 3),
            "habitat_score": habitat_score,
            **weather,
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": properties,
            }
        )

    geojson = {
        "type": "FeatureCollection",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        # dimensione della cella in gradi: il frontend la usa per disegnare
        # zone di raggio geografico fisso (in metri) invece di punti sfocati
        # in pixel, così non si "restringono" più zoomando
        "grid_step_deg": LAT_STEP,
        "features": features,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(geojson), encoding="utf-8")
    print(f"\nCelle scritte (solo terraferma): {len(features)}")
    print(f"Celle scartate perché a quota <= 0 (mare residuo nel buffer): {skipped_sea}")
    print(f"File: {OUT_PATH}")


if __name__ == "__main__":
    main()
