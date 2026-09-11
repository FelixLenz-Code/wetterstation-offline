"""Warnungen aus den Vorhersagen ableiten und zustellen.

Der Teil, bei dem eine Vorhersage zu einer Handlung führt. Frost, Sturm und
Gewitter sind genau die Lagen, in denen man etwas tun kann -- Pflanzen abdecken,
Markise einfahren, Fenster schliessen. Alles andere ist Information.

Zwei Entwurfsentscheidungen, die den Unterschied zwischen nützlich und lästig
ausmachen:

**Die Schwelle liegt bei der Wahrscheinlichkeit, nicht beim Wert.** Bei Frost
lohnt sich die Warnung schon ab dreissig Prozent -- einmal zu viel die Pflanzen
abzudecken kostet zehn Minuten, einmal zu wenig die Ernte. Bei Sturm ist es
umgekehrt: wer bei jedem böigen Nachmittag gewarnt wird, schaltet die Warnungen
ab und bekommt dann auch die richtige nicht mehr.

**Eine Sperrfrist je Warnungsart.** Ohne sie meldet dieselbe Frostnacht sich
stündlich neu. Die Vorhersage ändert sich ja kaum -- nur die Uhrzeit.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def probability_below(quantiles: dict[str, float], threshold: float) -> float:
    """Schätzt aus wenigen Quantilen, wie wahrscheinlich ein Wert darunter liegt.

    Die Modelle liefern drei Stützstellen -- 10, 50 und 90 Prozent. Innerhalb
    dieser Spanne wird linear zwischen ihnen interpoliert: dort sind die Quantile
    selbst die beste verfügbare Aussage.

    Ausserhalb wird eine Normalverteilung angesetzt, die an der äussersten
    Stützstelle stetig anschliesst. Das ist die wichtigere Hälfte, denn Frost
    liegt fast immer unterhalb des untersten Quantils.

    Warum nicht einfach linear fortschreiben: die Steigung im Inneren ist die des
    *dichten* Teils der Verteilung und viel zu steil für den Ausläufer. Bei einem
    Band von 2 bis 8 Grad wäre die Wahrscheinlichkeit für unter 1 Grad linear
    fortgeschrieben schon null -- obwohl die Vorhersagemitte nur vier Grad
    darüber liegt. Eine Frostwarnung käme damit erst, wenn das ganze Band unter
    null liegt, also viel zu spät.
    """
    if not quantiles:
        return float("nan")

    punkte = sorted((float(q), float(w)) for q, w in quantiles.items())
    qs = np.array([p[0] for p in punkte])
    werte = np.array([p[1] for p in punkte])

    if len(punkte) < 2 or not np.all(np.diff(werte) > 0):
        return float("nan")

    if werte[0] <= threshold <= werte[-1]:
        return float(np.interp(threshold, werte, qs))

    # Ausserhalb: Normalverteilung mit der Mitte als Erwartungswert und einer
    # Streuung, die genau durch die äusserste Stützstelle geht.
    mitte_idx = int(np.argmin(np.abs(qs - 0.5)))
    mu = float(werte[mitte_idx])

    if threshold < werte[0]:
        rand_q, rand_w = float(qs[0]), float(werte[0])
    else:
        rand_q, rand_w = float(qs[-1]), float(werte[-1])

    z_rand = float(_norm_ppf(rand_q))
    if abs(z_rand) < 1e-9 or abs(rand_w - mu) < 1e-12:
        # Die äusserste Stützstelle *ist* die Mitte -- dann gibt es keinen
        # Ausläufer zu beschreiben.
        return float(np.clip(rand_q, 0.0, 1.0))

    sigma = abs(rand_w - mu) / abs(z_rand)
    return float(np.clip(_norm_cdf((threshold - mu) / sigma), 0.0, 1.0))


def _norm_cdf(z: float) -> float:
    """Verteilungsfunktion der Standardnormalverteilung."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Umkehrung dazu, über die Fehlerfunktion.

    Für die drei Quantile, um die es hier geht, genügt die Näherung von
    Acklam bei weitem -- sie liegt über den ganzen Bereich unter 1e-9 daneben,
    und die Quantile selbst sind auf zwei Stellen genau.
    """
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf

    # Rationale Näherung nach Acklam.
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)
    p_low, p_high = 0.02425, 1.0 - 0.02425

    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > p_high:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)

    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


@dataclass
class Alert:
    """Eine ausgelöste Warnung."""

    kind: str
    title: str
    text: str
    probability: float
    valid_at: datetime
    url: str = "/vorhersage"

    def payload(self) -> str:
        return json.dumps(
            {"titel": self.title, "text": self.text, "art": self.kind.lower(),
             "url": self.url},
            ensure_ascii=False,
        )


@dataclass
class Rule:
    kind: str
    enabled: bool
    threshold: float
    min_probability: float
    cooldown_minutes: int
    last_fired_at: datetime | None


def load_rules(session: Session) -> list[Rule]:
    """Liest die Warnungsregeln aus dem Schema der Oberfläche.

    Mit rohem SQL, weil die Tabelle Prisma gehört -- ein SQLAlchemy-Modell wäre
    eine zweite Wahrheit über dieselbe Tabelle.
    """
    zeilen = session.execute(
        text(
            'SELECT kind, enabled, threshold, "minProbability", "cooldownMinutes", '
            '"lastFiredAt" FROM app.alert_rule'
        )
    ).all()
    return [
        Rule(
            kind=z[0],
            enabled=bool(z[1]),
            threshold=float(z[2]),
            min_probability=float(z[3]),
            cooldown_minutes=int(z[4]),
            last_fired_at=z[5],
        )
        for z in zeilen
    ]


def in_cooldown(rule: Rule, now: datetime) -> bool:
    """Läuft die Sperrfrist dieser Warnungsart noch?"""
    if rule.last_fired_at is None:
        return False
    zuletzt = rule.last_fired_at
    if zuletzt.tzinfo is None:
        zuletzt = zuletzt.replace(tzinfo=UTC)
    return now - zuletzt < timedelta(minutes=rule.cooldown_minutes)


def evaluate_frost(
    rule: Rule, forecasts: list[tuple[datetime, dict[str, float]]]
) -> Alert | None:
    """Prüft die Temperaturvorhersagen auf Frost.

    Gewarnt wird vor der *frühesten* Stunde, die die Schwelle reisst, nicht vor
    der kältesten. Wer um 22 Uhr erfährt, dass es um 5 Uhr frieren wird, deckt
    ab; wer erfährt, dass es um 7 Uhr am kältesten wird, hat die entscheidende
    Stunde schon verpasst.
    """
    for gueltig, quantile in sorted(forecasts, key=lambda f: f[0]):
        p = probability_below(quantile, rule.threshold)
        if not np.isfinite(p) or p < rule.min_probability:
            continue
        return Alert(
            kind=rule.kind,
            title="Frostwarnung",
            text=(
                f"{p:.0%} Wahrscheinlichkeit für unter {rule.threshold:.0f} °C "
                f"gegen {gueltig.astimezone().strftime('%H:%M')} Uhr."
            ),
            probability=p,
            valid_at=gueltig,
        )
    return None


def mark_fired(session: Session, kind: str, now: datetime) -> None:
    session.execute(
        text('UPDATE app.alert_rule SET "lastFiredAt" = :jetzt WHERE kind = :kind'),
        {"kind": kind, "jetzt": now},
    )


def subscriptions(session: Session) -> list[dict]:
    """Alle Browser, die Warnungen bekommen möchten."""
    zeilen = session.execute(
        text('SELECT id, endpoint, p256dh, auth, failures FROM app.push_subscription')
    ).all()
    return [
        {
            "id": z[0],
            "subscription_info": {
                "endpoint": z[1],
                "keys": {"p256dh": z[2], "auth": z[3]},
            },
            "failures": int(z[4]),
        }
        for z in zeilen
    ]


#: Nach so vielen Fehlversuchen in Folge wird ein Abo entfernt. Ein abgemeldeter
#: Browser meldet sich nicht ab -- er antwortet nur nicht mehr.
MAX_FAILURES = 5


def send_push(session: Session, alert: Alert, vapid_private_key: str,
              vapid_claim_email: str) -> tuple[int, int]:
    """Stellt eine Warnung an alle angemeldeten Browser zu.

    Gibt zugestellt und fehlgeschlagen zurück. Abos, die dauerhaft scheitern,
    werden entfernt -- ein abgemeldeter Browser meldet sich nicht ab, er
    antwortet nur nicht mehr, und ein Abo aus einem längst gelöschten Profil
    würde sonst ewig mitgeschleppt.
    """
    from pywebpush import WebPushException, webpush

    nutzlast = alert.payload()
    zugestellt = 0
    fehlgeschlagen = 0

    for abo in subscriptions(session):
        try:
            webpush(
                subscription_info=abo["subscription_info"],
                data=nutzlast,
                vapid_private_key=vapid_private_key,
                vapid_claims={"sub": f"mailto:{vapid_claim_email}"},
                ttl=3600,
            )
            zugestellt += 1
            session.execute(
                text(
                    'UPDATE app.push_subscription SET "lastSeen" = :jetzt, '
                    "failures = 0 WHERE id = :id"
                ),
                {"id": abo["id"], "jetzt": datetime.now(UTC)},
            )
        except WebPushException as exc:
            fehlgeschlagen += 1
            code = getattr(exc.response, "status_code", None)
            # 404 und 410 heissen: dieses Abo gibt es nicht mehr. Da hilft kein
            # erneuter Versuch, das ist endgültig.
            endgueltig = code in (404, 410)
            neue_fehler = abo["failures"] + 1

            if endgueltig or neue_fehler >= MAX_FAILURES:
                session.execute(
                    text("DELETE FROM app.push_subscription WHERE id = :id"),
                    {"id": abo["id"]},
                )
                grund = "abgemeldet" if endgueltig else "zu oft gescheitert"
                log.info("Abo entfernt (%s)", grund)
            else:
                session.execute(
                    text("UPDATE app.push_subscription SET failures = :n WHERE id = :id"),
                    {"id": abo["id"], "n": neue_fehler},
                )
        except Exception:
            fehlgeschlagen += 1
            log.exception("Zustellung fehlgeschlagen")

    return zugestellt, fehlgeschlagen
