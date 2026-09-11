/// <reference lib="webworker" />

// Service Worker der PWA.
//
// Zwei Aufgaben. Erstens der Offline-Betrieb: eine Wetterstation, die man im
// Garten aufruft, verliert dort regelmässig das WLAN. Dann soll die App die
// zuletzt gesehenen Werte zeigen statt einer Fehlerseite -- mit deutlichem
// Hinweis, dass sie alt sind.
//
// Zweitens die Warnungen. Frost, Sturm und Gewitter sind die Fälle, in denen
// eine Vorhersage etwas wert ist, weil man handeln kann: Pflanzen abdecken,
// Markise einfahren. Eine Warnung, die man erst beim nächsten Öffnen der Seite
// sieht, kommt dafür zu spät.

import { defaultCache } from "@serwist/next/worker";
import type { PrecacheEntry, SerwistGlobalConfig } from "serwist";
import { Serwist } from "serwist";

declare global {
  interface WorkerGlobalScope extends SerwistGlobalConfig {
    __SW_MANIFEST: (PrecacheEntry | string)[] | undefined;
  }
}

declare const self: ServiceWorkerGlobalScope;

const serwist = new Serwist({
  precacheEntries: self.__SW_MANIFEST,
  // Eine neue Fassung soll sofort übernehmen. Bei einer Anzeige ohne Eingaben
  // gibt es nichts, was ein Wechsel unterbrechen könnte.
  skipWaiting: true,
  clientsClaim: true,
  navigationPreload: true,
  runtimeCaching: defaultCache,
});

serwist.addEventListeners();

// --- Warnungen ---

type Warnung = {
  titel: string;
  text: string;
  art?: string;
  url?: string;
};

self.addEventListener("push", (event) => {
  if (!event.data) return;

  let w: Warnung;
  try {
    w = event.data.json() as Warnung;
  } catch {
    w = { titel: "Wetterstation", text: event.data.text() };
  }

  event.waitUntil(
    self.registration.showNotification(w.titel, {
      body: w.text,
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      // Gleichartige Warnungen ersetzen einander, statt sich zu stapeln: drei
      // Frostwarnungen in einer Nacht sind zwei zu viel.
      tag: w.art ?? "wetter",
      // Warnungen bleiben stehen, bis man sie wegtippt -- bei Frost um drei Uhr
      // nachts nützt eine Meldung nichts, die nach fünf Sekunden verschwindet.
      requireInteraction: true,
      data: { url: w.url ?? "/vorhersage" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const ziel = (event.notification.data?.url as string) ?? "/";

  event.waitUntil(
    (async () => {
      const fenster = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      // Ein bereits offenes Fenster wiederverwenden, statt ein zweites zu
      // öffnen -- sonst sammeln sich nach einer Gewitternacht ein halbes
      // Dutzend Tabs an.
      for (const f of fenster) {
        if ("focus" in f) {
          await f.navigate(ziel).catch(() => undefined);
          return f.focus();
        }
      }
      return self.clients.openWindow(ziel);
    })(),
  );
});
