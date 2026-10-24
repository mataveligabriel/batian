"""Monitor BGP: sessões dos roteadores e os seus prefixos vistos de fora.

Sessões — por SNMP (BGP4-MIB: estado, AS remoto, tempo estabelecido; prefixos aceitos pela CISCO-BGP4-MIB quando o
equipamento tem) ou pela CLI (resumo do BGP: Huawei/Cisco/Datacom/ZTE/Juniper), que traz também os prefixos.

Prefixos próprios — pelos dados públicos do RIPE RIS (RIPEstat): quem origina (sequestro), sub-prefixos de outra
origem (sequestro de mais específico), visibilidade (sumiu da Internet), caminho pelo upstream esperado
(vazamento) e o estado RPKI do anúncio.
"""
import ipaddress
import re
from typing import Dict, List, Optional

BGP_PEER = "1.3.6.1.2.1.15.3.1"
COL = {"2": "state", "3": "admin", "9": "asn", "14": "last_error", "16": "established_s", "24": "in_update_s"}
STATES = {1: "idle", 2: "connect", 3: "active", 4: "opensent", 5: "openconfirm", 6: "established"}
CISCO_ACCEPTED = "1.3.6.1.4.1.9.9.187.1.2.4.1.1"          # cbgpPeerAcceptedPrefixes.<ip>.<afi>.<safi>
RIPESTAT = "https://stat.ripe.net/data"

DEFAULTS = {"routers": [], "interval_min": 5, "cli_interval_min": 30, "prefix_drop_pct": 50, "alert_sessions": True,
            "prefixes": [], "ripe_interval_min": 15, "visibility_min_pct": 50, "alert_prefixes": True, "peer_names": {}}


def peers_from_walk(walk: Dict[str, object]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for oid, val in walk.items():
        rest = oid[len(BGP_PEER) + 1:] if oid.startswith(BGP_PEER + ".") else ""
        col, _, ip = rest.partition(".")
        if col not in COL or ip.count(".") != 3:
            continue
        p = out.setdefault(ip, {"peer": ip})
        k = COL[col]
        if k == "state":
            p["state"] = STATES.get(val, str(val)) if isinstance(val, int) else str(val)
        elif k == "admin":
            p["admin"] = "stop" if val == 1 else "start"
        elif k == "last_error":
            p["last_error"] = val.hex() if isinstance(val, bytes) else ("" if val is None else str(val))
        else:
            p[k] = val if isinstance(val, int) else None
    return out


def cisco_prefixes(walk: Dict[str, object]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for oid, val in walk.items():
        if not oid.startswith(CISCO_ACCEPTED + ".") or not isinstance(val, int):
            continue
        parts = oid[len(CISCO_ACCEPTED) + 1:].split(".")
        if len(parts) >= 6:
            ip = ".".join(parts[:4])
            out[ip] = out.get(ip, 0) + val
    return out


_IP = r"(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7})"
_STATE_WORDS = {"established": "established", "establ": "established", "idle": "idle", "active": "active", "connect": "connect",
                "opensent": "opensent", "openconfirm": "openconfirm", "idle(admin)": "idle", "admin": "idle"}


def parse_cli_summary(text: str) -> Dict[str, dict]:
    """Resumo do BGP de vários fabricantes -> {peer: {asn, state, prefixes}}.
    Cisco/ZTE/Datacom: 'vizinho V AS ... Up/Down Estado|PfxRcd'; Huawei: '... Up/Down State PrefRcv';
    Juniper: 'peer AS InPkt OutPkt OutQ Flaps Last State|#Active/Received/Accepted' (+ linhas 'inet.0: a/r/c/d')."""
    out: Dict[str, dict] = {}
    last = None
    for ln in (text or "").splitlines():
        m = re.match(r"^\s*\*?\s*" + _IP + r"\s+(.*)$", ln)
        if not m:
            jm = re.match(r"^\s+[\w.\-]+\.\d+:\s*(\d+)/(\d+)/(\d+)/(\d+)", ln)        # Juniper: inet.0: 10/20/20/0
            if jm and last:
                out[last]["prefixes"] = (out[last].get("prefixes") or 0) + int(jm.group(3))
            continue
        peer, rest = m.group(1), m.group(2).split()
        try:
            ipaddress.ip_address(peer)
        except ValueError:
            continue
        if len(rest) < 3:
            continue
        nums = [t for t in rest if re.fullmatch(r"\d+", t)]
        asn = None
        if rest[0] in ("4", "6") and len(rest) > 1 and rest[1].isdigit():           # Cisco/Huawei: V AS
            asn = int(rest[1])
        elif rest[0].isdigit():                                                     # Juniper: AS primeiro
            asn = int(rest[0])
        tail = rest[-1]
        low = tail.lower()
        state, pfx = None, None
        jm = re.fullmatch(r"(\d+)/(\d+)/(\d+)/(\d+)", tail)
        if jm:
            state, pfx = "established", int(jm.group(3))
        elif low.rstrip("*") in _STATE_WORDS:
            state = _STATE_WORDS[low.rstrip("*")]
        elif re.fullmatch(r"\d+", tail):
            # Huawei: ... Established 900000; Cisco: ... 1d02h 900000 (número no fim = estabelecida)
            prev = rest[-2].lower() if len(rest) >= 2 else ""
            state = _STATE_WORDS.get(prev, "established")
            pfx = int(tail)
        elif "(" in tail or low.startswith("idle"):
            state = "idle"
        if state is None:
            continue
        p = {"peer": peer, "asn": asn, "state": state}
        if pfx is not None:
            p["prefixes"] = pfx
        out[peer] = p
        last = peer
        _ = nums
    return out


CLI_SUMMARY = {"huawei": ["display bgp peer", "display bgp ipv6 peer"], "cisco": ["show bgp all summary"],
               "zte": ["show ip bgp summary"], "datacom": ["show ip bgp summary"], "juniper": ["show bgp summary"],
               "other": ["show ip bgp summary"]}


# ---------------------------------------------------------------- prefixos (RIPEstat)
def clean_prefixes(rows: list) -> list:
    out = []
    for r in rows or []:
        try:
            net = str(ipaddress.ip_network(str(r.get("prefix", "")).strip(), strict=False))
        except ValueError:
            raise ValueError(f"Prefixo inválido: {r.get('prefix')}")
        origins = sorted({int(str(a).upper().replace("AS", "")) for a in (r.get("origins") or []) if str(a).strip()})
        ups = sorted({int(str(a).upper().replace("AS", "")) for a in (r.get("upstreams") or []) if str(a).strip()})
        if not origins:
            raise ValueError(f"Informe o AS de origem esperado de {net}")
        out.append({"prefix": net, "origins": origins, "upstreams": ups, "note": str(r.get("note") or "")[:80]})
    return out[:200]


def _asn(v) -> Optional[int]:
    try:
        return int(str(v).upper().replace("AS", "").strip())
    except (TypeError, ValueError):
        return None


def paths_from_lg(lg: dict) -> List[List[int]]:
    """looking-glass do RIPEstat -> lista de caminhos AS (de quem vê até a origem)."""
    paths = []
    for rrc in (lg or {}).get("rrcs") or []:
        for p in rrc.get("peers") or []:
            ap = p.get("as_path") or ""
            asns = [a for a in (_asn(x) for x in str(ap).replace("{", " ").replace("}", " ").split()) if a]
            if asns:
                dedup = [asns[0]] + [a for i, a in enumerate(asns[1:], 1) if a != asns[i - 1]]   # tira o prepend
                paths.append(dedup)
    return paths


def evaluate_prefix(cfg: dict, rs: Optional[dict], lg: Optional[dict], rpki: Dict[str, str], vis_min: float) -> dict:
    """-> {seen_origins, visibility_pct, more_specifics, findings:[{code, level, text}]}"""
    want = set(cfg["origins"])
    findings = []
    rs = rs or {}
    origins = sorted({a for a in (_asn(o.get("origin")) for o in rs.get("origins") or []) if a})
    fam = "v6" if ":" in cfg["prefix"] else "v4"
    vis = (rs.get("visibility") or {}).get(fam) or {}
    seeing, total = vis.get("ris_peers_seeing"), vis.get("total_ris_peers")
    pct = round(100.0 * seeing / total, 1) if seeing is not None and total else None
    if rs and not origins:
        findings.append({"code": "unseen", "level": "high", "text": "Prefixo não aparece na tabela global (nenhuma origem vista pelo RIPE RIS)"})
    bad_orig = [a for a in origins if a not in want]
    if bad_orig:
        findings.append({"code": "hijack", "level": "critical",
                         "text": f"Anunciado por AS{', AS'.join(map(str, bad_orig))} (esperado AS{', AS'.join(map(str, sorted(want)))}) — possível sequestro"})
    ms = []
    for m in rs.get("more_specifics") or []:
        o = _asn(m.get("origin"))
        ms.append({"prefix": m.get("prefix"), "origin": o})
    bad_ms = [m for m in ms if m["origin"] and m["origin"] not in want]
    if bad_ms:
        findings.append({"code": "subhijack", "level": "critical",
                         "text": "Sub-prefixo de outra origem: " + ", ".join(f"{m['prefix']} por AS{m['origin']}" for m in bad_ms[:5])})
    if pct is not None and origins and pct < vis_min:
        findings.append({"code": "visibility", "level": "high", "text": f"Visível só para {pct}% dos coletores do RIPE RIS (mínimo {vis_min:g}%)"})
    for asn, st in rpki.items():
        if st and st.startswith("invalid"):
            findings.append({"code": "rpki", "level": "high",
                             "text": f"RPKI inválido para AS{asn} ({'AS diferente do ROA' if st == 'invalid_asn' else 'prefixo mais longo que o maxLength do ROA'})"})
    leak = {}
    if cfg.get("upstreams") and lg:
        ups = set(cfg["upstreams"])
        for path in paths_from_lg(lg):
            if len(path) >= 2 and path[-1] in want and path[-2] not in ups and path[-2] not in want:
                leak[path[-2]] = leak.get(path[-2], 0) + 1
        if leak:
            top = sorted(leak.items(), key=lambda x: -x[1])[:3]
            findings.append({"code": "leak", "level": "medium",
                             "text": "Chegando por upstream não esperado: " + ", ".join(f"AS{a} ({n} caminho(s))" for a, n in top) + " — possível vazamento"})
    return {"seen_origins": origins, "visibility_pct": pct, "more_specifics": ms[:50], "rpki": rpki, "leak": leak, "findings": findings}
