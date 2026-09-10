"""Stationsliste des DWD einlesen und die naechstgelegene Station finden.

Die Beschreibungsdateien sind spaltenorientierter Text in latin-1, Aufbau:

    Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge \
Stationsname Bundesland Abgabe
    ----------- --------- --------- ------------- --------- --------- ...
    00044 20070401 20260909             44     52.9336    8.2370 Grossenkneten \
Niedersachsen  Frei

Feste Spaltenbreiten sind hier zweimal fragil: der DWD hat die Spalte "Abgabe" erst
spaeter angehaengt, und bei stillgelegten Stationen fehlt ihr Wert komplett (Station
00106 "Altenau" hat acht Felder, Station 00044 neun). Ausserdem laufen Stations- und
Landesname regelmaessig ueber die in der Strichlinie angegebene Breite hinaus.

Deshalb wird das Bundesland ueber eine feste Laenderliste gefunden: die ersten sechs
Felder sind immer Zahlen, dahinter steht der (mehrteilige) Stationsname, dann das
Bundesland, und danach hoechstens noch die Abgabe-Kennzeichnung.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from wetter.dwd.catalog import Dataset

#: Mittlerer Erdradius in km.
EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Station:
    """Eine DWD-Messstation fuer genau einen Datensatz."""

    station_id: int
    start: date
    end: date
    altitude_m: float
    latitude: float
    longitude: float
    name: str
    state: str

    @property
    def years(self) -> float:
        """Laenge der Messreihe in Jahren."""
        return (self.end - self.start).days / 365.25


def _parse_date(raw: str) -> date:
    return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))


BUNDESLAENDER: frozenset[str] = frozenset(
    {
        "Baden-Württemberg",
        "Bayern",
        "Berlin",
        "Brandenburg",
        "Bremen",
        "Hamburg",
        "Hessen",
        "Mecklenburg-Vorpommern",
        "Niedersachsen",
        "Nordrhein-Westfalen",
        "Rheinland-Pfalz",
        "Saarland",
        "Sachsen",
        "Sachsen-Anhalt",
        "Schleswig-Holstein",
        "Thüringen",
    }
)


def _split_name_and_state(rest: list[str]) -> tuple[str, str] | None:
    """Trennt Stationsname, Bundesland und optionale Abgabe-Spalte.

    Gesucht wird von rechts nach dem letzten Feld, das ein Bundesland ist -- das
    funktioniert unabhaengig davon, ob die Abgabe-Spalte gefuellt ist, und auch bei
    mehrteiligen Stationsnamen wie "Muenstertal-Obermuenstertal".
    """
    for i in range(len(rest) - 1, -1, -1):
        if rest[i] in BUNDESLAENDER:
            name = " ".join(rest[:i])
            return (name, rest[i]) if name else None
    # Unbekanntes Bundesland (der DWD fuehrt vereinzelt Sonderfaelle): auf die
    # Stellung zurueckfallen und lieber eine Station zu viel behalten.
    if len(rest) >= 2:
        name = " ".join(rest[:-1])
        if name:
            return name, rest[-1]
    return None


def parse_station_list(text: str) -> list[Station]:
    """Parst eine ``*_Stundenwerte_Beschreibung_Stationen.txt``.

    Zeilen, die nicht dem erwarteten Aufbau folgen, werden uebersprungen statt zu
    werfen -- eine einzelne kaputte Zeile darf den Bootstrap nicht verhindern.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    stations: list[Station] = []
    # Zeile 0 ist die Kopfzeile, Zeile 1 die Strichlinie.
    for line in lines[2:]:
        parts = line.split()
        if len(parts) < 8:
            continue
        try:
            station_id = int(parts[0])
            start = _parse_date(parts[1])
            end = _parse_date(parts[2])
            altitude = float(parts[3])
            lat = float(parts[4])
            lon = float(parts[5])
        except (ValueError, IndexError):
            continue

        split = _split_name_and_state(parts[6:])
        if split is None:
            continue
        name, state = split

        stations.append(
            Station(
                station_id=station_id,
                start=start,
                end=end,
                altitude_m=altitude,
                latitude=lat,
                longitude=lon,
                name=name,
                state=state,
            )
        )
    return stations


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Grosskreisentfernung nach der Haversine-Formel."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class Candidate:
    """Eine Station mit ihrem Abstand zum eigenen Standort."""

    station: Station
    distance_km: float


def rank_stations(
    stations: list[Station],
    latitude: float,
    longitude: float,
    *,
    min_years: float = 10.0,
    active_since: date | None = None,
    max_distance_km: float = 150.0,
) -> list[Candidate]:
    """Sortiert die brauchbaren Stationen nach Entfernung.

    Ausgesiebt wird, was fuer ein Bootstrap nichts taugt: zu kurze Messreihen,
    laengst stillgelegte Stationen und alles jenseits von ``max_distance_km``.
    """
    if active_since is None:
        active_since = date.today().replace(year=date.today().year - 1)

    out: list[Candidate] = []
    for st in stations:
        if st.years < min_years or st.end < active_since:
            continue
        d = distance_km(latitude, longitude, st.latitude, st.longitude)
        if d > max_distance_km:
            continue
        out.append(Candidate(station=st, distance_km=d))

    out.sort(key=lambda c: c.distance_km)
    return out


def select_per_dataset(
    ranked: dict[str, list[Candidate]],
    *,
    reuse_discount: float = 0.7,
) -> dict[str, Candidate]:
    """Waehlt je Datensatz eine Station, bevorzugt aber Mehrfachnutzung.

    Nicht jede Station misst jede Groesse, deshalb muss pro Datensatz gewaehlt
    werden. Trotzdem sind Daten aus *einer* Station stimmiger als aus fuenf -- eine
    Station, die schon fuer einen anderen Datensatz gewaehlt wurde, wird deshalb mit
    ``reuse_discount`` bevorzugt und gewinnt auch dann, wenn sie etwas weiter weg ist.

    ``ranked`` bildet DWD-Kuerzel auf die nach Entfernung sortierten Kandidaten ab.
    Datensaetze ohne jeden Kandidaten tauchen im Ergebnis nicht auf.
    """
    chosen: dict[str, Candidate] = {}
    used: set[int] = set()

    # Datensaetze mit den wenigsten Kandidaten zuerst -- die sind am staerksten
    # eingeschraenkt, und ihre Wahl gibt den anderen die Richtung vor.
    order = sorted(
        (k for k, v in ranked.items() if v),
        key=lambda k: len(ranked[k]),
    )
    for key in order:
        best = min(
            ranked[key],
            key=lambda c: c.distance_km
            * (reuse_discount if c.station.station_id in used else 1.0),
        )
        chosen[key] = best
        used.add(best.station.station_id)
    return chosen


def describe_selection(
    chosen: dict[str, Candidate], datasets: dict[str, Dataset]
) -> str:
    """Menschenlesbare Zusammenfassung der Stationsauswahl fuers Log und Setup."""
    lines = []
    for key, cand in sorted(chosen.items(), key=lambda kv: kv[1].distance_km):
        ds = datasets.get(key)
        what = ds.directory if ds else key
        st = cand.station
        lines.append(
            f"  {key:<3} {what:<16} {st.name} (#{st.station_id:05d}), "
            f"{cand.distance_km:5.1f} km, {st.years:.0f} Jahre, {st.altitude_m:.0f} m"
        )
    return "\n".join(lines)
