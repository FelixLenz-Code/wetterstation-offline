import { Zeitreihe } from "@/components/diagramm";
import { Hinweis } from "@/components/kachel";
import { ersteStation, verlauf } from "@/lib/queries";

export const dynamic = "force-dynamic";

const ZEITRAEUME = [
  { stunden: 24, name: "24 Stunden" },
  { stunden: 72, name: "3 Tage" },
  { stunden: 168, name: "7 Tage" },
  { stunden: 720, name: "30 Tage" },
] as const;

export default async function Verlauf({
  searchParams,
}: {
  // In Next.js 16 sind searchParams ein Promise und müssen erwartet werden.
  searchParams: Promise<{ h?: string }>;
}) {
  const { h } = await searchParams;
  const gewaehlt =
    ZEITRAEUME.find((z) => String(z.stunden) === h) ?? ZEITRAEUME[1];

  const station = await ersteStation();
  if (!station) {
    return <Hinweis art="neutral">Noch keine Station vorhanden.</Hinweis>;
  }

  const reihe = await verlauf(station.id, gewaehlt.stunden);
  if (reihe.length < 2) {
    return (
      <Hinweis art="neutral">
        Für die letzten {gewaehlt.name} liegen noch zu wenige Stundenwerte vor.
      </Hinweis>
    );
  }

  const zeiten = reihe.map((m) => m.zeit);
  const reihen = (feld: keyof (typeof reihe)[number]) =>
    reihe.map((m, i) => ({ zeit: zeiten[i], wert: m[feld] as number | null }));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-xl font-semibold">Verlauf</h1>
        {/* Filter in einer Zeile über den Diagrammen, wie es sich gehört. */}
        <nav aria-label="Zeitraum" className="flex gap-1">
          {ZEITRAEUME.map((z) => (
            <a
              key={z.stunden}
              href={`/verlauf?h=${z.stunden}`}
              aria-current={z.stunden === gewaehlt.stunden ? "true" : undefined}
              className={[
                "rounded-lg px-2.5 py-1 text-xs font-medium",
                z.stunden === gewaehlt.stunden
                  ? "bg-akzent text-white"
                  : "text-schrift-leise hover:bg-grund-erhoben",
              ].join(" ")}
            >
              {z.name}
            </a>
          ))}
        </nav>
      </div>

      {/* Getrennte Diagramme statt zweier Achsen in einem: Temperatur und Druck
          haben nichts miteinander zu tun, und eine zweite Achse lädt dazu ein,
          Zusammenhänge zu sehen, die nur aus der Skalierung stammen. */}
      <Zeitreihe
        titel="Temperatur"
        einheit="°C"
        punkte={reihen("temperatur")}
        farbe="var(--serie-1)"
      />
      <Zeitreihe
        titel="Luftdruck auf Meeresniveau"
        einheit="hPa"
        punkte={reihen("druck")}
        farbe="var(--serie-3)"
      />
      <Zeitreihe
        titel="Luftfeuchte"
        einheit="%"
        punkte={reihen("feuchte")}
        farbe="var(--serie-1)"
        stellen={0}
      />
      <Zeitreihe
        titel="Wind"
        einheit="m/s"
        punkte={reihen("windGeschwindigkeit")}
        farbe="var(--serie-2)"
      />
      <Zeitreihe
        titel="Niederschlag je Stunde"
        einheit="mm"
        punkte={reihen("niederschlag")}
        farbe="var(--serie-3)"
      />
    </div>
  );
}
