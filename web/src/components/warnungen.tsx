"use client";

// Schaltfläche zum An- und Abmelden von Warnungen.
//
// Der Browser fragt beim ersten Klick nach Erlaubnis. Das darf nur auf eine
// echte Nutzerhandlung hin geschehen -- ein Dialog, der beim blossen Öffnen der
// Seite aufspringt, wird von den meisten Menschen weggeklickt, und danach lässt
// er sich nur noch in den Browsereinstellungen zurückholen.

import { useEffect, useState } from "react";

type Zustand = "unbekannt" | "nicht-unterstützt" | "aus" | "an" | "abgelehnt";

/** Der VAPID-Schlüssel kommt als Base64 im URL-Alphabet und muss für die
 *  Push-API als Bytefeld vorliegen.
 *
 *  Der Puffer wird ausdrücklich angelegt, statt `Uint8Array.from` zu nutzen:
 *  seit TypeScript 5.7 ist Uint8Array über seinen Puffertyp generisch, und die
 *  Push-API verlangt einen echten ArrayBuffer statt eines SharedArrayBuffer. */
function schluesselUmwandeln(base64: string): Uint8Array<ArrayBuffer> {
  const fuellung = "=".repeat((4 - (base64.length % 4)) % 4);
  const roh = atob((base64 + fuellung).replace(/-/g, "+").replace(/_/g, "/"));
  const bytes = new Uint8Array(new ArrayBuffer(roh.length));
  for (let i = 0; i < roh.length; i++) {
    bytes[i] = roh.charCodeAt(i);
  }
  return bytes;
}

export function Warnungen({ vapidKey }: { vapidKey: string }) {
  const [zustand, setZustand] = useState<Zustand>("unbekannt");
  const [laeuft, setLaeuft] = useState(false);

  useEffect(() => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      setZustand("nicht-unterstützt");
      return;
    }
    if (Notification.permission === "denied") {
      setZustand("abgelehnt");
      return;
    }
    navigator.serviceWorker.ready
      .then((reg) => reg.pushManager.getSubscription())
      .then((abo) => setZustand(abo ? "an" : "aus"))
      .catch(() => setZustand("aus"));
  }, []);

  async function anmelden() {
    setLaeuft(true);
    try {
      const erlaubnis = await Notification.requestPermission();
      if (erlaubnis !== "granted") {
        setZustand(erlaubnis === "denied" ? "abgelehnt" : "aus");
        return;
      }
      const reg = await navigator.serviceWorker.ready;
      const abo = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: schluesselUmwandeln(vapidKey),
      });
      const antwort = await fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(abo),
      });
      setZustand(antwort.ok ? "an" : "aus");
    } catch {
      setZustand("aus");
    } finally {
      setLaeuft(false);
    }
  }

  async function abmelden() {
    setLaeuft(true);
    try {
      const reg = await navigator.serviceWorker.ready;
      const abo = await reg.pushManager.getSubscription();
      if (abo) {
        await fetch(`/api/push/subscribe?endpoint=${encodeURIComponent(abo.endpoint)}`, {
          method: "DELETE",
        });
        await abo.unsubscribe();
      }
      setZustand("aus");
    } finally {
      setLaeuft(false);
    }
  }

  // Ob der Browser Warnungen kann, weiss erst der Browser. Serverseitig steht
  // deshalb ein Platzhalter derselben Höhe -- sonst springt das Layout, sobald
  // die Seite im Browser lebendig wird.
  if (zustand === "unbekannt") {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <span className="border-rand text-schrift-leise rounded-lg border px-3 py-1.5 text-sm">
          Warnungen auf diesem Gerät
        </span>
      </div>
    );
  }

  if (zustand === "nicht-unterstützt") {
    return (
      <p className="text-schrift-leise text-sm">
        Dieser Browser kann keine Warnungen empfangen.
      </p>
    );
  }

  if (zustand === "abgelehnt") {
    return (
      <p className="text-schrift-leise text-sm">
        Warnungen sind für diese Seite blockiert. Das lässt sich nur in den
        Browsereinstellungen zurücknehmen.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={zustand === "an" ? abmelden : anmelden}
        disabled={laeuft}
        className={[
          "rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-50",
          zustand === "an"
            ? "border-rand hover:bg-grund-erhoben border"
            : "bg-akzent text-white",
        ].join(" ")}
      >
        {laeuft
          ? "einen Moment ..."
          : zustand === "an"
            ? "Warnungen abbestellen"
            : "Warnungen auf diesem Gerät"}
      </button>
      <span className="text-schrift-leise text-xs">
        {zustand === "an"
          ? "Frost, Sturm und Gewitter kommen als Mitteilung."
          : "Frost, Sturm und Gewitter — nur wenn es wirklich darauf ankommt."}
      </span>
    </div>
  );
}
