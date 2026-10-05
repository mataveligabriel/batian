#!/usr/bin/env bash
# BastiON — cópia de segurança do Krill (chaves privadas das CAs + ROAs). Guarde o arquivo FORA deste servidor.
# Uso:  sudo bash /opt/bastion/deploy/rpki-backup.sh [pasta de destino]      (padrão: /opt/bastion-backups)
set -euo pipefail
cd "$(dirname "$0")"
DEST="${1:-/opt/bastion-backups}"
mkdir -p "$DEST"; chmod 700 "$DEST"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
docker compose --profile rpki cp krill:/var/krill/data "$TMP/krill-data" >/dev/null
OUT="$DEST/krill-$(date +%Y%m%d-%H%M%S).tar.gz"
(umask 077; tar czf "$OUT" -C "$TMP" krill-data)
ls -1t "$DEST"/krill-*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f       # mantém as 14 mais recentes
echo "OK: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Este arquivo contém as chaves privadas das CAs: copie para um lugar seguro fora do servidor."
echo "Para restaurar: pare o krill, devolva o conteúdo para o volume (docker compose cp krill-data/. krill:/var/krill/data) e suba de novo."
