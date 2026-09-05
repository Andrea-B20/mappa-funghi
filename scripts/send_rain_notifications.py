"""
Controlla la pioggia caduta in ognuna delle zone salvate da ogni iscritto
alle notifiche e invia un push via OneSignal quando è piovuto. Gira come
job indipendente ("notify-rain") nell'Action giornaliera — non dipende
dalla griglia meteo, interroga Open-Meteo punto per punto per conto suo.

Richiede due secret GitHub Action:
  ONESIGNAL_APP_ID
  ONESIGNAL_REST_API_KEY   (REST API Key dell'app, non l'App ID)

Se mancano esce subito senza fare nulla: finché non vengono configurati
le notifiche restano semplicemente disattivate, senza rompere il resto
dell'aggiornamento dati.

Le zone di un utente sono un poligono disegnato a mano libera sul sito
(vedi setupRainZone in web/app.js): qui contano solo id + centroide,
salvati in un unico tag OneSignal "notify_zones" (JSON), non l'intera
forma — al server serve solo un punto rappresentativo per interrogare il
meteo, non il contorno esatto.

OneSignal non offre un endpoint per elencare direttamente tutti gli
iscritti: bisogna passare da un export CSV asincrono (POST
/players/csv_export, poi si scarica il file quando è pronto). È
l'approccio usato qui in fetch_subscribers().
"""

import csv
import gzip
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SCORER = ROOT / "scripts" / "score_cases.js"
OCC_PATH = ROOT / "web" / "data" / "occurrences.geojson"

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

# sotto questa soglia (mm caduti nel giorno) non avvisiamo: una spruzzata
# non cambia le condizioni di raccolta e manderebbe solo notifiche inutili
MIN_MM_TO_NOTIFY = 3.0


def fetch_subscribers():
    """Ritorna [{subscription_ids, zones: [{id, lat, lon}, ...]}], una voce
    per ogni iscritto che ha almeno una zona salvata (tag notify_zones)."""
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
        raw_zones = tags.get("notify_zones")
        if not raw_zones:
            continue
        try:
            zones = json.loads(raw_zones)
        except (TypeError, ValueError):
            continue
        zones = [z for z in zones if z.get("lat") is not None and z.get("lon") is not None]
        # "id" nell'export /players/csv_export è lo stesso identificativo
        # accettato da include_subscription_ids nell'invio (vedi send_push)
        subscription_id = row.get("id")
        if not zones or not subscription_id:
            continue
        subscribers.append({"subscription_ids": [subscription_id], "zones": zones})
    return subscribers


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


def score_species(zones):
    """Punteggi delle specie per ogni zona, calcolati dal MODELLO DEL SITO
    (web/model.js via Node), non da una copia in Python: una riscrittura
    divergerebbe al primo ritocco e la notifica finirebbe per dire una cosa
    diversa da quella che l'utente legge aprendo la mappa.

    Se Node o il file dei ritrovamenti mancano, ritorna {} e le notifiche
    tornano a essere quelle di prima (solo millimetri): meglio una notifica
    più povera che nessuna notifica.
    """
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
    if not cases:
        return {}
    try:
        train = json.loads(OCC_PATH.read_text(encoding="utf-8"))["features"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"ritrovamenti non leggibili ({exc}): notifiche senza specie", file=sys.stderr)
        return {}

    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "cases.json"
        out_path = Path(tmp) / "scores.json"
        in_path.write_text(json.dumps({"trainOccurrences": train, "cases": cases}), encoding="utf-8")
        try:
            subprocess.run(["node", str(SCORER), str(in_path), str(out_path)], check=True, capture_output=True)
            rows = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            print(f"modello non eseguibile ({exc}): notifiche senza specie", file=sys.stderr)
            return {}

    best = {}
    for row in rows:
        zone_idx = int(row["id"].split(":")[0])
        current = best.get(zone_idx)
        if current is None or row["scoreNew"] > current["scoreNew"]:
            best[zone_idx] = row
    return best


def species_note(best_row):
    """"I porcini sono attesi fra 6-14 giorni" — la parte che rende la
    notifica azionabile invece che solo meteorologica. Compare solo se il
    modello dà a quella specie un punteggio non trascurabile: annunciare
    funghi che non verranno è peggio che tacere."""
    if not best_row or best_row.get("scoreNew", 0) < 0.12:
        return ""
    label = SPECIES_LABELS.get(best_row["species"])
    if not label:
        return ""
    return f" Occhio ai {label}."


def send_push(subscription_ids, rained_zones):
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


def main():
    if not ONESIGNAL_APP_ID or not ONESIGNAL_REST_API_KEY:
        print("Notifiche pioggia non configurate (secret OneSignal mancanti), salto.")
        return
    subscribers = fetch_subscribers()
    total_zones = sum(len(s["zones"]) for s in subscribers)
    print(f"{len(subscribers)} iscritti, {total_zones} zone totali da controllare")
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

        # quali funghi mette in moto questa pioggia, secondo lo stesso
        # modello che colora la mappa
        best_by_zone = score_species(rained)
        for idx, zone in enumerate(rained):
            zone["best"] = best_by_zone.get(idx)
        try:
            send_push(sub["subscription_ids"], rained)
            print(f"notifica inviata: {len(rained)} zone piovose su {len(sub['zones'])}")
        except Exception as exc:
            print(f"errore invio push: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
