"""Auditoria CERT.br (passiva): serviços que respondem para a internet e viram refletor de DDoS.

Três fontes, sem varrer nada:
  flow   — um IP seu respondendo de uma porta de serviço UDP (53, 123, 1900, 161, 11211…) para IPs de fora
  aviso  — o texto das notificações que o CERT.br manda por e-mail (colado na tela ou encaminhado ao webhook)
  config — regras da Conformidade nos backups (DNS/NTP/UPnP/SOCKS abertos nos seus equipamentos)
"""
import ipaddress
import re
from typing import Dict, List, Optional, Tuple

from flowagg import PrefixSet, ip_str

SERVICES = {
    "dns": {"name": "DNS recursivo aberto", "port": 53, "kw": r"dns|recursiv|resolver",
            "fix": "Recursão só para os seus prefixos (allow-recursion / acl), ou bloquear UDP/TCP 53 vindo de fora. No MikroTik do cliente: allow-remote-requests=no ou firewall na chain input."},
    "ntp": {"name": "NTP com monlist/aberto", "port": 123, "kw": r"\bntp\b|monlist",
            "fix": "Desligar o monlist (disable monitor) e restringir (restrict default noquery) ou bloquear UDP 123 de fora."},
    "ssdp": {"name": "SSDP/UPnP aberto", "port": 1900, "kw": r"ssdp|upnp",
             "fix": "Desligar UPnP na WAN do roteador do cliente (comum em roteador doméstico) ou bloquear UDP 1900 de fora."},
    "snmp": {"name": "SNMP aberto", "port": 161, "kw": r"\bsnmp\b",
             "fix": "Community longa e ACL só para o servidor de monitoramento; bloquear UDP 161 de fora."},
    "memcached": {"name": "Memcached aberto", "port": 11211, "kw": r"memcache",
                  "fix": "Escutar só em 127.0.0.1 (-l 127.0.0.1 -U 0) e bloquear UDP/TCP 11211."},
    "portmap": {"name": "Portmapper aberto", "port": 111, "kw": r"portmap|rpcbind|sunrpc",
                "fix": "Desligar o rpcbind se não usa NFS, ou bloquear UDP/TCP 111 de fora."},
    "netbios": {"name": "NetBIOS aberto", "port": 137, "kw": r"netbios",
                "fix": "Bloquear UDP 137-139 e TCP 445 vindos da internet."},
    "mdns": {"name": "mDNS aberto", "port": 5353, "kw": r"mdns|multicast dns",
             "fix": "mDNS não deve responder fora da rede local: bloquear UDP 5353 na WAN."},
    "cldap": {"name": "CLDAP aberto", "port": 389, "kw": r"cldap|\bldap\b",
              "fix": "Bloquear UDP 389 vindo da internet (controlador de domínio exposto)."},
    "ubiquiti": {"name": "Descoberta Ubiquiti aberta", "port": 10001, "kw": r"ubiquiti|ubnt|airos",
                 "fix": "Desligar o Discovery na interface WAN do equipamento Ubiquiti ou bloquear UDP 10001."},
    "chargen": {"name": "Chargen aberto", "port": 19, "kw": r"chargen",
                "fix": "Desligar o serviço chargen (inetd/xinetd) e bloquear UDP 19."},
    "wsd": {"name": "WS-Discovery aberto", "port": 3702, "kw": r"ws-discovery|wsd",
            "fix": "Bloquear UDP 3702 na WAN (câmeras/impressoras expostas)."},
    "tftp": {"name": "TFTP aberto", "port": 69, "kw": r"\btftp\b", "fix": "Desligar o TFTP ou restringir à rede de gerência."},
    "dvr": {"name": "DVR/câmera (porta 37810) aberto", "port": 37810, "kw": r"dvr|dahua", "fix": "Bloquear UDP 37810 na WAN do DVR."},
    "ard": {"name": "Apple Remote Desktop aberto", "port": 3283, "kw": r"apple remote|\bard\b", "fix": "Bloquear UDP 3283 vindo da internet."},
    "socks": {"name": "Proxy SOCKS/HTTP aberto", "port": 1080, "kw": r"socks|proxy aberto|open proxy",
              "fix": "Desligar o proxy (no MikroTik: /ip socks set enabled=no) ou restringir por ACL."},
}
PORT_SERVICE = {v["port"]: k for k, v in SERVICES.items() if k != "socks"}
DEFAULTS = {"enabled": True, "min_remotes": 3, "auto_resolve_days": 7, "ignore": [], "token": "", "alert": True}
_IP4 = re.compile(r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?![\d.])")
_IP6 = re.compile(r"(?<![0-9A-Fa-f:])((?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{1,4})*)(?![0-9A-Fa-f:])")


def detect_service(text: str) -> Optional[str]:
    t = (text or "").lower()
    for k, v in SERVICES.items():
        if re.search(v["kw"], t):
            return k
    m = re.search(r"\b(\d{1,5})\s*/\s*udp\b|\budp\s*/?\s*(\d{1,5})\b", t)
    if m:
        p = int(m.group(1) or m.group(2))
        return PORT_SERVICE.get(p)
    return None


def parse_notice(text: str, subject: str = "") -> List[dict]:
    """Aviso do CERT.br (ou de outro CSIRT): [{ip, service, line}]. O serviço sai da linha (porta) ou do assunto/corpo."""
    whole = detect_service(subject) or detect_service((text or "")[:3000])
    out, seen = [], set()
    for ln in (text or "").splitlines():
        ips = _IP4.findall(ln) + [x for x in _IP6.findall(ln) if x.count(":") >= 2]
        if not ips:
            continue
        svc = detect_service(ln) or whole
        for ip in ips:
            try:
                a = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if a.is_private or a.is_loopback or a.is_multicast or a.is_reserved or a.is_unspecified:
                continue
            k = (str(a), svc)
            if k in seen:
                continue
            seen.add(k)
            out.append({"ip": str(a), "service": svc or "unknown", "line": ln.strip()[:300]})
    return out


def clean_settings(body: dict) -> dict:
    out = {**DEFAULTS, **{k: body[k] for k in DEFAULTS if k in body}}
    out["enabled"] = bool(out["enabled"])
    out["alert"] = bool(out["alert"])
    out["min_remotes"] = max(1, min(1000, int(out["min_remotes"] or 3)))
    out["auto_resolve_days"] = max(1, min(90, int(out["auto_resolve_days"] or 7)))
    ign = []
    for x in out["ignore"] or []:
        x = str(x).strip()
        if not x:
            continue
        try:
            ign.append(str(ipaddress.ip_network(x, strict=False)))
        except ValueError:
            raise ValueError(f"IP/rede inválido em ignorar: {x}")
    out["ignore"] = ign[:2000]
    out["token"] = str(out.get("token") or "")[:80]
    return out


class FlowTracker:
    """No coletor: IP seu respondendo de porta de serviço UDP para fora (dport diferente da porta do serviço)."""
    MAX_KEYS = 20_000
    MAX_REMOTES = 200

    def __init__(self):
        self.own: Optional[PrefixSet] = None
        self.ign: Optional[PrefixSet] = None
        self.cur: Dict[Tuple, list] = {}
        self.on = False

    def configure(self, enabled: bool, own_prefixes: List[str], ignore: List[str]):
        self.own = PrefixSet((p, 1) for p in own_prefixes or []) or None
        self.ign = PrefixSet((p, 1) for p in ignore or []) or None
        self.on = bool(enabled and self.own)

    def add(self, f):
        from flowproto import F_BYTES, F_DPORT, F_DST, F_PKTS, F_PROTO, F_SPORT, F_SRC, F_VER
        if f[F_PROTO] != 17:
            return
        sp = f[F_SPORT]
        svc = PORT_SERVICE.get(sp)
        if not svc or f[F_DPORT] == sp:
            return
        ver, src, dst = f[F_VER], f[F_SRC], f[F_DST]
        own = self.own
        if not own.match(ver, src) or own.match(ver, dst):
            return
        if self.ign and self.ign.match(ver, src):
            return
        k = (ver, src, svc)
        v = self.cur.get(k)
        if v is None:
            if len(self.cur) >= self.MAX_KEYS:
                return
            v = self.cur[k] = [0, 0, set()]
        v[0] += f[F_BYTES]
        v[1] += f[F_PKTS]
        if len(v[2]) < self.MAX_REMOTES:
            v[2].add(dst)

    def flush(self) -> List[dict]:
        cur, self.cur = self.cur, {}
        return [{"ip": ip_str(ver, src), "service": svc, "bytes": b, "pkts": p,
                 "remotes": [ip_str(ver, r) for r in list(rs)[:50]], "n_remotes": len(rs)}
                for (ver, src, svc), (b, p, rs) in cur.items()]
