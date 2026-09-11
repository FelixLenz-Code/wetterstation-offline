// Anmeldung eines Browsers für Warnungen.
//
// Der Browser erzeugt das Abo selbst und schickt Endpunkt und Schlüssel hierher.
// Gespeichert wird im Schema `app` -- die Oberfläche besitzt diese Tabelle, und
// der Worker liest sie beim Zustellen.

import { NextResponse } from "next/server";

import { prisma } from "@/lib/db";

type Abo = {
  endpoint?: string;
  keys?: { p256dh?: string; auth?: string };
};

export async function POST(request: Request) {
  let abo: Abo;
  try {
    abo = (await request.json()) as Abo;
  } catch {
    return NextResponse.json({ fehler: "kein gültiges JSON" }, { status: 400 });
  }

  const { endpoint, keys } = abo;
  if (!endpoint || !keys?.p256dh || !keys?.auth) {
    return NextResponse.json(
      { fehler: "endpoint, p256dh und auth werden gebraucht" },
      { status: 400 },
    );
  }

  // Derselbe Browser meldet sich nach jedem Leeren der Website-Daten neu an,
  // oft mit demselben Endpunkt. Ein Upsert verhindert, dass sich Abos häufen,
  // die alle dasselbe Gerät meinen -- sonst käme jede Warnung mehrfach.
  await prisma.pushSubscription.upsert({
    where: { endpoint },
    create: {
      endpoint,
      p256dh: keys.p256dh,
      auth: keys.auth,
      userAgent: request.headers.get("user-agent")?.slice(0, 300) ?? null,
    },
    update: {
      p256dh: keys.p256dh,
      auth: keys.auth,
      // Ein erneutes Anmelden setzt die Fehlerzählung zurück: das Gerät ist
      // offensichtlich wieder da.
      failures: 0,
      lastSeen: new Date(),
    },
  });

  return NextResponse.json({ ok: true });
}

export async function DELETE(request: Request) {
  const { searchParams } = new URL(request.url);
  const endpoint = searchParams.get("endpoint");
  if (!endpoint) {
    return NextResponse.json({ fehler: "endpoint fehlt" }, { status: 400 });
  }
  await prisma.pushSubscription.deleteMany({ where: { endpoint } });
  return NextResponse.json({ ok: true });
}
