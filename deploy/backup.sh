#!/usr/bin/env bash
# Bastion — backup completo: banco (Mongo), deploy/.env (tem o JWT_SECRET que criptografa as senhas
# dos equipamentos), certificados do Caddy e a versão do código.
#
#   sudo bash /opt/bastion/deploy/backup.sh              # tudo
#   sudo bash /opt/bastion/deploy/backup.sh --sem-flow   # sem o histórico de flow (arquivo bem menor)
#
# Gera /opt/bastion-backups/bastion-AAAAMMDD-HHMMSS.tar.gz (só root lê) e mantém os últimos $KEEP.
# Restaurar: sudo bash /opt/bastion/deploy/restore.sh /opt/bastion-backups/bastion-....tar.gz
set -euo pipefail
DIR="${DIR:-/opt/bastion}"
OUT="${OUT:-/opt/bastion-backups}"
KEEP="${KEEP:-14}"
SEM_FLOW=0
[ "${1:-}" = "--sem-flow" ] && SEM_FLOW=1

cd "$DIR/deploy"
[ -f .env ] || { echo "!! $DIR/deploy/.env não encontrado"; exit 1; }
DB_NAME="$(grep -E '^DB_NAME=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')"
DB_NAME="${DB_NAME:-bastion}"

umask 077
mkdir -p "$OUT"
chmod 700 "$OUT"
STAMP="$(date +%Y%m%d-%H%M%S)"
TMP="$(mktemp -d "$OUT/.tmp-$STAMP-XXXX")"
trap 'rm -rf "$TMP"' EXIT

echo ">> Banco $DB_NAME…"
EXTRA=()
[ "$SEM_FLOW" = 1 ] && EXTRA=(--excludeCollectionsWithPrefix=flow_) && echo "   (sem as coleções flow_*)"
docker compose exec -T mongo mongodump --quiet --db "$DB_NAME" --archive --gzip "${EXTRA[@]}" > "$TMP/mongo.archive.gz"
[ -s "$TMP/mongo.archive.gz" ] || { echo "!! mongodump não gerou nada"; exit 1; }

echo ">> Configuração (.env)…"
cp .env "$TMP/env"

echo ">> Certificados do Caddy…"
VOL="$(docker volume ls -q | grep -E '(^|_)caddy_data$' | head -1 || true)"
if [ -n "$VOL" ]; then
  docker run --rm -v "$VOL":/d:ro alpine tar czf - -C /d . > "$TMP/caddy_data.tgz" 2>/dev/null \
    || { echo "   (não consegui copiar — o Caddy emite de novo sozinho)"; rm -f "$TMP/caddy_data.tgz"; }
else
  echo "   (volume não encontrado — pulando)"
fi

{
  echo "data=$(date -Is)"
  echo "host=$(hostname)"
  echo "db=$DB_NAME"
  echo "sem_flow=$SEM_FLOW"
  echo "commit=$(git -C "$DIR" rev-parse HEAD 2>/dev/null || echo desconhecido)"
  echo "commit_msg=$(git -C "$DIR" log -1 --format=%s 2>/dev/null || true)"
} > "$TMP/VERSION"

FILE="$OUT/bastion-$STAMP.tar.gz"
tar czf "$FILE" -C "$TMP" .
chmod 600 "$FILE"
echo "OK: $FILE ($(du -h "$FILE" | cut -f1))"

# retenção
ls -1t "$OUT"/bastion-*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
echo "   mantendo os últimos $KEEP em $OUT"
echo "!! O arquivo contém o JWT_SECRET (abre as senhas dos equipamentos). Guarde cópia fora do servidor, em local protegido."
