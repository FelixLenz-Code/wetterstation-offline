"""Tests fuer das Einlesen der DWD-Stationslisten.

Die Beispielzeilen stammen woertlich aus
``TU_Stundenwerte_Beschreibung_Stationen.txt`` (abgerufen 2026-09-10) und decken
gezielt die Faelle ab, an denen ein naives Parsing scheitert.
"""

from datetime import date

import pytest

from wetter.dwd.stations import (
    Candidate,
    Station,
    distance_km,
    parse_station_list,
    rank_stations,
    select_per_dataset,
)

HEADER = (
    "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge "
    "Stationsname Bundesland Abgabe\n"
    "----------- --------- --------- ------------- --------- --------- "
    "----------- ---------- ------\n"
)

# Station mit gefuellter Abgabe-Spalte (neun Felder).
ROW_MIT_ABGABE = (
    "00044 20070401 20260909             44     52.9336    8.2370 "
    "Großenkneten                             Niedersachsen       Frei"
)
# Stillgelegte Station -- die Abgabe-Spalte fehlt komplett (acht Felder).
ROW_OHNE_ABGABE = (
    "00106 19990101 20061201            500     51.7976   10.4429 "
    "Altenau                                  Niedersachsen"
)
# Mehrteiliger Stationsname mit Bindestrich und Umlauten.
ROW_LANGER_NAME = (
    "03702 20030101 20061231            515     47.8780    7.8288 "
    "Münstertal-Obermünstertal                Baden-Württemberg   Frei"
)


def test_parst_zeile_mit_abgabe_spalte():
    (st,) = parse_station_list(HEADER + ROW_MIT_ABGABE)
    assert st.station_id == 44
    assert st.name == "Großenkneten"
    assert st.state == "Niedersachsen"
    assert st.altitude_m == pytest.approx(44.0)
    assert st.latitude == pytest.approx(52.9336)
    assert st.start == date(2007, 4, 1)
    assert st.end == date(2026, 9, 9)


def test_parst_zeile_ohne_abgabe_spalte():
    """Bei stillgelegten Stationen fehlt der Abgabe-Wert -- Zeile bleibt gueltig."""
    (st,) = parse_station_list(HEADER + ROW_OHNE_ABGABE)
    assert st.station_id == 106
    assert st.name == "Altenau"
    assert st.state == "Niedersachsen"


def test_parst_mehrteiligen_namen_mit_umlauten():
    (st,) = parse_station_list(HEADER + ROW_LANGER_NAME)
    assert st.name == "Münstertal-Obermünstertal"
    assert st.state == "Baden-Württemberg"


def test_ueberspringt_kaputte_zeilen_statt_zu_werfen():
    text = HEADER + "voelliger unsinn\n" + ROW_MIT_ABGABE
    assert [s.station_id for s in parse_station_list(text)] == [44]


def test_leere_datei_ergibt_leere_liste():
    assert parse_station_list("") == []
    assert parse_station_list(HEADER) == []


def test_jahre_aus_messreihe():
    (st,) = parse_station_list(HEADER + ROW_OHNE_ABGABE)
    assert st.years == pytest.approx(7.9, abs=0.2)


def test_entfernung_gegen_bekannten_wert():
    """Freiburg -> Karlsruhe sind rund 120 km Luftlinie."""
    d = distance_km(47.9990, 7.8421, 49.0069, 8.4037)
    assert d == pytest.approx(120.0, abs=5.0)


def test_entfernung_ist_null_am_selben_ort():
    assert distance_km(48.0, 7.8, 48.0, 7.8) == pytest.approx(0.0)


def _station(sid: int, lat: float, lon: float, *, years: int = 20) -> Station:
    return Station(
        station_id=sid,
        start=date(2026, 1, 1).replace(year=2026 - years),
        end=date(2026, 9, 1),
        altitude_m=200.0,
        latitude=lat,
        longitude=lon,
        name=f"Station {sid}",
        state="Baden-Württemberg",
    )


def test_rangliste_siebt_kurze_messreihen_aus():
    stations = [_station(1, 48.0, 7.8, years=20), _station(2, 48.0, 7.81, years=2)]
    ranked = rank_stations(stations, 48.0, 7.8, min_years=10.0)
    assert [c.station.station_id for c in ranked] == [1]


def test_rangliste_siebt_stillgelegte_stationen_aus():
    alt = Station(
        station_id=9,
        start=date(1980, 1, 1),
        end=date(2005, 1, 1),
        altitude_m=200.0,
        latitude=48.0,
        longitude=7.8,
        name="Stillgelegt",
        state="Bayern",
    )
    assert rank_stations([alt], 48.0, 7.8) == []


def test_rangliste_siebt_zu_weit_entfernte_aus():
    weit = _station(5, 53.5, 10.0)
    assert rank_stations([weit], 48.0, 7.8, max_distance_km=150.0) == []


def test_rangliste_ist_nach_entfernung_sortiert():
    stations = [_station(1, 48.5, 7.8), _station(2, 48.05, 7.8), _station(3, 48.2, 7.8)]
    ranked = rank_stations(stations, 48.0, 7.8)
    assert [c.station.station_id for c in ranked] == [2, 3, 1]


def test_auswahl_bevorzugt_bereits_genutzte_station():
    """Eine schon gewaehlte Station gewinnt auch, wenn sie etwas weiter weg ist.

    Daten aus einer Station sind stimmiger als aus mehreren, deshalb der Rabatt.
    """
    nah = Candidate(station=_station(1, 48.0, 7.8), distance_km=10.0)
    weiter = Candidate(station=_station(2, 48.0, 7.9), distance_km=12.0)
    ranked = {
        # Nur Station 2 misst diese Groesse -- sie wird zwingend gewaehlt.
        "N": [weiter],
        # Hier waere Station 1 naeher, aber 12 * 0.7 = 8.4 < 10.
        "TU": [nah, weiter],
    }
    chosen = select_per_dataset(ranked, reuse_discount=0.7)
    assert chosen["N"].station.station_id == 2
    assert chosen["TU"].station.station_id == 2


def test_auswahl_nimmt_naechste_wenn_rabatt_nicht_reicht():
    nah = Candidate(station=_station(1, 48.0, 7.8), distance_km=10.0)
    weit = Candidate(station=_station(2, 48.0, 8.5), distance_km=80.0)
    chosen = select_per_dataset({"N": [weit], "TU": [nah, weit]}, reuse_discount=0.7)
    assert chosen["TU"].station.station_id == 1


def test_auswahl_ueberspringt_datensaetze_ohne_kandidaten():
    nah = Candidate(station=_station(1, 48.0, 7.8), distance_km=10.0)
    chosen = select_per_dataset({"TU": [nah], "ST": []})
    assert set(chosen) == {"TU"}
