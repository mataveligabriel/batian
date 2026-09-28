#!/usr/bin/env bash
# SSH Bastion Central — atualiza para a última versão do repositório e reconstrói os containers.
set -euo pipefail
DIR="${DIR:-/opt/bastion}"
BRANCH="${BRANCH:-main}"
cd "$DIR"
echo ">> Baixando última versão…"
git fetch -q origin && git reset -q --hard "origin/$BRANCH"
cd deploy
# buffer UDP maior para o coletor de flow não perder pacotes em rajadas
if [ "$(id -u)" -eq 0 ] && [ ! -f /etc/sysctl.d/90-bastion-flow.conf ]; then
  echo "net.core.rmem_max=33554432" > /etc/sysctl.d/90-bastion-flow.conf
  sysctl -q -p /etc/sysctl.d/90-bastion-flow.conf || true
fi
# VPN (COMPOSE_PROFILES=vpn no .env): o container precisa do /dev/ppp do servidor
if grep -Eq '^COMPOSE_PROFILES=.*vpn' .env 2>/dev/null && [ "$(id -u)" -eq 0 ]; then
  modprobe ppp_generic 2>/dev/null || true
  [ -e /dev/ppp ] || mknod /dev/ppp c 108 0 2>/dev/null || echo "!! /dev/ppp indisponível neste servidor (VPS OpenVZ/LXC?) — a VPN não vai subir"
  grep -qx ppp_generic /etc/modules 2>/dev/null || echo ppp_generic >> /etc/modules
fi
echo ">> Reconstruindo containers…"
docker compose up -d --build
echo ">> Aguardando backend…"
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8001/api/ >/dev/null 2>&1 && { echo "OK: backend respondendo"; break; }
  sleep 3
done
docker compose ps
