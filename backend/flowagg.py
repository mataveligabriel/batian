"""Agregação dos flows (sem banco): o coletor chama add() para cada flow e os flush periodicamente.

- Totais por interface/direção a cada 1 min (+ conteúdos monitorados, exatos) e média móvel de 60 s para o "ao vivo".
- Dimensões (AS, prefixo, IP, protocolo, porta, interface par) a cada 5 min, guardando só os maiores (top-K).
- Detector de ataque por IP de destino em janelas de 10 s (média móvel de 30 s), com classificação.

Direção (igual ao Akvorado): flow amostrado na entrada (ou sem essa informação) conta "in" na interface de entrada
e "out" na de saída. Se o exportador também manda flows amostrados na saída (campo de direção = 1), ele passa para o
modo "por ponto de amostragem": entrada conta só "in" e saída conta só "out" — assim nada é contado duas vezes.
"""
import heapq
import ipaddress
import time
from collections import defaultdict, deque
from typing import Callable, Dict, List, Optional, Tuple

from flowproto import (F_BYTES, F_DAS, F_DIR, F_DPORT, F_DST, F_EXP, F_FLAGS, F_IN, F_OUT, F_PKTS, F_PROTO, F_SAS,
                       F_SPORT, F_SRC, F_VER)

PROTO_NAME = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP", 41: "IPv6-in-IP", 47: "GRE", 50: "ESP", 51: "AH",
              58: "ICMPv6", 89: "OSPF", 103: "PIM", 112: "VRRP", 132: "SCTP"}
AMP_PORTS = {19: "Chargen", 53: "DNS", 69: "TFTP", 111: "Portmap", 123: "NTP", 137: "NetBIOS", 161: "SNMP", 389: "CLDAP",
             520: "RIP", 1900: "SSDP", 3283: "ARD", 3702: "WS-Discovery", 5353: "mDNS", 10001: "Ubiquiti",
             11211: "Memcached", 37810: "DVR", 27015: "Steam", 3478: "STUN", 1434: "MSSQL", 0: "fragmentos"}
KNOWN_HIGH = {1194, 1701, 1723, 1812, 1813, 1900, 3074, 3389, 3478, 3702, 4500, 5060, 5061, 5222, 5228, 5353, 8080,
              8443, 8888, 10001, 11211, 27015, 37810, 1434, 3283}
TOPK = {"sas": 100, "das": 100, "spfx": 150, "dpfx": 150, "sip": 30, "dip": 30, "proto": 15, "sport": 25, "dport": 25,
        "peer": 25}
DIM_CAP = 40000        # acima disso, uma dimensão descarta a metade menor (mantém os pesados, memória limitada)
EXTERNAL_ROLES = ("transito", "pni", "ix", "cdn")
CONTENT_PORTS = {80, 443, 8080, 8443, 1935}
V6_48 = ~((1 << 80) - 1) & ((1 << 128) - 1)
ATTACK_DEFAULTS = {"enabled": True, "pps": 200_000, "bps": 2_000_000_000, "amp_bps": 500_000_000, "syn_pps": 100_000,
                   "min_windows": 2, "end_windows": 6, "avg_windows": 3}


def svc_port(p: int) -> int:
    """Porta como dimensão: conhecida fica; alta/efêmera vira 0."""
    return p if (p < 1024 or p in KNOWN_HIGH) else 0


def ip_str(ver: int, v: int) -> str:
    return str(ipaddress.IPv4Address(v)) if ver == 4 else str(ipaddress.IPv6Address(v))


def pfx_str(ver: int, v: int) -> str:
    if ver == 4:
        return f"{ipaddress.IPv4Address(v & 0xFFFFFF00)}/24"
    return f"{ipaddress.IPv6Address(v & V6_48)}/48"


class PrefixSet:
    """Casamento rápido IP -> rótulos, por tamanho de máscara (poucos tamanhos distintos)."""

    def __init__(self, items=()):
        self.by: Dict[Tuple[int, int], Dict[int, list]] = defaultdict(dict)
        self.lens: Dict[int, List[int]] = {4: [], 6: []}
        for cidr, label in items:
            self.add(cidr, label)

    def add(self, cidr: str, label) -> bool:
        try:
            n = ipaddress.ip_network(str(cidr).strip(), strict=False)
        except ValueError:
            return False
        ver, bits = n.version, (32 if n.version == 4 else 128)
        net = int(n.network_address) >> (bits - n.prefixlen) if n.prefixlen else 0
        self.by[(ver, n.prefixlen)].setdefault(net, []).append(label)
        if n.prefixlen not in self.lens[ver]:
            self.lens[ver].append(n.prefixlen)
            self.lens[ver].sort(reverse=True)
        return True

    def match(self, ver: int, ip: int) -> list:
        bits = 32 if ver == 4 else 128
        out = []
        for ln in self.lens[ver]:
            hit = self.by[(ver, ln)].get(ip >> (bits - ln) if ln else 0)
            if hit:
                out.extend(hit)
        return out

    def __bool__(self):
        return bool(self.lens[4] or self.lens[6])


def _inc(d: dict, k, b: int, p: int):
    v = d.get(k)
    if v is None:
        d[k] = [b, p]
    else:
        v[0] += b
        v[1] += p


def _prune(d: dict):
    if len(d) > DIM_CAP:
        keep = heapq.nlargest(DIM_CAP // 2, d.items(), key=lambda kv: kv[1][0])
        d.clear()
        d.update(keep)


def classify(proto_b: Dict[int, int], sport_p: Dict[int, int], syn_p: int, pkts: int) -> str:
    top_proto = max(proto_b, key=proto_b.get) if proto_b else 0
    if top_proto == 17:
        if sport_p:
            sp = max(sport_p, key=sport_p.get)
            if sport_p[sp] >= 0.5 * pkts:
                if sp == 0:
                    return "UDP fragmentado"
                if sp in AMP_PORTS:
                    return f"Amplificação {AMP_PORTS[sp]} (UDP/{sp})"
        return "UDP flood"
    if top_proto == 6:
        return "TCP SYN flood" if pkts and syn_p >= 0.5 * pkts else "TCP flood"
    if top_proto in (1, 58):
        return "ICMP flood"
    if top_proto == 47:
        return "GRE flood"
    return f"IP proto {top_proto} flood" if top_proto else "Volumétrico"


def format_5m(raw: Dict[Tuple[str, str], dict]) -> List[dict]:
    """Converte o bloco de 5 min (retirado por take_5m) em documentos com o top-K de cada dimensão."""
    docs = []
    for (k, d), x in raw.items():
        dims = {}
        for dim, dct in x["d"].items():
            top = heapq.nlargest(TOPK[dim], dct.items(), key=lambda kv: kv[1][0])
            if dim in ("spfx", "dpfx"):
                top = [[pfx_str(*key), v[0], v[1]] for key, v in top]
            elif dim in ("sip", "dip"):
                top = [[ip_str(*key), v[0], v[1]] for key, v in top]
            else:
                top = [[key, v[0], v[1]] for key, v in top]
            dims[dim] = top
        docs.append({"key": k, "dir": d, "bytes": x["bytes"], "pkts": x["pkts"], "d": dims})
    return docs


def merge_dims(docs: List[dict], k_mult: int = 2) -> dict:
    """Junta vários documentos de dimensões (ex.: 12 de 5 min -> 1 h) mantendo o top-K (um pouco maior)."""
    tot_b = tot_p = 0
    acc: Dict[str, dict] = defaultdict(dict)
    for doc in docs:
        tot_b += doc.get("bytes", 0)
        tot_p += doc.get("pkts", 0)
        for dim, rows in (doc.get("d") or {}).items():
            a = acc[dim]
            for k, b, p in rows:
                _inc(a, k, b, p)
    dims = {dim: [[k, v[0], v[1]] for k, v in heapq.nlargest(TOPK.get(dim, 50) * k_mult, a.items(), key=lambda kv: kv[1][0])]
            for dim, a in acc.items()}
    return {"bytes": tot_b, "pkts": tot_p, "d": dims}


class Aggregator:
    def __init__(self):
        self.monitored: Dict[Tuple[str, int], str] = {}   # (exportador, ifIndex) -> chave "exportador|ifIndex"
        self.roles: Dict[str, str] = {}                    # chave -> transito/pni/ix/cdn/cliente/outro
        self.groups = PrefixSet()
        self.group_asn: Dict[int, List[str]] = {}
        self.own = PrefixSet()
        self.ignore = PrefixSet()
        self.asn_lookup: Optional[Callable[[int, int], int]] = None
        self.attack = dict(ATTACK_DEFAULTS)
        self.m1: Dict[Tuple[str, str], dict] = {}
        self.m5: Dict[Tuple[str, str], dict] = {}
        self.w10: Dict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])
        self.live_hist: deque = deque(maxlen=6)
        self.win: Dict[Tuple[int, int], list] = {}        # contadores baratos por IP destino (janela atual)
        self.win_hist: deque = deque(maxlen=3)
        self.watch: set = set()                            # IPs quentes: coleta detalhes (proto, porta, AS…)
        self.detail: Dict[Tuple[int, int], dict] = {}
        self.victims: Dict[Tuple[int, int], dict] = {}     # ataques em andamento
        self.seen: Dict[Tuple[str, int], list] = {}        # (exportador, ifIndex) -> [bytes entrada, bytes saída]
        self.bidir: Dict[str, int] = {}                    # exportador -> tick em que mandou flow de saída
        self.tick = 0
        self.stats = {"flows": 0, "unmonitored": 0}

    # ---------- configuração ----------
    def configure(self, monitored: Dict[Tuple[str, int], str], roles: Dict[str, str], groups: List[dict],
                  own_prefixes: List[str], attack: Optional[dict] = None, ignore_prefixes: List[str] = ()):
        self.monitored = dict(monitored)
        self.roles = dict(roles)
        self.groups = PrefixSet()
        self.group_asn = defaultdict(list)
        for g in groups:
            for c in g.get("prefixes") or []:
                self.groups.add(c, g["id"])
            for a in g.get("asns") or []:
                try:
                    self.group_asn[int(str(a).upper().replace("AS", ""))].append(g["id"])
                except (TypeError, ValueError):
                    pass
        self.group_asn = dict(self.group_asn)
        self.own = PrefixSet([(c, True) for c in own_prefixes or []])
        self.ignore = PrefixSet([(c, True) for c in ignore_prefixes or []])
        self.attack = dict(ATTACK_DEFAULTS)
        if attack:
            self.attack.update({k: v for k, v in attack.items() if v is not None and k in ATTACK_DEFAULTS})
        n = max(1, int(self.attack["avg_windows"]))
        if self.win_hist.maxlen != n:
            self.win_hist = deque(self.win_hist, maxlen=n)

    # ---------- entrada ----------
    def _as(self, ver: int, ip: int, given: int) -> int:
        if given:
            return given
        if self.asn_lookup:
            try:
                return self.asn_lookup(ver, ip) or 0
            except Exception:
                return 0
        return 0

    def add(self, f: tuple):
        st = self.stats
        st["flows"] += 1
        exp, d, b, p = f[F_EXP], f[F_DIR], f[F_BYTES], f[F_PKTS]
        i_in, i_out = f[F_IN], f[F_OUT]
        seen = self.seen
        if i_in:
            s = seen.get((exp, i_in))
            if s is None:
                s = seen[(exp, i_in)] = [0, 0]
            s[0] += b
        if i_out:
            s = seen.get((exp, i_out))
            if s is None:
                s = seen[(exp, i_out)] = [0, 0]
            s[1] += b
        if d == 1:
            self.bidir[exp] = self.tick
            k_in = None
            k_out = self.monitored.get((exp, i_out))
        else:
            k_in = self.monitored.get((exp, i_in))
            k_out = None if exp in self.bidir else self.monitored.get((exp, i_out))
        if not k_in and not k_out:
            st["unmonitored"] += 1
            return
        ver, src, dst, pr = f[F_VER], f[F_SRC], f[F_DST], f[F_PROTO]
        sas, das = self._as(ver, src, f[F_SAS]), self._as(ver, dst, f[F_DAS])
        gids = ()
        if self.groups or self.group_asn:
            gids = set(self.groups.match(ver, src))
            gids.update(self.groups.match(ver, dst))
            gids.update(self.group_asn.get(sas, ()))
            gids.update(self.group_asn.get(das, ()))
        for key, direction, peer in ((k_in, "in", i_out), (k_out, "out", i_in)):
            if not key:
                continue
            kd = (key, direction)
            m = self.m1.get(kd)
            if m is None:
                m = self.m1[kd] = {"bytes": 0, "pkts": 0, "flows": 0, "g": {}, "proto": {}}
            m["bytes"] += b
            m["pkts"] += p
            m["flows"] += 1
            for g in gids:
                _inc(m["g"], g, b, p)
            m["proto"][pr] = m["proto"].get(pr, 0) + b
            w = self.w10[kd]
            w[0] += b
            w[1] += p
            x = self.m5.get(kd)
            if x is None:
                x = self.m5[kd] = {"bytes": 0, "pkts": 0, "d": {k: {} for k in TOPK}}
            x["bytes"] += b
            x["pkts"] += p
            dd = x["d"]
            _inc(dd["sas"], sas, b, p)
            _inc(dd["das"], das, b, p)
            _inc(dd["spfx"], (ver, src & (0xFFFFFF00 if ver == 4 else V6_48)), b, p)
            _inc(dd["dpfx"], (ver, dst & (0xFFFFFF00 if ver == 4 else V6_48)), b, p)
            _inc(dd["sip"], (ver, src), b, p)
            _inc(dd["dip"], (ver, dst), b, p)
            _inc(dd["proto"], pr, b, p)
            _inc(dd["sport"], svc_port(f[F_SPORT]), b, p)
            _inc(dd["dport"], svc_port(f[F_DPORT]), b, p)
            _inc(dd["peer"], peer, b, p)
        # detector: tráfego que entra (numa interface monitorada) em direção a um IP nosso
        cfg = self.attack
        if k_in and cfg["enabled"]:
            if self.own.match(ver, dst) if self.own else self.roles.get(k_in, "transito") in EXTERNAL_ROLES:
                if not (self.ignore and self.ignore.match(ver, dst)):
                    vk = (ver, dst)
                    c = self.win.get(vk)
                    if c is None:
                        if len(self.win) >= 1_000_000:
                            c = None
                        else:
                            c = self.win[vk] = [0, 0, 0, 0]
                    if c is not None:
                        sp, fl = f[F_SPORT], f[F_FLAGS]
                        # resposta de conteúdo (HTTPS/HTTP com ACK, QUIC) não conta no limite de volume:
                        # um IP de CGNAT ou cache recebe vários Gb/s legítimos de Google, Meta, Netflix…
                        if not ((pr == 6 and sp in CONTENT_PORTS and fl & 0x10) or (pr == 17 and sp == 443)):
                            c[0] += b
                            c[1] += p
                        if pr == 17 and sp in AMP_PORTS:
                            c[2] += b
                        elif pr == 6 and fl & 0x02 and not fl & 0x10:
                            c[3] += p
                        if vk in self.watch:
                            v = self.detail.get(vk)
                            if v is None:
                                v = self.detail[vk] = {"b": 0, "p": 0, "syn": 0, "proto": {}, "sport": {}, "dport": {},
                                                       "sas": {}, "if": set()}
                            v["b"] += b
                            v["p"] += p
                            if pr == 6 and fl & 0x02 and not fl & 0x10:
                                v["syn"] += p
                            v["proto"][pr] = v["proto"].get(pr, 0) + b
                            v["sport"][sp] = v["sport"].get(sp, 0) + p
                            dp = f[F_DPORT]
                            v["dport"][dp] = v["dport"].get(dp, 0) + p
                            v["sas"][sas] = v["sas"].get(sas, 0) + b
                            v["if"].add(k_in)
        if st["flows"] % 20000 == 0:
            for x in self.m5.values():
                for dct in x["d"].values():
                    _prune(dct)

    # ---------- saída ----------
    def flush_live(self, window_sec: float = 10.0) -> Dict[str, dict]:
        """Média móvel (até 60 s) por interface/direção — suaviza o 'active timeout' do NetFlow."""
        self.live_hist.append(self.w10)
        self.w10 = defaultdict(lambda: [0, 0])
        span = len(self.live_hist) * window_sec
        acc: Dict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])
        for w in self.live_hist:
            for kd, (b, p) in w.items():
                a = acc[kd]
                a[0] += b
                a[1] += p
        out = {}
        for (key, direction), (b, p) in acc.items():
            out.setdefault(key, {})[direction] = {"bps": b * 8 / span, "pps": p / span}
        return out

    def flush_1m(self) -> List[dict]:
        docs = [{"key": k, "dir": d, "bytes": m["bytes"], "pkts": m["pkts"], "flows": m["flows"],
                 "g": {g: v for g, v in m["g"].items()}, "proto": {str(p): v for p, v in m["proto"].items()}}
                for (k, d), m in self.m1.items()]
        self.m1 = {}
        return docs

    def take_5m(self) -> Dict[Tuple[str, str], dict]:
        raw, self.m5 = self.m5, {}
        return raw

    def flush_5m(self) -> List[dict]:
        return format_5m(self.take_5m())

    def take_seen(self) -> Dict[Tuple[str, int], list]:
        s, self.seen = self.seen, {}
        return s

    def is_bidir(self, exporter: str) -> bool:
        return exporter in self.bidir

    def attack_tick(self, window_sec: float = 10.0, now: Optional[float] = None) -> List[Tuple[str, dict]]:
        """Fecha a janela de 10 s e devolve eventos ('start'|'update'|'end', ataque)."""
        now = now or time.time()
        self.tick += 1
        for e in [e for e, t in self.bidir.items() if self.tick - t > 60]:   # 10 min sem flow de saída
            del self.bidir[e]
        cfg = self.attack
        self.win_hist.append(self.win)
        self.win = {}
        detail, self.detail = self.detail, {}
        span = len(self.win_hist) * window_sec
        tot: Dict[Tuple[int, int], list] = {}
        for w in self.win_hist:
            for k, c in w.items():
                t = tot.get(k)
                if t is None:
                    tot[k] = list(c)
                else:
                    t[0] += c[0]
                    t[1] += c[1]
                    t[2] += c[2]
                    t[3] += c[3]
        hot = {}
        for k, t in tot.items():
            bps, pps = t[0] * 8 / span, t[1] / span
            if pps >= cfg["pps"] or bps >= cfg["bps"] or t[2] * 8 / span >= cfg["amp_bps"] or t[3] / span >= cfg["syn_pps"]:
                hot[k] = (bps, pps)
        if len(hot) > 1000:     # ataque espalhado: acompanha só os 1000 maiores
            hot = dict(heapq.nlargest(1000, hot.items(), key=lambda kv: kv[1][0]))
        self.watch = set(hot)
        events = []
        for vk, (bps, pps) in hot.items():
            st = self.victims.get(vk)
            if st is None:
                st = self.victims[vk] = {"victim": ip_str(*vk), "start": now, "windows": 0, "cold": 0, "alerted": False,
                                         "peak_bps": 0, "peak_pps": 0, "ifaces": set(), "src_as": {}, "proto": {},
                                         "sport": {}, "dport": {}, "syn": 0, "p": 0, "type": ""}
            st["windows"] += 1
            st["cold"] = 0
            st["last"] = now
            st["peak_bps"] = max(st["peak_bps"], bps)
            st["peak_pps"] = max(st["peak_pps"], pps)
            st["cur_bps"], st["cur_pps"] = bps, pps
            v = detail.get(vk)
            if v:
                st["ifaces"] |= v["if"]
                st["syn"] += v["syn"]
                st["p"] += v["p"]
                for src_d, dst_d in ((v["proto"], st["proto"]), (v["sport"], st["sport"]), (v["dport"], st["dport"]),
                                     (v["sas"], st["src_as"])):
                    for a, n in src_d.items():
                        dst_d[a] = dst_d.get(a, 0) + n
                for dct in (st["sport"], st["dport"], st["src_as"]):
                    if len(dct) > 2000:
                        keep = heapq.nlargest(500, dct.items(), key=lambda kv: kv[1])
                        dct.clear()
                        dct.update(keep)
            if st["p"]:
                st["type"] = classify(st["proto"], st["sport"], st["syn"], st["p"])
            if not st["alerted"] and st["windows"] >= cfg["min_windows"]:
                st["alerted"] = True
                events.append(("start", self._pub(st)))
            elif st["alerted"]:
                events.append(("update", self._pub(st)))
        for vk in list(self.victims):
            if vk in hot:
                continue
            st = self.victims[vk]
            st["cold"] += 1
            t = tot.get(vk)                          # abaixo do limite, mas ainda mostra quanto está chegando
            st["cur_bps"], st["cur_pps"] = (t[0] * 8 / span, t[1] / span) if t else (0, 0)
            if not st["alerted"] or st["cold"] >= cfg["end_windows"]:
                if st["alerted"]:
                    st["end"] = now
                    events.append(("end", self._pub(st)))
                del self.victims[vk]
            else:
                events.append(("update", self._pub(st)))
        return events

    @staticmethod
    def _pub(st: dict) -> dict:
        top = lambda d, n: [[a, b] for a, b in heapq.nlargest(n, d.items(), key=lambda kv: kv[1])]   # noqa: E731
        return {"victim": st["victim"], "type": st["type"] or "Volumétrico", "start": st["start"], "end": st.get("end"),
                "peak_bps": st["peak_bps"], "peak_pps": st["peak_pps"], "cur_bps": st.get("cur_bps", 0),
                "cur_pps": st.get("cur_pps", 0), "ifaces": sorted(st["ifaces"]), "src_as": top(st["src_as"], 8),
                "sport": top(st["sport"], 5), "dport": top(st["dport"], 5),
                "proto": [[a, b] for a, b in sorted(st["proto"].items(), key=lambda kv: -kv[1])[:4]]}
