// Prisma-Client für die Weboberfläche.
//
// Prisma 7 arbeitet mit dem Query Compiler statt der früheren Rust-Engine und
// braucht deshalb einen Treiber-Adapter. Die Verbindungsdaten kommen nicht mehr
// aus dem Schema, sondern hier aus der Umgebung.
//
// Wichtig: dieser Client liest das Schema `wetter` nur. Geschrieben wird
// ausschließlich ins Schema `app`. Die Messreihe gehört Alembic.

import { PrismaPg } from "@prisma/adapter-pg";

import { PrismaClient } from "@/generated/prisma/client";

const verbindung = process.env.DATABASE_URL;
if (!verbindung) {
  throw new Error("DATABASE_URL fehlt");
}

function erzeuge() {
  return new PrismaClient({
    adapter: new PrismaPg({ connectionString: verbindung }),
    // Im Betrieb nur Fehler, beim Entwickeln auch die Abfragen -- sonst sucht man
    // langsame Seiten blind.
    log:
      process.env.NODE_ENV === "development"
        ? ["warn", "error"]
        : ["error"],
  });
}

// In der Entwicklung lädt Next.js Module bei jeder Änderung neu. Ohne das
// Festhalten am globalen Objekt entstünde bei jedem Speichern ein neuer Pool,
// und nach ein paar Minuten wären die Verbindungen der Datenbank aufgebraucht.
const global_ = globalThis as unknown as { prisma?: PrismaClient };

export const prisma = global_.prisma ?? erzeuge();

if (process.env.NODE_ENV !== "production") {
  global_.prisma = prisma;
}
