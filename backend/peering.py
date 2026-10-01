"""Sugestão de peering: quem mais chega pelos trânsitos e poderia vir por PTT/PNI/cache.

Junta o tráfego por AS de origem nas interfaces de trânsito (flow) com a presença em IX de cada AS no PeeringDB
(pública, cache de 24 h) e aponta os IXs em comum com o seu AS.
"""
import time
from typing import Awaitable, Callable, Dict, List, Optional

import httpx

import flowstore

PDB = "https://www.peeringdb.com/api"
CACHE_TTL = 86400
CACHE_PROGRAMS = {
    15169: "Google Global Cache (GGC)", 36040: "Google Global Cache (GGC)", 2906: "Netflix Open Connect (OCA)",
    40027: "Netflix Open Connect (OCA)", 32934: "Meta Network Appliance (FNA)", 20940: "Akamai Accelerated Network (AANP)",
    16625: "Akamai Accelerated Network (AANP)", 16509: "Amazon CloudFront embedded POP", 14618: "Amazon CloudFront embedded POP",
    13335: "Cloudflare (PNI/IX; sem cache embarcado)", 8075: "Microsoft (PNI via Peering Portal)",
    46489: "Twitch (PNI)", 54113: "Fastly (PNI/IX)", 22822: "Edgio/Limelight (PNI/IX)",
}


async def _pdb_get(http: httpx.AsyncClient, path: str, params: dict) -> List[dict]:
    kw = dict(params=params, headers={"User-Agent": "BastiON-NOC (peering suggestions)"}, timeout=25)
    try:
        r = await http.get(f"{PDB}/{path}", **kw)
    except httpx.ConnectError:
        # IPv6 sem rota no servidor: tenta de novo só por IPv4
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0")) as v4:
            r = await v4.get(f"{PDB}/{path}", **kw)
    r.raise_for_status()
    return r.json().get("data") or []


async def peeringdb(db, asns: List[int], http: Optional[httpx.AsyncClient] = None) -> Dict[int, dict]:
    """{asn: {name, type, policy, ixs:[{ix_id, name, speed, rs}]}} com cache no Mongo."""
    asns = sorted({int(a) for a in asns if a})
    now = time.time()
    out: Dict[int, dict] = {}
    cached = {d["_id"]: d async for d in db.peeringdb_cache.find({"_id": {"$in": asns}})}
    missing = [a for a in asns if a not in cached or now - cached[a].get("t", 0) > CACHE_TTL]
    for a in asns:
        if a in cached:
            out[a] = cached[a]
    if missing:
        own = http is None
        http = http or httpx.AsyncClient()
        try:
            for i in range(0, len(missing), 40):
                chunk = missing[i:i + 40]
                ids = ",".join(map(str, chunk))
                nets = await _pdb_get(http, "net", {"asn__in": ids, "fields": "asn,name,info_type,policy_general,website"})
                nix = await _pdb_get(http, "netixlan", {"asn__in": ids, "fields": "asn,ix_id,name,speed,is_rs_peer"})
                by_asn: Dict[int, dict] = {a: {"_id": a, "t": now, "found": False, "ixs": []} for a in chunk}
                for n in nets:
                    a = int(n.get("asn") or 0)
                    if a in by_asn:
                        by_asn[a].update(found=True, name=n.get("name"), type=n.get("info_type") or "",
                                         policy=n.get("policy_general") or "", website=n.get("website") or "")
                seen = set()
                for x in nix:
                    a = int(x.get("asn") or 0)
                    if a not in by_asn or (a, x.get("ix_id")) in seen:
                        continue
                    seen.add((a, x.get("ix_id")))
                    by_asn[a]["ixs"].append({"ix_id": x.get("ix_id"), "name": x.get("name") or f"IX {x.get('ix_id')}",
                                             "speed": x.get("speed") or 0, "rs": bool(x.get("is_rs_peer"))})
                for a, d in by_asn.items():
                    await db.peeringdb_cache.replace_one({"_id": a}, d, upsert=True)
                    out[a] = d
        finally:
            if own:
                await http.aclose()
    return out


def _advice(row: dict, mine: set, pdb_ok: bool) -> (str, str):
    """(nível, texto)."""
    gbps = row["transit_avg"] / 1e9
    common = row["common_ixs"]
    tips = []
    level = "baixa"
    if common:
        names = ", ".join(c["name"] for c in common[:3])
        rs = any(c.get("rs") for c in common)
        if row["ix_avg"] > 0:
            tips.append(f"já chega parte pelo PTT ({names}); o resto ainda vem pelo trânsito — confira se recebe todos os prefixos dele")
        elif rs:
            tips.append(f"está no route server de {names}, onde você também está — confira a sessão com o RS e o filtro de import")
        else:
            tips.append(f"vocês dois estão em {names} — peça sessão bilateral" + (" (política aberta)" if row.get("policy") == "Open" else ""))
        level = "alta" if gbps >= 0.1 else "média"
    elif row.get("ixs"):
        names = ", ".join(i["name"] for i in row["ixs"][:3])
        tips.append(f"presente em {names} (você não está lá)")
        level = "média" if gbps >= 1 else "baixa"
    elif pdb_ok and not row.get("pdb_found"):
        tips.append("sem cadastro no PeeringDB")
    if row.get("cache"):
        tips.append(f"programa de cache/PNI: {row['cache']}")
        if gbps >= 1:
            level = "alta"
    if gbps >= 1 and not common:
        tips.append("volume alto: vale avaliar PNI ou cache embarcado")
    return level, "; ".join(tips) or "—"


async def suggestions(db, *, transit_keys: List[str], ix_keys: List[str], days: int, own_asn: int,
                      asn_name: Optional[Callable[[int], str]] = None, http: Optional[httpx.AsyncClient] = None,
                      now: Optional[float] = None) -> dict:
    minutes = max(1, min(int(days), 90)) * 1440
    tr = await flowstore.query(db, keys=transit_keys, minutes=minutes, direction="in", group_by="sas", top=30, now=now)
    ix = await flowstore.query(db, keys=ix_keys, minutes=minutes, direction="in", group_by="sas", top=30, now=now) if ix_keys else None
    ix_by = {r["id"]: r for r in (ix or {}).get("table", [])}
    transit_total = sum(r["avg"] for r in tr["table"])
    rows = []
    for r in tr["table"]:
        if r["id"] == "__other":
            continue
        try:
            asn = int(r["id"])
        except ValueError:
            continue
        if asn == 0 or asn == own_asn:
            continue
        rows.append({"asn": asn, "name": (asn_name(asn) if asn_name else "") or "", "transit_avg": r["avg"], "transit_p95": r["p95"],
                     "transit_max": r["max"], "share": r["avg"] / transit_total if transit_total else 0,
                     "ix_avg": (ix_by.get(r["id"]) or {}).get("avg", 0.0), "cache": CACHE_PROGRAMS.get(asn)})
    pdb, pdb_error = {}, None
    try:
        pdb = await peeringdb(db, [x["asn"] for x in rows] + ([own_asn] if own_asn else []), http)
    except Exception as e:
        pdb_error = f"PeeringDB indisponível agora ({e.__class__.__name__}) — mostrando só o tráfego"
    mine = {i["ix_id"] for i in (pdb.get(own_asn) or {}).get("ixs", [])} if own_asn else set()
    for x in rows:
        p = pdb.get(x["asn"]) or {}
        x["pdb_found"] = bool(p.get("found"))
        x["type"], x["policy"] = p.get("type") or "", p.get("policy") or ""
        if not x["name"] and p.get("name"):
            x["name"] = p["name"]
        x["ixs"] = sorted(p.get("ixs") or [], key=lambda i: -(i.get("speed") or 0))
        x["common_ixs"] = [i for i in x["ixs"] if i["ix_id"] in mine]
        x["level"], x["advice"] = _advice(x, mine, not pdb_error)
    order = {"alta": 0, "média": 1, "baixa": 2}
    rows.sort(key=lambda x: (order[x["level"]], -x["transit_avg"]))
    movable = sum(x["transit_avg"] for x in rows if x["common_ixs"] or (x["cache"] and x["transit_avg"] >= 1e9))
    return {"days": days, "transit_avg": transit_total, "movable_avg": movable,
            "movable_share": movable / transit_total if transit_total else 0, "own_asn": own_asn,
            "own_ixs": sorted({i["name"] for i in (pdb.get(own_asn) or {}).get("ixs", [])}), "own_in_pdb": bool((pdb.get(own_asn) or {}).get("found")),
            "pdb_error": pdb_error, "rows": rows, "transit_ifaces": len(transit_keys), "ix_ifaces": len(ix_keys)}
