"""
Backtest del modello di prontezza sui ritrovamenti reali.

Il problema che risolve: fino a oggi non esisteva modo di sapere se una
modifica al modello lo migliorasse o lo peggiorasse. Le soglie e le curve
erano stime ragionate, e ogni variabile aggiunta era un atto di fede.

Come misura. Ogni ritrovamento GBIF/iNaturalist ha data e coordinate. Per
ognuno si interroga l'archivio meteo storico di Open-Meteo e si calcola il
punteggio che il sito avrebbe mostrato IN QUEL PUNTO E IN QUEL GIORNO. Poi
si calcola lo stesso punteggio NELLO STESSO PUNTO ma in giorni di controllo
(+/-70 giorni). Se il modello vale qualcosa, il giorno del
ritrovamento vero deve ottenere un punteggio più alto dei giorni di
controllo. La metrica è l'AUC: la probabilità che un ritrovamento vero
prenda un punteggio più alto di un controllo preso a caso. 0.5 = il modello
non sa niente; 1.0 = separazione perfetta.

Cosa misura e cosa NON misura. Caso e controlli condividono il luogo,
quindi bosco, quota e pH sono identici e si annullano: questo backtest
misura la parte TEMPORALE del modello (finestra di pioggia, temperatura di
incubazione, evaporazione, stagione, temperatura del suolo). Per validare i
fattori di habitat servirebbero veri assenti, cioè punti dove si è cercato
senza trovare, e i dati citizen-science non li registrano.

Le curve stagionali si costruiscono su una parte dei ritrovamenti e la
misura si fa sull'altra: costruirle su tutti e poi misurare su quegli
stessi darebbe un punteggio gonfiato che premia la memoria, non la
previsione.

Uso:
    .venv/bin/python scripts/backtest_model.py [--sample 300]
"""

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

ROOT = Path(__file__).resolve().parent.parent
OCC_PATH = ROOT / "web" / "data" / "occurrences.geojson"
FINE_PATH = ROOT / "web" / "data" / "vegetation_fine.geojson"
PH_PATH = ROOT / "web" / "data" / "soil_ph.json"
SCORER = ROOT / "scripts" / "score_cases.js"

HISTORY_DAYS = 17  # come POPUP_RAIN_DAYS nel modello
# Open-Meteo pesa le richieste per quantità di dati, non per numero: con
# +/-120 giorni e quattro variabili orarie il backtest prendeva 429 dopo
# poche decine di punti. +/-70 giorni bastano per avere controlli in
# stagioni diverse dal ritrovamento, e dimezzano l'arco richiesto.
CONTROL_OFFSETS = (-70, 70)
TRAIN_FRACTION = 0.7
FINE_GRID_STEP_DEG = 0.15  # come nel frontend
PH_KEY_ROUND_DEG = 0.01

# Solo ciò che speciesScore usa davvero. Umidità dell'aria e umidità del
# suolo entrano in conditionsQuality, che pesa i colori della mappa ma non
# il punteggio della singola specie: chiederle qui sarebbe peso di richiesta
# pagato per dati che il backtest non guarda.
DAILY_VARS = "precipitation_sum,temperature_2m_mean,et0_fao_evapotranspiration"
HOURLY_VARS = "soil_temperature_6cm"

# l'archivio Open-Meteo si ferma qualche giorno prima di oggi
ARCHIVE_LAG_DAYS = 7


def fine_cell_key(lat, lon):
    return f"{int(round(lat / FINE_GRID_STEP_DEG))}_{int(round(lon / FINE_GRID_STEP_DEG))}"


def load_context():
    occ = json.loads(OCC_PATH.read_text(encoding="utf-8"))["features"]

    veg_by_cell = {}
    if FINE_PATH.exists():
        for f in json.loads(FINE_PATH.read_text(encoding="utf-8"))["features"]:
            veg_by_cell[f["properties"]["cell_key"]] = f["properties"]

    ph_by_point = {}
    if PH_PATH.exists():
        ph_by_point = json.loads(PH_PATH.read_text(encoding="utf-8")).get("ph_by_point", {})
    return occ, veg_by_cell, ph_by_point


def ph_at(ph_by_point, lat, lon):
    r = PH_KEY_ROUND_DEG
    return ph_by_point.get(f"{round(lat / r) * r:.2f},{round(lon / r) * r:.2f}")


def fetch_archive(lat, lon, start, end):
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": DAILY_VARS,
        "hourly": HOURLY_VARS,
        "timezone": "auto",
    }
    for attempt in range(4):
        resp = requests.get(ARCHIVE_URL, params=params, timeout=90)
        # 429 = quota per minuto esaurita, non un errore permanente: qui
        # conviene aspettare, perché saltare il punto falserebbe il campione
        if resp.status_code == 429:
            time.sleep(20 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def slice_env(archive, target_day, veg, ph):
    """L'env che il modello riceverebbe se 'oggi' fosse target_day."""
    daily = archive.get("daily", {})
    dates = daily.get("time") or []
    if target_day.isoformat() not in dates:
        return None
    end = dates.index(target_day.isoformat())
    start = end - HISTORY_DAYS + 1
    if start < 0:
        return None

    def dseries(key):
        return [v if v is not None else 0.0 for v in daily.get(key, [])[start : end + 1]]

    # le serie orarie coprono lo stesso arco: l'ultimo valore del giorno
    # target è quello che il sito leggerebbe come "adesso"
    hourly = archive.get("hourly", {})
    h_end = (end + 1) * 24 - 1

    def hlast(key):
        series = hourly.get(key) or []
        window = [v for v in series[max(0, h_end - 23) : h_end + 1] if v is not None]
        return window[-1] if window else None

    return {
        "dates": dates[start : end + 1],
        "precip": dseries("precipitation_sum"),
        "temp": dseries("temperature_2m_mean"),
        "et0": dseries("et0_fao_evapotranspiration"),
        "soilTempC": hlast("soil_temperature_6cm"),
        "vegClass": (veg or {}).get("veg_class"),
        "elevation": (veg or {}).get("elevation_m"),
        "ph": ph,
    }


def matched_auc(rows, key):
    """AUC APPAIATA: ogni ritrovamento vero contro i SUOI controlli, cioè
    contro altri giorni nello stesso identico punto.

    Confrontare tutti i veri contro tutti i controlli in un unico calderone
    darebbe un numero diverso e più lusinghiero per il motivo sbagliato:
    misurerebbe anche quanto i luoghi differiscono fra loro (bosco, quota,
    pH), che qui non è in discussione perché caso e controllo lo
    condividono. Appaiando, resta solo la domanda che interessa: il modello
    riconosce il GIORNO giusto in un posto dato?
    """
    by_occurrence = {}
    for row in rows:
        occ = row["id"].split(":")[0]
        by_occurrence.setdefault(occ, {"reale": [], "controllo": []})
        by_occurrence[occ][row["label"]].append(row[key])

    wins = 0.0
    pairs = 0
    for groups in by_occurrence.values():
        for p in groups["reale"]:
            for n in groups["controllo"]:
                wins += 1.0 if p > n else 0.5 if p == n else 0.0
                pairs += 1
    return (wins / pairs, len(by_occurrence)) if pairs else (None, 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=250, help="ritrovamenti da testare")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--cerca-pesi",
        action="store_true",
        help="prova griglie di esponenti sui fattori temporali e riporta i migliori",
    )
    parser.add_argument(
        "--cache",
        default=str(ROOT / "backtest_cases.json"),
        help="dove tenere i casi già scaricati (lo scaricamento è la parte lenta)",
    )
    parser.add_argument("--riscarica", action="store_true", help="ignora la cache dei casi")
    args = parser.parse_args()

    random.seed(args.seed)
    occ, veg_by_cell, ph_by_point = load_context()

    dated = []
    for f in occ:
        iso = (f["properties"].get("eventDate") or "")[:10]
        if len(iso) != 10:
            continue
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        lon, lat = f["geometry"]["coordinates"]
        dated.append({"species": f["properties"]["species"], "date": d, "lat": lat, "lon": lon})

    # niente ritrovamenti così recenti che i controlli in avanti
    # cadrebbero fuori dall'archivio
    latest = date.today() - timedelta(days=ARCHIVE_LAG_DAYS + max(CONTROL_OFFSETS))
    dated = [d for d in dated if d["date"] <= latest]
    random.shuffle(dated)

    split = int(len(dated) * TRAIN_FRACTION)
    train, test = dated[:split], dated[split:]
    sample = test[: args.sample]
    print(f"{len(dated)} ritrovamenti datati: {len(train)} per costruire le curve stagionali, {len(sample)} da testare.")

    train_occ = [{"properties": {"species": d["species"], "eventDate": d["date"].isoformat()}} for d in train]

    cache_path = Path(args.cache)
    if cache_path.exists() and not args.riscarica:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("seed") == args.seed and cached.get("sample") == args.sample:
            print(f"Riuso {len(cached['cases'])} casi già scaricati da {cache_path.name} (--riscarica per rifarli).")
            run_report(cached["cases"], cached["trainOccurrences"], args)
            return

    cases = []
    for i, occurrence in enumerate(sample, 1):
        lat, lon, day = occurrence["lat"], occurrence["lon"], occurrence["date"]
        veg = veg_by_cell.get(fine_cell_key(lat, lon))
        ph = ph_at(ph_by_point, lat, lon)
        span_start = day + timedelta(days=min(CONTROL_OFFSETS) - HISTORY_DAYS)
        span_end = day + timedelta(days=max(CONTROL_OFFSETS))
        try:
            archive = fetch_archive(lat, lon, span_start, span_end)
        except requests.RequestException as e:
            print(f"  archivio non disponibile per {lat},{lon}: {e}")
            continue

        for offset in (0,) + CONTROL_OFFSETS:
            target = day + timedelta(days=offset)
            env = slice_env(archive, target, veg, ph)
            if env is None:
                continue
            cases.append(
                {
                    "id": f"{i}:{offset}",
                    "species": occurrence["species"],
                    "label": "reale" if offset == 0 else "controllo",
                    "date": target.isoformat(),
                    "env": env,
                }
            )
        if i % 25 == 0 or i == len(sample):
            print(f"  {i}/{len(sample)} ritrovamenti scaricati ({len(cases)} casi)...")
        time.sleep(1.0)

    if not cases:
        print("Nessun caso costruito, mi fermo.", file=sys.stderr)
        return

    cache_path.write_text(
        json.dumps({"seed": args.seed, "sample": args.sample, "trainOccurrences": train_occ, "cases": cases}),
        encoding="utf-8",
    )
    run_report(cases, train_occ, args)


def score(cases, train_occ, weightings=None):
    """Passa i casi al modello vero (web/model.js via Node) e riprende i punteggi."""
    payload = {"trainOccurrences": train_occ, "cases": cases}
    if weightings:
        payload["weightings"] = weightings
    tmp_in = ROOT / ".backtest_in.json"
    tmp_out = ROOT / ".backtest_out.json"
    tmp_in.write_text(json.dumps(payload), encoding="utf-8")
    subprocess.run(["node", str(SCORER), str(tmp_in), str(tmp_out)], check=True, capture_output=True)
    scored = json.loads(tmp_out.read_text(encoding="utf-8"))
    tmp_in.unlink()
    tmp_out.unlink()
    return scored


def search_weights(cases, train_occ):
    """Cerca gli esponenti dei fattori TEMPORALI che separano meglio.

    Onestà sul metodo: la griglia si sceglie su metà dei ritrovamenti e si
    verifica sull'altra metà, mai sulla stessa. Con 250 ritrovamenti e
    qualche centinaio di combinazioni, sceglierli e giudicarli sugli stessi
    dati darebbe un numero gonfio che non si ripeterebbe sul campo.

    I fattori di LUOGO (bosco, quota, pH) non compaiono: il backtest
    confronta giorni diversi nello stesso punto, dove si annullano. I loro
    pesi restano ragionati, non misurati, e il codice lo dichiara.
    """
    levels = [0.25, 0.5, 0.75, 1.0]
    weightings = []
    for season in levels:
        for temp in levels:
            for retention in [0.25, 0.5, 1.0]:
                for soil in [0.25, 1.0]:
                    name = f"s{season}_t{temp}_r{retention}_st{soil}"
                    weightings.append(
                        {
                            "name": name,
                            "weights": {
                                "rain": 1,
                                "tree": 1,
                                "ph": 0.3,
                                "season": season,
                                "temp": temp,
                                "retention": retention,
                                "soilTemp": soil,
                            },
                        }
                    )
    print(f"Provo {len(weightings)} combinazioni di esponenti...")

    occ_ids = sorted({c["id"].split(":")[0] for c in cases}, key=int)
    half = len(occ_ids) // 2
    fit_ids, check_ids = set(occ_ids[:half]), set(occ_ids[half:])
    fit = [c for c in cases if c["id"].split(":")[0] in fit_ids]
    check = [c for c in cases if c["id"].split(":")[0] in check_ids]

    by_name = {w["name"]: w["weights"] for w in weightings}
    scored_fit = score(fit, train_occ, weightings)
    scored_check = score(check, train_occ, weightings)
    rows = []
    for name in by_name:
        rows.append((matched_auc_key(scored_fit, name)[0], matched_auc_key(scored_check, name)[0], name))
    rows.sort(reverse=True)

    def label(name):
        w = by_name[name]
        return f"stagione {w['season']:.2f}  temp {w['temp']:.2f}  evap {w['retention']:.2f}  suolo {w['soilTemp']:.2f}"

    print(f"\nMigliori 10 sulla metà di scelta ({len(fit_ids)} ritrovamenti, verifica su {len(check_ids)}):")
    print(f"  {'esponenti':<48}{'scelta':>9}{'verifica':>10}")
    for auc_fit, auc_check, name in rows[:10]:
        print(f"  {label(name):<48}{auc_fit:>9.3f}{auc_check:>10.3f}")

    # Quanto conta davvero ogni scelta: se lo scarto fra il migliore e il
    # peggiore di un fattore è dentro il rumore, quel peso non è misurato
    # e sceglierlo al terzo decimale è illudersi.
    print("\nSensibilità di ciascun esponente (AUC sulla metà di verifica):")
    for factor in ["season", "temp", "retention", "soilTemp"]:
        vals = sorted({by_name[n][factor] for n in by_name})
        line = []
        for v in vals:
            aucs = [c for _, c, n in rows if by_name[n][factor] == v]
            line.append(f"{v:.2f}:{max(aucs):.3f}")
        print(f"  {factor:<12}{'   '.join(line)}")

    best = by_name[rows[0][2]]
    print(f"\nMigliore sulla metà di scelta: {best}")
    print(f"AUC sulla metà di verifica: {rows[0][1]:.3f}")
    return best


def matched_auc_key(rows, key):
    """Come matched_auc ma leggendo rows[i]['scores'][key]."""
    flat = [{**r, "_v": r["scores"][key]} for r in rows]
    return matched_auc(flat, "_v")


def run_report(cases, train_occ, args):
    if args.cerca_pesi:
        search_weights(cases, train_occ)
        return
    scored = score(cases, train_occ)

    print("\n" + "=" * 70)
    print("AUC appaiata — probabilità che il giorno del ritrovamento vero batta")
    print("un giorno di controllo NELLO STESSO PUNTO. 0.50 = il modello non sa nulla.")
    print("=" * 70)
    print(f"{'specie':<20}{'ritrov.':>9}{'modello vecchio':>18}{'modello nuovo':>16}{'':>7}")

    by_species = {}
    for row in scored:
        by_species.setdefault(row["species"], []).append(row)

    for sp, rows in sorted(by_species.items()):
        old_auc, n_old = matched_auc(rows, "scoreOld")
        new_auc, _ = matched_auc(rows, "scoreNew")
        if old_auc is None or new_auc is None:
            continue
        print(f"{sp:<20}{n_old:>9}{old_auc:>18.3f}{new_auc:>16.3f}{new_auc - old_auc:>+7.3f}")

    old_auc, n_occ = matched_auc(scored, "scoreOld")
    new_auc, _ = matched_auc(scored, "scoreNew")
    print("-" * 70)
    print(f"{'TUTTE':<20}{n_occ:>9}{old_auc:>18.3f}{new_auc:>16.3f}{new_auc - old_auc:>+7.3f}")

    delta = new_auc - old_auc
    verdict = "il modello nuovo separa meglio" if delta > 0.01 else "peggio" if delta < -0.01 else "nessuna differenza"
    print(f"\n{verdict} ({delta:+.3f})")

    # Quanto lavora ciascun fattore nuovo. Serve la colonna dei controlli,
    # non solo quella dei ritrovamenti veri: un fattore che vale 1.00 sui
    # veri sembrerebbe inutile, mentre se sui controlli scende è proprio lì
    # che sta separando. Utile davvero è solo il fattore con uno SCARTO fra
    # le due colonne; uno che vale 1.00 in entrambe non sta facendo niente e
    # andrebbe tolto invece di restare come complicazione gratuita.
    # SOGLIE DEGLI ALERT, calibrate su cosa succede DAVVERO dove i funghi
    # sono stati trovati. Fino a ieri erano numeri scelti guardando la
    # distribuzione sulla griglia e chiedendosi quante celle finissero in
    # verde: un criterio estetico. Qui invece "pronto" significa una cosa
    # verificabile — le condizioni assomigliano a quelle in cui la gente
    # questo fungo lo ha trovato per davvero.
    real_scores = sorted(r["scoreNew"] for r in scored if r["label"] == "reale")
    if real_scores:
        def pct(p):
            return real_scores[min(len(real_scores) - 1, int(p * len(real_scores)))]

        print("\nSoglie suggerite per gli alert, dai punteggi dei ritrovamenti veri:")
        print(f"  mediana dei ritrovamenti      {pct(0.50):.3f}   -> soglia \"pronto\"")
        print(f"  primo quartile                {pct(0.25):.3f}   -> soglia \"in arrivo\"")
        print(f"  decimo percentile             {pct(0.10):.3f}")
        ctrl = sorted(r["scoreNew"] for r in scored if r["label"] == "controllo")
        if ctrl:
            print(f"  (mediana dei controlli        {ctrl[len(ctrl) // 2]:.3f})")

    real = [r for r in scored if r["label"] == "reale"]
    controls = [r for r in scored if r["label"] == "controllo"]
    if real and controls:
        print("\nQuanto lavora ogni fattore nuovo (1.00 = non penalizza mai):")
        print(f"  {'fattore':<28}{'ritrovamenti':>13}{'controlli':>11}{'scarto':>9}")
        for key, name in [
            ("tempFactor", "temperatura incubazione"),
            ("retention", "acqua non evaporata"),
            ("season", "stagione"),
            ("soilTempFactor", "temperatura del suolo"),
            ("phFactor", "pH"),
        ]:
            hits = [r[key] for r in real if r.get(key) is not None]
            miss = [r[key] for r in controls if r.get(key) is not None]
            if not hits or not miss:
                continue
            m_hit = sum(hits) / len(hits)
            m_miss = sum(miss) / len(miss)
            print(f"  {name:<28}{m_hit:>13.2f}{m_miss:>11.2f}{m_hit - m_miss:>+9.2f}")


if __name__ == "__main__":
    main()
