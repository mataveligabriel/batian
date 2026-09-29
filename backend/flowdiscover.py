"""Descoberta de interfaces com flow: o que os roteadores estão exportando e ainda não está monitorado,
com nome/descrição via SNMP e sugestão de papel (trânsito, IX, PNI/CDN, cliente) pela descrição."""
import re
from typing import List, Optional, Tuple

_RULES = [
    ("ix", re.compile(r"(?i)\bix[\s._-]|\bix$|ix\.br|ixbr|\bptt\b|ptt[\s._-]|\bpix\b|route[- ]?server|\brs\d?\b")),
    ("cdn", re.compile(r"(?i)\bgg?c\b|google|netflix|\boca\b|facebook|\bmeta\b|\bfna\b|akamai|\bcdn\b|cache|cloudfront|amazon|\baws\b|"
                       r"microsoft|cloudflare|fastly|twitch|tiktok|bytedance")),
    ("pni", re.compile(r"(?i)\bpni\b|private[- ]?peer|peering[- ]?privad")),
    ("transito", re.compile(r"(?i)tr[aâ]nsito|transit|upstream|\bip[- ]?link|link[- ]?ip|operadora|\bcarrier\b|"
                            r"cirion|lumen|level ?3|telxius|embratel|algar|\bvivo\b|telef[oô]nica|\boi\b|\bctbc\b|mundivox|"
                            r"sparkle|\bgtt\b|cogent|hurricane|\bhe\.net|\btim\b|vogel|eletronet|v\.?tal|ufinet|americanet|"
                            r"forte[_ -]?ip|\bntt\b|arelion|telia|seaborn|angola cables|\bbrdigital|ligga|copel")),
    ("cliente", re.compile(r"(?i)client|customer|\bcust\b|\bolt\b|pppoe|\bbng\b|\bbras\b|cgnat|dedicad|corporativ|empresa")),
]
_SKIP = re.compile(r"(?i)^(null|inloop|loop|vlanif1$|meth|mgmt|management|console|nve|tunnel|virtual-template|dialer)")


def suggest_role(name: str, alias: str) -> Tuple[Optional[str], str]:
    """(papel sugerido ou None, motivo)."""
    text = f"{alias or ''} {name or ''}"
    for role, rx in _RULES:
        m = rx.search(alias or "") or (rx.search(name or "") if role == "ix" else None)
        if m:
            return role, f"“{m.group(0).strip()}” na descrição"
    return None, "sem pista na descrição"


def build(exporters: List[dict], devices: List[dict], caches: dict, monitored: set, min_bps: float) -> dict:
    """exporters: docs de flow_exporters; devices: equipamentos do usuário; caches: device_id -> interfaces SNMP;
    monitored: {(exporter, if_index)}. Devolve candidatos por equipamento e exportadores sem equipamento."""
    by_host = {}
    for d in devices:
        for h in (d.get("host"), d.get("flow_exporter")):
            if h:
                by_host.setdefault(h, d)
    out, orphans = [], []
    for e in exporters:
        ip = e["_id"]
        dev = by_host.get(ip) or by_host.get(e.get("src") or "")
        ifs = e.get("ifs") or {}
        if not dev:
            busy = [k for k, (a, b) in ifs.items() if (a or 0) + (b or 0) >= min_bps]
            if busy:
                orphans.append({"exporter": ip, "kind": e.get("kind"), "ifaces": len(busy), "last": e.get("last")})
            continue
        names = {int(i["index"]): i for i in (caches.get(dev["id"]) or [])}
        rows = []
        for k, v in ifs.items():
            try:
                idx = int(k)
            except ValueError:
                continue
            bin_, bout = (v + [0, 0])[:2] if isinstance(v, list) else (0, 0)
            if (ip, idx) in monitored or (bin_ or 0) + (bout or 0) < min_bps or idx == 0:
                continue
            info = names.get(idx) or {}
            name, alias = info.get("name") or f"ifIndex {idx}", info.get("alias") or ""
            if _SKIP.match(name):
                continue
            role, why = suggest_role(name, alias)
            rows.append({"exporter": ip, "if_index": idx, "if_name": name, "alias": alias, "speed_mbps": info.get("speed_mbps"),
                         "in_bps": bin_ or 0, "out_bps": bout or 0, "role": role, "why": why, "snmp": bool(info)})
        rows.sort(key=lambda r: -(r["in_bps"] + r["out_bps"]))
        if rows:
            out.append({"device_id": dev["id"], "device_name": dev.get("name"), "exporter": ip, "snmp_cached": bool(names),
                        "last": e.get("last"), "items": rows})
    out.sort(key=lambda d: -sum(r["in_bps"] + r["out_bps"] for r in d["items"]))
    return {"devices": out, "orphans": orphans, "total": sum(len(d["items"]) for d in out)}
