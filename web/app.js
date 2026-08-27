const SPECIES_META = {
  porcino_comune: { label: "Porcino comune", color: "var(--sp-porcino-comune)", hex: "#b07a3e" },
  porcino_pini: { label: "Porcino dei pini", color: "var(--sp-porcino-pini)", hex: "#8a4a2a" },
  ovolo: { label: "Ovolo", color: "var(--sp-ovolo)", hex: "#e2602f" },
  gallinaccio: { label: "Gallinaccio", color: "var(--sp-gallinaccio)", hex: "#eeab3a" },
};

const ICONS = {
  rain: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c3.6 4.3 6 7.8 6 10.6a6 6 0 1 1-12 0C6 10.8 8.4 7.3 12 3z"/></svg>`,
  soil: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20V10"/><path d="M12 10C12 6 9 4 5 4c0 4.4 3 6 7 6z"/><path d="M12 12c0-3.2 2.6-5.5 6.5-5.5 0 3.7-2.7 5.5-6.5 5.5z"/></svg>`,
  humidity: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 9c1.8-1.8 3.6-1.8 5.4 0s3.6 1.8 5.4 0 3.6-1.8 5.4 0"/><path d="M3.5 14.2c1.8-1.8 3.6-1.8 5.4 0s3.6 1.8 5.4 0 3.6-1.8 5.4 0"/><path d="M3.5 19.4c1.8-1.8 3.6-1.8 5.4 0s3.6 1.8 5.4 0 3.6-1.8 5.4 0"/></svg>`,
  elevation: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 19l5.5-9.5 3.3 4.8 2-2.8L21 19z"/></svg>`,
  forest: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5l3 4.7h-2l2.8 4.3h-2.4l3 4.5H7.6l3-4.5H8.2l2.8-4.3h-2z"/><path d="M12 17v4"/></svg>`,
  timer: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="7.5"/><path d="M12 9.5v4l2.8 1.8"/><path d="M9.5 2h5"/></svg>`,
};

// Fasce forestali/montane maggiori dell'Italia, usate come proxy interinale
// di copertura vegetale finché non è disponibile un dato OSM/Overpass reale
// (vedi commento in scripts/fetch_weather_grid.py per il perché).
const FOREST_REGIONS = [
  [45.5, 7.4, 85], [46.1, 10.4, 95], [46.3, 11.8, 65], [46.05, 12.35, 40],
  [44.5, 9.2, 40], [44.05, 11.6, 65], [42.88, 11.6, 35], [42.9, 13.15, 50],
  [42.05, 13.9, 55], [40.4, 15.6, 55], [39.3, 16.5, 35], [38.2, 15.9, 30],
  [37.75, 15.0, 25], [37.9, 14.3, 40], [40.0, 9.3, 40],
];
const KM_PER_DEG_LAT = 111;

function forestRegionScore(lat, lon) {
  let maxW = 0;
  for (const [rLat, rLon, radiusKm] of FOREST_REGIONS) {
    const dLat = (lat - rLat) * KM_PER_DEG_LAT;
    const dLon = (lon - rLon) * KM_PER_DEG_LAT * Math.abs(Math.cos((rLat * Math.PI) / 180));
    const dist = Math.hypot(dLat, dLon);
    maxW = Math.max(maxW, Math.max(0, Math.min(1, 1 - dist / radiusKm)));
  }
  return maxW;
}

// In Italia la composizione forestale è fortemente stratificata per quota:
// querceti/castagneti in basso, faggete a media quota, conifere in alto (la
// soglia si alza spostandosi a sud, quindi correggiamo con la latitudine).
// Senza un dato reale di uso del suolo (Overpass non raggiungibile, vedi
// scripts/fetch_weather_grid.py) è il proxy più sensato per distinguere il
// TIPO di bosco — non solo la sua presenza — e quindi filtrare davvero per
// specie: il porcino dei pini vive con le conifere, l'ovolo quasi solo con
// querce/castagni in zone calde, ecc.
const TREE_ZONES = [
  { key: "broadleaf_warm", center: 350, label: "Querceto / castagneto (bosco caldo)" },
  { key: "beech", center: 1000, label: "Faggeta" },
  { key: "conifer_mixed", center: 1600, label: "Bosco misto faggio-conifere" },
  { key: "conifer_high", center: 2200, label: "Conifere d'alta quota" },
];

const SPECIES_TREE_AFFINITY = {
  porcino_comune: { broadleaf_warm: 0.75, beech: 1.0, conifer_mixed: 0.7, conifer_high: 0.3 },
  porcino_pini: { broadleaf_warm: 0.15, beech: 0.35, conifer_mixed: 0.85, conifer_high: 0.9 },
  ovolo: { broadleaf_warm: 1.0, beech: 0.35, conifer_mixed: 0.05, conifer_high: 0.02 },
  gallinaccio: { broadleaf_warm: 0.75, beech: 0.9, conifer_mixed: 0.65, conifer_high: 0.25 },
};

function effectiveElevation(elevation, lat) {
  return elevation - (lat - 43) * 100;
}

function treeZoneMemberships(effElev) {
  if (effElev <= TREE_ZONES[0].center) return { [TREE_ZONES[0].key]: 1 };
  for (let i = 0; i < TREE_ZONES.length - 1; i++) {
    if (effElev <= TREE_ZONES[i + 1].center) {
      const t = (effElev - TREE_ZONES[i].center) / (TREE_ZONES[i + 1].center - TREE_ZONES[i].center);
      return { [TREE_ZONES[i].key]: 1 - t, [TREE_ZONES[i + 1].key]: t };
    }
  }
  return { [TREE_ZONES[TREE_ZONES.length - 1].key]: 1 };
}

function dominantTreeZone(elevation, lat) {
  if (elevation == null) return null;
  const memberships = treeZoneMemberships(effectiveElevation(elevation, lat));
  const topKey = Object.entries(memberships).sort((a, b) => b[1] - a[1])[0][0];
  return TREE_ZONES.find((z) => z.key === topKey);
}

function speciesAffinityAt(species, elevation, lat) {
  if (elevation == null) return 0.6;
  const memberships = treeZoneMemberships(effectiveElevation(elevation, lat));
  let score = 0;
  for (const [zoneKey, w] of Object.entries(memberships)) {
    score += w * (SPECIES_TREE_AFFINITY[species]?.[zoneKey] ?? 0.5);
  }
  return score;
}

// Idoneità del tipo di bosco per le specie attualmente selezionate: prende
// la specie più favorita in quel punto, cosicché selezionare solo "Porcino
// dei pini" faccia davvero risaltare le zone di conifere e non i querceti.
function activeSpeciesAffinityAt(elevation, lat) {
  if (activeSpecies.size === 0) return 0.5;
  let best = 0;
  for (const sp of activeSpecies) best = Math.max(best, speciesAffinityAt(sp, elevation, lat));
  return best;
}

function vegetationInfo(lat, lon, elevation) {
  const presence = forestRegionScore(lat, lon);
  if (presence < 0.15) {
    return { typeLabel: "Fuori dalle fasce boschive principali" };
  }
  const zone = dominantTreeZone(elevation, lat);
  return { typeLabel: zone ? zone.label : "Tipo non determinabile (quota mancante)" };
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
   ultimi 15gg" — non un indice smussato — apposta perché il numero mostrato
   nella spiegazione di ogni specie sia lo stesso che si può verificare a
   occhio sommando le barre del grafico (in precedenza usavamo un indice a
   decadimento esponenziale: matematicamente più elegante, ma restituiva un
   valore diverso da quello visibile nel grafico, disallineando i due).
*/
const SPECIES_RAIN_PROFILE = {
  porcino_comune: { minRainMm: 20, optimalRainMm: 34, incubationMin: 6, incubationPeak: 9, incubationMax: 14, windowDays: 6 },
  porcino_pini: { minRainMm: 18, optimalRainMm: 30, incubationMin: 6, incubationPeak: 8, incubationMax: 13, windowDays: 5 },
  ovolo: { minRainMm: 20, optimalRainMm: 34, incubationMin: 8, incubationPeak: 11, incubationMax: 16, windowDays: 8 },
  gallinaccio: { minRainMm: 12, optimalRainMm: 22, incubationMin: 4, incubationPeak: 6, incubationMax: 12, windowDays: 4 },
};

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
function speciesRainReadiness(species, dailyDates, dailyPrecip) {
  const profile = SPECIES_RAIN_PROFILE[species];
  const empty = {
    score: 0.05,
    daysSince: null,
    eventMm: null,
    eventDate: null,
    windowStartDate: null,
    metThreshold: false,
  };
  if (!profile || !dailyDates || !dailyDates.length) return empty;

  const sums = rollingWindowSums(dailyPrecip, profile.windowDays);
  const todayIdx = dailyDates.length - 1;

  let best = { ...empty, score: 0 };
  for (let i = todayIdx; i >= 0; i--) {
    const daysSince = todayIdx - i;
    if (daysSince > profile.incubationMax) break;
    const curve = incubationCurve(daysSince, profile);
    if (curve <= 0) continue;
    const amountFactor = Math.min(1, sums[i] / profile.optimalRainMm);
    const score = curve * amountFactor;
    if (score > best.score) {
      const windowStartIdx = Math.max(0, i - profile.windowDays + 1);
      best = {
        score,
        daysSince,
        eventMm: Math.round(sums[i]),
        eventDate: dailyDates[i],
        windowStartDate: dailyDates[windowStartIdx],
        metThreshold: sums[i] >= profile.minRainMm,
      };
    }
  }
  // pavimento 0.05: mai un vero zero, ma un evento debole/assente resta
  // comunque nettamente sotto un evento forte e ben temporizzato
  return { ...best, score: Math.max(0.05, best.score) };
}

function conditionsQuality(soilMoisture, humidityPct) {
  const soilW = soilMoisture != null ? Math.max(0, Math.min(1, (soilMoisture - 0.05) / (0.4 - 0.05))) : 0.4;
  const humW = Math.max(0, Math.min(1, ((humidityPct || 0) - 40) / (95 - 40)));
  return Math.max(0, Math.min(1, 0.5 * soilW + 0.5 * humW));
}

// Combina, per le specie attualmente selezionate, la finestra di
// incubazione pioggia-specifica con l'idoneità del tipo di bosco: un luogo
// "pronto" richiede sia la pioggia giusta al momento giusto sia l'albero
// simbionte giusto.
function activeSpeciesReadinessAt(dailyDates, dailyPrecip, elevation, lat) {
  if (activeSpecies.size === 0) return 0.5;
  let best = 0;
  for (const sp of activeSpecies) {
    const rain = speciesRainReadiness(sp, dailyDates, dailyPrecip).score;
    const tree = speciesAffinityAt(sp, elevation, lat);
    best = Math.max(best, rain * tree);
  }
  return best;
}

// Stato combinato (pioggia nella finestra giusta × tipo di bosco adatto)
// per tutte e 4 le specie tracciate in un punto esatto — la stessa formula
// usata per colorare la mappa, così il popup e la mappa raccontano sempre
// la stessa storia. Ordinato dalla più alla meno pronta in questo momento.
function speciesReadinessList(dailyDates, dailyPrecip, elevation, lat) {
  return Object.keys(SPECIES_META)
    .map((sp) => {
      const rain = speciesRainReadiness(sp, dailyDates, dailyPrecip);
      const tree = speciesAffinityAt(sp, elevation, lat);
      return { sp, label: SPECIES_META[sp].label, color: SPECIES_META[sp].color, ...rain, score: rain.score * tree };
    })
    .sort((a, b) => b.score - a.score);
}

function speciesStatusBadge(r) {
  if (r.score >= 0.45) return { word: "pronto", cls: "ready" };
  if (r.score >= 0.18) return { word: "in arrivo", cls: "soon" };
  return { word: "non ora", cls: "none" };
}

function fmtDateShort(iso) {
  if (!iso) return null;
  return new Date(iso + "T00:00:00").toLocaleDateString("it-IT", { day: "numeric", month: "long" });
}

// "14 – 20 agosto" se stesso mese, altrimenti "28 luglio – 3 agosto".
function fmtDateRange(startIso, endIso) {
  if (!startIso || !endIso) return fmtDateShort(endIso);
  if (startIso === endIso) return fmtDateShort(endIso);
  const start = new Date(startIso + "T00:00:00");
  const end = new Date(endIso + "T00:00:00");
  const sameMonth = start.getMonth() === end.getMonth();
  const startLabel = sameMonth
    ? start.toLocaleDateString("it-IT", { day: "numeric" })
    : start.toLocaleDateString("it-IT", { day: "numeric", month: "long" });
  return `${startLabel} – ${fmtDateShort(endIso)}`;
}

// Spiegazione in linguaggio semplice del motivo dietro "pronto"/"in
// arrivo"/"non ora", mostrata quando l'utente clicca la riga della specie.
// Cita lo stesso intervallo di date e mm evidenziati nel grafico sopra
// (stessa somma, nessun indice "smussato" diverso da quello disegnato).
function speciesDetailText(r) {
  const profile = SPECIES_RAIN_PROFILE[r.sp];
  const growthNote = `Dopo una pioggia così, questa specie compare di solito dopo ${profile.incubationMin}-${profile.incubationMax} giorni: il momento migliore è intorno al giorno ${profile.incubationPeak}.`;

  if (r.daysSince == null) {
    return `Non è piovuto abbastanza negli ultimi 15 giorni: servono almeno ${profile.minRainMm}mm in ${profile.windowDays} giorni. ${growthNote}`;
  }

  const rangeLabel = fmtDateRange(r.windowStartDate, r.eventDate);
  const rainWhen = r.daysSince === 0 ? "è di oggi" : `risale a ${r.daysSince} giorni fa`;
  const weakNote = r.metThreshold ? "" : " È sotto la soglia minima: il segnale è debole.";

  return `Tra il ${rangeLabel} sono caduti ${r.eventMm}mm (vedi il grafico sopra). L'ultima di queste piogge ${rainWhen}.${weakNote} ${growthNote}`;
}

const MODE_LABELS = {
  storico: {
    title: "Densità storica ritrovamenti",
    desc: "Concentrazione di segnalazioni passate (GBIF + iNaturalist) per le specie selezionate.",
  },
  meteo: {
    title: "Favorevolezza meteo attuale",
    desc: "Finestra di incubazione pioggia→fruttificazione delle specie selezionate (soglia di pioggia e giorni di attesa tipici di ciascuna) più tipo di bosco adatto.",
  },
  combinato: {
    title: "Probabilità stimata",
    desc: "Storico dei ritrovamenti modulato da meteo attuale, quota, tipo di bosco e finestra di incubazione pioggia-specie: rosso = zone note e pronte ora per le specie selezionate.",
  },
};

const GRADIENT_STOPS = [
  [0.0, [43, 108, 176]],
  [0.25, [99, 176, 106]],
  [0.5, [224, 195, 65]],
  [0.75, [224, 138, 60]],
  [1.0, [217, 72, 58]],
];

function colorForValue(v) {
  const x = Math.max(0, Math.min(1, v));
  for (let i = 1; i < GRADIENT_STOPS.length; i++) {
    const [p0, c0] = GRADIENT_STOPS[i - 1];
    const [p1, c1] = GRADIENT_STOPS[i];
    if (x <= p1) {
      const t = (x - p0) / (p1 - p0 || 1);
      const r = Math.round(c0[0] + (c1[0] - c0[0]) * t);
      const g = Math.round(c0[1] + (c1[1] - c0[1]) * t);
      const b = Math.round(c0[2] + (c1[2] - c0[2]) * t);
      return `rgb(${r},${g},${b})`;
    }
  }
  const last = GRADIENT_STOPS[GRADIENT_STOPS.length - 1][1];
  return `rgb(${last[0]},${last[1]},${last[2]})`;
}

// lookup 0-255 pre-calcolata dagli stessi GRADIENT_STOPS: converte l'alpha
// accumulato sul canvas della heatmap in un colore, pixel per pixel, senza
// dover interpolare la scala per ogni singolo pixel ad ogni redraw
const COLOR_LUT = (() => {
  const lut = new Uint8ClampedArray(256 * 4);
  for (let a = 0; a < 256; a++) {
    const [r, g, b] = colorForValue(a / 255)
      .match(/\d+/g)
      .map(Number);
    lut[a * 4] = r;
    lut[a * 4 + 1] = g;
    lut[a * 4 + 2] = b;
  }
  return lut;
})();

const map = L.map("map", { zoomControl: false }).setView([42.3, 12.8], 6);
L.control.zoom({ position: "topright" }).addTo(map);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 18,
}).addTo(map);

new ResizeObserver(() => map.invalidateSize()).observe(document.getElementById("map"));

// Layer canvas per la mappa di calore: i punti sono resi come sfumature
// radiali che si sovrappongono e si fondono (come una vera heatmap), non
// come cerchi pieni dal bordo netto. Il raggio è comunque calcolato da un
// valore reale in metri (vedi zoneRadiusMeters) e riconvertito in pixel a
// ogni redraw in base a zoom e latitudine: cresce/si rimpicciolisce
// correttamente con lo zoom invece di restare fisso sullo schermo.
const HeatCanvasLayer = L.Layer.extend({
  onAdd(map) {
    this._map = map;
    this._canvas = L.DomUtil.create("canvas", "heat-canvas");
    this._canvas.style.pointerEvents = "none";
    const size = map.getSize();
    this._canvas.width = size.x;
    this._canvas.height = size.y;
    const animated = map.options.zoomAnimation && L.Browser.any3d;
    L.DomUtil.addClass(this._canvas, animated ? "leaflet-zoom-animated" : "leaflet-zoom-hide");
    map.getPanes().overlayPane.appendChild(this._canvas);
    map.on("moveend resize", this._reset, this);
    if (animated) map.on("zoomanim", this._animateZoom, this);
    this._zones = [];
    this._reset();
  },
  onRemove(map) {
    L.DomUtil.remove(this._canvas);
    map.off("moveend resize", this._reset, this);
    map.off("zoomanim", this._animateZoom, this);
  },
  setZones(zones, radiusMeters) {
    this._zones = zones;
    this._radiusMeters = radiusMeters;
    this._redraw();
  },
  clearLayers() {
    this.setZones([], this._radiusMeters);
  },
  _animateZoom(e) {
    const map = this._map;
    const scale = map.getZoomScale(e.zoom);
    const offset = map._latLngBoundsToNewLayerBounds(map.getBounds(), e.zoom, e.center).min;
    L.DomUtil.setTransform(this._canvas, offset, scale);
  },
  _reset() {
    const map = this._map;
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(this._canvas, topLeft);
    const size = map.getSize();
    if (this._canvas.width !== size.x) this._canvas.width = size.x;
    if (this._canvas.height !== size.y) this._canvas.height = size.y;
    this._redraw();
  },
  _redraw() {
    const map = this._map;
    const ctx = this._canvas.getContext("2d");
    const w = this._canvas.width;
    const h = this._canvas.height;
    if (w === 0 || h === 0) return;
    ctx.clearRect(0, 0, w, h);
    if (!this._zones || !this._zones.length || !this._radiusMeters) return;

    // Il campo di calore è campionato su una griglia di calcolo più rada
    // (un campione ogni CELL px) invece che pixel per pixel: per ogni
    // campione si sommano i contributi di TUTTE le zone vicine pesati per
    // distanza (kernel (1-t²)²), poi si prende la MEDIA pesata dei valori
    // — non un accumulo di opacità. Così due zone adiacenti coi loro dati
    // si fondono in un'unica figura continua il cui colore riflette
    // davvero i dati nel punto, invece di scurirsi artificialmente solo
    // perché più cerchi si sovrappongono. La griglia rada viene poi
    // ingrandita con lo smoothing bilineare del canvas, che elimina
    // qualunque bordo a gradini. L'opacità finale è tenuta sotto un tetto
    // (MAX_OPACITY) perché la mappa sottostante resti sempre leggibile.
    const CELL = 4;
    const gw = Math.ceil(w / CELL);
    const gh = Math.ceil(h / CELL);
    const sumW = new Float32Array(gw * gh);
    const sumWV = new Float32Array(gw * gh);

    const origin = map.containerPointToLayerPoint([0, 0]);
    for (const z of this._zones) {
      if (z.value <= 0.03) continue;
      const p = map.latLngToLayerPoint([z.lat, z.lon]).subtract(origin);
      const metersPerPixel = (156543.03392804097 * Math.cos((z.lat * Math.PI) / 180)) / Math.pow(2, map.getZoom());
      const r = this._radiusMeters / metersPerPixel;
      if (p.x + r < 0 || p.x - r > w || p.y + r < 0 || p.y - r > h) continue;

      const gx0 = Math.max(0, Math.floor((p.x - r) / CELL));
      const gx1 = Math.min(gw - 1, Math.ceil((p.x + r) / CELL));
      const gy0 = Math.max(0, Math.floor((p.y - r) / CELL));
      const gy1 = Math.min(gh - 1, Math.ceil((p.y + r) / CELL));
      const r2 = r * r;

      for (let gy = gy0; gy <= gy1; gy++) {
        const cy = (gy + 0.5) * CELL;
        const dy2 = (cy - p.y) * (cy - p.y);
        if (dy2 > r2) continue;
        const rowBase = gy * gw;
        for (let gx = gx0; gx <= gx1; gx++) {
          const cx = (gx + 0.5) * CELL;
          const dx = cx - p.x;
          const d2 = dx * dx + dy2;
          if (d2 >= r2) continue;
          const t2 = d2 / r2;
          const wgt = (1 - t2) * (1 - t2);
          const idx = rowBase + gx;
          sumW[idx] += wgt;
          sumWV[idx] += wgt * z.value;
        }
      }
    }

    const small = document.createElement("canvas");
    small.width = gw;
    small.height = gh;
    const sctx = small.getContext("2d");
    const img = sctx.createImageData(gw, gh);
    const data = img.data;
    const MAX_OPACITY = 200;
    for (let i = 0; i < sumW.length; i++) {
      const wgt = sumW[i];
      if (wgt <= 0.001) continue;
      const value = Math.max(0, Math.min(1, sumWV[i] / wgt));
      const alpha = MAX_OPACITY * (1 - Math.exp(-wgt * 1.3));
      const li = Math.round(value * 255) * 4;
      const di = i * 4;
      data[di] = COLOR_LUT[li];
      data[di + 1] = COLOR_LUT[li + 1];
      data[di + 2] = COLOR_LUT[li + 2];
      data[di + 3] = Math.round(alpha);
    }
    sctx.putImageData(img, 0, 0);

    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(small, 0, 0, gw, gh, 0, 0, w, h);
  },
});

const zonesLayer = new HeatCanvasLayer().addTo(map);

let occurrences = [];
let weatherCells = [];
let weatherGridStepDeg = 0.5;
let mode = "combinato";
const activeSpecies = new Set(Object.keys(SPECIES_META));

// stato del popup meteo aperto: usati per la spiegazione a comparsa quando
// si clicca "pronto"/"in arrivo" su una specie (vedi toggleSpeciesDetail).
// Leaflet Popup.update()/setContent() ri-renderizzano SEMPRE da zero dalla
// stringa originale (sovrascrivono qualsiasi modifica diretta al DOM), quindi
// il toggle deve rigenerare l'intero contenuto invece di manipolare il DOM.
let activePopupInstance = null;
let lastPopupParams = null;
let openSpeciesIdx = null;

// risoluzione per lo storico/combinato: più fine della griglia meteo
// (0.5°) perché i punti GBIF sono molto più numerosi e localizzati delle
// celle meteo, quindi meritano zone più piccole e precise
const FINE_GRID_STEP_DEG = 0.15;

/* ---------------- Toolbar ---------------- */

function buildFilters() {
  const container = document.getElementById("filters");
  Object.entries(SPECIES_META).forEach(([key, meta]) => {
    const chip = document.createElement("label");
    chip.className = "chip active";
    chip.innerHTML = `<input type="checkbox" checked /><span class="dot" style="background:${meta.color}"></span>${meta.label}`;
    const input = chip.querySelector("input");
    input.addEventListener("change", () => {
      if (input.checked) activeSpecies.add(key);
      else activeSpecies.delete(key);
      chip.classList.toggle("active", input.checked);
      render();
    });
    container.appendChild(chip);
  });
}

function buildModeSwitch() {
  const container = document.getElementById("modeSwitch");
  Object.keys(MODE_LABELS).forEach((key) => {
    const btn = document.createElement("button");
    btn.textContent = { storico: "Storico", meteo: "Meteo attuale", combinato: "Combinato" }[key];
    btn.className = key === mode ? "active" : "";
    btn.addEventListener("click", () => {
      mode = key;
      [...container.children].forEach((c) => c.classList.toggle("active", c === btn));
      updateLegend();
      render();
    });
    container.appendChild(btn);
  });
}

function updateLegend() {
  document.getElementById("legendTitle").textContent = MODE_LABELS[mode].title;
  document.getElementById("legendDesc").textContent = MODE_LABELS[mode].desc;
}

/* ---------------- Zone rendering ----------------
   Il raggio di ogni zona è un valore reale in metri (vedi zoneRadiusMeters):
   ad ogni redraw viene riconvertito in pixel in base a zoom e latitudine
   correnti, quindi le zone occupano sempre la stessa area reale sul
   terreno e non "si restringono" zoomando. Le zone stesse sono sfumature
   radiali che si fondono dove si sovrappongono (vedi HeatCanvasLayer sopra),
   così la mappa si legge come una vera heatmap e non come dischi dal bordo
   netto.
*/

function haversineDeg(lat1, lon1, lat2, lon2) {
  const dLat = lat1 - lat2;
  const dLon = (lon1 - lon2) * Math.cos(((lat1 + lat2) / 2) * (Math.PI / 180));
  return Math.sqrt(dLat * dLat + dLon * dLon);
}

// Raggruppa i punti storici in celle di griglia fissa: ogni cella con
// segnalazioni diventa una zona reale, invece di un punto la cui area
// visibile dipende dallo zoom corrente.
function fineCellCenter(lat, lon) {
  const latIdx = Math.floor(lat / FINE_GRID_STEP_DEG);
  const lonIdx = Math.floor(lon / FINE_GRID_STEP_DEG);
  return {
    key: `${latIdx}_${lonIdx}`,
    lat: (latIdx + 0.5) * FINE_GRID_STEP_DEG,
    lon: (lonIdx + 0.5) * FINE_GRID_STEP_DEG,
  };
}

function buildHistoricalGrid(filteredOccurrences) {
  const cells = new Map();
  for (const f of filteredOccurrences) {
    const [lon, lat] = f.geometry.coordinates;
    const { key, lat: cLat, lon: cLon } = fineCellCenter(lat, lon);
    const existing = cells.get(key);
    if (existing) existing.count++;
    else cells.set(key, { lat: cLat, lon: cLon, count: 1 });
  }
  return [...cells.values()];
}

function nearestWeatherCell(lat, lon) {
  let best = null;
  let bestDist = Infinity;
  for (const cell of weatherCells) {
    const [clon, clat] = cell.geometry.coordinates;
    const d = haversineDeg(lat, lon, clat, clon);
    if (d < bestDist) {
      bestDist = d;
      best = cell;
    }
  }
  return best;
}

function zonesForStorico() {
  const filtered = occurrences.filter((f) => activeSpecies.has(f.properties.species));
  const cells = buildHistoricalGrid(filtered);
  const maxCount = Math.max(1, ...cells.map((c) => c.count));
  return cells.map((c) => ({ lat: c.lat, lon: c.lon, value: c.count / maxCount }));
}

function zonesForMeteo() {
  const raw = weatherCells.map((f) => {
    const lat = f.geometry.coordinates[1];
    const lon = f.geometry.coordinates[0];
    const conditions = conditionsQuality(f.properties.soil_moisture, f.properties.humidity_pct);
    const readiness = activeSpeciesReadinessAt(
      f.properties.daily_dates,
      f.properties.daily_precip_mm,
      f.properties.elevation_m,
      lat
    );
    // umidità generale del suolo/aria × finestra di incubazione pioggia →
    // fruttificazione specifica delle specie selezionate (soglia di pioggia
    // e ritardo tipico) × idoneità del tipo di bosco: senza questo, "Meteo
    // attuale" ignorerebbe sia il filtro specie sia i tempi di comparsa
    return { lat, lon, value: (0.3 + 0.7 * conditions) * readiness };
  });
  const maxValue = Math.max(0.01, ...raw.map((z) => z.value));
  return raw.map((z) => ({ ...z, value: z.value / maxValue })).filter((z) => z.value > 0.06);
}

function zonesForCombinato() {
  const filtered = occurrences.filter((f) => activeSpecies.has(f.properties.species));
  const cells = buildHistoricalGrid(filtered);
  const maxCount = Math.max(1, ...cells.map((c) => c.count));

  const raw = cells.map((c) => {
    const norm = c.count / maxCount;
    const nearest = nearestWeatherCell(c.lat, c.lon);
    const conditions = nearest ? conditionsQuality(nearest.properties.soil_moisture, nearest.properties.humidity_pct) : 0.4;
    const habitat = nearest ? (nearest.properties.habitat_score ?? 0.5) : 0.5;
    const elevation = nearest ? nearest.properties.elevation_m : null;
    const readiness = nearest
      ? activeSpeciesReadinessAt(nearest.properties.daily_dates, nearest.properties.daily_precip_mm, elevation, c.lat)
      : 0.5;
    // storico locale × condizioni generali × idoneità del luogo (quota +
    // fascia boschiva) × finestra pioggia-fruttificazione delle specie
    // selezionate. Ogni fattore ha un pavimento: un ritrovamento storico
    // reale resta un'evidenza concreta anche quando il nostro modello
    // quota/pioggia per quel punto fosse impreciso
    const value = norm * (0.3 + 0.7 * conditions) * (0.3 + 0.7 * habitat) * (0.3 + 0.7 * readiness);
    return { lat: c.lat, lon: c.lon, value };
  });

  const maxValue = Math.max(0.01, ...raw.map((z) => z.value));
  return raw.map((z) => ({ ...z, value: z.value / maxValue }));
}

function zoneRadiusMeters() {
  if (mode === "meteo") return weatherGridStepDeg * 111000 * 0.85;
  return FINE_GRID_STEP_DEG * 111000 * 0.75;
}

function drawZones(zones) {
  zonesLayer.setZones(zones, zoneRadiusMeters());
}

function render() {
  let zones = [];
  const activeCount = occurrences.filter((f) => activeSpecies.has(f.properties.species)).length;

  if (mode === "storico") {
    zones = zonesForStorico();
  } else if (mode === "meteo") {
    zones = zonesForMeteo();
  } else {
    zones = zonesForCombinato();
  }

  const badge = document.getElementById("countBadge");
  if (mode === "storico") badge.textContent = `${activeCount} osservazioni storiche in ${zones.length} zone (GBIF + iNaturalist)`;
  else if (mode === "meteo") badge.textContent = `${zones.length} zone meteo (Open-Meteo)`;
  else badge.textContent = `${activeCount} osservazioni storiche in ${zones.length} zone, pesate per il meteo attuale`;

  drawZones(zones);
}

/* ---------------- Click-to-inspect weather popup ---------------- */

function soilPct(soilMoisture) {
  if (soilMoisture == null) return null;
  const pct = ((soilMoisture - 0.05) / (0.4 - 0.05)) * 100;
  return Math.max(0, Math.min(100, Math.round(pct)));
}

function popupSkeleton(lat, lon) {
  return `
    <div class="wx-popup">
      <p class="wx-coords">${lat.toFixed(3)}°, ${lon.toFixed(3)}°</p>
      <div class="wx-loading"><span class="wx-spinner"></span> Recupero dati meteo…</div>
    </div>`;
}

function popupError(lat, lon) {
  return `
    <div class="wx-popup">
      <p class="wx-coords">${lat.toFixed(3)}°, ${lon.toFixed(3)}°</p>
      <div class="wx-error">Dati meteo non disponibili al momento.</div>
    </div>`;
}

// Mini grafico a colonne (SVG) con la pioggia giornaliera degli ultimi 15
// giorni. Barre con cima arrotondata su una singola serie (nessuna legenda
// necessaria: il titolo dice già cosa mostra); ogni barra ha un'area di
// tocco a tutta altezza per un hover/tap affidabile anche sui giorni a 0mm,
// ed etichette solo alle due estremità dell'asse per restare leggibile in
// poco spazio.
function buildRainChartHtml(dates, precip, highlight = null) {
  const n = 15;
  const d = dates.slice(-n);
  const p = precip.slice(-n).map((v) => v || 0);
  if (!d.length) return "";
  const inHighlight = (dateStr) => !!highlight && dateStr >= highlight.start && dateStr <= highlight.end;

  const width = 248;
  const chartTop = 4;
  const chartBottom = 60;
  const chartHeight = chartBottom - chartTop;
  const gap = 2;
  const barWidth = (width - gap * (d.length - 1)) / d.length;
  const maxMm = Math.max(...p, 5);
  const total = p.reduce((a, b) => a + b, 0);

  const bars = d
    .map((dateStr, i) => {
      const mm = p[i];
      const x = i * (barWidth + gap);
      const h = Math.max(0, (mm / maxMm) * chartHeight);
      const y = chartBottom - h;
      const r = Math.min(2.5, barWidth / 2, h);
      const isToday = i === d.length - 1;
      const highlighted = inHighlight(dateStr);
      const barPath =
        h <= 0.5
          ? ""
          : `M${x},${chartBottom} L${x},${y + r} Q${x},${y} ${x + r},${y} ` +
            `L${x + barWidth - r},${y} Q${x + barWidth},${y} ${x + barWidth},${y + r} ` +
            `L${x + barWidth},${chartBottom} Z`;
      return `
        <g class="wx-rain-bar${highlighted ? " wx-rain-bar-highlight" : ""}">
          <rect class="wx-rain-slot" x="${x}" y="${chartTop}" width="${barWidth}" height="${chartHeight}" rx="2"></rect>
          <path d="${barPath}" fill="var(--wet)" opacity="${isToday || highlighted ? 1 : 0.75}"></path>
          <rect class="wx-rain-hit" x="${x}" y="0" width="${barWidth}" height="${chartBottom + 4}" fill="transparent"
            data-date="${dateStr}" data-mm="${mm.toFixed(1)}"
            onmouseenter="showRainTip(this)" onmouseleave="hideRainTip()"
            onclick="showRainTip(this)"></rect>
        </g>`;
    })
    .join("");

  const firstLabel = fmtDateShort(d[0]);
  const lastLabel = "oggi";
  const defaultCaption = highlight
    ? `${fmtDateRange(highlight.start, highlight.end)}: ${highlight.mm}mm`
    : `Totale: ${Math.round(total)}mm`;

  return `
    <div class="wx-rain-section">
      <div class="wx-rain-header">
        <span class="wx-rain-title">Pioggia ultimi 15gg</span>
        <span class="wx-rain-caption" data-default="${defaultCaption}">${defaultCaption}</span>
      </div>
      <svg class="wx-rain-chart" viewBox="0 0 ${width} 74" preserveAspectRatio="none">
        <line x1="0" y1="${chartBottom}" x2="${width}" y2="${chartBottom}" stroke="var(--border)" stroke-width="1"></line>
        ${bars}
        <text x="0" y="72" font-size="9" fill="var(--text-faint)">${firstLabel}</text>
        <text x="${width}" y="72" font-size="9" fill="var(--text-faint)" text-anchor="end">${lastLabel}</text>
      </svg>
    </div>`;
}

function popupContent(lat, lon, data, openIdx = null) {
  const daily = data.daily || {};
  const dates = daily.time || [];
  const precip = daily.precipitation_sum || [];

  let daysSinceRain = null;
  const today = dates.length ? new Date(dates[dates.length - 1] + "T00:00:00") : new Date();
  for (let i = dates.length - 1; i >= 0; i--) {
    if (precip[i] != null && precip[i] >= 1.0) {
      daysSinceRain = Math.round((today - new Date(dates[i] + "T00:00:00")) / 86400000);
      break;
    }
  }
  const rainShort = daysSinceRain == null ? "nessuna (16gg)" : daysSinceRain === 0 ? "oggi" : `${daysSinceRain}gg fa`;

  const hourly = data.hourly || {};
  const humiditySeries = (hourly.relative_humidity_2m || []).filter((v) => v != null);
  const soilSeries = (hourly.soil_moisture_0_to_1cm || []).filter((v) => v != null);
  const humidity = humiditySeries.length ? Math.round(humiditySeries[humiditySeries.length - 1]) : null;
  const soil = soilSeries.length ? soilSeries[soilSeries.length - 1] : null;
  const soilPercent = soilPct(soil);

  const elevation = typeof data.elevation === "number" ? Math.round(data.elevation) : null;
  const veg = vegetationInfo(lat, lon, elevation);
  const species = speciesReadinessList(dates, precip, elevation, lat);

  const openSpecies = openIdx != null ? species[openIdx] : null;
  const highlight =
    openSpecies && openSpecies.eventDate
      ? { start: openSpecies.windowStartDate, end: openSpecies.eventDate, mm: openSpecies.eventMm }
      : null;

  const tile = (icon, label, value) => `
    <div class="wx-tile">
      ${icon}
      <div>
        <p class="wx-tile-label">${label}</p>
        <p class="wx-tile-value">${value}</p>
      </div>
    </div>`;

  const speciesRow = (r, idx) => {
    const badge = speciesStatusBadge(r);
    const days = r.daysSince != null ? `${r.daysSince}gg` : "—";
    const isOpen = idx === openIdx;
    const detailHtml = isOpen ? `<li class="wx-species-detail">${speciesDetailText(r)}</li>` : "";
    return `
      <li class="wx-species-row${isOpen ? " wx-species-row-open" : ""}" data-idx="${idx}" onclick="toggleSpeciesDetail(${idx})">
        <span class="wx-species-dot" style="background:${r.color}"></span>
        <span class="wx-species-name">${r.label}</span>
        <span class="wx-species-days">${days}</span>
        <span class="wx-species-badge wx-badge-${badge.cls}">${badge.word}</span>
        <span class="wx-chevron">›</span>
      </li>${detailHtml}`;
  };

  return `
    <div class="wx-popup">
      <p class="wx-coords">${lat.toFixed(3)}°, ${lon.toFixed(3)}°</p>

      <div class="wx-grid">
        ${tile(ICONS.rain, "Pioggia", rainShort)}
        ${tile(ICONS.elevation, "Quota", elevation != null ? elevation + " m" : "n/d")}
        ${tile(ICONS.soil, "Terreno", soilPercent != null ? soilPercent + "%" : "n/d")}
        ${tile(ICONS.humidity, "Aria", humidity != null ? humidity + "%" : "n/d")}
      </div>

      ${buildRainChartHtml(dates, precip, highlight)}

      <div class="wx-forest-line">
        ${ICONS.forest}
        <span>${veg.typeLabel}</span>
      </div>

      <p class="wx-section-title">Specie tracciate <span class="wx-section-hint">(tocca per il motivo)</span></p>
      <ul class="wx-species-list">
        ${species.map(speciesRow).join("")}
      </ul>
    </div>`;
}

// Espande/richiude la spiegazione ("perché pronto/in arrivo/non ora") sotto
// la riga di una specie nel popup. Esposta su window perché il contenuto
// del popup è iniettato come stringa HTML (onclick inline). Leaflet
// ri-renderizza SEMPRE l'intero popup dalla stringa quando lo si aggiorna,
// quindi qui rigeneriamo tutto il contenuto invece di editare il DOM a mano
// (un tentativo precedente che manipolava il DOM veniva cancellato subito
// dopo dalla successiva chiamata a popup.update()).
window.toggleSpeciesDetail = function (idx) {
  if (!activePopupInstance || !lastPopupParams) return;
  openSpeciesIdx = openSpeciesIdx === idx ? null : idx;
  const { lat, lon, data } = lastPopupParams;
  activePopupInstance.setContent(popupContent(lat, lon, data, openSpeciesIdx));
};

// Hover/tap su una barra del grafico pioggia: legge data/mm direttamente
// dall'elemento (nessuno stato globale da tenere sincronizzato col popup).
window.showRainTip = function (el) {
  const caption = el.closest(".wx-rain-section")?.querySelector(".wx-rain-caption");
  if (!caption) return;
  const label = fmtDateShort(el.dataset.date);
  caption.textContent = `${label} — ${el.dataset.mm}mm`;
};
window.hideRainTip = function () {
  document.querySelectorAll(".wx-rain-caption").forEach((el) => {
    el.textContent = el.dataset.default || "";
  });
};

function onMapClick(e) {
  const { lat, lng } = e.latlng;
  openSpeciesIdx = null;
  const popup = L.popup({ className: "wx-leaflet-popup", maxWidth: 320, minWidth: 280 })
    .setLatLng(e.latlng)
    .setContent(popupSkeleton(lat, lng))
    .openOn(map);
  activePopupInstance = popup;
  lastPopupParams = null;

  // Leaflet ferma la propagazione di mousedown/touchstart dal popup verso la
  // mappa, ma NON quella dell'evento "click" vero e proprio: un tocco sulle
  // righe specie arrivava fino al listener di click della mappa e apriva un
  // secondo popup sopra quello attuale (per questo "non si cliccava" — in
  // realtà il popup sbagliato si richiudeva/sostituiva subito). Fermiamo
  // esplicitamente anche "click" sul contenuto del popup.
  const popupEl = popup.getElement();
  if (popupEl) L.DomEvent.on(popupEl, "click", L.DomEvent.stopPropagation);

  const url =
    `https://api.open-meteo.com/v1/forecast?latitude=${lat.toFixed(4)}&longitude=${lng.toFixed(4)}` +
    `&daily=precipitation_sum&hourly=relative_humidity_2m,soil_moisture_0_to_1cm` +
    `&past_days=16&forecast_days=1&timezone=auto`;

  fetch(url)
    .then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then((data) => {
      if (map.hasLayer(popup)) {
        lastPopupParams = { lat, lon: lng, data };
        popup.setContent(popupContent(lat, lng, data, openSpeciesIdx));
      }
    })
    .catch((err) => {
      console.error(err);
      if (map.hasLayer(popup)) popup.setContent(popupError(lat, lng));
    });
}

/* ---------------- Init ---------------- */

buildFilters();
buildModeSwitch();
updateLegend();
map.on("click", onMapClick);

Promise.all([
  fetch("data/occurrences.geojson").then((r) => r.json()),
  fetch("data/weather_grid.geojson").then((r) => r.json()),
])
  .then(([occGeojson, weatherGeojson]) => {
    occurrences = occGeojson.features;
    weatherCells = weatherGeojson.features;
    weatherGridStepDeg = weatherGeojson.grid_step_deg || 0.5;
    render();
  })
  .catch((err) => {
    console.error(err);
    document.getElementById("countBadge").textContent = "Errore nel caricamento dati";
  });
