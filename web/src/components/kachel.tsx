// Eine Messwert-Kachel. Bewusst schlicht: Beschriftung klein, Wert groß.
//
// Die Einheit steht an der Zahl und nicht in der Beschriftung, weil man den Wert
// beim Überfliegen als Ganzes liest -- "12,4 °C", nicht "Temperatur in °C: 12,4".

export function Kachel({
  name,
  wert,
  zusatz,
  hinweis,
}: {
  name: string;
  wert: string;
  zusatz?: string;
  hinweis?: string;
}) {
  return (
    <div className="border-rand bg-grund-erhoben rounded-xl border p-4">
      <div className="text-schrift-leise text-xs font-medium tracking-wide uppercase">
        {name}
      </div>
      <div className="zahl mt-1 text-2xl font-semibold">{wert}</div>
      {zusatz && <div className="text-schrift-leise zahl mt-0.5 text-sm">{zusatz}</div>}
      {hinweis && <div className="text-schrift-leise mt-2 text-xs">{hinweis}</div>}
    </div>
  );
}

/** Farbig hinterlegter Zustand -- für Sensorzustände und Warnungen. */
export function Marke({
  art,
  children,
}: {
  art: "gut" | "test" | "aus" | "warn" | "gefahr";
  children: React.ReactNode;
}) {
  const farben = {
    gut: "bg-gut/10 text-gut",
    test: "bg-test/10 text-test",
    aus: "bg-schrift-leise/10 text-schrift-leise",
    warn: "bg-warn/10 text-warn",
    gefahr: "bg-gefahr/10 text-gefahr",
  } as const;
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${farben[art]}`}
    >
      {children}
    </span>
  );
}

/** Hinweisbalken, etwa wenn ein Sensor im Testmodus läuft. */
export function Hinweis({
  art = "warn",
  children,
}: {
  art?: "warn" | "gefahr" | "neutral";
  children: React.ReactNode;
}) {
  const farben = {
    warn: "border-warn/30 bg-warn/5 text-warn",
    gefahr: "border-gefahr/30 bg-gefahr/5 text-gefahr",
    neutral: "border-rand bg-grund-erhoben text-schrift-leise",
  } as const;
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${farben[art]}`}>
      {children}
    </div>
  );
}
