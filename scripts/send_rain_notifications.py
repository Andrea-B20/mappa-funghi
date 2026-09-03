"""
Controlla la pioggia caduta nella zona salvata da ogni iscritto alle
notifiche e invia un push via OneSignal quando è piovuto. Gira dopo
fetch_weather_grid.py, nella stessa Action giornaliera.

Richiede due secret GitHub Action:
  ONESIGNAL_APP_ID
  ONESIGNAL_REST_API_KEY   (REST API Key dell'app, non l'App ID)

Se mancano esce subito senza fare nulla: finché non vengono configurati
le notifiche restano semplicemente disattivate, senza rompere il resto
dell'aggiornamento dati.

Le zone sono salvate come tag sull'utente OneSignal (zone_lat, zone_lon,
zone_radius_km) quando l'utente disegna la zona sul sito, vedi
requestPushAndTagZone in web/app.js.

NB: non è stato possibile testare questo script end-to-end senza un
account OneSignal reale — verificare con un invio di prova
(workflow_dispatch) alla prima attivazione, e aggiustare gli endpoint se
nel frattempo l'API di OneSignal fosse cambiata.
"""

import os
import sys
import time

import requests

ONESIGNAL_APP_ID = os.environ.get("ONESIGNAL_APP_ID")
ONESIGNAL_REST_API_KEY = os.environ.get("ONESIGNAL_REST_API_KEY")

USERS_URL = "https://api.onesignal.com/apps/{app_id}/users"
NOTIFICATIONS_URL = "https://onesignal.com/api/v1/notifications"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# sotto questa soglia (mm caduti nel giorno) non avvisiamo: una spruzzata
# non cambia le condizioni di raccolta e manderebbe solo notifiche inutili
MIN_MM_TO_NOTIFY = 3.0


def fetch_subscribers():
    """Ritorna una lista di zone salvate: [{subscription_ids, lat, lon,
    radius_km}], una per ogni iscritto che ha effettivamente disegnato e
    attivato una zona (tag zone_lat/zone_lon presenti)."""
    subscribers = []
    offset = 0
    limit = 200
    headers = {"Authorization": f"Key {ONESIGNAL_REST_API_KEY}"}
    while True:
        url = USERS_URL.format(app_id=ONESIGNAL_APP_ID)
        resp = requests.get(url, headers=headers, params={"limit": limit, "offset": offset}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        users = data.get("users", [])
        if not users:
            break
        for user in users:
            tags = (user.get("properties") or {}).get("tags") or {}
            lat, lon = tags.get("zone_lat"), tags.get("zone_lon")
            if lat is None or lon is None:
                continue
            push_ids = [
                s["id"] for s in (user.get("subscriptions") or [])
                if s.get("type") == "OneSignalPush" and s.get("id")
            ]
            if not push_ids:
                continue
            subscribers.append({
                "subscription_ids": push_ids,
                "lat": float(lat),
                "lon": float(lon),
                "radius_km": float(tags.get("zone_radius_km") or 10.0),
            })
        offset += limit
        if offset >= data.get("total_count", offset):
            break
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
        "date": dates[idx],
        "mm": precip[idx] if idx < len(precip) else None,
        "temp_c": hourly_temp[-1] if hourly_temp else None,
    }


def send_push(subscription_ids, mm, temp_c):
    headers = {"Authorization": f"Key {ONESIGNAL_REST_API_KEY}", "Content-Type": "application/json"}
    temp_txt = f", {round(temp_c)}°C" if temp_c is not None else ""
    body = {
        "app_id": ONESIGNAL_APP_ID,
        "include_subscription_ids": subscription_ids,
        "headings": {"it": "Ha piovuto nella tua zona"},
        "contents": {"it": f"{mm:.0f}mm caduti ieri{temp_txt}. Controlla le condizioni sulla mappa."},
        "url": "https://andrea-b20.github.io/mappa-funghi/",
    }
    resp = requests.post(NOTIFICATIONS_URL, headers=headers, json=body, timeout=30)
    resp.raise_for_status()


def main():
    if not ONESIGNAL_APP_ID or not ONESIGNAL_REST_API_KEY:
        print("Notifiche pioggia non configurate (secret OneSignal mancanti), salto.")
        return
    subscribers = fetch_subscribers()
    print(f"{len(subscribers)} zone salvate da controllare")
    for sub in subscribers:
        try:
            conditions = fetch_conditions(sub["lat"], sub["lon"])
        except Exception as exc:
            print(f"errore meteo per {sub['lat']},{sub['lon']}: {exc}", file=sys.stderr)
            continue
        if not conditions or not conditions["mm"] or conditions["mm"] < MIN_MM_TO_NOTIFY:
            continue
        try:
            send_push(sub["subscription_ids"], conditions["mm"], conditions["temp_c"])
            print(f"notifica inviata per {sub['lat']},{sub['lon']}: {conditions['mm']}mm")
        except Exception as exc:
            print(f"errore invio push: {exc}", file=sys.stderr)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
