"use client";

// Zuverlässigkeitsdiagramm.
//
// Die Frage, die es beantwortet: sagt „70 %" auch in 70 von 100 Fällen Regen?
// Ein perfekt geeichtes Modell liegt auf der Diagonalen. Darunter heisst: es ist
// zu selbstbewusst und verspricht mehr Regen, als kommt.
//
// Die Diagonale ist hier kein Schmuck, sondern der Massstab -- deshalb ist sie die
// einzige Hilfslinie und trägt eine Beschriftung.

import { useId, useState } from "react";

export type Balken = {
  mitte: number;
  vorhergesagt: number;
  beobachtet: number;
  anzahl: number;
};

const GROESSE = 300;
const RAND = { oben: 12, rechts: 12, unten: 34, links: 42 };

export function Zuverlaessigkeit({
  titel,
  balken,
}: {
  titel: string;
  balken: Balken[];
}) {
  const id = useId();
  const [aktiv, setAktiv] = useState<number | null>(null);

  const brauchbar = balken.filter((b) => b.anzahl >= 5);
  if (brauchbar.length < 3) {
    return (
      <figure className="border-rand bg-grund-erhoben rounded-xl border p-4">
        <figcaption className="text-sm font-medium">{titel}</figcaption>
        <p className="text-schrift-leise mt-2 text-sm">
          Noch zu wenige bewertete Vorhersagen. Für eine belastbare Aussage braucht
          es einige hundert.
        </p>
      </figure>
    );
  }

  const x = (a: number) => RAND.links + a * (GROESSE - RAND.links - RAND.rechts);
  const y = (a: number) =>
    GROESSE - RAND.unten - a * (GROESSE - RAND.oben - RAND.unten);

  // Punktgrösse nach Fallzahl: ein Eimer mit tausend Fällen wiegt schwerer als
  // einer mit zehn, und das soll man sehen.
  const maxAnzahl = Math.max(...brauchbar.map((b) => b.anzahl));
  const radius = (n: number) => 4 + 5 * Math.sqrt(n / maxAnzahl);

  const linie = brauchbar
    .map((b, i) => `${i === 0 ? "M" : "L"}${x(b.vorhergesagt)},${y(b.beobachtet)}`)
    .join(" ");

  return (
    <figure className="border-rand bg-grund-erhoben rounded-xl border p-4">
      <figcaption className="text-sm font-medium">{titel}</figcaption>
      <div className="relative mt-2">
        <svg
          viewBox={`0 0 ${GROESSE} ${GROESSE}`}
          className="w-full max-w-sm"
          role="img"
          aria-labelledby={`${id}-t`}
        >
          <title id={`${id}-t`}>
            {titel}: Vorhersage gegen tatsächliche Häufigkeit, {brauchbar.length}{" "}
            Wahrscheinlichkeitsklassen
          </title>

          {[0, 0.25, 0.5, 0.75, 1].map((a) => (
            <g key={a}>
              <text
                x={RAND.links - 8}
                y={y(a) + 4}
                textAnchor="end"
                fontSize={10}
                className="zahl"
                fill="var(--schrift-leise)"
              >
                {a * 100}
              </text>
              <text
                x={x(a)}
                y={GROESSE - RAND.unten + 14}
                textAnchor="middle"
                fontSize={10}
                className="zahl"
                fill="var(--schrift-leise)"
              >
                {a * 100}
              </text>
            </g>
          ))}

          {/* Die Diagonale: hier läge ein perfekt geeichtes Modell. */}
          <line
            x1={x(0)}
            y1={y(0)}
            x2={x(1)}
            y2={y(1)}
            stroke="var(--rand)"
            strokeWidth={1}
          />
          <text
            x={x(0.72)}
            y={y(0.78)}
            fontSize={10}
            fill="var(--schrift-leise)"
            transform={`rotate(-45 ${x(0.72)} ${y(0.78)})`}
          >
            perfekt geeicht
          </text>

          <path
            d={linie}
            fill="none"
            stroke="var(--serie-1)"
            strokeWidth={2}
            strokeLinejoin="round"
          />
          {brauchbar.map((b, i) => (
            <circle
              key={i}
              cx={x(b.vorhergesagt)}
              cy={y(b.beobachtet)}
              r={radius(b.anzahl)}
              fill="var(--serie-1)"
              stroke="var(--grund-erhoben)"
              strokeWidth={2}
              onPointerEnter={() => setAktiv(i)}
              onPointerLeave={() => setAktiv(null)}
              className="cursor-pointer"
            />
          ))}

          <text
            x={x(0.5)}
            y={GROESSE - 4}
            textAnchor="middle"
            fontSize={10}
            fill="var(--schrift-leise)"
          >
            vorhergesagt (%)
          </text>
          <text
            x={12}
            y={y(0.5)}
            textAnchor="middle"
            fontSize={10}
            fill="var(--schrift-leise)"
            transform={`rotate(-90 12 ${y(0.5)})`}
          >
            tatsächlich eingetreten (%)
          </text>
        </svg>

        {aktiv !== null && (
          <div className="border-rand bg-grund absolute top-0 right-0 rounded-lg border px-2 py-1 text-xs">
            <div className="zahl">
              sagt {(brauchbar[aktiv].vorhergesagt * 100).toFixed(0)} % →{" "}
              <strong>{(brauchbar[aktiv].beobachtet * 100).toFixed(0)} %</strong>
            </div>
            <div className="text-schrift-leise zahl">
              {brauchbar[aktiv].anzahl} Fälle
            </div>
          </div>
        )}
      </div>
      <figcaption className="text-schrift-leise mt-2 text-xs">
        Punktgrösse zeigt die Fallzahl. Liegt die Kurve unter der Diagonalen, kündigt
        das Modell mehr Regen an, als eintritt.
      </figcaption>
    </figure>
  );
}
