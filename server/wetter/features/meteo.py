"""Meteorologische Umrechnungen.

Dieses Modul ist die Bruecke zwischen den eigenen Sensoren und den DWD-Daten. Ohne
sie ist ein auf DWD-Daten trainiertes Modell auf der eigenen Station wertlos: der
BME280 misst den Druck auf 237 m Hoehe, der DWD fuehrt ihn auf Meeresniveau -- das
sind rund 28 hPa Unterschied, ein Vielfaches dessen, was ein Wetterwechsel ausmacht.

Alle Funktionen arbeiten elementweise und akzeptieren Skalare wie auch Arrays und
pandas-Serien. Fehlwerte (``NaN``) laufen unveraendert durch.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Magnus-Koeffizienten ueber Wasser nach WMO.
MAGNUS_A = 17.62
MAGNUS_B = 243.12

#: Saettigungsdampfdruck bei 0 degC in hPa.
E0 = 6.112

#: Normfallbeschleunigung in m/s^2.
G_N = 9.80665

#: Spezifische Gaskonstante trockener Luft in J/(kg*K).
R_DRY = 287.05

#: Vertikaler Temperaturgradient der Standardatmosphaere in K/m.
LAPSE_RATE = 0.0065

#: Feuchtekorrektur der DWD-Druckreduktion in K/hPa.
C_H = 0.12

#: Solarkonstante in W/m^2.
SOLAR_CONSTANT = 1361.0


def saturation_vapour_pressure(temp_c):
    """Saettigungsdampfdruck in hPa (Magnus-Formel ueber Wasser)."""
    t = np.asarray(temp_c, dtype=float)
    return E0 * np.exp(MAGNUS_A * t / (MAGNUS_B + t))


def vapour_pressure(temp_c, humidity_pct):
    """Tatsaechlicher Dampfdruck in hPa aus Temperatur und relativer Feuchte."""
    rh = np.asarray(humidity_pct, dtype=float)
    return saturation_vapour_pressure(temp_c) * rh / 100.0


def dewpoint(temp_c, humidity_pct):
    """Taupunkt in degC nach der Magnus-Formel.

    Der Taupunkt ist eines der staerksten Merkmale ueberhaupt: die Differenz zur
    Lufttemperatur sagt, wie weit die Luft von Saettigung entfernt ist -- also wie
    nah an Nebel, Tau oder Niederschlag.
    """
    t = np.asarray(temp_c, dtype=float)
    rh = np.asarray(humidity_pct, dtype=float)
    # Feuchtewerte von exakt 0 % gibt es physikalisch nicht und ergaeben -inf.
    rh = np.where(rh <= 0.0, np.nan, np.minimum(rh, 100.0))
    gamma = np.log(rh / 100.0) + MAGNUS_A * t / (MAGNUS_B + t)
    return MAGNUS_B * gamma / (MAGNUS_A - gamma)


def dewpoint_spread(temp_c, dewpoint_c):
    """Taupunktdifferenz in K. Nahe null bedeutet Saettigung, also Nebelgefahr."""
    return np.asarray(temp_c, dtype=float) - np.asarray(dewpoint_c, dtype=float)


def relative_humidity(temp_c, dewpoint_c):
    """Relative Feuchte in Prozent aus Temperatur und Taupunkt (Umkehrung)."""
    return 100.0 * saturation_vapour_pressure(dewpoint_c) / saturation_vapour_pressure(
        temp_c
    )


def to_sea_level(pressure_hpa, altitude_m, temp_c, humidity_pct=None):
    """Rechnet Stationsdruck auf Meeresniveau um.

    Verwendet die vom DWD genutzte barometrische Hoehenformel mit Feuchtekorrektur,
    damit das Ergebnis mit der DWD-Spalte ``P`` vergleichbar ist:

        p0 = p * exp( g * h / (R * (T + C_h * e + a * h / 2)) )

    ``humidity_pct`` ist optional -- ohne Feuchte faellt die Korrektur weg, was bei
    normalen Temperaturen unter 0,3 hPa Abweichung bedeutet.
    """
    p = np.asarray(pressure_hpa, dtype=float)
    h = np.asarray(altitude_m, dtype=float)
    t_k = np.asarray(temp_c, dtype=float) + 273.15
    e = 0.0 if humidity_pct is None else vapour_pressure(temp_c, humidity_pct)
    denom = R_DRY * (t_k + C_H * e + LAPSE_RATE * h / 2.0)
    return p * np.exp(G_N * h / denom)


def from_sea_level(pressure_sea_hpa, altitude_m, temp_c, humidity_pct=None):
    """Umkehrung von :func:`to_sea_level` -- Meeresniveaudruck auf Stationshoehe."""
    p0 = np.asarray(pressure_sea_hpa, dtype=float)
    h = np.asarray(altitude_m, dtype=float)
    t_k = np.asarray(temp_c, dtype=float) + 273.15
    e = 0.0 if humidity_pct is None else vapour_pressure(temp_c, humidity_pct)
    denom = R_DRY * (t_k + C_H * e + LAPSE_RATE * h / 2.0)
    return p0 / np.exp(G_N * h / denom)


def wind_components(speed, direction_deg):
    """Zerlegt Wind in Ost- und Nordkomponente (u, v) in m/s.

    Richtungen in Grad sind als Modellmerkmal untauglich: 359 Grad und 1 Grad liegen
    nebeneinander, ein Baummodell sieht dort aber den groesstmoeglichen Sprung. Die
    Zerlegung in Komponenten macht daraus einen stetigen Uebergang.

    Meteorologische Konvention: die Richtung ist die, *aus* der der Wind kommt.
    """
    f = np.asarray(speed, dtype=float)
    rad = np.radians(np.asarray(direction_deg, dtype=float))
    return -f * np.sin(rad), -f * np.cos(rad)


def wind_direction(u, v):
    """Umkehrung von :func:`wind_components` -- Richtung in Grad (0..360)."""
    uu = np.asarray(u, dtype=float)
    vv = np.asarray(v, dtype=float)
    return np.degrees(np.arctan2(-uu, -vv)) % 360.0


def angular_difference(a_deg, b_deg):
    """Kuerzeste Winkeldifferenz in Grad, Ergebnis in (-180, 180].

    Fuer die Winddrehung ueber die letzten Stunden -- der klassische Anzeiger fuer
    einen Frontdurchgang.
    """
    diff = (np.asarray(a_deg, dtype=float) - np.asarray(b_deg, dtype=float) + 180.0)
    return (diff % 360.0) - 180.0


def clear_sky_emissivity(dewpoint_c, hour_utc):
    """Emissionsgrad des wolkenlosen Himmels nach Berdahl und Martin (1984).

    Auch ein voellig klarer Himmel strahlt -- und zwar umso waermer, je feuchter die
    Luft ist. Ohne diese Korrektur wuerde eine schwuele klare Nacht als bedeckt
    durchgehen.
    """
    td = np.asarray(dewpoint_c, dtype=float)
    t = np.asarray(hour_utc, dtype=float)
    return 0.711 + 0.0056 * td + 0.000073 * td**2 + 0.013 * np.cos(2 * np.pi * t / 24.0)


def hemispheric_clear_sky_temp(air_temp_c, dewpoint_c, hour_utc):
    """Strahlungstemperatur des wolkenlosen Himmelshalbraums in degC."""
    t_air_k = np.asarray(air_temp_c, dtype=float) + 273.15
    eps = np.clip(clear_sky_emissivity(dewpoint_c, hour_utc), 0.01, 1.0)
    return t_air_k * np.power(eps, 0.25) - 273.15


@dataclass(frozen=True)
class SkyCalibration:
    """Kalibrierung eines nach oben gerichteten IR-Sensors (MLX90614).

    Warum das noetig ist: Berdahl und Martin beschreiben den *gesamten* Himmels-
    halbraum ueber das volle Spektrum. Ein MLX90614 sieht dagegen einen schmalen
    Kegel senkrecht nach oben und misst nur im atmosphaerischen Fenster von etwa
    8 bis 14 Mikrometern -- also genau dort, wo die Atmosphaere durchsichtig ist und
    der Blick praktisch bis in den Weltraum geht. Ein klarer Zenit liest sich damit
    typischerweise 20 bis 30 K kaelter als der Halbraumwert.

    Wer die Halbraumformel ungeprueft nutzt, bekommt bei klarem Himmel einen
    Bedeckungsgrad von 0 % erst weit unter dem, was der Sensor real liefert -- oder
    schlimmer: klare Naechte werden als bedeckt gewertet, und die Frostvorhersage
    zeigt genau dann daneben, wenn sie gebraucht wird.

    Die Vorgabewerte sind ein plausibler Startpunkt, kein Messergebnis. Sobald
    eigene Daten neben den DWD-Bedeckungsgraden vorliegen, ersetzt
    :func:`fit_sky_calibration` sie durch angepasste Werte.
    """

    clear_offset_k: float = -25.0
    """Wieviel kaelter der Sensor den klaren Zenit sieht als den Halbraum."""

    overcast_offset_k: float = -1.0
    """Abstand der geschlossenen Wolkendecke zur Lufttemperatur."""

    min_span_k: float = 3.0
    """Kleinste Spanne, ab der eine Aussage sinnvoll ist."""


DEFAULT_SKY_CALIBRATION = SkyCalibration()


def cloud_cover_from_sky_temp(
    sky_temp_c,
    air_temp_c,
    dewpoint_c,
    hour_utc,
    calibration: SkyCalibration = DEFAULT_SKY_CALIBRATION,
):
    """Schaetzt den Bedeckungsgrad (0..1) aus der IR-Himmelstemperatur.

    Das ist der Sensor, der eine selbstgebaute Station von einer gekauften abhebt:
    ein nach oben gerichteter MLX90614 misst die Strahlungstemperatur des Himmels.
    Klarer Himmel ist sehr kalt (oft unter -40 degC), eine geschlossene Wolkendecke
    strahlt nahezu mit Lufttemperatur.

    Der Bedeckungsgrad ergibt sich als Lage der Messung zwischen dem kalibrierten
    Klarhimmelwert (0) und der Wolkendecke (1), begrenzt auf 0..1. Zur Kalibrierung
    siehe :class:`SkyCalibration`.
    """
    t_sky = np.asarray(sky_temp_c, dtype=float)
    t_air = np.asarray(air_temp_c, dtype=float)

    t_clear = (
        hemispheric_clear_sky_temp(air_temp_c, dewpoint_c, hour_utc)
        + calibration.clear_offset_k
    )
    t_overcast = t_air + calibration.overcast_offset_k

    span = t_overcast - t_clear
    # Bei sehr feuchter, warmer Luft wird die Spanne klein und das Verhaeltnis
    # numerisch unbrauchbar -- dann lieber kein Wert als ein erfundener.
    span = np.where(span < calibration.min_span_k, np.nan, span)
    return np.clip((t_sky - t_clear) / span, 0.0, 1.0)


def fit_sky_calibration(
    sky_temp_c,
    air_temp_c,
    dewpoint_c,
    hour_utc,
    cloud_cover_okta,
    *,
    base: SkyCalibration = DEFAULT_SKY_CALIBRATION,
) -> SkyCalibration:
    """Passt die Sensorkalibrierung an beobachtete Bedeckungsgrade an.

    Gedacht fuer den Abgleich gegen die DWD-Spalte ``V_N`` (Bedeckung in Achteln,
    0 bis 8) der Referenzstation. Genutzt werden nur die eindeutigen Faelle --
    wolkenlos (0 Achtel) und bedeckt (8 Achtel) --, denn nur die legen die beiden
    Endpunkte der Skala fest. Alles dazwischen haengt von Wolkenart und -hoehe ab
    und wuerde die Anpassung eher verzerren als verbessern.

    Gibt bei zu wenigen brauchbaren Beobachtungen ``base`` unveraendert zurueck.
    """
    sky = np.asarray(sky_temp_c, dtype=float)
    air = np.asarray(air_temp_c, dtype=float)
    okta = np.asarray(cloud_cover_okta, dtype=float)
    hemi = hemispheric_clear_sky_temp(air_temp_c, dewpoint_c, hour_utc)

    gueltig = np.isfinite(sky) & np.isfinite(air) & np.isfinite(hemi) & np.isfinite(okta)
    klar = gueltig & (okta <= 0.5)
    bedeckt = gueltig & (okta >= 7.5)

    clear_offset = base.clear_offset_k
    overcast_offset = base.overcast_offset_k
    if klar.sum() >= 50:
        clear_offset = float(np.median(sky[klar] - hemi[klar]))
    if bedeckt.sum() >= 50:
        overcast_offset = float(np.median(sky[bedeckt] - air[bedeckt]))

    return SkyCalibration(
        clear_offset_k=clear_offset,
        overcast_offset_k=overcast_offset,
        min_span_k=base.min_span_k,
    )


def solar_position(timestamps, latitude, longitude):
    """Sonnenhoehe und Azimut in Grad (vereinfachtes NOAA-Verfahren).

    Genauigkeit rund 0,1 Grad -- fuer Merkmale wie den Klarhimmel-Index reichlich.
    ``timestamps`` muss ein zeitzonenbewusster pandas-DatetimeIndex in UTC sein.
    """
    day = timestamps.dayofyear.to_numpy(dtype=float)
    hour = (
        timestamps.hour.to_numpy(dtype=float)
        + timestamps.minute.to_numpy(dtype=float) / 60.0
    )

    # Deklination und Zeitgleichung nach Spencer (1971).
    gamma = 2 * np.pi / 365.0 * (day - 1 + (hour - 12) / 24.0)
    decl = (
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma)
        + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma)
        + 0.00148 * np.sin(3 * gamma)
    )
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma)
        - 0.040849 * np.sin(2 * gamma)
    )

    true_solar_time = hour * 60.0 + eqtime + 4.0 * longitude
    hour_angle = np.radians(true_solar_time / 4.0 - 180.0)

    lat = np.radians(latitude)
    cos_zenith = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(
        hour_angle
    )
    cos_zenith = np.clip(cos_zenith, -1.0, 1.0)
    elevation = np.degrees(np.arcsin(cos_zenith))

    azimuth = np.degrees(
        np.arctan2(
            -np.sin(hour_angle),
            np.tan(decl) * np.cos(lat) - np.sin(lat) * np.cos(hour_angle),
        )
    ) % 360.0
    return elevation, azimuth


def clear_sky_radiation(elevation_deg):
    """Globalstrahlung bei wolkenlosem Himmel in W/m^2 (Modell nach Haurwitz).

    Nachts (Sonne unter dem Horizont) ist das Ergebnis 0.
    """
    elev = np.asarray(elevation_deg, dtype=float)
    cos_z = np.sin(np.radians(elev))
    cos_z = np.where(cos_z <= 0.01, np.nan, cos_z)
    ghi = 1098.0 * cos_z * np.exp(-0.059 / cos_z)
    return np.where(np.isnan(ghi), 0.0, ghi)


def clear_sky_index(measured_wm2, elevation_deg, *, min_elevation_deg=10.0):
    """Verhaeltnis gemessener zu wolkenloser Einstrahlung (0..1,2).

    Ein Wert nahe 1 heisst klarer Himmel, nahe 0 dichte Bewoelkung. Bei tiefem
    Sonnenstand wird der Quotient unbrauchbar (kleine Nenner, lange Schattenwege),
    deshalb gibt es dort ``NaN`` statt einer Scheinzahl.
    """
    elev = np.asarray(elevation_deg, dtype=float)
    clear = clear_sky_radiation(elev)
    ratio = np.asarray(measured_wm2, dtype=float) / np.where(clear <= 0, np.nan, clear)
    return np.where(elev < min_elevation_deg, np.nan, np.clip(ratio, 0.0, 1.2))


def jcm2_to_wm2(joule_per_cm2, hours=1.0):
    """Rechnet die DWD-Einheit J/cm^2 je Stunde in W/m^2 um.

    1 J/cm^2 = 10 000 J/m^2; verteilt auf eine Stunde (3600 s) sind das 2,777... W/m^2.
    """
    return np.asarray(joule_per_cm2, dtype=float) * 10000.0 / (3600.0 * hours)
