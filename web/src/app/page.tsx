import { Hinweis, Kachel, Marke } from "@/components/kachel";
import {
  aktuelleWerte,
  drucktendenz,
  ersteStation,
  sensorzustaende,
} from "@/lib/queries";
import {
  datumZeit,
  druck,
  millimeter,
  prozent,
  seit,
  temperatur,
  tendenz,
  windgeschwindigkeit,
  windrichtung,
  zahl,
} from "@/lib/format";

// Server-Komponente: sie liest direkt aus der Datenbank. Keine Zwischenspeicherung
// -- eine Wetteranzeige, die zehn Minuten alte Werte zeigt, ist kaputt.
export const dynamic = "force-dynamic";

export default async function Jetzt() {
  const station = await ersteStation();
  if (!station) {
    return (
      <Hinweis art="neutral">
        Noch keine Station vorhanden. Sobald die Außenstation das erste Mal Daten
        schickt, legt der Ingest sie selbst an.
      </Hinweis>
    );
  }

  const [werte, tendenz3h, sensoren] = await Promise.all([
    aktuelleWerte(station.id),
    drucktendenz(station.id),
    sensorzustaende(station.id),
  ]);

  if (!werte) {
    return (
      <Hinweis art="neutral">
        Für {station.name} liegen noch keine Stundenwerte vor. Der Worker
        verdichtet die Rohmessungen alle fünf Minuten.
      </Hinweis>
    );
  }

  const imTest = sensoren.filter((s) => s.zustand === "test");
  const veraltet = Date.now() - werte.zeit.getTime() > 3 * 3600_000;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold">{station.name}</h1>
        <p className="text-schrift-leise text-sm">
          Stand {datumZeit(werte.zeit)} ({seit(werte.zeit)})
        </p>
      </div>

      {veraltet && (
        <Hinweis art="gefahr">
          Die jüngste Stunde liegt mehr als drei Stunden zurück. Entweder ist die
          Station offline, oder der Worker verdichtet nicht mehr.
        </Hinweis>
      )}

      {imTest.length > 0 && (
        <Hinweis>
          {imTest.length === 1 ? "Ein Sensor läuft" : `${imTest.length} Sensoren laufen`}{" "}
          im Testmodus: {imTest.map((s) => s.sensor).join(", ")}. Die Werte werden
          angezeigt, fließen aber nicht ins Training der Vorhersage.
        </Hinweis>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <Kachel
          name="Temperatur"
          wert={temperatur(werte.temperatur)}
          zusatz={
            werte.taupunkt !== null ? `Taupunkt ${temperatur(werte.taupunkt)}` : undefined
          }
        />
        <Kachel
          name="Luftdruck"
          wert={druck(werte.druck)}
          zusatz={
            tendenz3h !== null
              ? `${tendenz(tendenz3h)} (${tendenz3h > 0 ? "+" : ""}${zahl(tendenz3h, 1)} hPa/3h)`
              : "Tendenz noch unbekannt"
          }
          hinweis="Auf Meeresniveau gerechnet"
        />
        <Kachel
          name="Luftfeuchte"
          wert={werte.feuchte === null ? "–" : `${zahl(werte.feuchte, 0)} %`}
        />
        <Kachel
          name="Wind"
          wert={windgeschwindigkeit(werte.windGeschwindigkeit)}
          zusatz={
            werte.windRichtung !== null ? `aus ${windrichtung(werte.windRichtung)}` : undefined
          }
          hinweis={
            werte.windBoe !== null
              ? `Böen bis ${windgeschwindigkeit(werte.windBoe)}`
              : undefined
          }
        />
        <Kachel name="Niederschlag" wert={millimeter(werte.niederschlag)} hinweis="in dieser Stunde" />
        <Kachel
          name="Bewölkung"
          wert={prozent(werte.bewoelkung)}
          zusatz={
            werte.himmelstemperatur !== null
              ? `Himmel ${temperatur(werte.himmelstemperatur)}`
              : undefined
          }
        />
        <Kachel
          name="Einstrahlung"
          wert={werte.einstrahlung === null ? "–" : `${zahl(werte.einstrahlung, 0)} W/m²`}
        />
        <Kachel
          name="Messwerte"
          wert={String(werte.stichproben)}
          hinweis="Rohmessungen in dieser Stunde"
        />
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">Sensoren</h2>
        <div className="flex flex-wrap gap-2">
          {sensoren.length === 0 && (
            <p className="text-schrift-leise text-sm">
              Noch keine Sensorzustände gemeldet.
            </p>
          )}
          {sensoren.map((s) => (
            <Marke
              key={s.sensor}
              art={
                s.zustand === "productive" ? "gut" : s.zustand === "test" ? "test" : "aus"
              }
            >
              {s.sensor}
              {s.zustand === "test" && " · Test"}
              {s.zustand === "inactive" && " · aus"}
            </Marke>
          ))}
        </div>
      </section>
    </div>
  );
}
