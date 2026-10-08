#!/bin/sh
# Sobe o Caddy. Ao Caddyfile padrão acrescenta, conforme o deploy/.env:
#   - BASTION_HTTPS_HOST: o HTTPS (site + portas do Acesso Web);
#   - LG_PUBLIC_PORT (padrão 8089; 0 desliga): o Looking Glass público numa porta própria, que só serve a página
#     pública e /api/public/lg — o resto da API não passa por ela.
# Se a configuração gerada não validar, sobe só a padrão para o BastiON nunca ficar fora do ar por causa disso.
BASE=/etc/caddy/Caddyfile
CFG="$BASE"
HOST="${BASTION_HTTPS_HOST:-}"
LGP="${LG_PUBLIC_PORT:-8089}"
OUT=/tmp/Caddyfile.gen
# corpo do site = Caddyfile padrão sem os comentários do topo e sem a linha de endereços
BODY=$(awk 'found {print} /^\{\$BASTION_DOMAIN\}/ {found=1}' "$BASE")
TLS=""
# IP não tem certificado público: usa a autoridade própria do Caddy. Domínio usa Let's Encrypt.
if [ -n "$HOST" ] && echo "$HOST" | grep -Eq '^[0-9.]+$|:'; then TLS="	tls internal"; fi
lg_site() {   # $1 = endereço do site, $2 = linha de TLS (ou vazio)
  printf '%s {\n%s\n\tencode gzip\n\thandle /api/public/lg* {\n\t\treverse_proxy 127.0.0.1:8001\n\t}\n' "$1" "$2"
  printf '\thandle /api/* {\n\t\trespond "Not found" 404\n\t}\n\thandle {\n\t\troot * /srv\n\t\t@home path /\n\t\tredir @home /looking-glass\n'
  printf '\t\t@static path /static/*\n\t\theader @static Cache-Control "public, max-age=31536000, immutable"\n\t\t@page not path /static/*\n\t\theader @page Cache-Control "no-cache"\n'
  printf '\t\ttry_files {path} /index.html\n\t\tfile_server\n\t}\n}\n\n'
}
{
  printf '{$BASTION_DOMAIN} {$BASTION_ALT} {\n%s\n}\n\n' "$BODY"
  if [ -n "$HOST" ]; then
    PORT="${BASTION_HTTPS_PORT:-8443}"
    OFFSET="${WEB_PROXY_TLS_OFFSET:-400}"
    PORTS="${WEB_PROXY_PORTS:-8090-8099}"
    # site principal por HTTPS (pula se o endereço principal já é esse domínio em HTTPS)
    MAIN="${BASTION_DOMAIN:-}"
    if [ "$MAIN" != "$HOST" ] && [ "$MAIN" != "https://$HOST" ] || [ "$PORT" != "443" ]; then
      printf 'https://%s:%s {\n%s\n%s\n}\n\n' "$HOST" "$PORT" "$TLS" "$BODY"
    fi
    # portas do Acesso Web por HTTPS: porta + OFFSET -> backend na porta original
    for part in $(echo "$PORTS" | tr ',' ' '); do
      case "$part" in
        *-*) a=${part%-*}; b=${part#*-} ;;
        *) a=$part; b=$part ;;
      esac
      i=$a
      while [ "$i" -le "$b" ]; do
        printf 'https://%s:%s {\n%s\n\treverse_proxy 127.0.0.1:%s\n}\n' "$HOST" "$((i + OFFSET))" "$TLS" "$i"
        i=$((i + 1))
      done
    done
  fi
  if [ "$LGP" != "0" ] && echo "$LGP" | grep -Eq '^[0-9]+$'; then
    lg_site "http://:$LGP" ""
    if [ -n "$HOST" ]; then lg_site "https://$HOST:${LG_PUBLIC_TLS_PORT:-$((LGP + 400))}" "$TLS"; fi
  fi
} > "$OUT"
if [ "${1:-}" = "--print" ]; then cat "$OUT"; exit 0; fi
if caddy validate --config "$OUT" --adapter caddyfile >/tmp/validate.log 2>&1; then
  CFG="$OUT"
  [ -n "$HOST" ] && echo ">> HTTPS ligado: https://$HOST:${BASTION_HTTPS_PORT:-8443} (Acesso Web em +${WEB_PROXY_TLS_OFFSET:-400})"
  [ "$LGP" != "0" ] && echo ">> Looking Glass público na porta $LGP"
else
  echo "!! A configuração gerada não validou — subindo só o endereço padrão. Motivo:"
  tail -5 /tmp/validate.log
fi
exec caddy run --config "$CFG" --adapter caddyfile
