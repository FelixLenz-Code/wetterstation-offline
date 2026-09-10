"""Herunterladen und Einlesen der DWD-Produktdateien.

Der Bootstrap laeuft genau dann, wenn Internet da ist -- er darf also langsam sein,
muss aber alles zwischenspeichern, damit ein Abbruch nicht alles wiederholt. Deshalb
liegt jede geladene Datei unveraendert im Cache; ein zweiter Lauf holt nur, was fehlt.

Zwei Eigenheiten der Quelle, die hier abgefangen werden:

* Historische Archive tragen ihren Datumsbereich im Dateinamen
  (``stundenwerte_TU_00044_20070401_20241231_hist.zip``). Der Bereich ist vorher nicht
  bekannt, also muss das Verzeichnislisting durchsucht werden.
* Der Datensatz ``solar`` hat als einziger keinen ``historical``/``recent``-Schnitt und
  einen abweichenden Zeitstempel im Format ``YYYYMMDDHH:MM``.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

import httpx
import pandas as pd

from wetter.dwd.catalog import ENCODING, MISSING, Dataset
from wetter.dwd.stations import Station, parse_station_list

log = logging.getLogger(__name__)

DEFAULT_CACHE = Path.home() / ".cache" / "wetter" / "dwd"

#: Der DWD-Server ist gelegentlich zaeh; grosszuegig, aber nicht unendlich.
TIMEOUT = httpx.Timeout(60.0, connect=15.0)


class DwdUnavailable(RuntimeError):
    """Der DWD-Server war nicht erreichbar oder lieferte die Datei nicht.

    Wird bewusst als eigene Klasse gefuehrt: der Aufrufer soll den Bootstrap
    ueberspringen koennen, ohne den laufenden Betrieb zu beenden.
    """


class Downloader:
    """Holt DWD-Dateien und legt sie im Cache ab."""

    def __init__(
        self,
        cache_dir: Path | str = DEFAULT_CACHE,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Downloader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _cache_path(self, url: str) -> Path:
        # Der Dateiname allein reicht nicht -- gleiche Namen in verschiedenen
        # Verzeichnissen. Das Verzeichnis wandert deshalb mit in den Pfad.
        parts = url.rstrip("/").split("/")
        return self.cache_dir / parts[-2] / parts[-1] if len(parts) > 1 else (
            self.cache_dir / parts[-1]
        )

    def fetch(self, url: str, *, use_cache: bool = True) -> bytes:
        """Laedt eine URL, bevorzugt aus dem Cache."""
        path = self._cache_path(url)
        if use_cache and path.is_file():
            return path.read_bytes()
        log.info("DWD: lade %s", url)
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DwdUnavailable(f"{url}: {exc}") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return resp.content

    def fetch_text(self, url: str, *, use_cache: bool = True) -> str:
        return self.fetch(url, use_cache=use_cache).decode(ENCODING)

    def station_list(self, dataset: Dataset, *, use_cache: bool = True) -> list[Station]:
        """Stationsliste eines Datensatzes."""
        text = self.fetch_text(dataset.station_list_url, use_cache=use_cache)
        return parse_station_list(text)

    def historical_url(self, dataset: Dataset, station_id: int) -> str | None:
        """Sucht das historische Archiv einer Station im Verzeichnislisting.

        Gibt ``None`` zurueck, wenn die Station kein historisches Archiv hat -- das
        ist normal fuer junge Stationen und kein Fehler.
        """
        if dataset.flat:
            return None
        listing = self.fetch_text(dataset.archive_url(station_id, recent=False))
        pattern = re.compile(
            rf"stundenwerte_{dataset.key}_{station_id:05d}_\d{{8}}_\d{{8}}_hist\.zip"
        )
        match = pattern.search(listing)
        if match is None:
            return None
        base = dataset.archive_url(station_id, recent=False)
        return f"{base}{match.group(0)}"


def parse_product(raw: bytes, dataset: Dataset) -> pd.DataFrame:
    """Liest ein heruntergeladenes ZIP in einen DataFrame mit kanonischen Spalten.

    Ergebnis: Index ``time`` (UTC, stundengenau), Spalten nach
    :attr:`Dataset.columns`. Fehlwerte (-999) werden zu ``NaN``.
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = [n for n in zf.namelist() if n.startswith("produkt")]
        if not names:
            raise DwdUnavailable(f"{dataset.key}: kein produkt_*.txt im Archiv")
        with zf.open(names[0]) as fh:
            frame = pd.read_csv(
                fh,
                sep=";",
                encoding=ENCODING,
                na_values=[MISSING, str(MISSING), f" {MISSING}"],
                skipinitialspace=True,
            )

    # Spaltennamen tragen fuehrende und folgende Leerzeichen ("  R1").
    frame.columns = [str(c).strip() for c in frame.columns]

    fmt = "%Y%m%d%H:%M" if dataset.woz_timestamp else "%Y%m%d%H"
    stamps = frame["MESS_DATUM"].astype(str).str.strip()
    frame["time"] = pd.to_datetime(stamps, format=fmt, utc=True, errors="coerce")
    if dataset.woz_timestamp:
        # ``solar`` stempelt das Ende des Messintervalls und liegt deshalb nicht auf
        # der vollen Stunde -- der Versatz wandert ueber die Jahre (1981: :09,
        # 2026: :23). Abrunden ordnet den Stundensummenwert seiner Stunde zu und
        # macht den Datensatz mit allen anderen zusammenfuehrbar.
        frame["time"] = frame["time"].dt.floor("h")

    keep = {src: dst for src, dst in dataset.columns.items() if src in frame.columns}
    missing = set(dataset.columns) - set(keep)
    if missing:
        # Kein Abbruch: der DWD fuehrt einzelne Spalten nicht an jeder Station.
        log.warning("%s: Spalten fehlen in der Produktdatei: %s", dataset.key, missing)

    out = frame[["time", *keep]].rename(columns=keep)
    out = out.dropna(subset=["time"]).set_index("time").sort_index()

    # Textspalten (z.B. V_N_I) sind hier nicht im Mapping; alles Uebrige ist Zahl.
    for col in out.columns:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    # Der DWD wiederholt gelegentlich Stunden an Archivgrenzen.
    return out[~out.index.duplicated(keep="last")]


def load_series(
    downloader: Downloader,
    dataset: Dataset,
    station_id: int,
    *,
    include_historical: bool = True,
) -> pd.DataFrame:
    """Laedt eine Station komplett: historisches Archiv plus aktuelle Daten.

    Die beiden Archive ueberlappen sich; bei Dopplungen gewinnen die aktuellen Daten,
    weil sie die nachtraeglich geprueften Werte enthalten.
    """
    frames: list[pd.DataFrame] = []

    if include_historical and not dataset.flat:
        hist = downloader.historical_url(dataset, station_id)
        if hist is not None:
            frames.append(parse_product(downloader.fetch(hist), dataset))

    try:
        recent = downloader.fetch(dataset.archive_url(station_id, recent=True))
        frames.append(parse_product(recent, dataset))
    except DwdUnavailable:
        if not frames:
            raise
        log.warning(
            "%s/%05d: aktuelle Daten nicht verfuegbar, nur historische genutzt",
            dataset.key,
            station_id,
        )

    combined = pd.concat(frames)
    return combined[~combined.index.duplicated(keep="last")].sort_index()
