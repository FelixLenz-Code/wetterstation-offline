// Einheitliche Formatierung für die ganze Oberfläche.
//
// Alles auf Deutsch und metrisch. Die Zeitzone kommt aus der Umgebung (TZ), damit
// Server und Anzeige dieselbe verwenden -- sonst springt die Uhrzeit beim Übergang
// vom serverseitig erzeugten HTML zur Hydrierung im Browser.

const ZEITZONE = process.env.TZ ?? "Europe/Berlin";

export function zahl(
  wert: number | null | undefined,
  stellen = 1,
  gruppierung = true,
): string {
  if (wert === null || wert === undefined || !Number.isFinite(wert)) return "–";
  return wert.toLocaleString("de-DE", {
    minimumFractionDigits: stellen,
    maximumFractionDigits: stellen,
    useGrouping: gruppierung,
  });
}

export function temperatur(wert: number | null | undefined): string {
  return wert === null || wert === undefined ? "–" : `${zahl(wert, 1)} °C`;
}

export function druck(wert: number | null | undefined): string {
  // Ohne Tausendertrennzeichen: Luftdruck schreibt man 1019,6 hPa, nicht
  // 1.019,6 hPa -- die Gruppierung liest sich hier wie ein Tippfehler.
  return wert === null || wert === undefined ? "–" : `${zahl(wert, 1, false)} hPa`;
}

export function prozent(anteil: number | null | undefined, stellen = 0): string {
  if (anteil === null || anteil === undefined || !Number.isFinite(anteil)) return "–";
  return `${zahl(anteil * 100, stellen)} %`;
}

export function millimeter(wert: number | null | undefined): string {
  return wert === null || wert === undefined ? "–" : `${zahl(wert, 1)} mm`;
}

export function windgeschwindigkeit(wert: number | null | undefined): string {
  return wert === null || wert === undefined ? "–" : `${zahl(wert, 1)} m/s`;
}

/** Windrichtung als Himmelsrichtung, wie man sie tatsächlich vorliest. */
export function windrichtung(grad: number | null | undefined): string {
  if (grad === null || grad === undefined || !Number.isFinite(grad)) return "–";
  const namen = [
    "N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
  ];
  const sektor = Math.round(((grad % 360) + 360) % 360 / 22.5) % 16;
  return `${namen[sektor]} (${Math.round(grad)}°)`;
}

export function uhrzeit(zeit: Date | null | undefined): string {
  if (!zeit) return "–";
  return zeit.toLocaleTimeString("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: ZEITZONE,
  });
}

export function datumZeit(zeit: Date | null | undefined): string {
  if (!zeit) return "–";
  return zeit.toLocaleString("de-DE", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: ZEITZONE,
  });
}

/** "vor 3 Minuten" -- sagt mehr über die Aktualität als ein Zeitstempel. */
export function seit(zeit: Date | null | undefined, jetzt = new Date()): string {
  if (!zeit) return "–";
  const sekunden = Math.round((jetzt.getTime() - zeit.getTime()) / 1000);
  if (sekunden < 0) return "gleich";
  if (sekunden < 60) return "gerade eben";
  const minuten = Math.round(sekunden / 60);
  if (minuten < 60) return `vor ${minuten} min`;
  const stunden = Math.round(minuten / 60);
  if (stunden < 24) return `vor ${stunden} h`;
  const tage = Math.round(stunden / 24);
  return `vor ${tage} ${tage === 1 ? "Tag" : "Tagen"}`;
}

/** Drucktendenz als Wort -- die Zahl allein sagt den meisten nichts. */
export function tendenz(deltaHpa: number | null | undefined): string {
  if (deltaHpa === null || deltaHpa === undefined || !Number.isFinite(deltaHpa)) {
    return "–";
  }
  const betrag = Math.abs(deltaHpa);
  // Schwellen nach der WMO-Kennzeichnung für die Drucktendenz über drei Stunden.
  if (betrag < 0.5) return "gleichbleibend";
  const richtung = deltaHpa > 0 ? "steigend" : "fallend";
  if (betrag >= 3.0) return `stark ${richtung}`;
  if (betrag >= 1.6) return richtung;
  return `leicht ${richtung}`;
}
