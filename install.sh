#!/usr/bin/env bash
# WizzOS – installera på Debian/Ubuntu (hemmaserver eller VPS).
#
#   curl -fsSL https://raw.githubusercontent.com/wizz666/wizzos/main/install.sh | sudo bash
#
#   sudo ./install.sh            frågar hemma/VPS
#   sudo ./install.sh --home     alla i nätverket når WizzOS och apparna
#   sudo ./install.sh --vps      bara Tailscale eller SSH-tunnel når dem
#   sudo ./install.sh --dev      bygg från källkoden i den här mappen (för utvecklare)
#
# Kör du skriptet igen uppdateras WizzOS till senaste versionen. Dina appar och
# inställningar i /DATA rörs inte.
set -euo pipefail

REPO="wizz666/wizzos"
RAW="https://raw.githubusercontent.com/$REPO/main"
DEST=/opt/wizzos
PORT="${WIZZOS_PORT:-8899}"
MODE=""
DEV=0
for a in "$@"; do
  case "$a" in
    --home) MODE=home ;;
    --vps) MODE=vps ;;
    --dev) DEV=1 ;;
    *) echo "Okänt val: $a"; exit 1 ;;
  esac
done

say()  { printf '\n\033[1;35m▸ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Kör med sudo, t.ex.: curl -fsSL $RAW/install.sh | sudo bash"
command -v apt-get >/dev/null || fail "Skriptet stöder Debian och Ubuntu."
ARCH="$(dpkg --print-architecture)"
case "$ARCH" in amd64|arm64) ;; *) fail "WizzOS finns för amd64 och arm64 (den här datorn är $ARCH)." ;; esac

# Kör vi från en nedladdad kopia av koden, eller via curl?
SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/docker-compose.yml" ]; then
  SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
[ "$DEV" = 1 ] && [ -z "$SRC" ] && fail "--dev kräver att du kör skriptet från källkodsmappen."

# 1. Docker
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  say "Installerar Docker (officiella skriptet från get.docker.com)"
  apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
command -v curl >/dev/null || { apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null; }

# 2. Hemma eller VPS? (behåll tidigare val vid uppdatering)
if [ -z "$MODE" ] && [ -f "$DEST/.env" ]; then
  # shellcheck disable=SC1091
  LISTEN_IP="$(grep -E '^LISTEN_IP=' "$DEST/.env" | cut -d= -f2-)"
  APPS_BIND_IP="$(grep -E '^APPS_BIND_IP=' "$DEST/.env" | cut -d= -f2-)"
  MODE=keep
fi
if [ -z "$MODE" ]; then
  if [ -t 0 ] || [ -e /dev/tty ]; then
    echo
    echo "Var står servern?"
    echo "  1) Hemma (alla i hemnätverket kan nå den)"
    echo "  2) VPS / på internet (bara du, via Tailscale eller SSH-tunnel)"
    read -rp "Välj 1 eller 2 [1]: " ans </dev/tty || ans=1
    [ "${ans:-1}" = "2" ] && MODE=vps || MODE=home
  else
    MODE=home
  fi
fi

if [ "$MODE" = home ]; then
  LISTEN_IP=0.0.0.0; APPS_BIND_IP=""
elif [ "$MODE" = vps ]; then
  LISTEN_IP=127.0.0.1; APPS_BIND_IP=127.0.0.1
  TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
  if [ -z "$TS_IP" ] && [ -e /dev/tty ]; then
    read -rp "Installera Tailscale så att bara dina enheter når servern? [J/n]: " ts </dev/tty || ts=n
    if [ "${ts:-j}" != "n" ] && [ "${ts:-j}" != "N" ]; then
      curl -fsSL https://tailscale.com/install.sh | sh
      say "Logga in Tailscale – öppna länken som visas"
      tailscale up
      TS_IP="$(tailscale ip -4 | head -n1)"
    fi
  fi
  [ -n "$TS_IP" ] && { LISTEN_IP="$TS_IP"; APPS_BIND_IP="$TS_IP"; }
fi

# 3. Version och filer
mkdir -p "$DEST" /DATA/WizzOS/proxy /DATA/AppData /DATA/Media /DATA/Downloads /DATA/Documents
if [ "$DEV" = 1 ]; then
  VERSION=dev
  say "Bygger WizzOS från källkoden i $SRC"
  docker build -q -t "ghcr.io/$REPO:dev" "$SRC" >/dev/null
  docker build -q -t "ghcr.io/$REPO-proxy:dev" "$SRC/proxy" >/dev/null
  cp "$SRC/docker-compose.yml" "$DEST/docker-compose.yml"
else
  VERSION="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null | grep -m1 '"tag_name"' | sed -E 's/.*"v?([^"]+)".*/\1/' || true)"
  VERSION="${VERSION:-latest}"
  say "Hämtar WizzOS $VERSION"
  if [ -n "$SRC" ]; then cp "$SRC/docker-compose.yml" "$DEST/docker-compose.yml"
  else curl -fsSL "$RAW/docker-compose.yml" -o "$DEST/docker-compose.yml"; fi
fi
TZ_NOW="$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo Europe/Stockholm)"
cat > "$DEST/.env" <<EOF
LISTEN_IP=$LISTEN_IP
APPS_BIND_IP=$APPS_BIND_IP
WIZZOS_PORT=$PORT
WIZZOS_VERSION=$VERSION
WIZZOS_HOST_DIR=$DEST
TZ=$TZ_NOW
EOF

# 4. Starta
cd "$DEST"
[ "$DEV" = 1 ] || docker compose pull -q
docker compose up -d --remove-orphans

# 5. Klart
HOST_IP="$(hostname -I | awk '{print $1}')"
echo
printf '\033[1;32m✓ WizzOS %s är igång!\033[0m\n\n' "$VERSION"
if [ "$LISTEN_IP" = 0.0.0.0 ]; then
  echo "  Öppna:  http://$HOST_IP:$PORT"
elif [ "$LISTEN_IP" = 127.0.0.1 ]; then
  echo "  WizzOS syns bara på servern själv. Från din dator:"
  echo "    ssh -L $PORT:127.0.0.1:$PORT root@$HOST_IP"
  echo "  och öppna sedan  http://localhost:$PORT"
else
  echo "  Öppna (från en enhet med Tailscale):  http://$LISTEN_IP:$PORT"
fi
echo "  Första gången väljer du ett lösenord – sedan guidar WizzOS dig."
echo "  WizzOS håller sig själv uppdaterad (och backar om något går fel)."
echo
