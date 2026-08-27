"""
Scarica occorrenze storiche di funghi per l'Italia da due fonti open:
  - GBIF (aggregatore scientifico, include anche dati "research-grade" di
    iNaturalist, ma spesso in ritardo di sincronizzazione)
  - iNaturalist (osservazioni dirette, comprese quelle "needs_id" più
    recenti non ancora sincronizzate su GBIF)

Le due fonti si sovrappongono parzialmente (GBIF ripubblica le osservazioni
iNaturalist "research grade"): i record vengono deduplicati per specie +
posizione arrotondata (~100m) + data, tenendo la versione GBIF quando
coincidono (metadata più stabile).

FILTRI DI ATTENDIBILITÀ POSIZIONE (aggiunti dopo aver verificato segnalazioni
implausibili, es. nel centro di grandi città): scartiamo i record la cui
posizione non è abbastanza precisa da fidarsene per una mappa di calore,
individuati controllando incertezza/precisione dichiarata dalle fonti:
  - GBIF: solo basisOfRecord=HUMAN_OBSERVATION (esclude reperti da erbario/
    campioni di laboratorio, spesso vecchi di decenni e geocodificati sul
    nome del comune anziché sul punto di ritrovamento — es. trovato un
    "Amanita caesarea" del 1892 con incertezza dichiarata di 3km, coordinate
    coincidenti col centro di Genova) e coordinateUncertaintyInMeters entro
    COORD_UNCERTAINTY_MAX_M quando il campo è presente.
  - iNaturalist: esclude le osservazioni "obscured" (posizione volutamente
    randomizzata dall'utente entro un raggio di ~20-30km — pratica comune
    per specie pregiate, per proteggere i propri punti di raccolta da altri
    raccoglitori: la stragrande maggioranza dei casi rilevati) e quelle con
    public_positional_accuracy oltre la stessa soglia; esclude anche le
    osservazioni "captive" (funghi coltivati/in vaso, non ritrovamenti
    selvatici).
  Verificato: senza questi filtri, sulle ~1100 occorrenze GBIF di Boletus
  edulis un campione di 300 record ne conteneva 154 (51%) con incertezza
  dichiarata di circa 27km — quasi tutte osservazioni iNaturalist
  offuscate ripubblicate via GBIF.

Salva il risultato come un unico GeoJSON con proprietà:
  - species: chiave interna (es. "porcino_comune")
  - label: nome comune italiano
  - scientificName
  - year, eventDate
  - source: "gbif" o "inaturalist"

Uso:
    .venv/bin/python scripts/fetch_occurrences.py
"""

import json
import time
from pathlib import Path

import requests

GBIF_URL = "https://api.gbif.org/v1/occurrence/search"
INATURALIST_URL = "https://api.inaturalist.org/v1/observations"

# oltre questa soglia la posizione dichiarata non è abbastanza precisa da
# fidarsene per attribuire un ritrovamento a una cella/zona specifica —
# vedi commento in testa al file per come è stata scelta
COORD_UNCERTAINTY_MAX_M = 2000

# specie da tracciare: chiave interna -> (nome comune, nome scientifico)
SPECIES = {
    "porcino_comune": ("Porcino comune", "Boletus edulis"),
    "porcino_pini": ("Porcino dei pini", "Boletus pinophilus"),
    "ovolo": ("Ovolo", "Amanita caesarea"),
    "gallinaccio": ("Gallinaccio", "Cantharellus cibarius"),
}

# bounding box approssimativo dell'Italia (usato solo per iNaturalist, che
# non ha un filtro "country" diretto come GBIF)
IT_BBOX = {"swlat": 35.2, "swlng": 6.0, "nelat": 47.3, "nelng": 19.0}

GBIF_PAGE_SIZE = 300
GBIF_MAX_RECORDS = 3000
INAT_PAGE_SIZE = 200
INAT_MAX_PAGES = 15  # 15*200 = 3000 record max per specie, come GBIF

OUT_PATH = Path(__file__).resolve().parent.parent / "web" / "data" / "occurrences.geojson"


def fetch_gbif(scientific_name):
    records = []
    skipped_imprecise = 0
    offset = 0
    while offset < GBIF_MAX_RECORDS:
        params = {
            "scientificName": scientific_name,
            "country": "IT",
            "hasCoordinate": "true",
            "hasGeospatialIssue": "false",
            # esclude reperti da erbario/campioni: non sono "l'ho visto qui",
            # e spesso hanno solo una posizione approssimata dal nome del
            # comune — vedi commento in testa al file
            "basisOfRecord": "HUMAN_OBSERVATION",
            "limit": GBIF_PAGE_SIZE,
            "offset": offset,
        }
        resp = requests.get(GBIF_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        for r in results:
            lat, lon = r.get("decimalLatitude"), r.get("decimalLongitude")
            if lat is None or lon is None:
                continue
            uncertainty = r.get("coordinateUncertaintyInMeters")
            if uncertainty is not None and uncertainty > COORD_UNCERTAINTY_MAX_M:
                skipped_imprecise += 1
                continue
            records.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "year": r.get("year"),
                    "eventDate": r.get("eventDate"),
                    "source": "gbif",
                }
            )
        offset += GBIF_PAGE_SIZE
        if data.get("endOfRecords", True) or not results:
            break
        time.sleep(0.2)
    if skipped_imprecise:
        print(f"    (scartati {skipped_imprecise} record GBIF con posizione poco precisa, >{COORD_UNCERTAINTY_MAX_M}m)")
    return records


def fetch_inaturalist(scientific_name):
    records = []
    skipped_imprecise = 0
    for page in range(1, INAT_MAX_PAGES + 1):
        params = {
            "taxon_name": scientific_name,
            "quality_grade": "research,needs_id",
            "geo": "true",
            # esclude funghi coltivati/in vaso: non sono ritrovamenti selvatici
            "captive": "false",
            "per_page": INAT_PAGE_SIZE,
            "page": page,
            "order_by": "observed_on",
            **IT_BBOX,
        }
        resp = requests.get(INATURALIST_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        for o in results:
            loc = o.get("location")
            if not loc:
                continue
            # "obscured": l'utente (o iNaturalist, per specie sensibili) ha
            # volutamente randomizzato la posizione entro ~20-30km per
            # proteggere il proprio punto di raccolta — pratica comune per
            # specie pregiate come queste, vedi commento in testa al file
            if o.get("obscured"):
                skipped_imprecise += 1
                continue
            accuracy = o.get("public_positional_accuracy")
            if accuracy is not None and accuracy > COORD_UNCERTAINTY_MAX_M:
                skipped_imprecise += 1
                continue
            lat_str, lon_str = loc.split(",")
            date = o.get("observed_on")
            records.append(
                {
                    "lat": float(lat_str),
                    "lon": float(lon_str),
                    "year": int(date[:4]) if date else None,
                    "eventDate": date,
                    "source": "inaturalist",
                }
            )
        if len(results) < INAT_PAGE_SIZE:
            break
        time.sleep(1)  # cortesia verso l'API pubblica (rate limit ~60/min)
    if skipped_imprecise:
        print(f"    (scartati {skipped_imprecise} record iNaturalist offuscati o poco precisi)")
    return records


def dedupe_key(rec):
    # ~100m di tolleranza in posizione, stessa data: due segnalazioni così
    # vicine nello stesso giorno sono quasi certamente lo stesso ritrovamento
    # pubblicato su entrambe le piattaforme
    date = (rec.get("eventDate") or "")[:10]
    return (round(rec["lat"], 3), round(rec["lon"], 3), date)


def merge_dedupe(gbif_records, inat_records):
    seen = {dedupe_key(r) for r in gbif_records}
    merged = list(gbif_records)
    duplicates = 0
    for r in inat_records:
        key = dedupe_key(r)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        merged.append(r)
    return merged, duplicates


def main():
    features = []
    for key, (label, scientific_name) in SPECIES.items():
        print(f"\n{label} ({scientific_name})")

        print("  GBIF...")
        gbif_records = fetch_gbif(scientific_name)
        print(f"    -> {len(gbif_records)} record")

        print("  iNaturalist...")
        inat_records = fetch_inaturalist(scientific_name)
        print(f"    -> {len(inat_records)} record")

        merged, duplicates = merge_dedupe(gbif_records, inat_records)
        print(f"  Totale dopo deduplica: {len(merged)} (scartati {duplicates} duplicati)")

        for r in merged:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                    "properties": {
                        "species": key,
                        "label": label,
                        "scientificName": scientific_name,
                        "year": r["year"],
                        "eventDate": r["eventDate"],
                        "source": r["source"],
                    },
                }
            )

    geojson = {"type": "FeatureCollection", "features": features}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(geojson), encoding="utf-8")
    print(f"\nTotale occorrenze salvate: {len(features)}")
    print(f"File scritto in: {OUT_PATH}")


if __name__ == "__main__":
    main()
