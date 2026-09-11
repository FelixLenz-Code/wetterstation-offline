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
