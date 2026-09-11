"""Ende-zu-Ende-Test der Server-Schleife.

Prüft den kompletten Weg, den ein Messwert im Betrieb nimmt:

    Stundenwerte -> Merkmale -> Training -> Register -> Vorhersage -> Verifikation

Die Einzelteile sind anderswo getestet; hier geht es darum, dass sie zusammenpassen.
Genau an den Nahtstellen sitzen die Fehler, die einzeln getestete Bausteine nicht
zeigen: eine vertauschte Zeitkonvention, eine Spaltenreihenfolge, die sich zwischen
Training und Inferenz verschiebt, oder eine Verifikation, die ein anderes Ereignis
nachschlägt als das vorhergesagte.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from wetter.db.models import Forecast, Hourly, Station, Verification
from wetter.features.build import build_features
from wetter.features.climatology import Climatology
from wetter.features.targets import build_targets
from wetter.models.registry import (
    STATUS_SHADOW,
    active_model,
    clear_cache,
    list_models,
    load_rain_model,
    missing_features,
    promote,
    save_rain_model,
    save_temp_model,
)
from wetter.models.split import TimeSplit
from wetter.models.train import train_rain_model, train_temp_model
from wetter.worker.forecast import run_forecasts
from wetter.worker.verify import observed_rain, score_forecast, verify_pending

STUNDEN = 4000
START = datetime(2025, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def kunstwetter() -> pd.DataFrame:
    """Erzeugt eine Wetterreihe mit echtem Zusammenhang zwischen Druck und Regen.

    Kein Zufallsrauschen: das Modell muss etwas zu lernen *haben*, sonst prüft der
    Test nur, ob der Code durchläuft. Fallender Druck erhöht hier die
    Regenwahrscheinlichkeit -- derselbe Zusammenhang, auf dem auch Zambretti beruht.
    """
    rng = np.random.default_rng(42)
    idx = pd.date_range(START, periods=STUNDEN, freq="h", tz="UTC")
    t = np.arange(STUNDEN, dtype=float)

    # Tages- und Jahresgang plus langsame Wetterwellen.
    tagesgang = 6.0 * np.sin(2 * np.pi * (t % 24) / 24 - np.pi / 2)
    jahresgang = 10.0 * np.sin(2 * np.pi * t / (24 * 365))
    welle = 8.0 * np.sin(2 * np.pi * t / 120) + 4.0 * np.sin(2 * np.pi * t / 53)

    druck = 1013.0 + welle + rng.normal(0, 0.6, STUNDEN)
    temp = 10.0 + jahresgang + tagesgang - 0.25 * welle + rng.normal(0, 0.8, STUNDEN)

    # Regen folgt dem Druckfall über drei Stunden.
    tendenz = pd.Series(druck).diff(3).to_numpy()
    neigung = 1.0 / (1.0 + np.exp((tendenz + 1.0) * 1.6))
    regen = (rng.random(STUNDEN) < neigung * 0.55) * rng.gamma(1.4, 0.8, STUNDEN)

    feuchte = np.clip(62.0 + 14.0 * neigung - 0.9 * tagesgang + rng.normal(0, 4, STUNDEN),
                      15.0, 100.0)
    wind = np.clip(2.5 + 2.2 * neigung + rng.normal(0, 0.9, STUNDEN), 0.0, None)
    drehung = 90.0 * np.sin(2 * np.pi * t / 120)
    richtung = (180.0 + drehung + rng.normal(0, 12, STUNDEN)) % 360

    return pd.DataFrame(
        {
            "temperature_c": temp,
            "humidity_pct": feuchte,
            "pressure_station_hpa": druck - 28.0,
            "pressure_sea_hpa": druck,
            "precip_mm": np.round(regen, 2),
            "wind_speed_ms": wind,
            "wind_dir_deg": richtung,
        },
        index=idx,
    )


@pytest.fixture(autouse=True)
def frischer_zwischenspeicher():
    """Der Modell-Zwischenspeicher darf nicht über Tests hinweg wirken.

    Die Tabellen werden zwischen den Tests geleert und die Kennungen beginnen wieder
    bei 1 -- ein zwischengespeichertes Modell aus dem Vortest würde dann unter
    derselben Kennung wiederverwendet.
    """
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def station(session) -> Station:
    s = Station(
        key="testgarten",
        name="Testgarten",
        latitude=48.0,
        longitude=7.85,
        altitude_m=237.0,
        created_at=datetime.now(UTC),
    )
    session.add(s)
    session.flush()
    return s


@pytest.fixture
def gefuellt(session, station, kunstwetter) -> Station:
    """Schreibt die Kunstreihe als Stundenwerte in die Datenbank."""
    session.bulk_save_objects(
        [
            Hourly(
                station_id=station.id,
                time=zeit.to_pydatetime(),
                sample_count=6,
                **{k: float(v) for k, v in reihe.items() if pd.notna(v)},
            )
            for zeit, reihe in kunstwetter.iterrows()
        ]
    )
    session.flush()
    return station


@pytest.fixture(scope="module")
def trainiert(kunstwetter, tmp_path_factory):
    """Trainiert je ein Regen- und ein Temperaturmodell auf der Kunstreihe."""
    clim = Climatology.from_hourly(kunstwetter)
    merkmale = build_features(
        kunstwetter, latitude=48.0, longitude=7.85, altitude_m=237.0, climatology=clim
    ).drop(columns=lambda_cols(kunstwetter))
    ziele = build_targets(kunstwetter)
    split = TimeSplit.by_fraction(merkmale.index, train=0.7, calib=0.15)

    regen = train_rain_model(
        merkmale,
        ziele["rain_next_6h"],
        lead_hours=6,
        split=split,
        num_boost_round=60,
        early_stopping_rounds=15,
    )
    temp = train_temp_model(
        merkmale,
        ziele["temp_at_6h"],
        lead_hours=6,
        split=split,
        quantiles=(0.1, 0.5, 0.9),
        num_boost_round=60,
        early_stopping_rounds=15,
    )
    return regen, temp, clim, merkmale, ziele


def lambda_cols(frame: pd.DataFrame) -> list[str]:
    """Blitzmerkmale gibt es in dieser Reihe nicht -- sie wären durchgehend leer."""
    return ["lightning_count_1h", "lightning_min_distance_km"]


# --- Register ---------------------------------------------------------------


def test_modell_wird_abgelegt_und_wieder_geladen(session, trainiert, tmp_path):
    regen, _, _, merkmale, _ = trainiert
    ref = save_rain_model(
        session,
        regen,
        root=tmp_path,
        train_start=merkmale.index[0].to_pydatetime(),
        train_end=merkmale.index[-1].to_pydatetime(),
    )
    session.flush()

    geladen = load_rain_model(session, ref.id)
    assert geladen.lead_hours == 6
    assert geladen.feature_names == regen.feature_names
    assert geladen.calibrator is not None

    # Dieselbe Eingabe muss dieselbe Vorhersage liefern -- sonst ist beim Ablegen
    # etwas verlorengegangen.
    eingabe = merkmale.iloc[-50:]
    np.testing.assert_allclose(
        geladen.predict(eingabe), regen.predict(eingabe), rtol=1e-9
    )


def test_neues_modell_startet_im_schattenbetrieb(session, trainiert, tmp_path):
    """Ein zusätzlicher Sensor macht ein Modell nicht automatisch besser."""
    regen, _, _, merkmale, _ = trainiert
    ref = save_rain_model(
        session,
        regen,
        root=tmp_path,
        train_start=merkmale.index[0].to_pydatetime(),
        train_end=merkmale.index[-1].to_pydatetime(),
    )
    session.flush()
    assert ref.status == STATUS_SHADOW
    assert active_model(session, "rain", 6) is None


def test_befoerderung_versetzt_das_alte_modell_in_den_ruhestand(
    session, trainiert, tmp_path
):
    regen, _, _, merkmale, _ = trainiert
    args = {
        "root": tmp_path,
        "train_start": merkmale.index[0].to_pydatetime(),
        "train_end": merkmale.index[-1].to_pydatetime(),
    }
    alt = save_rain_model(session, regen, **args)
    neu = save_rain_model(session, regen, **args)
    session.flush()

    promote(session, alt.id)
    session.flush()
    assert active_model(session, "rain", 6).id == alt.id

    promote(session, neu.id)
    session.flush()
    assert active_model(session, "rain", 6).id == neu.id
    stati = {m.id: m.status for m in list_models(session, target="rain")}
    assert stati[alt.id] == "retired"


def test_fehlende_merkmale_werden_gemeldet(session, trainiert, tmp_path):
    """Wichtig, sobald ein Sensor ausfällt: das Modell liefert klaglos weiter Zahlen."""
    regen, _, _, merkmale, _ = trainiert
    ref = save_rain_model(
        session,
        regen,
        root=tmp_path,
        train_start=merkmale.index[0].to_pydatetime(),
        train_end=merkmale.index[-1].to_pydatetime(),
    )
    ohne_druck = [c for c in merkmale.columns if not c.startswith("pressure")]
    fehlend = missing_features(ref, ohne_druck)
    assert any(f.startswith("pressure") for f in fehlend)
    assert missing_features(ref, list(merkmale.columns)) == []


# --- Vorhersage und Verifikation --------------------------------------------


def test_vorgeschichte_reicht_fuer_das_laengste_merkmal():
    """Die geladene Vorgeschichte muss den Deckel von hours_since_rain abdecken.

    Sonst kann das Merkmal bei der Inferenz nie so hoch steigen wie im Training,
    und dasselbe Merkmal bedeutet auf beiden Seiten etwas anderes. Beim Regenmodell
    ist es das wichtigste überhaupt.
    """
    from wetter.features.build import MAX_DRY_HOURS, TENDENCY_HOURS
    from wetter.worker.forecast import HISTORY_HOURS

    assert HISTORY_HOURS > MAX_DRY_HOURS
    assert max(TENDENCY_HOURS) < HISTORY_HOURS


def test_vorhersagen_werden_ausgestellt(session, gefuellt, trainiert, tmp_path):
    regen, temp, clim, merkmale, _ = trainiert
    args = {
        "root": tmp_path,
        "train_start": merkmale.index[0].to_pydatetime(),
        "train_end": merkmale.index[-1].to_pydatetime(),
    }
    r = save_rain_model(session, regen, **args)
    t = save_temp_model(session, temp, **args)
    promote(session, r.id)
    promote(session, t.id)
    session.flush()

    ausgestellt = START + timedelta(hours=STUNDEN - 100)
    anzahl, hinweise = run_forecasts(
        session, gefuellt, climatology=clim, issued_at=ausgestellt
    )
    session.flush()

    assert anzahl == 2
    # Ohne hinterlegte Bias-Korrektur meldet die Vorhersage das -- und das soll sie:
    # die Modelle sind auf DWD-Werten trainiert, die Station liefert ihre Rohwerte.
    assert any("Bias-Korrektur" in h for h in hinweise)
    assert not [h for h in hinweise if "Merkmale fehlen" in h]

    zeilen = session.scalars(select(Forecast).order_by(Forecast.target)).all()
    nach_ziel = {z.target: z for z in zeilen}

    # Regen ist eine Wahrscheinlichkeit.
    assert 0.0 <= nach_ziel["rain"].value <= 1.0
    # Temperatur kommt mit Band, und das Band muss die Mitte einschliessen.
    q = nach_ziel["temperature"].quantiles
    assert float(q["0.1"]) <= float(q["0.5"]) <= float(q["0.9"])
    # Gültigkeit liegt genau die Vorlaufzeit später.
    assert nach_ziel["rain"].valid_at - nach_ziel["rain"].issued_at == timedelta(hours=6)


def test_zwischengespeichertes_modell_liefert_dieselbe_vorhersage(
    session, trainiert, tmp_path
):
    """Der Zwischenspeicher darf das Ergebnis nicht verändern."""
    regen, _, _, merkmale, _ = trainiert
    ref = save_rain_model(
        session,
        regen,
        root=tmp_path,
        train_start=merkmale.index[0].to_pydatetime(),
        train_end=merkmale.index[-1].to_pydatetime(),
    )
    session.flush()

    eingabe = merkmale.iloc[-30:]
    erste = load_rain_model(session, ref.id).predict(eingabe)
    zweite = load_rain_model(session, ref.id).predict(eingabe)
    np.testing.assert_allclose(erste, zweite)

    # Nach dem Leeren muss dasselbe herauskommen -- also wirklich von der Platte.
    clear_cache()
    np.testing.assert_allclose(load_rain_model(session, ref.id).predict(eingabe), erste)


def test_erneuter_lauf_erzeugt_keine_dublette(session, gefuellt, trainiert, tmp_path):
    regen, _, clim, merkmale, _ = trainiert
    r = save_rain_model(
        session,
        regen,
        root=tmp_path,
        train_start=merkmale.index[0].to_pydatetime(),
        train_end=merkmale.index[-1].to_pydatetime(),
    )
    promote(session, r.id)
    session.flush()

    ausgestellt = START + timedelta(hours=STUNDEN - 100)
    for _ in range(3):
        run_forecasts(session, gefuellt, climatology=clim, issued_at=ausgestellt)
        session.flush()

    assert session.scalar(select(func.count()).select_from(Forecast)) == 1


def test_verifikation_bewertet_abgelaufene_vorhersagen(
    session, gefuellt, trainiert, tmp_path
):
    regen, temp, clim, merkmale, _ = trainiert
    args = {
        "root": tmp_path,
        "train_start": merkmale.index[0].to_pydatetime(),
        "train_end": merkmale.index[-1].to_pydatetime(),
    }
    promote(session, save_rain_model(session, regen, **args).id)
    promote(session, save_temp_model(session, temp, **args).id)
    session.flush()

    ausgestellt = START + timedelta(hours=STUNDEN - 100)
    run_forecasts(session, gefuellt, climatology=clim, issued_at=ausgestellt)
    session.flush()

    # Zum Zeitpunkt der Ausstellung ist noch nichts zu bewerten.
    assert verify_pending(session, now=ausgestellt) == 0

    # Sechs Stunden später schon.
    anzahl = verify_pending(session, now=ausgestellt + timedelta(hours=7))
    session.flush()
    assert anzahl == 2

    bewertungen = session.scalars(select(Verification)).all()
    nach_ziel = {}
    for b in bewertungen:
        f = session.get(Forecast, b.forecast_id)
        nach_ziel[f.target] = b

    assert "brier" in nach_ziel["rain"].scores
    assert nach_ziel["rain"].observed in (0.0, 1.0)
    assert "absolute_error" in nach_ziel["temperature"].scores
    assert "pinball_0.5" in nach_ziel["temperature"].scores


def test_verifikation_wartet_auf_fehlende_messung(
    session, gefuellt, trainiert, tmp_path
):
    """Eine Lücke darf nicht als kein Regen durchgehen.

    Ausgerechnet bei Starkregen fällt eine Station gern aus -- die Lücke als trocken
    zu werten, rechnete dem Modell eine zu gute Trefferquote an.
    """
    regen, _, clim, merkmale, _ = trainiert
    promote(
        session,
        save_rain_model(
            session,
            regen,
            root=tmp_path,
            train_start=merkmale.index[0].to_pydatetime(),
            train_end=merkmale.index[-1].to_pydatetime(),
        ).id,
    )
    session.flush()

    # Ausstellung so spät, dass das Zielfenster über das Ende der Daten hinausreicht.
    ausgestellt = START + timedelta(hours=STUNDEN - 3)
    run_forecasts(session, gefuellt, climatology=clim, issued_at=ausgestellt)
    session.flush()

    assert verify_pending(session, now=ausgestellt + timedelta(hours=10)) == 0
    assert session.scalar(select(func.count()).select_from(Verification)) == 0


def test_beobachteter_regen_prueft_das_ganze_fenster(session, gefuellt):
    """Nicht die Zielstunde zählt, sondern ob es irgendwann im Fenster regnete."""
    start = START + timedelta(hours=100)
    ende = start + timedelta(hours=6)
    beobachtet = observed_rain(session, gefuellt.id, start, ende)
    assert beobachtet in (0.0, 1.0)

    # Gegenprobe von Hand über dieselben Stunden.
    summe = session.scalar(
        select(func.max(Hourly.precip_mm)).where(
            Hourly.station_id == gefuellt.id,
            Hourly.time > start,
            Hourly.time <= ende,
        )
    )
    assert beobachtet == (1.0 if summe >= 0.1 else 0.0)


# --- Bewertungslogik ---------------------------------------------------------


def test_brier_score_einer_perfekten_vorhersage():
    assert score_forecast("rain", 1.0, None, 1.0)["brier"] == pytest.approx(0.0)
    assert score_forecast("rain", 0.0, None, 0.0)["brier"] == pytest.approx(0.0)


def test_brier_score_einer_voellig_falschen_vorhersage():
    assert score_forecast("rain", 1.0, None, 0.0)["brier"] == pytest.approx(1.0)


def test_band_treffer_wird_vermerkt():
    q = {"0.1": 8.0, "0.5": 10.0, "0.9": 12.0}
    assert score_forecast("temperature", 10.0, q, 11.0)["in_band"] == 1.0
    assert score_forecast("temperature", 10.0, q, 15.0)["in_band"] == 0.0


def test_pinball_bestraft_asymmetrisch():
    """Das untere Quantil darf zu tief liegen, aber nicht zu hoch."""
    q = {"0.1": 8.0}
    zu_tief = score_forecast("temperature", 10.0, q, 9.0)["pinball_0.1"]
    zu_hoch = score_forecast("temperature", 10.0, q, 7.0)["pinball_0.1"]
    assert zu_hoch > zu_tief
