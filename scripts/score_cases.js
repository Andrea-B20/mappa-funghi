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

const results = payload.cases.map((c) => {
  const env = c.env;
  const rainFull = model.speciesRainReadiness(c.species, env);
  const habitat = model.speciesAffinityAt(c.species, env.vegClass, env.elevation);

  const scoreNew =
    rainFull.score *
    habitat *
    model.phFactor(c.species, env.ph) *
    seasonAt(c.species, c.date) *
    model.soilTempFactor(c.species, env.soilTempC);

  // il vecchio modello non conosceva né temperatura né ET0: togliendo le
  // due serie dall'env, speciesRainReadiness ricade esattamente sul
  // comportamento di prima (tempFactor e waterRetention ritornano 1)
  const rainOnly = model.speciesRainReadiness(c.species, { ...env, temp: null, et0: null });
  const scoreOld = rainOnly.score * habitat;

  return {
    id: c.id,
    species: c.species,
    label: c.label,
    scoreNew,
    scoreOld,
    rainScore: rainFull.score,
    tempFactor: rainFull.tempFactor,
    retention: rainFull.retention,
    season: seasonAt(c.species, c.date),
    soilTempFactor: model.soilTempFactor(c.species, env.soilTempC),
    phFactor: model.phFactor(c.species, env.ph),
  };
});

fs.writeFileSync(outPath, JSON.stringify(results));
console.log(`${results.length} casi valutati -> ${outPath}`);
