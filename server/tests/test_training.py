"""Tests des nächtlichen Trainingsjobs.

Gearbeitet wird mit einer kurzen Kunstreihe statt mit echten DWD-Daten: der Job soll
hier nicht auf Güte geprüft werden -- das tut die Verifikation im Betrieb --, sondern
darauf, dass die drei Stufen in der richtigen Reihenfolge greifen und neue Modelle
wirklich im Schatten landen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from wetter.db.models import DwdHourly, Hourly, Model, Station
from wetter.models.registry import STATUS_SHADOW
from wetter.worker.training import (
    MIN_OWN_HOURS_FINETUNE,
    OWN_ONLY_FEATURES,
    load_own_hourly,
    needs_training,
    train_for_station,
)

START = datetime(2024, 1, 1, tzinfo=UTC)


def kunstreihe(stunden: int, *, versatz: float = 0.0, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(START, periods=stunden, freq="h", tz="UTC")
    t = np.arange(stunden, dtype=float)
    welle = 8.0 * np.sin(2 * np.pi * t / 120)
    druck = 1013.0 + welle + rng.normal(0, 0.6, stunden)
    tendenz = pd.Series(druck).diff(3).to_numpy()
    neigung = 1.0 / (1.0 + np.exp((tendenz + 1.0) * 1.6))
    return pd.DataFrame(
        {
            "temperature_c": 10.0
            + versatz
            + 6.0 * np.sin(2 * np.pi * (t % 24) / 24)
            + rng.normal(0, 0.8, stunden),
            "humidity_pct": np.clip(65.0 + 15.0 * neigung, 20.0, 100.0),
            "pressure_sea_hpa": druck,
            "pressure_station_hpa": druck - 28.0,
            "precip_mm": np.round((rng.random(stunden) < neigung * 0.5) * 1.2, 2),
            "wind_speed_ms": np.clip(2.5 + 2.0 * neigung, 0.0, None),
            "wind_dir_deg": (180.0 + 80.0 * np.sin(2 * np.pi * t / 120)) % 360,
        },
        index=idx,
    )


@pytest.fixture
def station(session) -> Station:
    s = Station(
        key="t",
        name="T",
        latitude=48.0,
        longitude=7.85,
        altitude_m=237.0,
        created_at=datetime.now(UTC),
    )
    session.add(s)
    session.flush()
    return s


def _dwd_einspielen(session, frame: pd.DataFrame) -> None:
    session.bulk_save_objects(
        [
            DwdHourly(time=zeit.to_pydatetime(), **{k: float(v) for k, v in r.items()})
            for zeit, r in frame.iterrows()
        ]
    )
    session.flush()


def _eigene_einspielen(session, station: Station, frame: pd.DataFrame) -> None:
    session.bulk_save_objects(
        [
            Hourly(
                station_id=station.id,
                time=zeit.to_pydatetime(),
                sample_count=6,
                **{k: float(v) for k, v in r.items()},
            )
            for zeit, r in frame.iterrows()
        ]
    )
    session.flush()


KLEIN = {"rain_leads": (6,), "temp_leads": (6,), "num_boost_round": 30}


def test_ohne_dwd_daten_passiert_nichts(session, station, tmp_path):
    """Ohne Bootstrap gibt es nichts zu trainieren -- und das soll auffallen."""
    bericht = train_for_station(session, station, root=tmp_path, **KLEIN)
    assert bericht.models == []
    assert any("Bootstrap" in g for g in bericht.skipped)


def test_grundmodell_aus_dwd_daten(session, station, tmp_path):
    _dwd_einspielen(session, kunstreihe(3000))
    bericht = train_for_station(session, station, root=tmp_path, **KLEIN)

    assert bericht.dwd_hours == 3000
    assert len(bericht.models) == 2  # ein Regen-, ein Temperaturmodell
    assert not bericht.finetuned

    modelle = session.scalars(select(Model)).all()
    assert {m.source for m in modelle} == {"dwd"}


def test_neue_modelle_landen_im_schatten(session, station, tmp_path):
    """Nie direkt aktiv: ein neueres Modell ist nicht automatisch ein besseres."""
    _dwd_einspielen(session, kunstreihe(3000))
    train_for_station(session, station, root=tmp_path, **KLEIN)
    assert {m.status for m in session.scalars(select(Model)).all()} == {STATUS_SHADOW}


def test_blitzmerkmale_fliegen_beim_dwd_training_raus(session, station, tmp_path):
    """Der DWD kennt keine Blitzdaten -- die Spalten blieben durchgehend leer."""
    _dwd_einspielen(session, kunstreihe(3000))
    train_for_station(session, station, root=tmp_path, **KLEIN)
    modell = session.scalars(select(Model)).first()
    assert not set(OWN_ONLY_FEATURES) & set(modell.feature_names)


def test_wenig_eigene_daten_fuehren_nicht_zum_feintuning(session, station, tmp_path):
    """Unter einem halben Jahr lernte das Modell vor allem, dass gerade Sommer ist."""
    _dwd_einspielen(session, kunstreihe(3000))
    _eigene_einspielen(session, station, kunstreihe(800, versatz=1.5, seed=9))

    bericht = train_for_station(session, station, root=tmp_path, **KLEIN)
    assert not bericht.finetuned
    assert any("Feintuning ab" in g for g in bericht.skipped)


def test_genug_eigene_daten_loesen_feintuning_aus(session, station, tmp_path):
    _dwd_einspielen(session, kunstreihe(9000))
    _eigene_einspielen(
        session, station, kunstreihe(MIN_OWN_HOURS_FINETUNE + 200, versatz=1.5, seed=9)
    )

    bericht = train_for_station(session, station, root=tmp_path, **KLEIN)
    assert bericht.finetuned
    modelle = session.scalars(select(Model)).all()
    assert {m.source for m in modelle} == {"mixed"}


def test_bias_korrektur_wird_gelernt(session, station, tmp_path):
    """Die eigene Reihe liegt systematisch 1,5 K über der DWD-Reihe."""
    _dwd_einspielen(session, kunstreihe(9000))
    _eigene_einspielen(session, station, kunstreihe(5000, versatz=1.5, seed=9))

    bericht = train_for_station(session, station, root=tmp_path, **KLEIN)
    assert "temperature_c" in bericht.bias_columns


def test_eigene_stunden_werden_lueckenlos_gelesen(session, station):
    frame = kunstreihe(200)
    # Zwei Stunden aus der Mitte entfernen -- das Raster muss sie als Lücke führen.
    luecken = frame.drop(frame.index[100:102])
    _eigene_einspielen(session, station, luecken)

    gelesen = load_own_hourly(session, station)
    assert len(gelesen) == 200
    assert gelesen["temperature_c"].isna().sum() == 2


def test_training_ist_faellig_wenn_nie_gelaufen(session):
    assert needs_training(session)


def test_training_ist_nach_einem_lauf_nicht_sofort_wieder_faellig(
    session, station, tmp_path
):
    _dwd_einspielen(session, kunstreihe(3000))
    train_for_station(session, station, root=tmp_path, **KLEIN)
    session.flush()
    assert not needs_training(session)
    # Einen Tag später schon.
    assert needs_training(session, now=datetime.now(UTC) + timedelta(days=1))


def test_modelldateien_liegen_wirklich_auf_der_platte(session, station, tmp_path):
    _dwd_einspielen(session, kunstreihe(3000))
    train_for_station(session, station, root=tmp_path, **KLEIN)

    for modell in session.scalars(select(Model)).all():
        from pathlib import Path

        ordner = Path(modell.artifact_path)
        assert ordner.is_dir()
        assert (ordner / "meta.json").is_file()
        if modell.target == "rain":
            assert (ordner / "booster.txt").is_file()
            assert (ordner / "calibrator.pkl").is_file()
        else:
            assert (ordner / "q050.txt").is_file()
