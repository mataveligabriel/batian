"""Mapa automático: dos equipamentos escolhidos, descobre os enlaces e monta o mapa (com o tráfego real medido).

Enlaces vêm de duas fontes, lidas por SNMP:
  1. sub-rede ponto a ponto (/29–/31) com uma ponta em cada equipamento (teopt.discover_links) — enlaces L3, inclusive
     Eth-Trunk/ae (a interface lógica, com o tráfego do bundle inteiro);
  2. vizinhos LLDP (LLDP-MIB): enlaces L2 sem IP (trunk entre switches, PTP de transporte), casando o sysName do
     vizinho com o nome/sysName dos equipamentos escolhidos e a porta remota com as interfaces dele.
Quando um par de equipamentos já tem enlace L3, os LLDP daquele par (portas físicas de um bundle) ficam de fora — a
não ser que se peça as portas físicas.

O desenho usa um layout de forças (determinístico) e o mapa é salvo com as interfaces de cada enlace: o monitor de
interfaces passa a coletar o tráfego delas sozinho (como em qualquer mapa).
"""
import math
import random
import re
from typing import Dict, List, Optional, Tuple

LLDP_LOC_PORT = "1.0.8802.1.1.2.1.3.7.1"     # lldpLocPortTable: 3 PortId, 4 PortDesc (índice: lldpLocPortNum)
LLDP_REM = "1.0.8802.1.1.2.1.4.1.1"          # lldpRemTable: índice timeMark.localPortNum.remIndex
REM_COLS = {6: "port_subtype", 7: "port_id", 8: "port_desc", 9: "sys_name"}


async def collect_lldp(client) -> List[dict]:
    """Vizinhos LLDP do equipamento: [{local_num, local_id, local_desc, sys_name, port_id, port_desc, port_subtype}]."""
    import netanalysis as NA
    loc = await NA._table(client, LLDP_LOC_PORT, {3: "id", 4: "desc"})
    rem = await NA._table(client, LLDP_REM, REM_COLS)
    out = []
    for idx, r in rem.items():
        parts = idx.split(".")
        if len(parts) < 3:
            continue
        num = parts[1]
        lp = loc.get(num) or {}
        out.append({"local_num": int(num) if num.isdigit() else None, "local_id": _s(lp.get("id")), "local_desc": _s(lp.get("desc")),
                    "sys_name": _s(r.get("sys_name")), "port_id": _s(r.get("port_id")), "port_desc": _s(r.get("port_desc")),
                    "port_subtype": r.get("port_subtype") if isinstance(r.get("port_subtype"), int) else None})
    return out


def _s(v) -> str:
    if not isinstance(v, str):
        return ""
    v = v.strip().strip("\x00")
    return v if v.isprintable() else ""


_ABBR = [(r"^gigabitethernet", "ge"), (r"^tengigabitethernet", "te"), (r"^xgigabitethernet", "xge"), (r"^25ge", "25ge"),
         (r"^100ge", "100ge"), (r"^fortygigabitethernet", "fo"), (r"^hundredgige?", "hu"), (r"^fastethernet", "fa"),
         (r"^ethernet", "eth"), (r"^eth-trunk", "eth-trunk"), (r"^port-channel", "po"), (r"^bundle-ether", "be")]


def norm_if(name: str) -> str:
    n = re.sub(r"\s+", "", (name or "").lower())
    for rx, ab in _ABBR:
        if re.match(rx, n):
            return re.sub(rx, ab, n)
    return n


def norm_host(name: str) -> str:
    n = (name or "").strip().lower()
    if re.fullmatch(r"[\d.]+", n):
        return n
    return n.split(".")[0]


def _find_if(ifaces: Dict[int, dict], *names: str, num: Optional[int] = None) -> Optional[int]:
    keys = [norm_if(x) for x in names if x]
    if keys:
        for idx, i in ifaces.items():
            cand = {norm_if(i.get("name") or ""), norm_if(i.get("descr") or "")}
            if cand & set(keys):
                return idx
    for x in names:                              # porta remota anunciada como número (ifIndex)
        if x and x.isdigit() and int(x) in ifaces:
            return int(x)
    if num is not None and num in ifaces:
        return num
    return None


def lldp_links(devs: Dict[str, dict], data: Dict[str, dict], lldp: Dict[str, List[dict]]) -> Tuple[List[dict], List[dict]]:
    """Enlaces a partir do LLDP de cada equipamento. Devolve (enlaces, vizinhos fora da seleção)."""
    by_name: Dict[str, str] = {}
    for did, dev in devs.items():
        for n in (dev.get("name"), dev.get("snmp_sys_name"), (data.get(did) or {}).get("sys_name"), dev.get("host")):
            if n:
                by_name.setdefault(norm_host(str(n)), did)
    full, partial = [], []
    outside: Dict[str, dict] = {}
    for did, nbrs in lldp.items():
        ifs_a = (data.get(did) or {}).get("ifaces") or {}
        for nb in nbrs:
            other = by_name.get(norm_host(nb["sys_name"]))
            if not other:
                if nb["sys_name"]:
                    o = outside.setdefault(norm_host(nb["sys_name"]), {"sys_name": nb["sys_name"], "seen_by": set(), "ports": set()})
                    o["seen_by"].add(devs[did]["name"])
                    o["ports"].add(nb["port_id"] or nb["port_desc"])
                continue
            if other == did:
                continue
            ia = _find_if(ifs_a, nb["local_desc"], nb["local_id"], num=nb["local_num"])
            if ia is None:
                continue
            ib = _find_if((data.get(other) or {}).get("ifaces") or {}, nb["port_id"], nb["port_desc"])
            (full if ib is not None else partial).append({"a": did, "a_idx": ia, "b": other, "b_idx": ib, "method": "lldp"})
    links, seen, ends = [], set(), set()
    for L in full:                                  # os dois lados do mesmo enlace viram um só
        k = frozenset({(L["a"], L["a_idx"]), (L["b"], L["b_idx"])})
        if k in seen:
            continue
        seen.add(k)
        ends.update(k)
        links.append(L)
    for L in partial:                               # porta remota não casou: só entra se o outro lado não trouxe o enlace
        if (L["a"], L["a_idx"]) in ends:
            continue
        ends.add((L["a"], L["a_idx"]))
        links.append(L)
    out = [{"sys_name": o["sys_name"], "seen_by": sorted(o["seen_by"]), "ports": sorted(p for p in o["ports"] if p)[:6]}
           for o in outside.values()]
    return links, sorted(out, key=lambda o: o["sys_name"].lower())


def merge(devs: Dict[str, dict], data: Dict[str, dict], ip_links: List[dict], ll_links: List[dict], physical: bool = False) -> List[dict]:
    """Lista final de enlaces no formato do mapa (a/b com índice, nome e velocidade da interface, tráfego medido)."""
    def iface(did, idx):
        return ((data.get(did) or {}).get("ifaces") or {}).get(idx) or {}

    out = []
    pairs_l3 = set()
    used = set()
    for L in ip_links:
        a_idx = next((i for i, f in (iface_map(data, L["a"])).items() if f.get("name") == L["a_if"]), None)
        b_idx = next((i for i, f in (iface_map(data, L["b"])).items() if f.get("name") == L["b_if"]), None)
        pairs_l3.add(frozenset({L["a"], L["b"]}))
        used.update({(L["a"], a_idx), (L["b"], b_idx)})
        out.append(_row(devs, L["a"], a_idx, iface(L["a"], a_idx), L["b"], b_idx, iface(L["b"], b_idx), "ip", L.get("net")))
    for L in ll_links:
        if not physical and frozenset({L["a"], L["b"]}) in pairs_l3:
            continue
        if (L["a"], L["a_idx"]) in used or (L["b_idx"] is not None and (L["b"], L["b_idx"]) in used):
            continue
        used.update({(L["a"], L["a_idx"]), (L["b"], L["b_idx"])})
        out.append(_row(devs, L["a"], L["a_idx"], iface(L["a"], L["a_idx"]), L["b"], L["b_idx"],
                        iface(L["b"], L["b_idx"]) if L["b_idx"] is not None else {}, "lldp", None))
    return out


def iface_map(data, did) -> Dict[int, dict]:
    return (data.get(did) or {}).get("ifaces") or {}


def _row(devs, a, a_idx, fa, b, b_idx, fb, method, net) -> dict:
    cap = min([c for c in (fa.get("speed_mbps"), fb.get("speed_mbps")) if c] or [0]) or None
    ab = fa.get("out_bps") if fa.get("out_bps") is not None else fb.get("in_bps")
    ba = fb.get("out_bps") if fb.get("out_bps") is not None else fa.get("in_bps")
    pct = (lambda v: round(v / (cap * 1e6) * 100, 1) if v is not None and cap else None)
    return {"id": f"{a[:6]}.{a_idx}-{b[:6]}.{b_idx}", "a": a, "b": b, "a_name": devs.get(a, {}).get("name", "?"),
            "b_name": devs.get(b, {}).get("name", "?"), "a_idx": a_idx, "b_idx": b_idx,
            "a_if": fa.get("name") or (f"ifIndex {a_idx}" if a_idx is not None else ""),
            "b_if": fb.get("name") or (f"ifIndex {b_idx}" if b_idx is not None else "?"),
            "a_alias": fa.get("alias") or "", "capacity_mbps": cap, "method": method, "net": net,
            "oper_a": fa.get("oper"), "oper_b": fb.get("oper"), "ab_bps": ab, "ba_bps": ba, "ab_pct": pct(ab), "ba_pct": pct(ba),
            "a_speed": fa.get("speed_mbps") or None, "b_speed": fb.get("speed_mbps") or None}


def layout(node_ids: List[str], links: List[Tuple[str, str]], width: float = 1400, height: float = 800,
           fixed: Optional[Dict[str, Tuple[float, float]]] = None, iters: int = 400) -> Dict[str, Tuple[float, float]]:
    """Layout de forças (Fruchterman–Reingold), sempre o mesmo resultado para a mesma entrada. `fixed` não se move."""
    fixed = fixed or {}
    n = len(node_ids)
    if not n:
        return {}
    rnd = random.Random(42)
    pos = {}
    for i, v in enumerate(sorted(node_ids)):
        if v in fixed:
            pos[v] = list(fixed[v])
        else:
            ang = 2 * math.pi * i / max(1, n)
            pos[v] = [width / 2 + width * 0.35 * math.cos(ang) + rnd.uniform(-5, 5), height / 2 + height * 0.35 * math.sin(ang) + rnd.uniform(-5, 5)]
    k = math.sqrt(width * height / n) * 0.75
    t = width / 8
    adj = [(a, b) for a, b in links if a in pos and b in pos and a != b]
    for it in range(iters):
        disp = {v: [0.0, 0.0] for v in pos}
        vs = list(pos)
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                a, b = vs[i], vs[j]
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d = max(0.01, math.hypot(dx, dy))
                f = k * k / d
                disp[a][0] += dx / d * f
                disp[a][1] += dy / d * f
                disp[b][0] -= dx / d * f
                disp[b][1] -= dy / d * f
        for a, b in adj:
            dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
            d = max(0.01, math.hypot(dx, dy))
            f = d * d / k
            disp[a][0] -= dx / d * f
            disp[a][1] -= dy / d * f
            disp[b][0] += dx / d * f
            disp[b][1] += dy / d * f
        for v in pos:
            if v in fixed:
                continue
            dx, dy = disp[v]
            d = max(0.01, math.hypot(dx, dy))
            pos[v][0] = min(width - 60, max(60, pos[v][0] + dx / d * min(d, t)))
            pos[v][1] = min(height - 50, max(50, pos[v][1] + dy / d * min(d, t)))
        t = max(1.0, t * 0.985)
    return {v: (round(p[0], 1), round(p[1], 1)) for v, p in pos.items()}


def build_map_items(links: List[dict], node_ids: List[str], existing: Optional[dict] = None, uid=None) -> Tuple[List[dict], List[dict], dict]:
    """Nós e enlaces no formato do mapa. Com `existing`, acrescenta só o que falta (mantém posições e enlaces)."""
    import os
    uid = uid or (lambda: os.urandom(4).hex())
    nodes = [dict(n) for n in (existing or {}).get("nodes", [])]
    maplinks = [dict(x) for x in (existing or {}).get("links", [])]
    node_of = {n.get("device_id"): n["id"] for n in nodes if n.get("device_id")}
    fixed = {n["id"]: (n["x"], n["y"]) for n in nodes}
    added_nodes = 0
    for did in node_ids:
        if did not in node_of:
            nid = uid()
            node_of[did] = nid
            nodes.append({"id": nid, "kind": "device", "device_id": did, "label": "", "x": 0, "y": 0})
            added_nodes += 1
    have = {(l["from"], (l.get("from_if") or {}).get("index"), l["to"], (l.get("to_if") or {}).get("index")) for l in maplinks}
    have |= {(c, d, a, b) for a, b, c, d in have}
    added_links = 0
    for L in links:
        fa, fb = node_of.get(L["a"]), node_of.get(L["b"])
        if not fa or not fb:
            continue
        if (fa, L["a_idx"], fb, L["b_idx"]) in have:
            continue
        fi = {"index": L["a_idx"], "name": L["a_if"], "speed_mbps": L.get("a_speed")} if L["a_idx"] is not None else None
        ti = {"index": L["b_idx"], "name": L["b_if"], "speed_mbps": L.get("b_speed")} if L["b_idx"] is not None else None
        maplinks.append({"id": uid(), "from": fa, "to": fb, "from_if": fi, "to_if": ti, "capacity_mbps": L.get("capacity_mbps"),
                         "label": L.get("a_alias") or "", "curve": None})
        have.add((fa, L["a_idx"], fb, L["b_idx"]))
        added_links += 1
    pos = layout([n["id"] for n in nodes], [(l["from"], l["to"]) for l in maplinks], fixed={k: v for k, v in fixed.items()})
    for n in nodes:
        if n["id"] not in fixed:
            n["x"], n["y"] = pos[n["id"]]
    return nodes, maplinks, {"added_nodes": added_nodes, "added_links": added_links}


__all__ = ["collect_lldp", "lldp_links", "merge", "layout", "build_map_items", "norm_if", "norm_host"]
