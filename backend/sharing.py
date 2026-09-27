"""Enviar equipamentos, mapas e dashboards de um usuário para outro (cópia).

- Mapas e dashboards levam junto os equipamentos que usam (e os agentes/saltos desses equipamentos).
- Equipamento que o destino já tem com o mesmo host:porta:protocolo é reaproveitado (não duplica).
- Agente igual no destino (mesmo modo/host/porta/túnel/usuário) também é reaproveitado.
- Agente de túnel reverso copiado aponta para o MESMO túnel (mesma porta): funciona sem reinstalar nada.
- Senhas vão junto só se `include_credentials` (continuam criptografadas no cofre).
"""
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return os.urandom(8).hex()


def _dev_key(d: dict) -> tuple:
    return ((d.get("host") or "").strip().lower(), int(d.get("port") or 22), d.get("protocol") or "ssh")


def _agent_key(a: dict) -> tuple:
    return (a.get("mode"), (a.get("host") or "").strip().lower(), int(a.get("port") or 22),
            a.get("tunnel_port"), a.get("username") or "")


def map_device_ids(m: dict) -> List[str]:
    return [n["device_id"] for n in m.get("nodes", []) if n.get("device_id")]


def dash_device_ids(d: dict) -> List[str]:
    out = []
    for w in d.get("widgets", []):
        if w.get("device_id"):
            out.append(w["device_id"])
        out += [s["device_id"] for s in (w.get("sources") or []) if s.get("device_id")]
    return out


async def _unique_name(coll, owner_id: str, name: str, suffix: str) -> str:
    taken = {d["name"] async for d in coll.find({"owner_id": owner_id}, {"_id": 0, "name": 1})}
    if name not in taken:
        return name
    cand = f"{name} ({suffix})"
    i = 2
    while cand in taken:
        cand = f"{name} ({suffix} {i})"
        i += 1
    return cand


async def copy_assets(db, src_uid: str, dst_uid: str, device_ids: List[str], map_ids: List[str],
                      dashboard_ids: List[str], include_credentials: bool = True, src_label: str = "") -> dict:
    maps = [m async for m in db.maps.find({"id": {"$in": list(map_ids)}, "owner_id": src_uid}, {"_id": 0})]
    dashes = [d async for d in db.dashboards.find({"id": {"$in": list(dashboard_ids)}, "owner_id": src_uid}, {"_id": 0})]
    need = set(device_ids)
    for m in maps:
        need |= set(map_device_ids(m))
    for d in dashes:
        need |= set(dash_device_ids(d))

    src_devs = [d async for d in db.devices.find({"id": {"$in": list(need)}, "owner_id": src_uid}, {"_id": 0})]
    dst_by_key = {}
    async for d in db.devices.find({"owner_id": dst_uid}, {"_id": 0, "id": 1, "host": 1, "port": 1, "protocol": 1}):
        dst_by_key.setdefault(_dev_key(d), d["id"])
    dst_agents = {}
    async for a in db.agents.find({"owner_id": dst_uid}, {"_id": 0}):
        dst_agents.setdefault(_agent_key(a), a["id"])

    agent_map: Dict[str, Optional[str]] = {}
    res = {"devices_created": 0, "devices_reused": 0, "agents_created": 0, "agents_reused": 0,
           "maps": 0, "dashboards": 0, "skipped": 0}

    async def copy_agent(aid: Optional[str], depth: int = 0) -> Optional[str]:
        if not aid or depth > 10:
            return None
        if aid in agent_map:
            return agent_map[aid]
        a = await db.agents.find_one({"id": aid, "owner_id": src_uid}, {"_id": 0})
        if not a:
            agent_map[aid] = None
            return None
        parent = await copy_agent(a.get("parent_agent_id"), depth + 1)
        k = _agent_key(a)
        if k in dst_agents:
            agent_map[aid] = dst_agents[k]
            res["agents_reused"] += 1
            return agent_map[aid]
        new = {**a, "id": _new_id(), "owner_id": dst_uid, "parent_agent_id": parent, "copied_from": a["id"],
               "created_at": _now()}
        if not include_credentials:
            new["password"] = ""
        await db.agents.insert_one(dict(new))
        dst_agents[k] = new["id"]
        agent_map[aid] = new["id"]
        res["agents_created"] += 1
        return new["id"]

    dev_map: Dict[str, str] = {}
    for d in src_devs:
        k = _dev_key(d)
        if k in dst_by_key:
            dev_map[d["id"]] = dst_by_key[k]
            res["devices_reused"] += 1
            continue
        new = {**d, "id": _new_id(), "owner_id": dst_uid, "agent_id": await copy_agent(d.get("agent_id")),
               "copied_from": d["id"], "created_at": _now()}
        if not include_credentials:
            new["password"] = ""
        await db.devices.insert_one(dict(new))
        # interfaces já descobertas: o destino já consegue escolher interface sem esperar o SNMP
        cache = await db.device_ifaces.find_one({"device_id": d["id"]}, {"_id": 0})
        if cache:
            await db.device_ifaces.insert_one({**cache, "device_id": new["id"]})
        dst_by_key[k] = new["id"]
        dev_map[d["id"]] = new["id"]
        res["devices_created"] += 1

    suffix = f"de {src_label}" if src_label else "cópia"
    for m in maps:
        nodes, keep = [], set()
        for n in m.get("nodes", []):
            if n.get("device_id"):
                if n["device_id"] not in dev_map:      # equipamento apagado na origem: sai do mapa
                    res["skipped"] += 1
                    continue
                n = {**n, "device_id": dev_map[n["device_id"]]}
            nodes.append(n)
            keep.add(n["id"])
        links = [ln for ln in m.get("links", []) if ln.get("from") in keep and ln.get("to") in keep]
        doc = {**m, "id": _new_id(), "owner_id": dst_uid, "nodes": nodes, "links": links,
               "name": await _unique_name(db.maps, dst_uid, m["name"], suffix),
               "copied_from": m["id"], "created_at": _now(), "updated_at": _now()}
        await db.maps.insert_one(dict(doc))
        res["maps"] += 1

    for d in dashes:
        widgets = []
        for w in d.get("widgets", []):
            w = dict(w)
            if w.get("device_id"):
                if w["device_id"] not in dev_map:
                    res["skipped"] += 1
                    continue
                w["device_id"] = dev_map[w["device_id"]]
            if w.get("sources"):
                w["sources"] = [{**s, "device_id": dev_map[s["device_id"]]} for s in w["sources"] if s.get("device_id") in dev_map]
                if not w["sources"]:
                    res["skipped"] += 1
                    continue
            widgets.append(w)
        doc = {**d, "id": _new_id(), "owner_id": dst_uid, "widgets": widgets,
               "name": await _unique_name(db.dashboards, dst_uid, d["name"], suffix),
               "copied_from": d["id"], "created_at": _now(), "updated_at": _now()}
        await db.dashboards.insert_one(dict(doc))
        res["dashboards"] += 1
    return res


async def viewer_device_ids(db, view_maps: List[str], view_dashboards: List[str]) -> set:
    """Equipamentos que aparecem nos mapas/dashboards liberados para um usuário View."""
    ids = set()
    if view_maps:
        async for m in db.maps.find({"id": {"$in": list(view_maps)}}, {"_id": 0, "nodes": 1}):
            ids |= set(map_device_ids(m))
    if view_dashboards:
        async for d in db.dashboards.find({"id": {"$in": list(view_dashboards)}}, {"_id": 0, "widgets": 1}):
            ids |= set(dash_device_ids(d))
    return ids
