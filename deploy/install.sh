#!/usr/bin/env bash
# SSH Bastion Central — instalação do zero (Debian 11/12 ou Ubuntu 22.04/24.04). Rodar como root.
# Uso: curl -fsSL https://raw.githubusercontent.com/<usuario>/<repo>/main/deploy/install.sh | REPO_URL=https://github.com/<usuario>/<repo>.git bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/mataveligabriel/batian.git}"
BRANCH="${BRANCH:-main}"
DIR="${DIR:-/opt/bastion}"

[ "$(id -u)" -eq 0 ] || { echo "Rode como root (sudo -i)"; exit 1; }
. /etc/os-release

echo ">> Instalando dependências (Docker, git, ufw)…"
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git ufw openssl >/dev/null
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
fi
systemctl enable --now docker >/dev/null

echo ">> Firewall: liberando 22, 80 e 443…"
ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo ">> Baixando o código…"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin && git -C "$DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q -b "$BRANCH" "$REPO_URL" "$DIR"
fi
cd "$DIR/deploy"

if [ ! -f .env ]; then
  echo
  echo "=== Configuração inicial ==="
  read -rp "Domínio ou IP público deste servidor (ex.: bastion.empresa.com ou 177.1.2.3): " HOSTIN </dev/tty
  read -rp "E-mail do administrador: " ADMIN_EMAIL </dev/tty
  read -rsp "Senha do administrador: " ADMIN_PASSWORD </dev/tty; echo
  if [[ "$HOSTIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    PUBLIC_URL="http://$HOSTIN"; BASTION_DOMAIN="http://$HOSTIN"
  else
    PUBLIC_URL="https://$HOSTIN"; BASTION_DOMAIN="$HOSTIN"
  fi
  cat > .env <<EOF
PUBLIC_URL=$PUBLIC_URL
BASTION_DOMAIN=$BASTION_DOMAIN
MONGO_URL=mongodb://127.0.0.1:27017
DB_NAME=bastion
JWT_SECRET=$(openssl rand -hex 32)
ADMIN_EMAIL=$ADMIN_EMAIL
ADMIN_PASSWORD=$ADMIN_PASSWORD
CORS_ORIGINS=$PUBLIC_URL
TUNNEL_BIND_HOST=127.0.0.1
SEED_SAMPLE_DATA=false
EOF
  chmod 600 .env
  echo ">> .env criado em $DIR/deploy/.env"
else
  echo ">> .env já existe — mantendo configuração atual."
fi

echo ">> Construindo e iniciando (pode levar alguns minutos na primeira vez)…"
docker compose up -d --build
echo ">> Aguardando backend…"
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8001/api/ >/dev/null 2>&1 && break
  sleep 3
done
docker compose ps
echo
echo "=============================================="
echo " Bastion pronto: $(grep PUBLIC_URL .env | cut -d= -f2)"
echo " Login: $(grep ADMIN_EMAIL .env | cut -d= -f2)"
echo " Próximo passo: Agentes -> Configurar Bastion -> rodar o script de preparação neste servidor."
echo " Atualizar depois: bash $DIR/deploy/update.sh"
echo "=============================================="
