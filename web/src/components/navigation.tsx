"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Client-Komponente, weil sie den aktuellen Pfad braucht, um den aktiven Punkt zu
// markieren. Der Rest der Seiten bleibt serverseitig.

const PUNKTE = [
  { pfad: "/", name: "Jetzt" },
  { pfad: "/verlauf", name: "Verlauf" },
  { pfad: "/vorhersage", name: "Vorhersage" },
  { pfad: "/verifikation", name: "Güte" },
  { pfad: "/sensoren", name: "Sensoren" },
] as const;

export function Navigation() {
  const pfad = usePathname();

  return (
    <header className="border-rand border-b">
      <nav
        aria-label="Hauptnavigation"
        // Auf schmalen Geräten scrollt die Leiste seitwärts, statt umzubrechen --
        // fünf Punkte in zwei Zeilen sehen nach Fehler aus.
        className="-mx-4 flex gap-1 overflow-x-auto px-4 py-3"
      >
        {PUNKTE.map((p) => {
          const aktiv = p.pfad === "/" ? pfad === "/" : pfad.startsWith(p.pfad);
          return (
            <Link
              key={p.pfad}
              href={p.pfad}
              aria-current={aktiv ? "page" : undefined}
              className={[
                "shrink-0 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                aktiv
                  ? "bg-akzent text-white"
                  : "text-schrift-leise hover:bg-grund-erhoben hover:text-schrift",
              ].join(" ")}
            >
              {p.name}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
