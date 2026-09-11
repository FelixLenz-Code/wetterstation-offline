"use client";

// Diagramme als eigenes SVG, ohne Bibliothek.
//
// Die Charts hier sind einfach -- eine Reihe über die Zeit, ein paar Säulen, ein
// Unsicherheitsband. Dafür eine Diagrammbibliothek zu laden hiesse, mehrere hundert
// Kilobyte JavaScript an ein Gerät zu schicken, das die Seite womöglich über ein
// müdes WLAN im Garten lädt -- und die Vorgaben (2px-Linien, 4px gerundete
// Balkenenden, 2px Lücken, dezentes Raster) müsste man der Bibliothek ohnehin
// einzeln abringen.
//
// Farben kommen aus der validierten Palette und stehen als CSS-Variablen in
// globals.css, damit hell und dunkel an einer Stelle umschalten.

import { useId, useMemo, useState } from "react";

export type Punkt = { zeit: Date; wert: number | null };

export type BandPunkt = Punkt & { unten: number | null; oben: number | null };

const RAND = { oben: 16, rechts: 52, unten: 28, links: 44 };
const BREITE = 720;
const HOEHE = 220;

function skala(werte: number[]): [number, number] {
  const gueltig = werte.filter((w) => Number.isFinite(w));
  if (gueltig.length === 0) return [0, 1];
  let min = Math.min(...gueltig);
  let max = Math.max(...gueltig);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  // Etwas Luft nach oben und unten, damit die Kurve nicht am Rahmen klebt.
  const luft = (max - min) * 0.12;
  return [min - luft, max + luft];
}

/** Achsenbeschriftungen auf runde Zahlen, wie die Vorgabe es verlangt. */
function ticks(min: number, max: number, anzahl = 4): number[] {
  const spanne = max - min;
  const roh = spanne / anzahl;
  const groesse = Math.pow(10, Math.floor(Math.log10(roh)));
  const schritt = [1, 2, 2.5, 5, 10]
    .map((m) => m * groesse)
    .find((s) => s >= roh) ?? groesse * 10;
  const out: number[] = [];
  for (let t = Math.ceil(min / schritt) * schritt; t <= max; t += schritt) {
    out.push(Number(t.toFixed(6)));
  }
  return out;
}

function zeitBeschriftung(zeit: Date, spanneStunden: number): string {
  if (spanneStunden <= 36) {
    return zeit.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  }
  return zeit.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

export function Zeitreihe({
  titel,
  einheit,
  punkte,
  farbe = "var(--serie-1)",
  band,
  stellen = 1,
}: {
  titel: string;
  einheit: string;
  punkte: Punkt[];
  farbe?: string;
  /** Optionales Unsicherheitsband -- für die Temperaturvorhersage. */
  band?: BandPunkt[];
  stellen?: number;
}) {
  const id = useId();
  const [zeigeTabelle, setZeigeTabelle] = useState(false);
  const [aktiv, setAktiv] = useState<number | null>(null);

  const daten = useMemo(() => punkte.filter((p) => p.wert !== null), [punkte]);

  const geometrie = useMemo(() => {
    if (daten.length < 2) return null;
    const t0 = daten[0].zeit.getTime();
    const t1 = daten[daten.length - 1].zeit.getTime();
    const alleWerte = [
      ...daten.map((p) => p.wert as number),
      ...(band ?? []).flatMap((b) =>
        [b.unten, b.oben].filter((w): w is number => w !== null),
      ),
    ];
    const [min, max] = skala(alleWerte);

    const x = (zeit: Date) =>
      RAND.links +
      ((zeit.getTime() - t0) / Math.max(1, t1 - t0)) *
        (BREITE - RAND.links - RAND.rechts);
    const y = (wert: number) =>
      RAND.oben + (1 - (wert - min) / (max - min)) * (HOEHE - RAND.oben - RAND.unten);

    return { x, y, min, max, t0, t1 };
  }, [daten, band]);

  if (!geometrie) {
    return (
      <figure className="border-rand bg-grund-erhoben rounded-xl border p-4">
        <figcaption className="text-sm font-medium">{titel}</figcaption>
        <p className="text-schrift-leise mt-2 text-sm">
          Noch zu wenige Messwerte für eine Kurve.
        </p>
      </figure>
    );
  }

  const { x, y, min, max, t0, t1 } = geometrie;
  const spanneStunden = (t1 - t0) / 3600_000;
  const linie = daten
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.zeit).toFixed(1)},${y(p.wert as number).toFixed(1)}`)
    .join(" ");

  const bandPfad = (() => {
    const gueltig = (band ?? []).filter((b) => b.unten !== null && b.oben !== null);
    if (gueltig.length < 2) return null;
    const oben = gueltig
      .map((b, i) => `${i === 0 ? "M" : "L"}${x(b.zeit).toFixed(1)},${y(b.oben as number).toFixed(1)}`)
      .join(" ");
    const unten = [...gueltig]
      .reverse()
      .map((b) => `L${x(b.zeit).toFixed(1)},${y(b.unten as number).toFixed(1)}`)
      .join(" ");
    return `${oben} ${unten} Z`;
  })();

  const letzter = daten[daten.length - 1];
  const gezeigt = aktiv !== null ? daten[aktiv] : null;

  function beiBewegung(e: React.PointerEvent<SVGSVGElement>) {
    const kasten = e.currentTarget.getBoundingClientRect();
    const relativ = ((e.clientX - kasten.left) / kasten.width) * BREITE;
    const anteil = (relativ - RAND.links) / (BREITE - RAND.links - RAND.rechts);
    const ziel = t0 + anteil * (t1 - t0);
    let beste = 0;
    let abstand = Infinity;
    daten.forEach((p, i) => {
      const d = Math.abs(p.zeit.getTime() - ziel);
      if (d < abstand) {
        abstand = d;
        beste = i;
      }
    });
    setAktiv(beste);
  }

  return (
    <figure className="border-rand bg-grund-erhoben rounded-xl border p-4">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-medium">
          {titel} <span className="text-schrift-leise font-normal">({einheit})</span>
        </span>
        <button
          type="button"
          onClick={() => setZeigeTabelle((z) => !z)}
          className="text-schrift-leise hover:text-schrift text-xs underline underline-offset-2"
        >
          {zeigeTabelle ? "Diagramm" : "Als Tabelle"}
        </button>
      </figcaption>

      {zeigeTabelle ? (
        <div className="mt-3 max-h-64 overflow-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">
              {titel} in {einheit}, Messwerte nach Zeitpunkt
            </caption>
            <thead className="text-schrift-leise sticky top-0 text-left">
              <tr className="bg-grund-erhoben">
                <th scope="col" className="py-1 font-medium">Zeit</th>
                <th scope="col" className="py-1 text-right font-medium">{einheit}</th>
              </tr>
            </thead>
            <tbody className="zahl">
              {daten.map((p) => (
                <tr key={p.zeit.toISOString()} className="border-rand border-t">
                  <td className="py-1">
                    {p.zeit.toLocaleString("de-DE", {
                      day: "2-digit",
                      month: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </td>
                  <td className="py-1 text-right">
                    {(p.wert as number).toFixed(stellen).replace(".", ",")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative mt-2">
          <svg
            viewBox={`0 0 ${BREITE} ${HOEHE}`}
            className="w-full touch-pan-y"
            role="img"
            aria-labelledby={`${id}-titel`}
            onPointerMove={beiBewegung}
            onPointerLeave={() => setAktiv(null)}
          >
            <title id={`${id}-titel`}>
              {titel} von {daten[0].zeit.toLocaleString("de-DE")} bis{" "}
              {letzter.zeit.toLocaleString("de-DE")}, zuletzt{" "}
              {(letzter.wert as number).toFixed(stellen)} {einheit}
            </title>

            {/* Raster: haarfein, durchgezogen, zurückhaltend. */}
            {ticks(min, max).map((t) => (
              <g key={t}>
                <line
                  x1={RAND.links}
                  x2={BREITE - RAND.rechts}
                  y1={y(t)}
                  y2={y(t)}
                  stroke="var(--rand)"
                  strokeWidth={1}
                />
                <text
                  x={RAND.links - 8}
                  y={y(t) + 4}
                  textAnchor="end"
                  className="zahl"
                  fontSize={11}
                  fill="var(--schrift-leise)"
                >
                  {t.toLocaleString("de-DE", { maximumFractionDigits: stellen })}
                </text>
              </g>
            ))}

            {/* Unsicherheitsband als Wasch, nicht als satter Block. */}
            {bandPfad && <path d={bandPfad} fill={farbe} opacity={0.1} />}

            <path
              d={linie}
              fill="none"
              stroke={farbe}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {/* Endpunkt mit Ring in Flächenfarbe, damit er über der Linie liest. */}
            <circle
              cx={x(letzter.zeit)}
              cy={y(letzter.wert as number)}
              r={4}
              fill={farbe}
              stroke="var(--grund-erhoben)"
              strokeWidth={2}
            />
            <text
              x={x(letzter.zeit) + 10}
              y={y(letzter.wert as number) + 4}
              fontSize={12}
              className="zahl"
              fill="var(--schrift)"
              fontWeight={600}
            >
              {(letzter.wert as number).toFixed(stellen).replace(".", ",")}
            </text>

            {/* Zeitachse: nur Anfang, Mitte, Ende -- mehr wird auf dem Handy zu eng. */}
            {[daten[0], daten[Math.floor(daten.length / 2)], letzter].map((p, i) => (
              <text
                key={i}
                x={x(p.zeit)}
                y={HOEHE - 8}
                textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}
                fontSize={11}
                fill="var(--schrift-leise)"
              >
                {zeitBeschriftung(p.zeit, spanneStunden)}
              </text>
            ))}

            {gezeigt && (
              <g>
                <line
                  x1={x(gezeigt.zeit)}
                  x2={x(gezeigt.zeit)}
                  y1={RAND.oben}
                  y2={HOEHE - RAND.unten}
                  stroke="var(--schrift-leise)"
                  strokeWidth={1}
                />
                <circle
                  cx={x(gezeigt.zeit)}
                  cy={y(gezeigt.wert as number)}
                  r={4}
                  fill={farbe}
                  stroke="var(--grund-erhoben)"
                  strokeWidth={2}
                />
              </g>
            )}
          </svg>

          {gezeigt && (
            <div
              className="border-rand bg-grund pointer-events-none absolute top-0 rounded-lg border px-2 py-1 text-xs shadow-sm"
              style={{
                left: `${(x(gezeigt.zeit) / BREITE) * 100}%`,
                transform: "translateX(-50%)",
              }}
            >
              <div className="text-schrift-leise">
                {gezeigt.zeit.toLocaleString("de-DE", {
                  day: "2-digit",
                  month: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </div>
              <div className="zahl font-semibold">
                {(gezeigt.wert as number).toFixed(stellen).replace(".", ",")} {einheit}
              </div>
            </div>
          )}
        </div>
      )}
    </figure>
  );
}
