// Datenabfragen der Weboberfläche.
//
// Alle Abfragen liegen hier statt in den Seiten: sie sind der Teil, der mit dem
// Datenbankschema verwoben ist, und sollen an einer Stelle stehen, wenn sich das
// Schema ändert.
//
// Zur Zwischenspeicherung: Next.js 16 speichert `fetch` nicht mehr von sich aus,
// und Datenbankabfragen ohnehin nie. Das ist hier richtig so -- eine Wetteranzeige,
// die eine zwischengespeicherte Messung von vor zehn Minuten zeigt, ist kaputt.
// Zwischengespeichert wird nur, was sich wirklich selten ändert.

import { prisma } from "@/lib/db";

export type Station = {
  id: number;
  key: string;
  name: string;
  latitude: number;
  longitude: number;
  altitudeM: number;
};

export async function ersteStation(): Promise<Station | null> {
  const zeile = await prisma.station.findFirst({ orderBy: { id: "asc" } });
  if (!zeile) return null;
  return {
    id: zeile.id,
    key: zeile.key,
    name: zeile.name,
    latitude: zeile.latitude,
    longitude: zeile.longitude,
    altitudeM: zeile.altitude_m,
  };
}

export type Messwerte = {
  zeit: Date;
  temperatur: number | null;
  feuchte: number | null;
  taupunkt: number | null;
  druck: number | null;
  windGeschwindigkeit: number | null;
  windBoe: number | null;
  windRichtung: number | null;
  niederschlag: number | null;
  bewoelkung: number | null;
  himmelstemperatur: number | null;
  einstrahlung: number | null;
  stichproben: number;
};

function zuMesswerten(z: {
  time: Date;
  temperature_c: number | null;
  humidity_pct: number | null;
  dewpoint_c: number | null;
  pressure_sea_hpa: number | null;
  wind_speed_ms: number | null;
  wind_gust_ms: number | null;
  wind_dir_deg: number | null;
  precip_mm: number | null;
  cloud_cover_okta: number | null;
  sky_temp_c: number | null;
  global_radiation_wm2: number | null;
  sample_count: number;
}): Messwerte {
  return {
    zeit: z.time,
    temperatur: z.temperature_c,
    feuchte: z.humidity_pct,
    taupunkt: z.dewpoint_c,
    druck: z.pressure_sea_hpa,
    windGeschwindigkeit: z.wind_speed_ms,
    windBoe: z.wind_gust_ms,
    windRichtung: z.wind_dir_deg,
    niederschlag: z.precip_mm,
    // Der DWD führt Bewölkung in Achteln; für die Anzeige ist ein Anteil handlicher.
    bewoelkung: z.cloud_cover_okta === null ? null : z.cloud_cover_okta / 8,
    himmelstemperatur: z.sky_temp_c,
    einstrahlung: z.global_radiation_wm2,
    stichproben: z.sample_count,
  };
}

/** Die jüngste vollständige Stunde. */
export async function aktuelleWerte(stationId: number): Promise<Messwerte | null> {
  const zeile = await prisma.hourly.findFirst({
    where: { station_id: stationId },
    orderBy: { time: "desc" },
  });
  return zeile ? zuMesswerten(zeile) : null;
}

/** Verlauf der letzten Stunden, älteste zuerst. */
export async function verlauf(
  stationId: number,
  stunden: number,
): Promise<Messwerte[]> {
  const seit = new Date(Date.now() - stunden * 3600_000);
  const zeilen = await prisma.hourly.findMany({
    where: { station_id: stationId, time: { gte: seit } },
    orderBy: { time: "asc" },
  });
  return zeilen.map(zuMesswerten);
}

/**
 * Drucktendenz über drei Stunden -- das wichtigste Einzelmerkmal der Vorhersage
 * und der Wert, den man als Erstes sehen will.
 */
export async function drucktendenz(stationId: number): Promise<number | null> {
  const zeilen = await prisma.hourly.findMany({
    where: { station_id: stationId, pressure_sea_hpa: { not: null } },
    orderBy: { time: "desc" },
    take: 4,
    select: { time: true, pressure_sea_hpa: true },
  });
  if (zeilen.length < 4) return null;

  const jetzt = zeilen[0];
  const vorher = zeilen[3];
  // Nur vergleichen, wenn wirklich drei Stunden dazwischenliegen -- bei einer
  // Messlücke wäre die Tendenz sonst über einen längeren Zeitraum gerechnet und
  // damit zu klein.
  const abstandStunden =
    (jetzt.time.getTime() - vorher.time.getTime()) / 3600_000;
  if (Math.abs(abstandStunden - 3) > 0.01) return null;

  return (jetzt.pressure_sea_hpa ?? 0) - (vorher.pressure_sea_hpa ?? 0);
}

export type Sensorzustand = {
  sensor: string;
  zustand: "productive" | "test" | "inactive";
  seit: Date;
};

/** Der aktuelle Zustand je Sensor -- also die offenen Zeiträume. */
export async function sensorzustaende(
  stationId: number,
): Promise<Sensorzustand[]> {
  const zeilen = await prisma.sensor_state.findMany({
    where: { station_id: stationId, valid_to: null },
    orderBy: { sensor_key: "asc" },
  });
  return zeilen.map((z) => ({
    sensor: z.sensor_key,
    zustand: z.state as Sensorzustand["zustand"],
    seit: z.valid_from,
  }));
}

export type Vorhersage = {
  ziel: string;
  vorlaufStunden: number;
  gueltigAb: Date;
  wert: number | null;
  quantile: Record<string, number> | null;
};

/** Die neuesten Vorhersagen der aktiven Modelle. */
export async function aktuelleVorhersagen(
  stationId: number,
): Promise<Vorhersage[]> {
  const aktive = await prisma.model.findMany({
    where: { status: "active" },
    select: { id: true, target: true, lead_hours: true },
  });
  if (aktive.length === 0) return [];

  const nachModell = new Map(aktive.map((m) => [m.id, m]));
  const zeilen = await prisma.forecast.findMany({
    where: { station_id: stationId, model_id: { in: aktive.map((m) => m.id) } },
    orderBy: { issued_at: "desc" },
    take: aktive.length * 4,
  });

  // Je Modell nur die jüngste Ausstellung behalten.
  // Die Kennungen sind in der Datenbank BIGINT und kommen als bigint an -- ein
  // Set<number> würde hier stillschweigend nie treffen.
  const gesehen = new Set<bigint>();
  const out: Vorhersage[] = [];
  for (const z of zeilen) {
    if (gesehen.has(z.model_id)) continue;
    gesehen.add(z.model_id);
    const m = nachModell.get(z.model_id);
    if (!m) continue;
    out.push({
      ziel: m.target,
      vorlaufStunden: m.lead_hours,
      gueltigAb: z.valid_at,
      wert: z.value,
      quantile: z.quantiles as Record<string, number> | null,
    });
  }
  return out.sort((a, b) => a.vorlaufStunden - b.vorlaufStunden);
}

export type Guete = {
  ziel: string;
  vorlaufStunden: number;
  anzahl: number;
  /** Brier Score beim Regen, mittlerer absoluter Fehler bei der Temperatur. */
  fehler: number;
  /** Anteil der Fälle, in denen es tatsächlich geregnet hat. */
  basisrate: number | null;
  /** Brier Score der Klimatologie -- also der Vergleichsmassstab. */
  klimatologie: number | null;
  /** Anteil der Beobachtungen im 10-bis-90-Prozent-Band. */
  bandTreffer: number | null;
};

/**
 * Güte der aktiven Modelle über einen Zeitraum.
 *
 * Bewusst als rohes SQL: die Bewertungen liegen als JSONB, und die Aggregation
 * über einen JSONB-Schlüssel ist in SQL eine Zeile und über den ORM eine
 * Schleife über Zehntausende Zeilen.
 */
export async function guete(stationId: number, tage = 30): Promise<Guete[]> {
  const zeilen = await prisma.$queryRaw<
    {
      target: string;
      lead_hours: number;
      anzahl: bigint;
      fehler: number | null;
      basisrate: number | null;
      band: number | null;
    }[]
  >`
    SELECT m.target,
           m.lead_hours,
           count(*)                                            AS anzahl,
           avg(COALESCE((v.scores->>'brier')::float,
                        (v.scores->>'absolute_error')::float))  AS fehler,
           avg(v.observed) FILTER (WHERE m.target = 'rain')     AS basisrate,
           avg((v.scores->>'in_band')::float)                   AS band
      FROM wetter.verification v
      JOIN wetter.forecast f ON f.id = v.forecast_id
      JOIN wetter.model m    ON m.id = f.model_id
     WHERE f.station_id = ${stationId}
       AND m.status = 'active'
       AND f.issued_at >= now() - (${tage} || ' days')::interval
     GROUP BY m.target, m.lead_hours
     ORDER BY m.target, m.lead_hours
  `;

  return zeilen.map((z) => {
    const basisrate = z.basisrate;
    return {
      ziel: z.target,
      vorlaufStunden: z.lead_hours,
      anzahl: Number(z.anzahl),
      fehler: z.fehler ?? 0,
      basisrate,
      // Der Brier Score der Klimatologie ist bei einer konstanten Vorhersage der
      // Basisrate genau p*(1-p) -- der Massstab, den ein Modell schlagen muss.
      klimatologie: basisrate === null ? null : basisrate * (1 - basisrate),
      bandTreffer: z.band,
    };
  });
}

export type Zuverlaessigkeit = {
  vorlaufStunden: number;
  balken: { mitte: number; vorhergesagt: number; beobachtet: number; anzahl: number }[];
};

/**
 * Zuverlässigkeitsdiagramm: sagt „70 %" auch in 70 % der Fälle Regen?
 *
 * Das ist die Zahl, die zählt. Eine Trefferquote sagt wenig; erst hier sieht man,
 * *wo* ein Modell danebenliegt -- ob es durchweg zu selbstbewusst ist oder nur bei
 * hohen Wahrscheinlichkeiten.
 */
export async function zuverlaessigkeit(
  stationId: number,
  tage = 90,
): Promise<Zuverlaessigkeit[]> {
  const zeilen = await prisma.$queryRaw<
    {
      lead_hours: number;
      eimer: number;
      vorhergesagt: number;
      beobachtet: number;
      anzahl: bigint;
    }[]
  >`
    SELECT m.lead_hours,
           width_bucket(f.value, 0, 1, 10) AS eimer,
           avg(f.value)                    AS vorhergesagt,
           avg(v.observed)                 AS beobachtet,
           count(*)                        AS anzahl
      FROM wetter.verification v
      JOIN wetter.forecast f ON f.id = v.forecast_id
      JOIN wetter.model m    ON m.id = f.model_id
     WHERE f.station_id = ${stationId}
       AND m.target = 'rain'
       AND m.status = 'active'
       AND f.value IS NOT NULL
       AND f.issued_at >= now() - (${tage} || ' days')::interval
     GROUP BY m.lead_hours, eimer
     ORDER BY m.lead_hours, eimer
  `;

  const nachVorlauf = new Map<number, Zuverlaessigkeit>();
  for (const z of zeilen) {
    if (!nachVorlauf.has(z.lead_hours)) {
      nachVorlauf.set(z.lead_hours, { vorlaufStunden: z.lead_hours, balken: [] });
    }
    nachVorlauf.get(z.lead_hours)!.balken.push({
      // width_bucket zählt ab 1; Eimer 11 fängt den Wert exakt 1,0 ab.
      mitte: (Math.min(z.eimer, 10) - 0.5) / 10,
      vorhergesagt: z.vorhergesagt,
      beobachtet: z.beobachtet,
      anzahl: Number(z.anzahl),
    });
  }
  return [...nachVorlauf.values()].sort(
    (a, b) => a.vorlaufStunden - b.vorlaufStunden,
  );
}
