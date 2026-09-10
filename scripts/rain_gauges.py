"""
Pioggia MISURATA da pluviometri reali, non prevista da un modello.

Perché esiste questo file
-------------------------
Fino a ieri la pioggia di ogni cella veniva da un modello meteo
(Open-Meteo). Un modello non misura la pioggia: la calcola. Confrontando
un anno di celle contro i 256 pluviometri della rete regionale lombarda
(15.454 coppie stazione-giorno, luglio-settembre 2026) il modello
risultava:

  - 12.8% di giorni "piovosi" in cui al pluviometro non era caduto nulla
  - +25% di acqua in eccesso sul totale

e questo NEL PUNTO ESATTO della stazione. Sulla griglia da 0.5° che l'app
usava davvero — un punto ogni 55 km — i falsi positivi salivano al 17.6%.
È esattamente il sintomo osservato: la cella dice che ha piovuto, i siti
meteo consultati per quel paese dicono di no.

Stessa verifica, ma interpolando i pluviometri veri invece del modello
(lasciando fuori a turno la stazione da indovinare, così il confronto è
onesto): 7.3% di falsi positivi e un errore medio di 1.6 mm al giorno
contro i 3.5 mm del modello. Meno della metà.

Da qui questo modulo: scarica i totali giornalieri veri dalle reti
regionali che pubblicano dati aperti, e li tiene in un archivio che si
accumula giorno per giorno.

L'archivio serve perché le API non hanno la stessa memoria
--------------------------------------------------------
Arpa Piemonte pubblica solo le ultime 72 ore. Se ogni esecuzione
ripartisse da zero, la finestra utile sarebbe di 3 giorni, mentre l'app
ragiona su 16 giorni di pioggia. Con l'archivio in data/rain_gauges.json
ogni esecuzione aggiunge i giorni nuovi a quelli già raccolti, e dopo due
settimane di aggiornamenti quotidiani anche il Piemonte ha una storia
completa.

Copertura
---------
Le reti pluviometriche italiane non hanno un'API nazionale: ogni regione
pubblica per conto suo, con formati diversi, e diverse non pubblicano
affatto in modo interrogabile. Qui ci sono le cinque reti verificate
funzionanti; dove non arriva nessun pluviometro la cella resta sul
modello (vedi fetch_weather_grid.py, che segna la provenienza cella per
cella così la mappa può dirlo).

Aggiungere una regione = aggiungere una funzione che restituisce
stazioni con coordinate e totali giornalieri, e metterla in PROVIDERS.
Vale la pena farlo: misurato che una cella con un pluviometro entro 3 km
azzecca la quantità di pioggia nel 76% dei casi contro il 50% di una cella
servita dai soli modelli.
Se una rete non risponde, quella regione torna al modello e le altre
continuano: nessun provider può far fallire l'aggiornamento.

Uso:
    .venv/bin/python scripts/rain_gauges.py     # aggiorna l'archivio
"""

import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_PATH = ROOT / "data" / "rain_gauges.json"
WEB_PATH = ROOT / "web" / "data" / "rain_gauges.json"

# quanti giorni tenere in archivio. L'app guarda indietro 16 giorni
# (PAST_DAYS in fetch_weather_grid.py); 30 dà margine perché una rete che
# resta muta per qualche giorno non buchi la finestra
KEEP_DAYS = 30

# quanti giorni chiedere a ogni rete a ogni esecuzione. Più di quanti ne
# servano: se un'esecuzione salta, la successiva recupera il buco invece
# di lasciarlo lì
FETCH_DAYS = 8

TIMEOUT = 120

# Un "giorno di pioggia" qui è sempre un giorno ITALIANO, lo stesso su cui
# Open-Meteo somma la sua (timezone=auto). Senza fuso esplicito le
# conversioni userebbero quello della macchina: giusto su un portatile
# italiano, sbagliato di due ore sul runner a UTC che esegue
# l'aggiornamento notturno. Un errore invisibile in locale e presente solo
# in produzione, cioè il peggior tipo.
ROMA = ZoneInfo("Europe/Rome")


def _get(url, params=None, retries=3, base_delay=3, timeout=TIMEOUT, **kwargs):
    last = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last = e
            if attempt < retries - 1:
                time.sleep(base_delay * (attempt + 1))
    raise last


def _window():
    """I giorni COMPLETI da chiedere: oggi è ancora in corso, va escluso.

    Un totale giornaliero letto alle 7 del mattino non è il totale del
    giorno, è il totale di sette ore. Metterlo in archivio insieme ai
    giorni veri significherebbe far comparire un giorno quasi asciutto in
    mezzo a una settimana di pioggia.
    """
    # "oggi" è oggi in Italia, non sulla macchina che esegue: il lavoro
    # notturno gira su un runner a UTC, e vicino a mezzanotte le due date
    # non coincidono
    end = datetime.now(ROMA).date() - timedelta(days=1)
    return [end - timedelta(days=i) for i in range(FETCH_DAYS)]


# --------------------------------------------------------------------------
# Lombardia — ARPA Lombardia, portale open data regionale (Socrata).
# La rete più densa fra quelle disponibili: 256 pluviometri, un dato ogni
# 10 minuti, storico completo dal 2023. La somma giornaliera la fa il
# server (GROUP BY sul giorno) invece di scaricare 36.864 letture al
# giorno e sommarle qui.
# --------------------------------------------------------------------------

LOMBARDIA_SENSORS = "https://www.dati.lombardia.it/resource/nf78-nj6b.json"
LOMBARDIA_VALUES = "https://www.dati.lombardia.it/resource/647i-nhxk.json"


def fetch_lombardia(days):
    sensors = _get(
        LOMBARDIA_SENSORS,
        params={"$limit": 5000, "tipologia": "Precipitazione", "storico": "N"},
    ).json()
    meta = {}
    for s in sensors:
        try:
            meta[s["idsensore"]] = (float(s["lat"]), float(s["lng"]), s.get("nomestazione", ""))
        except (KeyError, TypeError, ValueError):
            continue
    if not meta:
        return []

    ids = ",".join(f"'{k}'" for k in meta)
    start = min(days).isoformat()
    # il limite superiore non e' un dettaglio: senza, la somma del giorno in
    # corso entrerebbe in archivio come se fosse un totale giornaliero, e
    # un'esecuzione delle 7 del mattino farebbe comparire un giorno quasi
    # asciutto in mezzo a una settimana di pioggia
    end = (max(days) + timedelta(days=1)).isoformat()
    # stato='VA' = lettura validata dall'agenzia. Le altre (mancante,
    # sospetta, in attesa di validazione) restano fuori: un pluviometro
    # guasto che segna 0 sarebbe peggio del modello
    where = (f"data >= '{start}T00:00:00' AND data < '{end}T00:00:00' "
             f"AND stato='VA' AND idsensore IN ({ids})")
    rows = _get(
        LOMBARDIA_VALUES,
        params={
            "$select": "idsensore,date_trunc_ymd(data) as d,sum(valore) as mm",
            "$group": "idsensore,d",
            "$limit": 50000,
            "$where": where,
        },
    ).json()

    daily = {}
    for r in rows:
        sid = r.get("idsensore")
        if sid not in meta:
            continue
        try:
            mm = float(r["mm"])
        except (KeyError, TypeError, ValueError):
            continue
        # il sensore rotto che spara valori negativi esiste davvero
        if mm < 0:
            continue
        daily.setdefault(sid, {})[r["d"][:10]] = round(mm, 1)

    out = []
    for sid, series in daily.items():
        lat, lon, name = meta[sid]
        out.append({"id": f"lom:{sid}", "name": name, "network": "ARPA Lombardia",
                    "lat": lat, "lon": lon, "daily": series})
    return out


# --------------------------------------------------------------------------
# Piemonte — Arpa Piemonte, API realtime pubblica (documentata su
# utility.arpa.piemonte.it/api_realtime/docs). Dà l'ora, non il giorno, e
# solo le ultime 72 ore: la somma giornaliera la facciamo qui e
# l'archivio si occupa della memoria lunga.
# --------------------------------------------------------------------------

PIEMONTE_ANAG = "https://utility.arpa.piemonte.it/api_realtime/pie_anag"
PIEMONTE_DATA = "https://utility.arpa.piemonte.it/api_realtime/data_pie"


def fetch_piemonte(days):
    anag = _get(PIEMONTE_ANAG).json()
    anag = anag.get("data", anag) if isinstance(anag, dict) else anag
    meta = {}
    for s in anag:
        # station_type è una stringa di lettere, una per strumento
        # installato: "P" è il pluviometro. Le stazioni senza P misurano
        # altro (livello dei fiumi, neve, vento) e non ci servono
        if "P" not in (s.get("station_type") or ""):
            continue
        try:
            meta[str(s["station_code"])] = (float(s["lat"]), float(s["lng"]), s.get("name", ""))
        except (KeyError, TypeError, ValueError):
            continue
    if not meta:
        return []

    wanted = {d.isoformat() for d in days}
    daily = {}
    page = 1
    while True:
        payload = _get(
            PIEMONTE_DATA,
            params={"date_from": min(days).isoformat(), "page": page, "page_size": 5000},
        ).json()
        rows = payload.get("data", [])
        for r in rows:
            code = str(r.get("station_code"))
            if code not in meta:
                continue
            mm = r.get("cum_rain_1h")
            if mm is None or mm < 0:
                continue
            # "2026-09-07T14:00:00+02:00" -> giorno locale. L'ora è già
            # locale nella risposta, quindi bastano i primi 10 caratteri
            day = (r.get("date") or "")[:10]
            if day not in wanted:
                continue
            bucket = daily.setdefault(code, {})
            bucket[day] = round(bucket.get(day, 0.0) + float(mm), 1)
        if page >= payload.get("total_pages", 1) or not rows:
            break
        page += 1
        time.sleep(0.3)

    out = []
    for code, series in daily.items():
        lat, lon, name = meta[code]
        out.append({"id": f"pie:{code}", "name": name, "network": "Arpa Piemonte",
                    "lat": lat, "lon": lon, "daily": series})
    return out


# --------------------------------------------------------------------------
# Trentino — Meteotrentino, servizio dati aperti provinciale. Qui i totali
# giornalieri arrivano già fatti (PrecTotale), e una singola richiesta ne
# restituisce diversi giorni insieme.
# --------------------------------------------------------------------------

TRENTINO_STATIONS = "https://dati.meteotrentino.it/service.asmx/listaStazioni"
TRENTINO_DAILY = "https://dati.meteotrentino.it/service.asmx/getValoriAggregatiGiornoJson"
_TN_NS = "{http://www.meteotrentino.it/}"


def fetch_trentino(days):
    xml = _get(TRENTINO_STATIONS).text
    root = ElementTree.fromstring(xml)
    meta = {}
    for node in root.findall(f"{_TN_NS}anagrafica"):
        def text(tag):
            el = node.find(f"{_TN_NS}{tag}")
            return el.text if el is not None else None
        code, lat, lon = text("codice"), text("latitudine"), text("longitudine")
        if not (code and lat and lon):
            continue
        try:
            meta[code] = (float(lat), float(lon), text("nome") or "")
        except ValueError:
            continue
    if not meta:
        return []

    wanted = {d.isoformat() for d in days}
    daily = {}
    # una richiesta copre una finestra di più giorni attorno alla data
    # chiesta, quindi bastano poche date distanziate per coprire tutto
    for anchor in sorted(wanted)[::4] + [max(wanted)]:
        try:
            payload = _get(TRENTINO_DAILY, params={"data": anchor}).json()
        except (requests.RequestException, ValueError):
            continue
        for r in payload.get("valoriAggregati", []):
            code = r.get("idstaz")
            day = r.get("giorno")
            mm = r.get("PrecTotale")
            if code not in meta or day not in wanted or mm is None or mm < 0:
                continue
            daily.setdefault(code, {})[day] = round(float(mm), 1)
        time.sleep(0.3)

    out = []
    for code, series in daily.items():
        lat, lon, name = meta[code]
        out.append({"id": f"tn:{code}", "name": name, "network": "Meteotrentino",
                    "lat": lat, "lon": lon, "daily": series})
    return out


# --------------------------------------------------------------------------
# Emilia-Romagna — Arpae, API pubblica del portale Allerta Meteo (la stessa
# che alimenta la mappa "Precipitazioni" del sito regionale).
#
# È l'unica delle reti collegate che non offre né il giorno né una serie:
# risponde con il cumulato di UN'ORA per tutte le stazioni a un istante
# dato. Il totale giornaliero va quindi ricomposto sommando 24 istantanee.
# Ne vale la pena: sono 296 pluviometri sull'Appennino, cioè la fascia
# dove i porcini si cercano davvero, e senza di loro tutta la dorsale da
# Piacenza a Rimini resterebbe sulla stima dei modelli.
# --------------------------------------------------------------------------

EMILIA_URL = "https://allertameteo.regione.emilia-romagna.it/o/api/allerta/get-sensor-values-no-time"
EMILIA_VAR = "1,0,3600/1,-,-,-/B13011"  # B13011 = precipitazione, cumulata su 3600s

# Quanti giorni chiedere: 24 richieste ciascuno, quindi il numero conta.
# Lo storico dell'endpoint si ferma comunque intorno alla settimana
# (verificato: a 9 giorni risponde ancora ma con tutti i valori vuoti), e
# l'archivio locale si occupa della memoria lunga.
EMILIA_DAYS = 4

def fetch_emilia(days):
    daily = {}
    meta = {}
    for day in sorted(days)[-EMILIA_DAYS:]:
        # Il totale del giorno è la somma di 24 istantanee: se anche una
        # sola non arriva, quel totale è più basso del vero. Un giorno di
        # pioggia sottostimato è esattamente l'errore che tutto questo
        # lavoro serve a togliere, quindi un giorno incompleto si butta e
        # resta ai modelli, invece di entrare in archivio come misura.
        ore_perse = False
        for hour in range(24):
            # il valore all'istante T è la pioggia dell'ora che finisce in T,
            # quindi l'ora 00:00-01:00 si chiede con T = 01:00
            when = datetime(day.year, day.month, day.day, hour, tzinfo=ROMA) + timedelta(hours=1)
            try:
                rows = _get(EMILIA_URL, params={
                    "variabile": EMILIA_VAR,
                    "time": int(when.timestamp() * 1000),
                }, retries=2).json()
            except (requests.RequestException, ValueError):
                ore_perse = True
                continue
            for r in rows:
                sid = r.get("idstazione")
                mm = r.get("value")
                if sid is None or mm is None or mm < 0:
                    continue
                if sid not in meta:
                    try:
                        # lat/lon arrivano come interi in centomillesimi di grado
                        meta[sid] = (int(r["lat"]) / 100000.0, int(r["lon"]) / 100000.0,
                                     r.get("nomestaz", ""))
                    except (KeyError, TypeError, ValueError):
                        continue
                bucket = daily.setdefault(sid, {})
                key = day.isoformat()
                bucket[key] = round(bucket.get(key, 0.0) + float(mm), 1)
        if ore_perse:
            key = day.isoformat()
            for bucket in daily.values():
                bucket.pop(key, None)
        time.sleep(0.2)

    out = []
    for sid, series in daily.items():
        if sid not in meta or not series:
            continue
        lat, lon, name = meta[sid]
        out.append({"id": f"er:{sid}", "name": name, "network": "Arpae Emilia-Romagna",
                    "lat": lat, "lon": lon, "daily": series})
    return out


# --------------------------------------------------------------------------
# Toscana — provata a fondo, NON collegata. Il Centro Funzionale Regionale
# pubblica 379 pluviometri e i dati ci sono, ma nessuna delle due strade
# porta a un totale giornaliero affidabile:
#
#   - actions.php?action=station&id=... dà la pioggia di ieri spezzata in
#     fasce di tre ore (CUM48_00_03 ... CUM48_21_24), che è esattamente
#     quello che serve. Verificato che l'interpretazione è giusta: le fasce
#     sommate tornano al totale dichiarato dal servizio e correlano a 0.87
#     con il modello negli stessi punti. Ma è una richiesta per stazione, e
#     il server risponde 429 ben prima di arrivare in fondo alle 379 —
#     anche rallentando a due richieste al secondo. Un aggiornamento
#     notturno che martella così un servizio pubblico non si fa comunque.
#
#   - actions.php?action=CUM24 e ?action=CUM48 danno tutte le stazioni in
#     una richiesta sola, ma su finestre che partono dalla mezzanotte di
#     ieri e dell'altroieri, non su un giorno solare. La differenza fra le
#     due DOVREBBE essere il totale dell'altroieri; non è stato possibile
#     verificarlo perché nel giorno del controllo era asciutto ovunque in
#     Toscana, e mettere in archivio come misura un'interpretazione non
#     verificata è il contrario di quello che fa questo file.
#
# Da riprendere dopo una giornata di pioggia diffusa: se la differenza fra
# le due cumulate risulta un totale giornaliero vero, la Toscana entra al
# costo di due richieste a notte.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Campania — Centro Funzionale Multirischi di Protezione Civile.
#
# 214 pluviometri fra Matese, Picentini, Cilento e Partenio. È l'unica
# rete del sud che risponde con dati interrogabili, e porta l'app dove
# finora non arrivava nulla.
#
# Qui la serie oraria c'è davvero, due giorni pieni più il giorno in corso,
# quindi il totale giornaliero si costruisce sommando le ore.
# --------------------------------------------------------------------------

CAMPANIA_BASE = "https://centrofunzionale.regione.campania.it/CentroFunzionalePortaleRest/rest/recuperodati"

# "Pluviometro Areale" non è uno strumento: è una media calcolata su un
# bacino. Interpolarla insieme alle misure vere significherebbe dare peso
# di misura a un numero che è già una stima di qualcun altro.
CAMPANIA_TIPO = "Pluviometro Puntuale"


def fetch_campania(days):
    stazioni = _get(f"{CAMPANIA_BASE}/stazioniByFilters").json().get("listaStazioni", [])
    coords = {}
    for st in stazioni:
        try:
            # i decimali arrivano con la virgola, non con il punto
            coords[str(st["idStazione"])] = (
                float(str(st["latitudine"]).replace(",", ".")),
                float(str(st["longitudine"]).replace(",", ".")),
                st.get("denominazione", ""),
            )
        except (KeyError, TypeError, ValueError):
            continue

    sensori = [s for s in _get(f"{CAMPANIA_BASE}/sensoriByFilters").json()
               if s.get("tipoSensore") == CAMPANIA_TIPO and s.get("attivo")]

    wanted = {d.isoformat() for d in days}
    out = []
    for sensore in sensori:
        key = str(sensore.get("idStazione"))
        if key not in coords:
            continue
        try:
            # come per le altre reti interrogate sensore per sensore: tetto
            # stretto, perche' 214 richieste con il timeout generale da due
            # minuti possono trasformare una rete lenta in un aggiornamento
            # notturno che non finisce. Un sensore che tarda si salta.
            data = _get(f"{CAMPANIA_BASE}/temporeale/{sensore['idSensore']}",
                        retries=2, base_delay=1, timeout=15).json().get("data", [])
        except (requests.RequestException, ValueError):
            continue
        per_day = {}
        ore = {}
        for row in data:
            stamp = (row.get("timestamp") or "")[:10]
            value = row.get("value")
            if stamp not in wanted or value is None or value < 0:
                continue
            per_day[stamp] = round(per_day.get(stamp, 0.0) + float(value), 1)
            ore[stamp] = ore.get(stamp, 0) + 1
        # solo i giorni con tutte e 24 le ore: il giorno in corso e quelli
        # a cui manca qualche lettura darebbero un totale sottostimato
        per_day = {d: mm for d, mm in per_day.items() if ore.get(d) == 24}
        if not per_day:
            continue
        lat, lon, name = coords[key]
        out.append({"id": f"cam:{sensore['idSensore']}", "name": name,
                    "network": "Centro Funzionale Campania",
                    "lat": lat, "lon": lon, "daily": per_day})
        time.sleep(0.05)
    return out


PROVIDERS = [
    ("Lombardia", fetch_lombardia),
    ("Piemonte", fetch_piemonte),
    ("Trentino", fetch_trentino),
    ("Emilia-Romagna", fetch_emilia),
    ("Campania", fetch_campania),
]


# --------------------------------------------------------------------------
# Quando i pluviometri non arrivano: tre modelli invece di uno
#
# Gran parte dell'Italia non ha una rete pluviometrica interrogabile. Lì la
# pioggia resta calcolata, ma non da un modello solo.
# --------------------------------------------------------------------------

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


# Tre modelli meteo di tre centri diversi (Reading, Offenbach, Exeter), non
# tre versioni dello stesso. Prima si usava il solo "best_match" di
# Open-Meteo: sui 256 pluviometri lombardi dava +25% di acqua in eccesso,
# perché ogni modello globale tende a inventare pioviggine dove non c'è.
# Modelli indipendenti sbagliano in modo scorrelato, quindi la loro media
# si avvicina al vero: sulla pioggia dei 7 giorni l'errore medio scende da
# 15.0 a 11.7 mm e i falsi positivi dal 7.2% al 2.5%.
#
# Sono tre e non quattro perché il quarto peggiorava. Provate tutte le
# combinazioni dei quattro disponibili contro i pluviometri: aggiungere
# GFS (Washington) a questi tre porta l'errore da 11.7 a 11.9 mm e i falsi
# positivi dal 2.5% al 2.9%. Un modello in più non è automaticamente
# meglio, e questo costa anche un quarto di richieste in più.
#
# Costano una richiesta sola: Open-Meteo accetta più modelli insieme e
# restituisce una serie per ciascuno.
PRECIP_MODELS = ["ecmwf_ifs025", "icon_seamless", "ukmo_seamless"]


def fetch_precip_models_batch(points, past_days):
    """Solo la pioggia, ma da tutti i modelli in una richiesta.

    Restituisce, per ogni punto, una lista di serie giornaliere (una per
    modello) da dare in pasto a blend_model_precip.
    """
    params = {
        "latitude": ",".join(str(p[0]) for p in points),
        "longitude": ",".join(str(p[1]) for p in points),
        "daily": "precipitation_sum",
        "models": ",".join(PRECIP_MODELS),
        "past_days": past_days,
        "forecast_days": 1,
        "timezone": "auto",
    }
    resp = requests.get(FORECAST_URL, params=params, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        data = [data]
    out = []
    for payload in data:
        daily = payload.get("daily", {})
        out.append({
            "dates": daily.get("time", []),
            "series": [daily.get(f"precipitation_sum_{m}") or [] for m in PRECIP_MODELS],
        })
    return out


def blend_model_precip(series_per_model, n_days):
    """Media dei modelli, ridotta da quanti sono d'accordo che piova.

    Un giorno su cui tutti i modelli mettono pioggia è quasi sempre un
    giorno di pioggia vera; uno su cui la mette un modello solo è quasi
    sempre la pioviggine inventata da quel modello. Moltiplicare
    la media per la frazione di modelli concordi tiene interi i primi e
    riduce i secondi, invece di scegliere fra credere a tutti o a nessuno.
    Provate anche mediana, minimo e soglie secche di accordo: questa dà
    l'errore più basso (11.7 mm sui 7 giorni) tenendo i falsi positivi al
    2.5% contro il 7.2% del modello singolo.
    """
    blended = []
    for i in range(n_days):
        vals = []
        for serie in series_per_model:
            v = serie[i] if i < len(serie) else None
            if v is not None:
                vals.append(float(v))
        if not vals:
            blended.append(None)
            continue
        agreement = sum(1 for v in vals if v >= 1.0) / len(vals)
        blended.append((sum(vals) / len(vals)) * agreement)
    return blended


# --------------------------------------------------------------------------
# Da pluviometri sparsi a pioggia in un punto qualsiasi
# --------------------------------------------------------------------------

# Oltre questa distanza un pluviometro non racconta più la pioggia del
# punto che stiamo guardando. Provati 15, 25 e 40 km: sotto i 25 si perde
# copertura senza guadagnare precisione, sopra si comincia a importare la
# pioggia di un'altra valle. A 25 km l'errore sulla pioggia dei 7 giorni
# è 7.4 mm contro i 18.2 della vecchia griglia sul modello.
GAUGE_MAX_KM = 25.0

# Quanti pluviometri mediare. Con 3 l'errore è già al minimo; aggiungerne
# altri allarga solo il raggio medio e riporta dentro pioggia lontana.
GAUGE_K = 3

# Sotto questo peso la stima dei pluviometri conta troppo poco perché la
# cella possa dirsi "misurata": resta nel calcolo, ma la provenienza
# mostrata all'utente dice onestamente che è ancora il modello.
MIN_MEASURED_WEIGHT = 0.1


def _km(lat1, lon1, lat2, lon2):
    """Distanza piana, sufficiente sotto i 100 km e molto più veloce."""
    return 111.0 * math.hypot(lat1 - lat2, (lon1 - lon2) * math.cos(math.radians(lat1)))


class GaugeField:
    """I pluviometri dell'archivio, interrogabili per punto e giorno.

    Interpola con pesi 1/distanza²: il pluviometro a 3 km conta cento
    volte quello a 30. È l'inverse distance weighting classico, scelto
    dopo averlo verificato togliendo a turno una stazione e provando a
    indovinarla dalle vicine (7.3% di falsi positivi contro il 12.8% del
    modello, errore medio 1.6 mm al giorno contro 3.5).
    """

    def __init__(self, stations):
        self.points = [
            (s["lat"], s["lon"], s["daily"])
            for s in stations.values()
            if s.get("lat") is not None and s.get("lon") is not None
        ]

    def __len__(self):
        return len(self.points)

    def nearby(self, lat, lon):
        """I pluviometri entro il raggio utile, dal più vicino."""
        found = []
        for glat, glon, daily in self.points:
            # scarto grossolano prima della distanza vera: senza, ogni
            # cella confronterebbe centinaia di stazioni una per una
            if abs(glat - lat) > 0.3 or abs(glon - lon) > 0.4:
                continue
            d = _km(lat, lon, glat, glon)
            if d <= GAUGE_MAX_KM:
                found.append((d, daily))
        found.sort(key=lambda x: x[0])
        return found

    def estimate(self, nearby, day):
        """mm misurati in quel giorno, o None se nessuno ha misurato."""
        num = den = 0.0
        used = 0
        for dist, daily in nearby:
            mm = daily.get(day)
            if mm is None:
                continue
            w = 1.0 / max(dist, 1.0) ** 2
            num += w * mm
            den += w
            used += 1
            if used >= GAUGE_K:
                break
        if not den:
            return None, 0
        return num / den, used


def blend(model_mm, gauge_mm, gauge_count, nearest_km):
    """Quanto credere ai pluviometri e quanto al modello.

    Non è un aut-aut: un solo pluviometro a 24 km è un indizio, tre a 5 km
    sono una misura. Il peso cresce con la vicinanza e con quanti sono, e
    quando non ce n'è nessuno resta il modello — che è il caso di gran
    parte dell'Italia, dove le reti regionali non pubblicano dati aperti.
    """
    if gauge_mm is None or nearest_km is None:
        return model_mm, 0.0
    closeness = max(0.0, min(1.0, 1.0 - nearest_km / GAUGE_MAX_KM))
    weight = 0.5 + 0.5 * closeness if gauge_count >= 2 else 0.4 * closeness
    if model_mm is None:
        return gauge_mm, 1.0
    return weight * gauge_mm + (1.0 - weight) * model_mm, weight


def apply_gauges(lat, lon, dates, model_precip, gauges):
    """Sostituisce la pioggia calcolata con quella misurata, dove esiste.

    Restituisce la serie corretta e la provenienza, che finisce nelle
    proprietà della cella: la mappa deve poter distinguere "qui la pioggia
    l'ha misurata un pluviometro a 4 km" da "qui è la media di tre
    modelli", perché sono due gradi di fiducia diversi e chi decide dove
    andare a funghi ha il diritto di sapere quale sta guardando.
    """
    nearby = gauges.nearby(lat, lon) if len(gauges) else []
    if not nearby:
        return model_precip, {"rain_source": "modello", "rain_gauge_count": 0, "rain_gauge_km": None}

    nearest_km = round(nearby[0][0], 1)
    corrected = []
    measured_days = 0
    max_count = 0
    for i, day in enumerate(dates):
        model_mm = model_precip[i] if i < len(model_precip) else None
        gauge_mm, count = gauges.estimate(nearby, day)
        value, weight = blend(model_mm, gauge_mm, count, nearest_km)
        # un solo pluviometro al limite dei 25 km pesa quasi zero: contarlo
        # come "misurato" farebbe dire alla mappa che quella pioggia è
        # stata vista da uno strumento quando in pratica è ancora il modello
        if weight >= MIN_MEASURED_WEIGHT:
            measured_days += 1
            max_count = max(max_count, count)
        corrected.append(value)

    # "misto" non è un ripiego: sono i giorni più vecchi della finestra,
    # entrati in archivio prima che la rete regionale fosse collegata, che
    # restano sul modello mentre i recenti sono misurati
    if measured_days == 0:
        source = "modello"
    elif measured_days >= len(dates) - 1:
        source = "pluviometri"
    else:
        source = "misto"
    return corrected, {
        "rain_source": source,
        "rain_gauge_count": max_count,
        "rain_gauge_km": nearest_km if measured_days else None,
    }


def load_field():
    """L'archivio pronto da interrogare; vuoto se non è ancora stato creato."""
    return GaugeField(load_archive())


def load_archive():
    if not ARCHIVE_PATH.exists():
        return {}
    try:
        data = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("stations", {})


def collect(verbose=True):
    """Scarica da tutte le reti e fonde con l'archivio già raccolto."""
    days = _window()
    wanted = {d.isoformat() for d in days}
    stations = load_archive()
    fetched_before = sum(len(s.get("daily", {})) for s in stations.values())

    for label, fn in PROVIDERS:
        try:
            found = fn(days)
        except Exception as e:  # noqa: BLE001 — una rete giù non deve fermare le altre
            if verbose:
                print(f"  {label}: non disponibile ({type(e).__name__}: {e}), resta il modello")
            continue
        for st in found:
            # rete di sicurezza: un provider che restituisse il giorno in
            # corso, o un giorno piu' vecchio della finestra, non deve
            # poterlo far entrare in archivio
            st["daily"] = {d: mm for d, mm in st["daily"].items() if d in wanted}
            if not st["daily"]:
                continue
            entry = stations.setdefault(
                st["id"],
                {"name": st["name"], "network": st["network"], "lat": st["lat"], "lon": st["lon"], "daily": {}},
            )
            # coordinate e nome possono essere corretti dall'agenzia nel
            # tempo: vince sempre l'ultima versione scaricata
            entry["name"], entry["network"] = st["name"], st["network"]
            entry["lat"], entry["lon"] = st["lat"], st["lon"]
            entry["daily"].update(st["daily"])
        if verbose:
            got = sum(len(s["daily"]) for s in found)
            print(f"  {label}: {len(found)} pluviometri, {got} totali giornalieri")

    cutoff = (datetime.now(ROMA).date() - timedelta(days=KEEP_DAYS)).isoformat()
    # il limite superiore ripulisce anche l'archivio gia' scritto: un
    # giorno in corso finito dentro da un'esecuzione precedente resterebbe
    # li' per sempre come totale sbagliato
    latest = max(days).isoformat()
    for st in stations.values():
        st["daily"] = {d: mm for d, mm in st["daily"].items() if cutoff <= d <= latest}
    # una stazione dismessa smette di ricevere giorni: quando l'ultimo esce
    # dalla finestra sparisce da sola, senza bisogno di manutenzione
    stations = {k: v for k, v in stations.items() if v["daily"]}

    if verbose:
        total = sum(len(s["daily"]) for s in stations.values())
        delta = total - fetched_before
        print(f"\nArchivio: {len(stations)} pluviometri, {total} totali giornalieri "
              f"({delta:+d} rispetto a prima)")
    return stations


def save(stations):
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "stations": stations,
    }
    for path in (ARCHIVE_PATH, WEB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    print(f"File: {ARCHIVE_PATH}")
    print(f"File: {WEB_PATH}")


def main():
    print("Pluviometri reali: aggiorno l'archivio...")
    save(collect())


if __name__ == "__main__":
    main()
