#!/bin/sh
# Sobe o Caddy. Ao Caddyfile padrão acrescenta, conforme o deploy/.env:
#   - BASTION_HTTPS_HOST: o HTTPS (site + portas do Acesso Web);
#   - LG_PUBLIC_PORT (padrão 8089; 0 desliga): o Looking Glass público numa porta própria, que só serve a página
#     pública e /api/public/lg — o resto da API não passa por ela.
# Se a configuração gerada não validar, sobe só a padrão para o BastiON nunca ficar fora do ar por causa disso.
BASE="${CADDYFILE_BASE:-/etc/caddy/Caddyfile}"
CFG="$BASE"
HOST="${BASTION_HTTPS_HOST:-}"
LGP="${LG_PUBLIC_PORT:-8089}"
OUT=/tmp/Caddyfile.gen
# domínios cadastrados em Configurações → Domínio (o backend grava; um por linha). Mudou: o Caddy recarrega sozinho.
DOMFILE="${BASTION_DOMAINS_FILE:-/bastion/web/domains.txt}"
STATUS="${DOMFILE%/*}/caddy-status.json"
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
extra_domains() {   # site do BastiON também em cada domínio do painel (HTTPS automático pelo Let's Encrypt)
  [ -f "$DOMFILE" ] || return 0
  LIST=""
  for d in $(grep -Eio '^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?)+$' "$DOMFILE" | tr 'A-Z' 'a-z' | grep -v '^[0-9.]*$' | sort -u); do
    case " ${BASTION_DOMAIN:-} ${BASTION_ALT:-} https://${BASTION_DOMAIN:-} " in *" $d "*|*" https://$d "*) continue ;; esac
    LIST="${LIST:+$LIST, }$d"
  done
  [ -n "$LIST" ] && printf '%s {\n%s\n}\n\n' "$LIST" "$BODY"
  return 0
}
gen() {
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
  extra_domains
  if [ "$LGP" != "0" ] && echo "$LGP" | grep -Eq '^[0-9]+$'; then
    lg_site "http://:$LGP" ""
    if [ -n "$HOST" ]; then lg_site "https://$HOST:${LG_PUBLIC_TLS_PORT:-$((LGP + 400))}" "$TLS"; fi
  fi
} > "$OUT"
}
write_status() {   # $1 = ok|erro, $2 = mensagem
  [ -d /bastion/web ] || return 0
  printf '{"state":"%s","at":"%s","domains":"%s","message":"%s"}\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$( [ -f "$DOMFILE" ] && tr '\n' ' ' < "$DOMFILE" | sed 's/"//g')" "$(echo "$2" | tr '\n"' '  ' | cut -c1-400)" > "$STATUS.tmp" \
    && mv "$STATUS.tmp" "$STATUS"
}
sum_domains() { [ -f "$DOMFILE" ] && md5sum "$DOMFILE" | cut -d' ' -f1 || echo none; }
gen
if [ "${1:-}" = "--print" ]; then cat "$OUT"; exit 0; fi
if caddy validate --config "$OUT" --adapter caddyfile >/tmp/validate.log 2>&1; then
  CFG="$OUT"
  [ -n "$HOST" ] && echo ">> HTTPS ligado: https://$HOST:${BASTION_HTTPS_PORT:-8443} (Acesso Web em +${WEB_PROXY_TLS_OFFSET:-400})"
  [ "$LGP" != "0" ] && echo ">> Looking Glass público na porta $LGP"
else
  echo "!! A configuração gerada não validou — subindo só o endereço padrão. Motivo:"
  tail -5 /tmp/validate.log
fi
[ "$CFG" = "$OUT" ] && write_status ok "" || write_status erro "$(tail -3 /tmp/validate.log)"
caddy run --config "$CFG" --adapter caddyfile &
PID=$!
trap 'kill -TERM $PID 2>/dev/null; wait $PID; exit 0' TERM INT
LAST=$(sum_domains)
# vigia a lista de domínios do painel: mudou → gera, valida e recarrega sem derrubar as conexões
while kill -0 $PID 2>/dev/null; do
  sleep 5 &
  wait $!
  NOW=$(sum_domains)
  [ "$NOW" = "$LAST" ] && continue
  LAST=$NOW
  gen
  if caddy validate --config "$OUT" --adapter caddyfile >/tmp/validate.log 2>&1 \
     && caddy reload --config "$OUT" --adapter caddyfile >/tmp/reload.log 2>&1; then
    echo ">> domínios do painel aplicados: $(tr '\n' ' ' < "$DOMFILE" 2>/dev/null)"
    write_status ok ""
  else
    echo "!! domínios do painel não aplicados:"; tail -3 /tmp/validate.log /tmp/reload.log 2>/dev/null
    write_status erro "$(tail -3 /tmp/validate.log; tail -3 /tmp/reload.log 2>/dev/null)"
  fi
done
wait $PID
