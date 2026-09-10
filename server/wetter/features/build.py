"""Merkmalsbau aus einer Stundentabelle im kanonischen Schema.

Dieselbe Funktion laeuft ueber DWD-Daten und ueber die eigenen Sensordaten. Genau
darin liegt der Sinn: ein Modell, das auf Jahrzehnten DWD-Messungen gelernt hat,
laesst sich nur dann auf die eigene Station anwenden, wenn beide Seiten exakt
dieselben Merkmale in derselben Einheit und Definition liefern.

Fehlende Spalten sind ausdruecklich erlaubt und ergeben ``NaN``-Merkmale. Die
Sensoren des Users kommen nach und nach dazu, und LightGBM behandelt ``NaN`` nativ --
das Modell laeuft also auch mit halbem Sensorsatz.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from wetter.features import meteo
from wetter.features.climatology import Climatology

#: Vorlaufzeiten in Stunden, ueber die Tendenzen gebildet werden.
TENDENCY_HOURS: tuple[int, ...] = (1, 3, 6, 12, 24)

#: Fenster in Stunden fuer Niederschlagssummen.
RAIN_WINDOWS: tuple[int, ...] = (1, 3, 6, 12, 24)

#: Ab dieser Stundensumme gilt eine Stunde als "Regen".
RAIN_THRESHOLD_MM = 0.1

#: Nach so vielen Stunden ohne Regen wird nicht weitergezaehlt.
MAX_DRY_HOURS = 240


def _require_hourly(frame: pd.DataFrame) -> None:
    """Stellt sicher, dass der Index ein lueckenloses Stundenraster ist.

    Alle Tendenzen hier arbeiten mit ``shift(n)``, also ueber Zeilen und nicht ueber
    Zeitstempel. Haette die Tabelle Luecken, waere "vor drei Stunden" in Wahrheit
    irgendwas -- und die Drucktendenz, das wichtigste Merkmal ueberhaupt, still falsch.
    """
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("Index muss ein DatetimeIndex sein")
    if frame.index.tz is None:
        raise ValueError("Index muss zeitzonenbewusst sein (UTC)")
    if len(frame) > 1:
        schritte = np.diff(frame.index.to_numpy()).astype("timedelta64[m]")
        if not np.all(schritte == np.timedelta64(60, "m")):
            raise ValueError(
                "Index muss ein lueckenloses Stundenraster sein -- "
                "merge_hourly() erzeugt eins"
            )


def _hours_since_rain(precip: pd.Series) -> np.ndarray:
    """Stunden seit dem letzten Niederschlag, gedeckelt auf :data:`MAX_DRY_HOURS`.

    Lange Trockenheit sagt etwas ueber die Wetterlage aus, aber der Unterschied
    zwischen 300 und 400 trockenen Stunden ist bedeutungslos -- deshalb der Deckel.
    """
    werte = precip.to_numpy(dtype=float)
    nass = werte >= RAIN_THRESHOLD_MM
    out = np.empty(len(werte), dtype=float)
    zaehler = np.nan
    for i, ist_nass in enumerate(nass):
        if np.isnan(werte[i]):
            out[i] = zaehler
            continue
        zaehler = 0.0 if ist_nass else (1.0 if np.isnan(zaehler) else zaehler + 1.0)
        out[i] = min(zaehler, MAX_DRY_HOURS)
    return out


def build_features(
    frame: pd.DataFrame,
    *,
    latitude: float,
    longitude: float,
    altitude_m: float,
    climatology: Climatology | None = None,
    sky_calibration: meteo.SkyCalibration = meteo.DEFAULT_SKY_CALIBRATION,
) -> pd.DataFrame:
    """Baut den Merkmalssatz.

    ``frame`` ist eine Stundentabelle im kanonischen Schema (siehe
    :mod:`wetter.dwd.catalog`), ``altitude_m`` die Hoehe des Messorts.
    """
    _require_hourly(frame)
    idx = frame.index
    f = pd.DataFrame(index=idx)

    def spalte(name: str) -> pd.Series:
        """Holt eine Spalte oder liefert eine reine NaN-Spalte."""
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce")
        return pd.Series(np.nan, index=idx, dtype=float)

    temp = spalte("temperature_c")
    hum = spalte("humidity_pct")

    # --- Druck: immer auf Meeresniveau, sonst nicht mit dem DWD vergleichbar ---
    druck = spalte("pressure_sea_hpa")
    station_druck = spalte("pressure_station_hpa")
    abgeleitet = pd.Series(
        meteo.to_sea_level(station_druck, altitude_m, temp, hum), index=idx
    )
    druck = druck.fillna(abgeleitet)
    f["pressure"] = druck

    # --- Drucktendenz: das wichtigste Merkmal der ganzen Vorhersage ---
    for h in TENDENCY_HOURS:
        f[f"pressure_delta_{h}h"] = druck - druck.shift(h)
    # Zweite Ableitung: faellt der Druck beschleunigt oder verlangsamt er sich?
    f["pressure_accel_3h"] = f["pressure_delta_3h"] - f["pressure_delta_3h"].shift(3)

    # --- Temperatur und Feuchte ---
    f["temperature"] = temp
    f["humidity"] = hum
    aus_feuchte = pd.Series(meteo.dewpoint(temp, hum), index=idx)
    taupunkt = spalte("dewpoint_c").fillna(aus_feuchte)
    f["dewpoint"] = taupunkt
    f["dewpoint_spread"] = meteo.dewpoint_spread(temp, taupunkt)
    for h in (1, 3, 24):
        f[f"temperature_delta_{h}h"] = temp - temp.shift(h)
    f["humidity_delta_3h"] = hum - hum.shift(3)

    # --- Wind: Komponenten statt Grad, plus Drehung ---
    speed = spalte("wind_speed_ms")
    direction = spalte("wind_dir_deg")
    u, v = meteo.wind_components(speed, direction)
    f["wind_speed"] = speed
    f["wind_u"] = u
    f["wind_v"] = v
    f["wind_gust"] = spalte("wind_gust_ms").fillna(
        speed.rolling(3, min_periods=1).max()
    )
    for h in (3, 6):
        f[f"wind_dir_change_{h}h"] = pd.Series(
            meteo.angular_difference(direction, direction.shift(h)), index=idx
        )
    f["wind_speed_delta_3h"] = speed - speed.shift(3)

    # --- Niederschlag ---
    regen = spalte("precip_mm")
    for w in RAIN_WINDOWS:
        f[f"precip_sum_{w}h"] = regen.rolling(w, min_periods=1).sum()
    f["hours_since_rain"] = _hours_since_rain(regen)

    # --- Bewoelkung: aus DWD-Achteln oder aus dem eigenen IR-Sensor ---
    okta = spalte("cloud_cover_okta")
    bedeckung = okta / 8.0
    himmel = spalte("sky_temp_c")
    if himmel.notna().any():
        aus_ir = pd.Series(
            meteo.cloud_cover_from_sky_temp(
                himmel, temp, taupunkt, idx.hour.to_numpy(dtype=float), sky_calibration
            ),
            index=idx,
        )
        bedeckung = bedeckung.fillna(aus_ir)
    f["cloud_cover"] = bedeckung
    f["cloud_cover_delta_3h"] = bedeckung - bedeckung.shift(3)

    # --- Sonnenstand und Einstrahlung ---
    elevation, azimut = meteo.solar_position(idx, latitude, longitude)
    f["solar_elevation"] = elevation
    f["solar_azimuth"] = azimut

    strahlung = spalte("global_radiation_wm2")
    aus_dwd = spalte("global_radiation_jcm2")
    if aus_dwd.notna().any():
        strahlung = strahlung.fillna(pd.Series(meteo.jcm2_to_wm2(aus_dwd), index=idx))
    f["global_radiation"] = strahlung
    f["clear_sky_index"] = pd.Series(
        meteo.clear_sky_index(strahlung, elevation), index=idx
    )
    f["sunshine_min"] = spalte("sunshine_min")
    f["visibility"] = spalte("visibility_m")

    # --- Blitz: hat kein DWD-Gegenstueck, bleibt bei DWD-Daten NaN ---
    f["lightning_count_1h"] = spalte("lightning_count").rolling(1, min_periods=1).sum()
    f["lightning_min_distance_km"] = spalte("lightning_distance_km")

    # --- Zeit als stetige Groessen ---
    stunde = idx.hour.to_numpy(dtype=float) + idx.minute.to_numpy(dtype=float) / 60.0
    tag = idx.dayofyear.to_numpy(dtype=float)
    f["hour_sin"] = np.sin(2 * np.pi * stunde / 24.0)
    f["hour_cos"] = np.cos(2 * np.pi * stunde / 24.0)
    f["doy_sin"] = np.sin(2 * np.pi * tag / 365.25)
    f["doy_cos"] = np.cos(2 * np.pi * tag / 365.25)

    # --- Klimatologie als Bezugsgroesse ---
    if climatology is not None:
        normal = climatology.normal_temp(idx)
        f["temperature_normal"] = normal
        f["temperature_anomaly"] = temp.to_numpy(dtype=float) - normal
        f["rain_probability_normal"] = climatology.normal_rain_probability(idx)

    return f


def feature_names(
    *, with_climatology: bool = True, with_lightning: bool = True
) -> list[str]:
    """Namen aller Merkmale in der Reihenfolge, die :func:`build_features` liefert.

    Dient dem Modell-Register: jedes Modell haelt fest, mit welchem Merkmalssatz es
    trainiert wurde, damit ein spaeter dazugekommener Sensor nicht stillschweigend
    die Spaltenreihenfolge verschiebt.
    """
    idx = pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC")
    leer = pd.DataFrame(index=idx)
    clim = Climatology.empty() if with_climatology else None
    namen = list(
        build_features(
            leer, latitude=50.0, longitude=8.0, altitude_m=0.0, climatology=clim
        ).columns
    )
    if not with_lightning:
        namen = [n for n in namen if not n.startswith("lightning_")]
    return namen
