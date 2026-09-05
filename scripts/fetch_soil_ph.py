"""
pH del suolo (SoilGrids 2.0, ISRIC — 250m, gratuito) per i punti delle due
griglie del sito.

Perché serve: Corine dice "latifoglie/conifere/misto" e basta, ma due
querceti identici per Corine possono stare uno su arenaria acida e uno su
calcare, e per due delle quattro specie tracciate la differenza è
decisiva. L'ovolo (Amanita caesarea) predilige suoli acidi/silicei ed è
raro sui calcarei; il porcino dei pini (Boletus pinophilus) vuole suoli
acidi e sabbiosi. In Italia questo separa davvero il territorio: Appennino
settentrionale arenaceo contro Appennino centrale calcareo, Prealpi
carbonatiche contro Alpi silicee.

Perché è uno script a parte e non una colonna dentro fetch_weather_grid:
  1. il pH è una proprietà STATICA del suolo — rifarla ogni giorno sarebbe
     traffico sprecato su un servizio gratuito altrui;
  2. SoilGrids dichiara un fair use di 5 chiamate al minuto e risponde in
     ~3s, quindi coprire tutti i punti in una sola esecuzione vorrebbe dire
     ore di job. Qui la cache si riempie a scaglioni: ogni esecuzione
     aggiunge al massimo MAX_NEW_POINTS punti nuovi e si ferma, così in
     qualche giorno di aggiornamenti automatici la copertura si completa da
     sola e da lì in poi lo script è un no-op da pochi secondi;
  3. weather_grid.geojson e vegetation_fine.geojson vengono rigenerati da
     zero dai rispettivi script: una colonna pH scritta lì dentro verrebbe
     cancellata al primo aggiornamento meteo. Il file separato sopravvive.

Uso:
    .venv/bin/python scripts/fetch_soil_ph.py
"""

import json
import time
from datetime import datetime
from pathlib import Path

import requests

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

# fair use dichiarato da ISRIC: 5 chiamate al minuto. 12s di distanza le
# rispetta esattamente; non abbassarlo per "fare prima", è un servizio
# pubblico gratuito e l'alternativa a rispettarlo è restare senza dato
REQUEST_SPACING_S = 12
MAX_NEW_POINTS = 120

# SoilGrids è gratuito ma lento e a tratti irraggiungibile: nei test 2
# chiamate su 3 sono andate in read timeout a 60s. Da qui il timeout largo,
# un solo secondo tentativo (siamo comunque a un ordine di grandezza sotto
# il fair use) e soprattutto un tetto di durata: senza, un'ora storta del
# servizio farebbe girare a vuoto il job di GitHub Actions per ore. Quello
# che non si scarica oggi resta semplicemente fuori cache e si ritenta al
# prossimo aggiornamento — la copertura si completa in qualche giorno.
REQUEST_TIMEOUT_S = 100
MAX_RUNTIME_S = 25 * 60

# la cache va su disco ogni tot punti, non solo alla fine: un'esecuzione
# lunga interrotta a metà (timeout del job, runner riciclato) altrimenti
# butterebbe via tutto il lavoro fatto, e ricominciare da zero ogni volta
# significherebbe non completare mai la copertura
SAVE_EVERY = 10

# le due griglie campionano a scale diverse, ma il pH del suolo varia con
# continuità: arrotondare le chiavi a ~1km evita di richiedere due volte
# quello che SoilGrids restituirebbe identico (la sua risoluzione è 250m)
KEY_ROUND_DEG = 0.01

DATA_DIR = Path(__file__).resolve().parent.parent / "web" / "data"
GRID_PATH = DATA_DIR / "weather_grid.geojson"
FINE_PATH = DATA_DIR / "vegetation_fine.geojson"
OUT_PATH = DATA_DIR / "soil_ph.json"


def point_key(lat, lon):
    return f"{round(lat / KEY_ROUND_DEG) * KEY_ROUND_DEG:.2f},{round(lon / KEY_ROUND_DEG) * KEY_ROUND_DEG:.2f}"


def wanted_points():
    """Tutti i punti che le due griglie mostrano, senza duplicati.

    Ordine deliberato: prima la griglia meteo (poche celle, alimenta la
    modalità "Meteo attuale"), poi quella fine dei ritrovamenti. Così le
    prime esecuzioni rendono già utilizzabile la mappa più semplice invece
    di riempire a caso metà di entrambe.
    """
    points = {}
    for path in (GRID_PATH, FINE_PATH):
        if not path.exists():
            print(f"  {path.name} assente, salto.")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for feature in data.get("features", []):
            lon, lat = feature["geometry"]["coordinates"]
            points.setdefault(point_key(lat, lon), (lat, lon))
    return points


def fetch_ph(lat, lon):
    """pH in acqua sullo strato 5-30cm, quello esplorato dal micelio.

    SoilGrids restituisce il pH moltiplicato per 10 (campo d_factor), da
    qui la divisione: un valore grezzo di 66 significa pH 6.6.
    """
    params = [
        ("lat", lat),
        ("lon", lon),
        ("property", "phh2o"),
        ("depth", "5-15cm"),
        ("depth", "15-30cm"),
        ("value", "mean"),
    ]
    try:
        resp = requests.get(SOILGRIDS_URL, params=params, timeout=REQUEST_TIMEOUT_S)
    except requests.Timeout:
        time.sleep(REQUEST_SPACING_S)
        resp = requests.get(SOILGRIDS_URL, params=params, timeout=REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    layers = resp.json().get("properties", {}).get("layers", [])
    values = []
    for layer in layers:
        factor = layer.get("unit_measure", {}).get("d_factor", 10)
        for depth in layer.get("depths", []):
            raw = (depth.get("values") or {}).get("mean")
            if raw is not None:
                values.append(raw / factor)
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def write_cache(cache, points):
    known = [v for v in cache.values() if v is not None]
    OUT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "key_round_deg": KEY_ROUND_DEG,
                "covered": len(cache),
                "wanted": len(points),
                "ph_min": round(min(known), 2) if known else None,
                "ph_max": round(max(known), 2) if known else None,
                "ph_by_point": cache,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main():
    cache = {}
    if OUT_PATH.exists():
        cache = json.loads(OUT_PATH.read_text(encoding="utf-8")).get("ph_by_point", {})

    points = wanted_points()
    # "None" in cache = SoilGrids non ha dato per quel punto (mare, roccia
    # nuda): è una risposta, non un buco da ritentare ogni giorno per sempre
    missing = [(k, v) for k, v in points.items() if k not in cache]
    print(f"{len(points)} punti richiesti, {len(cache)} già in cache, {len(missing)} mancanti.")

    if not missing:
        print("Cache completa, nulla da fare.")
        return

    todo = missing[:MAX_NEW_POINTS]
    print(f"Ne scarico {len(todo)} in questa esecuzione (~{len(todo) * REQUEST_SPACING_S // 60} minuti).")

    added = 0
    failed = 0
    started = time.monotonic()
    for idx, (key, (lat, lon)) in enumerate(todo, 1):
        if time.monotonic() - started > MAX_RUNTIME_S:
            print(f"  Tetto di durata raggiunto dopo {idx - 1} punti, mi fermo qui.")
            break
        try:
            cache[key] = fetch_ph(lat, lon)
            added += 1
        except requests.RequestException as e:
            # il punto NON finisce in cache, quindi rientra fra i mancanti
            # alla prossima esecuzione: un servizio giù oggi non lascia un
            # buco permanente nella copertura
            failed += 1
            print(f"  Errore SoilGrids per {key}: {e}")
        if idx % SAVE_EVERY == 0:
            write_cache(cache, points)
        if idx % 20 == 0 or idx == len(todo):
            print(f"  {idx}/{len(todo)} (ok {added}, falliti {failed})...", flush=True)
        if idx < len(todo):
            time.sleep(REQUEST_SPACING_S)

    write_cache(cache, points)
    known = [v for v in cache.values() if v is not None]
    span = f"{min(known):.1f}-{max(known):.1f}" if known else "n/d"
    print(
        f"Scritti {added} punti nuovi ({failed} falliti, si ritentano alla prossima esecuzione). "
        f"Copertura {len(cache)}/{len(points)}, pH nel campo {span} -> {OUT_PATH}"
    )


if __name__ == "__main__":
    main()
