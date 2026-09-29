#!/usr/bin/env bash
# Bastion — restaura um backup feito pelo backup.sh.
#
#   sudo bash /opt/bastion/deploy/restore.sh /opt/bastion-backups/bastion-AAAAMMDD-HHMMSS.tar.gz
#
# Substitui o banco atual pelo do backup. O deploy/.env só é trocado se não existir (servidor novo)
# ou com --com-env. Sem o JWT_SECRET do backup, as senhas dos equipamentos não abrem.
set -euo pipefail
DIR="${DIR:-/opt/bastion}"
FILE="${1:-}"
COM_ENV=0
[ "${2:-}" = "--com-env" ] && COM_ENV=1
[ -f "$FILE" ] || { echo "uso: $0 arquivo.tar.gz [--com-env]"; exit 1; }

umask 077
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
tar xzf "$FILE" -C "$TMP"
[ -s "$TMP/mongo.archive.gz" ] || { echo "!! backup sem mongo.archive.gz"; exit 1; }
echo ">> Backup:"; sed 's/^/   /' "$TMP/VERSION" 2>/dev/null || true

cd "$DIR/deploy"
if [ ! -f .env ] || [ "$COM_ENV" = 1 ]; then
  [ -f .env ] && cp .env ".env.antes-restore-$(date +%Y%m%d-%H%M)"
  cp "$TMP/env" .env
  echo ">> .env restaurado do backup"
else
  a="$(grep -E '^JWT_SECRET=' .env | tail -1)"; b="$(grep -E '^JWT_SECRET=' "$TMP/env" | tail -1)"
  if [ "$a" != "$b" ]; then
    echo "!! O JWT_SECRET do .env atual é diferente do backup: as senhas dos equipamentos não vão abrir."
    echo "   Rode de novo com --com-env para usar o .env do backup."
  fi
fi

read -r -p ">> Isto APAGA o banco atual e coloca o do backup. Digite RESTAURAR para seguir: " ok
[ "$ok" = "RESTAURAR" ] || { echo "cancelado"; exit 1; }

echo ">> Parando backend/coletor/VPN (o Mongo continua)…"
docker compose up -d mongo
docker compose stop backend flow vpn 2>/dev/null || true
for i in $(seq 1 30); do
  docker compose exec -T mongo mongosh --quiet --eval 'db.adminCommand("ping").ok' >/dev/null 2>&1 && break
  sleep 2
done

echo ">> Restaurando banco…"
docker compose exec -T mongo mongorestore --quiet --drop --archive --gzip < "$TMP/mongo.archive.gz"

if [ -s "$TMP/caddy_data.tgz" ]; then
  VOL="$(docker volume ls -q | grep -E '(^|_)caddy_data$' | head -1 || true)"
  if [ -n "$VOL" ]; then
    echo ">> Certificados do Caddy…"
    docker compose stop frontend 2>/dev/null || true
    docker run --rm -i -v "$VOL":/d alpine sh -c 'tar xzf - -C /d' < "$TMP/caddy_data.tgz" || echo "   (falhou — o Caddy emite de novo)"
  fi
fi

echo ">> Subindo tudo…"
docker compose up -d
echo "OK: restaurado de $FILE"
