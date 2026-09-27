"""Flow: configuração, índices e consultas (usado pela API e pelo coletor).

Coleções:
  flow_ifaces   interfaces monitoradas {id, owner_id, device_id, exporter, if_index, if_name, key, role, label}
  flow_groups   conteúdos {id, owner_id, name, asns[], prefixes[]}
  flow_1m       {key, dir, ts, bytes, pkts, flows, g{grupo:[b,p]}, proto{}}           — totais exatos por minuto
  flow_5m       {key, dir, ts, bytes, pkts, d{dim:[[chave,b,p]…]}}                    — top-K por dimensão
  flow_1h       junção por hora das duas acima (consultas longas)
  flow_live     {_id:"live", at, ifaces{key:{in:{bps,pps},out:{…}}}}
  flow_exporters{_id: ip, kind, pps, fps, rate, last, ifs{ifIndex:[bps in, bps out]}, …}
  flow_attacks  {id, victim, type, status, start, end, peak_bps, peak_pps, ifaces[], src_as[], owners[]}
"""
import ipaddress
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import flowagg

DEFAULTS = {
    "netflow_port": 2055, "sflow_port": 6343,
    "retention_days": 7,          # minuto a minuto e dimensões de 5 min
    "hourly_days": 90,            # junção por hora
    "own_prefixes": [], "ignore_prefixes": [],
    "sampling": {},               # ip do exportador -> taxa fixa (quando o roteador não informa)
    "attack": dict(flowagg.ATTACK_DEFAULTS),
    "asn_auto": True,
}
ROLES = {"transito": "Trânsito", "pni": "PNI", "ix": "IX / PTT", "cdn": "CDN / cache", "cliente": "Cliente", "outro": "Outro"}
DIMS = {"sas": "AS de origem", "das": "AS de destino", "spfx": "Prefixo de origem", "dpfx": "Prefixo de destino",
        "sip": "IP de origem", "dip": "IP de destino", "proto": "Protocolo", "sport": "Porta de origem",
        "dport": "Porta de destino", "peer": "Interface par"}
PORT_NAME = {20: "FTP-data", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 67: "DHCP", 68: "DHCP", 69: "TFTP",
             80: "HTTP", 110: "POP3", 123: "NTP", 137: "NetBIOS", 143: "IMAP", 161: "SNMP", 179: "BGP", 389: "LDAP",
             443: "HTTPS", 445: "SMB", 465: "SMTPS", 500: "IKE", 514: "Syslog", 587: "Submission", 853: "DoT",
             993: "IMAPS", 995: "POP3S", 1194: "OpenVPN", 1433: "MSSQL", 1701: "L2TP", 1723: "PPTP", 1812: "RADIUS",
             1900: "SSDP", 3074: "Xbox", 3306: "MySQL", 3389: "RDP", 3478: "STUN", 4500: "IPsec NAT-T",
             5060: "SIP", 5222: "XMPP", 5228: "Google Play", 5353: "mDNS", 8080: "HTTP-alt", 8443: "HTTPS-alt",
             10001: "Ubiquiti", 11211: "Memcached", 27015: "Steam"}
STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400]
MAX_FINE_MIN = 2880               # até 48 h usa minuto/5 min; acima, a junção por hora


def iface_key(exporter: str, if_index: int) -> str:
    return f"{exporter}|{int(if_index)}"


async def get_settings(db) -> dict:
    doc = await db.config.find_one({"key": "flow"}, {"_id": 0}) or {}
    out = {**DEFAULTS, **{k: v for k, v in doc.items() if k != "key" and k in DEFAULTS}}
    out["attack"] = {**flowagg.ATTACK_DEFAULTS, **(doc.get("attack") or {})}
    return out


def clean_prefixes(items) -> List[str]:
    out = []
    for x in items or []:
        for part in str(x).replace(",", " ").replace(";", " ").split():
            try:
                n = ipaddress.ip_network(part.strip(), strict=False)
            except ValueError:
                raise ValueError(f"bloco inválido: {part}")
            if str(n) not in out:
                out.append(str(n))
    return out


def clean_asns(items) -> List[int]:
    out = []
    for x in items or []:
        for part in str(x).replace(",", " ").replace(";", " ").split():
            p = part.strip().upper().removeprefix("AS")
            if not p.isdigit() or not (0 < int(p) < 2 ** 32):
                raise ValueError(f"ASN inválido: {part}")
            if int(p) not in out:
                out.append(int(p))
    return out


async def ensure_ttl(db, coll: str, seconds: int):
    info = await db[coll].index_information()
    idx = info.get("ts_1")
    if idx is None:
        await db[coll].create_index("ts", expireAfterSeconds=seconds)
    elif idx.get("expireAfterSeconds") != seconds:
        await db.command("collMod", coll, index={"keyPattern": {"ts": 1}, "expireAfterSeconds": seconds})


async def ensure_indexes(db, s: dict):
    days = max(1, min(int(s.get("retention_days") or 7), 60))
    hdays = max(7, min(int(s.get("hourly_days") or 90), 730))
    for coll, sec in (("flow_1m", days * 86400), ("flow_5m", days * 86400), ("flow_1h", hdays * 86400)):
        await ensure_ttl(db, coll, sec)
        await db[coll].create_index([("key", 1), ("ts", 1)])
    await db.flow_attacks.create_index([("start", -1)])
    await db.flow_ifaces.create_index("owner_id")
    await db.flow_groups.create_index("owner_id")


# ---------------------------------------------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------------------------------------------
def pick_step(minutes: int, base: int) -> int:
    want = minutes * 60 / 300
    for s in STEPS:
        if s >= base and s >= want:
            return s
    return STEPS[-1]


def _pct(v: List[float], p: float) -> float:
    if not v:
        return 0.0
    v = sorted(v)
    k = (len(v) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _ts(d) -> float:
    t = d["ts"]
    if isinstance(t, str):
        t = datetime.fromisoformat(t)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


class _Matcher:
    """Filtro de valores de uma dimensão: ASNs, portas, protocolos ou blocos (prefixo/IP casam por bloco)."""

    def __init__(self, dim: str, values: list):
        self.dim = dim
        self.pfx = dim in ("spfx", "dpfx", "sip", "dip")
        self.cache: Dict[str, list] = {}
        if self.pfx:
            self.nets = []
            for v in values:
                try:
                    self.nets.append(ipaddress.ip_network(str(v).strip(), strict=False))
                except ValueError:
                    pass
            self.nets.sort(key=lambda b: -b.prefixlen)     # o bloco mais específico primeiro
        else:
            vals = set()
            for v in values:
                s = str(v).strip().upper().removeprefix("AS")
                if s.lstrip("-").isdigit():
                    vals.add(int(s))
                elif s in {n.upper() for n in flowagg.PROTO_NAME.values()}:
                    vals.add(next(k for k, n in flowagg.PROTO_NAME.items() if n.upper() == s))
            self.vals = vals

    def blocks(self, key) -> list:
        """Blocos informados que contêm a chave (vazio = não casa). Para dimensões numéricas devolve [chave]."""
        if not self.pfx:
            return [key] if key in self.vals else []
        hit = self.cache.get(key)
        if hit is None:
            try:
                n = ipaddress.ip_network(key, strict=False)
                hit = [str(b) for b in self.nets if b.version == n.version and
                       (n.subnet_of(b) or (b.subnet_of(n)))]
            except (ValueError, TypeError):
                hit = []
            self.cache[key] = hit
        return hit


async def query(db, *, keys: List[str], minutes: int, direction: str = "in", group_by: str = "interface",
                filt: Optional[dict] = None, top: int = 10, unit: str = "bps", group_ids: List[str] = (),
                by_block: bool = False, now: Optional[float] = None) -> dict:
    """Séries empilháveis + tabela (média, p95, máximo, total, participação)."""
    minutes = max(5, min(int(minutes), 90 * 1440))
    top = max(1, min(int(top), 30))
    now = now or datetime.now(timezone.utc).timestamp()
    filt = filt or {}
    fdim, fvals = filt.get("dim"), [v for v in (filt.get("values") or []) if str(v).strip()]
    if fdim and not fvals:
        fdim = None
    if group_by not in ("interface", "group", *DIMS):
        raise ValueError("agrupamento inválido")
    if fdim and fdim not in ("group", *DIMS):
        raise ValueError("filtro inválido")
    uses_dims = group_by in DIMS or (fdim in DIMS)
    if group_by in DIMS and fdim and fdim != group_by:
        raise ValueError("Nos detalhes cada dimensão é guardada separada: o filtro precisa ser da mesma dimensão do "
                         "agrupamento (ou agrupe por interface).")
    if group_by == "group" and fdim and fdim != "group":
        raise ValueError("Para ver conteúdos, filtre por conteúdo (ou agrupe por interface).")
    fine = minutes <= MAX_FINE_MIN
    coll = ("flow_5m" if uses_dims else "flow_1m") if fine else "flow_1h"
    base = (300 if uses_dims else 60) if fine else 3600
    step = pick_step(minutes, base)
    t_end = math.floor(now / step) * step
    t0 = t_end - minutes * 60
    t0 = math.floor(t0 / step) * step
    n = max(1, int((t_end - t0) // step))
    ts_list = [int((t0 + i * step) * 1000) for i in range(n)]
    dirs = ["in", "out"] if direction == "both" else [direction if direction in ("in", "out") else "in"]
    proj = {"_id": 0, "key": 1, "dir": 1, "ts": 1, "bytes": 1, "pkts": 1}
    dim = group_by if group_by in DIMS else (fdim if fdim in DIMS else None)
    if dim:
        proj[f"d.{dim}"] = 1
    if group_by == "group" or fdim == "group":
        proj["g"] = 1
    q = {"key": {"$in": list(keys)}, "ts": {"$gte": datetime.fromtimestamp(t0, timezone.utc),
                                           "$lt": datetime.fromtimestamp(t_end, timezone.utc)}}
    docs = await db[coll].find(q, proj).to_list(None)
    vi = 0 if unit == "bps" else 1
    mult = 8 if unit == "bps" else 1
    series: Dict[str, List[float]] = defaultdict(lambda: [0.0] * n)
    totals = [0.0] * n          # tudo que passou (para "Outros" e participação)
    tot_bytes: Dict[str, float] = defaultdict(float)
    matcher = _Matcher(fdim, fvals) if fdim in DIMS else None
    gsel = set(fvals) if fdim == "group" else set(group_ids)
    for d in docs:
        if d.get("dir") not in dirs:
            continue
        i = int((_ts(d) - t0) // step)
        if not 0 <= i < n:
            continue
        val = d.get("bytes" if vi == 0 else "pkts", 0)
        totals[i] += val
        k = d["key"]
        sfx = f"#{d['dir']}" if direction == "both" and group_by == "interface" else ""
        if group_by == "interface" and not fdim:
            series[k + sfx][i] += val
            tot_bytes[k + sfx] += d.get("bytes", 0)
        elif group_by == "interface" and fdim == "group":
            for g, (b, p) in (d.get("g") or {}).items():
                if g in gsel:
                    series[k + sfx][i] += b if vi == 0 else p
                    tot_bytes[k + sfx] += b
        elif group_by == "interface":
            for rk, b, p in (d.get("d") or {}).get(fdim, []):
                if matcher.blocks(rk):
                    series[k + sfx][i] += b if vi == 0 else p
                    tot_bytes[k + sfx] += b
        elif group_by == "group":
            for g, (b, p) in (d.get("g") or {}).items():
                if g in gsel:
                    series[g][i] += b if vi == 0 else p
                    tot_bytes[g] += b
        else:
            exp = k.split("|")[0]
            for rk, b, p in (d.get("d") or {}).get(group_by, []):
                if matcher:
                    hit = matcher.blocks(rk)
                    if not hit:
                        continue
                    names = hit[:1] if by_block and matcher.pfx else [rk]
                else:
                    names = [rk]
                sk = f"{exp}|{rk}" if group_by == "peer" else names[0]
                series[sk][i] += b if vi == 0 else p
                tot_bytes[sk] += b
    # escolhe as maiores e junta o resto em "Outros"
    order = sorted(series, key=lambda s: -sum(series[s]))
    keep = order if group_by in ("interface", "group") else order[:top]
    out_series = []
    fac = mult / step
    grand = sum(totals)
    for s in keep:
        vals = [v * fac for v in series[s]]
        out_series.append({"id": str(s), "raw": s, "values": vals})
    if group_by in DIMS and not fdim:
        rest = [max(0.0, totals[i] - sum(series[s][i] for s in keep)) * fac for i in range(n)]
        if any(v > 0 for v in rest):
            out_series.append({"id": "__other", "raw": None, "values": rest, "other": True})
    # participação: entre as interfaces (quanto de cada local) ou sobre todo o tráfego das interfaces escolhidas
    denom = sum(sum(series[s]) for s in keep) if group_by == "interface" else grand
    table = []
    for s in out_series:
        v = s["values"]
        total_units = sum(v) * step / mult
        table.append({"id": s["id"], "avg": sum(v) / n, "p95": _pct(v, 0.95), "max": max(v) if v else 0,
                      "last": v[-1] if v else 0, "total": total_units, "share": total_units / denom if denom else 0})
    return {"step": step, "ts": ts_list, "series": out_series, "table": table, "unit": unit, "source": coll,
            "direction": direction, "group_by": group_by, "total_all": grand * fac / n if n else 0}


def dim_label(dim: str, raw, asn_name=None, if_name=None) -> str:
    if raw is None:
        return "Outros"
    if dim in ("sas", "das"):
        if not raw:
            return "AS desconhecido / próprio"
        nm = asn_name(raw) if asn_name else ""
        return f"AS{raw} {nm}".strip()
    if dim == "proto":
        return flowagg.PROTO_NAME.get(raw, f"IP proto {raw}")
    if dim in ("sport", "dport"):
        if not raw:
            return "portas altas / efêmeras"
        return f"{raw} ({PORT_NAME[raw]})" if raw in PORT_NAME else str(raw)
    if dim == "peer":
        exp, idx = str(raw).split("|", 1) if "|" in str(raw) else ("", raw)
        nm = if_name(exp, int(idx)) if if_name else None
        return nm or f"ifIndex {idx}" + (f" ({exp})" if exp else "")
    return str(raw)


def fmt_bps(v: float) -> str:
    for u, d in (("Tb/s", 1e12), ("Gb/s", 1e9), ("Mb/s", 1e6), ("kb/s", 1e3)):
        if v >= d:
            return f"{v / d:.1f} {u}"
    return f"{v:.0f} b/s"


def fmt_pps(v: float) -> str:
    for u, d in (("Mpps", 1e6), ("kpps", 1e3)):
        if v >= d:
            return f"{v / d:.1f} {u}"
    return f"{v:.0f} pps"


def hour_floor(t: float) -> datetime:
    return datetime.fromtimestamp(t - t % 3600, timezone.utc)


async def rollup_hour(db, hour_start: datetime):
    """Junta minuto/5 min de uma hora em flow_1h (idempotente)."""
    import asyncio
    h1 = hour_start + timedelta(hours=1)
    q = {"ts": {"$gte": hour_start, "$lt": h1}}
    m1 = await db.flow_1m.find(q, {"_id": 0}).to_list(None)
    m5 = await db.flow_5m.find(q, {"_id": 0}).to_list(None)
    if not m1 and not m5:
        return 0
    acc: Dict[tuple, dict] = {}
    for d in m1:
        a = acc.setdefault((d["key"], d["dir"]), {"bytes": 0, "pkts": 0, "flows": 0, "g": {}, "proto": {}, "d5": []})
        a["bytes"] += d.get("bytes", 0)
        a["pkts"] += d.get("pkts", 0)
        a["flows"] += d.get("flows", 0)
        for g, (b, p) in (d.get("g") or {}).items():
            x = a["g"].setdefault(g, [0, 0])
            x[0] += b
            x[1] += p
        for pr, b in (d.get("proto") or {}).items():
            a["proto"][pr] = a["proto"].get(pr, 0) + b
    for d in m5:
        a = acc.setdefault((d["key"], d["dir"]), {"bytes": 0, "pkts": 0, "flows": 0, "g": {}, "proto": {}, "d5": []})
        a["d5"].append(d)
    docs = []
    for (k, dr), a in acc.items():
        merged = await asyncio.to_thread(flowagg.merge_dims, a.pop("d5"), 1) if a.get("d5") else {"d": {}}
        a.pop("d5", None)
        if not a["bytes"] and merged.get("bytes"):
            a["bytes"], a["pkts"] = merged["bytes"], merged["pkts"]
        docs.append({"key": k, "dir": dr, "ts": hour_start, **a, "d": merged.get("d", {})})
    await db.flow_1h.delete_many({"ts": hour_start})
    if docs:
        await db.flow_1h.insert_many(docs)
    return len(docs)
