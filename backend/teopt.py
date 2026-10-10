"""Engenharia de tráfego: topologia OSPF + carga dos enlaces → caminhos (SPF/CSPF), simulação e otimização de custos.

Fontes da topologia:
  - equipamentos escolhidos: lê por SNMP (IF-MIB, IP-MIB, OSPF-MIB e contadores de octetos em duas leituras) e liga
    as interfaces que estão na mesma sub-rede ponto a ponto (/29 a /31) → enlace com custo OSPF de cada lado,
    capacidade e tráfego medido em cada sentido;
  - ou a última análise de um mapa (enlaces já desenhados e monitorados).

Modelo de tráfego (sem matriz de tráfego): o que passa hoje em cada sentido de um enlace é tratado como demanda entre
as pontas e, ao mudar custos/derrubar enlaces, o desvio é estimado pelo SPF com ECMP (netanalysis.simulate).
É uma estimativa de 1ª ordem — boa para comparar alternativas, não para prever o número exato.
"""
import asyncio
import heapq
import ipaddress
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import netanalysis as NA
import snmp_service as S

IF_HC_IN_OCT = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT_OCT = "1.3.6.1.2.1.31.1.1.1.10"


# ---------- coleta por SNMP ----------
async def _octets(client) -> Dict[int, Tuple[Optional[int], Optional[int]]]:
    out: Dict[int, list] = defaultdict(lambda: [None, None])
    for k, base in ((0, IF_HC_IN_OCT), (1, IF_HC_OUT_OCT)):
        for oid, v in (await NA._walk(client, base)).items():
            i = S._idx(oid, base)
            if i is not None and isinstance(v, int):
                out[i][k] = v
    return {i: tuple(v) for i, v in out.items()}


async def collect(client, sample_sec: float = 10) -> dict:
    """Leitura de um equipamento para a engenharia de tráfego (usa a coleta da análise de rede + octetos)."""
    t0 = time.monotonic()
    o0 = await _octets(client)
    d = await NA.collect_device(client, sample_sec=sample_sec)
    t1 = time.monotonic()
    o1 = await _octets(client)
    dt = max(0.5, t1 - t0)
    for i, iface in (d.get("ifaces") or {}).items():
        a, b = o0.get(i), o1.get(i)
        if a and b:
            iface["in_bps"] = (NA._rate(a[0], b[0], dt, 2 ** 64) or 0) * 8 if a[0] is not None and b[0] is not None else None
            iface["out_bps"] = (NA._rate(a[1], b[1], dt, 2 ** 64) or 0) * 8 if a[1] is not None and b[1] is not None else None
    d.pop("bgp", None)
    return d


def discover_links(devs: Dict[str, dict], data: Dict[str, dict]) -> Tuple[List[dict], List[dict]]:
    """Liga as interfaces de equipamentos diferentes que estão na mesma sub-rede ponto a ponto (/29–/31)."""
    by_net: Dict[str, List[dict]] = defaultdict(list)
    for did, d in data.items():
        if not d.get("ok"):
            continue
        ospf = (d.get("ospf") or {}).get("ifaces") or {}
        for idx, iface in (d.get("ifaces") or {}).items():
            for cidr in iface.get("ips") or []:
                try:
                    ipi = ipaddress.IPv4Interface(cidr)
                except ValueError:
                    continue
                if not 29 <= ipi.network.prefixlen <= 31 or ipi.ip.is_loopback:
                    continue
                by_net[str(ipi.network)].append({"dev": did, "idx": idx, "ip": str(ipi.ip), "iface": iface,
                                                 "ospf": ospf.get(idx)})
    links, notes = [], []
    for net, ends in sorted(by_net.items()):
        devset = {e["dev"] for e in ends}
        if len(ends) != 2 or len(devset) != 2:
            if len(devset) > 2:
                notes.append({"net": net, "note": "sub-rede com mais de 2 equipamentos (broadcast) — fora do cálculo"})
            continue
        x, y = sorted(ends, key=lambda e: (devs.get(e["dev"], {}).get("name") or ""))
        ix, iy = x["iface"], y["iface"]
        cap = min([c for c in (ix.get("speed_mbps"), iy.get("speed_mbps")) if c] or [0]) or None
        ab = ix.get("out_bps") if ix.get("out_bps") is not None else iy.get("in_bps")
        ba = iy.get("out_bps") if iy.get("out_bps") is not None else ix.get("in_bps")
        ox, oy = x["ospf"] or {}, y["ospf"] or {}
        L = {"id": f"{x['dev'][:8]}.{x['idx']}-{y['dev'][:8]}.{y['idx']}", "net": net,
             "a": x["dev"], "b": y["dev"], "a_name": devs.get(x["dev"], {}).get("name", "?"), "b_name": devs.get(y["dev"], {}).get("name", "?"),
             "a_if": ix.get("name"), "b_if": iy.get("name"), "a_ip": x["ip"], "b_ip": y["ip"],
             "a_type": devs.get(x["dev"], {}).get("device_type"), "b_type": devs.get(y["dev"], {}).get("device_type"),
             "area_a": ox.get("area"), "area_b": oy.get("area"),
             "capacity_mbps": cap, "cost_ab": ox.get("cost"), "cost_ba": oy.get("cost"),
             "oper_a": ix.get("oper"), "oper_b": iy.get("oper"), "adjacency": None,
             "ab_bps": ab, "ba_bps": ba}
        L["ab_pct"] = round(ab / (cap * 1e6) * 100, 1) if ab is not None and cap else None
        L["ba_pct"] = round(ba / (cap * 1e6) * 100, 1) if ba is not None and cap else None
        if L["cost_ab"] is None or L["cost_ba"] is None:
            notes.append({"net": net, "note": f"{L['a_name']} {L['a_if']} ↔ {L['b_name']} {L['b_if']}: sem OSPF nas duas pontas — fora do SPF"})
        links.append(L)
    return links, notes


def from_analysis(report: dict) -> Tuple[List[dict], Dict[str, str]]:
    """Enlaces da análise de um mapa (já têm custo, capacidade e tráfego)."""
    links, names = [], {}
    for L in report.get("links") or []:
        x = {k: L.get(k) for k in ("id", "a", "b", "a_name", "b_name", "a_if", "b_if", "capacity_mbps", "cost_ab", "cost_ba",
                                    "oper_a", "oper_b", "adjacency", "ab_bps", "ba_bps", "ab_pct", "ba_pct", "area_a", "area_b")}
        x.update({"a_ip": None, "b_ip": None, "a_type": L.get("a_type"), "b_type": L.get("b_type"), "net": None})
        names[x["a"]], names[x["b"]] = x["a_name"], x["b_name"]
        links.append(x)
    return links, names


# ---------- utilização ----------
def utilization(links: List[dict], pred: Optional[dict] = None) -> List[dict]:
    """[{link, dir, from, to, bps, pct}] dos sentidos com capacidade conhecida, do mais cheio para o mais vazio."""
    rows = []
    for L in links:
        cap = (L.get("capacity_mbps") or 0) * 1e6
        for d in ("ab", "ba"):
            v = (pred or {}).get(L["id"], {}).get(f"{d}_bps") if pred else L.get(f"{d}_bps")
            if v is None:
                continue
            rows.append({"link": L["id"], "dir": d, "from": L["a_name"] if d == "ab" else L["b_name"],
                         "to": L["b_name"] if d == "ab" else L["a_name"], "bps": v,
                         "pct": round(v / cap * 100, 1) if cap else None})
    return sorted(rows, key=lambda r: -(r["pct"] or -1))


def _score(pred: dict, links: List[dict], target: float) -> Tuple[float, float]:
    worst, excess = 0.0, 0.0
    for L in links:
        cap = (L.get("capacity_mbps") or 0) * 1e6
        if not cap:
            continue
        for d in ("ab", "ba"):
            p = (pred[L["id"]].get(f"{d}_bps") or 0) / cap * 100
            worst = max(worst, p)
            excess += max(0.0, p - target)
    return round(worst, 2), round(excess, 2)


def _alt_cost(links: List[dict], costs: dict, skip: str, s: str, t: str) -> Optional[int]:
    g = NA.build_graph(links).without({skip}, set(), costs)
    return g.dijkstra(s).get(t)


def _alt_path_links(links: List[dict], costs: dict, skip: str, s: str, t: str) -> List[Tuple[str, str]]:
    g = NA.build_graph(links).without({skip}, set(), costs)
    dist = g.all_dist()
    return list(g.route(dist, s, t).keys())


# ---------- otimização de custos OSPF ----------
def optimize(links: List[dict], target: float = 80.0, max_changes: int = 4, symmetric: bool = True,
             min_cost: int = 1, max_cost: int = 65535) -> dict:
    """Busca gulosa: a cada rodada ataca o sentido mais cheio (acima da meta) testando
    (a) subir o custo dele (empate com o caminho alternativo = divide por ECMP; um acima = desvia tudo)
    (b) baixar o custo de enlaces folgados do caminho alternativo. Fica com o que mais reduz o pico,
    sem isolar ninguém. Devolve as mudanças, a carga prevista antes/depois e os comandos por equipamento."""
    NA.build_graph(links)
    byid = {L["id"]: L for L in links}
    costs: Dict[Tuple[str, str], int] = {}
    base = NA.simulate(links, costs={}, count_changes=False)
    cur_score = _score(base["links"], links, target)
    steps = []
    tried = set()
    for _ in range(max(1, min(max_changes, 10))):
        pred = NA.simulate(links, costs=costs, count_changes=False)["links"]
        hot = None
        for L in links:
            cap = (L.get("capacity_mbps") or 0) * 1e6
            if not cap or not L.get("in_spf"):
                continue
            for d in ("ab", "ba"):
                p = (pred[L["id"]].get(f"{d}_bps") or 0) / cap * 100
                if p > target and (hot is None or p > hot[2]):
                    hot = (L, d, p)
        if not hot:
            break
        L, d, p = hot
        s, t = (L["a"], L["b"]) if d == "ab" else (L["b"], L["a"])
        cur = costs.get((L["id"], d), L[f"cost_{d}"])
        alt = _alt_cost(links, costs, L["id"], s, t)
        cands: List[Dict[Tuple[str, str], int]] = []

        def setc(lid, dd, val, into):
            val = max(min_cost, min(max_cost, int(val)))
            into[(lid, dd)] = val
            if symmetric:
                into[(lid, "ba" if dd == "ab" else "ab")] = val
        if alt is not None:
            for val in sorted({alt, alt + 1, int(cur * 1.5) + 1, cur * 2, cur * 3}):
                if val > cur:
                    c = dict(costs)
                    setc(L["id"], d, val, c)
                    cands.append(c)
            # baixar custos no caminho alternativo (enlaces com folga)
            for key in _alt_path_links(links, costs, L["id"], s, t):
                lid, dd = key
                M = byid[lid]
                mc = costs.get((lid, dd), M[f"cost_{dd}"])
                capm = (M.get("capacity_mbps") or 0) * 1e6
                mp = (pred[lid].get(f"{dd}_bps") or 0) / capm * 100 if capm else 100
                if mp > target * 0.6 or mc <= min_cost:
                    continue
                gap = alt - cur
                for val in sorted({mc - gap, mc - gap - 1}):
                    if min_cost <= val < mc:
                        c = dict(costs)
                        setc(lid, dd, val, c)
                        cands.append(c)
        best = None
        for c in cands:
            sig = tuple(sorted(c.items()))
            if sig in tried:
                continue
            tried.add(sig)
            r = NA.simulate(links, costs=c, count_changes=False)
            if r["isolated_pairs"]:
                continue
            sc = _score(r["links"], links, target)
            if sc < cur_score and (best is None or sc < best[0]):
                best = (sc, c)
        if not best:
            steps.append({"stuck": True, "link": L["id"], "dir": d,
                          "why": f"{L['a_name'] if d == 'ab' else L['b_name']} → {L['b_name'] if d == 'ab' else L['a_name']} "
                                 f"({p:.0f}%): nenhuma mudança de custo reduz o pico sem sobrecarregar outro caminho"
                                 + (" — não há caminho alternativo" if alt is None else "")})
            break
        changed = {k: v for k, v in best[1].items() if costs.get(k) != v}
        steps.append({"changes": [{"link": k[0], "dir": k[1], "old": costs.get(k, byid[k[0]][f"cost_{k[1]}"]), "new": v}
                                  for k, v in sorted(changed.items())],
                      "peak_before": cur_score[0], "peak_after": best[0][0],
                      "reason": f"aliviar {L['a_name'] if d == 'ab' else L['b_name']} → {L['b_name'] if d == 'ab' else L['a_name']} ({p:.0f}%)"})
        costs, cur_score = best[1], best[0]

    final = NA.simulate(links, costs=costs, count_changes=True)
    changes = []
    for (lid, dd), v in sorted(costs.items()):
        M = byid[lid]
        old = M[f"cost_{dd}"]
        if v == old:
            continue
        dev = M["a"] if dd == "ab" else M["b"]
        changes.append({"link": lid, "dir": dd, "device_id": dev, "device": M["a_name"] if dd == "ab" else M["b_name"],
                        "iface": M["a_if"] if dd == "ab" else M["b_if"], "area": M["area_a"] if dd == "ab" else M["area_b"],
                        "type": M["a_type"] if dd == "ab" else M["b_type"], "old": old, "new": v,
                        "toward": M["b_name"] if dd == "ab" else M["a_name"]})
    return {"target": target, "before": utilization(links, base["links"]), "after": utilization(links, final["links"]),
            "peak_before": _score(base["links"], links, target)[0], "peak_after": _score(final["links"], links, target)[0],
            "steps": steps, "changes": changes, "changed_pairs": final["changed_pairs"],
            "commands": commands(changes)}


# ---------- comandos ----------
def _cost_cmds(vendor: Optional[str], iface: str, cost: int, area: Optional[str]) -> List[str]:
    v = (vendor or "").lower()
    if v == "huawei":
        return ["system-view", f"interface {iface}", f"ospf cost {cost}", "quit"]
    if v == "juniper":
        return [f"set protocols ospf area {area or '0.0.0.0'} interface {iface} metric {cost}", "commit"]
    if v in ("cisco", "zte"):
        return ["configure terminal", f"interface {iface}", f"ip ospf cost {cost}", "end"]
    if v == "datacom":
        return ["config", f"router ospf 1 area {area or '0'} interface {iface} cost {cost}", "commit", "end"]
    if v == "mikrotik":
        return [f'/routing ospf interface-template set [find interfaces="{iface}"] cost={cost}']
    return [f"# {iface}: custo OSPF {cost} (fabricante não reconhecido — ajuste o comando)"]


def commands(changes: List[dict]) -> List[dict]:
    """Agrupa as mudanças por equipamento: [{device_id, device, type, lines}] (prontos para virar roteiro)."""
    by: Dict[str, dict] = {}
    for c in changes:
        e = by.setdefault(c["device_id"], {"device_id": c["device_id"], "device": c["device"], "type": c["type"], "lines": []})
        cmds = _cost_cmds(c["type"], c["iface"] or "?", c["new"], c.get("area"))
        if e["lines"] and c["type"] == "huawei":
            cmds = cmds[1:]                      # system-view uma vez só
        e["lines"] += cmds
    return list(by.values())


# ---------- caminhos ----------
def spf_path(links: List[dict], s: str, t: str, costs: Optional[dict] = None) -> dict:
    g = NA.build_graph(links).without(set(), set(), costs or {})
    dist = g.all_dist()
    if t not in dist.get(s, {}):
        return {"reachable": False}
    flow = g.route(dist, s, t)
    return {"reachable": True, "cost": dist[s][t], "hops": [{"link": k[0], "dir": k[1], "share": round(v, 3)} for k, v in flow.items()],
            "ecmp": any(v < 0.999 for v in flow.values())}


def cspf(links: List[dict], s: str, t: str, bw_mbps: float = 0, max_pct: float = 90.0, exclude_links=(), exclude_nodes=(),
         include_nodes=()) -> dict:
    """Menor custo OSPF passando só por enlaces que aguentam +bw sem passar de max_pct (com desvios obrigatórios)."""
    NA.build_graph(links)
    bw = float(bw_mbps or 0) * 1e6
    adj: Dict[str, List[Tuple[str, int, Tuple[str, str], float]]] = defaultdict(list)
    for L in links:
        if not L.get("in_spf") or L["id"] in exclude_links:
            continue
        cap = (L.get("capacity_mbps") or 0) * 1e6
        for d, u, v in (("ab", L["a"], L["b"]), ("ba", L["b"], L["a"])):
            if u in exclude_nodes or v in exclude_nodes:
                continue
            used = L.get(f"{d}_bps") or 0
            room = cap * max_pct / 100 - used if cap else float("inf")
            if room >= bw:
                adj[u].append((v, L[f"cost_{d}"], (L["id"], d), room))

    def dj(a: str, b: str, banned: set):
        dist, prev = {a: 0}, {}
        pq = [(0, a)]
        while pq:
            dd, u = heapq.heappop(pq)
            if u == b:
                break
            if dd > dist.get(u, 1 << 60):
                continue
            for v, c, key, _ in adj.get(u, []):
                if v in banned:
                    continue
                nd = dd + c
                if nd < dist.get(v, 1 << 60):
                    dist[v], prev[v] = nd, (u, key)
                    heapq.heappush(pq, (nd, v))
        if b not in dist:
            return None
        path, cur = [], b
        while cur != a:
            u, key = prev[cur]
            path.append((u, cur, key))
            cur = u
        return dist[b], list(reversed(path))

    way = [s, *[n for n in include_nodes if n not in (s, t)], t]
    total, hops, visited = 0, [], {s}
    for a, b in zip(way, way[1:]):
        r = dj(a, b, visited - {a})
        if not r:
            return {"found": False, "why": f"sem caminho com folga entre os pontos (meta {max_pct:.0f}% com +{bw_mbps:g} Mbps)"}
        total += r[0]
        for u, v, key in r[1]:
            hops.append((u, v, key))
            visited.add(v)
    byid = {L["id"]: L for L in links}
    out = []
    for u, v, (lid, d) in hops:
        L = byid[lid]
        cap = (L.get("capacity_mbps") or 0) * 1e6
        used = L.get(f"{d}_bps") or 0
        out.append({"link": lid, "dir": d, "from": L["a_name"] if d == "ab" else L["b_name"], "to": L["b_name"] if d == "ab" else L["a_name"],
                    "from_if": L["a_if"] if d == "ab" else L["b_if"], "next_hop": L["b_ip"] if d == "ab" else L["a_ip"],
                    "cost": L[f"cost_{d}"], "pct_now": round(used / cap * 100, 1) if cap else None,
                    "pct_after": round((used + bw) / cap * 100, 1) if cap else None})
    return {"found": True, "cost": total, "hops": out}


def te_config(vendor: Optional[str], name: str, hops: List[dict], dest_rid: Optional[str], bw_mbps: float) -> List[str]:
    """Caminho explícito para um túnel RSVP-TE na origem (precisa de MPLS TE habilitado na rede)."""
    nh = [h["next_hop"] for h in hops if h.get("next_hop")]
    if len(nh) != len(hops):
        return ["# sem os IPs dos saltos (topologia veio do mapa): monte o explicit-path com os IPs das interfaces"]
    v = (vendor or "").lower()
    kbps = int(bw_mbps * 1000)
    if v == "juniper":
        return [f"set protocols mpls path {name} {ip} strict" for ip in nh] + [
            f"set protocols mpls label-switched-path {name} to {dest_rid or 'ROUTER-ID-DESTINO'}",
            f"set protocols mpls label-switched-path {name} primary {name}"] + (
            [f"set protocols mpls label-switched-path {name} bandwidth {int(bw_mbps)}m"] if bw_mbps else []) + ["commit"]
    if v in ("cisco",):
        return ["configure terminal", f"explicit-path name {name}"] + [f" next-address strict {ip}" for ip in nh] + [
            "exit", "interface tunnel-te1", f" destination {dest_rid or 'ROUTER-ID-DESTINO'}",
            f" path-option 10 explicit name {name}"] + ([f" signalled-bandwidth {kbps}"] if bw_mbps else []) + ["end"]
    return ["system-view", f"explicit-path {name}"] + [f" next hop {ip}" for ip in nh] + [
        " quit", "# troque Tunnel0/0/1 por um número de túnel livre", "interface Tunnel0/0/1", " tunnel-protocol mpls te",
        f" destination {dest_rid or 'ROUTER-ID-DESTINO'}", f" mpls te path explicit-path {name}"] + (
        [f" mpls te bandwidth ct0 {kbps}"] if bw_mbps else []) + [" mpls te commit", " quit"]
