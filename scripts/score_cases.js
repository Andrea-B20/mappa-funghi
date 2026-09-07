/* Valutatore batch per il backtest: legge un file di casi, li passa al
 * MODELLO VERO del sito (web/model.js, lo stesso file che il browser carica)
 * e scrive i punteggi. Esiste perché il backtest sia una misura del sito e
 * non di una riscrittura in Python destinata a divergere al primo ritocco.
 *
 * Uso:  node scripts/score_cases.js <casi.json> <risultati.json>
 *
 * Ogni caso porta il proprio env già costruito. Per ognuno calcola:
 *   - scoreNew: il modello attuale, con tutti i fattori
 *   - scoreOld: il modello com'era prima di questo lavoro, cioè finestra di
 *     pioggia x bosco x quota, senza temperatura, evaporazione, stagione,
 *     temperatura del suolo e pH. Serve a rispondere alla sola domanda che
 *     conta: i dati nuovi separano i ritrovamenti veri meglio di prima?
 */

const fs = require("fs");
const path = require("path");
const model = require(path.join(__dirname, "..", "web", "model.js"));

const [casesPath, outPath] = process.argv.slice(2);
if (!casesPath || !outPath) {
  console.error("Uso: node scripts/score_cases.js <casi.json> <risultati.json>");
  process.exit(1);
}

const payload = JSON.parse(fs.readFileSync(casesPath, "utf8"));

// Le curve stagionali vengono costruite SOLO sui ritrovamenti di
// addestramento passati dal chiamante: costruirle su tutti e poi misurare
// su quegli stessi ritrovamenti misurerebbe la memoria, non la capacità di
// previsione.
model.buildSeasonCurves(payload.trainOccurrences || []);

// il fattore stagione dipende da "oggi": nel backtest "oggi" è la data del
// caso, quindi lo si valuta esplicitamente invece di lasciare il default
function seasonAt(sp, isoDate) {
  return model.seasonFactor(sp, new Date(isoDate + "T00:00:00"));
}

// Le griglie di pesi da provare arrivano dal chiamante. Con una sola (i
// pesi di default) questo è il backtest normale; con molte è la ricerca dei
// pesi, e valutarle tutte qui dentro evita di riscaricare i dati meteo per
// ogni combinazione — è la parte lenta, e non cambia coi pesi.
const weightings = payload.weightings || [{ name: "default", weights: model.SCORE_WEIGHTS }];

const results = payload.cases.map((c) => {
  const env = c.env;
  const rainFull = model.speciesRainReadiness(c.species, env);
  const season = seasonAt(c.species, c.date);

  // Stessa formula di speciesScore() in web/model.js, riscritta qui solo
  // perché il fattore stagione va valutato alla data del caso invece che
  // sull'orologio di sistema. Se cambia la formula là, va cambiata anche
  // qui: è l'unico punto del backtest che non riusa il modello parola per
  // parola, e va tenuto d'occhio.
  const scoreWith = (w) =>
    Math.pow(rainFull.rainScore, w.rain) *
    Math.pow(rainFull.tempFactor, w.temp) *
    Math.pow(rainFull.retention, w.retention) *
    Math.pow(model.speciesAffinityAt(c.species, env.vegClass, env.elevation), w.tree) *
    Math.pow(model.phFactor(c.species, env.ph), w.ph) *
    Math.pow(season, w.season) *
    Math.pow(model.soilTempFactor(c.species, env.soilTempC), w.soilTemp);

  const scores = {};
  for (const { name, weights } of weightings) scores[name] = scoreWith(weights);

  // il vecchio modello non conosceva né temperatura né ET0: togliendo le
  // due serie dall'env, speciesRainReadiness ricade esattamente sul
  // comportamento di prima (tempFactor e waterRetention ritornano 1)
  const rainOnly = model.speciesRainReadiness(c.species, { ...env, temp: null, et0: null });
  const scoreOld = rainOnly.rainScore * model.speciesAffinityAt(c.species, env.vegClass, env.elevation);

  return {
    id: c.id,
    species: c.species,
    label: c.label,
    scores,
    scoreNew: scores[weightings[0].name],
    // "tier" espone la STESSA soglia calibrata che usa la mappa
    // (READY_THRESHOLD/SOON_THRESHOLD in web/model.js), così chi consuma
    // questo output — scripts/send_notifications.py — non deve tenere una
    // sua copia dei due numeri: leggerebbe "pronto" con soglie diverse da
    // quelle che l'utente vede aprendo il sito, lo stesso disallineamento
    // già corretto due volte fra grafico/testo e badge/verdetto.
    tier: model.speciesTier(scores[weightings[0].name]),
    scoreOld,
    rainScore: rainFull.rainScore,
    tempFactor: rainFull.tempFactor,
    retention: rainFull.retention,
    season,
    soilTempFactor: model.soilTempFactor(c.species, env.soilTempC),
    phFactor: model.phFactor(c.species, env.ph),
  };
});

fs.writeFileSync(outPath, JSON.stringify(results));
console.log(`${results.length} casi valutati -> ${outPath}`);
