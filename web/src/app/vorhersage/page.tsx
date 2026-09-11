import { Zeitreihe } from "@/components/diagramm";
import { Hinweis, Kachel } from "@/components/kachel";
import { aktuelleVorhersagen, ersteStation } from "@/lib/queries";
import { datumZeit, prozent, temperatur, zahl } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function VorhersageSeite() {
  const station = await ersteStation();
  if (!station) {
    return <Hinweis art="neutral">Noch keine Station vorhanden.</Hinweis>;
  }

  const alle = await aktuelleVorhersagen(station.id);
  if (alle.length === 0) {
    return (
      <Hinweis art="neutral">
        Noch keine Vorhersagen. Der Worker trainiert nachts um 03:00 Ortszeit und
        rechnet danach alle zehn Minuten -- solange kein Modell aktiv ist, bleibt
        diese Seite leer.
      </Hinweis>
    );
  }

  const regen = alle.filter((v) => v.ziel === "rain");
  const temp = alle.filter((v) => v.ziel === "temperature");
  const ausgestellt = alle[0]?.gueltigAb;

  const tempPunkte = temp.map((v) => ({ zeit: v.gueltigAb, wert: v.wert }));
  const tempBand = temp.map((v) => ({
    zeit: v.gueltigAb,
    wert: v.wert,
    unten: v.quantile?.["0.1"] ?? null,
    oben: v.quantile?.["0.9"] ?? null,
  }));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold">Vorhersage</h1>
        {ausgestellt && (
          <p className="text-schrift-leise text-sm">
            Gerechnet für {datumZeit(ausgestellt)}
          </p>
        )}
      </div>

      {regen.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">Regenwahrscheinlichkeit</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {regen.map((v) => (
              <Kachel
                key={v.vorlaufStunden}
                name={`in ${v.vorlaufStunden} h`}
                wert={prozent(v.wert)}
                hinweis={`bis ${datumZeit(v.gueltigAb)}`}
              />
            ))}
          </div>
          <p className="text-schrift-leise text-xs">
            Wahrscheinlichkeit, dass es im Zeitraum bis dahin mindestens eine Stunde
            regnet. Wie gut diese Zahlen treffen, steht unter „Güte“.
          </p>
        </section>
      )}

      {temp.length >= 2 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">Temperatur</h2>
          <Zeitreihe
            titel="Vorhergesagte Temperatur"
            einheit="°C"
            punkte={tempPunkte}
            band={tempBand}
            farbe="var(--serie-1)"
          />
          <p className="text-schrift-leise text-xs">
            Die Linie ist der wahrscheinlichste Verlauf, das hellere Band schliesst
            80 von 100 Fällen ein. Ein breites Band ist keine Schwäche der Anzeige,
            sondern eine ehrliche Aussage über die Lage.
          </p>
        </section>
      )}

      {temp.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">Im Einzelnen</h2>
          <div className="border-rand overflow-hidden rounded-xl border">
            <table className="w-full text-sm">
              <thead className="bg-grund-erhoben text-schrift-leise text-left">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">Vorlauf</th>
                  <th scope="col" className="px-3 py-2 font-medium">gültig</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Median</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Band</th>
                </tr>
              </thead>
              <tbody className="zahl">
                {temp.map((v) => (
                  <tr key={v.vorlaufStunden} className="border-rand border-t">
                    <td className="px-3 py-2">{v.vorlaufStunden} h</td>
                    <td className="px-3 py-2">{datumZeit(v.gueltigAb)}</td>
                    <td className="px-3 py-2 text-right">{temperatur(v.wert)}</td>
                    <td className="text-schrift-leise px-3 py-2 text-right">
                      {v.quantile
                        ? `${zahl(v.quantile["0.1"], 1)} bis ${zahl(v.quantile["0.9"], 1)}`
                        : "–"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
