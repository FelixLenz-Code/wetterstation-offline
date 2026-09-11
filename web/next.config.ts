import withSerwistInit from "@serwist/next";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Die Seiten lesen direkt aus der Datenbank; der Standalone-Ausgabemodus
  // macht das Docker-Abbild deutlich kleiner, weil node_modules nicht
  // vollständig mitmuss.
  output: "standalone",
};

const withSerwist = withSerwistInit({
  swSrc: "src/app/sw.ts",
  swDest: "public/sw.js",
  // Besuchte Seiten landen im Zwischenspeicher. Genau darum geht es: wer die
  // Station im Garten anschaut und das WLAN verliert, soll die letzten Werte
  // weiter sehen statt einer Fehlerseite.
  cacheOnNavigation: true,
  reloadOnOnline: true,
});

// Beim Entwickeln wird Serwist gar nicht erst eingehängt.
//
// Zwei Gründe. Erstens stört ein Service Worker dort mehr, als er nützt: er
// liefert zwischengespeicherte Seiten aus, während man am Code arbeitet.
//
// Zweitens hängt Serwist immer eine webpack-Konfiguration ein, auch wenn es
// abgeschaltet ist. Next 16 nutzt voreingestellt Turbopack und bricht ab, sobald
// eine webpack-Konfiguration ohne passende Turbopack-Konfiguration vorliegt --
// mit einer Meldung, die nicht sagt, dass ein Plugin sie hinzugefügt hat. Der
// Bau für den Betrieb läuft deshalb über `next build --webpack`, siehe
// package.json.
const istEntwicklung = process.env.NODE_ENV === "development";

export default istEntwicklung ? nextConfig : withSerwist(nextConfig);
