#!/usr/bin/env bash
# SSH Bastion Central — atualiza para a última versão do repositório e reconstrói os containers.
set -euo pipefail
DIR="${DIR:-/opt/bastion}"
BRANCH="${BRANCH:-main}"
cd "$DIR"
echo ">> Baixando última versão…"
git fetch -q origin && git reset -q --hard "origin/$BRANCH"
cd deploy
echo ">> Reconstruindo containers…"
docker compose up -d --build
echo ">> Aguardando backend…"
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8001/api/ >/dev/null 2>&1 && { echo "OK: backend respondendo"; break; }
  sleep 3
done
docker compose ps
