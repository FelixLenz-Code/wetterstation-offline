import type { Metadata, Viewport } from "next";

import { Navigation } from "@/components/navigation";

import "./globals.css";

export const metadata: Metadata = {
  title: "Wetterstation",
  description:
    "Eigene Messwerte und eine Vorhersage, die ohne Internet auskommt.",
  applicationName: "Wetterstation",
  manifest: "/manifest.json",
  appleWebApp: { capable: true, title: "Wetter", statusBarStyle: "default" },
  icons: {
    icon: [
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1116" },
  ],
  // Zoom bleibt erlaubt: eine Wetterseite, die man nicht vergrößern kann, ist
  // für alle unbrauchbar, die kleine Schrift schlecht lesen.
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="de">
      <body className="min-h-dvh">
        <div className="mx-auto flex min-h-dvh max-w-5xl flex-col px-4">
          <Navigation />
          <main className="flex-1 py-6">{children}</main>
          <footer className="border-rand text-schrift-leise border-t py-6 text-xs">
            <p>
              Historische Vergleichsdaten: Deutscher Wetterdienst, Climate Data
              Center. Frei nutzbar nach GeoNutzV.
            </p>
          </footer>
        </div>
      </body>
    </html>
  );
}
