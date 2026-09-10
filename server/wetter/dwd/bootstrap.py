"""Bootstrap: aus DWD-Daten eine durchgehende Stundentabelle bauen.

Das ist der Schritt, der das Projekt ueberhaupt erst am Tag eins nutzbar macht --
statt zwei Jahre auf die eigene Messreihe zu warten, wird auf Jahrzehnten echter
Messungen der naechstgelegenen Stationen trainiert.

Ergebnis ist ein DataFrame im *kanonischen* Schema, also genau dem, in das spaeter
auch die eigenen Sensordaten uebersetzt werden. Nur so ist ein hier trainiertes
Modell auf der eigenen Station ueberhaupt anwendbar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from wetter.dwd.catalog import BY_KEY, DATASETS, REQUIRED_KEYS
from wetter.dwd.download import Downloader, DwdUnavailable, load_series
from wetter.dwd.stations import (
    Candidate,
    rank_stations,
    select_per_dataset,
)

log = logging.getLogger(__name__)


class BootstrapIncomplete(RuntimeError):
    """Ein Pflicht-Datensatz liess sich nicht beschaffen."""


@dataclass
class BootstrapResult:
    """Ergebnis eines Bootstrap-Laufs."""

    frame: pd.DataFrame
    """Stundentabelle im kanonischen Schema, Index ``time`` in UTC."""

    stations: dict[str, Candidate]
    """Je DWD-Kuerzel die genutzte Station."""

    failed: dict[str, str]
    """Datensaetze, die nicht geladen werden konnten, mit Begruendung."""

    @property
    def reference_altitude_m(self) -> float:
        """Hoehe der Druckstation -- noetig, um Druck auf Meeresniveau zu rechnen."""
        cand = self.stations.get("P0")
        return cand.station.altitude_m if cand else 0.0

    def summary(self) -> str:
        f = self.frame
        span = f"{f.index.min():%Y-%m-%d} bis {f.index.max():%Y-%m-%d}" if len(f) else "-"
        lines = [
            f"Stunden: {len(f):,}".replace(",", "."),
            f"Zeitraum: {span}",
            "Abdeckung je Spalte:",
        ]
        for col in f.columns:
            share = float(f[col].notna().mean()) * 100 if len(f) else 0.0
            lines.append(f"  {col:<24} {share:5.1f} %")
        if self.failed:
            lines.append("Nicht geladen:")
            lines.extend(f"  {k}: {v}" for k, v in self.failed.items())
        return "\n".join(lines)


def choose_stations(
    downloader: Downloader,
    latitude: float,
    longitude: float,
    *,
    min_years: float = 10.0,
    max_distance_km: float = 150.0,
) -> tuple[dict[str, Candidate], dict[str, str]]:
    """Sucht je Datensatz die passendste Station zum eigenen Standort."""
    ranked: dict[str, list[Candidate]] = {}
    failed: dict[str, str] = {}

    for ds in DATASETS:
        try:
            stations = downloader.station_list(ds)
        except DwdUnavailable as exc:
            failed[ds.key] = f"Stationsliste nicht ladbar ({exc})"
            continue
        cands = rank_stations(
            stations,
            latitude,
            longitude,
            min_years=min_years,
            max_distance_km=max_distance_km,
        )
        if not cands:
            failed[ds.key] = (
                f"keine Station mit >={min_years:.0f} Jahren "
                f"im Umkreis von {max_distance_km:.0f} km"
            )
            continue
        ranked[ds.key] = cands

    return select_per_dataset(ranked), failed


def merge_hourly(series: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Fuehrt die Einzeldatensaetze zu einer Stundentabelle zusammen.

    Zusammengefuehrt wird auf einem *luckenlosen* Stundenraster: fehlende Stunden
    tauchen als Zeile mit NaN auf statt einfach zu fehlen. Das ist wichtig, weil die
    spaetere Merkmalsberechnung mit Zeitverschiebungen arbeitet -- ohne durchgehendes
    Raster waere "vor drei Stunden" nicht verlaesslich drei Stunden her.
    """
    frames = [f for f in series.values() if len(f)]
    if not frames:
        return pd.DataFrame()

    start = min(f.index.min() for f in frames)
    end = max(f.index.max() for f in frames)
    grid = pd.date_range(start, end, freq="h", tz="UTC", name="time")

    out = pd.DataFrame(index=grid)
    # Reihenfolge des Katalogs entscheidet bei doppelten Spalten.
    for key in (d.key for d in DATASETS):
        frame = series.get(key)
        if frame is None or not len(frame):
            continue
        aligned = frame[~frame.index.duplicated(keep="last")].reindex(grid)
        for col in aligned.columns:
            if col in out.columns:
                out[col] = out[col].fillna(aligned[col])
            else:
                out[col] = aligned[col]
    return out


def bootstrap(
    latitude: float,
    longitude: float,
    *,
    cache_dir: Path | str,
    min_years: float = 10.0,
    max_distance_km: float = 150.0,
    include_historical: bool = True,
    downloader: Downloader | None = None,
) -> BootstrapResult:
    """Laedt die DWD-Referenzdaten fuer einen Standort.

    Fehlt ein *optionaler* Datensatz, laeuft der Bootstrap weiter und vermerkt das --
    die Modelle kommen mit fehlenden Merkmalen zurecht. Fehlt ein Pflicht-Datensatz
    (Temperatur, Druck, Niederschlag, Wind), wird abgebrochen: ohne die ergibt ein
    Training keinen Sinn.
    """
    own = downloader is None
    dl = downloader or Downloader(cache_dir)
    try:
        chosen, failed = choose_stations(
            dl,
            latitude,
            longitude,
            min_years=min_years,
            max_distance_km=max_distance_km,
        )

        series: dict[str, pd.DataFrame] = {}
        for key, cand in chosen.items():
            try:
                series[key] = load_series(
                    dl,
                    BY_KEY[key],
                    cand.station.station_id,
                    include_historical=include_historical,
                )
            except DwdUnavailable as exc:
                failed[key] = str(exc)
                log.warning("%s: Import fehlgeschlagen: %s", key, exc)

        fehlend = [k for k in REQUIRED_KEYS if k not in series]
        if fehlend:
            raise BootstrapIncomplete(
                "Pflicht-Datensaetze fehlen: "
                + ", ".join(f"{k} ({failed.get(k, 'unbekannt')})" for k in fehlend)
            )

        return BootstrapResult(
            frame=merge_hourly(series),
            stations={k: v for k, v in chosen.items() if k in series},
            failed=failed,
        )
    finally:
        if own:
            dl.close()
