"""Tests der meteorologischen Umrechnungen.

Der wichtigste Teil sind die Vergleiche gegen echte DWD-Messungen in
``fixtures/dwd_freiburg_1443.csv`` (200 Stunden der Station Freiburg, 237 m, quer
ueber ein Jahr gestreut, Temperaturen von -3,9 bis 37,5 degC). Diese Datei enthaelt
sowohl Stationsdruck als auch den vom DWD selbst gerechneten Meeresniveaudruck --
damit laesst sich unsere Druckreduktion gegen die amtliche Rechnung pruefen statt
gegen eine selbst ausgedachte Erwartung.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wetter.features.meteo import (
    DEFAULT_SKY_CALIBRATION,
    SkyCalibration,
    angular_difference,
    clear_sky_index,
    clear_sky_radiation,
    cloud_cover_from_sky_temp,
    dewpoint,
    dewpoint_spread,
    fit_sky_calibration,
    from_sea_level,
    hemispheric_clear_sky_temp,
    jcm2_to_wm2,
    relative_humidity,
    saturation_vapour_pressure,
    solar_position,
    to_sea_level,
    vapour_pressure,
    wind_components,
    wind_direction,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dwd_freiburg_1443.csv"
#: Hoehe der DWD-Station Freiburg (#01443) laut Stationsbeschreibung.
FREIBURG_ALTITUDE_M = 237.0


@pytest.fixture(scope="module")
def dwd() -> pd.DataFrame:
    return pd.read_csv(FIXTURE, index_col="time", parse_dates=["time"])


# --- Dampfdruck und Taupunkt -------------------------------------------------


def test_saettigungsdampfdruck_bei_null_grad():
    assert saturation_vapour_pressure(0.0) == pytest.approx(6.112, abs=1e-6)


def test_saettigungsdampfdruck_bei_zwanzig_grad():
    # Tabellenwert rund 23,4 hPa.
    assert saturation_vapour_pressure(20.0) == pytest.approx(23.4, abs=0.1)


def test_dampfdruck_bei_voller_saettigung_entspricht_saettigungswert():
    assert vapour_pressure(15.0, 100.0) == pytest.approx(
        saturation_vapour_pressure(15.0)
    )


def test_taupunkt_bei_hundert_prozent_gleicht_lufttemperatur():
    for t in (-10.0, 0.0, 12.5, 30.0):
        assert dewpoint(t, 100.0) == pytest.approx(t, abs=1e-9)


def test_taupunkt_bekannter_referenzwert():
    """20 degC bei 50 % Feuchte ergeben rund 9,3 degC Taupunkt."""
    assert dewpoint(20.0, 50.0) == pytest.approx(9.27, abs=0.05)


def test_taupunkt_liegt_nie_ueber_der_lufttemperatur():
    t = np.linspace(-20.0, 40.0, 61)
    for rh in (1.0, 25.0, 60.0, 99.9, 100.0):
        assert np.all(dewpoint(t, rh) <= t + 1e-9)


def test_taupunkt_bei_feuchte_null_ist_nan():
    assert np.isnan(dewpoint(20.0, 0.0))


def test_taupunkt_gegen_echte_dwd_reihe(dwd):
    """Gegen die eigenstaendig gemessene DWD-Taupunktreihe."""
    mine = dewpoint(dwd["temperature_c"], dwd["humidity_pct"])
    err = np.asarray(mine) - dwd["dewpoint_c"].to_numpy()
    # Der DWD rundet auf 0,1 K; mehr als 0,5 K Abweichung waere ein echter Fehler.
    assert np.abs(err).max() < 0.5
    assert abs(err.mean()) < 0.05


def test_relative_feuchte_ist_umkehrung_des_taupunkts():
    for t, rh in ((20.0, 50.0), (-5.0, 80.0), (33.0, 20.0)):
        assert relative_humidity(t, dewpoint(t, rh)) == pytest.approx(rh, abs=1e-6)


def test_taupunktdifferenz():
    assert dewpoint_spread(20.0, 9.3) == pytest.approx(10.7)


# --- Druckreduktion ----------------------------------------------------------


def test_druckreduktion_gegen_dwd_rechnung(dwd):
    """Unsere Formel muss die amtliche DWD-Reduktion treffen.

    Auf die Drucktendenz kommt es bei der Vorhersage an, und die bewegt sich in
    Zehntel-hPa -- ein systematischer Versatz waere hier fatal.
    """
    calc = to_sea_level(
        dwd["pressure_station_hpa"],
        FREIBURG_ALTITUDE_M,
        dwd["temperature_c"],
        dwd["humidity_pct"],
    )
    err = np.asarray(calc) - dwd["pressure_sea_hpa"].to_numpy()
    assert abs(err.mean()) < 0.1
    assert err.std() < 0.1
    assert np.abs(err).max() < 0.3


def test_feuchtekorrektur_verbessert_die_reduktion(dwd):
    """Ohne Feuchte streut das Ergebnis messbar staerker."""
    args = (dwd["pressure_station_hpa"], FREIBURG_ALTITUDE_M, dwd["temperature_c"])
    mit = np.asarray(to_sea_level(*args, dwd["humidity_pct"]))
    ohne = np.asarray(to_sea_level(*args))
    ziel = dwd["pressure_sea_hpa"].to_numpy()
    assert (mit - ziel).std() < (ohne - ziel).std()


def test_druckreduktion_auf_meereshoehe_aendert_nichts():
    assert to_sea_level(1013.25, 0.0, 15.0, 50.0) == pytest.approx(1013.25)


def test_druckreduktion_erhoeht_den_wert():
    """Auf Hoehe gemessener Druck ist immer kleiner als auf Meeresniveau."""
    assert to_sea_level(985.0, 237.0, 10.0, 70.0) > 985.0


def test_druckreduktion_ist_umkehrbar(dwd):
    p0 = to_sea_level(
        dwd["pressure_station_hpa"],
        FREIBURG_ALTITUDE_M,
        dwd["temperature_c"],
        dwd["humidity_pct"],
    )
    back = from_sea_level(
        p0, FREIBURG_ALTITUDE_M, dwd["temperature_c"], dwd["humidity_pct"]
    )
    assert np.allclose(back, dwd["pressure_station_hpa"], atol=1e-6)


# --- Wind --------------------------------------------------------------------


def test_nordwind_zeigt_nach_sueden():
    """Wind *aus* Norden (0 Grad) bewegt Luft nach Sueden, also v negativ."""
    u, v = wind_components(10.0, 0.0)
    assert u == pytest.approx(0.0, abs=1e-9)
    assert v == pytest.approx(-10.0)


def test_ostwind_zeigt_nach_westen():
    u, v = wind_components(10.0, 90.0)
    assert u == pytest.approx(-10.0)
    assert v == pytest.approx(0.0, abs=1e-9)


def test_windkomponenten_erhalten_den_betrag():
    for d in range(0, 360, 15):
        u, v = wind_components(7.5, float(d))
        assert np.hypot(u, v) == pytest.approx(7.5)


def test_windrichtung_ist_umkehrung_der_komponenten():
    for d in range(0, 360, 15):
        u, v = wind_components(5.0, float(d))
        assert wind_direction(u, v) == pytest.approx(float(d), abs=1e-6)


def test_windrichtung_gibt_nie_exakt_360_zurueck():
    """Fliesskomma kann aus einem Hauch unter null glatte 360 machen.

    Ein Vektormittel ueber den Nordpunkt trifft genau diesen Fall; 360 Grad wuerde
    jede Bereichspruefung und Sektorzuordnung dahinter aus dem Tritt bringen.
    """
    # Wind aus Norden: der Vektor zeigt nach Sueden, also v negativ. Ein Hauch
    # positives u laesst arctan2 minimal negativ werden -- genau der Fall.
    assert wind_direction(1e-16, -5.0) == pytest.approx(0.0)
    assert wind_direction(1e-15, -5.0) == pytest.approx(0.0)
    assert wind_direction(0.0, -5.0) == pytest.approx(0.0)
    rng = np.random.default_rng(1)
    u, v = rng.normal(size=200), rng.normal(size=200)
    grad = wind_direction(u, v)
    assert np.all((grad >= 0.0) & (grad < 360.0))


def test_winkeldifferenz_ueber_den_nulldurchgang():
    """350 Grad und 10 Grad liegen 20 Grad auseinander, nicht 340."""
    assert angular_difference(10.0, 350.0) == pytest.approx(20.0)
    assert angular_difference(350.0, 10.0) == pytest.approx(-20.0)


def test_winkeldifferenz_bleibt_im_intervall():
    a = np.arange(0.0, 360.0, 7.0)
    b = np.roll(a, 3)
    diff = angular_difference(a, b)
    assert np.all(diff > -180.0) and np.all(diff <= 180.0)


# --- Bewoelkung aus IR-Himmelstemperatur -------------------------------------


def test_klarer_himmel_ergibt_geringe_bedeckung():
    """Ein tief kalter Zenit bei milder Luft heisst wolkenlos.

    -45 degC bei 12 degC Luft ist ein typischer Messwert fuer klaren Himmel -- der
    Halbraumwert laege hier bei rund -5 degC, deshalb braucht es die Kalibrierung.
    """
    assert cloud_cover_from_sky_temp(-45.0, 12.0, 4.0, 2.0) == pytest.approx(0.0)


def test_bedeckter_himmel_ergibt_volle_bedeckung():
    """Himmelstemperatur nahe Lufttemperatur heisst geschlossene Wolkendecke."""
    assert cloud_cover_from_sky_temp(11.5, 12.0, 10.0, 2.0) == pytest.approx(1.0)


def test_teilbewoelkung_liegt_dazwischen():
    wert = cloud_cover_from_sky_temp(-15.0, 12.0, 4.0, 2.0)
    assert 0.0 < wert < 1.0


def test_halbraumwert_ist_viel_waermer_als_der_zenit():
    """Belegt den Grund fuer die Kalibrierung.

    Berdahl und Martin beschreiben den Himmelshalbraum; ein MLX90614 sieht nur den
    schmalen Zenitkegel im atmosphaerischen Fenster und liest deutlich kaelter.
    Wuerde man den Halbraumwert direkt als Nullpunkt nehmen, kaeme fuer echte
    Klarhimmelmessungen ein negativer Bedeckungsgrad heraus.
    """
    hemi = hemispheric_clear_sky_temp(12.0, 4.0, 2.0)
    assert -10.0 < hemi < 0.0
    assert DEFAULT_SKY_CALIBRATION.clear_offset_k < -15.0


def test_bedeckung_steigt_monoton_mit_der_himmelstemperatur():
    sky = np.linspace(-50.0, 12.0, 40)
    wert = cloud_cover_from_sky_temp(sky, 12.0, 4.0, 2.0)
    assert np.all(np.diff(wert) >= -1e-12)


def test_bedeckung_bleibt_im_erlaubten_bereich():
    sky = np.linspace(-60.0, 25.0, 40)
    wert = cloud_cover_from_sky_temp(sky, 20.0, 8.0, 12.0)
    gueltig = wert[~np.isnan(wert)]
    assert np.all((gueltig >= 0.0) & (gueltig <= 1.0))


def test_bedeckung_ist_nan_wenn_die_spanne_zu_klein_wird():
    """Ruecken Klarhimmel- und Wolkenwert zusammen, ist keine Aussage mehr moeglich.

    Bei warmer, fast gesaettigter Luft schrumpft die Spanne ohnehin -- ohne die
    Kalibrierungsverschiebung liegt sie hier bei nur rund 5 K. Der Test erzwingt den
    Grenzfall ueber ``min_span_k``, statt sich auf eine bestimmte Wetterlage zu
    verlassen.
    """
    eng = SkyCalibration(clear_offset_k=0.0, overcast_offset_k=0.0, min_span_k=10.0)
    assert np.isnan(cloud_cover_from_sky_temp(30.0, 30.0, 29.9, 12.0, eng))
    # Mit der echten Kalibrierung ist die Spanne gross genug fuer eine Aussage.
    assert not np.isnan(cloud_cover_from_sky_temp(30.0, 30.0, 29.9, 12.0))


def test_kalibrierung_findet_die_endpunkte_zurueck():
    """Aus synthetischen Messungen mit bekannter Kalibrierung muss sie herausfallen."""
    echt = SkyCalibration(clear_offset_k=-28.0, overcast_offset_k=-2.0)
    rng = np.random.default_rng(0)
    air = rng.uniform(0.0, 25.0, 400)
    td = air - rng.uniform(2.0, 12.0, 400)
    hour = rng.uniform(0.0, 24.0, 400)
    hemi = hemispheric_clear_sky_temp(air, td, hour)

    okta = np.where(np.arange(400) % 2 == 0, 0.0, 8.0)
    sky = np.where(okta == 0.0, hemi + echt.clear_offset_k, air + echt.overcast_offset_k)

    fit = fit_sky_calibration(sky, air, td, hour, okta)
    assert fit.clear_offset_k == pytest.approx(echt.clear_offset_k, abs=0.5)
    assert fit.overcast_offset_k == pytest.approx(echt.overcast_offset_k, abs=0.5)


def test_kalibrierung_bleibt_bei_zu_wenig_daten_unveraendert():
    fit = fit_sky_calibration([-40.0], [10.0], [3.0], [2.0], [0.0])
    assert fit == DEFAULT_SKY_CALIBRATION


# --- Sonnenstand und Einstrahlung --------------------------------------------


def test_sonnenhoehe_mittags_zur_sommersonnenwende():
    """Freiburg (48 Grad N) erreicht zur Sonnenwende rund 65 Grad Sonnenhoehe."""
    ts = pd.DatetimeIndex(["2026-06-21 11:30"], tz="UTC")
    elev, _ = solar_position(ts, 47.999, 7.842)
    assert elev[0] == pytest.approx(65.5, abs=1.5)


def test_sonnenhoehe_mittags_zur_wintersonnenwende():
    """Zur Wintersonnenwende sind es rund 18,5 Grad."""
    ts = pd.DatetimeIndex(["2026-12-21 11:30"], tz="UTC")
    elev, _ = solar_position(ts, 47.999, 7.842)
    assert elev[0] == pytest.approx(18.5, abs=1.5)


def test_sonne_steht_nachts_unter_dem_horizont():
    ts = pd.DatetimeIndex(["2026-06-21 00:00"], tz="UTC")
    elev, _ = solar_position(ts, 47.999, 7.842)
    assert elev[0] < 0.0


def test_sonne_steht_mittags_im_sueden():
    ts = pd.DatetimeIndex(["2026-06-21 11:30"], tz="UTC")
    _, az = solar_position(ts, 47.999, 7.842)
    assert az[0] == pytest.approx(180.0, abs=10.0)


def test_klarhimmelstrahlung_ist_nachts_null():
    assert clear_sky_radiation(-10.0) == pytest.approx(0.0)


def test_klarhimmelstrahlung_im_zenit():
    """Bei senkrechtem Sonnenstand rund 1030 W/m^2."""
    assert clear_sky_radiation(90.0) == pytest.approx(1035.0, abs=20.0)


def test_klarhimmelindex_bei_wolkenlosem_himmel_nahe_eins():
    ghi = clear_sky_radiation(45.0)
    assert clear_sky_index(ghi, 45.0) == pytest.approx(1.0, abs=1e-6)


def test_klarhimmelindex_ist_bei_tiefem_sonnenstand_nan():
    assert np.isnan(clear_sky_index(100.0, 5.0))


def test_einheitenumrechnung_der_dwd_strahlung():
    """1 J/cm^2 in einer Stunde sind 2,78 W/m^2."""
    assert jcm2_to_wm2(1.0) == pytest.approx(2.7778, abs=1e-3)
    # 360 J/cm^2 je Stunde entsprechen 1000 W/m^2, also vollem Sonnenschein.
    assert jcm2_to_wm2(360.0) == pytest.approx(1000.0, abs=0.1)
