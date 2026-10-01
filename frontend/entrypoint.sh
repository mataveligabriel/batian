#!/bin/sh
# Sobe o Caddy. Com BASTION_HTTPS_HOST no deploy/.env, acrescenta o HTTPS (site + portas do Acesso Web)
# ao Caddyfile padrão. Se a configuração com HTTPS não validar, sobe só a configuração padrão (HTTP)
# para o BastiON nunca ficar fora do ar por causa disso.
BASE=/etc/caddy/Caddyfile
CFG="$BASE"
HOST="${BASTION_HTTPS_HOST:-}"
if [ -n "$HOST" ]; then
  PORT="${BASTION_HTTPS_PORT:-8443}"
  OFFSET="${WEB_PROXY_TLS_OFFSET:-400}"
  PORTS="${WEB_PROXY_PORTS:-8090-8099}"
  OUT=/tmp/Caddyfile.https
  # IP não tem certificado público: usa a autoridade própria do Caddy. Domínio usa Let's Encrypt.
  TLS=""
  if echo "$HOST" | grep -Eq '^[0-9.]+$|:'; then TLS="	tls internal"; fi
  # corpo do site = Caddyfile padrão sem os comentários do topo e sem a linha de endereços
  BODY=$(awk 'found {print} /^\{\$BASTION_DOMAIN\}/ {found=1}' "$BASE")
  {
    printf '{$BASTION_DOMAIN} {$BASTION_ALT} {\n%s\n}\n\n' "$BODY"
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
  } > "$OUT"
  if [ "${1:-}" = "--print" ]; then cat "$OUT"; exit 0; fi
  if caddy validate --config "$OUT" --adapter caddyfile >/tmp/validate.log 2>&1; then
    CFG="$OUT"
    echo ">> HTTPS ligado: https://$HOST:$PORT (Acesso Web em +$OFFSET)"
  else
    echo "!! A configuração com HTTPS não validou — subindo só o endereço padrão. Motivo:"
    tail -5 /tmp/validate.log
  fi
fi
exec caddy run --config "$CFG" --adapter caddyfile
