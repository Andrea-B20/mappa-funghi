"""
Quanto sbaglia la pioggia dell'app, misurato contro pluviometri veri.

I numeri citati nei commenti di rain_gauges.py e fetch_weather_grid.py
vengono da qui. Questo script li ricalcola da capo, così non restano
affermazioni da credere sulla parola: se un domani Open-Meteo cambia
modelli, o si aggiunge una rete regionale, basta rieseguirlo per sapere se
le scelte fatte reggono ancora.

Come funziona
-------------
La rete di ARPA Lombardia pubblica 256 pluviometri con storico completo:
è il banco di prova più denso disponibile in Italia. Per ogni stazione e
ogni giorno si confronta la pioggia MISURATA con tre stime:

  1. quella vecchia: un modello solo, letto sul nodo più vicino della
     griglia da 0.5° — cioè esattamente quello che l'app mostrava
  2. quella nuova dove non arrivano pluviometri: media di tre modelli
     ridotta da quanti sono d'accordo
  3. quella nuova dove arrivano: la 2 corretta con i pluviometri vicini,
     ESCLUSA la stazione da indovinare

L'esclusione al punto 3 è ciò che rende onesto il confronto: senza,
staremmo chiedendo a un pluviometro di prevedere sé stesso e otterremmo
zero errore per costruzione.

Il confronto è sulla pioggia cumulata di 7 giorni, non sul singolo giorno,
perché è così che l'app la usa: nessuna specie fruttifica per la pioggia di
ieri, tutte ragionano su finestre di giorni (vedi windowDays in
web/model.js).

Uso:
    .venv/bin/python scripts/validate_rain.py [giorni]   # default 60
"""

import json
import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from rain_gauges import (  # noqa: E402
    GAUGE_K,
    GAUGE_MAX_KM,
    LOMBARDIA_SENSORS,
    LOMBARDIA_VALUES,
    PRECIP_MODELS,
    blend,
    blend_model_precip,
    fetch_precip_models_batch,
)

# la griglia che l'app usava prima: serve a misurare quanto dell'errore era
# semplicemente distanza fra il punto campionato e quello guardato
OLD_GRID_STEP = 0.5
OLD_LAT_MIN, OLD_LON_MIN = 36.0, 6.5

WINDOW_DAYS = 7
# "pioggia utile": sotto i 10 mm in una settimana nessuna delle specie
# tracciate parte (la soglia più bassa, il gallinaccio, è 12 mm in 4 giorni)
USEFUL_MM = 10.0
# sotto questa soglia il pluviometro dice "non è piovuto": non zero esatto,
# perché una bascula registra decimi anche solo con la rugiada
DRY_MM = 2.0

BATCH = 25


def fetch_gauges(days_back):
    sensors = requests.get(
        LOMBARDIA_SENSORS,
        params={"$limit": 5000, "tipologia": "Precipitazione", "storico": "N"},
        timeout=120,
    ).json()
    meta = {}
    for s in sensors:
        try:
            meta[s["idsensore"]] = (float(s["lat"]), float(s["lng"]))
        except (KeyError, TypeError, ValueError):
            continue

    start = (date.today() - timedelta(days=days_back)).isoformat()
    end = date.today().isoformat()
    ids = ",".join(f"'{k}'" for k in meta)
    rows = requests.get(
        LOMBARDIA_VALUES,
        params={
            "$select": "idsensore,date_trunc_ymd(data) as d,sum(valore) as mm",
            "$group": "idsensore,d",
            "$limit": 100000,
            "$where": (f"data >= '{start}T00:00:00' AND data < '{end}T00:00:00' "
                       f"AND stato='VA' AND idsensore IN ({ids})"),
        },
        timeout=300,
    ).json()

    daily = defaultdict(dict)
    for r in rows:
        sid = r.get("idsensore")
        if sid not in meta:
            continue
        mm = float(r["mm"])
        if mm >= 0:
            daily[sid][r["d"][:10]] = mm
    return meta, daily


def fetch_models(points, past_days):
    out = []
    for i in range(0, len(points), BATCH):
        batch = points[i : i + BATCH]
        out.extend(fetch_precip_models_batch(batch, past_days))
        print(f"  modelli {min(i + BATCH, len(points))}/{len(points)}", flush=True)
        time.sleep(0.5)
    return out


def snap_to_old_grid(lat, lon):
    return (
        round(OLD_LAT_MIN + round((lat - OLD_LAT_MIN) / OLD_GRID_STEP) * OLD_GRID_STEP, 4),
        round(OLD_LON_MIN + round((lon - OLD_LON_MIN) / OLD_GRID_STEP) * OLD_GRID_STEP, 4),
    )


def km(lat1, lon1, lat2, lon2):
    return 111.0 * math.hypot(lat1 - lat2, (lon1 - lon2) * math.cos(math.radians(lat1)))


def score(label, pairs):
    """pairs: (osservato, stimato) su finestre di 7 giorni."""
    ae = [abs(m - o) for o, m in pairs]
    tot_o = sum(o for o, m in pairs) or 1e-9
    tot_m = sum(m for o, m in pairs)
    said_rain = [p for p in pairs if p[1] >= USEFUL_MM]
    was_rain = [p for p in pairs if p[0] >= USEFUL_MM]
    false_pos = [p for p in said_rain if p[0] < DRY_MM]
    caught = [p for p in was_rain if p[1] >= USEFUL_MM]
    print(
        f"{label:42s} errore medio {statistics.mean(ae):5.1f} mm   "
        f"scarto {tot_m / tot_o - 1:+4.0%}   "
        f"falsi positivi {100 * len(false_pos) / max(1, len(said_rain)):5.1f}%   "
        f"piogge riconosciute {100 * len(caught) / max(1, len(was_rain)):5.1f}%"
    )


def windows(series):
    """Somme mobili di WINDOW_DAYS, saltando le finestre incomplete."""
    out = []
    for i in range(WINDOW_DAYS - 1, len(series)):
        chunk = series[i - WINDOW_DAYS + 1 : i + 1]
        if any(o is None or m is None for o, m in chunk):
            continue
        out.append((sum(o for o, m in chunk), sum(m for o, m in chunk)))
    return out


def main():
    days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    print(f"Scarico i pluviometri di ARPA Lombardia (ultimi {days_back} giorni)...")
    meta, obs = fetch_gauges(days_back)
    sids = sorted(obs)
    print(f"  {len(sids)} pluviometri, {sum(len(v) for v in obs.values())} totali giornalieri")

    points = [meta[s] for s in sids]
    print(f"Scarico i {len(PRECIP_MODELS)} modelli nel punto esatto di ogni stazione...")
    at_station = fetch_models(points, days_back)

    old_points = sorted({snap_to_old_grid(*meta[s]) for s in sids})
    print(f"Scarico il modello sui {len(old_points)} nodi della vecchia griglia da {OLD_GRID_STEP}°...")
    at_old_grid = fetch_models(old_points, days_back)
    old_by_node = {p: r for p, r in zip(old_points, at_old_grid)}

    all_days = sorted({d for v in obs.values() for d in v})
    by_day = defaultdict(dict)
    for sid, series in obs.items():
        for d, mm in series.items():
            by_day[d][sid] = mm

    # vicini di ogni stazione, la stazione stessa esclusa
    neighbours = {}
    for sid in sids:
        lat, lon = meta[sid]
        near = sorted(
            (km(lat, lon, *meta[o]), o) for o in sids if o != sid
        )
        neighbours[sid] = [n for n in near if n[0] <= GAUGE_MAX_KM]

    old_pairs, model_pairs, full_pairs = [], [], []
    for sid, models in zip(sids, at_station):
        dates = models["dates"]
        model_series = blend_model_precip(models["series"], len(dates))

        node = old_by_node[snap_to_old_grid(*meta[sid])]
        old_series = node["series"][0] if node["series"] else []

        old_seq, model_seq, full_seq = [], [], []
        for i, d in enumerate(dates):
            truth = obs[sid].get(d)
            m = model_series[i]
            old = old_series[i] if i < len(old_series) else None
            old_seq.append((truth, old))
            model_seq.append((truth, m))

            num = den = 0.0
            used = 0
            for dist, other in neighbours[sid]:
                v = by_day[d].get(other)
                if v is None:
                    continue
                w = 1.0 / max(dist, 1.0) ** 2
                num += w * v
                den += w
                used += 1
                if used >= GAUGE_K:
                    break
            g = num / den if den else None
            nearest = neighbours[sid][0][0] if neighbours[sid] else None
            full_seq.append((truth, blend(m, g, used, nearest)[0]))

        old_pairs += windows(old_seq)
        model_pairs += windows(model_seq)
        full_pairs += windows(full_seq)

    print(
        f"\nPioggia cumulata su {WINDOW_DAYS} giorni, soglia \"utile\" {USEFUL_MM:.0f} mm, "
        f"{len(all_days)} giorni, {len(sids)} stazioni\n"
    )
    score(f"PRIMA: griglia {OLD_GRID_STEP}° + un modello", old_pairs)
    score(f"DOPO, senza pluviometri vicini", model_pairs)
    score(f"DOPO, con pluviometri (stazione esclusa)", full_pairs)


if __name__ == "__main__":
    main()
