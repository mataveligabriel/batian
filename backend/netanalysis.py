"""Análise de rede de um mapa: OSPF, BGP, interfaces e cenários de falha — só SNMP padrão, sem custo.

Coleta (por equipamento do mapa):
  - OSPF-MIB (RFC 1850): custo por interface, área, tipo de rede, hello/dead, estado; vizinhos e estado (FULL?)
  - BGP4-MIB (RFC 4273): sessões IPv4, estado, AS remoto, tempo estabelecida, último erro, nº de quedas
  - IF-MIB: erros/descartes (duas leituras -> taxa por segundo), MTU, status, descrição
  - IP-MIB: IP/máscara de cada interface (liga o OSPF à interface e o vizinho ao enlace)
Análise:
  - regras por enlace do mapa (custo x capacidade, assimetria, área/tipo/timers/MTU divergentes, adjacência)
  - SPF com ECMP sobre os custos reais: quais enlaces carregam as rotas, rotas assimétricas, enlaces ociosos
  - cenários: queda de cada enlace -> quem fica isolado (ponto único de falha) e para onde vai o tráfego
"""
import asyncio
import heapq
import ipaddress
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import mplscli
import snmp_service as S

# ---------- OIDs ----------
OSPF_ROUTER_ID = "1.3.6.1.2.1.14.1.1.0"
OSPF_IF = "1.3.6.1.2.1.14.7.1"           # ospfIfTable (índice ip.addrLess)
OSPF_METRIC = "1.3.6.1.2.1.14.8.1.4"     # ospfIfMetricValue (índice ip.addrLess.tos)
OSPF_NBR = "1.3.6.1.2.1.14.10.1"         # ospfNbrTable (índice ip.addrLess)
BGP_LOCAL_AS = "1.3.6.1.2.1.15.2.0"
BGP_PEER = "1.3.6.1.2.1.15.3.1"          # bgpPeerTable (índice ip do peer)
IP_AD_IFINDEX = "1.3.6.1.2.1.4.20.1.2"
IP_AD_MASK = "1.3.6.1.2.1.4.20.1.3"
IF_MTU = "1.3.6.1.2.1.2.2.1.4"
IF_IN_DISC = "1.3.6.1.2.1.2.2.1.13"
IF_IN_ERR = "1.3.6.1.2.1.2.2.1.14"
IF_OUT_DISC = "1.3.6.1.2.1.2.2.1.19"
IF_OUT_ERR = "1.3.6.1.2.1.2.2.1.20"
IF_HC_IN_UCAST = "1.3.6.1.2.1.31.1.1.1.7"
IF_HC_OUT_UCAST = "1.3.6.1.2.1.31.1.1.1.11"

OSPF_IF_STATE = {1: "down", 2: "loopback", 3: "waiting", 4: "point-to-point", 5: "DR", 6: "BDR", 7: "DROther"}
OSPF_IF_TYPE = {1: "broadcast", 2: "nbma", 3: "point-to-multipoint", 5: "point-to-point"}
OSPF_NBR_STATE = {1: "down", 2: "attempt", 3: "init", 4: "2-way", 5: "exstart", 6: "exchange", 7: "loading", 8: "full"}
BGP_STATE = {1: "idle", 2: "connect", 3: "active", 4: "opensent", 5: "openconfirm", 6: "established"}
BGP_ERR = {  # (código, subcódigo) da última NOTIFICATION
    (1, 0): "erro de cabeçalho", (2, 0): "erro no OPEN", (2, 2): "AS remoto incorreto", (2, 3): "BGP identifier inválido",
    (2, 6): "hold time inaceitável", (3, 0): "erro no UPDATE", (4, 0): "hold timer expirou (keepalives perdidos)",
    (5, 0): "erro na máquina de estados", (6, 0): "cease", (6, 1): "limite de prefixos atingido",
    (6, 2): "desativada administrativamente", (6, 3): "peer removido", (6, 4): "reset administrativo",
    (6, 5): "conexão rejeitada", (6, 6): "mudança de configuração", (6, 7): "colisão de conexão", (6, 8): "falta de recursos",
}

DEFAULT_OPTS = {
    "ref_bw_mbps": None,        # referência p/ custo "esperado"; None = descobre a que a própria rede usa
    "util_warn": 70, "util_crit": 90,
    "err_warn_pps": 0.0,        # qualquer erro crescendo já é atenção
    "err_crit_ratio": 0.001,    # 0,1% dos pacotes com erro = crítico
    "err_crit_pps": 10.0,
    "lane_imbalance_db": 3.0,
    "sample_sec": 10,           # intervalo entre as duas leituras de contadores
    "bgp_flap_sec": 3600,       # sessão estabelecida há menos que isso = recente (possível flap)
}


def _suffix(oid: str, base: str) -> str:
    return oid[len(base) + 1:]


def _ip_from(parts: List[str]) -> str:
    return ".".join(parts[:4])


async def _walk(client, base: str) -> Dict[str, object]:
    try:
        return await client.walk(base)
    except S.SnmpError:
        return {}


async def _table(client, base: str, cols: Dict[int, str]) -> Dict[str, dict]:
    """Walk de colunas de uma tabela -> {índice: {nome_coluna: valor}}."""
    rows: Dict[str, dict] = defaultdict(dict)
    for col, name in cols.items():
        cb = f"{base}.{col}"
        for oid, v in (await _walk(client, cb)).items():
            if isinstance(v, S.NoSuch):
                continue
            rows[_suffix(oid, cb)][name] = v
    return dict(rows)


async def _counters(client) -> Dict[int, dict]:
    out: Dict[int, dict] = defaultdict(dict)
    for key, base in (("in_err", IF_IN_ERR), ("out_err", IF_OUT_ERR), ("in_disc", IF_IN_DISC), ("out_disc", IF_OUT_DISC),
                      ("in_pkts", IF_HC_IN_UCAST), ("out_pkts", IF_HC_OUT_UCAST)):
        for oid, v in (await _walk(client, base)).items():
            i = S._idx(oid, base)
            if i is not None and isinstance(v, int):
                out[i][key] = v
    return dict(out)


def _rate(a: Optional[int], b: Optional[int], dt: float, wrap: int = 2 ** 32) -> Optional[float]:
    if a is None or b is None or dt <= 0:
        return None
    d = b - a
    if d < 0:
        d += wrap          # Counter32 deu a volta
        if d < 0 or d > wrap // 2:
            return None
    return d / dt


def _bgp_err_code(le) -> Optional[Tuple[int, int]]:
    """bgpPeerLastError: 2 bytes (código, subcódigo). Chega como bytes, texto de 2 chars ou hex do net-snmp."""
    code = None
    if isinstance(le, (bytes, bytearray)) and len(le) >= 2:
        code = (le[0], le[1])
    elif isinstance(le, str) and le:
        t = le.strip().strip('"').strip()
        hexish = t.replace(" ", "")
        if len(hexish) >= 4 and all(c in "0123456789abcdefABCDEF" for c in hexish):
            b = bytes.fromhex(hexish[:4])
            code = (b[0], b[1])
        elif len(le) == 2 and all(ord(c) < 256 for c in le):
            code = (ord(le[0]), ord(le[1]))
    return None if code in (None, (0, 0)) else code


async def collect_device(client, sample_sec: float = 10) -> dict:
    """Lê tudo o que a análise precisa de um equipamento (tolerante: o que não existir fica vazio)."""
    info = await S.discover_interfaces(client)     # falha aqui = sem SNMP (erro sobe)
    ifaces = {i["index"]: dict(i) for i in info["interfaces"]}
    for oid, v in (await _walk(client, IF_MTU)).items():
        i = S._idx(oid, IF_MTU)
        if i in ifaces and isinstance(v, int):
            ifaces[i]["mtu"] = v

    # IP de cada interface
    ip_if: Dict[str, int] = {}
    ip_mask: Dict[str, str] = {}
    for oid, v in (await _walk(client, IP_AD_IFINDEX)).items():
        if isinstance(v, int):
            ip_if[_suffix(oid, IP_AD_IFINDEX)] = v
    for oid, v in (await _walk(client, IP_AD_MASK)).items():
        if isinstance(v, str):
            ip_mask[_suffix(oid, IP_AD_MASK)] = v
    for ip, i in ip_if.items():
        if i in ifaces and ip_mask.get(ip):
            ifaces[i].setdefault("ips", []).append(f"{ip}/{ip_mask[ip]}")

    # OSPF
    rid = None
    try:
        rid = (await client.get([OSPF_ROUTER_ID])).get(OSPF_ROUTER_ID)
    except S.SnmpError:
        pass
    rid = rid if isinstance(rid, str) and rid.count(".") == 3 else None
    ospf_rows = await _table(client, OSPF_IF, {3: "area", 4: "type", 5: "admin", 9: "hello", 10: "dead", 12: "state"})
    metrics = {}
    for oid, v in (await _walk(client, OSPF_METRIC)).items():
        parts = _suffix(oid, OSPF_METRIC).split(".")
        if len(parts) >= 6 and parts[5] == "0" and isinstance(v, int):     # TOS 0
            metrics[".".join(parts[:5])] = v
    ospf_ifs: Dict[int, dict] = {}
    for idx, row in ospf_rows.items():
        parts = idx.split(".")
        if len(parts) < 5:
            continue
        ip, addrless = _ip_from(parts), int(parts[4])
        ifidx = addrless if addrless else ip_if.get(ip)
        if ifidx is None:
            continue
        ospf_ifs[ifidx] = {
            "ip": ip if ip != "0.0.0.0" else None, "area": row.get("area"),
            "type": OSPF_IF_TYPE.get(row.get("type"), str(row.get("type"))),
            "admin": row.get("admin") == 1, "hello": row.get("hello"), "dead": row.get("dead"),
            "state": OSPF_IF_STATE.get(row.get("state"), str(row.get("state"))), "cost": metrics.get(idx),
        }
    nbr_rows = await _table(client, OSPF_NBR, {3: "rtr_id", 6: "state"})
    nets = []
    for ip, i in ip_if.items():
        try:
            nets.append((ipaddress.IPv4Network(f"{ip}/{ip_mask.get(ip, '255.255.255.255')}", strict=False), i))
        except ValueError:
            pass
    nbrs = []
    for idx, row in nbr_rows.items():
        parts = idx.split(".")
        if len(parts) < 5:
            continue
        ip, addrless = _ip_from(parts), int(parts[4])
        ifidx = addrless or None
        if ifidx is None:
            try:
                a = ipaddress.IPv4Address(ip)
                ifidx = next((i for n, i in nets if n.prefixlen < 32 and a in n), None)
            except ValueError:
                pass
        nbrs.append({"ip": ip, "rtr_id": row.get("rtr_id"), "state": OSPF_NBR_STATE.get(row.get("state"), str(row.get("state"))),
                     "ifindex": ifidx})

    # BGP
    bgp = await collect_bgp(client)

    # erros/descartes: duas leituras
    t0 = time.monotonic()
    c0 = await _counters(client)
    await asyncio.sleep(max(0.0, sample_sec - (time.monotonic() - t0)))
    t1 = time.monotonic()
    c1 = await _counters(client)
    dt = t1 - t0
    for i, a in c0.items():
        b = c1.get(i, {})
        if i not in ifaces:
            continue
        r = {k: _rate(a.get(k), b.get(k), dt, 2 ** 64 if k.endswith("pkts") else 2 ** 32) for k in a}
        ifaces[i].update({f"{k}_ps": v for k, v in r.items()})
        ifaces[i].update({f"{k}_total": b.get(k) for k in ("in_err", "out_err")})

    return {"ok": True, "sys_name": info.get("sys_name"), "sys_descr": info.get("sys_descr"), "router_id": rid,
            "ifaces": ifaces, "ospf": {"enabled": bool(ospf_ifs), "ifaces": ospf_ifs, "nbrs": nbrs},
            "bgp": bgp, "sample_sec": round(dt, 1)}


async def collect_bgp(client) -> dict:
    """Sessões BGP da BGP4-MIB (IPv4 da instância principal): estado, AS remoto, tempo estabelecida, último erro."""
    local_as = None
    try:
        local_as = (await client.get([BGP_LOCAL_AS])).get(BGP_LOCAL_AS)
    except S.SnmpError:
        pass
    bgp_rows = await _table(client, BGP_PEER, {2: "state", 3: "admin", 9: "remote_as", 10: "in_updates", 14: "last_error",
                                               15: "transitions", 16: "established_sec"})
    peers = []
    for ip, row in bgp_rows.items():
        code = _bgp_err_code(row.get("last_error"))
        peers.append({"ip": ip, "state": BGP_STATE.get(row.get("state"), str(row.get("state"))), "admin_up": row.get("admin") == 2,
                      "remote_as": row.get("remote_as"), "in_updates": row.get("in_updates"),
                      "transitions": row.get("transitions"), "established_sec": row.get("established_sec"),
                      "last_error": (BGP_ERR.get(code) or BGP_ERR.get((code[0], 0)) or f"código {code[0]}/{code[1]}") if code else None})
    return {"local_as": local_as if isinstance(local_as, int) else None, "peers": peers}


# =====================================================================================
# Análise
# =====================================================================================
SEV_ORDER = {"crit": 0, "warn": 1, "info": 2}
HUAWEI, JUNIPER = "Huawei", "Juniper"


def _f(sev: str, cat: str, title: str, detail: str = "", fix: str = "", **where) -> dict:
    return {"sev": sev, "cat": cat, "title": title, "detail": detail, "fix": fix, **{k: v for k, v in where.items() if v is not None}}


def fmt_bps(v: Optional[float]) -> str:
    if v is None:
        return "—"
    for u, d in (("Gbps", 1e9), ("Mbps", 1e6), ("Kbps", 1e3)):
        if v >= d:
            return f"{v / d:.2f} {u}"
    return f"{v:.0f} bps"


def fmt_speed(mbps: Optional[float]) -> str:
    if not mbps:
        return "?"
    return f"{mbps / 1000:g}G" if mbps >= 1000 else f"{mbps:g}M"


def expected_cost(ref_mbps: float, cap_mbps: Optional[float]) -> Optional[int]:
    if not cap_mbps:
        return None
    return max(1, int(round(ref_mbps / cap_mbps)))


# ---------- SPF com ECMP ----------
class Graph:
    """Arestas dirigidas (link_id, sentido) com custo OSPF da interface de saída."""

    def __init__(self):
        self.adj: Dict[str, List[Tuple[str, int, Tuple[str, str]]]] = defaultdict(list)   # u -> [(v, custo, (link, dir))]
        self.nodes: set = set()

    def add(self, u: str, v: str, cost: int, key: Tuple[str, str]):
        self.adj[u].append((v, int(cost), key))
        self.nodes.update((u, v))

    def without(self, links: set = frozenset(), nodes: set = frozenset(), costs: Optional[dict] = None) -> "Graph":
        g = Graph()
        g.nodes = set(self.nodes) - set(nodes)
        for u, es in self.adj.items():
            if u in nodes:
                continue
            for v, c, key in es:
                if v in nodes or key[0] in links:
                    continue
                g.adj[u].append((v, (costs or {}).get(key, c), key))
        return g

    def dijkstra(self, s: str) -> Dict[str, int]:
        dist = {s: 0}
        pq = [(0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, 1 << 60):
                continue
            for v, c, _ in self.adj.get(u, []):
                nd = d + c
                if nd < dist.get(v, 1 << 60):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist

    def all_dist(self) -> Dict[str, Dict[str, int]]:
        return {n: self.dijkstra(n) for n in self.nodes}

    def route(self, dist: Dict[str, Dict[str, int]], s: str, t: str, amount: float = 1.0) -> Dict[Tuple[str, str], float]:
        """Divide `amount` de s até t salto a salto entre os próximos saltos de mesmo custo (como o OSPF faz)."""
        flow: Dict[Tuple[str, str], float] = defaultdict(float)
        if t not in dist.get(s, {}):
            return flow
        at = {s: amount}
        order = sorted((n for n in self.nodes if t in dist.get(n, {})), key=lambda n: -dist[n][t])
        for u in order:
            a = at.get(u, 0.0)
            if not a or u == t:
                continue
            nh = [(v, key) for v, c, key in self.adj.get(u, []) if t in dist.get(v, {}) and c + dist[v][t] == dist[u][t]]
            if not nh:
                continue
            part = a / len(nh)
            for v, key in nh:
                flow[key] += part
                at[v] = at.get(v, 0.0) + part
        return flow


def _pairs(g: Graph, dist) -> List[Tuple[str, str]]:
    return [(s, t) for s in g.nodes for t in g.nodes if s != t and t in dist.get(s, {})]


# ---------- análise principal ----------
def analyze(m: dict, devs: Dict[str, dict], data: Dict[str, dict], live_links: Dict[str, dict],
            optics: Dict[Tuple[str, int], dict], opts: Optional[dict] = None, mpls: Optional[Dict[str, dict]] = None) -> dict:
    o = {**DEFAULT_OPTS, **(opts or {})}
    F: List[dict] = []
    nodes = {n["id"]: n for n in m.get("nodes", [])}
    node_dev = {nid: n.get("device_id") for nid, n in nodes.items() if n.get("kind") == "device" and n.get("device_id")}
    dname = lambda did: (devs.get(did) or {}).get("name") or "?"        # noqa: E731
    nname = lambda nid: dname(node_dev.get(nid)) if nid in node_dev else (nodes.get(nid, {}).get("label") or "?")  # noqa: E731

    # ---- coleta com falha
    for did, d in data.items():
        if not d.get("ok"):
            F.append(_f("warn", "Coleta", f"Sem leitura SNMP de {dname(did)}", d.get("error", ""),
                        "Confira a community SNMP e o ACL do equipamento; atrás de agente, o net-snmp precisa estar instalado no agente.",
                        device=dname(did)))

    # ---- enlaces
    links = []
    seen_nbrs = set()          # vizinhos já avaliados pelo enlace (não repetir no equipamento)
    pending_cost = []          # custo x capacidade: avaliado depois de descobrir a referência da rede
    for ln in m.get("links", []):
        a_dev, b_dev = node_dev.get(ln["from"]), node_dev.get(ln["to"])
        if not a_dev or not b_dev:
            continue
        fi, ti = ln.get("from_if") or {}, ln.get("to_if") or {}
        da, db = data.get(a_dev, {}), data.get(b_dev, {})
        ia = (da.get("ifaces") or {}).get(fi.get("index")) if fi else None
        ib = (db.get("ifaces") or {}).get(ti.get("index")) if ti else None
        oa = (da.get("ospf") or {}).get("ifaces", {}).get(fi.get("index")) if fi else None
        ob = (db.get("ospf") or {}).get("ifaces", {}).get(ti.get("index")) if ti else None
        cap = ln.get("capacity_mbps") or fi.get("speed_mbps") or ti.get("speed_mbps") or (ia or {}).get("speed_mbps") or (ib or {}).get("speed_mbps")
        lv = live_links.get(ln["id"]) or {}
        a_nm, b_nm = dname(a_dev), dname(b_dev)
        where = f"{a_nm} {fi.get('name', '?')} ↔ {b_nm} {ti.get('name', '?')}"
        L = {"id": ln["id"], "label": ln.get("label") or "", "a": ln["from"], "b": ln["to"], "a_name": a_nm, "b_name": b_nm,
             "a_if": fi.get("name"), "b_if": ti.get("name"), "capacity_mbps": cap,
             "expected_cost": None,
             "cost_ab": (oa or {}).get("cost"), "cost_ba": (ob or {}).get("cost"),
             "area_a": (oa or {}).get("area"), "area_b": (ob or {}).get("area"),
             "type_a": (oa or {}).get("type"), "type_b": (ob or {}).get("type"),
             "mtu_a": (ia or {}).get("mtu"), "mtu_b": (ib or {}).get("mtu"),
             "oper_a": (ia or {}).get("oper"), "oper_b": (ib or {}).get("oper"),
             "ab_bps": lv.get("ab_bps"), "ba_bps": lv.get("ba_bps"), "ab_pct": lv.get("ab_pct"), "ba_pct": lv.get("ba_pct"),
             "adjacency": None, "sev": None}
        lf = []   # achados deste enlace
        add = lambda sev, cat, title, detail="", fix="": lf.append(_f(sev, cat, title, detail, fix, link_id=ln["id"], where=where))  # noqa: E731

        down = [nm for nm, i in ((a_nm, ia), (b_nm, ib)) if i and i.get("oper") not in (None, "up", "unknown")]
        if down:
            add("crit", "Interfaces", "Enlace DOWN", f"Interface fora em: {', '.join(down)}",
                "Verifique a camada física (potência óptica, cordão, transceptor) e se a interface não está em shutdown.")
        # OSPF do enlace
        a_runs = bool((da.get("ospf") or {}).get("enabled"))
        b_runs = bool((db.get("ospf") or {}).get("enabled"))
        if fi and ti and (a_runs or b_runs):
            if oa and not ob and b_runs:
                add("crit", "OSPF", f"OSPF ativo só em {a_nm}", f"{b_nm} {ti.get('name')} não participa do OSPF.",
                    "Ative o OSPF na interface do outro lado (mesma área) ou remova do lado que sobrou.")
            elif ob and not oa and a_runs:
                add("crit", "OSPF", f"OSPF ativo só em {b_nm}", f"{a_nm} {fi.get('name')} não participa do OSPF.",
                    "Ative o OSPF na interface do outro lado (mesma área) ou remova do lado que sobrou.")
            elif not oa and not ob and a_runs and b_runs:
                add("info", "OSPF", "Enlace entre roteadores OSPF sem OSPF",
                    "Os dois equipamentos rodam OSPF, mas não neste enlace (estático/BGP/L2?).",
                    "Se o enlace deveria rotear, ative o OSPF nas duas interfaces.")
        if oa and ob:
            if oa.get("area") != ob.get("area"):
                add("crit", "OSPF", "Áreas diferentes nas pontas", f"{a_nm}: área {oa.get('area')} · {b_nm}: área {ob.get('area')}",
                    "As duas interfaces precisam estar na mesma área OSPF.")
            if (oa.get("hello"), oa.get("dead")) != (ob.get("hello"), ob.get("dead")):
                add("crit", "OSPF", "Timers hello/dead diferentes",
                    f"{a_nm}: {oa.get('hello')}/{oa.get('dead')}s · {b_nm}: {ob.get('hello')}/{ob.get('dead')}s",
                    f"Iguale os timers ({HUAWEI}: ospf timer hello/dead; {JUNIPER}: hello-interval/dead-interval).")
            if oa.get("type") != ob.get("type"):
                add("warn", "OSPF", "Tipo de rede OSPF diferente nas pontas", f"{a_nm}: {oa.get('type')} · {b_nm}: {ob.get('type')}",
                    "Use o mesmo tipo nas duas pontas (em enlace ponto a ponto, p2p).")
            elif oa.get("type") == "broadcast":
                add("info", "OSPF", "Enlace ponto a ponto como broadcast",
                    "Com broadcast há eleição de DR/BDR e a adjacência demora mais a subir.",
                    f"Configure p2p nas duas pontas ({HUAWEI}: ospf network-type p2p; {JUNIPER}: interface-type p2p).")
            if L["mtu_a"] and L["mtu_b"] and L["mtu_a"] != L["mtu_b"]:
                add("crit", "OSPF", "MTU diferente nas pontas", f"{a_nm}: {L['mtu_a']} · {b_nm}: {L['mtu_b']}",
                    "Iguale o MTU: com MTU diferente a adjacência fica presa em ExStart/Exchange ou cai ao trocar LSAs grandes.")
            # adjacência pelo router-id do outro lado
            rid_b, rid_a = db.get("router_id"), da.get("router_id")
            na = next((x for x in (da.get("ospf") or {}).get("nbrs", []) if x.get("ifindex") == fi.get("index")
                       and (not rid_b or x.get("rtr_id") == rid_b)), None)
            nb = next((x for x in (db.get("ospf") or {}).get("nbrs", []) if x.get("ifindex") == ti.get("index")
                       and (not rid_a or x.get("rtr_id") == rid_a)), None)
            st = (na or nb or {}).get("state")
            L["adjacency"] = st
            for dd, x in ((a_dev, na), (b_dev, nb)):
                if x:
                    seen_nbrs.add((dd, x.get("ip")))
            p2p = "point-to-point" in (oa.get("type"), ob.get("type"))
            if st == "2-way" and not p2p:
                L["adjacency"] = "full"          # DROther <-> DROther em broadcast: 2-way é normal
            elif not down and (na or nb) and st != "full":
                add("crit", "OSPF", f"Adjacência OSPF não está FULL ({st})", "O enlace está up mas os roteadores não trocam rotas.",
                    "Confira MTU, área, timers, autenticação e tipo de rede nas duas pontas.")
            elif not down and not na and not nb and a_runs and b_runs and \
                    ((da.get("ospf") or {}).get("nbrs") or (db.get("ospf") or {}).get("nbrs")):   # só se a tabela de vizinhos existe
                add("crit", "OSPF", "Sem vizinho OSPF neste enlace", "OSPF configurado nas duas interfaces, mas nenhuma adjacência formada.",
                    "Confira MTU, área, timers, autenticação, tipo de rede e se o IP está na mesma sub-rede.")
            ca, cb = oa.get("cost"), ob.get("cost")
            if ca is not None and cb is not None and ca != cb:
                add("warn", "OSPF", f"Custo OSPF assimétrico ({ca} × {cb})",
                    f"{a_nm}→{b_nm} custa {ca} e {b_nm}→{a_nm} custa {cb}: ida e volta podem seguir caminhos diferentes.",
                    f"Iguale o custo nas duas pontas, a não ser que a assimetria seja proposital ({HUAWEI}: ospf cost N; {JUNIPER}: metric N).")
            pending_cost.append((L, ca, cb, cap, a_nm, b_nm, add))
        # utilização
        for pct, dirn in ((L["ab_pct"], f"{a_nm}→{b_nm}"), (L["ba_pct"], f"{b_nm}→{a_nm}")):
            if pct is None:
                continue
            if pct >= o["util_crit"]:
                add("crit", "Capacidade", f"Enlace a {pct:.0f}% ({dirn})", f"Capacidade {fmt_speed(cap)}.",
                    "Risco de perda de pacotes: planeje upgrade ou redistribua o tráfego (custos OSPF/ECMP).")
            elif pct >= o["util_warn"]:
                add("warn", "Capacidade", f"Enlace a {pct:.0f}% ({dirn})", f"Capacidade {fmt_speed(cap)}.",
                    "Acompanhe a tendência (p95) e planeje capacidade.")
        # óptica
        for did, idx, nm in ((a_dev, fi.get("index"), a_nm), (b_dev, ti.get("index"), b_nm)):
            ol = optics.get((did, idx)) or {}
            lanes = [x.get("rx") for x in ol.get("lanes") or [] if x.get("rx") is not None]
            if not lanes:
                continue
            if any(v <= -40 for v in lanes):
                add("crit", "Óptica", f"Lane sem luz em {nm}", "RX por lane: " + " / ".join(f"{v:.1f}" for v in lanes) + " dBm",
                    "Lane apagada: conector sujo, fibra danificada ou transceptor com defeito.")
            elif len(lanes) > 1 and max(lanes) - min(lanes) >= o["lane_imbalance_db"]:
                add("warn", "Óptica", f"Lanes desbalanceadas em {nm} ({max(lanes) - min(lanes):.1f} dB)",
                    "RX por lane: " + " / ".join(f"{v:.1f}" for v in lanes) + " dBm",
                    "Uma lane bem abaixo das outras indica fibra/conector sujo ou transceptor degradado — limpe e meça.")
        L["_lf"] = lf
        links.append(L)

    # referência de banda que a rede usa (mediana de custo × capacidade), a não ser que o usuário fixe uma
    prods = sorted(c * cap for (_, ca, cb, cap, *_r) in pending_cost for c in (ca, cb) if c and cap)
    ref = o.get("ref_bw_mbps") or (prods[len(prods) // 2] if prods else 100000)
    o["ref_used_mbps"] = ref
    o["ref_inferred"] = not o.get("ref_bw_mbps")
    for L, ca, cb, cap, a_nm, b_nm, add in pending_cost:
        exp = expected_cost(ref, cap)
        L["expected_cost"] = exp
        for c, nm in ((ca, a_nm), (cb, b_nm)):
            if c and exp and (c / exp >= 4 or c / exp <= 0.25) and not (c == 1 and exp == 1):
                add("info", "OSPF", f"Custo {c} fora do padrão da rede para {fmt_speed(cap)} (em {nm})",
                    f"Os outros enlaces seguem uma referência de ~{fmt_speed(ref)} (custo = referência ÷ banda): "
                    f"para {fmt_speed(cap)} o esperado seria ~{exp}.",
                    "Se for engenharia de tráfego proposital, ignore; senão ajuste o custo.")
                break
    for L in links:
        lf = L.pop("_lf")
        L["findings"] = len(lf)
        L["sev"] = min((x["sev"] for x in lf), key=lambda s: SEV_ORDER[s]) if lf else None
        F += lf

    # parallelos sem ECMP
    groups = defaultdict(list)
    for L in links:
        if L["cost_ab"] is not None:
            groups[tuple(sorted((L["a"], L["b"])))].append(L)
    for (x, y), ls in groups.items():
        if len(ls) > 1:
            costs = {(l["cost_ab"] if l["a"] == x else l["cost_ba"]) for l in ls}
            if len(costs) > 1:
                F.append(_f("warn", "OSPF", f"Enlaces paralelos sem ECMP ({nname(x)} ↔ {nname(y)})",
                            f"{len(ls)} enlaces com custos diferentes ({', '.join(str(c) for c in sorted(costs, key=lambda v: (v is None, v)))}): "
                            "só o de menor custo carrega tráfego.",
                            "Iguale os custos para balancear (ECMP) ou agregue em LAG.", link_id=ls[0]["id"]))

    # custos todos = 1 (auto-cost padrão)
    ospf_links = [L for L in links if L["cost_ab"] is not None and L["cost_ba"] is not None]
    caps = {L["capacity_mbps"] for L in ospf_links if L["capacity_mbps"]}
    if len(ospf_links) >= 2 and all(L["cost_ab"] == 1 and L["cost_ba"] == 1 for L in ospf_links) and len(caps) > 1:
        F.append(_f("warn", "OSPF", "Todos os enlaces com custo 1",
                    "Banda de referência padrão (100 Mbps): o OSPF não diferencia 1G de 100G e escolhe o caminho só pelo número de saltos.",
                    f"Ajuste a referência em TODOS os roteadores ({HUAWEI}: ospf 1 → bandwidth-reference 100000; "
                    f"{JUNIPER}: set protocols ospf reference-bandwidth 100g; Cisco: auto-cost reference-bandwidth 100000) ou defina custos por enlace."))

    # ---- por equipamento: OSPF, BGP, interfaces fora do mapa
    map_ifaces = set()
    for ln in m.get("links", []):
        for side, key in (("from_if", "from"), ("to_if", "to")):
            if ln.get(side) and node_dev.get(ln[key]):
                map_ifaces.add((node_dev[ln[key]], ln[side]["index"]))
    rids = defaultdict(list)
    bgp_rows, iface_rows, dev_rows = [], [], []
    for nid, did in node_dev.items():
        d = data.get(did) or {}
        nm = dname(did)
        if not d.get("ok"):
            dev_rows.append({"id": did, "name": nm, "ok": False, "error": d.get("error")})
            continue
        ospf = d.get("ospf") or {}
        if d.get("router_id"):
            rids[d["router_id"]].append(nm)
        bad_nbrs = [x for x in ospf.get("nbrs", []) if x.get("state") not in ("full", "2-way")]
        for x in bad_nbrs:
            if (did, x.get("ip")) in seen_nbrs:      # já avaliado no enlace do mapa
                continue
            F.append(_f("warn", "OSPF", f"Vizinho OSPF {x.get('rtr_id') or x['ip']} em {x['state']}", f"Em {nm}, pelo IP {x['ip']}.",
                        "Adjacência que não chega a FULL: confira MTU, área, timers e autenticação.", device=nm))
        bgp = d.get("bgp") or {}
        for p in bgp.get("peers", []):
            row = {"device": nm, **p}
            bgp_rows.append(row)
            if not p.get("admin_up"):
                continue
            ras = p.get("remote_as")
            asn = "AS 4 bytes" if ras == 23456 else f"AS{ras}"
            if p.get("state") != "established":
                F.append(_f("crit", "BGP", f"Sessão BGP caída: {p['ip']} ({asn}) em {nm}",
                            f"Estado {p.get('state')}" + (f" · último erro: {p['last_error']}" if p.get("last_error") else ""),
                            "Veja o detalhe do peer (Huawei: display bgp peer IP verbose; Juniper: show bgp neighbor IP): "
                            "alcance do IP, AS remoto, senha MD5, TTL/multihop e filtros.", device=nm))
            else:
                est = p.get("established_sec")
                if isinstance(est, int) and est < o["bgp_flap_sec"]:
                    F.append(_f("warn", "BGP", f"Sessão BGP {p['ip']} ({asn}) reiniciou há {max(1, est // 60)} min",
                                f"Em {nm}" + (f" · último erro: {p['last_error']}" if p.get("last_error") else "")
                                + (f" · {p['transitions']} quedas desde o boot" if p.get("transitions") else ""),
                                "Se voltar a cair, veja se é hold timer (enlace/CPU) ou limite de prefixos.", device=nm))
                elif isinstance(p.get("transitions"), int) and p["transitions"] >= 10:
                    F.append(_f("info", "BGP", f"Sessão BGP {p['ip']} ({asn}) já caiu {p['transitions']} vezes",
                                f"Em {nm} (contador desde o boot do equipamento).", "Histórico de instabilidade: acompanhe.", device=nm))
        for idx, i in (d.get("ifaces") or {}).items():
            ie, oe = i.get("in_err_ps"), i.get("out_err_ps")
            idisc, odisc = i.get("in_disc_ps"), i.get("out_disc_ps")
            pps = (i.get("in_pkts_ps") or 0) + (i.get("out_pkts_ps") or 0)
            errs = (ie or 0) + (oe or 0)
            in_map = (did, idx) in map_ifaces
            row = {"device": nm, "iface": i.get("name"), "alias": i.get("alias"), "oper": i.get("oper"), "admin": i.get("admin"),
                   "speed_mbps": i.get("speed_mbps"), "mtu": i.get("mtu"), "in_err_ps": ie, "out_err_ps": oe,
                   "in_disc_ps": idisc, "out_disc_ps": odisc, "in_err_total": i.get("in_err_total"), "out_err_total": i.get("out_err_total"),
                   "in_map": in_map}
            interesting = errs > o["err_warn_pps"] or (idisc or 0) + (odisc or 0) > 1
            if errs > o["err_warn_pps"]:
                ratio = errs / pps if pps else None
                sev = "crit" if (errs >= o["err_crit_pps"] or (ratio is not None and ratio >= o["err_crit_ratio"])) else "warn"
                kind = "entrada" if (ie or 0) >= (oe or 0) else "saída"
                F.append(_f(sev, "Interfaces", f"{nm} {i.get('name')}: {errs:.1f} erros/s", 
                            f"Erros de {kind}" + (f" · {ratio * 100:.3f}% dos pacotes" if ratio is not None else "")
                            + (f" · {i.get('alias')}" if i.get("alias") else ""),
                            "Erro de entrada costuma ser físico (CRC): limpe/troque cordão e conector, confira transceptor e potência; "
                            "erro de saída indica problema no equipamento ou duplex.", device=nm, iface=i.get("name")))
            if (idisc or 0) + (odisc or 0) > 100:
                F.append(_f("info", "Interfaces", f"{nm} {i.get('name')}: {(idisc or 0) + (odisc or 0):.0f} descartes/s",
                            "Descartes de " + ("saída" if (odisc or 0) >= (idisc or 0) else "entrada") + " — fila cheia ou política de QoS.",
                            "Com o enlace perto do limite, descarte é congestionamento: veja a utilização e o QoS.", device=nm, iface=i.get("name")))
            if i.get("admin") == "up" and i.get("oper") not in ("up", "unknown", None) and (i.get("alias") or "").strip() and not in_map:
                interesting = True
                F.append(_f("info", "Interfaces", f"{nm} {i.get('name')} descrita e DOWN", f"Descrição: {i.get('alias')}",
                            "Interface ativa (no shutdown) com descrição mas sem link: é esperado? Se não for, verifique ou dê shutdown.",
                            device=nm, iface=i.get("name")))
            if interesting or in_map:
                iface_rows.append(row)
        dev_rows.append({"id": did, "name": nm, "ok": True, "router_id": d.get("router_id"), "sys_descr": (d.get("sys_descr") or "")[:120],
                         "ospf_ifaces": len(ospf.get("ifaces", {})), "ospf_nbrs": len(ospf.get("nbrs", [])),
                         "ospf_full": sum(1 for x in ospf.get("nbrs", []) if x.get("state") == "full"),
                         "bgp_local_as": bgp.get("local_as"), "bgp_peers": len(bgp.get("peers", [])),
                         "bgp_up": sum(1 for x in bgp.get("peers", []) if x.get("state") == "established"),
                         "ifaces": len(d.get("ifaces") or {})})
    for rid, names in rids.items():
        if len(names) > 1:
            F.append(_f("crit", "OSPF", f"Router-ID {rid} duplicado", "Usado por: " + ", ".join(names),
                        "Cada roteador precisa de um router-ID único (normalmente o IP da loopback)."))

    # ---- topologia / cenários
    graph = build_graph(links)
    topo, scen, tf = topology(graph, links, nname, o)
    F += tf
    mpls_rep = None
    if mpls:
        mf, mpls_rep = mpls_checks(mpls, links, graph, topo, node_dev, data, dname, nname)
        F += mf

    F.sort(key=lambda f: (SEV_ORDER[f["sev"]], f["cat"], f["title"]))
    cnt = {s: sum(1 for f in F if f["sev"] == s) for s in ("crit", "warn", "info")}
    score = max(0, 100 - 15 * cnt["crit"] - 5 * cnt["warn"] - cnt["info"])
    summary = {"crit": cnt["crit"], "warn": cnt["warn"], "info": cnt["info"], "score": score,
               "devices": len(node_dev), "devices_ok": sum(1 for r in dev_rows if r.get("ok")),
               "links": len(links), "ospf_links": len(ospf_links),
               "bgp_sessions": sum(1 for r in bgp_rows if r.get("admin_up")),
               "bgp_down": sum(1 for r in bgp_rows if r.get("admin_up") and r.get("state") != "established"),
               "ifaces_with_errors": sum(1 for r in iface_rows if (r.get("in_err_ps") or 0) + (r.get("out_err_ps") or 0) > 0)}
    if mpls_rep:
        summary["mpls"] = mpls_rep["summary"]
    return {"summary": summary, "findings": F, "links": links, "devices": dev_rows, "bgp": bgp_rows, "mpls": mpls_rep,
            "ifaces": sorted(iface_rows, key=lambda r: -((r.get("in_err_ps") or 0) + (r.get("out_err_ps") or 0))),
            "topology": topo, "scenarios": scen, "opts": o}


def build_graph(links: List[dict]) -> Graph:
    g = Graph()
    for L in links:
        usable = (L["cost_ab"] is not None and L["cost_ba"] is not None
                  and L.get("oper_a") in (None, "up", "unknown") and L.get("oper_b") in (None, "up", "unknown")
                  and L.get("adjacency") in (None, "full"))
        L["in_spf"] = usable
        if usable:
            g.add(L["a"], L["b"], L["cost_ab"], (L["id"], "ab"))
            g.add(L["b"], L["a"], L["cost_ba"], (L["id"], "ba"))
    return g


def _demands(links: List[dict]) -> Dict[Tuple[str, str], float]:
    """Sem matriz de tráfego: cada sentido medido de um enlace vira uma demanda entre suas pontas."""
    dem: Dict[Tuple[str, str], float] = defaultdict(float)
    for L in links:
        if not L.get("in_spf"):
            continue
        if L.get("ab_bps"):
            dem[(L["a"], L["b"])] += L["ab_bps"]
        if L.get("ba_bps"):
            dem[(L["b"], L["a"])] += L["ba_bps"]
    return dem


def _loads(g: Graph, dist, dem) -> Dict[Tuple[str, str], float]:
    out: Dict[Tuple[str, str], float] = defaultdict(float)
    for (s, t), amt in dem.items():
        for k, v in g.route(dist, s, t, amt).items():
            out[k] += v
    return out


def simulate(links: List[dict], down: set = frozenset(), costs: Optional[Dict[Tuple[str, str], int]] = None,
             down_nodes: set = frozenset(), count_changes: bool = True) -> dict:
    """Tráfego previsto = medido + (carga no cenário − carga no modelo atual). Estimativa de 1ª ordem."""
    base = build_graph(links)          # (re)marca in_spf
    g2 = base.without(set(down), set(down_nodes), costs)
    d1, d2 = base.all_dist(), g2.all_dist()
    dem = _demands(links)
    l1, l2 = _loads(base, d1, dem), _loads(g2, d2, dem)
    before = {(s, t) for s, t in _pairs(base, d1)}
    after = {(s, t) for s, t in _pairs(g2, d2)}
    isolated = sorted({tuple(sorted(p)) for p in before - after if p[0] not in down_nodes and p[1] not in down_nodes})
    # quem fica isolado de fato: fora do maior grupo que continua conectado
    comp, seen = [], set()
    for n in g2.nodes:
        if n in seen:
            continue
        c = {v for v in d2.get(n, {}) if n in d2.get(v, {})}
        seen |= c
        comp.append(c)
    main = max(comp, key=len) if comp else set()
    cut_nodes = sorted(n for n in (g2.nodes - main) if any(n in p for p in isolated))
    changed = 0
    for s, t in (before & after if count_changes else ()):
        p1 = {k[0] for k in base.route(d1, s, t)}
        p2 = {k[0] for k in g2.route(d2, s, t)}
        if p1 != p2:
            changed += 1
    pred = {}
    for L in links:
        cap = (L.get("capacity_mbps") or 0) * 1e6
        row = {}
        for dirn, meas in (("ab", L.get("ab_bps")), ("ba", L.get("ba_bps"))):
            key = (L["id"], dirn)
            if L["id"] in down or L["a"] in down_nodes or L["b"] in down_nodes:
                v = 0.0
            else:
                v = max(0.0, (meas or 0.0) + l2.get(key, 0.0) - l1.get(key, 0.0))
            row[f"{dirn}_bps"] = v
            row[f"{dirn}_pct"] = round(v / cap * 100, 1) if cap else None
        row["down"] = L["id"] in down or L["a"] in down_nodes or L["b"] in down_nodes
        row["cost_ab"] = (costs or {}).get((L["id"], "ab"), L.get("cost_ab"))
        row["cost_ba"] = (costs or {}).get((L["id"], "ba"), L.get("cost_ba"))
        pred[L["id"]] = row
    return {"links": pred, "isolated_pairs": isolated, "cut_nodes": cut_nodes, "changed_pairs": changed}


def topology(g: Graph, links: List[dict], nname, o: dict):
    F = []
    topo = {"nodes": len(g.nodes), "pairs": 0, "asymmetric_pairs": 0, "unused_links": [], "usage": {}}
    if len(g.nodes) < 2:
        return topo, [], F
    dist = g.all_dist()
    pairs = _pairs(g, dist)
    topo["pairs"] = len(pairs)
    use: Dict[Tuple[str, str], float] = defaultdict(float)
    asym = 0
    paths = {}
    for s, t in pairs:
        fl = g.route(dist, s, t)
        paths[(s, t)] = {k[0] for k in fl}
        for k, v in fl.items():
            use[k] += v
    for s, t in pairs:
        if s < t and (t, s) in paths and paths[(s, t)] != paths[(t, s)]:
            asym += 1
    topo["asymmetric_pairs"] = asym
    for L in links:
        if not L.get("in_spf"):
            continue
        u = use.get((L["id"], "ab"), 0) + use.get((L["id"], "ba"), 0)
        topo["usage"][L["id"]] = round(u, 2)
        if u == 0:
            topo["unused_links"].append(L["id"])
            F.append(_f("info", "Topologia", f"Enlace de reserva: {L['a_name']} ↔ {L['b_name']}",
                        "Nenhuma rota do mapa passa por ele com a rede íntegra (só entra se outro cair).",
                        "Se ele deveria carregar tráfego, baixe o custo ou iguale ao caminho principal.", link_id=L["id"]))
    if asym:
        F.append(_f("info", "Topologia", f"{asym} par(es) de equipamentos com rota assimétrica",
                    "Ida e volta passam por enlaces diferentes (normalmente por custo assimétrico).",
                    "Assimetria atrapalha troubleshooting e pode quebrar firewalls/CGNAT stateful no caminho."))

    # cenários: queda de cada enlace
    scen = []
    for L in links:
        if not L.get("in_spf"):
            continue
        r = simulate(links, down={L["id"]}, count_changes=False)
        worst = None
        for lid, p in r["links"].items():
            if lid == L["id"]:
                continue
            for dirn in ("ab", "ba"):
                pct = p.get(f"{dirn}_pct")
                if pct is not None and (worst is None or pct > worst[2]):
                    other = next(x for x in links if x["id"] == lid)
                    worst = (lid, dirn, pct, other)
        row = {"link_id": L["id"], "name": f"{L['a_name']} ↔ {L['b_name']}", "isolated": len(r["isolated_pairs"]),
               "isolated_nodes": [nname(n) for n in r["cut_nodes"]],
               "worst_link": (f"{worst[3]['a_name']} ↔ {worst[3]['b_name']}" if worst else None),
               "worst_link_id": worst[0] if worst else None, "worst_pct": worst[2] if worst else None}
        scen.append(row)
        if r["isolated_pairs"]:
            F.append(_f("crit" if len(row["isolated_nodes"]) >= 2 else "warn", "Topologia",
                        f"Ponto único de falha: {L['a_name']} ↔ {L['b_name']}",
                        f"Se cair, fica(m) isolado(s) (via OSPF): " + ", ".join(row["isolated_nodes"][:8]),
                        "Crie um caminho alternativo (outro enlace ou rota por outro POP).", link_id=L["id"]))
        elif worst and worst[2] is not None and worst[2] >= 100:
            F.append(_f("crit", "Topologia", f"Se {L['a_name']} ↔ {L['b_name']} cair, {row['worst_link']} congestiona",
                        f"Estimativa: {worst[2]:.0f}% de uso no enlace que assume o tráfego.",
                        "Aumente a capacidade do caminho alternativo ou ajuste custos para dividir o tráfego.", link_id=L["id"]))
        elif worst and worst[2] is not None and worst[2] >= o["util_crit"]:
            F.append(_f("warn", "Topologia", f"Se {L['a_name']} ↔ {L['b_name']} cair, {row['worst_link']} vai a {worst[2]:.0f}%",
                        "Estimativa com o tráfego atual.", "Avalie a capacidade do caminho alternativo.", link_id=L["id"]))
    scen.sort(key=lambda r: (-r["isolated"], -(r["worst_pct"] or 0)))

    # equipamento ponto único de falha (articulação)
    for n in sorted(g.nodes):
        g2 = g.without(nodes={n})
        d2 = g2.all_dist()
        ok_pairs = set(_pairs(g2, d2))
        lost = [p for p in pairs if n not in p and p not in ok_pairs]
        if lost:
            comp = max(({v for v in d2.get(x, {}) if x in d2.get(v, {})} for x in g2.nodes), key=len, default=set())
            iso = sorted(nname(x) for x in g2.nodes - comp)
            F.append(_f("warn", "Topologia", f"Equipamento ponto único de falha: {nname(n)}",
                        "Se ele parar, fica(m) isolado(s): " + ", ".join(iso[:8]),
                        "Considere redundância (segundo caminho que não passe por ele)."))
    return topo, scen, F


# =====================================================================================
# MPLS (LDP, VPWS, VPLS, L3VPN) — dados coletados pela CLI (mplscli)
# =====================================================================================
LDP_FIX = ("Habilite MPLS/LDP na interface (Huawei: mpls + mpls ldp na interface; Juniper: set protocols ldp interface X e "
           "family mpls; Cisco: mpls ip; DMOS/ZTE: mpls ldp na interface) e ative a sincronia LDP-IGP para o OSPF evitar o "
           "enlace enquanto o LDP não sobe (Huawei: ospf ldp-sync; Juniper: ldp-synchronization; Cisco: mpls ldp sync).")


def pair_usage(g: Graph, only: Optional[set] = None) -> Dict[str, float]:
    """Quantos pares (entre os nós `only`, se dado) passam por cada enlace."""
    dist = g.all_dist()
    use: Dict[str, float] = defaultdict(float)
    for s_, t in _pairs(g, dist):
        if only is not None and (s_ not in only or t not in only):
            continue
        for k, v in g.route(dist, s_, t).items():
            use[k[0]] += v
    return use


def _ldp_on(iface_list: List[dict], name: Optional[str]) -> Optional[bool]:
    if not name:
        return None
    k = mplscli.iface_key(name)
    for x in iface_list:
        if not x.get("active", True):
            continue
        if mplscli.iface_match(x["iface"], name) or mplscli.iface_key(x["iface"]).startswith(k + "."):
            return True
    return False


def mpls_checks(mpls: Dict[str, dict], links: List[dict], g: Graph, topo: dict, node_dev: Dict[str, str],
                data: Dict[str, dict], dname, nname):
    F: List[dict] = []
    dev_node = {d: n for n, d in node_dev.items()}
    # IP -> equipamento (router-id + IPs das interfaces lidos por SNMP)
    ip_dev: Dict[str, str] = {}
    for did, d in data.items():
        if not d.get("ok"):
            continue
        if d.get("router_id"):
            ip_dev[d["router_id"]] = did
        for i in (d.get("ifaces") or {}).values():
            for cidr in i.get("ips") or []:
                ip_dev.setdefault(cidr.split("/")[0], did)
    peer_name = lambda ip: dname(ip_dev[ip]) if ip in ip_dev else ip      # noqa: E731
    sec = lambda did, k: ((mpls.get(did) or {}).get("sections") or {}).get(k) or {}   # noqa: E731
    ok = lambda did, k: bool(sec(did, k).get("ok"))                                  # noqa: E731
    items = lambda did, k: sec(did, k).get("items") or []                             # noqa: E731

    for did, m in mpls.items():
        if m.get("error"):
            F.append(_f("warn", "Coleta", f"Sem leitura MPLS de {dname(did)} (SSH)", m["error"],
                        "Confira usuário/senha SSH do equipamento no BastiON (a leitura MPLS usa a CLI).", device=dname(did)))

    # ---- LDP nos enlaces OSPF (só importa no caminho entre roteadores que rodam MPLS)
    no_ldp = set()
    reported_pairs = set()
    mpls_nodes = {nid for nid, did in node_dev.items()
                  if items(did, "ldp_iface") or any(x["state"] == "up" for x in items(did, "ldp_session"))}
    usage_m = pair_usage(g, mpls_nodes) if mpls_nodes else {}
    if not mpls_nodes and any(ok(did, "ldp_iface") or ok(did, "ldp_session") for did in node_dev.values()):
        F.append(_f("info", "MPLS", "Nenhum LDP ativo nos equipamentos do mapa", "As regras de MPLS nos enlaces foram puladas."))
    for L in links:
        a_dev, b_dev = node_dev.get(L["a"]), node_dev.get(L["b"])
        L["ldp_a"] = _ldp_on(items(a_dev, "ldp_iface"), L.get("a_if")) if ok(a_dev, "ldp_iface") else None
        L["ldp_b"] = _ldp_on(items(b_dev, "ldp_iface"), L.get("b_if")) if ok(b_dev, "ldp_iface") else None
        L["ldp_session"] = None
        if not L.get("in_spf"):
            continue
        where = f"{L['a_name']} {L.get('a_if')} ↔ {L['b_name']} {L.get('b_if')}"
        missing = [nm for nm, v in ((L["a_name"], L["ldp_a"]), (L["b_name"], L["ldp_b"])) if v is False]
        used = usage_m.get(L["id"], 0) > 0
        if missing and not used and not (L["a"] in mpls_nodes and L["b"] in mpls_nodes):
            missing = []            # borda só IP (sem MPLS) e nenhum PE passa por aqui: normal
        if missing:
            no_ldp.add(L["id"])
            F.append(_f("crit" if used else "warn", "MPLS",
                        f"Enlace OSPF sem LDP em {', '.join(missing)}" + ("" if used else " (enlace de reserva)"),
                        ("O OSPF manda tráfego por este enlace, mas sem rótulo MPLS: VPLS, VPWS e L3VPN que passam por aqui caem (buraco negro)."
                         if used else "Hoje ninguém passa por aqui; se o caminho principal cair, o tráfego MPLS vem para cá e as VPNs quebram."),
                        LDP_FIX, link_id=L["id"], where=where))
            continue
        # sessão LDP entre as pontas
        if L["ldp_a"] and L["ldp_b"] and (ok(a_dev, "ldp_session") or ok(b_dev, "ldp_session")):
            def has_up(x_dev, y_dev):
                return any(ip_dev.get(sx["peer"]) == y_dev and sx["state"] == "up" for sx in items(x_dev, "ldp_session"))
            up = has_up(a_dev, b_dev) or has_up(b_dev, a_dev)
            L["ldp_session"] = "up" if up else "down"
            if not up:
                reported_pairs.add(frozenset((a_dev, b_dev)))
                F.append(_f("crit", "MPLS", f"Sem sessão LDP operacional entre {L['a_name']} e {L['b_name']}",
                            "LDP habilitado nas duas interfaces, mas a sessão não está operacional: não há troca de rótulos neste enlace.",
                            "Confira alcance entre as loopbacks (LSR-ID / transport-address), autenticação MD5 do LDP e ACLs para TCP/UDP 646.",
                            link_id=L["id"], where=where))
    # sessões LDP caídas em geral
    ldp_up = ldp_total = 0
    for did in node_dev.values():
        for sx in items(did, "ldp_session"):
            ldp_total += 1
            ldp_up += sx["state"] == "up"
            if sx["state"] == "up":
                continue
            other = ip_dev.get(sx["peer"])
            if other and frozenset((did, other)) in reported_pairs:
                continue
            F.append(_f("crit" if other else "warn", "MPLS", f"Sessão LDP {dname(did)} → {peer_name(sx['peer'])} não operacional",
                        f"Estado: {sx.get('raw_state') or sx['state']}" + ("" if other else " · peer fora deste mapa"),
                        "Confira alcance entre as loopbacks, autenticação do LDP e se o outro lado tem o LDP ativo.", device=dname(did)))

    # cenário: queda de um enlace joga o tráfego num enlace sem LDP
    if no_ldp:
        base = usage_m
        for L in links:
            if not L.get("in_spf") or L["id"] in no_ldp:
                continue
            after = pair_usage(g.without(links={L["id"]}), mpls_nodes)
            hit = [x for x in links if x["id"] in no_ldp and base.get(x["id"], 0) == 0 and after.get(x["id"], 0) > 0]
            for x in hit:
                F.append(_f("crit", "MPLS", f"Se {L['a_name']} ↔ {L['b_name']} cair, o tráfego passa por enlace sem LDP",
                            f"O desvio vai por {x['a_name']} ↔ {x['b_name']}, que não tem LDP: as VPNs param durante a falha.",
                            LDP_FIX, link_id=L["id"]))

    # ---- VPWS (l2vc)
    vc_up = vc_total = 0
    for did in node_dev.values():
        for vc in items(did, "vpws"):
            vc_total += 1
            vc_up += vc["state"] == "up"
            who = f"VC {vc.get('vcid') or '?'}" + (f" ({vc['name']})" if vc.get("name") else "")
            dst = peer_name(vc["peer"]) if vc.get("peer") else "?"
            if vc["state"] != "up":
                if vc.get("ac") == "down":
                    F.append(_f("warn", "VPWS", f"{who} em {dname(did)}: circuito do cliente (AC) DOWN",
                                f"Interface {vc.get('iface') or '?'} → {dst}. O PW não sobe porque a porta do cliente está fora.",
                                "Verifique a porta/cabo/equipamento do cliente.", device=dname(did)))
                else:
                    F.append(_f("crit", "VPWS", f"{who} DOWN em {dname(did)} → {dst}",
                                f"Interface {vc.get('iface') or '?'}" + (f" · estado {vc['raw_state']}" if vc.get("raw_state") else ""),
                                "Confira a sessão LDP com o PE remoto, o mesmo VC ID e tipo/MTU nos dois lados (MTU diferente derruba o PW).",
                                device=dname(did)))
            other = ip_dev.get(vc.get("peer") or "")
            if other and other in dev_node and ok(other, "vpws") and vc.get("vcid"):
                if not any(x.get("vcid") == vc["vcid"] and ip_dev.get(x.get("peer") or "") == did for x in items(other, "vpws")):
                    F.append(_f("warn", "VPWS", f"{who} só existe em {dname(did)}",
                                f"{dname(did)} aponta para {dname(other)}, mas {dname(other)} não tem o VC {vc['vcid']} de volta.",
                                "Configure o mesmo VC ID no PE remoto apontando para este equipamento.", device=dname(did)))
    # ---- VPLS (vsi)
    vsi_up = vsi_total = 0
    for did in node_dev.values():
        for v in items(did, "vpls"):
            vsi_total += 1
            vsi_up += v["state"] == "up"
            pws = f" · PWs {v['pws_up']}/{v['pws']} up" if v.get("pws") else ""
            if v["state"] == "down":
                F.append(_f("crit", "VPLS", f"VSI {v['name']} DOWN em {dname(did)}", "Serviço VPLS parado neste PE" + pws,
                            "Veja se há AC ativo na VSI e se os PWs para os outros PEs sobem (sessões LDP, VSI-ID/peer iguais).",
                            device=dname(did)))
            elif v["state"] == "degraded" or (v.get("pws") and v.get("pws_up") is not None and v["pws_up"] < v["pws"]):
                F.append(_f("warn", "VPLS", f"VSI {v['name']} com PW down em {dname(did)}", pws.strip(" ·"),
                            "Algum PE remoto ficou fora da VSI: confira a sessão LDP com ele e a configuração do peer.",
                            device=dname(did)))
    # ---- L3VPN
    vrfs = 0
    for did in node_dev.values():
        for v in items(did, "vrf"):
            vrfs += 1
            if v.get("routes") == 0:
                F.append(_f("warn", "L3VPN", f"VRF {v['name']} sem rotas em {dname(did)}", "A tabela da VRF está vazia.",
                            "Confira as sessões MP-BGP VPNv4, RT import/export e as interfaces da VRF.", device=dname(did)))
    bgp_up = bgp_total = 0
    for did in node_dev.values():
        for p in items(did, "bgp_vpn"):
            bgp_total += 1
            bgp_up += p["state"] == "up"
            if p["state"] != "up":
                F.append(_f("crit", "L3VPN", f"Sessão MP-BGP VPN caída: {dname(did)} → {peer_name(p['peer'])}",
                            f"AS {p.get('as') or '?'} · sem ela as rotas das VPNs L3 entre esses PEs não são trocadas.",
                            "Confira alcance entre as loopbacks, update-source/connect-interface, AS e a address-family vpnv4 ativa nos dois lados.",
                            device=dname(did)))
            elif p.get("prefixes") == 0:
                F.append(_f("info", "L3VPN", f"Sessão VPNv4 {dname(did)} → {peer_name(p['peer'])} sem rotas recebidas", "",
                            "Normal se o outro PE não tem VRF com rotas; senão confira route-targets e políticas.", device=dname(did)))

    devices = []
    for nid, did in node_dev.items():
        m = mpls.get(did) or {}
        devices.append({"id": did, "name": dname(did), "error": m.get("error"), "device_type": m.get("device_type"),
                        "sections": {k: {"ok": v.get("ok"), "command": v.get("command"), "items": v.get("items") or []}
                                     for k, v in (m.get("sections") or {}).items()}})
    summary = {"devices": sum(1 for d in devices if d["sections"]), "ldp_up": ldp_up, "ldp_total": ldp_total,
               "links_no_ldp": len(no_ldp), "vc_up": vc_up, "vc_total": vc_total, "vsi_up": vsi_up, "vsi_total": vsi_total,
               "vrfs": vrfs, "bgp_vpn_up": bgp_up, "bgp_vpn_total": bgp_total}
    return F, {"summary": summary, "devices": devices, "no_ldp_links": sorted(no_ldp)}
