"""Datenbankschema.

Liegt im Postgres-Schema ``wetter`` und gehört Alembic. Die PWA bekommt ein eigenes
Schema ``app`` unter Prisma und liest ``wetter`` nur -- so kollidieren nicht zwei
Migrationssysteme auf denselben Tabellen.

Zum Datenvolumen: bei Speicherung im Minutentakt fallen rund 525.000 Zeilen und etwa
100 MB im Jahr an. Das ist für normales Postgres unauffällig; ein BRIN-Index auf der
Zeitspalte reicht, weil die Daten ohnehin zeitlich sortiert eintreffen. TimescaleDB
wäre hier eine zusätzliche Abhängigkeit ohne Gegenwert.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "wetter"

#: Feste Namenskonventionen, damit Alembic stabile Constraint-Namen erzeugt und
#: Migrationen nicht bei jedem Lauf anders heißen.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING)


def _ts(**kw) -> Mapped[datetime]:
    """Zeitstempel immer mit Zeitzone -- alles Interne läuft in UTC."""
    return mapped_column(DateTime(timezone=True), **kw)


class Station(Base):
    """Die eigene Wetterstation. Mehrere sind vorgesehen, aber selten nötig."""

    __tablename__ = "station"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    altitude_m: Mapped[float] = mapped_column(Float)
    """Höhe über Meeresniveau. Ohne sie lässt sich der Druck nicht reduzieren und
    die Daten sind mit dem DWD nicht vergleichbar."""

    created_at: Mapped[datetime] = _ts()

    sensor_states: Mapped[list[SensorStateRow]] = relationship(back_populates="station")


class SensorStateRow(Base):
    """Historie der Sensorzustände.

    Ein Zeitraum je Sensor und Zustand. ``valid_to`` bleibt leer, solange der Zustand
    gilt. Nachträgliche Korrekturen sind ausdrücklich vorgesehen -- wer beim Basteln
    vergisst, den Testmodus einzuschalten, markiert den Zeitraum hinterher.
    """

    __tablename__ = "sensor_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.station.id"))
    sensor_key: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16))
    valid_from: Mapped[datetime] = _ts()
    valid_to: Mapped[datetime | None] = _ts(nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    station: Mapped[Station] = relationship(back_populates="sensor_states")

    __table_args__ = (
        Index("ix_sensor_state_lookup", "station_id", "sensor_key", "valid_from"),
    )


class Measurement(Base):
    """Rohmessungen der eigenen Station, eine Zeile je Zeitpunkt.

    Breite Tabelle mit einer nullbaren Spalte je Messgröße statt eines
    Schlüssel-Wert-Modells: die Abfragen sind einfacher, der Speicherbedarf kleiner,
    und ein neuer Sensor ist eine Migration statt eines Sonderfalls im Code.

    Wichtig: ``NULL`` heißt hier *nicht gemessen*. Ob ein vorhandener Wert ins
    Training darf, entscheidet allein :class:`SensorStateRow` -- deshalb wird der
    Zustand nicht in diese Tabelle kopiert, sonst ließe er sich nicht nachträglich
    korrigieren.
    """

    __tablename__ = "measurement"

    station_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.station.id"), primary_key=True
    )
    time: Mapped[datetime] = _ts(primary_key=True)
    seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    """Fortlaufende Nummer der Station -- macht Lücken im Ringpuffer sichtbar."""

    # BME280
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure_station_hpa: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Wind
    wind_speed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_gust_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_dir_deg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Niederschlag
    precip_mm: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Zusatzsensoren
    sky_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    lightning_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    lightning_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    illuminance_lux: Mapped[float | None] = mapped_column(Float, nullable=True)
    panel_voltage_v: Mapped[float | None] = mapped_column(Float, nullable=True)
    panel_current_ma: Mapped[float | None] = mapped_column(Float, nullable=True)
    global_radiation_wm2: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Zustand der Station selbst
    battery_v: Mapped[float | None] = mapped_column(Float, nullable=True)
    rssi_dbm: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    power_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        # BRIN statt B-Tree: die Daten treffen zeitlich sortiert ein, damit reicht
        # ein Index, der nur Blockbereiche merkt -- Bruchteil der Größe.
        Index(
            "ix_measurement_time_brin",
            "time",
            postgresql_using="brin",
            postgresql_with={"pages_per_range": 64},
        ),
    )


class Hourly(Base):
    """Stundenwerte der eigenen Station, im selben Schema wie die DWD-Daten.

    Vorhersagen und Merkmale arbeiten ausschließlich auf dieser Tabelle, nie auf den
    Rohmessungen -- so ist der Weg für eigene und DWD-Daten wirklich derselbe.
    """

    __tablename__ = "hourly"

    station_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.station.id"), primary_key=True
    )
    time: Mapped[datetime] = _ts(primary_key=True)

    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure_station_hpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure_sea_hpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    dewpoint_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_gust_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_dir_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    precip_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    cloud_cover_okta: Mapped[float | None] = mapped_column(Float, nullable=True)
    sky_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    global_radiation_wm2: Mapped[float | None] = mapped_column(Float, nullable=True)
    illuminance_lux: Mapped[float | None] = mapped_column(Float, nullable=True)
    lightning_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lightning_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)

    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    """Wieviele Rohmessungen in die Stunde eingingen -- eine Stunde aus zwei Werten
    ist etwas anderes als eine aus 360."""

    __table_args__ = (
        Index("ix_hourly_time_brin", "time", postgresql_using="brin"),
    )


class DwdStation(Base):
    """Die je Messgröße gewählte DWD-Referenzstation."""

    __tablename__ = "dwd_station"

    dataset_key: Mapped[str] = mapped_column(String(4), primary_key=True)
    station_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    altitude_m: Mapped[float] = mapped_column(Float)
    distance_km: Mapped[float] = mapped_column(Float)
    imported_at: Mapped[datetime] = _ts()
    first_time: Mapped[datetime | None] = _ts(nullable=True)
    last_time: Mapped[datetime | None] = _ts(nullable=True)


class DwdHourly(Base):
    """Importierte DWD-Stundenwerte, bereits zusammengeführt.

    Kein Stationsbezug in der Zeile: die Zusammenführung über mehrere Stationen ist
    beim Import schon passiert, welche Station welche Spalte lieferte, steht in
    :class:`DwdStation`.
    """

    __tablename__ = "dwd_hourly"

    time: Mapped[datetime] = _ts(primary_key=True)

    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure_sea_hpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure_station_hpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    dewpoint_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    precip_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    precip_indicator: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_dir_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_gust_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cloud_cover_okta: Mapped[float | None] = mapped_column(Float, nullable=True)
    sunshine_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    visibility_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    global_radiation_jcm2: Mapped[float | None] = mapped_column(Float, nullable=True)
    diffuse_radiation_jcm2: Mapped[float | None] = mapped_column(Float, nullable=True)
    atmo_radiation_jcm2: Mapped[float | None] = mapped_column(Float, nullable=True)
    vapour_pressure_hpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    wetbulb_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    abs_humidity_gm3: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_dwd_hourly_time_brin", "time", postgresql_using="brin"),
    )


class Model(Base):
    """Modell-Register.

    Kommt ein Sensor neu dazu, wird nicht das laufende Modell umgebaut. Stattdessen
    läuft ein neues mit erweitertem Merkmalssatz erst im Schattenbetrieb mit und löst
    das aktive erst ab, wenn die Verifikation über ein gleitendes Fenster zeigt, dass
    es tatsächlich besser ist.
    """

    __tablename__ = "model"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    """z.B. lightgbm_binary, lightgbm_quantile, analog_knn, baseline_zambretti."""

    target: Mapped[str] = mapped_column(String(32))
    """rain, temperature, gust."""

    lead_hours: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="shadow")
    """shadow, active oder retired."""

    feature_names: Mapped[list] = mapped_column(JSONB)
    """Der Merkmalssatz, mit dem trainiert wurde -- sonst verschiebt ein später
    dazugekommener Sensor stillschweigend die Spaltenreihenfolge."""

    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    trained_at: Mapped[datetime] = _ts()
    train_start: Mapped[datetime] = _ts()
    train_end: Mapped[datetime] = _ts()
    source: Mapped[str] = mapped_column(String(16), default="dwd")
    """dwd, own oder mixed -- woher die Trainingsdaten kamen."""

    artifact_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_model_lookup", "target", "lead_hours", "status"),
    )


class Forecast(Base):
    """Eine abgegebene Vorhersage.

    Wird immer gespeichert, auch wenn sie nie jemand ansieht -- ohne die Ablage
    lässt sich später nicht mehr bewerten, ob das Modell recht hatte.
    """

    __tablename__ = "forecast"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.model.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.station.id"))
    issued_at: Mapped[datetime] = _ts()
    valid_at: Mapped[datetime] = _ts()
    target: Mapped[str] = mapped_column(String(32))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantiles: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("model_id", "issued_at", "valid_at", "target"),
        Index("ix_forecast_valid", "valid_at", "target"),
    )


class Verification(Base):
    """Die Auflösung: was tatsächlich eingetreten ist, und wie gut die Vorhersage war."""

    __tablename__ = "verification"

    forecast_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.forecast.id"), primary_key=True
    )
    verified_at: Mapped[datetime] = _ts()
    observed: Mapped[float | None] = mapped_column(Float, nullable=True)
    scores: Mapped[dict] = mapped_column(JSONB, default=dict)
    """brier, absolute_error, pinball je Quantil -- was zum Ziel passt."""
