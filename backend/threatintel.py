"""Threat intel no Flow: listas públicas de IPs maliciosos cruzadas com o tráfego.

O backend baixa as listas (a cada 6 h) e guarda em ti_lists; o coletor de flow carrega o conjunto, confere a origem e o
destino de cada flow e grava por minuto quem falou com quem (ti_hits). Saída de um host seu para C2/malware vira
incidente; entrada de scanners e atacantes fica só registrada (é ruído de fundo da internet).

Falso positivo clássico: o IP ruim faz varredura e o seu host RESPONDE (SYN-ACK, RST, ICMP, resposta de DNS). Pelo
flow isso parece "saída para IP ruim". Resposta é reconhecida pela porta (o host responde de uma porta de serviço
para a porta alta do scanner), pelos flags TCP (RST/SYN-ACK sem dados) e por ter vindo antes a entrada do mesmo par;
fica registrada como entrada, com a marca "respondeu".
"""
import ipaddress
import re
import time
import zlib
from typing import Dict, List, Optional, Tuple

from flowagg import PrefixSet, ip_str

CATEGORIES = {"c2": "Comando e controle (C2)", "malware": "Malware / botnet", "compromised": "Máquina comprometida",
              "attacker": "Atacante / força bruta", "scanner": "Scanner", "hijacked": "Bloco sequestrado (DROP)", "tor": "Saída Tor",
              "custom": "Lista própria"}
# categoria -> severidade quando um host SEU fala com o IP (saída)
OUT_SEVERITY = {"c2": "high", "malware": "high", "hijacked": "medium", "compromised": "medium", "attacker": "low",
                "scanner": "info", "tor": "info", "custom": "medium"}
FEEDS = [
    {"id": "feodo", "name": "abuse.ch Feodo Tracker (C2 de botnets)", "category": "c2", "enabled": True,
     "url": "https://feodotracker.abuse.ch/downloads/ipblocklist_recommended.txt"},
    {"id": "spamhaus_drop", "name": "Spamhaus DROP (IPv4)", "category": "hijacked", "enabled": True,
     "url": "https://www.spamhaus.org/drop/drop_v4.json"},
    {"id": "spamhaus_drop6", "name": "Spamhaus DROP (IPv6)", "category": "hijacked", "enabled": True,
     "url": "https://www.spamhaus.org/drop/drop_v6.json"},
    {"id": "et_compromised", "name": "Emerging Threats — IPs comprometidos", "category": "compromised", "enabled": True,
     "url": "https://rules.emergingthreats.net/blockrules/compromised-ips.txt"},
    {"id": "blocklist_de", "name": "blocklist.de (ataques SSH/mail/web)", "category": "attacker", "enabled": True,
     "url": "https://lists.blocklist.de/lists/all.txt"},
    {"id": "cins", "name": "CINS Army (ameaças ativas)", "category": "scanner", "enabled": True,
     "url": "https://cinsscore.com/list/ci-badguys.txt"},
    {"id": "dshield", "name": "DShield — blocos mais atacantes", "category": "scanner", "enabled": False,
     "url": "https://feeds.dshield.org/block.txt"},
    {"id": "tor_exit", "name": "Tor — nós de saída", "category": "tor", "enabled": False,
     "url": "https://check.torproject.org/torbulkexitlist"},
]
FEED_BY_ID = {f["id"]: f for f in FEEDS}
DEFAULTS = {"enabled": True, "feeds": [f["id"] for f in FEEDS if f["enabled"]], "custom": [], "manual": [],
            "alert_out": True, "alert_in_min_flows": 0, "ignore": [], "retention_days": 30,
            # ruído: saída para categorias que não são C2/malware só alerta com pelo menos N pacotes (soma do dia)
            "out_min_pkts": 10, "ignore_local": [], "auto_resolve_hours": 6,
            # bloqueio na borda: categorias exportadas na lista para o roteador (DROP e C2 não têm falso positivo prático)
            "block_categories": ["hijacked", "c2"], "block_token": ""}
STRONG = ("c2", "malware")                # 1 pacote já basta
BLOCKABLE = ("hijacked", "c2", "malware", "compromised", "attacker", "scanner", "tor", "custom")
MAX_PER_FEED = 300_000
_IP_RX = re.compile(r"([0-9a-fA-F:.]*[0-9a-fA-F](?:/\d{1,3})?)")
_DSHIELD = re.compile(r"^\s*(\d+\.\d+\.\d+\.\d+)\s+\d+\.\d+\.\d+\.\d+\s+(\d{1,2})\b")


def parse(text: str, limit: int = MAX_PER_FEED) -> List[str]:
    """Qualquer lista de IP/CIDR: um por linha, comentário com # ou ;, JSON por linha (Spamhaus), formato DShield."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s[0] in "#;":
            continue
        m = _DSHIELD.match(s)
        if m:
            cand = f"{m.group(1)}/{m.group(2)}"
        else:
            jm = re.search(r'"cidr"\s*:\s*"([^"]+)"', s)
            if jm:
                cand = jm.group(1)
            else:
                tok = s.split("#")[0].split(";")[0].strip().split()
                if not tok:
                    continue
                cand = tok[0].split(",")[0].strip('"')
                if re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", cand):
                    cand = cand.split(":")[0]
        try:
            n = ipaddress.ip_network(cand, strict=False)
        except ValueError:
            continue
        if n.is_private or n.is_loopback or n.is_multicast or n.is_unspecified or n.is_reserved or n.is_link_local:
            continue
        if n.prefixlen < (8 if n.version == 4 else 19):
            continue
        k = str(n)
        if k not in seen:
            seen.add(k)
            out.append(k)
            if len(out) >= limit:
                break
    return out


def pack(cidrs: List[str]) -> bytes:
    return zlib.compress("\n".join(cidrs).encode(), 6)


def unpack(blob: bytes) -> List[str]:
    return zlib.decompress(blob).decode().split("\n") if blob else []


def clean_settings(body: dict) -> dict:
    out = {**DEFAULTS, **{k: body[k] for k in DEFAULTS if k in body}}
    out["enabled"] = bool(out["enabled"])
    out["feeds"] = [f for f in out["feeds"] if f in FEED_BY_ID]
    custom = []
    for i, c in enumerate(out["custom"] or [], 1):
        url = str((c or {}).get("url") or "").strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Lista própria {i}: URL deve começar com http(s)://")
        cat = c.get("category") if c.get("category") in CATEGORIES else "custom"
        cid = re.sub(r"[^a-z0-9_]", "", str(c.get("id") or "")) or f"c{abs(hash(url)) % 10**8}"
        custom.append({"id": cid[:20], "name": str(c.get("name") or url)[:80], "url": url[:500], "category": cat})
    out["custom"] = custom[:10]
    manual = []
    for x in out["manual"] or []:
        x = str(x).strip()
        if not x:
            continue
        try:
            manual.append(str(ipaddress.ip_network(x, strict=False)))
        except ValueError:
            raise ValueError(f"IP/rede inválido na lista manual: {x}")
    out["manual"] = manual[:5000]
    ign = []
    for x in out["ignore"] or []:
        x = str(x).strip()
        if x:
            try:
                ign.append(str(ipaddress.ip_network(x, strict=False)))
            except ValueError:
                raise ValueError(f"IP/rede inválido em ignorar: {x}")
    out["ignore"] = ign[:2000]
    out["alert_out"] = bool(out["alert_out"])
    out["retention_days"] = max(3, min(365, int(out["retention_days"] or 30)))
    out["alert_in_min_flows"] = max(0, int(out["alert_in_min_flows"] or 0))
    out["out_min_pkts"] = max(1, min(100000, int(out["out_min_pkts"] or 1)))
    out["auto_resolve_hours"] = max(0, min(720, int(out["auto_resolve_hours"] or 0)))
    il = []
    for x in out["ignore_local"] or []:
        x = str(x).strip()
        if x:
            try:
                il.append(str(ipaddress.ip_network(x, strict=False)))
            except ValueError:
                raise ValueError(f"IP/rede inválido em hosts ignorados: {x}")
    out["ignore_local"] = il[:2000]
    out["block_categories"] = [c for c in out["block_categories"] or [] if c in BLOCKABLE]
    out["block_token"] = re.sub(r"[^A-Za-z0-9_-]", "", str(out["block_token"] or ""))[:64]
    return out


_SERVICE_PORTS = {20, 21, 22, 23, 25, 53, 80, 110, 123, 143, 161, 179, 389, 443, 445, 465, 500, 587, 993, 995, 1194, 1701, 1723,
                  1812, 1813, 3306, 3389, 4500, 5060, 5432, 8080, 8291, 8443, 8728, 8729}


def is_reply(proto: int, sport: int, dport: int, flags: int) -> bool:
    """Flow do host local para o IP ruim que é RESPOSTA a algo que veio de lá (não iniciativa do host)."""
    if proto in (1, 58):                          # ICMP/ICMPv6 saindo: echo reply, unreachable
        return True
    if proto == 6 and flags and dport >= 1024 and dport not in _SERVICE_PORTS:     # não é o seu host indo a um serviço
        if flags & 0x04 and not flags & 0x02 and not flags & 0x08:
            return True                           # RST sem SYN nem dados: porta fechada recusando
        if flags & 0x12 == 0x12 and not flags & 0x08 and not flags & 0x01:
            return True                           # SYN-ACK sem dados: porta aberta respondendo varredura
    if proto in (6, 17) and dport >= 1024 and (sport in _SERVICE_PORTS or sport < 1024):
        return True                               # de porta de serviço para porta alta
    return False


def out_alertable(h: dict, cfg: dict) -> bool:
    return h.get("category") in STRONG or (h.get("pkts") or 0) >= cfg.get("out_min_pkts", 1)


def blocklist(intel: "Intel", cats: List[str], own: List[str] = ()) -> List[str]:
    """Redes das listas nas categorias escolhidas, sem nada que esteja dentro (ou contenha) os seus prefixos."""
    ownn = []
    for p in own or []:
        try:
            ownn.append(ipaddress.ip_network(p, strict=False))
        except ValueError:
            pass
    out, seen = [], set()
    for i, fid in enumerate(intel.ids):
        if (intel.meta.get(fid) or {}).get("category", "custom") not in cats:
            continue
        for c in intel.src.get(fid) or []:
            if c in seen:
                continue
            seen.add(c)
            try:
                n = ipaddress.ip_network(c, strict=False)
            except ValueError:
                continue
            if any(n.version == o.version and (n.overlaps(o)) for o in ownn):
                continue
            if intel.ign and intel.ign.match(n.version, int(n.network_address)):
                continue
            out.append(str(n))
    return sorted(out, key=lambda x: (":" in x, ipaddress.ip_network(x)))


def render_blocklist(nets: List[str], fmt: str, name: str = "bastion-ti") -> str:
    if fmt == "routeros":
        lines = [f"# BastiON threat intel - {len(nets)} redes", "/ip firewall address-list"]
        for n in nets:
            if ":" in n:
                continue
            lines.append(f':do {{ add list={name} address={n} timeout=2d comment=bastion }} on-error={{ '
                         f'set [find list={name} address={n}] timeout=2d }}')
        v6 = [n for n in nets if ":" in n]
        if v6:
            lines.append("/ipv6 firewall address-list")
            for n in v6:
                lines.append(f':do {{ add list={name} address={n} timeout=2d comment=bastion }} on-error={{ '
                             f'set [find list={name} address={n}] timeout=2d }}')
        return "\n".join(lines) + "\n"
    if fmt == "juniper":
        pl = name.upper().replace("-", "_")
        return "\n".join([f"delete policy-options prefix-list {pl}"] + [f"set policy-options prefix-list {pl} {n}" for n in nets]) + "\n"
    if fmt == "cisco":
        v4 = [n for n in nets if ":" not in n]
        return "\n".join([f"no object-group network {name.upper()}", f"object-group network {name.upper()}"] +
                          [f" {ipaddress.ip_network(n).network_address} {ipaddress.ip_network(n).netmask}" for n in v4]) + "\n"
    return "\n".join(nets) + "\n"


def feed_list(cfg: dict) -> List[dict]:
    """Feeds ativos (catálogo + próprios), na ordem de bit."""
    return [FEED_BY_ID[f] for f in cfg.get("feeds") or [] if f in FEED_BY_ID] + list(cfg.get("custom") or [])


class Intel:
    """Conjunto de listas: IP -> feeds (bitmask) e categorias."""

    def __init__(self, lists: Dict[str, List[str]], meta: Dict[str, dict], manual: List[str] = (), ignore: List[str] = ()):
        self.ids = list(lists) + (["manual"] if manual else [])
        self.src = {**lists, **({"manual": list(manual)} if manual else {})}
        self.meta = {**meta, "manual": {"name": "Lista manual", "category": "custom"}}
        self.ps = PrefixSet()
        self.count = 0
        for i, fid in enumerate(self.ids):
            for c in (lists.get(fid) or (manual if fid == "manual" else [])):
                if self.ps.add(c, i):
                    self.count += 1
        self.ign = PrefixSet((c, 1) for c in ignore)

    def lookup_int(self, ver: int, ip: int) -> int:
        hits = self.ps.match(ver, ip)
        if not hits or (self.ign and self.ign.match(ver, ip)):
            return 0
        m = 0
        for i in hits:
            m |= 1 << i
        return m

    def lookup(self, ip: str) -> List[dict]:
        a = ipaddress.ip_address(ip)
        m = self.lookup_int(a.version, int(a))
        return self.describe(m)

    def describe(self, mask: int) -> List[dict]:
        out = []
        for i, fid in enumerate(self.ids):
            if mask & (1 << i):
                md = self.meta.get(fid) or {}
                out.append({"id": fid, "name": md.get("name", fid), "category": md.get("category", "custom")})
        return out

    def __bool__(self):
        return bool(self.count)


def worst_category(feeds: List[dict]) -> str:
    order = ["c2", "malware", "custom", "compromised", "hijacked", "attacker", "scanner", "tor"]
    cats = {f["category"] for f in feeds}
    return next((c for c in order if c in cats), "custom")


class Tracker:
    """No coletor: por minuto, (IP ruim, IP seu, sentido) -> bytes, pacotes, flows, portas."""
    MAX_KEYS = 50_000

    def __init__(self):
        self.intel: Optional[Intel] = None
        self.own: Optional[PrefixSet] = None
        self.cur: Dict[Tuple, list] = {}
        self.inports: Dict[Tuple, set] = {}
        self.on = False
        self.stats = {"flows": 0, "hits": 0}

    def configure(self, intel: Optional[Intel], own_prefixes: List[str]):
        self.intel = intel if intel else None
        self.own = PrefixSet((p, 1) for p in own_prefixes or []) or None
        self.on = bool(self.intel)

    def add(self, f):
        from flowproto import F_BYTES, F_DPORT, F_DST, F_FLAGS, F_PKTS, F_PROTO, F_SPORT, F_SRC, F_VER
        intel = self.intel
        ver = f[F_VER]
        ms = intel.lookup_int(ver, f[F_SRC])
        md = intel.lookup_int(ver, f[F_DST]) if not ms else 0
        if not ms and not md:
            return
        self.stats["hits"] += 1
        reply = 0
        if ms:          # IP ruim na origem -> entrando na sua rede
            bad, local, direction, mask, port = f[F_SRC], f[F_DST], "in", ms, f[F_DPORT]
        else:
            bad, local, direction, mask, port = f[F_DST], f[F_SRC], "out", md, f[F_DPORT]
        if self.own is not None and not self.own.match(ver, local):
            return                                  # trânsito que não é seu (ex.: cliente com IP próprio não listado)
        pk = (ver, bad, local)
        if direction == "in":
            ip_ = self.inports.get(pk)
            if ip_ is None and len(self.inports) < self.MAX_KEYS:
                ip_ = self.inports[pk] = set()
            if ip_ is not None and len(ip_) < 64:
                ip_.add((f[F_PROTO], f[F_DPORT]))
        elif is_reply(f[F_PROTO], f[F_SPORT], f[F_DPORT], f[F_FLAGS]) or (f[F_PROTO], f[F_SPORT]) in self.inports.get(pk, ()):
            direction, reply, port = "in", 1, f[F_SPORT]      # resposta do seu host: conta como entrada
        k = (ver, bad, local, direction)
        v = self.cur.get(k)
        if v is None:
            if len(self.cur) >= self.MAX_KEYS:
                return
            v = self.cur[k] = [0, 0, 0, mask, set(), 0]
        v[0] += f[F_BYTES]
        v[1] += f[F_PKTS]
        v[2] += 1
        v[3] |= mask
        v[5] += reply
        if len(v[4]) < 8:
            v[4].add(f"{f[F_PROTO]}/{port}")

    def flush(self) -> List[dict]:
        cur, self.cur = self.cur, {}
        inports, self.inports = self.inports, {}
        out = []
        for (ver, bad, local, direction), (b, p, n, mask, ports, reply) in cur.items():
            feeds = self.intel.describe(mask) if self.intel else []
            out.append({"bad_ip": ip_str(ver, bad), "local_ip": ip_str(ver, local), "dir": direction, "bytes": b, "pkts": p,
                        "flows": n, "feeds": [x["id"] for x in feeds], "category": worst_category(feeds), "ports": sorted(ports),
                        "replied": reply})
        return out


def day_key(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t or time.time()))
