/* Modello di prontezza dei funghi — la parte scientifica, senza UI.
 *
 * Sta in un file a sé perché scripts/backtest_model.py lo esegue con Node
 * per validarlo sui ritrovamenti reali: se il backtest girasse su una copia
 * riscritta in Python misurerebbe la copia, non il sito. Qui misura
 * esattamente le funzioni che colorano la mappa e riempiono il popup.
 *
 * Caricato come script classico prima di app.js (niente moduli: il sito è
 * statico su GitHub Pages e app.js usa gli stessi nomi globali), e
 * esportato via module.exports quando gira sotto Node.
 *
 * Ogni fattore vale 1 — neutro — quando il dato manca: un fetch fallito è
 * ignoranza nostra, non un'informazione sfavorevole sul posto.
 */

// Affinità di base delle specie per ciascun tipo (il porcino dei pini vive
// con le conifere, l'ovolo quasi solo con latifoglie calde, ecc.).
const SPECIES_VEG_AFFINITY = {
  porcino_comune: { broadleaf: 0.85, conifer: 0.65, mixed: 1.0, shrub: 0.2, none: 0.05 },
  porcino_pini: { broadleaf: 0.15, conifer: 0.95, mixed: 0.6, shrub: 0.15, none: 0.05 },
  ovolo: { broadleaf: 1.0, conifer: 0.05, mixed: 0.15, shrub: 0.1, none: 0.05 },
  gallinaccio: { broadleaf: 0.85, conifer: 0.55, mixed: 0.85, shrub: 0.3, none: 0.05 },
};

// Corine distingue il tipo di bosco (latifoglie/conifere/misto) ma non la
// quota a cui si trova: due celle "latifoglie" possono essere un querceto
// mediterraneo a 200m o una faggeta a 1400m, con specie diverse. Questa è
// una correzione SECONDARIA (una gaussiana larga sulla quota tipica della
// specie) che affina l'affinità reale, senza mai ribaltarla: un pavimento
// di 0.4 evita che la sola quota azzeri un'affinità di vegetazione reale.
const SPECIES_ELEVATION_PREF = {
  porcino_comune: { center: 900, spread: 900 },
  porcino_pini: { center: 1300, spread: 900 },
  ovolo: { center: 350, spread: 500 },
  gallinaccio: { center: 700, spread: 800 },
};

function elevationFactor(species, elevation) {
  if (elevation == null) return 1;
  const pref = SPECIES_ELEVATION_PREF[species];
  const z = (elevation - pref.center) / pref.spread;
  return Math.max(0.4, Math.exp(-0.5 * z * z));
}

// "Non sappiamo che bosco c'è" non è la stessa cosa di "non c'è bosco".
// Prima erano indistinguibili: un vegClass nullo cadeva su "none" = 0.05 e
// azzerava il punteggio, quindi un errore di rete di Corine faceva sparire
// una zona buona, e le notifiche sulle zone disegnate a mano (dove il bosco
// non si può sapere, la zona copre chilometri di terreno vario) non
// arrivavano mai. Questo valore intermedio non premia e non punisce.
const UNKNOWN_VEG_AFFINITY = 0.45;

function speciesAffinityAt(species, vegClass, elevation) {
  const table = SPECIES_VEG_AFFINITY[species];
  const base = vegClass == null ? UNKNOWN_VEG_AFFINITY : (table?.[vegClass] ?? UNKNOWN_VEG_AFFINITY);
  return base * elevationFactor(species, elevation);
}

/* ---------------- Finestra di incubazione pioggia → fruttificazione ----------------
   Ogni specie ha una soglia di pioggia minima per innescare una "buttata" e un
   ritardo tipico prima che i corpi fruttiferi emergano (i raccoglitori lo
   sanno bene: "aspetta una decina di giorni dopo la pioggia"). I valori sono
   una stima ragionata da conoscenza micologica/pratica di raccolta corrente,
   non una misura di laboratorio per singola specie:
   - soglia di pioggia minima nella finestra utile della specie
   - giorni minimi/di picco/massimi tra pioggia e comparsa dei funghi
   - lunghezza della finestra: su quanti giorni si somma la pioggia (specie
     con miceli/riserve più profonde "ricordano" un periodo piovoso più
     lungo, non solo l'ultimo acquazzone)

   La somma è una somma mobile SEMPLICE sui mm reali del grafico "Pioggia
   ultimi giorni" — non un indice smussato — apposta perché il numero mostrato
   nella spiegazione di ogni specie sia lo stesso che si può verificare a
   occhio sommando le barre del grafico (in precedenza usavamo un indice a
   decadimento esponenziale: matematicamente più elegante, ma restituiva un
   valore diverso da quello visibile nel grafico, disallineando i due).

   TEMPERATURA, SUOLO E pH sono stati aggiunti dopo: il modello era cieco a
   tutto ciò che non fosse pioggia, e dava lo stesso verdetto a 8 °C e a
   26 °C.

   tempOptimumC / tempToleranceC: la fruttificazione segue una campana, non
   una soglia — cala sia al freddo sia al caldo. Per il porcino comune il
   valore NON è una stima nostra: due studi indipendenti (modelli di resa su
   Pinus sylvestris in Spagna e dieci anni di monitoraggio in faggeta
   centroeuropea) convergono su 13.2 °C, e il secondo conclude che la
   temperatura vincola la fruttificazione più della pioggia a breve termine.
   Gli altri tre sono stime ragionate a partire dall'ecologia della specie:
   l'ovolo è termofilo mediterraneo di querceto/castagneto a bassa quota, il
   porcino dei pini è specie di pineta montana più fresca, il gallinaccio è
   il più estivo dei quattro (vedi le curve stagionali reali qui sotto).

   soilTempMinC: sotto questa temperatura del suolo i primordi non partono
   proprio. Per il gallinaccio la letteratura indica 12.5 °C misurati a 5cm;
   per gli altri è una stima più prudente e più bassa.

   phOptimum / phTolerance: tutte e quattro preferiscono suoli da acidi a
   subacidi, ma con larghezze diverse. Il porcino comune è il più tollerante
   e arriva al neutro; ovolo e porcino dei pini sono i più legati ai suoli
   acidi/silicei ed è su di loro che il dato pH discrimina davvero (in Italia
   separa l'Appennino settentrionale arenaceo da quello centrale calcareo).
   Il pH modula, non veto: vedi il pavimento in phFactor(). */
const SPECIES_RAIN_PROFILE = {
  porcino_comune: { minRainMm: 20, optimalRainMm: 34, incubationMin: 6, incubationPeak: 9, incubationMax: 14, windowDays: 6, tempOptimumC: 13.2, tempToleranceC: 7, soilTempMinC: 8, phOptimum: 5.6, phTolerance: 1.5 },
  porcino_pini: { minRainMm: 18, optimalRainMm: 30, incubationMin: 6, incubationPeak: 8, incubationMax: 13, windowDays: 5, tempOptimumC: 12, tempToleranceC: 6.5, soilTempMinC: 7, phOptimum: 4.9, phTolerance: 1.0 },
  ovolo: { minRainMm: 20, optimalRainMm: 34, incubationMin: 8, incubationPeak: 11, incubationMax: 16, windowDays: 8, tempOptimumC: 19, tempToleranceC: 6, soilTempMinC: 13, phOptimum: 5.5, phTolerance: 1.1 },
  gallinaccio: { minRainMm: 12, optimalRainMm: 22, incubationMin: 4, incubationPeak: 6, incubationMax: 12, windowDays: 4, tempOptimumC: 15.5, tempToleranceC: 7, soilTempMinC: 12.5, phOptimum: 5.0, phTolerance: 1.3 },
};

// L'evapotraspirazione di riferimento ET0 è calcolata su prato irrigato in
// pieno sole. Sotto una volta di bosco, all'ombra e con meno vento, la
// domanda evaporativa reale è nettamente inferiore: questo fattore la
// riporta a scala di sottobosco. Senza, il bilancio idrico cancellerebbe
// ogni pioggia estiva (ET0 mediana italiana 4.6 mm/giorno, misurata su 28
// punti: una settimana di sole "evapora" 32mm sulla carta).
const FOREST_ET_FACTOR = 0.55;

// Estremi fisici del suolo, gli stessi usati lato server per la griglia
// (vedi SOIL_WILTING_POINT / SOIL_FIELD_CAPACITY in fetch_weather_grid.py):
// sotto il punto di appassimento l'acqua non è disponibile alle radici,
// sopra la capacità di campo il resto drena via.
const SOIL_WILTING_POINT = 0.1;
const SOIL_FIELD_CAPACITY = 0.32;

// Giorni di storico pioggia che il popup usa (grafico + analisi specie).
// Tenuto uguale alla finestra della griglia meteo (PAST_DAYS=16 + il giorno
// corrente in scripts/fetch_weather_grid.py): popup e colori della mappa
// devono giudicare sugli stessi giorni, altrimenti raccontano storie diverse
// sullo stesso punto.
const POPUP_RAIN_DAYS = 17;

/* ---------------- Fattori non-pioggia ----------------
   Ognuno risponde "quanto questo aspetto favorisce la specie qui e ora",
   in scala 0-1, e vale 1 (neutro) quando il dato manca: un buco nei dati è
   ignoranza nostra, non un'informazione sfavorevole sul posto, e penalizzare
   per un fetch fallito farebbe sparire zone buone senza motivo. */

// Campana attorno alla temperatura ottimale della specie. Va valutata sulla
// media dei giorni di INCUBAZIONE (dalla pioggia a oggi), non su quella di
// oggi: è mentre i primordi si formano che la temperatura conta.
function tempFactor(species, meanTempC) {
  if (meanTempC == null) return 1;
  const p = SPECIES_RAIN_PROFILE[species];
  const z = (meanTempC - p.tempOptimumC) / p.tempToleranceC;
  return Math.max(0.05, Math.exp(-0.5 * z * z));
}

// Il suolo troppo freddo blocca l'avvio della fruttificazione a monte, per
// quanto sia piovuto: qui la penalità è più netta di una campana, ma non un
// azzeramento (la soglia è una stima, e la temperatura del suolo che
// misuriamo è quella di oggi, non della settimana intera).
//
// ONESTÀ SUI NUMERI: nel backtest questo fattore vale 1.00 sia sui
// ritrovamenti veri sia sui controlli, cioè NON sta separando niente. Non
// è la prova che sia sbagliato — i controlli stanno a +/-70 giorni dal
// ritrovamento, e a quella distanza in Italia il suolo è quasi sempre
// ancora sopra soglia — ma è la prova che finora non serve a nulla di
// misurabile. Resta perché è fisicamente fondato e non costa niente; se un
// backtest con controlli invernali continuasse a dare scarto zero, andrebbe
// tolto invece di restare come complicazione decorativa.
function soilTempFactor(species, soilTempC) {
  if (soilTempC == null) return 1;
  const min = SPECIES_RAIN_PROFILE[species].soilTempMinC;
  if (soilTempC >= min) return 1;
  return Math.max(0.1, 1 - (min - soilTempC) / 6);
}

// Il pH modula, non decide: pavimento a 0.3 perché è una proprietà media di
// una cella da 250m, e un versante può ospitare sacche diverse.
//
// Non ancora validato: la cache di scripts/fetch_soil_ph.py si riempie a
// scaglioni e al momento dell'ultimo backtest era quasi vuota, quindi il
// fattore valeva 1 ovunque e la misura non dice niente su di lui. Da
// rimisurare quando la copertura è completa.
function phFactor(species, ph) {
  if (ph == null) return 1;
  const p = SPECIES_RAIN_PROFILE[species];
  const z = (ph - p.phOptimum) / p.phTolerance;
  return Math.max(0.3, Math.exp(-0.5 * z * z));
}

// Quanta parte della pioggia di quell'evento è ancora nel terreno oggi,
// stimata sottraendo l'evapotraspirazione dei giorni successivi. Risponde
// alla domanda che la sola somma dei mm non poteva porsi: "è piovuto, ma
// poi ha fatto sole e vento per una settimana?".
function waterRetention(eventMm, et0Series, fromIdx, toIdx) {
  if (!eventMm || !et0Series || !et0Series.length) return 1;
  let demand = 0;
  for (let i = fromIdx + 1; i <= toIdx; i++) demand += (et0Series[i] || 0) * FOREST_ET_FACTOR;
  return Math.max(0.15, Math.min(1, (eventMm - demand) / eventMm));
}

function soilWaterIndex(soilMoisture) {
  if (soilMoisture == null) return null;
  return Math.max(0, Math.min(1, (soilMoisture - SOIL_WILTING_POINT) / (SOIL_FIELD_CAPACITY - SOIL_WILTING_POINT)));
}

/* ---------------- Stagionalità, dai ritrovamenti reali ----------------
   Il modello dava lo stesso verdetto a 40mm di marzo e 40mm di settembre.
   La correzione non richiede dati nuovi: i ~2900 ritrovamenti GBIF che il
   sito già carica hanno la data, e le quattro specie mostrano curve
   nettamente diverse (il gallinaccio parte a luglio e ha coda invernale,
   l'ovolo è concentrato su settembre-ottobre, il porcino dei pini ha una
   spalla estiva coerente con la buttata primaverile documentata in Italia).

   Curva costruita con un kernel gaussiano circolare sul giorno dell'anno,
   non contando i mesi: 131 ritrovamenti di porcino dei pini sparsi su 12
   mesi darebbero una curva a denti di sega, e il confine fra il 31 agosto e
   il 1° settembre non esiste per un fungo.

   Limite dichiarato: sono date di OSSERVAZIONE, quindi risentono anche di
   quando la gente va per boschi (weekend, "stagione dei funghi"). Il segnale
   fenologico resta dominante, ma per questo la curva modula e non decide —
   vedi il pavimento SEASON_FLOOR. */
const SEASON_SIGMA_DAYS = 18;
const SEASON_FLOOR = 0.08;
const seasonCurveBySpecies = new Map();

function dayOfYear(d) {
  return Math.floor((d - new Date(d.getFullYear(), 0, 0)) / 86400000);
}

function buildSeasonCurves(features) {
  seasonCurveBySpecies.clear();
  const daysBySpecies = new Map();
  for (const f of features) {
    const iso = f.properties.eventDate;
    if (!iso || iso.length < 10) continue;
    const d = new Date(iso.slice(0, 10) + "T00:00:00");
    if (Number.isNaN(d.getTime())) continue;
    if (!daysBySpecies.has(f.properties.species)) daysBySpecies.set(f.properties.species, []);
    daysBySpecies.get(f.properties.species).push(dayOfYear(d));
  }
  for (const [sp, days] of daysBySpecies) {
    if (days.length < 30) continue; // troppo pochi per una curva credibile
    const curve = new Float64Array(367);
    for (let doy = 1; doy <= 366; doy++) {
      let acc = 0;
      for (const d of days) {
        // distanza circolare: dicembre e gennaio sono vicini
        let diff = Math.abs(doy - d);
        if (diff > 183) diff = 366 - diff;
        const z = diff / SEASON_SIGMA_DAYS;
        if (z < 4) acc += Math.exp(-0.5 * z * z);
      }
      curve[doy] = acc;
    }
    const max = Math.max(...curve);
    if (max > 0) for (let i = 1; i <= 366; i++) curve[i] = Math.max(SEASON_FLOOR, curve[i] / max);
    seasonCurveBySpecies.set(sp, curve);
  }
}

// "agosto–ottobre": il periodo in cui la curva reale della specie sta sopra
// metà del suo picco. Serve alla spiegazione, per dire a chi clicca fuori
// stagione QUANDO conviene tornare invece che solo "non ora".
function seasonWindowLabel(species) {
  const curve = seasonCurveBySpecies.get(species);
  if (!curve) return null;
  const good = [];
  for (let doy = 1; doy <= 366; doy++) if (curve[doy] >= 0.5) good.push(doy);
  if (!good.length || good.length >= 360) return null;
  // La stagione può scavalcare il capodanno (il gallinaccio ha coda
  // invernale), quindi non basta prendere primo e ultimo giorno buono: si
  // cerca il BUCO più lungo fra giorni buoni consecutivi, e la stagione è
  // l'arco che comincia subito dopo quel buco e finisce subito prima.
  let gapIdx = 0; // indice del giorno buono con cui la stagione ricomincia
  let gapLen = 366 - good[good.length - 1] + good[0];
  for (let i = 1; i < good.length; i++) {
    const len = good[i] - good[i - 1];
    if (len > gapLen) {
      gapLen = len;
      gapIdx = i;
    }
  }
  const startDoy = good[gapIdx];
  const endDoy = good[(gapIdx + good.length - 1) % good.length];
  const monthOf = (doy) => new Date(2001, 0, doy).toLocaleDateString("it-IT", { month: "long" });
  const a = monthOf(startDoy);
  const b = monthOf(endDoy);
  return a === b ? a : `${a}–${b}`;
}

function seasonFactor(species, when = new Date()) {
  const curve = seasonCurveBySpecies.get(species);
  if (!curve) return 1;
  return curve[dayOfYear(when)] || SEASON_FLOOR;
}

// Media dei valori validi fra due indici inclusi; null se non ce n'è nessuno
// (una serie assente non deve diventare uno zero che penalizza).
function meanOfRange(series, fromIdx, toIdx) {
  if (!series || !series.length) return null;
  let sum = 0;
  let n = 0;
  for (let i = Math.max(0, fromIdx); i <= Math.min(series.length - 1, toIdx); i++) {
    if (series[i] != null) {
      sum += series[i];
      n++;
    }
  }
  return n ? sum / n : null;
}

// Somma mobile sui windowDays precedenti (inclusi): result[i] = somma di
// dailyPrecip da i-windowDays+1 a i.
function rollingWindowSums(dailyPrecip, windowDays) {
  return dailyPrecip.map((_, i) => {
    const start = Math.max(0, i - windowDays + 1);
    let sum = 0;
    for (let j = start; j <= i; j++) sum += dailyPrecip[j] || 0;
    return sum;
  });
}

// Curva a trapezio: 0 prima della soglia minima, sale al picco, poi scende
// (i funghi marciscono/vengono raccolti) fino a un residuo basso oltre la
// finestra massima.
function incubationCurve(daysSince, profile) {
  const { incubationMin: min, incubationPeak: peak, incubationMax: max } = profile;
  if (daysSince < min) return 0;
  if (daysSince <= peak) return (daysSince - min) / (peak - min || 1);
  if (daysSince <= max) return 1 - 0.8 * ((daysSince - peak) / (max - peak || 1));
  return 0.08;
}

// Quanto è "pronta" la fruttificazione di una specie oggi, valutando
// l'intero storico di pioggia disponibile (16 giorni) tramite l'API sopra,
// non solo l'ultimo giorno di pioggia. Per ogni giorno della finestra
// calcoliamo quanto sarebbe "pronta" la specie SE oggi fosse quel tanti-
// esimo giorno dopo quell'accumulo, e teniamo il giorno con il punteggio
// migliore — così una pioggia abbondante ma non recentissima non sparisce
// solo perché non è la più vicina a oggi.
function speciesRainReadiness(species, env) {
  const profile = SPECIES_RAIN_PROFILE[species];
  const dailyDates = env.dates || [];
  const dailyPrecip = env.precip || [];
  const empty = {
    score: 0.05,
    daysSince: null,
    eventMm: null,
    eventDate: null,
    windowStartDate: null,
    metThreshold: false,
    pending: null,
    incubationTempC: null,
    tempFactor: 1,
    retention: 1,
  };
  if (!profile || !dailyDates.length) return empty;

  const sums = rollingWindowSums(dailyPrecip, profile.windowDays);
  const todayIdx = dailyDates.length - 1;

  let best = { ...empty, score: 0 };
  for (let i = todayIdx; i >= 0; i--) {
    const daysSince = todayIdx - i;
    if (daysSince > profile.incubationMax) break;
    const curve = incubationCurve(daysSince, profile);
    if (curve <= 0) continue;
    const amountFactor = Math.min(1, sums[i] / profile.optimalRainMm);
    // temperatura MEDIA dei giorni di incubazione (dalla pioggia a oggi) e
    // quota di quella pioggia sopravvissuta all'evaporazione nello stesso
    // arco: entrambe riguardano ciò che è successo DOPO l'evento, quindi
    // vanno valutate qui dentro, evento per evento, non una volta sola sul
    // punto — due piogge diverse hanno avuto due settimane diverse
    const incubationTempC = meanOfRange(env.temp, i, todayIdx);
    const tf = tempFactor(species, incubationTempC);
    const retention = waterRetention(sums[i], env.et0, i, todayIdx);
    const score = curve * amountFactor * tf * retention;
    if (score > best.score) {
      const windowStartIdx = Math.max(0, i - profile.windowDays + 1);
      best = {
        score,
        daysSince,
        eventMm: Math.round(sums[i]),
        eventDate: dailyDates[i],
        windowStartDate: dailyDates[windowStartIdx],
        metThreshold: sums[i] >= profile.minRainMm,
        incubationTempC,
        tempFactor: tf,
        retention,
      };
    }
  }
  // Pioggia che ha già superato la soglia della specie ma è caduta troppo
  // di recente perché i funghi siano usciti: il punteggio resta (giustamente)
  // basso, ma senza registrarla la spiegazione direbbe "non è piovuto
  // abbastanza" mentre nel grafico si vedono barre alte — la contraddizione
  // più confondente del popup.
  let pending = null;
  for (let i = todayIdx; i >= 0; i--) {
    const daysSince = todayIdx - i;
    if (daysSince >= profile.incubationMin) break;
    if (sums[i] < profile.minRainMm) continue;
    if (!pending || sums[i] > pending.mm) {
      // la temperatura va misurata sui giorni di QUESTO evento, non su
      // quelli dell'evento incubato: sono due piogge diverse, e attribuire
      // al racconto dell'una i numeri dell'altra era esattamente il tipo di
      // disallineamento fra testo e grafico che si era già corretto
      const tC = meanOfRange(env.temp, i, todayIdx);
      pending = {
        mm: Math.round(sums[i]),
        daysSince,
        eventDate: dailyDates[i],
        windowStartDate: dailyDates[Math.max(0, i - profile.windowDays + 1)],
        incubationTempC: tC,
        tempFactor: tempFactor(species, tC),
      };
    }
  }

  // pavimento 0.05: mai un vero zero, ma un evento debole/assente resta
  // comunque nettamente sotto un evento forte e ben temporizzato
  return { ...best, pending, score: Math.max(0.05, best.score) };
}

// Condizioni generali del punto adesso: quanta acqua c'è davvero nel
// terreno dove sta il micelio, e quanto l'aria la sta riprendendo. Non
// dipende dalla specie.
//
// L'umidità del suolo ora viene dallo strato 3-9cm (il feltro miceliale)
// invece che da 0-1cm, e la scala va dal punto di appassimento alla
// capacità di campo invece che da 0.05 a 0.40: la vecchia combinazione
// teneva la variabile schiacciata nel quarto basso della scala (mediana
// 0.090 su 136 celle) e mostrava "Terreno 16%" per un bosco appenninico in
// condizioni normali, dove lo strato del micelio dava 39%.
//
// Dell'umidità dell'aria conta il MINIMO delle ultime ore, non il valore
// dell'istante in cui abbiamo scaricato i dati: la letteratura su Boletus
// edulis riporta che la crescita dei carpofori si ferma quando l'umidità
// relativa minima scende sotto il 40%.
function conditionsQuality(soilMoisture, humidityPct, soilMoistureDeep = null, humidityMinPct = null) {
  const mat = soilWaterIndex(soilMoisture);
  const reserve = soilWaterIndex(soilMoistureDeep);
  let soilW;
  if (mat == null && reserve == null) soilW = 0.4;
  else if (mat == null) soilW = reserve;
  else if (reserve == null) soilW = mat;
  else soilW = 0.65 * mat + 0.35 * reserve;
  const hum = humidityMinPct != null ? humidityMinPct : humidityPct;
  const humW = Math.max(0, Math.min(1, ((hum || 0) - 40) / (95 - 40)));
  return Math.max(0, Math.min(1, 0.5 * soilW + 0.5 * humW));
}

// Il punteggio completo di una specie in un punto: pioggia nella finestra
// giusta (già pesata per temperatura di incubazione ed evaporazione) × bosco
// adatto × quota × pH del suolo × stagione × suolo abbastanza caldo. Un
// unico posto, usato sia dai colori della mappa sia dal popup, così le due
// cose non possono raccontare storie diverse sullo stesso punto.
function speciesScore(sp, env, rain = null) {
  const r = rain || speciesRainReadiness(sp, env);
  return (
    r.score *
    speciesAffinityAt(sp, env.vegClass, env.elevation) *
    phFactor(sp, env.ph) *
    seasonFactor(sp) *
    soilTempFactor(sp, env.soilTempC)
  );
}

/* Sotto Node (il backtest) esporta; nel browser questo blocco non esiste e
   le funzioni restano semplicemente globali come ogni script classico. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SPECIES_VEG_AFFINITY,
    UNKNOWN_VEG_AFFINITY,
    SPECIES_ELEVATION_PREF,
    SPECIES_RAIN_PROFILE,
    POPUP_RAIN_DAYS,
    FOREST_ET_FACTOR,
    SOIL_WILTING_POINT,
    SOIL_FIELD_CAPACITY,
    elevationFactor,
    speciesAffinityAt,
    tempFactor,
    soilTempFactor,
    phFactor,
    waterRetention,
    soilWaterIndex,
    buildSeasonCurves,
    seasonFactor,
    seasonWindowLabel,
    seasonCurveBySpecies,
    meanOfRange,
    rollingWindowSums,
    incubationCurve,
    speciesRainReadiness,
    conditionsQuality,
    speciesScore,
  };
}
