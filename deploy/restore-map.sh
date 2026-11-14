#!/usr/bin/env bash
# Bastion — traz de volta um MAPA apagado, a partir de um backup do backup.sh, SEM mexer no resto do banco.
#
#   sudo bash /opt/bastion/deploy/restore-map.sh                         # lista os backups
#   sudo bash /opt/bastion/deploy/restore-map.sh BACKUP.tar.gz           # lista os mapas do backup (marca os que sumiram)
#   sudo bash /opt/bastion/deploy/restore-map.sh BACKUP.tar.gz "Nome"    # restaura o mapa (pelo nome ou pelo id)
#
# Só insere mapas que NÃO existem mais no banco atual (não sobrescreve nada).
set -euo pipefail
DIR="${DIR:-/opt/bastion}"
OUT="${OUT:-/opt/bastion-backups}"
FILE="${1:-}"
WANT="${2:-}"

if [ -z "$FILE" ]; then
  echo ">> Backups em $OUT (mais novo primeiro):"
  ls -1t "$OUT"/bastion-*.tar.gz 2>/dev/null | while read -r f; do echo "   $f  ($(date -r "$f" '+%d/%m/%Y %H:%M'))"; done \
    || echo "   nenhum backup encontrado"
  echo; echo "Escolha um backup feito ANTES de apagar o mapa e rode:"; echo "   sudo bash $0 /opt/bastion-backups/bastion-....tar.gz"
  exit 0
fi
[ -f "$FILE" ] || { echo "!! arquivo não encontrado: $FILE"; exit 1; }

cd "$DIR/deploy"
DB_NAME="$(grep -E '^DB_NAME=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')"
DB_NAME="${DB_NAME:-bastion}"
TMPDB="bastion_recupera_$$"

umask 077
TMP="$(mktemp -d)"
cleanup() {
  docker compose exec -T mongo mongosh --quiet --eval "db.getSiblingDB('$TMPDB').dropDatabase()" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

tar xzf "$FILE" -C "$TMP" --wildcards '*mongo.archive.gz'
echo ">> Lendo os mapas do backup…"
docker compose exec -T mongo mongorestore --quiet --archive --gzip \
  --nsInclude "$DB_NAME.maps" --nsFrom "$DB_NAME.maps" --nsTo "$TMPDB.maps" < "$TMP/mongo.archive.gz"

docker compose exec -T -e WANT="$WANT" mongo mongosh --quiet --eval "
const cur = db.getSiblingDB('$DB_NAME').maps, bk = db.getSiblingDB('$TMPDB').maps;
const want = process.env.WANT || '';
const users = {}; db.getSiblingDB('$DB_NAME').users.find({}, {id: 1, email: 1, name: 1}).forEach(u => users[u.id] = u.email || u.name || u.id);
const all = bk.find({}, {_id: 0}).toArray();
if (!want) {
  print('   Mapas no backup:');
  all.forEach(m => print('   ' + (cur.countDocuments({id: m.id}) ? '   ' : '>> ') + JSON.stringify(m.name) + '  id=' + m.id +
    '  (' + (m.nodes || []).length + ' equip., ' + (m.links || []).length + ' enlaces, dono ' + (users[m.owner_id] || '?') + ')' +
    (cur.countDocuments({id: m.id}) ? '' : '  <- APAGADO')));
  print(''); print('Para restaurar: sudo bash $0 $FILE \"NOME DO MAPA\"   (ou o id)');
} else {
  const hits = all.filter(m => m.id === want || m.name === want);
  if (!hits.length) { print('!! Nenhum mapa com esse nome/id no backup.'); quit(2); }
  hits.forEach(m => {
    if (cur.countDocuments({id: m.id})) { print('   ' + m.name + ': já existe no banco atual — nada feito.'); return; }
    m.updated_at = new Date().toISOString();
    cur.insertOne(m);
    print('>> Restaurado: ' + m.name + ' (' + (m.nodes || []).length + ' equipamentos, ' + (m.links || []).length + ' enlaces).');
  });
}
"
