"""Tests der Bias-Korrektur.

Der Sinn der Quantil-Abbildung ist, dass sie *wertabhängige* Unterschiede korrigiert.
Ein Thermometer an der Hauswand liest tagsüber deutlich zu warm und nachts kaum --
ein einfacher Mittelwertversatz träfe deshalb daneben. Genau das prüfen die Tests.
"""

import numpy as np
import pandas as pd
import pytest

from wetter.models.bias import (
    MIN_SAMPLES,
    QuantileMapping,
    apply_all,
    fit_all,
    fit_mapping,
)


def reihe(werte) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(werte), freq="h", tz="UTC")
    return pd.Series(np.asarray(werte, dtype=float), index=idx)


def _fit(eigene, referenz) -> QuantileMapping:
    """Kurzform für die immer gleiche Abbildung auf temperature_c."""
    return fit_mapping(reihe(eigene), reihe(referenz), column="temperature_c")


@pytest.fixture
def wahrheit() -> np.ndarray:
    """Gut ein Jahr stündlicher Werte mit Tages- und Jahresgang.

    Die Länge ist Absicht: erst ab MIN_SAMPLES_FULL nutzt die Anpassung die volle
    Quantil-Abbildung. Kürzere Reihen prüft
    :func:`test_kurze_reihe_ergibt_nur_einen_versatz` eigens.
    """
    n = 9000
    rng = np.random.default_rng(7)
    t = np.arange(n, dtype=float)
    tagesgang = 8.0 * np.sin(2 * np.pi * t / 24)
    jahresgang = 10.0 * np.sin(2 * np.pi * t / 8760)
    return 10.0 + jahresgang + tagesgang + rng.normal(0, 1.5, n)


def test_konstanter_versatz_wird_zurueckgerechnet(wahrheit):
    eigene = reihe(wahrheit + 1.5)
    referenz = reihe(wahrheit)
    abbildung = fit_mapping(eigene, referenz, column="temperature_c")

    assert abbildung is not None
    assert abbildung.median_shift == pytest.approx(-1.5, abs=0.15)
    korrigiert = abbildung.apply(eigene)
    assert float(np.nanmean(korrigiert - wahrheit)) == pytest.approx(0.0, abs=0.1)


def test_wertabhaengiger_versatz_wird_korrigiert(wahrheit):
    """Der eigentliche Grund für die Quantil-Abbildung.

    Hier liest die eigene Station bei hohen Werten stark zu warm und bei niedrigen
    kaum -- so verhält sich ein Thermometer an einer besonnten Hauswand. Ein
    Mittelwertversatz könnte das nicht beheben.
    """
    verzerrung = 0.15 * np.clip(wahrheit - 10.0, 0.0, None)
    eigene = reihe(wahrheit + verzerrung)
    referenz = reihe(wahrheit)

    abbildung = fit_mapping(eigene, referenz, column="temperature_c")
    korrigiert = abbildung.apply(eigene)

    # Der verbleibende Fehler muss deutlich kleiner sein als vorher.
    vorher = float(np.sqrt(np.mean((np.asarray(eigene) - wahrheit) ** 2)))
    nachher = float(np.sqrt(np.mean((korrigiert - wahrheit) ** 2)))
    assert nachher < vorher / 3.0

    # Und er darf nicht mehr vom Wert abhängen: warme und kalte Hälfte
    # müssen ähnlich gut getroffen sein.
    warm = wahrheit > np.median(wahrheit)
    assert abs(np.mean(korrigiert[warm] - wahrheit[warm])) < 0.3
    assert abs(np.mean(korrigiert[~warm] - wahrheit[~warm])) < 0.3


def test_gleiche_verteilung_bleibt_nahezu_unveraendert(wahrheit):
    abbildung = fit_mapping(reihe(wahrheit), reihe(wahrheit), column="temperature_c")
    korrigiert = abbildung.apply(wahrheit)
    assert np.allclose(korrigiert, wahrheit, atol=0.35)


def test_nur_gemeinsame_stunden_zaehlen(wahrheit):
    """Ein Ausfall der eigenen Station ist kein Verteilungsunterschied."""
    eigene = wahrheit + 1.5
    # Die erste Hälfte der eigenen Reihe fehlt -- und das war die kalte Jahreszeit.
    haelfte = len(eigene) // 2
    eigene[:haelfte] = np.nan
    abbildung = fit_mapping(reihe(eigene), reihe(wahrheit), column="temperature_c")
    assert abbildung is not None
    assert abbildung.samples == len(eigene) - haelfte
    assert abbildung.median_shift == pytest.approx(-1.5, abs=0.2)


def test_kurze_reihe_ergibt_nur_einen_versatz(wahrheit):
    """Unter einem Jahr wird nur verschoben, nicht die Form verändert.

    Eine Quantil-Abbildung, die auf zwei Sommermonaten angepasst wurde, hat nie
    einen Frostwert gesehen und rechnet im Winter Werte zurecht, für die sie keine
    Grundlage hat. Ein konstanter Versatz kann das nicht.
    """
    kurz = wahrheit[:1500]
    abbildung = _fit(kurz + 0.8, kurz)
    assert abbildung is not None
    # Der Abstand zwischen den Stützstellen ist auf beiden Seiten gleich --
    # das Kennzeichen einer reinen Verschiebung.
    diff = abbildung.reference_quantiles - abbildung.own_quantiles
    assert np.allclose(diff, diff[0], atol=1e-9)
    assert diff[0] == pytest.approx(-0.8, abs=0.15)


def test_lange_reihe_nutzt_die_volle_abbildung(wahrheit):
    """Ab einem Jahr darf die Form angepasst werden."""
    # Wertabhängige Verzerrung, die ein reiner Versatz nicht beheben könnte.
    eigene = wahrheit + 0.2 * np.clip(wahrheit - 10.0, 0.0, None)
    abbildung = _fit(eigene, wahrheit)
    diff = abbildung.reference_quantiles - abbildung.own_quantiles
    assert not np.allclose(diff, diff[0], atol=0.05)


def test_zu_wenige_daten_ergeben_keine_korrektur():
    """Unter vier Wochen beschreibt die Abbildung die Wetterlage, nicht die Station."""
    kurz = np.arange(100, dtype=float)
    assert fit_mapping(reihe(kurz), reihe(kurz + 1), column="temperature_c") is None


def test_konstante_reihe_ergibt_keine_korrektur():
    """Eine Abbildung aus einem festen Wert zöge alles auf eine Zahl."""
    fest = np.full(MIN_SAMPLES + 100, 12.0)
    rng = np.random.default_rng(0)
    referenz = rng.normal(10, 3, len(fest))
    assert fit_mapping(reihe(fest), reihe(referenz), column="temperature_c") is None


def test_werte_ausserhalb_des_gelernten_bereichs_werden_fortgeschrieben(wahrheit):
    """Ein Rekordwert soll korrigiert werden, nicht abgeschnitten."""
    abbildung = _fit(wahrheit + 2.0, wahrheit)
    extrem = float(np.max(wahrheit) + 20.0)
    korrigiert = float(abbildung.apply(extrem))
    assert korrigiert < extrem
    assert korrigiert == pytest.approx(extrem - 2.0, abs=0.6)


def test_fehlwerte_bleiben_fehlwerte(wahrheit):
    abbildung = _fit(wahrheit + 1.0, wahrheit)
    out = abbildung.apply([np.nan, 10.0, np.nan])
    assert np.isnan(out[0]) and np.isnan(out[2])
    assert np.isfinite(out[1])


def test_abbildung_ist_monoton(wahrheit):
    """Eine Korrektur darf die Reihenfolge der Messwerte nicht umdrehen."""
    abbildung = _fit(wahrheit + 1.0, wahrheit)
    x = np.linspace(np.min(wahrheit) - 5, np.max(wahrheit) + 5, 300)
    assert np.all(np.diff(abbildung.apply(x)) >= -1e-9)


def test_umkehrung_hebt_die_korrektur_wieder_auf(wahrheit):
    """Hin und zurück muss den Ausgangswert ergeben.

    Gebraucht wird die Umkehrung für die *Ausgabe* der Modelle: sie sind auf
    DWD-Daten trainiert und liefern DWD-Werte, angezeigt wird aber neben den
    Rohwerten der eigenen Station.
    """
    abbildung = _fit(wahrheit + 1.5, wahrheit)
    x = np.linspace(float(np.min(wahrheit)), float(np.max(wahrheit)), 200)
    np.testing.assert_allclose(abbildung.inverse(abbildung.apply(x)), x, atol=1e-6)


def test_umkehrung_zieht_den_versatz_wieder_auf(wahrheit):
    """Eine Vorhersage in DWD-Werten muss auf der Stationsskala landen."""
    abbildung = _fit(wahrheit + 1.5, wahrheit)
    # Das Modell sagt 12 Grad in DWD-Werten -- die Station läge bei rund 13,5.
    assert float(abbildung.inverse(12.0)) == pytest.approx(13.5, abs=0.2)


def test_umkehrung_ausserhalb_des_bereichs_wird_fortgeschrieben(wahrheit):
    abbildung = _fit(wahrheit + 2.0, wahrheit)
    extrem = float(np.max(wahrheit) + 20.0)
    assert float(abbildung.inverse(extrem)) == pytest.approx(extrem + 2.0, abs=0.6)


def test_umkehrung_erhaelt_fehlwerte(wahrheit):
    abbildung = _fit(wahrheit + 1.0, wahrheit)
    out = abbildung.inverse([np.nan, 10.0])
    assert np.isnan(out[0]) and np.isfinite(out[1])


def test_nicht_umkehrbare_abbildung_wird_abgelehnt():
    """Auch die Referenzseite muss streng steigen, sonst geht die Umkehrung nicht.

    Tritt real auf: die Bewölkung führt der DWD in Achteln, da wiederholen sich
    Quantilwerte.
    """
    rng = np.random.default_rng(2)
    n = 9000
    stufig = np.round(rng.uniform(0, 8, n))
    stetig = rng.normal(4, 2, n)
    assert fit_mapping(reihe(stetig), reihe(stufig), column="cloud_cover_okta") is None


def test_serialisierung_ist_verlustfrei(wahrheit):
    abbildung = _fit(wahrheit + 1.0, wahrheit)
    zurueck = QuantileMapping.from_dict(abbildung.to_dict())
    x = np.linspace(0, 25, 50)
    np.testing.assert_allclose(zurueck.apply(x), abbildung.apply(x))
    np.testing.assert_allclose(zurueck.inverse(x), abbildung.inverse(x))


def test_mehrere_spalten_auf_einmal(wahrheit):
    eigene = pd.DataFrame(
        {"temperature_c": wahrheit + 1.0, "pressure_sea_hpa": 1013.0 + wahrheit * 0.1}
    )
    referenz = pd.DataFrame(
        {"temperature_c": wahrheit, "pressure_sea_hpa": 1010.0 + wahrheit * 0.1}
    )
    abbildungen = fit_all(eigene, referenz)
    assert set(abbildungen) == {"temperature_c", "pressure_sea_hpa"}

    korrigiert = apply_all(eigene, abbildungen)
    assert korrigiert["temperature_c"].mean() == pytest.approx(
        referenz["temperature_c"].mean(), abs=0.1
    )
    # Spalten ohne Abbildung bleiben unangetastet.
    unveraendert = apply_all(eigene, {})
    pd.testing.assert_frame_equal(unveraendert, eigene)
