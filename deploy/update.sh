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
# 1) compila tudo ANTES de mexer nos containers: enquanto isso o sistema continua no ar com a versão
#    anterior, e se a compilação falhar (rede, erro no código) nada é derrubado.
echo ">> Compilando a versão nova (o Bastion continua no ar)…"
if ! docker compose build; then
  echo "!! A compilação falhou — os containers atuais continuam rodando a versão anterior."
  echo "   Mande as linhas de erro acima para análise."
  exit 1
fi
# Ollama: se já existe um Ollama instalado direto no servidor (porta 11434 ocupada por ele), o container
# "ollama" não consegue subir e derrubava a troca inteira. Nesse caso o perfil ollama é ignorado.
PROFILES="$(grep -E '^COMPOSE_PROFILES=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true)"
if echo ",$PROFILES," | grep -q ',ollama,'; then
  if ! docker compose ps --status running --services 2>/dev/null | grep -qx ollama \
     && (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -q ':11434 '; then
    echo ">> Ollama já roda direto no servidor (porta 11434): o container ollama não será usado."
    docker compose rm -sf ollama >/dev/null 2>&1 || true
    PROFILES="$(echo "$PROFILES" | tr ',' '\n' | grep -vx ollama | paste -sd, - || true)"
    export COMPOSE_PROFILES="$PROFILES"
  fi
fi
# 2) troca os containers (poucos segundos fora do ar). Se um serviço não subir, os outros sobem assim mesmo
#    e o passo 3 mostra qual faltou.
echo ">> Trocando os containers…"
docker compose up -d --remove-orphans || echo "!! Algum serviço não subiu de primeira — conferindo abaixo…"
echo ">> Aguardando backend…"
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8001/api/ >/dev/null 2>&1 && { echo "OK: backend respondendo"; break; }
  sleep 3
done
# 3) confere se todos os serviços ficaram de pé (ex.: o frontend/Caddy) e tenta subir de novo o que faltar
sleep 3
missing=""
for svc in $(docker compose config --services); do
  docker compose ps --status running --services 2>/dev/null | grep -qx "$svc" || missing="$missing $svc"
done
if [ -n "$missing" ]; then
  echo "!! Não subiram:$missing — tentando de novo…"
  docker compose up -d $missing || true
  sleep 5
  for svc in $missing; do
    if ! docker compose ps --status running --services 2>/dev/null | grep -qx "$svc"; then
      echo "!! $svc continua parado. Últimas linhas do log:"
      docker compose logs --tail 30 "$svc" || true
    fi
  done
fi
docker compose ps
