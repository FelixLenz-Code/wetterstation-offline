"""Katalog der DWD-Stundenwert-Datensaetze.

Alle Angaben hier wurden am 2026-09-10 gegen das echte Verzeichnis geprueft:
https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly/

Wichtig fuer den Importer: *nicht jede Station misst jede Groesse*. Station 00044
liefert z.B. Temperatur und Niederschlag, aber weder Druck noch Wind noch Bewoelkung.
Die Stationssuche laeuft deshalb pro Datensatz, nicht einmal fuer alle.

Die Spalten werden ueber die Kopfzeile aufgeloest, nie ueber feste Positionen -- der
DWD haengt gelegentlich Spalten an (die Stationsliste hat inzwischen z.B. eine
zusaetzliche Spalte "Abgabe").
"""

from __future__ import annotations

from dataclasses import dataclass, field

BASE_URL = (
    "https://opendata.dwd.de/climate_environment/CDC"
    "/observations_germany/climate/hourly"
)

#: Fehlwert-Kennzeichen in allen DWD-Produktdateien.
MISSING = -999

#: Die Textdateien sind latin-1 kodiert (Umlaute in Stations- und Landesnamen).
ENCODING = "latin-1"


@dataclass(frozen=True)
class Dataset:
    """Ein DWD-Datensatz (eine Messgroesse) mit seinem Spalten-Mapping."""

    key: str
    """DWD-Kuerzel, z.B. ``TU``. Taucht in Datei- und Verzeichnisnamen auf."""

    directory: str
    """Unterverzeichnis unter :data:`BASE_URL`, z.B. ``air_temperature``."""

    columns: dict[str, str]
    """DWD-Spaltenname -> kanonischer Name in unserem Schema."""

    flat: bool = False
    """True, wenn es kein ``historical/``/``recent/`` gibt (nur ``solar``)."""

    woz_timestamp: bool = False
    """True, wenn ``MESS_DATUM`` das Format ``YYYYMMDDHH:MM`` hat (nur ``solar``)."""

    optional: bool = field(default=True)
    """False fuer Datensaetze, ohne die kein Modell trainiert werden kann."""

    @property
    def station_list_url(self) -> str:
        sub = "" if self.flat else "/recent"
        return (
            f"{BASE_URL}/{self.directory}{sub}"
            f"/{self.key}_Stundenwerte_Beschreibung_Stationen.txt"
        )

    def archive_url(self, station_id: int, *, recent: bool) -> str:
        """URL des ZIP-Archivs fuer eine Station.

        ``solar`` kennt die Trennung nicht und nutzt durchgehend das Suffix ``_row``.
        """
        sid = f"{station_id:05d}"
        if self.flat:
            return f"{BASE_URL}/{self.directory}/stundenwerte_{self.key}_{sid}_row.zip"
        if recent:
            return (
                f"{BASE_URL}/{self.directory}/recent"
                f"/stundenwerte_{self.key}_{sid}_akt.zip"
            )
        # Historische Archive tragen den Datumsbereich im Namen und muessen deshalb
        # aus dem Verzeichnislisting geholt werden -- siehe download.historical_url().
        return f"{BASE_URL}/{self.directory}/historical/"


#: Alle genutzten Datensaetze. Reihenfolge = Prioritaet beim Zusammenfuehren:
#: frueher genannte Datensaetze gewinnen bei doppelten kanonischen Spalten.
#: Ueberschneidung gibt es nur bei ``dewpoint_c`` (aus ``dew_point`` und
#: ``moisture``); ``dew_point`` steht zuerst, weil es an mehr Stationen gefuehrt wird.
#: Aus ``moisture`` werden deshalb nur die Groessen genommen, die es exklusiv hat.
DATASETS: tuple[Dataset, ...] = (
    Dataset(
        key="TU",
        directory="air_temperature",
        columns={"TT_TU": "temperature_c", "RF_TU": "humidity_pct"},
        optional=False,
    ),
    Dataset(
        key="P0",
        directory="pressure",
        columns={"P": "pressure_sea_hpa", "P0": "pressure_station_hpa"},
        optional=False,
    ),
    Dataset(
        key="RR",
        directory="precipitation",
        columns={"R1": "precip_mm", "RS_IND": "precip_indicator"},
        optional=False,
    ),
    Dataset(
        key="FF",
        directory="wind",
        columns={"F": "wind_speed_ms", "D": "wind_dir_deg"},
        optional=False,
    ),
    Dataset(
        key="N",
        directory="cloudiness",
        columns={"V_N": "cloud_cover_okta"},
    ),
    Dataset(
        key="TD",
        directory="dew_point",
        columns={"TD": "dewpoint_c"},
    ),
    Dataset(
        key="TF",
        directory="moisture",
        columns={
            "TD_STD": "dewpoint_c",
            "VP_STD": "vapour_pressure_hpa",
            "TF_STD": "wetbulb_c",
            "ABSF_STD": "abs_humidity_gm3",
        },
    ),
    Dataset(
        key="FX",
        directory="extreme_wind",
        columns={"FX_911": "wind_gust_ms"},
    ),
    Dataset(
        key="SD",
        directory="sun",
        columns={"SD_SO": "sunshine_min"},
    ),
    Dataset(
        key="VV",
        directory="visibility",
        columns={"V_VV": "visibility_m"},
    ),
    Dataset(
        key="ST",
        directory="solar",
        columns={
            "FG_LBERG": "global_radiation_jcm2",
            "FD_LBERG": "diffuse_radiation_jcm2",
            "ATMO_LBERG": "atmo_radiation_jcm2",
        },
        flat=True,
        woz_timestamp=True,
    ),
)

BY_KEY: dict[str, Dataset] = {d.key: d for d in DATASETS}

#: Datensaetze, ohne die der Bootstrap nicht sinnvoll ist.
REQUIRED_KEYS: tuple[str, ...] = tuple(d.key for d in DATASETS if not d.optional)

#: Alle kanonischen Spalten, die aus DWD-Daten entstehen koennen.
CANONICAL_COLUMNS: tuple[str, ...] = tuple(
    dict.fromkeys(name for d in DATASETS for name in d.columns.values())
)
