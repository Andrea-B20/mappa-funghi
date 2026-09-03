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
import sys
import time

import requests

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
    """Pioggia e temperatura del giorno più recente già concluso, nel punto
    esatto della zona (stessa API usata dal popup del sito, vedi
    onMapClick in web/app.js — non la griglia grossolana in background)."""
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "daily": "precipitation_sum",
        "hourly": "temperature_2m",
        "past_days": 2,
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
    hourly_temp = (data.get("hourly") or {}).get("temperature_2m", [])
    return {
        "mm": precip[idx] if idx < len(precip) else None,
        "temp_c": hourly_temp[-1] if hourly_temp else None,
    }


def send_push(subscription_ids, rained_zones):
    headers = {"Authorization": f"Key {ONESIGNAL_REST_API_KEY}", "Content-Type": "application/json"}
    if len(rained_zones) == 1:
        z = rained_zones[0]
        temp_txt = f", {round(z['temp_c'])}°C" if z["temp_c"] is not None else ""
        heading = "Ha piovuto nella tua zona"
        content = f"{z['mm']:.0f}mm caduti ieri{temp_txt}. Controlla le condizioni sulla mappa."
    else:
        max_mm = max(z["mm"] for z in rained_zones)
        heading = f"Ha piovuto in {len(rained_zones)} delle tue zone"
        content = f"Fino a {max_mm:.0f}mm caduti ieri. Controlla le condizioni sulla mappa."
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
                rained.append({**zone, **conditions})
            time.sleep(0.1)
        if not rained:
            continue
        try:
            send_push(sub["subscription_ids"], rained)
            print(f"notifica inviata: {len(rained)} zone piovose su {len(sub['zones'])}")
        except Exception as exc:
            print(f"errore invio push: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
