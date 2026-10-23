"""Domínio próprio do BastiON (Configurações → Domínio).

O backend grava a lista em <dados>/web/domains.txt (volume bastion_data, que o container do Caddy também monta em
/bastion). O entrypoint do Caddy vigia esse arquivo: mudou, gera a configuração com o site do BastiON também nesses
domínios, valida e recarrega sem derrubar ninguém. O certificado HTTPS (Let's Encrypt) sai sozinho assim que o DNS
do domínio aponta para este servidor e as portas 80/443 estão abertas.
"""
import asyncio
import json
import os
import re
import socket
import ssl
import time
from typing import List, Optional

import httpx

DATA_DIR = os.environ.get("BASTION_DATA_DIR", "/data")
WEB_DIR = os.path.join(DATA_DIR, "web")
DOMAINS_FILE = os.path.join(WEB_DIR, "domains.txt")
STATUS_FILE = os.path.join(WEB_DIR, "caddy-status.json")
MAX_DOMAINS = 10

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
FQDN_RE = re.compile(rf"^(?:{_LABEL}\.)+[a-z]{{2,63}}$")

_nonces: dict = {}
_ip_cache = {"at": 0.0, "ip": ""}


class DomainError(ValueError):
    pass


def normalize(d: str) -> str:
    d = (d or "").strip().lower().rstrip(".")
    d = re.sub(r"^https?://", "", d).split("/")[0].split(":")[0]
    if not FQDN_RE.match(d) or len(d) > 253:
        raise DomainError(f"Domínio inválido: {d or '(vazio)'} — use só o nome, ex.: bastion.net.br")
    return d


def read_domains() -> List[str]:
    try:
        with open(DOMAINS_FILE) as f:
            return [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        return []


def write_domains(domains: List[str]) -> List[str]:
    out = []
    for d in domains:
        n = normalize(d)
        if n not in out:
            out.append(n)
    if len(out) > MAX_DOMAINS:
        raise DomainError(f"No máximo {MAX_DOMAINS} domínios")
    os.makedirs(WEB_DIR, exist_ok=True)
    tmp = DOMAINS_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write("".join(d + "\n" for d in out))
    os.replace(tmp, DOMAINS_FILE)
    return out


def caddy_status() -> dict:
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def new_nonce() -> str:
    now = time.time()
    for k, t in list(_nonces.items()):
        if now - t > 120:
            _nonces.pop(k, None)
    n = os.urandom(12).hex()
    _nonces[n] = now
    return n


def probe_ok(nonce: str) -> bool:
    return nonce in _nonces


async def public_ip() -> str:
    """IP público deste servidor (cache de 10 min)."""
    if _ip_cache["ip"] and time.time() - _ip_cache["at"] < 600:
        return _ip_cache["ip"]
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.get(url)
            ip = r.text.strip()
            socket.inet_aton(ip)
            _ip_cache.update(at=time.time(), ip=ip)
            return ip
        except Exception:
            continue
    return os.environ.get("PUBLIC_IP", "")


async def _resolve(domain: str, family) -> List[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(domain, None, family=family, type=socket.SOCK_STREAM)
        return sorted({i[4][0] for i in infos})
    except socket.gaierror:
        return []


async def check(domain: str) -> dict:
    """DNS aponta para cá? Porta 80 responde? HTTPS com certificado válido chega neste BastiON?"""
    d = normalize(domain)
    ip = await public_ip()
    v4, v6 = await asyncio.gather(_resolve(d, socket.AF_INET), _resolve(d, socket.AF_INET6))
    res = {"domain": d, "server_ip": ip, "a": v4, "aaaa": v6, "dns_ok": bool(ip and ip in v4),
           "http_ok": None, "https_ok": None, "cert_ok": None, "error": ""}
    if not v4:
        res["error"] = "O domínio ainda não tem registro A (ou o DNS ainda não propagou)"
        return res
    if ip and ip not in v4:
        res["error"] = f"O registro A aponta para {', '.join(v4)}, mas este servidor é {ip}"
        return res
    nonce = new_nonce()
    try:
        async with httpx.AsyncClient(timeout=6, follow_redirects=False) as c:
            await c.get(f"http://{d}/api/system/domain-probe/{nonce}")
        res["http_ok"] = True
    except Exception:
        res["http_ok"] = False
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"https://{d}/api/system/domain-probe/{nonce}")
        res["cert_ok"] = True
        res["https_ok"] = r.status_code == 200 and r.text.strip().strip('"') == nonce
        if not res["https_ok"]:
            res["error"] = f"HTTPS responde, mas não é este BastiON (HTTP {r.status_code})"
    except httpx.ConnectError as e:
        txt = str(e)
        if "CERTIFICATE_VERIFY_FAILED" in txt or isinstance(getattr(e, "__cause__", None), ssl.SSLError) or "SSL" in txt:
            res["cert_ok"] = False
            res["error"] = "Certificado HTTPS ainda não emitido (leva 1–2 min depois de salvar, com o DNS já apontando)"
        else:
            res["error"] = "A porta 443 não responde — libere 80 e 443 no firewall do servidor"
    except Exception as e:
        res["error"] = f"HTTPS não respondeu: {type(e).__name__}"
    if res["http_ok"] is False and not res["https_ok"]:
        res["error"] = (res["error"] + " · " if res["error"] else "") + "porta 80 fechada (o Let's Encrypt precisa dela)"
    return res
