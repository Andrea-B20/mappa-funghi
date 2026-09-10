"""
Scarica condizioni meteo recenti (pioggia, temperatura, evapotraspirazione,
umidità aria, umidità e temperatura del suolo), quota e copertura forestale
per una griglia di punti sull'Italia, e calcola:

  - weather_score: quanto le condizioni meteo attuali favoriscono la crescita
    (pioggia recente, umidità aria/suolo)
  - habitat_score: quanto il luogo è fisicamente adatto (quota + presenza di
    bosco), indipendentemente dal meteo del momento

La pioggia non viene da un modello solo
---------------------------------------
È il dato su cui tutto il resto poggia, ed era anche il meno affidabile.
Verificato sui 256 pluviometri della rete lombarda (15.454 coppie
stazione-giorno): il modello singolo che si usava prima dava per piovosi il
12.8% di giorni in cui a terra non era caduto nulla, e il 25% di acqua in
più del vero. Sulla griglia da 0.5° i falsi positivi salivano al 17.6%.

Adesso la pioggia passa per tre correzioni, in quest'ordine:

  1. tre modelli di tre centri meteorologici diversi invece di uno,
     mediati e ridotti da quanti sono d'accordo (blend_model_precip)
  2. dove esistono, i pluviometri veri delle reti regionali al posto del
     calcolo (rain_gauges.py, apply_gauges)
  3. una griglia più fitta per la pioggia, 0.3° invece di 0.5°, perché
     metà dell'errore era semplicemente la distanza fra il punto misurato
     e quello guardato

Sulla pioggia dei 7 giorni l'errore medio scende da 18.2 mm a 7.4 dove
arrivano i pluviometri e a 11.7 dove non arrivano; i falsi positivi dal
9.1% al 2.1%. Ogni cella porta con sé la provenienza (rain_source) così la
mappa può dire quale dei due casi sta mostrando.

La griglia viene ritagliata sul confine reale dell'Italia (da Nominatim/OSM,
vedi fetch_italy_boundary.py) così i punti in mare o in paesi confinanti
vengono esclusi a monte, invece di comparire come falsi "punti caldi".

Uso:
    .venv/bin/python scripts/fetch_italy_boundary.py   # una tantum / se il confine manca
    .venv/bin/python scripts/rain_gauges.py            # pioggia misurata (prima di questo)
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

import rain_gauges
from rain_gauges import FORECAST_URL, apply_gauges, blend_model_precip, fetch_precip_models_batch

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

# Passo della griglia. Era 0.5° — 55 km di lato, 133 celle per tutta
# l'Italia, cioè un punto di misura ogni 3000 km². Con una cella così
# grande la pioggia di un temporale caduto sull'altro versante della
# provincia veniva attribuita a tutta la zona: verificato sui 256
# pluviometri lombardi, sulla pioggia dei 7 giorni la griglia da 0.5°
# sbagliava in media 18.2 mm e dava per piovosa una zona asciutta nel
# 9.1% dei casi, mentre gli stessi dati presi nel punto esatto della
# stazione sbagliavano 15.3 mm e il 4.7%. Quasi metà dell'errore era
# solo distanza.
# Il guadagno però si esaurisce presto: misurato a 0.5°, 0.25° e 0.1°, i
# falsi positivi scendono dal 17.6% al 13.7% passando a 0.25° e solo al
# 13.1% arrivando a 0.1°. Quasi tutto il recuperabile sta nel primo
# dimezzamento; il resto si paga e non si incassa.
#
# E si paga davvero: Open-Meteo conta le richieste a variabile × giorno ×
# punto, e il piano gratuito taglia a 5.000 l'ora e 10.000 al giorno.
# Verificato che a 0.25° (790 celle) l'aggiornamento sfonda il limite
# orario e resta appeso ad aspettare; a 0.3° ci sta dentro in una volta
# sola, lasciando margine per i click degli utenti e per le notifiche, che
# pescano dalla stessa quota.
# 0.3° (33 km) porta la griglia da 133 a 366 celle di terraferma: quasi
# tre volte più fitta, con quasi tutto il guadagno possibile e un file che
# resta statico e leggero (26 KB compressi).
LAT_MIN, LAT_MAX, LAT_STEP = 36.0, 47.2, 0.3
LON_MIN, LON_MAX, LON_STEP = 6.5, 18.8, 0.3

# le celle il cui centro cade fino a questa distanza (in gradi, ~ km/111) fuori
# dal confine vengono comunque incluse: senza un piccolo margine, la
# semplificazione del poligono perderebbe punti costieri legittimi. Un
# margine troppo largo però non è solo un problema di mare aperto nei golfi
# (quello lo scarta comunque il controllo di quota reale, elevation_m <= 0
# più sotto): vicino ai confini terrestri include anche territorio straniero
# vero — verificato che 0.03° faceva rientrare celle sulla griglia in
# Svizzera (Lugano/Ticino), Francia (val di Susa) e Slovenia, tutte a quota
# positiva quindi non intercettate dal controllo sul mare. Un margine
# minimo tiene comunque i punti costieri (il controllo su elevation_m fa il
# resto) senza sconfinare
COASTAL_BUFFER_DEG = 0.002

PAST_DAYS = 16
BATCH_SIZE = 25

# Solo la PIOGGIA sta sulla griglia fitta. Temperatura, evapotraspirazione,
# umidità dell'aria e acqua nel terreno restano sulla griglia larga di
# prima. Non è un compromesso di comodo: la pioggia è l'unico campo che
# cambia bruscamente nel giro di pochi chilometri — un temporale bagna una
# valle e non quella accanto — mentre gli altri variano con continuità su
# decine di chilometri. Ed è la pioggia il dato che risultava sbagliato.
#
# Chiedere tutto fitto non è un'opzione: Open-Meteo conta le richieste a
# variabile × giorno × punto, e la griglia fitta con tutte le variabili su
# 17 giorni esauriva il limite orario gratuito a metà Italia (429 al
# batch 11 su 50, verificato). Spendere il budget sulla pioggia e lasciare
# il resto dov'era significa migliorare quello che serve senza peggiorare
# nulla: il contesto è esattamente quello di prima.
CONTEXT_STEP = 0.5

# Dei dati orari servono solo l'ultimo valore e il minimo di umidità delle
# ultime 72 ore (vedi compute_weather): quattro giorni bastano e costano un
# quarto di diciassette.
HOURLY_PAST_DAYS = 3

# Umidità del suolo: lo strato 3-9cm è dove sta il feltro miceliale dei
# funghi ectomicorrizici, insieme alle radici fini dell'albero simbionte.
# Prima si usava 0-1cm, cioè il primo centimetro di lettiera: si asciuga in
# poche ore, quindi raccontava il tempo di ieri pomeriggio e non lo stato
# del terreno. Misurato su 28 punti italiani: 0-1cm sta sistematicamente il
# 70-80% sotto lo strato del micelio (0.107 contro 0.185 in Appennino).
# 9-27cm è la riserva profonda, che tiene l'acqua molto più a lungo: entra
# nel punteggio con peso minore perché è il serbatoio, non la zona attiva.
SOIL_MAT_VAR = "soil_moisture_3_to_9cm"
SOIL_RESERVE_VAR = "soil_moisture_9_to_27cm"

# Estremi fisici del suolo, non numeri arbitrari: sotto il punto di
# appassimento l'acqua è trattenuta troppo forte per essere disponibile,
# sopra la capacità di campo il resto drena via. Verificato che i valori
# reali italiani (min 0.09, mediana 0.17, max 0.28 su 28 punti a settembre)
# cadono dentro questa scala invece di schiacciarsi nel quarto basso come
# succedeva con la vecchia normalizzazione 0.05-0.40 tarata su 0-1cm.
SOIL_WILTING_POINT = 0.10
SOIL_FIELD_CAPACITY = 0.32

BOUNDARY_PATH = Path(__file__).resolve().parent.parent / "data" / "italy_boundary.geojson"
OUT_PATH = Path(__file__).resolve().parent.parent / "web" / "data" / "weather_grid.geojson"

# Quota e tipo di bosco non cambiano da un giorno all'altro, ma con ~370
# celle costerebbero ogni volta 22 richieste di quota e 370 di Corine
# (che risponde un punto alla volta: venti minuti buoni). Vengono quindi
# messi da parte una volta e riletti; a ogni esecuzione si interrogano
# solo le celle nuove. Se la cache manca, la prima esecuzione la ricrea.
TERRAIN_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "terrain_cache.json"


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


def build_grid(polygon, step=None):
    step = step or LAT_STEP
    prepared = prep(polygon)
    grid = []
    for lat in frange(LAT_MIN, LAT_MAX, step):
        for lon in frange(LON_MIN, LON_MAX, step):
            if prepared.contains(Point(lon, lat)):
                grid.append((lat, lon))
    return grid


def nearest_payload(lat, lon, by_point):
    """Il punto della griglia larga più vicino a questa cella."""
    best = None
    best_d2 = None
    for (plat, plon), payload in by_point.items():
        d2 = (plat - lat) ** 2 + ((plon - lon) * math.cos(math.radians(lat))) ** 2
        if best_d2 is None or d2 < best_d2:
            best, best_d2 = payload, d2
    return best


def with_retries(fn, *args, retries=5, base_delay=2, **kwargs):
    """Riprova, ma con il 429 aspetta sul serio.

    Open-Meteo limita le richieste al minuto oltre che al giorno: quando
    risponde 429 non è un errore di rete da ritentare fra due secondi, è
    "hai finito il minuto". Ritentare subito consuma solo altri tentativi e
    lascia buchi nella griglia — verificato, con la vecchia attesa da 2-6
    secondi la metà meridionale dell'Italia restava senza dati. Qui si
    rispetta Retry-After quando c'è e si aspetta un minuto quando non c'è.
    """
    last_err = None
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except requests.RequestException as e:
            last_err = e
            if attempt >= retries - 1:
                break
            resp = getattr(e, "response", None)
            if resp is not None and resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = 60
                print(f"  Limite di richieste raggiunto, aspetto {wait:.0f}s...")
                time.sleep(wait)
            else:
                time.sleep(base_delay * (attempt + 1))
    raise last_err


def fetch_context_batch(points):
    """Tutto tranne la pioggia, sulla griglia larga.

    Temperatura ed evapotraspirazione servono giorno per giorno (le usa la
    finestra di incubazione di ogni specie); umidità e suolo solo come
    ultimo valore e minimo delle ultime 72 ore, quindi bastano quattro
    giorni. Due richieste invece di una: mescolarle costringerebbe a
    scaricare anche le orarie su 17 giorni, cioè quattro volte il
    necessario.
    """
    lats = ",".join(str(p[0]) for p in points)
    lons = ",".join(str(p[1]) for p in points)
    common = {"latitude": lats, "longitude": lons, "forecast_days": 1, "timezone": "auto"}

    daily_resp = requests.get(
        FORECAST_URL,
        params={**common, "daily": "temperature_2m_mean,et0_fao_evapotranspiration",
                "past_days": PAST_DAYS},
        timeout=90,
    )
    daily_resp.raise_for_status()
    hourly_resp = requests.get(
        FORECAST_URL,
        params={**common,
                "hourly": f"relative_humidity_2m,{SOIL_MAT_VAR},{SOIL_RESERVE_VAR},soil_temperature_6cm",
                "past_days": HOURLY_PAST_DAYS},
        timeout=90,
    )
    hourly_resp.raise_for_status()

    daily = daily_resp.json()
    hourly = hourly_resp.json()
    daily = daily if isinstance(daily, list) else [daily]
    hourly = hourly if isinstance(hourly, list) else [hourly]
    return [{"daily": d.get("daily", {}), "hourly": h.get("hourly", {})}
            for d, h in zip(daily, hourly)]


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


def key_of(point):
    """Chiave testuale stabile per la cache: le tuple non stanno nel JSON."""
    return f"{point[0]:.4f},{point[1]:.4f}"


def load_terrain_cache():
    if not TERRAIN_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(TERRAIN_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_terrain_cache(cache, grid):
    """Salva solo le celle della griglia attuale.

    Cambiare LAT_STEP sposta tutte le chiavi: senza questa potatura il file
    accumulerebbe per sempre le celle di ogni passo mai usato, che nessuno
    rileggerà più.
    """
    keep = {key_of(p) for p in grid}
    TERRAIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TERRAIN_CACHE_PATH.write_text(
        json.dumps({k: v for k, v in cache.items() if k in keep}), encoding="utf-8"
    )


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


def soil_water_index(soil_moisture):
    """Da m3/m3 a 0-1 fra punto di appassimento e capacita di campo."""
    if soil_moisture is None:
        return None
    return clamp01((soil_moisture - SOIL_WILTING_POINT) / (SOIL_FIELD_CAPACITY - SOIL_WILTING_POINT))


def compute_weather(dates, precip, context):
    """dates/precip: la pioggia di QUESTA cella, corretta (multi-modello +
    pluviometri) — l'unico dato che viene dalla griglia fitta.

    context: temperatura, evapotraspirazione, umidità e suolo dal punto
    più vicino della griglia larga (vedi CONTEXT_STEP), come prima.
    """
    daily = context.get("daily", {})
    temp_mean = daily.get("temperature_2m_mean", [])
    et0 = daily.get("et0_fao_evapotranspiration", [])

    last_rain_date = None
    days_since_rain = None
    today = date.fromisoformat(dates[-1]) if dates else date.today()
    for d_str, mm in reversed(list(zip(dates, precip))):
        if mm is not None and mm >= 1.0:
            last_rain_date = d_str
            days_since_rain = (today - date.fromisoformat(d_str)).days
            break

    rain_7d_mm = sum(mm for mm in precip[-7:] if mm is not None)
    # bilancio idrico: la pioggia che resta nel terreno e quella caduta meno
    # quella che l'atmosfera ha gia ripreso. 30mm seguiti da tre giorni di
    # scirocco non sono 30mm seguiti da tre giorni coperti — con ET0 mediana
    # italiana di 4.6 mm/giorno, una settimana di sole si mangia 32mm
    et0_7d_mm = sum(v for v in et0[-7:] if v is not None)

    hourly = context.get("hourly", {})

    def last_valid(key):
        series = [v for v in hourly.get(key, []) if v is not None]
        return series[-1] if series else None

    humidity_series = [h for h in hourly.get("relative_humidity_2m", []) if h is not None]
    humidity_pct = humidity_series[-1] if humidity_series else None
    # la crescita dei carpofori si ferma quando l'umidita relativa MINIMA
    # scende sotto il 40% (letteratura su Boletus edulis): e il minimo a
    # contare, non il valore del momento in cui abbiamo scaricato i dati
    humidity_min_pct = min(humidity_series[-72:]) if humidity_series else None

    soil_moisture = last_valid(SOIL_MAT_VAR)
    soil_moisture_deep = last_valid(SOIL_RESERVE_VAR)
    soil_temp_c = last_valid("soil_temperature_6cm")

    # zona attiva (feltro miceliale) pesata piu della riserva profonda
    mat_w = soil_water_index(soil_moisture)
    reserve_w = soil_water_index(soil_moisture_deep)
    if mat_w is None:
        soil_w = reserve_w if reserve_w is not None else 0.0
    elif reserve_w is None:
        soil_w = mat_w
    else:
        soil_w = 0.65 * mat_w + 0.35 * reserve_w

    recency = recency_weight(days_since_rain)
    amount_w = clamp01(rain_7d_mm / 30.0)
    humidity_w = clamp01(((humidity_pct or 0) - 40) / (95 - 40))

    weather_score = clamp01(0.40 * recency + 0.25 * amount_w + 0.20 * soil_w + 0.15 * humidity_w)

    return {
        "last_rain_date": last_rain_date,
        "days_since_rain": days_since_rain,
        "rain_7d_mm": round(rain_7d_mm, 1),
        "et0_7d_mm": round(et0_7d_mm, 1),
        "humidity_pct": round(humidity_pct, 0) if humidity_pct is not None else None,
        "humidity_min_pct": round(humidity_min_pct, 0) if humidity_min_pct is not None else None,
        # "soil_moisture" resta la chiave principale ma ora e lo strato del
        # micelio (3-9cm), non piu il primo centimetro di lettiera
        "soil_moisture": round(soil_moisture, 3) if soil_moisture is not None else None,
        "soil_moisture_deep": round(soil_moisture_deep, 3) if soil_moisture_deep is not None else None,
        "soil_temp_c": round(soil_temp_c, 1) if soil_temp_c is not None else None,
        "weather_score": round(weather_score, 3),
        # serie giornaliere grezze: il frontend le usa per calcolare la
        # "finestra di incubazione" specie per specie (ogni fungo ha una
        # soglia di pioggia, una temperatura ottimale e un ritardo di
        # fruttificazione diversi — vedi SPECIES_RAIN_PROFILE in web/app.js).
        # Servono giorno per giorno, non come medie: la temperatura che conta
        # e quella DEI GIORNI di incubazione di quella specie, non quella di
        # oggi ne la media dei 17 giorni
        "daily_dates": dates,
        "daily_precip_mm": [round(mm, 1) if mm is not None else 0.0 for mm in precip],
        "daily_temp_mean_c": [round(t, 1) if t is not None else None for t in temp_mean],
        "daily_et0_mm": [round(v, 1) if v is not None else 0.0 for v in et0],
    }

def main():
    print("Carico il confine dell'Italia...")
    polygon = load_italy_polygon()

    grid = build_grid(polygon)
    print(f"Griglia (solo terraferma italiana): {len(grid)} punti ({LAT_STEP}° di passo)")

    gauges = rain_gauges.load_field()
    if len(gauges):
        print(f"Pluviometri reali in archivio: {len(gauges)}")
    else:
        print("Nessun pluviometro in archivio (esegui scripts/rain_gauges.py): "
              "la pioggia resterà quella dei modelli")

    # Temperatura, evapotraspirazione, umidità e acqua nel terreno: sulla
    # griglia larga, come prima. Variano con continuità su decine di
    # chilometri, quindi il punto più vicino racconta la stessa storia
    context = build_grid(polygon, CONTEXT_STEP)
    print(f"Contesto (temperatura, umidità, suolo): {len(context)} punti "
          f"({CONTEXT_STEP}° di passo)")
    context_by_point = {}
    n_ctx = (len(context) - 1) // BATCH_SIZE + 1
    for i in range(0, len(context), BATCH_SIZE):
        batch = context[i : i + BATCH_SIZE]
        print(f"Contesto: batch {i // BATCH_SIZE + 1}/{n_ctx} ({len(batch)} punti)...")
        try:
            results = with_retries(fetch_context_batch, batch)
        except requests.RequestException as e:
            print(f"  Errore batch contesto (dopo retry): {e}, salto.")
            continue
        for point, payload in zip(batch, results):
            context_by_point[point] = payload
        time.sleep(0.5)
    if not context_by_point:
        raise SystemExit("Nessun dato di contesto scaricato: interrompo senza riscrivere il file.")

    weather_by_point = {}
    rain_meta_by_point = {}
    n_batches = (len(grid) - 1) // BATCH_SIZE + 1
    for i in range(0, len(grid), BATCH_SIZE):
        batch = grid[i : i + BATCH_SIZE]
        print(f"Pioggia: batch {i // BATCH_SIZE + 1}/{n_batches} ({len(batch)} punti)...")
        try:
            model_precip = with_retries(fetch_precip_models_batch, batch, PAST_DAYS)
        except requests.RequestException as e:
            print(f"  Errore batch pioggia (dopo retry): {e}, salto.")
            continue
        for (lat, lon), models in zip(batch, model_precip):
            dates = models["dates"]
            if not dates:
                continue
            base = blend_model_precip(models["series"], len(dates))
            precip, meta = apply_gauges(lat, lon, dates, base, gauges)
            ctx = nearest_payload(lat, lon, context_by_point)
            weather_by_point[(lat, lon)] = compute_weather(dates, precip, ctx)
            rain_meta_by_point[(lat, lon)] = meta
        time.sleep(0.5)

    terrain = load_terrain_cache()
    missing_elev = [p for p in grid if key_of(p) not in terrain]
    if missing_elev:
        print(f"Quota: {len(missing_elev)} celle nuove da scaricare "
              f"({len(grid) - len(missing_elev)} già in cache)")
    for i in range(0, len(missing_elev), BATCH_SIZE):
        batch = missing_elev[i : i + BATCH_SIZE]
        try:
            elevations = with_retries(fetch_elevation_batch, batch)
        except requests.RequestException as e:
            print(f"  Errore batch quota (dopo retry): {e}, salto.")
            continue
        for point, elev in zip(batch, elevations):
            terrain[key_of(point)] = {"elevation_m": elev}
        time.sleep(0.3)

    elevation_by_point = {p: terrain.get(key_of(p), {}).get("elevation_m") for p in grid}

    # solo i punti di terraferma: interrogare Corine anche per le celle di
    # mare residuo nel buffer costiero (scartate poco sotto) sarebbe tempo
    # sprecato — l'endpoint Corine risponde un punto alla volta, non a lotti
    land_points = [(lat, lon) for lat, lon in grid if not (elevation_by_point.get((lat, lon)) is not None and elevation_by_point[(lat, lon)] <= 0)]

    needs_veg = [p for p in land_points if "veg_class" not in terrain.get(key_of(p), {})]
    print(f"Vegetazione (Corine Land Cover): {len(needs_veg)} celle nuove "
          f"({len(land_points) - len(needs_veg)} già in cache), una richiesta alla volta...")
    for idx, (lat, lon) in enumerate(needs_veg, 1):
        try:
            code = with_retries(fetch_clc_code, lat, lon)
        except requests.RequestException as e:
            print(f"  Errore Corine per ({lat}, {lon}) dopo retry: {e}, salto (nessun bosco).")
            code = None
        terrain.setdefault(key_of((lat, lon)), {})["veg_class"] = CLC_VEG_CLASS.get(code, "none")
        if idx % 50 == 0 or idx == len(needs_veg):
            print(f"  {idx}/{len(needs_veg)}...")
        time.sleep(0.15)
    save_terrain_cache(terrain, grid)

    veg_class_by_point = {p: terrain.get(key_of(p), {}).get("veg_class", "none") for p in land_points}

    features = []
    skipped_sea = 0
    skipped_no_weather = 0
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

        # Una cella il cui batch di pioggia è fallito non ha serie
        # giornaliere: scritta lo stesso comparirebbe sulla mappa come una
        # zona senza una goccia d'acqua, che è peggio che non comparire
        weather = weather_by_point.get((lat, lon))
        if not weather:
            skipped_no_weather += 1
            continue

        veg_class = veg_class_by_point.get((lat, lon), "none")
        veg_score = veg_class_score(veg_class)
        elev_score = elevation_suitability(elevation_m)
        habitat_score = round(clamp01(elev_score * veg_score), 3)

        properties = {
            "lat": lat,
            "lon": lon,
            # da dove viene la pioggia di questa cella: "pluviometri" =
            # misurata, "misto" = misurata solo nei giorni recenti,
            # "modello" = calcolata. Il popup lo dice all'utente
            **rain_meta_by_point.get((lat, lon), {"rain_source": "modello", "rain_gauge_count": 0, "rain_gauge_km": None}),
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
    if skipped_no_weather:
        print(f"Celle scartate perché il meteo non è arrivato: {skipped_no_weather}")
    print(f"File: {OUT_PATH}")


if __name__ == "__main__":
    main()
