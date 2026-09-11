import { revalidatePath } from "next/cache";

import { Hinweis, Marke } from "@/components/kachel";
import { prisma } from "@/lib/db";
import { ersteStation, sensorzustaende } from "@/lib/queries";
import { datumZeit, seit } from "@/lib/format";

export const dynamic = "force-dynamic";

/** Alle Sensoren, die es geben kann -- auch die noch nicht gekauften. */
const SENSOREN = [
  { key: "bme280", name: "BME280", was: "Druck, Temperatur, Feuchte" },
  { key: "wind_speed", name: "Anemometer", was: "Windgeschwindigkeit" },
  { key: "wind_vane", name: "Windfahne", was: "Windrichtung" },
  { key: "rain_gauge", name: "Kippwaage", was: "Niederschlag" },
  { key: "mlx90614", name: "MLX90614", was: "IR-Himmelstemperatur, Bewölkung" },
  { key: "as3935", name: "AS3935", was: "Blitzerkennung" },
  { key: "bh1750", name: "BH1750", was: "Helligkeit" },
  { key: "ina219", name: "INA219", was: "Solarpanel, Einstrahlung" },
] as const;

const ZUSTAENDE = [
  { wert: "productive", name: "produktiv", art: "gut" as const },
  { wert: "test", name: "Test", art: "test" as const },
  { wert: "inactive", name: "aus", art: "aus" as const },
] as const;

async function zustandSetzen(formular: FormData) {
  "use server";

  const stationKey = String(formular.get("station") ?? "");
  const sensorKey = String(formular.get("sensor") ?? "");
  const zustand = String(formular.get("zustand") ?? "");
  if (!stationKey || !sensorKey || !["productive", "test", "inactive"].includes(zustand)) {
    return;
  }

  // Nicht direkt in wetter.sensor_state schreiben: dann wüsste die Datenbank
  // etwas, das die Station nicht weiss, und beim nächsten Neustart meldete das
  // Gerät seinen alten Zustand. Stattdessen ein Befehl, den der Worker per MQTT
  // zustellt -- die Station meldet den neuen Zustand zurück und der Ingest
  // schreibt ihn auf dem üblichen Weg in die Historie.
  await prisma.stationCommand.create({
    data: { stationKey, sensorKey, desiredState: zustand },
  });
  revalidatePath("/sensoren");
}

export default async function SensorenSeite() {
  const station = await ersteStation();
  if (!station) {
    return <Hinweis art="neutral">Noch keine Station vorhanden.</Hinweis>;
  }

  const [zustaende, offen] = await Promise.all([
    sensorzustaende(station.id),
    prisma.stationCommand.findMany({
      where: { stationKey: station.key, state: "PENDING" },
      orderBy: { createdAt: "desc" },
    }),
  ]);

  const nachSensor = new Map(zustaende.map((z) => [z.sensor, z]));
  const wartend = new Map(offen.map((b) => [b.sensorKey ?? "", b]));
  const imTest = zustaende.filter((z) => z.zustand === "test");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Sensoren</h1>
        <p className="text-schrift-leise mt-1 text-sm">
          Jeder Sensor hat drei Zustände. Solange einer im Testmodus läuft, werden
          seine Werte angezeigt, fliessen aber nicht ins Training der Vorhersage.
        </p>
      </div>

      {imTest.length > 0 && (
        <Hinweis>
          {imTest.length === 1 ? "Ein Sensor liegt" : `${imTest.length} Sensoren liegen`}{" "}
          im Testmodus. Vergiss nicht, sie auf „produktiv" zu stellen, wenn sie
          draussen hängen -- sonst fehlen ihre Werte dauerhaft im Training.
        </Hinweis>
      )}

      <div className="space-y-3">
        {SENSOREN.map((s) => {
          const zustand = nachSensor.get(s.key);
          const aktuell = zustand?.zustand ?? "inactive";
          const befehl = wartend.get(s.key);
          const marke = ZUSTAENDE.find((z) => z.wert === aktuell) ?? ZUSTAENDE[2];

          return (
            <div
              key={s.key}
              className="border-rand bg-grund-erhoben flex flex-wrap items-center justify-between gap-3 rounded-xl border p-4"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{s.name}</span>
                  <Marke art={marke.art}>{marke.name}</Marke>
                  {befehl && (
                    <Marke art="warn">
                      wartet auf Station: {befehl.desiredState}
                    </Marke>
                  )}
                </div>
                <div className="text-schrift-leise mt-0.5 text-sm">{s.was}</div>
                {zustand && (
                  <div className="text-schrift-leise mt-0.5 text-xs">
                    seit {datumZeit(zustand.seit)} ({seit(zustand.seit)})
                  </div>
                )}
              </div>

              <div className="flex gap-1">
                {ZUSTAENDE.map((z) => (
                  <form key={z.wert} action={zustandSetzen}>
                    <input type="hidden" name="station" value={station.key} />
                    <input type="hidden" name="sensor" value={s.key} />
                    <input type="hidden" name="zustand" value={z.wert} />
                    <button
                      type="submit"
                      disabled={aktuell === z.wert}
                      className={[
                        "rounded-lg px-2.5 py-1 text-xs font-medium",
                        aktuell === z.wert
                          ? "bg-akzent cursor-default text-white"
                          : "border-rand hover:bg-grund border",
                      ].join(" ")}
                    >
                      {z.name}
                    </button>
                  </form>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <p className="text-schrift-leise text-xs">
        Änderungen gehen als Befehl an die Station und gelten, sobald sie sie
        bestätigt hat. Ist die Station gerade offline, bleibt der Befehl liegen und
        wird beim nächsten Kontakt zugestellt.
      </p>
    </div>
  );
}
