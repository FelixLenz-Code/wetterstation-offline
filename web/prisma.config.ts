// Prisma 7 nimmt die Verbindungsdaten nicht mehr im Schema entgegen, sondern hier.
//
// Wichtig ist der Abschnitt `tables.external`: alle Tabellen im Schema `wetter`
// gehören Alembic (server/migrations). Prisma stellt sie im Client bereit, fasst
// sie aber bei keiner Migration an. Ohne diesen Eintrag würde `prisma migrate dev`
// beim ersten Lauf anbieten, sie zu löschen, weil sie in keiner Prisma-Migration
// stehen -- und damit die gesamte Messreihe.
import { defineConfig, env } from "prisma/config";

export default defineConfig({
  schema: "prisma/schema.prisma",

  // Nötig, damit `tables.external` überhaupt gelesen wird.
  experimental: {
    externalTables: true,
  },

  datasource: {
    url: env("DATABASE_URL"),
  },

  migrations: {
    path: "prisma/migrations",
  },

  tables: {
    external: [
      "wetter.station",
      "wetter.sensor_state",
      "wetter.measurement",
      "wetter.hourly",
      "wetter.dwd_station",
      "wetter.dwd_hourly",
      "wetter.model",
      "wetter.forecast",
      "wetter.verification",
      "wetter.alembic_version",
    ],
  },
});
