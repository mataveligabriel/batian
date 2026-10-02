#!/usr/bin/env bash
# BastiON — autoriza um drive na nuvem (Google Drive por padrão) para receber os backups.
# Uso:  sudo bash /opt/bastion/deploy/cloud-auth.sh [nome] [tipo]
#       nome: como o drive vai aparecer no BastiON (padrão: gdrive)
#       tipo: drive (Google Drive, padrão) | onedrive | dropbox | …  (tipos do rclone)
set -euo pipefail
cd "$(dirname "$0")"
NAME="${1:-gdrive}"
TYPE="${2:-drive}"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

if ! docker compose exec -T backend rclone version >/dev/null 2>&1; then
  echo "!! O rclone ainda não está no container. Rode antes: sudo bash /opt/bastion/deploy/update.sh"
  exit 1
fi

cat <<MSG

================  Autorizar "$NAME" ($TYPE)  ================
O Google só entrega a autorização para o endereço http://127.0.0.1:53682 — ou seja, o navegador
precisa "enxergar" essa porta do servidor. Faça assim:

 1) NO SEU PC, abra outro terminal (PowerShell ou WSL) e deixe este comando rodando:

        ssh -N -L 53682:127.0.0.1:53682 root@${IP:-IP_DO_SERVIDOR}

 2) Volte aqui. Vai aparecer um link começando com  http://127.0.0.1:53682/auth?state=...
    Copie e abra NO NAVEGADOR DO SEU PC, entre na conta do Google e clique em Permitir.

 3) Quando o navegador mostrar "Success!", este script termina sozinho.

O BastiON só enxerga no drive os arquivos que ele mesmo criar (permissão "drive.file").
=============================================================

MSG
read -r -p "Túnel do passo 1 aberto? Enter para continuar… " _

EXTRA=()
[ "$TYPE" = "drive" ] && EXTRA=(scope=drive.file)
docker compose exec backend rclone --config /data/rclone/rclone.conf config delete "$NAME" >/dev/null 2>&1 || true
docker compose exec backend rclone --config /data/rclone/rclone.conf config create "$NAME" "$TYPE" "${EXTRA[@]}"

echo
echo ">> Conferindo o acesso…"
if docker compose exec -T backend rclone --config /data/rclone/rclone.conf mkdir "$NAME:BastiON" \
   && docker compose exec -T backend rclone --config /data/rclone/rclone.conf lsd "$NAME:" >/dev/null; then
  echo "OK: drive \"$NAME\" autorizado. No BastiON: Automação → Backups na nuvem → escolha \"$NAME\" e as tags."
  echo "    (pode fechar o terminal do túnel no seu PC)"
else
  echo "!! Não consegui acessar o drive. Rode o script de novo; se o link não abrir, confira o túnel do passo 1."
  exit 1
fi
