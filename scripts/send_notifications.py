"""
Due tipi di notifica push via OneSignal, entrambi calcolati dal MODELLO DEL
SITO (web/model.js via Node, non una copia in Python — vedi run_scorer()):
gira come job indipendente ("notify-rain") nell'Action giornaliera, non
dipende dalla griglia meteo precalcolata, interroga Open-Meteo per conto
suo.

1. ZONE DISEGNATE (esistenti): un poligono disegnato a mano libera sul sito
   (vedi setupRainZone in web/app.js); notifica quando piove sul centroide,
   nominando la specie più promettente.

2. VICINO A CASA (nuove): un indirizzo + un raggio in km + le specie scelte
   dall'utente; notifica quando il punteggio di PRONTEZZA (non solo la
   pioggia — l'intero modello: temperatura, stagione, bosco, suolo) supera
   la soglia "in arrivo" o "pronto" in un punto qualunque del raggio.
   A differenza delle zone, qui serve controllare un'AREA, non un solo
   punto: casa è quasi sempre in paese, i funghi no. Vedi ring_points().

Entrambi richiedono due secret GitHub Action:
  ONESIGNAL_APP_ID
  ONESIGNAL_REST_API_KEY   (REST API Key dell'app, non l'App ID)
Se mancano lo script esce subito senza fare nulla.

OneSignal non offre un endpoint per elencare direttamente tutti gli
iscritti: bisogna passare da un export CSV asincrono (POST
/players/csv_export, poi si scarica il file quando è pronto). È
l'approccio usato qui in fetch_subscribers().
"""

import csv
import gzip
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import requests

import rain_gauges
from rain_gauges import apply_gauges, blend_model_precip, fetch_precip_models_batch

ROOT = Path(__file__).resolve().parent.parent
SCORER = ROOT / "scripts" / "score_cases.js"
OCC_PATH = ROOT / "web" / "data" / "occurrences.geojson"
GRID_PATH = ROOT / "web" / "data" / "weather_grid.geojson"
FINE_PATH = ROOT / "web" / "data" / "vegetation_fine.geojson"
PH_PATH = ROOT / "web" / "data" / "soil_ph.json"
# stato "ultimo livello osservato" per utente+specie, per non rimandare la
# stessa notifica ogni giorno finché la prontezza resta alta (vedi più
# sotto). NON in web/data: quella cartella è pubblicata su GitHub Pages, e
# qui dentro finiscono identificativi di iscrizione — data/ a livello di
# repo non è servita, è lo stesso posto dove sta italy_boundary.geojson.
HOME_STATE_PATH = ROOT / "data" / "notify_home_state.json"
PH_KEY_ROUND_DEG = 0.01

# gli stessi giorni di storico che usa il popup (POPUP_RAIN_DAYS in
# web/model.js): la notifica deve giudicare sugli stessi dati del sito,
# altrimenti annuncia una cosa e chi apre la mappa ne legge un'altra
HISTORY_DAYS = 17

SPECIES_LABELS = {
    "porcino_comune": "porcini",
    "porcino_pini": "porcini dei pini",
    "ovolo": "ovoli",
    "gallinaccio": "gallinacci",
}

ONESIGNAL_APP_ID = os.environ.get("ONESIGNAL_APP_ID")
ONESIGNAL_REST_API_KEY = os.environ.get("ONESIGNAL_REST_API_KEY")

CSV_EXPORT_URL = "https://api.onesignal.com/players/csv_export"
NOTIFICATIONS_URL = "https://api.onesignal.com/notifications"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# il file compresso non è pronto subito: va richiesto e poi ripescato con
# qualche tentativo, l'URL risponde 404 finché la generazione non finisce
CSV_POLL_INTERVAL_S = 3
CSV_POLL_MAX_ATTEMPTS = 20

# sotto questa soglia (mm caduti nel giorno) non avvisiamo per le zone: una
# spruzzata non cambia le condizioni di raccolta e manderebbe solo
# notifiche inutili
MIN_MM_TO_NOTIFY = 3.0

# Oltre questo raggio "vicino a casa" smette di voler dire qualcosa (un
# giorno di funghi implica comunque uno spostamento, non un raggio
# potenzialmente più largo dell'Italia): il valore che l'utente ha scelto
# sul sito viene comunque tagliato qui, lato server, non ci si fida del
# solo controllo client. Deve restare uguale a MAX_HOME_RADIUS_KM in
# web/app.js.
MAX_RADIUS_KM = 100

# Campionamento a tre anelli concentrici — 19 punti SEMPRE, indipendenti dal
# raggio scelto: il costo per utente resta costante (una sola chiamata
# Open-Meteo batch, vedi fetch_home_weather), a peggiorare con raggi grandi
# è la risoluzione, non il conto. Non è un'integrazione sull'area — un vero
# campionamento denso costerebbe troppe chiamate per troppi utenti — è un
# controllo a campione onesto: casa, un anello a metà raggio, uno al bordo.
HOME_SAMPLE_RINGS = [
    (0.0, 1),
    (0.5, 6),
    (0.9, 12),
]

TIER_RANK = {"none": 0, "soon": 1, "ready": 2}


# Una notifica è la cosa peggiore su cui sbagliare pioggia: sveglia il
# telefono di qualcuno per annunciare un temporale che non c'è stato. Qui
# la pioggia passa dalle stesse due correzioni della mappa — media di
# tre modelli e, dove esistono, pluviometri veri — invece che dal
# singolo modello che dava il 12.8% di giorni piovosi a secco.
_GAUGES = None


def gauges():
    global _GAUGES
    if _GAUGES is None:
        _GAUGES = rain_gauges.load_field()
    return _GAUGES


def corrected_precip(lat, lon, dates, daily):
    """La pioggia giornaliera migliore disponibile per questo punto.

    Se la richiesta multi-modello non riesce si resta sul modello singolo
    del payload: una notifica in meno non vale il rischio di non mandarne
    nessuna perché una richiesta accessoria è andata storta.
    """
    precip = daily.get("precipitation_sum") or []
    try:
        models = fetch_precip_models_batch([(lat, lon)], HISTORY_DAYS)[0]
        if models["dates"] == dates:
            precip = blend_model_precip(models["series"], len(dates))
    except (requests.RequestException, IndexError, KeyError):
        pass
    return apply_gauges(lat, lon, dates, precip, gauges())[0]


def fetch_subscribers():
    """Ritorna [{subscription_ids, zones, home}] per ogni iscritto che ha
    almeno una zona (tag notify_zones) o una casa (tag notify_home)."""
    headers = {"Authorization": f"Key {ONESIGNAL_REST_API_KEY}", "Content-Type": "application/json"}
    resp = requests.post(
        CSV_EXPORT_URL, headers=headers, params={"app_id": ONESIGNAL_APP_ID}, json={}, timeout=30
    )
    resp.raise_for_status()
    csv_url = resp.json().get("csv_file_url")
    if not csv_url:
        print("OneSignal non ha restituito un URL di export", file=sys.stderr)
        return []

    csv_bytes = None
    for attempt in range(CSV_POLL_MAX_ATTEMPTS):
        dl = requests.get(csv_url, timeout=30)
        if dl.status_code == 200:
            csv_bytes = dl.content
            break
        print(f"export non ancora pronto (tentativo {attempt + 1}/{CSV_POLL_MAX_ATTEMPTS}), aspetto...")
        time.sleep(CSV_POLL_INTERVAL_S)
    if csv_bytes is None:
        print("timeout in attesa dell'export CSV di OneSignal", file=sys.stderr)
        return []

    rows = csv.DictReader(io.StringIO(gzip.decompress(csv_bytes).decode("utf-8")))

    subscribers = []
    for row in rows:
        raw_tags = row.get("tags")
        if not raw_tags:
            continue
        try:
            tags = json.loads(raw_tags) or {}
        except (TypeError, ValueError):
            continue

        zones = []
        raw_zones = tags.get("notify_zones")
        if raw_zones:
            try:
                zones = [
                    z for z in json.loads(raw_zones) if z.get("lat") is not None and z.get("lon") is not None
                ]
            except (TypeError, ValueError):
                zones = []

        home = None
        raw_home = tags.get("notify_home")
        if raw_home:
            try:
                h = json.loads(raw_home)
                species = [s for s in (h.get("species") or []) if s in SPECIES_LABELS]
                if h.get("lat") is not None and h.get("lon") is not None and h.get("radiusKm") and species:
                    home = {
                        "lat": float(h["lat"]),
                        "lon": float(h["lon"]),
                        "radiusKm": min(float(h["radiusKm"]), MAX_RADIUS_KM),
                        "species": species,
                    }
            except (TypeError, ValueError, KeyError):
                home = None

        # "id" nell'export /players/csv_export è lo stesso identificativo
        # accettato da include_subscription_ids nell'invio (vedi send_push)
        subscription_id = row.get("id")
        if (not zones and not home) or not subscription_id:
            continue
        subscribers.append({"subscription_ids": [subscription_id], "zones": zones, "home": home})
    return subscribers


def load_occurrences():
    try:
        return json.loads(OCC_PATH.read_text(encoding="utf-8"))["features"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"ritrovamenti non leggibili ({exc}): punteggi delle specie non disponibili", file=sys.stderr)
        return None


def run_scorer(cases, occ_features):
    """Passa i casi al modello vero (web/model.js via Node, tramite
    scripts/score_cases.js) e ritorna le righe di punteggio. Condiviso da
    zone e case: una riscrittura del modello in Python divergerebbe al
    primo ritocco, e la notifica finirebbe per dire una cosa diversa da
    quella che l'utente legge aprendo la mappa."""
    if not cases:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "cases.json"
        out_path = Path(tmp) / "scores.json"
        in_path.write_text(json.dumps({"trainOccurrences": occ_features, "cases": cases}), encoding="utf-8")
        try:
            subprocess.run(["node", str(SCORER), str(in_path), str(out_path)], check=True, capture_output=True)
            return json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            print(f"modello non eseguibile ({exc})", file=sys.stderr)
            return []


# ---------------------------------------------------------------------
# Zone disegnate: pioggia sul centroide
# ---------------------------------------------------------------------


def fetch_conditions(lat, lon):
    """Condizioni nel punto esatto della zona, con le stesse variabili che
    il popup del sito scarica al click (vedi onMapClick in web/app.js) — non
    la griglia grossolana in background.

    Oltre a pioggia e temperatura del giorno concluso, restituisce l'intero
    "env" che il modello si aspetta: così la notifica può dire QUALI funghi
    quella pioggia mette in moto e da quando aspettarseli, invece di
    limitarsi ai millimetri.
    """
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "daily": "precipitation_sum,temperature_2m_mean,et0_fao_evapotranspiration",
        "hourly": "temperature_2m,soil_temperature_6cm",
        "past_days": HISTORY_DAYS,
        "forecast_days": 1,
        "timezone": "auto",
    }
    resp = requests.get(FORECAST_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    precip = daily.get("precipitation_sum", [])
    if not dates:
        return None
    # l'ultimo indice è "oggi" (dati ancora parziali): il giorno concluso
    # più recente è quello prima
    idx = len(dates) - 2 if len(dates) >= 2 else len(dates) - 1
    hourly = data.get("hourly") or {}
    hourly_temp = [v for v in hourly.get("temperature_2m", []) if v is not None]
    soil_temp = [v for v in hourly.get("soil_temperature_6cm", []) if v is not None]

    # la pioggia corretta sostituisce quella grezza PRIMA di ogni lettura:
    # i millimetri annunciati nella notifica e quelli su cui il modello
    # decide le specie devono essere gli stessi numeri
    precip = corrected_precip(lat, lon, dates, daily)
    daily["precipitation_sum"] = [v if v is not None else 0.0 for v in precip]

    def series(key):
        return [v if v is not None else 0.0 for v in (daily.get(key) or [])][-HISTORY_DAYS:]

    return {
        "mm": precip[idx] if idx < len(precip) else None,
        "temp_c": hourly_temp[-1] if hourly_temp else None,
        "env": {
            "dates": dates[-HISTORY_DAYS:],
            "precip": series("precipitation_sum"),
            "temp": series("temperature_2m_mean"),
            "et0": series("et0_fao_evapotranspiration"),
            "soilTempC": soil_temp[-1] if soil_temp else None,
            # bosco e pH non li sappiamo per una zona disegnata a mano
            # libera (copre chilometri di terreno vario): restano neutri,
            # e la notifica parla solo di quello che sa davvero, cioè meteo
            # e stagione. Chi apre la mappa vede poi il dettaglio del punto.
            "vegClass": None,
            "elevation": None,
            "ph": None,
        },
    }


def score_zones(zones, occ_features):
    """Specie più promettente per ciascuna zona piovosa."""
    cases = []
    for i, zone in enumerate(zones):
        for species in SPECIES_LABELS:
            cases.append(
                {
                    "id": f"{i}:{species}",
                    "species": species,
                    "label": "zona",
                    "date": zone["conditions"]["env"]["dates"][-1],
                    "env": zone["conditions"]["env"],
                }
            )
    if not cases or occ_features is None:
        return {}
    rows = run_scorer(cases, occ_features)
    best = {}
    for row in rows:
        zone_idx = int(row["id"].split(":")[0])
        current = best.get(zone_idx)
        if current is None or row["scoreNew"] > current["scoreNew"]:
            best[zone_idx] = row
    return best


def species_note(best_row):
    """"Occhio ai porcini" — la parte che rende la notifica azionabile
    invece che solo meteorologica. Compare solo se il modello classifica la
    specie almeno "in arrivo" (stessa soglia calibrata della mappa, vedi
    tier in score_cases.js): annunciare funghi che non verranno è peggio
    che tacere."""
    if not best_row or best_row.get("tier") == "none":
        return ""
    label = SPECIES_LABELS.get(best_row["species"])
    if not label:
        return ""
    return f" Occhio ai {label}."


def send_push_rain(subscription_ids, rained_zones):
    headers = {"Authorization": f"Key {ONESIGNAL_REST_API_KEY}", "Content-Type": "application/json"}
    if len(rained_zones) == 1:
        z = rained_zones[0]
        temp_txt = f", {round(z['temp_c'])}°C" if z["temp_c"] is not None else ""
        heading = "Ha piovuto nella tua zona"
        content = f"{z['mm']:.0f}mm caduti ieri{temp_txt}.{species_note(z.get('best'))} Controlla le condizioni sulla mappa."
    else:
        max_mm = max(z["mm"] for z in rained_zones)
        best = max((z.get("best") for z in rained_zones if z.get("best")), key=lambda r: r["scoreNew"], default=None)
        heading = f"Ha piovuto in {len(rained_zones)} delle tue zone"
        content = f"Fino a {max_mm:.0f}mm caduti ieri.{species_note(best)} Controlla le condizioni sulla mappa."
    body = {
        "app_id": ONESIGNAL_APP_ID,
        "include_subscription_ids": subscription_ids,
        "headings": {"it": heading},
        "contents": {"it": content},
        "url": "https://andrea-b20.github.io/mappa-funghi/",
    }
    resp = requests.post(NOTIFICATIONS_URL, headers=headers, json=body, timeout=30)
    resp.raise_for_status()


# ---------------------------------------------------------------------
# Vicino a casa: prontezza nel raggio
# ---------------------------------------------------------------------


def ring_points(lat, lon, radius_km):
    """19 punti a tre anelli concentrici (centro, metà raggio, bordo),
    sempre lo stesso numero indipendentemente dal raggio: vedi
    HOME_SAMPLE_RINGS più sopra per il perché."""
    points = []
    for fraction, count in HOME_SAMPLE_RINGS:
        dist_m = radius_km * fraction * 1000
        for i in range(count):
            angle = math.radians(360 * i / count) if count > 1 else 0.0
            dlat = (dist_m / 111320) * math.cos(angle)
            dlon = (dist_m / (111320 * math.cos(math.radians(lat)))) * math.sin(angle) if dist_m else 0.0
            points.append((round(lat + dlat, 5), round(lon + dlon, 5)))
    return points


_fine_cells = None
_weather_cells = None

# oltre questa distanza (in gradi, ~11km) la cella fine più vicina è troppo
# lontana per fidarsene: si ripiega sulla griglia nazionale, più grossolana
# ma sempre disponibile ovunque in Italia
FINE_MATCH_MAX_DEG = 0.1


def _load_cells(path):
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    cells = []
    for f in data["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        cells.append((lat, lon, p.get("veg_class"), p.get("elevation_m")))
    return cells


def _nearest(cells, lat, lon):
    if not cells:
        return None, None
    best, best_d2 = None, None
    for c in cells:
        d2 = (c[0] - lat) ** 2 + (c[1] - lon) ** 2
        if best_d2 is None or d2 < best_d2:
            best, best_d2 = c, d2
    return best, best_d2


def nearest_habitat(lat, lon):
    """vegClass/elevation dalla cella precalcolata più vicina: quella fine
    (0.15°, solo dove esistono ritrovamenti storici — vedi
    scripts/fetch_vegetation_fine.py) se abbastanza vicina, altrimenti
    quella nazionale (0.2°, copre tutta l'Italia ma più grossolana). Stessa
    idea di zonesForCombinato in web/app.js, qui semplificata a un solo
    criterio di distanza perché i punti campionati intorno a una casa non
    cadono in una cella nota a priori come i ritrovamenti storici."""
    global _fine_cells, _weather_cells
    if _fine_cells is None:
        _fine_cells = _load_cells(FINE_PATH)
    if _weather_cells is None:
        _weather_cells = _load_cells(GRID_PATH)

    fine, fine_d2 = _nearest(_fine_cells, lat, lon)
    if fine is not None and fine_d2 <= FINE_MATCH_MAX_DEG**2:
        return fine[2], fine[3]
    weather, _ = _nearest(_weather_cells, lat, lon)
    if weather is not None:
        return weather[2], weather[3]
    return None, None


_ph_cache = None


def ph_at(lat, lon):
    global _ph_cache
    if _ph_cache is None:
        _ph_cache = json.loads(PH_PATH.read_text(encoding="utf-8")).get("ph_by_point", {}) if PH_PATH.exists() else {}
    r = PH_KEY_ROUND_DEG
    key = f"{round(lat / r) * r:.2f},{round(lon / r) * r:.2f}"
    return _ph_cache.get(key)


def fetch_home_weather(points):
    """Un'unica chiamata batch per tutti i punti campionati nel raggio di
    un utente: Open-Meteo accetta liste di lat/lon separate da virgola
    (verificato: 19 punti in una sola richiesta, come già fa
    fetch_weather_batch in scripts/fetch_weather_grid.py per la griglia
    nazionale), quindi il costo per utente non cresce con il numero di
    punti campionati."""
    lats = ",".join(str(p[0]) for p in points)
    lons = ",".join(str(p[1]) for p in points)
    params = {
        "latitude": lats,
        "longitude": lons,
        "daily": "precipitation_sum,temperature_2m_mean,et0_fao_evapotranspiration",
        "hourly": "soil_temperature_6cm",
        "past_days": HISTORY_DAYS,
        "forecast_days": 1,
        "timezone": "auto",
    }
    resp = requests.get(FORECAST_URL, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else [data]


def envs_for_home(points):
    payloads = fetch_home_weather(points)
    # i tre modelli per TUTTI i punti in una richiesta sola, come il
    # meteo qui sopra: chiederli punto per punto moltiplicherebbe per
    # diciannove le richieste di ogni utente senza cambiare il risultato
    try:
        model_precip = fetch_precip_models_batch(points, HISTORY_DAYS)
    except requests.RequestException:
        model_precip = [None] * len(points)

    envs = []
    for (lat, lon), payload, models in zip(points, payloads, model_precip):
        daily = payload.get("daily", {})
        dates = daily.get("time", [])
        if not dates:
            continue

        precip = daily.get("precipitation_sum") or []
        if models and models["dates"] == dates:
            precip = blend_model_precip(models["series"], len(dates))
        daily["precipitation_sum"] = [
            v if v is not None else 0.0 for v in apply_gauges(lat, lon, dates, precip, gauges())[0]
        ]

        def series(key, daily=daily):
            return [v if v is not None else 0.0 for v in (daily.get(key) or [])][-HISTORY_DAYS:]

        hourly = payload.get("hourly", {})
        soil_temp = [v for v in hourly.get("soil_temperature_6cm", []) if v is not None]
        veg_class, elevation = nearest_habitat(lat, lon)
        envs.append(
            {
                "dates": dates[-HISTORY_DAYS:],
                "precip": series("precipitation_sum"),
                "temp": series("temperature_2m_mean"),
                "et0": series("et0_fao_evapotranspiration"),
                "soilTempC": soil_temp[-1] if soil_temp else None,
                "vegClass": veg_class,
                "elevation": elevation,
                "ph": ph_at(lat, lon),
            }
        )
    return envs


def score_home(envs, species_list, occ_features):
    """Il punteggio migliore raggiungibile per ciascuna specie scelta,
    calcolato sul MASSIMO fra i punti campionati nel raggio: risponde a "c'è
    un posto buono qui vicino?", non "il punto esatto di casa è buono?" —
    casa è quasi sempre in un centro abitato, i funghi no."""
    if not envs or not species_list or occ_features is None:
        return {}
    cases = []
    for i, env in enumerate(envs):
        for species in species_list:
            cases.append({"id": f"{i}:{species}", "species": species, "label": "casa", "date": env["dates"][-1], "env": env})
    rows = run_scorer(cases, occ_features)
    best = {}
    for row in rows:
        sp = row["species"]
        current = best.get(sp)
        if current is None or row["scoreNew"] > current["scoreNew"]:
            best[sp] = row
    return best


def load_home_state():
    if not HOME_STATE_PATH.exists():
        return {}
    try:
        return json.loads(HOME_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_home_state(state):
    HOME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOME_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def send_push_home(subscription_ids, radius_km, escalations):
    """escalations: [{species, tier}, ...], già filtrati a quelli SALITI di
    livello rispetto all'ultima osservazione (vedi TIER_RANK in main()) —
    senza questo filtro la notifica ripartirebbe ogni giorno finché la
    prontezza resta alta, invece che una volta sola quando comincia."""
    ready = [e for e in escalations if e["tier"] == "ready"]
    soon = [e for e in escalations if e["tier"] == "soon"]
    parts = []
    if ready:
        names = " e ".join(SPECIES_LABELS[e["species"]] for e in ready)
        parts.append(f"{names} sembrano pronti")
    if soon:
        names = " e ".join(SPECIES_LABELS[e["species"]] for e in soon)
        parts.append(f"{names} in arrivo")
    content = "; ".join(parts)
    content = content[0].upper() + content[1:] + f", entro {radius_km:.0f}km da casa. Controlla la mappa."
    headers = {"Authorization": f"Key {ONESIGNAL_REST_API_KEY}", "Content-Type": "application/json"}
    body = {
        "app_id": ONESIGNAL_APP_ID,
        "include_subscription_ids": subscription_ids,
        "headings": {"it": "Funghi vicino a casa"},
        "contents": {"it": content},
        "url": "https://andrea-b20.github.io/mappa-funghi/",
    }
    resp = requests.post(NOTIFICATIONS_URL, headers=headers, json=body, timeout=30)
    resp.raise_for_status()


def process_zones(subscribers, occ_features):
    total = sum(len(s["zones"]) for s in subscribers)
    print(f"{sum(1 for s in subscribers if s['zones'])} iscritti con zone, {total} zone totali da controllare")
    for sub in subscribers:
        rained = []
        for zone in sub["zones"]:
            try:
                conditions = fetch_conditions(zone["lat"], zone["lon"])
            except Exception as exc:
                print(f"errore meteo per {zone['lat']},{zone['lon']}: {exc}", file=sys.stderr)
                continue
            if conditions and conditions["mm"] and conditions["mm"] >= MIN_MM_TO_NOTIFY:
                rained.append({**zone, **conditions, "conditions": conditions})
            time.sleep(0.1)
        if not rained:
            continue

        best_by_zone = score_zones(rained, occ_features)
        for idx, zone in enumerate(rained):
            zone["best"] = best_by_zone.get(idx)
        try:
            send_push_rain(sub["subscription_ids"], rained)
            print(f"notifica zona inviata: {len(rained)} zone piovose su {len(sub['zones'])}")
        except Exception as exc:
            print(f"errore invio push zona: {exc}", file=sys.stderr)


def process_homes(subscribers, occ_features):
    homes = [s for s in subscribers if s.get("home")]
    print(f"{len(homes)} iscritti con casa+raggio impostati")
    if not homes:
        return

    state = load_home_state()
    today = date.today().isoformat()
    changed = False

    for sub in homes:
        home = sub["home"]
        sub_id = sub["subscription_ids"][0]
        points = ring_points(home["lat"], home["lon"], home["radiusKm"])
        try:
            envs = envs_for_home(points)
        except Exception as exc:
            print(f"errore meteo casa per {sub_id}: {exc}", file=sys.stderr)
            continue
        if not envs:
            continue

        best_by_species = score_home(envs, home["species"], occ_features)
        prev = state.get(sub_id, {})
        next_state = {}
        escalations = []
        for species in home["species"]:
            row = best_by_species.get(species)
            tier = row["tier"] if row else "none"
            prev_tier = prev.get(species, {}).get("tier", "none")
            # notifica solo su una SALITA di livello rispetto a ieri: una
            # discesa (pioggia riassorbita, stagione che avanza) azzera lo
            # stato senza avvisare, così una risalita successiva torna a
            # essere una notizia e non un rumore di fondo quotidiano
            if TIER_RANK[tier] > TIER_RANK[prev_tier]:
                escalations.append({"species": species, "tier": tier})
            next_state[species] = {"tier": tier, "date": today}
        state[sub_id] = next_state
        changed = True

        if escalations:
            try:
                send_push_home(sub["subscription_ids"], home["radiusKm"], escalations)
                print(f"notifica casa inviata a {sub_id}: {escalations}")
            except Exception as exc:
                print(f"errore invio push casa: {exc}", file=sys.stderr)
        time.sleep(0.1)

    if changed:
        save_home_state(state)


def main():
    if not ONESIGNAL_APP_ID or not ONESIGNAL_REST_API_KEY:
        print("Notifiche non configurate (secret OneSignal mancanti), salto.")
        return
    subscribers = fetch_subscribers()
    print(f"{len(subscribers)} iscritti totali (zone e/o casa)")
    occ_features = load_occurrences()

    process_zones(subscribers, occ_features)
    process_homes(subscribers, occ_features)


if __name__ == "__main__":
    main()
