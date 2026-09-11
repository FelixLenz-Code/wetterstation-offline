#!/usr/bin/env bash
# Wetterstation einrichten, aktualisieren und sichern.
#
# Nach demselben Muster wie deine übrigen Projekte: ein Skript, das den Stack
# hochzieht, Passwörter selbst erzeugt und die üblichen Unterbefehle kennt.
#
#   ./install.sh              einrichten und starten
#   ./install.sh update       neue Images holen und neu starten
#   ./install.sh status       Zustand der Dienste
#   ./install.sh logs [dienst]
#   ./install.sh backup       Datenbank und Modelle sichern
#   ./install.sh restore DATEI
#   ./install.sh station      Zugangsdaten für die ESP32-Firmware anzeigen
#   ./install.sh uninstall    alles entfernen (fragt nach)

set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HIER"

ENV_DATEI=".env"
SICHERUNG="backups"

rot=$'\e[31m'; gruen=$'\e[32m'; gelb=$'\e[33m'; aus=$'\e[0m'
info() { printf '%s\n' "$*"; }
gut()  { printf '%s%s%s\n' "$gruen" "$*" "$aus"; }
warn() { printf '%s%s%s\n' "$gelb" "$*" "$aus"; }
fehler() { printf '%s%s%s\n' "$rot" "$*" "$aus" >&2; exit 1; }

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    fehler "Docker Compose nicht gefunden."
  fi
}

pruefe_docker() {
  command -v docker >/dev/null 2>&1 || fehler "Docker ist nicht installiert."
  docker info >/dev/null 2>&1 || fehler "Docker läuft nicht oder du darfst es nicht bedienen (Gruppe docker?)."
}

passwort() {
  # openssl ist praktisch überall da; /dev/urandom als Rückfallebene.
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 24 | tr -d '/+=' | head -c 24
  else
    tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24
  fi
}

freier_port() {
  # Sucht ab dem übergebenen Port den ersten freien. Auf diesem Rechner sind
  # 3000 (open-webui), 5432, 5433 und 55432 schon belegt -- ein fester Standard
  # würde hier also direkt scheitern.
  local port="$1"
  while ss -ltn 2>/dev/null | grep -q ":${port} "; do
    port=$((port + 1))
  done
  printf '%s' "$port"
}

env_anlegen() {
  [ -f "$ENV_DATEI" ] && return 0

  info "Lege $ENV_DATEI an und erzeuge Passwörter ..."
  local app_port db_port
  app_port="$(freier_port 3000)"
  db_port="$(freier_port 55432)"
  [ "$app_port" != "3000" ] && warn "Port 3000 ist belegt, nehme $app_port."

  cat > "$ENV_DATEI" <<EOF
# Erzeugt von install.sh am $(date -Iseconds)
POSTGRES_PASSWORD=$(passwort)
MQTT_PASSWORD=$(passwort)

APP_PORT=$app_port
BIND_ADDR=127.0.0.1

MQTT_BIND=0.0.0.0
MQTT_PORT=1883
MQTT_USER=station

DB_PORT=$db_port

TZ=$(cat /etc/timezone 2>/dev/null || echo Europe/Berlin)
EOF
  chmod 600 "$ENV_DATEI"
  gut "$ENV_DATEI angelegt."
}

mqtt_passwort_datei() {
  # Mosquitto liest die Zugangsdaten aus einer eigenen Datei mit gehashtem
  # Passwort. Sie wird aus der .env erzeugt, damit es nur eine Quelle gibt.
  # shellcheck disable=SC1090
  source "$ENV_DATEI"
  mkdir -p mosquitto
  local datei="mosquitto/passwd"
  printf '%s:%s\n' "${MQTT_USER:-station}" "$MQTT_PASSWORD" > "$datei"
  # mosquitto_passwd ersetzt das Klartextpasswort durch seinen Hash.
  docker run --rm -v "$HIER/mosquitto:/m" eclipse-mosquitto:2 \
    mosquitto_passwd -U /m/passwd >/dev/null 2>&1 \
    || fehler "Konnte die MQTT-Passwortdatei nicht erzeugen."
  chmod 600 "$datei"
}

einrichten() {
  pruefe_docker
  env_anlegen
  mqtt_passwort_datei

  info "Starte den Stack ..."
  compose up -d --remove-orphans

  info "Warte auf die Datenbank ..."
  local i
  for i in $(seq 1 60); do
    if compose exec -T db pg_isready -q 2>/dev/null; then break; fi
    sleep 1
  done

  info "Wende Datenbankmigrationen an ..."
  compose run --rm --entrypoint alembic worker upgrade head

  # shellcheck disable=SC1090
  source "$ENV_DATEI"
  echo
  gut "Fertig."
  info "Weboberfläche:  http://${BIND_ADDR}:${APP_PORT}"
  info "MQTT-Broker:    ${MQTT_BIND}:${MQTT_PORT} (Benutzer ${MQTT_USER})"
  echo
  info "Nächster Schritt: die Zugangsdaten in die Firmware eintragen."
  info "  ./install.sh station"
}

station_daten() {
  # shellcheck disable=SC1090
  source "$ENV_DATEI"
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')"
  cat <<EOF

Zugangsdaten für die ESP32-Firmware (firmware/station/main/config.h):

  #define MQTT_HOST     "${ip:-<IP-des-Servers>}"
  #define MQTT_PORT     ${MQTT_PORT}
  #define MQTT_USER     "${MQTT_USER}"
  #define MQTT_PASSWORD "${MQTT_PASSWORD}"
  #define STATION_KEY   "garten"

Das Passwort steht auch in .env. Weitergeben brauchst du es nur an die Station.

EOF
}

aktualisieren() {
  pruefe_docker
  info "Hole neue Images ..."
  compose pull
  compose up -d --remove-orphans
  info "Wende Migrationen an ..."
  compose run --rm --entrypoint alembic worker upgrade head
  gut "Aktualisiert."
}

zustand() {
  compose ps
  echo
  # shellcheck disable=SC1090
  source "$ENV_DATEI" 2>/dev/null || true
  if compose exec -T db pg_isready -q 2>/dev/null; then
    info "Datenbankinhalt:"
    compose exec -T db psql -U "${POSTGRES_USER:-wetter}" -d "${POSTGRES_DB:-wetter}" -tAc "
      SELECT '  Messwerte:     '||count(*) FROM wetter.measurement
      UNION ALL SELECT '  Stundenwerte:  '||count(*) FROM wetter.hourly
      UNION ALL SELECT '  DWD-Stunden:   '||count(*) FROM wetter.dwd_hourly
      UNION ALL SELECT '  Modelle aktiv: '||count(*) FROM wetter.model WHERE status='active'
      UNION ALL SELECT '  Vorhersagen:   '||count(*) FROM wetter.forecast
      UNION ALL SELECT '  bewertet:      '||count(*) FROM wetter.verification" 2>/dev/null \
      || warn "  (Schema noch nicht angelegt)"
  fi
}

sichern() {
  pruefe_docker
  # shellcheck disable=SC1090
  source "$ENV_DATEI"
  mkdir -p "$SICHERUNG"
  local stempel ziel
  stempel="$(date +%Y%m%d-%H%M%S)"
  ziel="$SICHERUNG/wetter-$stempel.tar.gz"

  info "Sichere Datenbank und Modelle ..."
  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN

  compose exec -T db pg_dump -U "${POSTGRES_USER:-wetter}" -d "${POSTGRES_DB:-wetter}" \
    > "$tmp/datenbank.sql"
  # Die Modelldateien liegen im Volume und gehören dazu -- ohne sie müsste nach
  # dem Zurückspielen alles neu trainiert werden.
  docker run --rm -v wetterstation_model-data:/m -v "$tmp:/out" alpine \
    tar czf /out/modelle.tar.gz -C /m . 2>/dev/null || true
  cp "$ENV_DATEI" "$tmp/env"

  tar czf "$ziel" -C "$tmp" .
  chmod 600 "$ziel"
  gut "Gesichert nach $ziel ($(du -h "$ziel" | cut -f1))"
}

zurueckspielen() {
  local datei="${1:-}"
  [ -f "$datei" ] || fehler "Sicherungsdatei angeben: ./install.sh restore backups/wetter-....tar.gz"
  pruefe_docker
  warn "Das überschreibt die vorhandene Datenbank."
  read -r -p "Wirklich fortfahren? [ja/NEIN] " antwort
  [ "$antwort" = "ja" ] || { info "Abgebrochen."; return 0; }

  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  tar xzf "$datei" -C "$tmp"

  # shellcheck disable=SC1090
  source "$ENV_DATEI"
  compose up -d db
  sleep 5
  compose exec -T db psql -U "${POSTGRES_USER:-wetter}" -d postgres \
    -c "DROP DATABASE IF EXISTS ${POSTGRES_DB:-wetter}" \
    -c "CREATE DATABASE ${POSTGRES_DB:-wetter}"
  compose exec -T db psql -U "${POSTGRES_USER:-wetter}" -d "${POSTGRES_DB:-wetter}" \
    < "$tmp/datenbank.sql" >/dev/null

  if [ -f "$tmp/modelle.tar.gz" ]; then
    docker run --rm -v wetterstation_model-data:/m -v "$tmp:/in" alpine \
      sh -c 'rm -rf /m/* && tar xzf /in/modelle.tar.gz -C /m'
  fi
  compose up -d
  gut "Zurückgespielt."
}

entfernen() {
  warn "Das entfernt alle Container, Volumes und damit sämtliche Messdaten."
  read -r -p "Wirklich alles löschen? [ja/NEIN] " antwort
  [ "$antwort" = "ja" ] || { info "Abgebrochen."; return 0; }
  compose down -v --remove-orphans
  gut "Entfernt. Die Dateien .env und backups/ sind absichtlich geblieben."
}

case "${1:-install}" in
  install|"")   einrichten ;;
  update)       aktualisieren ;;
  status)       zustand ;;
  logs)         shift; compose logs -f "$@" ;;
  backup)       sichern ;;
  restore)      shift; zurueckspielen "$@" ;;
  station)      station_daten ;;
  uninstall)    entfernen ;;
  *)            fehler "Unbekannter Befehl: $1 (install, update, status, logs, backup, restore, station, uninstall)" ;;
esac
