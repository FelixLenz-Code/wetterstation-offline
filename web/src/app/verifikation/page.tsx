import { Zuverlaessigkeit } from "@/components/zuverlaessigkeit";
import { Hinweis, Marke } from "@/components/kachel";
import { ersteStation, guete, zuverlaessigkeit } from "@/lib/queries";
import { prozent, zahl } from "@/lib/format";

export const dynamic = "force-dynamic";

/** Gewinn gegenüber der Klimatologie. Negativ heisst: schlechter als nichts tun. */
function vorsprung(fehler: number, massstab: number | null): number | null {
  if (massstab === null || massstab <= 0) return null;
  return 1 - fehler / massstab;
}

export default async function VerifikationSeite() {
  const station = await ersteStation();
  if (!station) {
    return <Hinweis art="neutral">Noch keine Station vorhanden.</Hinweis>;
  }

  const [werte, diagramme] = await Promise.all([
    guete(station.id, 30),
    zuverlaessigkeit(station.id, 90),
  ]);

  if (werte.length === 0) {
    return (
      <Hinweis art="neutral">
        Noch keine bewerteten Vorhersagen. Eine Vorhersage lässt sich erst bewerten,
        wenn ihr Zeitpunkt vergangen ist -- bei 24 Stunden Vorlauf dauert das
        entsprechend.
      </Hinweis>
    );
  }

  const regen = werte.filter((w) => w.ziel === "rain");
  const temp = werte.filter((w) => w.ziel === "temperature");

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Güte</h1>
        <p className="text-schrift-leise mt-1 text-sm">
          Jede Vorhersage wird gespeichert und nach Ablauf gegen die Messung
          bewertet. Ohne diese Seite wäre die Vorhersage eine Behauptung.
        </p>
      </div>

      {regen.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">Regen</h2>
          <div className="border-rand overflow-x-auto rounded-xl border">
            <table className="w-full text-sm">
              <caption className="sr-only">
                Brier Score der Regenvorhersage je Vorlaufzeit, verglichen mit der
                Klimatologie
              </caption>
              <thead className="bg-grund-erhoben text-schrift-leise text-left">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">Vorlauf</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Brier</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Klimatologie
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Gewinn</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Fälle</th>
                </tr>
              </thead>
              <tbody className="zahl">
                {regen.map((w) => {
                  const gewinn = vorsprung(w.fehler, w.klimatologie);
                  return (
                    <tr key={w.vorlaufStunden} className="border-rand border-t">
                      <td className="px-3 py-2">{w.vorlaufStunden} h</td>
                      <td className="px-3 py-2 text-right">{zahl(w.fehler, 4)}</td>
                      <td className="text-schrift-leise px-3 py-2 text-right">
                        {zahl(w.klimatologie, 4)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {gewinn === null ? (
                          "–"
                        ) : (
                          <Marke art={gewinn > 0.05 ? "gut" : gewinn > 0 ? "warn" : "gefahr"}>
                            {gewinn > 0 ? "+" : ""}
                            {prozent(gewinn, 1)}
                          </Marke>
                        )}
                      </td>
                      <td className="text-schrift-leise px-3 py-2 text-right">
                        {w.anzahl}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-schrift-leise text-xs">
            Kleinerer Brier Score ist besser. Der Gewinn vergleicht mit der
            Klimatologie -- also der Vorhersage „es wird wie immer um diese
            Jahreszeit". Ein Modell, das die nicht schlägt, hat nichts gelernt.
          </p>
        </section>
      )}

      {diagramme.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">Zuverlässigkeit</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {diagramme.map((d) => (
              <Zuverlaessigkeit
                key={d.vorlaufStunden}
                titel={`Regen, ${d.vorlaufStunden} Stunden Vorlauf`}
                balken={d.balken}
              />
            ))}
          </div>
        </section>
      )}

      {temp.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">Temperatur</h2>
          <div className="border-rand overflow-x-auto rounded-xl border">
            <table className="w-full text-sm">
              <caption className="sr-only">
                Mittlerer absoluter Fehler und Bandabdeckung der Temperaturvorhersage
              </caption>
              <thead className="bg-grund-erhoben text-schrift-leise text-left">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">Vorlauf</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    mittlerer Fehler
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Band trifft
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Fälle</th>
                </tr>
              </thead>
              <tbody className="zahl">
                {temp.map((w) => (
                  <tr key={w.vorlaufStunden} className="border-rand border-t">
                    <td className="px-3 py-2">{w.vorlaufStunden} h</td>
                    <td className="px-3 py-2 text-right">{zahl(w.fehler, 2)} K</td>
                    <td className="px-3 py-2 text-right">
                      {w.bandTreffer === null ? (
                        "–"
                      ) : (
                        <Marke
                          art={
                            Math.abs(w.bandTreffer - 0.8) < 0.07
                              ? "gut"
                              : w.bandTreffer < 0.7
                                ? "gefahr"
                                : "warn"
                          }
                        >
                          {prozent(w.bandTreffer, 0)}
                        </Marke>
                      )}
                    </td>
                    <td className="text-schrift-leise px-3 py-2 text-right">
                      {w.anzahl}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-schrift-leise text-xs">
            Das angezeigte Band soll 80 von 100 Fällen einschliessen. Deutlich
            weniger heisst: die angezeigte Unsicherheit ist zu klein und das Modell
            selbstbewusster, als es sein darf.
          </p>
        </section>
      )}
    </div>
  );
}
