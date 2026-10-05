#!/usr/bin/env bash
# BastiON — ativa o RPKI: sobe o Krill (container ao lado, só em 127.0.0.1) e liga o menu RPKI.
# Rode uma vez:  sudo bash /opt/bastion/deploy/rpki-setup.sh
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "!! deploy/.env não encontrado"; exit 1; }
PORT="$(grep -E '^KRILL_PORT=' .env | tail -1 | cut -d= -f2- || true)"; PORT="${PORT:-3000}"

if ! grep -Eq '^KRILL_ADMIN_TOKEN=.+' .env; then
  if ! docker compose ps -a --services 2>/dev/null | grep -qx krill \
     && (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -q ":$PORT "; then
    echo "!! A porta $PORT já está em uso neste servidor. Escolha outra: acrescente KRILL_PORT=3010 e"
    echo "   KRILL_URL=https://127.0.0.1:3010 no deploy/.env e rode este script de novo."
    exit 1
  fi
  TOKEN="$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  sed -i '/^KRILL_ADMIN_TOKEN=/d' .env
  printf '\nKRILL_ADMIN_TOKEN=%s\n' "$TOKEN" >> .env
  echo ">> Token do Krill criado no deploy/.env"
fi
grep -Eq '^KRILL_URL=' .env || echo "KRILL_URL=https://127.0.0.1:$PORT" >> .env
if grep -Eq '^COMPOSE_PROFILES=' .env; then
  grep -E '^COMPOSE_PROFILES=' .env | tail -1 | tr -d '"' | cut -d= -f2- | tr ',' '\n' | grep -qx rpki \
    || sed -i -E '0,/^COMPOSE_PROFILES=/{s/^COMPOSE_PROFILES="?([^"]*)"?$/COMPOSE_PROFILES=\1,rpki/;s/=,rpki$/=rpki/}' .env
else
  echo "COMPOSE_PROFILES=rpki" >> .env
fi
chmod 600 .env 2>/dev/null || true

echo ">> Baixando e subindo o Krill…"
docker compose --profile rpki pull krill
docker compose --profile rpki up -d krill
echo ">> Reiniciando o backend para ele enxergar o Krill…"
docker compose up -d --force-recreate backend
ok=""
for i in $(seq 1 30); do
  curl -kfsS "https://127.0.0.1:$PORT/health" >/dev/null 2>&1 && { ok=1; break; }
  sleep 2
done
if [ -z "$ok" ]; then
  echo "!! O Krill não respondeu em https://127.0.0.1:$PORT — últimas linhas do log:"
  docker compose --profile rpki logs --tail 40 krill || true
  exit 1
fi
echo
echo "OK: Krill no ar. Abra o BastiON → menu RPKI → Nova CA."
echo "Importante: as chaves das CAs ficam no volume do Krill. Faça cópia com:"
echo "   sudo bash $(pwd)/rpki-backup.sh"
