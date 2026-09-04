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

// Tipo di copertura del suolo REALE per ogni cella della griglia meteo,
// da Corine Land Cover (Copernicus/EEA) — vedi scripts/fetch_weather_grid.py.
// broadleaf/conifer/mixed/shrub/none, non più una stima da quota+latitudine.
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

function speciesAffinityAt(species, vegClass, elevation) {
  const base = SPECIES_VEG_AFFINITY[species]?.[vegClass ?? "none"] ?? 0.3;
  return base * elevationFactor(species, elevation);
}

// Etichetta leggibile per il popup: il tipo (latifoglie/conifere/misto) è
// il dato Corine reale; la quota affina solo la parola usata (es. faggeta
// vs querceto) per restare più specifica di quanto Corine da solo dica.
function vegetationTypeLabel(vegClass, elevation) {
  if (vegClass === "broadleaf") return elevation != null && elevation > 700 ? "Faggeta" : "Querceto / castagneto";
  if (vegClass === "conifer") return elevation != null && elevation > 1400 ? "Conifere d'alta quota" : "Pineta";
  if (vegClass === "mixed") return "Bosco misto (latifoglie e conifere)";
  if (vegClass === "shrub") return "Macchia / vegetazione arbustiva";
  if (vegClass === "none") return "Nessun bosco significativo qui";
  return "Tipo di bosco non determinato";
}

function vegetationInfo(vegClass, elevation) {
  return { typeLabel: vegetationTypeLabel(vegClass, elevation) };
}

// codici Corine Land Cover rilevanti per la presenza di un vero bosco (vedi
// anche CLC_VEG_CLASS in scripts/fetch_weather_grid.py, stessa mappatura)
const CLC_VEG_CLASS = { 311: "broadleaf", 312: "conifer", 313: "mixed", 324: "shrub" };
const CLC_IDENTIFY_URL = "https://image.discomap.eea.europa.eu/arcgis/rest/services/Corine/CLC2018_WM/MapServer/identify";

function latLonToWebMercator(lat, lon) {
  const x = (lon * 20037508.34) / 180;
  const y = (Math.log(Math.tan(((90 + lat) * Math.PI) / 360)) / (Math.PI / 180)) * (20037508.34 / 180);
  return { x, y };
}

// Vegetazione REALE nel punto esatto cliccato, interrogata dal vivo nel
// browser (Corine Land Cover ha CORS aperto — verificato) invece che presa
// dalla cella meteo più vicina (fino a 30-40km di distanza, troppo per
// dire con affidabilità se lì c'è bosco o no): stessa fonte usata per
// popolare i dati server-side, ma qui alla precisione del singolo click.
async function fetchClcVegClass(lat, lon) {
  const { x, y } = latLonToWebMercator(lat, lon);
  const params = new URLSearchParams({
    geometry: JSON.stringify({ x, y, spatialReference: { wkid: 3857 } }),
    geometryType: "esriGeometryPoint",
    sr: "3857",
    layers: "all",
    tolerance: "1",
    mapExtent: "0,0,10,10",
    imageDisplay: "10,10,96",
    returnGeometry: "false",
    f: "json",
  });
  const resp = await fetch(`${CLC_IDENTIFY_URL}?${params}`);
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  const data = await resp.json();
  const results = data.results || [];
  let vectorCode = null;
  let rasterCode = null;
  for (const r of results) {
    const attrs = r.attributes || {};
    if (r.layerId === 0 && attrs.Code_18) vectorCode = attrs.Code_18;
    else if (r.layerId === 1 && attrs["Raster.CODE_18"]) rasterCode = attrs["Raster.CODE_18"];
  }
  return CLC_VEG_CLASS[vectorCode || rasterCode] ?? "none";
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
function activeSpeciesReadinessAt(dailyDates, dailyPrecip, vegClass, elevation) {
  if (activeSpecies.size === 0) return 0.5;
  let best = 0;
  for (const sp of activeSpecies) {
    const rain = speciesRainReadiness(sp, dailyDates, dailyPrecip).score;
    const tree = speciesAffinityAt(sp, vegClass, elevation);
    best = Math.max(best, rain * tree);
  }
  return best;
}

// Stato combinato (pioggia nella finestra giusta × tipo di bosco adatto)
// per tutte e 4 le specie tracciate in un punto esatto — la stessa formula
// usata per colorare la mappa, così il popup e la mappa raccontano sempre
// la stessa storia. Ordinato dalla più alla meno pronta in questo momento.
function speciesReadinessList(dailyDates, dailyPrecip, vegClass, elevation) {
  return Object.keys(SPECIES_META)
    .map((sp) => {
      const rain = speciesRainReadiness(sp, dailyDates, dailyPrecip);
      const tree = speciesAffinityAt(sp, vegClass, elevation);
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
    desc: "Concentrazione di segnalazioni passate per le specie selezionate.",
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

/* ---------------- Motore mappa ----------------
   Un unico motore (MapLibre GL, WebGL) per tutti e tre i tipi di mappa:
   stradale, satellite e rilievo 3D. Prima la 2D girava su Leaflet e solo
   il 3D su MapLibre, ma Leaflet non ha il concetto di "bearing": la
   rotazione della mappa — che qui serve su tutti i tipi — non era
   implementabile lì. Con un motore solo, zoom/rotazione/inclinazione si
   comportano allo stesso modo ovunque e i controlli sono gli stessi.

   Tutte le sorgenti sono gratuite e senza chiave API, stesso criterio già
   usato per il geocoding della ricerca (Nominatim): OpenStreetMap per lo
   stradale, Esri World Imagery per il satellite, ed elevazione dai tile
   aperti "Terrarium" (progetto Mapzen, oggi ospitati su AWS Open Data).

   NB sui livelli di zoom: MapLibre usa tile logici da 512px, Leaflet da
   256px, quindi lo stesso inquadramento vale un livello in meno qui
   rispetto ai valori usati in precedenza. */
const ESRI_ATTRIB =
  "Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community";

const MAP_STYLE = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: [
        "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "https://c.tile.openstreetmap.org/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      maxzoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    },
    esriImagery: {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      maxzoom: 19,
      attribution: ESRI_ATTRIB,
    },
    esriLabels: {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      maxzoom: 19,
    },
    terrainDem: {
      type: "raster-dem",
      tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
      tileSize: 256,
      encoding: "terrarium",
      maxzoom: 15,
    },
  },
  layers: [
    { id: "osm", type: "raster", source: "osm" },
    { id: "esriImagery", type: "raster", source: "esriImagery", layout: { visibility: "none" } },
    { id: "esriLabels", type: "raster", source: "esriLabels", layout: { visibility: "none" } },
  ],
};

const map = new maplibregl.Map({
  container: "map",
  style: MAP_STYLE,
  center: [12.8, 42.3],
  zoom: 5,
  bearing: 0,
  pitch: 0,
  maxPitch: 85,
  attributionControl: false,
});
// in basso a sinistra: l'angolo opposto rispetto al badge "Probabilità
// stimata" (in basso a destra), così i crediti non ci finiscono mai dietro
map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-left");
// la libreria apre da sola i crediti (testo esteso) al primo render;
// li richiudiamo una sola volta così la pagina si carica con la sola
// "i", senza scritte. Dopo questa prima apertura automatica il pulsante
// non si riapre più da solo (vedi _updateCompact di MapLibre), quindi
// l'observer può fermarsi al primo intervento senza interferire con i
// click dell'utente sul pulsante "i"
const attribEl = document.querySelector(".maplibregl-ctrl-attrib");
if (attribEl) {
  const closeAttribOnce = () => {
    attribEl.removeAttribute("open");
    attribEl.classList.remove("maplibregl-compact-show");
    attribObserver.disconnect();
  };
  const attribObserver = new MutationObserver(closeAttribOnce);
  attribObserver.observe(attribEl, { attributes: true, attributeFilter: ["open"] });
}
map.dragRotate.enable();
map.touchZoomRotate.enable();
// due dita che ruotano una rispetto all'altra = rotazione della mappa
map.touchZoomRotate.enableRotation();

let mapStyleReady = false;

const MAP_TYPE_STORAGE_KEY = "mappaFunghi.mapType";
const MAP_TYPE_LABELS = { street: "Standard", satellite: "Satellite", "3d": "Rilievo 3D" };
let mapType = ["street", "satellite", "3d"].includes(localStorage.getItem(MAP_TYPE_STORAGE_KEY))
  ? localStorage.getItem(MAP_TYPE_STORAGE_KEY)
  : "street";

// inclinazione corrente scelta con lo slider: ricordata anche quando si
// esce dal 3D, così rientrando si ritrova l'angolo che si era impostato
let tiltDegrees = 60;

// Fuori dal rilievo 3D la camera deve restare perfettamente a picco: la
// heatmap è disegnata in coordinate schermo, e con la vista inclinata i
// cerchi delle zone si deformerebbero rispetto al terreno. Non basta
// riportare l'inclinazione a zero, va anche impedita: senza questo si
// potrebbe comunque inclinare col tasto destro (desktop) o due dita
// (mobile). maxPitch 0 la blocca alla radice, qualunque sia l'input.
// Allineiamo guardando la camera reale, non solo la transizione, così il
// vincolo vale anche all'avvio quando si riapre il sito con una
// preferenza già salvata.
function applyPitchForMapType(type, animate) {
  const is3d = type === "3d";
  map.setMaxPitch(85);
  const wanted = is3d ? tiltDegrees : 0;
  const lockIf2D = () => {
    if (mapType !== "3d") map.setMaxPitch(0);
  };
  if (Math.abs(map.getPitch() - wanted) <= 0.5) {
    lockIf2D();
    return;
  }
  if (animate) {
    map.easeTo({ pitch: wanted, duration: 600 });
    map.once("moveend", lockIf2D);
  } else {
    map.jumpTo({ pitch: wanted });
    lockIf2D();
  }
}

function applyMapTypeSources(type) {
  const is3d = type === "3d";
  const aerial = is3d || type === "satellite";
  map.setLayoutProperty("osm", "visibility", aerial ? "none" : "visible");
  map.setLayoutProperty("esriImagery", "visibility", aerial ? "visible" : "none");
  map.setLayoutProperty("esriLabels", "visibility", aerial ? "visible" : "none");
  map.setTerrain(is3d ? { source: "terrainDem", exaggeration: 1.5 } : null);
}

function setMapType(type) {
  const was3d = mapType === "3d";
  const is3d = type === "3d";
  mapType = type;
  localStorage.setItem(MAP_TYPE_STORAGE_KEY, type);

  if (mapStyleReady) applyMapTypeSources(type);

  // guida il resto della UI specifica del 3D via CSS (badge modalità
  // nascosto, nome del sito al loro posto su mobile, bussola sempre
  // visibile anche a nord in alto) — vedi le regole "body.map-3d-active"
  document.body.classList.toggle("map-3d-active", is3d);

  // legenda, filtro specie e heatmap descrivono i dati sui funghi: nel
  // rilievo 3D non vengono disegnati (la vista serve a leggere pendenza
  // ed esposizione dei versanti), quindi lì spariscono anche i controlli
  const legend = document.getElementById("legend");
  if (legend) legend.style.display = is3d ? "none" : "";
  const filtersToggle = document.getElementById("filtersToggle");
  if (filtersToggle) filtersToggle.style.display = is3d ? "none" : "";
  const speciesFilters = document.getElementById("filters");
  if (speciesFilters) speciesFilters.style.display = is3d ? "none" : "";
  heatCanvas.style.display = is3d ? "none" : "";

  // lo slider di inclinazione ha senso solo dove il terreno ha rilievo
  const tiltControl = document.getElementById("tiltControl");
  if (tiltControl) tiltControl.hidden = !is3d;
  applyPitchForMapType(type, was3d !== is3d);

  document.querySelectorAll("#layerMenuList button[data-layer]").forEach((btn) => {
    btn.setAttribute("aria-checked", String(btn.dataset.layer === type));
  });
  const toggle = document.getElementById("layerToggle");
  if (toggle) toggle.setAttribute("aria-label", "Tipo di mappa: " + MAP_TYPE_LABELS[type]);

  updateLayerToggleOffset();
  if (!is3d) render();
}

function setupLayerMenu() {
  const menu = document.getElementById("layerMenu");
  const toggle = document.getElementById("layerToggle");
  const list = document.getElementById("layerMenuList");

  const closeMenu = () => {
    menu.classList.remove("open");
    toggle.setAttribute("aria-expanded", "false");
  };
  const openMenu = () => {
    menu.classList.add("open");
    toggle.setAttribute("aria-expanded", "true");
  };
  toggle.addEventListener("click", () => {
    if (menu.classList.contains("open")) closeMenu();
    else openMenu();
  });
  list.querySelectorAll("button[data-layer]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setMapType(btn.dataset.layer);
      closeMenu();
    });
  });
  document.addEventListener("click", (e) => {
    if (!menu.contains(e.target)) closeMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menu.classList.contains("open")) closeMenu();
  });
}
setupLayerMenu();

// Il pulsante vive sopra al badge "Probabilità stimata", nello stesso
// angolo: la sua altezza cambia (aperto/chiuso, nascosto durante il menu
// specie), quindi ne misuriamo la posizione reale invece di un valore
// fisso, che si sarebbe disallineato ad ogni ritocco della legenda. In
// vista 3D la legenda è nascosta: torniamo alla posizione base da CSS.
function updateLayerToggleOffset() {
  const legend = document.getElementById("legend");
  const menu = document.getElementById("layerMenu");
  if (!legend || !menu) return;
  if (legend.classList.contains("hidden-for-filters")) {
    menu.style.visibility = "hidden";
    return;
  }
  menu.style.visibility = "visible";
  if (getComputedStyle(legend).display === "none") {
    menu.style.bottom = "";
    return;
  }
  const gap = 12;
  const rect = legend.getBoundingClientRect();
  menu.style.bottom = window.innerHeight - rect.top + gap + "px";
}
window.addEventListener("resize", updateLayerToggleOffset);
document.getElementById("legend").addEventListener("transitionend", (e) => {
  if (e.propertyName === "max-height" || e.propertyName === "width") updateLayerToggleOffset();
});
updateLayerToggleOffset();

new ResizeObserver(() => {
  map.resize();
  resizeHeatCanvas();
  scheduleHeatRedraw();
}).observe(document.getElementById("map"));

/* ---------------- Controlli camera (zoom / rotazione / inclinazione) ---- */

function setupMapControls() {
  document.getElementById("zoomIn").addEventListener("click", () => map.zoomIn());
  document.getElementById("zoomOut").addEventListener("click", () => map.zoomOut());

  const controls = document.getElementById("mapControls");
  const compass = document.getElementById("compassBtn");
  const needle = document.getElementById("compassNeedle");

  // l'ago punta sempre al nord reale: ruota in senso opposto alla mappa
  const syncCompass = () => {
    const bearing = map.getBearing();
    needle.style.transform = `rotate(${-bearing}deg)`;
    controls.classList.toggle("is-rotated", Math.abs(bearing) > 0.5);
  };
  map.on("rotate", syncCompass);
  syncCompass();

  // trascinando la bussola si ruota la mappa con continuità; un tocco
  // secco (senza trascinare) rimette il nord in alto, come su Google Maps
  let dragging = false;
  let dragStartX = 0;
  let dragStartBearing = 0;
  let dragMoved = false;

  compass.addEventListener("pointerdown", (e) => {
    dragging = true;
    dragMoved = false;
    dragStartX = e.clientX;
    dragStartBearing = map.getBearing();
    // in rari casi limite (timing particolari, dispositivi non standard)
    // il puntatore può risultare già inattivo qui: non è un errore da
    // bloccare il gesto, il trascinamento funziona comunque via
    // pointermove sul documento
    try {
      compass.setPointerCapture(e.pointerId);
    } catch (err) {
      /* ignorato di proposito, vedi sopra */
    }
  });
  compass.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - dragStartX;
    if (Math.abs(dx) > 3) dragMoved = true;
    if (dragMoved) map.setBearing(dragStartBearing + dx * 0.8);
  });
  const endCompassDrag = (e) => {
    if (!dragging) return;
    dragging = false;
    try {
      if (compass.hasPointerCapture(e.pointerId)) compass.releasePointerCapture(e.pointerId);
    } catch (err) {
      /* ignorato di proposito, vedi sopra */
    }
    if (!dragMoved) map.easeTo({ bearing: 0, duration: 400 });
  };
  compass.addEventListener("pointerup", endCompassDrag);
  compass.addEventListener("pointercancel", endCompassDrag);

  const tiltRange = document.getElementById("tiltRange");
  tiltRange.addEventListener("input", () => {
    tiltDegrees = Number(tiltRange.value);
    if (mapType === "3d") map.setPitch(tiltDegrees);
  });
  // l'inclinazione si può cambiare anche col gesto a due dita: teniamo lo
  // slider allineato a quello che fa davvero la camera
  map.on("pitch", () => {
    if (mapType !== "3d") return;
    tiltDegrees = Math.round(map.getPitch());
    tiltRange.value = String(tiltDegrees);
  });
  tiltRange.value = String(tiltDegrees);
}
setupMapControls();

// Il contenuto del popup meteo viene rigenerato per intero ad ogni
// aggiornamento (vedi toggleSpeciesDetail): unico punto di scrittura.
function setPopupHTML(popup, html) {
  popup.setHTML(html);
}

/* ---------------- Mappa di calore (overlay canvas) ----------------
   I punti sono resi come sfumature radiali che si sovrappongono e si
   fondono (come una vera heatmap), non come cerchi pieni dal bordo netto.
   Il raggio di ogni zona è un valore reale in metri (vedi zoneRadiusMeters)
   riconvertito in pixel ad ogni ridisegno in base a zoom e latitudine:
   le zone occupano sempre la stessa area sul terreno e non si restringono
   zoomando.

   È un canvas sovrapposto alla mappa e ridisegnato in coordinate schermo:
   con la mappa ruotata resta corretto lo stesso, perché a inclinazione
   zero un cerchio sul terreno resta un cerchio sullo schermo qualunque sia
   l'orientamento. Nel rilievo 3D (inclinazione > 0, dove la prospettiva
   deformerebbe i cerchi) la heatmap non viene disegnata affatto. */
const heatCanvas = document.createElement("canvas");
heatCanvas.className = "heat-canvas";
document.getElementById("map").appendChild(heatCanvas);

let heatZones = [];
let heatRedrawQueued = false;

function resizeHeatCanvas() {
  const el = document.getElementById("map");
  const w = el.clientWidth;
  const h = el.clientHeight;
  if (heatCanvas.width !== w) heatCanvas.width = w;
  if (heatCanvas.height !== h) heatCanvas.height = h;
}

// la mappa emette "move" in continuo durante trascinamento/zoom/rotazione:
// accodiamo un solo ridisegno per frame invece di uno per evento
function scheduleHeatRedraw() {
  if (heatRedrawQueued) return;
  heatRedrawQueued = true;
  requestAnimationFrame(() => {
    heatRedrawQueued = false;
    drawHeatCanvas();
  });
}

// Durante un trascinamento/zoom/rotazione, MapLibre ridisegna la sua scena
// WebGL a ogni fotogramma (accelerata via GPU): se in parallelo ricalcoliamo
// da zero l'intera griglia della heatmap a ogni fotogramma, il ricalcolo
// (851 zone, tutte proiettate e sommate su una griglia) compete sul thread
// principale e la quantizzazione della griglia rada (CELL px) cade su
// pixel leggermente diversi a ogni fotogramma: il risultato è lo
// "sfarfallio"/vibrazione dei colori osservato, oltre a un movimento meno
// fluido. La stessa mappa in versione Leaflet non aveva questo problema
// perché durante il trascinamento non ricalcolava affatto: lasciava
// scorrere l'immagine già disegnata insieme al resto della mappa.
//
// Replichiamo lo stesso comportamento: durante il gesto il canvas non
// viene ridisegnato, solo trasformato via CSS (transform) in modo che
// segua esattamente il movimento della mappa sotto di lui — costo quasi
// nullo, nessun ricalcolo. Il ricalcolo vero, ad alta definizione, avviene
// solo a gesto concluso ("moveend"), quando il movimento si è già fermato.
let heatRideRef = null;

function captureHeatRideRef() {
  const w = heatCanvas.width;
  const h = heatCanvas.height;
  if (w === 0 || h === 0) {
    heatRideRef = null;
    return;
  }
  const corners = [
    [0, 0],
    [w, 0],
    [0, h],
  ];
  heatRideRef = {
    w,
    h,
    lngLats: corners.map(([x, y]) => {
      const ll = map.unproject([x, y]);
      return [ll.lng, ll.lat];
    }),
  };
}

function applyHeatRide() {
  if (!heatRideRef) return;
  const [d0, d1, d2] = heatRideRef.lngLats.map((ll) => map.project(ll));
  const { w, h } = heatRideRef;
  // (0,0)->d0, (w,0)->d1, (0,h)->d2: risolvendo x'=a·x+c·y+e, y'=b·x+d·y+f
  // per queste tre corrispondenze si ottiene direttamente, senza inversioni
  const e = d0.x;
  const f = d0.y;
  const a = (d1.x - e) / w;
  const b = (d1.y - f) / w;
  const c = (d2.x - e) / h;
  const d = (d2.y - f) / h;
  heatCanvas.style.transform = `matrix(${a}, ${b}, ${c}, ${d}, ${e}, ${f})`;
}

function resetHeatRide() {
  heatRideRef = null;
  heatCanvas.style.transform = "";
}

function drawHeatCanvas() {
  // qualunque ridisegno vero e proprio invalida il "passaggio" via
  // transform CSS lasciato da un eventuale gesto in corso: il contenuto
  // che stiamo per disegnare è già alla posizione/risoluzione corrette
  heatRideRef = null;
  heatCanvas.style.transform = "";
  const ctx = heatCanvas.getContext("2d");
  const w = heatCanvas.width;
  const h = heatCanvas.height;
  if (w === 0 || h === 0) return;
  ctx.clearRect(0, 0, w, h);
  if (mapType === "3d" || !heatZones.length) return;

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

  // metri per pixel a una data latitudine: MapLibre ragiona su tile logici
  // da 512px, quindi circonferenza terrestre / (512 * 2^zoom)
  const zoomScale = Math.pow(2, map.getZoom());

  for (const z of heatZones) {
    if (z.value <= 0.03) continue;
    const p = map.project([z.lon, z.lat]);
    const metersPerPixel = (78271.516964020484 * Math.cos((z.lat * Math.PI) / 180)) / zoomScale;
    const r = z.r / metersPerPixel;
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
}

// "movestart"/"move"/"moveend" coprono insieme trascinamento, zoom,
// rotazione e inclinazione: qualunque cambiamento della camera
map.on("movestart", captureHeatRideRef);
map.on("move", applyHeatRide);
map.on("moveend", () => {
  resetHeatRide();
  drawHeatCanvas();
});
resizeHeatCanvas();

let occurrences = [];
let weatherCells = [];
let weatherGridStepDeg = 0.5;
// vegetazione/quota REALI alla risoluzione fine (0.15°, la stessa di
// buildHistoricalGrid), precalcolate solo per le celle che contengono
// ritrovamenti storici — vedi scripts/fetch_vegetation_fine.py. Sostituisce
// la cella meteo più vicina (0.5°, fino a 30-40km di distanza) come fonte
// di vegetazione per "Combinato": un ritrovamento reale in fondovalle non
// deve più ereditare "nessun bosco" dalla vetta alpina più vicina sulla
// griglia meteo.
let vegetationFineByKey = new Map();
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
      updateFiltersToggleCount();
      render();
    });
    container.appendChild(chip);
  });
  updateFiltersToggleCount();
}

function updateFiltersToggleCount() {
  const el = document.getElementById("filtersToggleCount");
  if (el) el.textContent = activeSpecies.size;
}

// Su schermi stretti i filtri specie e la legenda diventano menu a
// comparsa (bottom sheet / sezione a scomparsa) invece di restare sempre
// visibili: altrimenti occuperebbero gran parte dello schermo prima
// ancora di mostrare la mappa. Su desktop questi controlli non fanno
// nulla di visibile (le media query disattivano lo stato "chiuso").
function setupMobileMenus() {
  const filtersToggle = document.getElementById("filtersToggle");
  const filtersEl = document.getElementById("filters");
  const scrim = document.getElementById("filtersScrim");
  const legend = document.getElementById("legend");

  // il badge legenda sta in basso a destra, proprio dove il menu specie
  // (foglio a comparsa dal basso) si apre: nascondiamolo mentre il menu
  // è aperto, altrimenti ci finisce sovrimpresso sopra
  const closeFilters = () => {
    filtersEl.classList.remove("open");
    filtersToggle.setAttribute("aria-expanded", "false");
    scrim.classList.remove("visible");
    legend.classList.remove("hidden-for-filters");
    updateLayerToggleOffset();
  };
  const openFilters = () => {
    filtersEl.classList.add("open");
    filtersToggle.setAttribute("aria-expanded", "true");
    scrim.classList.add("visible");
    legend.classList.add("hidden-for-filters");
    updateLayerToggleOffset();
  };
  filtersToggle.addEventListener("click", () => {
    if (filtersEl.classList.contains("open")) closeFilters();
    else openFilters();
  });
  scrim.addEventListener("click", closeFilters);

  // la spiegazione della legenda si apre solo al tocco del pulsante "i",
  // non più leggendo per forza il paragrafo ogni volta: la barra col
  // titolo/gradiente resta comunque sempre visibile
  const legendInfoBtn = document.getElementById("legendInfoBtn");
  legendInfoBtn.addEventListener("click", () => {
    const open = legend.classList.toggle("open");
    legendInfoBtn.setAttribute("aria-expanded", String(open));
    // su desktop l'apertura è un display toggle istantaneo (nessun
    // transitionend a cui appoggiarsi come su mobile), quindi va
    // ricalcolato subito qui
    updateLayerToggleOffset();
  });
}

// Ricerca località: usa Nominatim (OpenStreetMap), l'unico servizio di
// geocoding gratuito e senza chiave API adatto a un sito statico come
// questo. Rispettiamo la sua policy d'uso con un debounce (niente richieste
// a ogni tasto premuto) e una richiesta alla volta (le precedenti in corso
// vengono abortite).
const NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search";
const LOCATION_SEARCH_MIN_CHARS = 3;
const LOCATION_SEARCH_DEBOUNCE_MS = 400;

function shortLocationLabel(displayName) {
  return displayName.split(",").slice(0, 3).join(",").trim();
}

function setupLocationSearch() {
  const container = document.getElementById("locationSearch");
  const toolbarEl = document.getElementById("toolbar");
  const toggle = document.getElementById("locationSearchToggle");
  const form = document.getElementById("locationSearchForm");
  const input = document.getElementById("locationSearchInput");
  const clearBtn = document.getElementById("locationSearchClear");
  const resultsList = document.getElementById("locationSearchResults");

  let debounceTimer = null;
  let abortController = null;

  const hideResults = () => {
    resultsList.hidden = true;
    resultsList.innerHTML = "";
  };

  const closeSearch = () => {
    container.classList.remove("open");
    toolbarEl.classList.remove("search-open");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Cerca una località");
    hideResults();
  };
  const openSearch = () => {
    container.classList.add("open");
    toolbarEl.classList.add("search-open");
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-label", "Chiudi ricerca");
    input.focus();
  };
  toggle.addEventListener("click", () => {
    if (container.classList.contains("open")) closeSearch();
    else openSearch();
  });

  clearBtn.addEventListener("click", () => {
    input.value = "";
    form.classList.remove("has-value");
    hideResults();
    input.focus();
  });

  function renderResults(results) {
    resultsList.innerHTML = "";
    if (!results.length) {
      hideResults();
      return;
    }
    results.forEach((r) => {
      const lat = parseFloat(r.lat);
      const lon = parseFloat(r.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      const li = document.createElement("li");
      li.className = "location-search-result";
      li.textContent = shortLocationLabel(r.display_name);
      li.addEventListener("click", () => {
        map.flyTo({ center: [lon, lat], zoom: 12, duration: 1000 });
        input.value = shortLocationLabel(r.display_name);
        closeSearch();
      });
      resultsList.appendChild(li);
    });
    resultsList.hidden = false;
  }

  async function runSearch(query) {
    if (abortController) abortController.abort();
    abortController = new AbortController();
    const url =
      `${NOMINATIM_SEARCH_URL}?format=json&countrycodes=it&limit=5&accept-language=it&q=` +
      encodeURIComponent(query);
    try {
      const resp = await fetch(url, { signal: abortController.signal });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      renderResults(await resp.json());
    } catch (err) {
      if (err.name !== "AbortError") {
        console.error(err);
        renderResults([]);
      }
    }
  }

  input.addEventListener("input", () => {
    const q = input.value.trim();
    form.classList.toggle("has-value", q.length > 0);
    clearTimeout(debounceTimer);
    if (q.length < LOCATION_SEARCH_MIN_CHARS) {
      hideResults();
      return;
    }
    debounceTimer = setTimeout(() => runSearch(q), LOCATION_SEARCH_DEBOUNCE_MS);
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q.length < LOCATION_SEARCH_MIN_CHARS) return;
    clearTimeout(debounceTimer);
    runSearch(q);
  });

  document.addEventListener("click", (e) => {
    if (!container.contains(e.target)) hideResults();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && container.classList.contains("open")) closeSearch();
  });
}

// Etichetta breve per lo spazio ridotto di uno smartphone (mostrata via
// CSS sotto i 720px) ed etichetta estesa per il resto: niente più testo
// troncato con l'ellissi, che rendeva "Meteo attuale"/"Combinato"
// illeggibili sui telefoni più stretti.
const MODE_BUTTON_LABELS = {
  storico: { full: "Storico", short: "Storico" },
  meteo: { full: "Meteo attuale", short: "Meteo" },
  combinato: { full: "Combinato", short: "Misto" },
};

function buildModeSwitch() {
  const container = document.getElementById("modeSwitch");
  Object.keys(MODE_LABELS).forEach((key) => {
    const btn = document.createElement("button");
    const { full, short } = MODE_BUTTON_LABELS[key];
    btn.innerHTML = `<span class="ms-label-full">${full}</span><span class="ms-label-short">${short}</span>`;
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
   radiali che si fondono dove si sovrappongono (vedi drawHeatCanvas sopra),
   così la mappa si legge come una vera heatmap e non come dischi dal bordo
   netto.
*/

function haversineDeg(lat1, lon1, lat2, lon2) {
  const dLat = lat1 - lat2;
  const dLon = (lon1 - lon2) * Math.cos(((lat1 + lat2) / 2) * (Math.PI / 180));
  return Math.sqrt(dLat * dLat + dLon * dLon);
}

// Nucleo denso (centro storico/edificato) delle maggiori città italiane:
// [nome, lat, lon, raggio del nucleo in km]. Qui la presenza di funghi
// selvatici è irreale (asfalto/edifici) — un ritrovamento reale a pochi km
// di distanza, in un parco vero, non deve comunque "dipingere" di colore
// anche il centro pavimentato. Non è un filtro sui dati (che restano reali
// e posizionati esattamente dove sono stati raccolti): agisce solo sul
// colore renderizzato in quei punti precisi. Elenco non esaustivo (le
// città maggiori, per popolazione/estensione del nucleo edificato).
const URBAN_CORES = [
  ["Roma", 41.9028, 12.4964, 3.0], ["Milano", 45.4642, 9.19, 2.5], ["Napoli", 40.8518, 14.2681, 2.5],
  ["Torino", 45.0703, 7.6869, 2.0], ["Palermo", 38.1157, 13.3615, 2.0], ["Genova", 44.4056, 8.9463, 2.0],
  ["Bologna", 44.4949, 11.3426, 1.8], ["Firenze", 43.7696, 11.2558, 1.8], ["Bari", 41.1171, 16.8719, 1.5],
  ["Catania", 37.5079, 15.083, 1.5], ["Venezia", 45.4408, 12.3155, 1.5], ["Verona", 45.4384, 10.9916, 1.3],
  ["Messina", 38.1938, 15.554, 1.3], ["Padova", 45.4064, 11.8768, 1.3], ["Trieste", 45.6495, 13.7768, 1.3],
  ["Brescia", 45.5416, 10.2118, 1.2], ["Taranto", 40.4644, 17.247, 1.3], ["Prato", 43.8777, 11.1023, 1.2],
  ["Reggio Calabria", 38.1113, 15.6619, 1.2], ["Modena", 44.6471, 10.9252, 1.1], ["Reggio Emilia", 44.6989, 10.6297, 1.1],
  ["Perugia", 43.1122, 12.3888, 1.1], ["Livorno", 43.5485, 10.3106, 1.1], ["Ravenna", 44.4184, 12.2035, 1.1],
  ["Cagliari", 39.2238, 9.1217, 1.3], ["Foggia", 41.4621, 15.5444, 1.1], ["Rimini", 44.0678, 12.5695, 1.1],
  ["Salerno", 40.6824, 14.7681, 1.1], ["Ferrara", 44.8381, 11.6198, 1.1], ["Sassari", 40.7259, 8.559, 1.1],
  ["Latina", 41.4676, 12.9037, 1.0], ["Monza", 45.5845, 9.2744, 1.0], ["Siracusa", 37.0755, 15.2866, 1.1],
  ["Pescara", 42.4643, 14.2142, 1.1], ["Bergamo", 45.6983, 9.6773, 1.0], ["Forlì", 44.2226, 12.0407, 1.0],
  ["Trento", 46.0679, 11.1211, 1.0], ["Vicenza", 45.5455, 11.5354, 1.0], ["Terni", 42.5636, 12.6433, 1.0],
  ["Bolzano", 46.4983, 11.3548, 1.0], ["Novara", 45.4469, 8.6169, 1.0], ["Piacenza", 45.0526, 9.6929, 1.0],
  ["Ancona", 43.6158, 13.5189, 1.0], ["Udine", 46.0711, 13.2346, 1.0], ["Arezzo", 43.4633, 11.8797, 1.0],
  ["Lecce", 40.3519, 18.172, 1.0], ["Pesaro", 43.9102, 12.9133, 1.0],
];

// 0 dentro al nucleo urbano, torna a 1 (nessuna attenuazione) sfumando con
// dolcezza fino a ~2.2× il raggio del nucleo — mai un bordo netto, coerente
// con come si fonde il resto della heatmap.
function urbanSuppressionFactor(lat, lon) {
  let factor = 1;
  for (const [, clat, clon, coreKm] of URBAN_CORES) {
    const dKm = haversineDeg(lat, lon, clat, clon) * 111;
    const edgeKm = coreKm * 2.2;
    if (dKm >= edgeKm) continue;
    const f = dKm <= coreKm ? 0 : (() => {
      const t = (dKm - coreKm) / (edgeKm - coreKm);
      return t * t * (3 - 2 * t);
    })();
    factor = Math.min(factor, f);
  }
  return factor;
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
    else cells.set(key, { key, lat: cLat, lon: cLon, count: 1 });
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
  return cells.map((c) => ({
    lat: c.lat,
    lon: c.lon,
    value: (c.count / maxCount) * urbanSuppressionFactor(c.lat, c.lon),
    count: c.count,
  }));
}

function zonesForMeteo() {
  const raw = weatherCells.map((f) => {
    const lat = f.geometry.coordinates[1];
    const lon = f.geometry.coordinates[0];
    const conditions = conditionsQuality(f.properties.soil_moisture, f.properties.humidity_pct);
    const readiness = activeSpeciesReadinessAt(
      f.properties.daily_dates,
      f.properties.daily_precip_mm,
      f.properties.veg_class,
      f.properties.elevation_m
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
    // meteo/condizioni del suolo: variano con continuità su decine di km,
    // la cella meteo (0.5°) più vicina va benissimo per questi
    const nearest = nearestWeatherCell(c.lat, c.lon);
    const conditions = nearest ? conditionsQuality(nearest.properties.soil_moisture, nearest.properties.humidity_pct) : 0.4;
    // vegetazione e quota invece cambiano bruscamente nel giro di poche
    // centinaia di metri in montagna: qui serve il dato alla risoluzione
    // fine calcolato apposta su questa esatta cella con ritrovamenti,
    // non la cella meteo più vicina (poteva distare 30-40km)
    const fineVeg = vegetationFineByKey.get(c.key);
    const habitat = fineVeg ? fineVeg.habitat_score : nearest ? (nearest.properties.habitat_score ?? 0.5) : 0.5;
    const elevation = fineVeg ? fineVeg.elevation_m : nearest ? nearest.properties.elevation_m : null;
    const vegClass = fineVeg ? fineVeg.veg_class : nearest ? nearest.properties.veg_class : null;
    const readiness = nearest
      ? activeSpeciesReadinessAt(nearest.properties.daily_dates, nearest.properties.daily_precip_mm, vegClass, elevation)
      : 0.5;
    // storico locale × condizioni generali × idoneità del luogo (quota +
    // fascia boschiva) × finestra pioggia-fruttificazione delle specie
    // selezionate. Ogni fattore ha un pavimento: un ritrovamento storico
    // reale resta un'evidenza concreta anche quando il nostro modello
    // quota/pioggia per quel punto fosse impreciso
    const value =
      norm * (0.3 + 0.7 * conditions) * (0.3 + 0.7 * habitat) * (0.3 + 0.7 * readiness) * urbanSuppressionFactor(c.lat, c.lon);
    return { lat: c.lat, lon: c.lon, value, count: c.count };
  });

  const maxValue = Math.max(0.01, ...raw.map((z) => z.value));
  return raw.map((z) => ({ ...z, value: z.value / maxValue }));
}

// Raggio di sfumatura fisso, uguale per ogni zona (non più proporzionale
// al numero di segnalazioni: restringeva troppo anche le zone rurali ben
// documentate). Il problema delle "macchie" sopra i centri città si
// risolve invece sopprimendo il valore lì — vedi urbanSuppressionFactor
// più sotto — non riducendo il raggio ovunque.
function zoneRadiusMeters() {
  if (mode === "meteo") return weatherGridStepDeg * 111000 * 0.85;
  return FINE_GRID_STEP_DEG * 111000 * 0.75;
}

function drawZones(zones) {
  const r = zoneRadiusMeters();
  heatZones = zones.map((z) => ({ ...z, r }));
  scheduleHeatRedraw();
}

function render() {
  // la vista rilievo 3D non disegna l'heatmap: nessun dato da preparare
  if (mapType === "3d") return;

  let zones = [];

  if (mode === "storico") {
    zones = zonesForStorico();
  } else if (mode === "meteo") {
    zones = zonesForMeteo();
  } else {
    zones = zonesForCombinato();
  }

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
  // vegetazione reale (Corine Land Cover) interrogata dal vivo nel punto
  // esatto cliccato in onMapClick — non dalla cella meteo più vicina, che
  // può distare 30-40km e restituire un tipo di bosco sbagliato
  const vegClass = data.vegClass ?? null;
  const veg = vegetationInfo(vegClass, elevation);
  const species = speciesReadinessList(dates, precip, vegClass, elevation);

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
  setPopupHTML(activePopupInstance, popupContent(lat, lon, data, openSpeciesIdx));
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

// Header (in alto) e legenda (in basso, su mobile a tutta larghezza) sono
// overlay fissi sopra la mappa: il motore non sa che coprono parte del suo
// container, e i popup di MapLibre non hanno un auto-pan proprio. Dopo
// l'apertura misuriamo dove è finito davvero il popup e spostiamo la mappa
// quel tanto che basta perché non resti dietro a quegli elementi.
function nudgePopupIntoView(popup) {
  // su smartphone il popup è fisso sullo schermo (vedi .wx-map-popup in
  // CSS): spostare la mappa qui non serve più e la farebbe solo scorrere
  // a vuoto sotto un popup che non si muove
  if (window.matchMedia("(max-width: 720px), (max-height: 480px)").matches) return;
  requestAnimationFrame(() => {
    const el = popup.getElement();
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (!rect.height) return;

    const header = document.querySelector(".topbar");
    const legend = document.getElementById("legend");
    const margin = 12;
    const topLimit = (header ? header.getBoundingClientRect().bottom : 0) + margin;
    const legendVisible = legend && getComputedStyle(legend).display !== "none";
    const bottomLimit = legendVisible
      ? legend.getBoundingClientRect().top - margin
      : window.innerHeight - margin;

    // panBy sposta la mappa: il contenuto (popup compreso) si muove del
    // valore opposto, quindi lo scarto va passato con questo segno
    let dy = 0;
    if (rect.top < topLimit) dy = rect.top - topLimit;
    else if (rect.bottom > bottomLimit) dy = Math.min(rect.bottom - bottomLimit, rect.top - topLimit);

    let dx = 0;
    if (rect.left < margin) dx = rect.left - margin;
    else if (rect.right > window.innerWidth - margin) {
      dx = Math.min(rect.right - (window.innerWidth - margin), rect.left - margin);
    }

    if (dx || dy) map.panBy([dx, dy], { duration: 300 });
  });
}

/* ---------------- Notifiche pioggia (zone disegnate + OneSignal) ---- */

// App ID dell'app OneSignal collegata al sito (onesignal.com > Settings >
// Keys & IDs)
const ONESIGNAL_APP_ID = "a2dbdf99-3704-4e41-a791-e2add385f9dd";

const NOTIFY_ZONES_KEY = "mappaFunghi.notifyZones";
const PUSH_ENABLED_KEY = "mappaFunghi.pushEnabled";
const IOS_HINT_SHOWN_KEY = "mappaFunghi.iosHintShown";

// ogni zona è {id, points:[[lng,lat], ...]} — un poligono disegnato a
// mano libera, non più un cerchio: la forma reale che l'utente traccia
// può seguire un crinale, una vallata, i confini di un bosco
let notifyZones = [];
try {
  notifyZones = JSON.parse(localStorage.getItem(NOTIFY_ZONES_KEY) || "[]");
} catch {
  notifyZones = [];
}

let drawingZone = false;
let drawPoints = [];
let drawPointerId = null;
let drawLastClientPoint = null;
const MIN_DRAW_PX = 5; // distanza minima tra due punti campionati, per non accumulare migliaia di vertici
const MIN_DRAW_POINTS = 3;

window.OneSignalDeferred = window.OneSignalDeferred || [];
if (!ONESIGNAL_APP_ID.startsWith("INSERISCI")) {
  window.OneSignalDeferred.push(async (OneSignal) => {
    await OneSignal.init({
      appId: ONESIGNAL_APP_ID,
      // il sito vive in un sottopercorso di github.io (/mappa-funghi/),
      // non alla radice del dominio: senza queste due righe l'SDK cerca
      // OneSignalSDKWorker.js alla radice (404), il service worker non si
      // registra mai e nessuna vera sottoscrizione push si crea — anche
      // se il permesso del browser risulta concesso
      serviceWorkerPath: "mappa-funghi/OneSignalSDKWorker.js",
      serviceWorkerParam: { scope: "/mappa-funghi/" },
    });
  });
}

function isIOS() {
  // l'iPad in modalità "richiedi sito desktop" si presenta come
  // navigator.platform "MacIntel" con touch: lo distinguiamo da un vero
  // Mac (che non ha schermo touch) richiedendo anche "Macintosh" nella UA,
  // così un Android/Chrome che lasci platform a "MacIntel" (capita in
  // alcuni emulatori) non scatta come falso positivo
  return (
    /iP(hone|od|ad)/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1 && /Macintosh/.test(navigator.userAgent))
  );
}
function isStandalonePwa() {
  return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}
function showIosPushHint() {
  document.getElementById("iosPushHintScrim").hidden = false;
  document.getElementById("iosPushHint").hidden = false;
}
function hideIosPushHint() {
  document.getElementById("iosPushHintScrim").hidden = true;
  document.getElementById("iosPushHint").hidden = true;
}

// "granted" solo se il browser ha davvero concesso il permesso: il flag in
// localStorage da solo non basta, l'utente può revocarlo dalle impostazioni
// del sito in qualsiasi momento, anche dopo averlo concesso
function pushPermissionState() {
  if (!("Notification" in window)) return "unsupported";
  return Notification.permission; // "granted" | "denied" | "default"
}

function pushIsActive() {
  return pushPermissionState() === "granted" && localStorage.getItem(PUSH_ENABLED_KEY) === "1";
}

function showPushPrompt(text) {
  const prompt = document.getElementById("pushPrompt");
  const allowBtn = document.getElementById("pushPromptAllow");
  if (text) document.getElementById("pushPromptText").textContent = text;
  // se il browser ha già bloccato le notifiche non serve richiederle: un
  // nuovo requestPermission non mostrerebbe alcun dialogo, va sbloccato a
  // mano dalle impostazioni del sito
  const blocked = pushPermissionState() === "denied";
  if (blocked) {
    document.getElementById("pushPromptText").textContent =
      "Le notifiche sono bloccate per questo sito. Sbloccale dalle impostazioni del sito (icona vicino all'indirizzo), poi riprova.";
  }
  allowBtn.hidden = blocked;
  document.getElementById("pushPromptScrim").hidden = false;
  prompt.hidden = false;
}

function hidePushPrompt() {
  document.getElementById("pushPromptScrim").hidden = true;
  document.getElementById("pushPrompt").hidden = true;
}

// unico punto in cui si chiede il permesso, condiviso dal pulsante del
// pannello e dal popup che ricompare a zona disegnata
function requestPushPermission(onResult) {
  window.OneSignalDeferred.push(async (OneSignal) => {
    try {
      await OneSignal.Notifications.requestPermission();
    } catch (err) {
      console.error(err);
    }
    const granted = OneSignal.Notifications.permission === true;
    if (granted) {
      localStorage.setItem(PUSH_ENABLED_KEY, "1");
      syncZoneTags();
    }
    onResult(granted);
  });
}

function pointsToPolygonFeature(points, id) {
  const ring = points.slice();
  const [flng, flat] = ring[0];
  const [llng, llat] = ring[ring.length - 1];
  if (flng !== llng || flat !== llat) ring.push(ring[0]);
  return { type: "Feature", properties: { id }, geometry: { type: "Polygon", coordinates: [ring] } };
}

function renderZonesSource() {
  const source = map.getSource("notifyZones");
  if (!source) return;
  source.setData({
    type: "FeatureCollection",
    features: notifyZones.map((z) => pointsToPolygonFeature(z.points, z.id)),
  });
}

function renderDrawPreview() {
  const source = map.getSource("zoneDrawPreview");
  if (!source) return;
  source.setData({
    type: "FeatureCollection",
    features: drawPoints.length >= 2 ? [pointsToPolygonFeature(drawPoints, "preview")] : [],
  });
}

function zoneCentroid(points) {
  let lat = 0;
  let lon = 0;
  points.forEach(([lng, plat]) => {
    lon += lng;
    lat += plat;
  });
  return { lat: lat / points.length, lon: lon / points.length };
}

// una sola voce OneSignal (tag notify_zones, JSON con id+centroide di ogni
// zona) invece di un tag per zona: il numero di zone è variabile, i tag
// OneSignal sono invece una mappa piatta di chiavi fisse
function syncZoneTags() {
  if (localStorage.getItem(PUSH_ENABLED_KEY) !== "1") return;
  const summary = notifyZones.map((z) => {
    const c = zoneCentroid(z.points);
    return { id: z.id, lat: Number(c.lat.toFixed(4)), lon: Number(c.lon.toFixed(4)) };
  });
  window.OneSignalDeferred.push(async (OneSignal) => {
    try {
      if (summary.length) await OneSignal.User.addTags({ notify_zones: JSON.stringify(summary) });
      else await OneSignal.User.removeTags(["notify_zones"]);
    } catch (err) {
      console.error(err);
    }
  });
}

function persistZones() {
  localStorage.setItem(NOTIFY_ZONES_KEY, JSON.stringify(notifyZones));
}

function removeZone(id) {
  notifyZones = notifyZones.filter((z) => z.id !== id);
  persistZones();
  renderZonesSource();
  renderZoneList();
  syncZoneTags();
}

const ZONE_TRASH_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M5 7h14M10 7V5a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v2M7 7l1 12a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2l1-12"/></svg>';

function renderZoneList() {
  const list = document.getElementById("zoneList");
  const empty = document.getElementById("zoneEmpty");
  list.innerHTML = "";
  notifyZones.forEach((zone, i) => {
    const li = document.createElement("li");
    li.className = "zone-list-item";
    const name = document.createElement("span");
    name.className = "zone-list-name";
    name.textContent = `Zona ${i + 1}`;
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "zone-list-remove";
    removeBtn.setAttribute("aria-label", `Rimuovi Zona ${i + 1}`);
    removeBtn.innerHTML = ZONE_TRASH_ICON;
    removeBtn.addEventListener("click", () => removeZone(zone.id));
    li.append(name, removeBtn);
    list.appendChild(li);
  });
  empty.hidden = notifyZones.length > 0;
  document.getElementById("zoneEnablePush").hidden =
    notifyZones.length === 0 || localStorage.getItem(PUSH_ENABLED_KEY) === "1";
}

function lngLatFromPointerEvent(e) {
  const rect = map.getContainer().getBoundingClientRect();
  return map.unproject([e.clientX - rect.left, e.clientY - rect.top]);
}

function setupRainZone() {
  const btn = document.getElementById("notifyZoneBtn");
  const panel = document.getElementById("zonePanel");
  const drawHint = document.getElementById("zoneDrawHint");
  const drawBtn = document.getElementById("zoneDrawBtn");
  const drawCancelBtn = document.getElementById("zoneDrawCancel");
  const enablePushBtn = document.getElementById("zoneEnablePush");
  const closeBtn = document.getElementById("zonePanelClose");
  const canvasContainer = map.getCanvasContainer();

  const startDrawing = () => {
    drawingZone = true;
    drawPoints = [];
    drawPointerId = null;
    drawLastClientPoint = null;
    map.dragPan.disable();
    map.dragRotate.disable();
    map.touchZoomRotate.disable();
    map.doubleClickZoom.disable();
    map.scrollZoom.disable();
    map.getCanvas().style.cursor = "crosshair";
    drawHint.hidden = false;
    drawBtn.hidden = true;
    drawCancelBtn.hidden = false;
  };

  const stopDrawing = () => {
    drawingZone = false;
    map.dragPan.enable();
    map.dragRotate.enable();
    map.touchZoomRotate.enable();
    map.doubleClickZoom.enable();
    map.scrollZoom.enable();
    map.getCanvas().style.cursor = "";
    drawHint.hidden = true;
    drawBtn.hidden = false;
    drawCancelBtn.hidden = true;
    drawPoints = [];
    renderDrawPreview();
  };

  const onPointerDown = (e) => {
    if (!drawingZone || drawPointerId != null) return;
    drawPointerId = e.pointerId;
    const ll = lngLatFromPointerEvent(e);
    drawPoints = [[ll.lng, ll.lat]];
    drawLastClientPoint = [e.clientX, e.clientY];
    canvasContainer.setPointerCapture(e.pointerId);
    e.preventDefault();
  };

  const onPointerMove = (e) => {
    if (!drawingZone || e.pointerId !== drawPointerId) return;
    const dx = e.clientX - drawLastClientPoint[0];
    const dy = e.clientY - drawLastClientPoint[1];
    if (dx * dx + dy * dy < MIN_DRAW_PX * MIN_DRAW_PX) return;
    drawLastClientPoint = [e.clientX, e.clientY];
    const ll = lngLatFromPointerEvent(e);
    drawPoints.push([ll.lng, ll.lat]);
    renderDrawPreview();
  };

  const finishDrawing = (e) => {
    if (!drawingZone || e.pointerId !== drawPointerId) return;
    try {
      canvasContainer.releasePointerCapture(e.pointerId);
    } catch {
      /* capture già rilasciata dal browser, ignorato di proposito */
    }
    drawPointerId = null;
    const saved = drawPoints.length >= MIN_DRAW_POINTS;
    if (saved) {
      notifyZones.push({ id: `zone-${Date.now()}`, points: drawPoints });
      persistZones();
      renderZonesSource();
      renderZoneList();
      syncZoneTags();
    }
    stopDrawing();
    // una zona senza permesso di notifica non avviserebbe mai: meglio
    // chiederlo subito qui, invece di lasciarla salvata e muta
    if (saved && !pushIsActive()) {
      if (isIOS() && !isStandalonePwa()) showIosPushHint();
      else showPushPrompt("La zona è salvata, ma senza il permesso di notifica non riceverai l'avviso quando ci piove.");
    }
  };

  canvasContainer.addEventListener("pointerdown", onPointerDown);
  canvasContainer.addEventListener("pointermove", onPointerMove);
  canvasContainer.addEventListener("pointerup", finishDrawing);
  canvasContainer.addEventListener("pointercancel", finishDrawing);

  // un tocco interrotto a metà gesto (cambio app, notifica, tab in
  // background) potrebbe non generare pointerup/pointercancel: senza
  // questo la mappa resterebbe bloccata con pan/zoom disattivati
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && drawingZone) stopDrawing();
  });

  btn.addEventListener("click", () => {
    panel.hidden = false;
    renderZoneList();
  });

  closeBtn.addEventListener("click", () => {
    panel.hidden = true;
    if (drawingZone) stopDrawing();
  });

  drawBtn.addEventListener("click", startDrawing);
  drawCancelBtn.addEventListener("click", stopDrawing);

  const pushStatus = document.getElementById("zonePushStatus");
  const showPushStatus = (text) => {
    pushStatus.textContent = text;
    pushStatus.hidden = false;
  };

  enablePushBtn.addEventListener("click", () => {
    if (isIOS() && !isStandalonePwa()) {
      showIosPushHint();
      return;
    }
    pushStatus.hidden = true;
    enablePushBtn.disabled = true;
    enablePushBtn.textContent = "Attivazione…";
    // requestPermission si risolve anche se l'utente nega o ignora il popup
    // del browser: il vero esito arriva qui, non va assunto — altrimenti un
    // permesso negato verrebbe comunque segnato come "attivo" e il pulsante
    // sparirebbe senza che nulla funzioni davvero
    requestPushPermission((granted) => {
      enablePushBtn.disabled = false;
      enablePushBtn.textContent = "Attiva notifiche push";
      renderZoneList();
      if (!granted) {
        showPushStatus(
          "Permesso non concesso. Controlla le impostazioni del sito (icona vicino all'indirizzo) e imposta le notifiche su \"Consenti\", poi riprova."
        );
      }
    });
  });

  document.getElementById("pushPromptLater").addEventListener("click", hidePushPrompt);
  document.getElementById("pushPromptScrim").addEventListener("click", hidePushPrompt);
  document.getElementById("pushPromptAllow").addEventListener("click", () => {
    const allowBtn = document.getElementById("pushPromptAllow");
    allowBtn.disabled = true;
    allowBtn.textContent = "Attivazione…";
    requestPushPermission((granted) => {
      allowBtn.disabled = false;
      allowBtn.textContent = "Attiva notifiche";
      renderZoneList();
      if (granted) hidePushPrompt();
      else showPushPrompt();
    });
  });

  document.getElementById("iosPushHintClose").addEventListener("click", hideIosPushHint);
  document.getElementById("iosPushHintScrim").addEventListener("click", hideIosPushHint);

  if (isIOS() && !isStandalonePwa() && !localStorage.getItem(IOS_HINT_SHOWN_KEY)) {
    showIosPushHint();
    localStorage.setItem(IOS_HINT_SHOWN_KEY, "1");
  }
}

function onMapClick(e) {
  // mentre si disegna una zona il tap fa parte del gesto di disegno
  // (vedi setupRainZone), non deve aprire il popup meteo
  if (drawingZone) return;
  const { lat, lng } = e.lngLat;
  openSpeciesIdx = null;
  const popup = new maplibregl.Popup({
    className: "wx-map-popup",
    maxWidth: "320px",
    focusAfterOpen: false,
  })
    .setLngLat([lng, lat])
    .setHTML(popupSkeleton(lat, lng))
    .addTo(map);
  activePopupInstance = popup;
  lastPopupParams = null;
  nudgePopupIntoView(popup);

  // un tocco sulle righe specie non deve arrivare alla mappa e aprire un
  // secondo popup sopra quello attuale
  const popupEl = popup.getElement();
  if (popupEl) popupEl.addEventListener("click", (ev) => ev.stopPropagation());

  const url =
    `https://api.open-meteo.com/v1/forecast?latitude=${lat.toFixed(4)}&longitude=${lng.toFixed(4)}` +
    `&daily=precipitation_sum&hourly=relative_humidity_2m,soil_moisture_0_to_1cm` +
    `&past_days=16&forecast_days=1&timezone=auto`;

  Promise.all([
    fetch(url).then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }),
    // se Corine non risponde, il popup resta comunque utile con meteo/pioggia:
    // la vegetazione ripiega su "non determinato" invece di far fallire tutto
    fetchClcVegClass(lat, lng).catch(() => null),
  ])
    .then(([data, vegClass]) => {
      if (popup.isOpen()) {
        data.vegClass = vegClass;
        lastPopupParams = { lat, lon: lng, data };
        setPopupHTML(popup, popupContent(lat, lng, data, openSpeciesIdx));
        nudgePopupIntoView(popup);
      }
    })
    .catch((err) => {
      console.error(err);
      if (popup.isOpen()) setPopupHTML(popup, popupError(lat, lng));
    });
}

/* ---------------- Init ---------------- */

buildFilters();
buildModeSwitch();
updateLegend();
setupMobileMenus();
setupLocationSearch();
setupRainZone();
map.on("click", onMapClick);

// visibilità dei layer e terreno si possono impostare solo a stile
// caricato: qui applichiamo il tipo di mappa scelto (anche quello
// ripescato dalla preferenza salvata) e disegniamo la heatmap
map.on("load", () => {
  mapStyleReady = true;
  resizeHeatCanvas();
  setMapType(mapType);
  map.addSource("notifyZones", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({ id: "notifyZonesFill", type: "fill", source: "notifyZones", paint: { "fill-color": "#6fa8dc", "fill-opacity": 0.15 } });
  map.addLayer({ id: "notifyZonesLine", type: "line", source: "notifyZones", paint: { "line-color": "#6fa8dc", "line-width": 2 } });
  map.addSource("zoneDrawPreview", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({ id: "zoneDrawPreviewFill", type: "fill", source: "zoneDrawPreview", paint: { "fill-color": "#6fa8dc", "fill-opacity": 0.12 } });
  map.addLayer({
    id: "zoneDrawPreviewLine",
    type: "line",
    source: "zoneDrawPreview",
    paint: { "line-color": "#6fa8dc", "line-width": 2, "line-dasharray": [2, 2] },
  });
  renderZonesSource();
});

Promise.all([
  fetch("data/occurrences.geojson").then((r) => r.json()),
  fetch("data/weather_grid.geojson").then((r) => r.json()),
  fetch("data/vegetation_fine.geojson").then((r) => r.json()),
])
  .then(([occGeojson, weatherGeojson, vegFineGeojson]) => {
    occurrences = occGeojson.features;
    weatherCells = weatherGeojson.features;
    weatherGridStepDeg = weatherGeojson.grid_step_deg || 0.5;
    vegetationFineByKey = new Map(vegFineGeojson.features.map((f) => [f.properties.cell_key, f.properties]));
    render();
  })
  .catch((err) => {
    console.error(err);
    document.getElementById("countBadge").textContent = "Errore nel caricamento dati";
  });
