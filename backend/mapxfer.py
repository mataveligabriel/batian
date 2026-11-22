"""Exportar / importar mapas entre BastiONs (topologia e disponibilidade).

O arquivo leva o desenho (nós, ligações, traçados, aparência) e a identificação dos equipamentos usados
(nome, IP, porta, tipo, tags) — nunca usuário, senha, community ou jump. No outro BastiON cada equipamento é
casado pelo IP (+ porta) com um já cadastrado; quem não existe pode ser cadastrado (sem senha) ou vira um
texto/nuvem com o nome, para o desenho não perder o lugar.
"""
import os
from typing import Dict, List, Optional

FORMAT = "bastion-map"
VERSION = 1
MAX_MAPS, MAX_DEVICES = 500, 20000
DEV_FIELDS = ("name", "host", "port", "protocol", "device_type", "tags", "description")


class XferError(ValueError):
    pass


def _new_id() -> str:
    return os.urandom(8).hex()


def _dev_entry(ref: str, d: dict) -> dict:
    out = {"ref": ref}
    for k in DEV_FIELDS:
        v = d.get(k)
        if v not in (None, "", []):
            out[k] = v
    return out


def _dev_refs(ids, devs: Dict[str, dict]):
    refs, entries = {}, []
    for i, did in enumerate(sorted(i for i in ids if i in devs)):
        refs[did] = f"d{i + 1}"
        entries.append(_dev_entry(refs[did], devs[did]))
    return refs, entries


def _head(kind: str, name: str) -> dict:
    from datetime import datetime, timezone
    return {"format": FORMAT, "version": VERSION, "kind": kind, "name": name, "exported_at": datetime.now(timezone.utc).isoformat()}


# ------------------------------------------------------------------ exportar
def export_topology(m: dict, devs: Dict[str, dict]) -> dict:
    ids = {n.get("device_id") for n in m.get("nodes", []) if n.get("kind") == "device"}
    refs, entries = _dev_refs(ids, devs)
    nodes = []
    for n in m.get("nodes", []):
        nd = {k: n.get(k) for k in ("id", "kind", "label", "x", "y", "lat", "lon")}
        if n.get("kind") == "device":
            if n.get("device_id") not in refs:
                continue
            nd["device"] = refs[n["device_id"]]
        nodes.append(nd)
    keep = {n["id"] for n in nodes}
    links = [{k: v for k, v in l.items() if k != "_id"} for l in m.get("links", []) if l.get("from") in keep and l.get("to") in keep]
    return {**_head("topology", m.get("name", "")), "devices": entries,
            "map": {"name": m.get("name", ""), "description": m.get("description", ""), "nodes": nodes, "links": links, "geo": m.get("geo")}}


def descendants(root: str, maps: Dict[str, dict]) -> List[str]:
    order, seen, q = [], {root}, [root]
    while q:
        mid = q.pop(0)
        order.append(mid)
        for n in maps.get(mid, {}).get("nodes", []):
            if n.get("kind") == "submap" and n.get("map_id") in maps and n["map_id"] not in seen:
                seen.add(n["map_id"])
                q.append(n["map_id"])
    return order


def export_avail(root: str, maps: Dict[str, dict], devs: Dict[str, dict], submaps: bool = True) -> dict:
    if root not in maps:
        raise XferError("Mapa não encontrado")
    mids = descendants(root, maps) if submaps else [root]
    mref = {mid: f"m{i + 1}" for i, mid in enumerate(mids)}
    ids = set()
    for mid in mids:
        for n in maps[mid].get("nodes", []):
            if n.get("kind") == "device":
                ids.add(n.get("device_id"))
        for l in maps[mid].get("links", []):
            if l.get("snmp"):
                ids.add(l["snmp"].get("device_id"))
    refs, entries = _dev_refs(ids, devs)
    out_maps = []
    for mid in mids:
        m = maps[mid]
        nodes = []
        for n in m.get("nodes", []):
            nd = {k: v for k, v in n.items() if k not in ("device_id", "map_id")}
            if n.get("kind") == "device":
                if n.get("device_id") not in refs:
                    continue
                nd["device"] = refs[n["device_id"]]
            elif n.get("kind") == "submap":
                if n.get("map_id") not in mref:
                    continue                     # submapa fora do que foi exportado
                nd["map"] = mref[n["map_id"]]
            nodes.append(nd)
        keep = {n["id"] for n in nodes}
        links = []
        for l in m.get("links", []):
            if l.get("from") not in keep or l.get("to") not in keep:
                continue
            ld = {k: v for k, v in l.items() if k != "snmp"}
            sn = l.get("snmp")
            if sn and sn.get("device_id") in refs:
                ld["snmp"] = {"device": refs[sn["device_id"]], "if_index": sn.get("if_index"), "if_name": sn.get("if_name", "")}
            links.append(ld)
        out_maps.append({"ref": mref[mid], "name": m.get("name", ""), "description": m.get("description", ""), "nodes": nodes,
                         "links": links, "bg": m.get("bg"), "style": m.get("style") or {}})
    return {**_head("availability", maps[root].get("name", "")), "root": mref[root], "devices": entries, "maps": out_maps}


# ------------------------------------------------------------------ importar
def check(data) -> dict:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise XferError("Não é um arquivo de mapa exportado pelo BastiON")
    if int(data.get("version") or 0) > VERSION:
        raise XferError("Arquivo de uma versão mais nova do BastiON; atualize este antes de importar")
    if data.get("kind") not in ("topology", "availability"):
        raise XferError("Tipo de mapa desconhecido no arquivo")
    devs = data.get("devices") or []
    if not isinstance(devs, list) or len(devs) > MAX_DEVICES:
        raise XferError("Lista de equipamentos inválida")
    if data["kind"] == "availability" and (not isinstance(data.get("maps"), list) or not data["maps"] or len(data["maps"]) > MAX_MAPS):
        raise XferError("Lista de mapas inválida")
    if data["kind"] == "topology" and not isinstance(data.get("map"), dict):
        raise XferError("Mapa inválido no arquivo")
    return data


def match_devices(entries: List[dict], existing: List[dict]) -> Dict[str, Optional[str]]:
    """ref do arquivo -> id do equipamento deste BastiON (IP+porta; senão só IP quando há um só com aquele IP)."""
    by_hp, by_h = {}, {}
    for d in existing:
        h = str(d.get("host") or "").strip().lower()
        if not h:
            continue
        by_hp.setdefault((h, int(d.get("port") or 22)), d["id"])
        by_h.setdefault(h, []).append(d["id"])
    out = {}
    for e in entries:
        h = str(e.get("host") or "").strip().lower()
        try:
            p = int(e.get("port") or 22)
        except (TypeError, ValueError):
            p = 22
        out[str(e.get("ref"))] = by_hp.get((h, p)) or (by_h[h][0] if len(by_h.get(h, [])) == 1 else None)
    return out


def new_device_rows(entries: List[dict], match: Dict[str, Optional[str]], tag: str = "") -> List[dict]:
    """Equipamentos que não existem aqui, no formato da importação de equipamentos (sem senha)."""
    rows = []
    for e in entries:
        if match.get(str(e.get("ref"))) or not str(e.get("host") or "").strip():
            continue
        tags = [tag] if tag else [t for t in (e.get("tags") or []) if isinstance(t, str)][:20]
        rows.append({"ref": str(e["ref"]), "name": str(e.get("name") or e["host"])[:120], "host": str(e["host"]).strip()[:255],
                     "port": e.get("port") or 22, "protocol": e.get("protocol") or "ssh", "device_type": e.get("device_type") or "linux",
                     "tags": tags, "description": str(e.get("description") or "")[:500]})
    return rows


def build_topology(data: dict, dev: Dict[str, Optional[str]]) -> dict:
    """Corpo para gravar o mapa de topologia: equipamento que não existe aqui vira nuvem com o nome."""
    names = {str(e.get("ref")): e.get("name") or e.get("host") or "?" for e in data.get("devices") or []}
    m = data["map"]
    nodes = []
    for n in m.get("nodes") or []:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        nd = {"id": str(n["id"])[:40], "kind": n.get("kind") if n.get("kind") in ("device", "cloud", "label") else "label",
              "label": str(n.get("label") or "")[:80], "x": n.get("x") or 0, "y": n.get("y") or 0, "lat": n.get("lat"), "lon": n.get("lon"),
              "device_id": None}
        if nd["kind"] == "device":
            did = dev.get(str(n.get("device")))
            if did:
                nd["device_id"] = did
            else:
                nd.update(kind="cloud", label=nd["label"] or names.get(str(n.get("device")), "?"))
        nodes.append(nd)
    return {"name": str(m.get("name") or data.get("name") or "Mapa importado")[:80], "description": str(m.get("description") or "")[:300],
            "nodes": nodes, "links": [l for l in (m.get("links") or []) if isinstance(l, dict)], "geo": m.get("geo")}


def build_avail(data: dict, dev: Dict[str, Optional[str]]) -> Dict[str, dict]:
    """ref do mapa -> {id novo, corpo para availmap.clean_map}. Equipamento que não existe aqui vira texto com o nome."""
    names = {str(e.get("ref")): e.get("name") or e.get("host") or "?" for e in data.get("devices") or []}
    ids = {str(m.get("ref")): _new_id()[:12] for m in data["maps"] if isinstance(m, dict) and m.get("ref")}
    out = {}
    for m in data["maps"]:
        if not isinstance(m, dict) or str(m.get("ref")) not in ids:
            continue
        nodes = []
        for n in m.get("nodes") or []:
            if not isinstance(n, dict):
                continue
            nd = {k: v for k, v in n.items() if k not in ("device", "map")}
            if n.get("kind") == "device":
                did = dev.get(str(n.get("device")))
                if did:
                    nd["device_id"] = did
                else:
                    nd.update(kind="label", label=n.get("label") or names.get(str(n.get("device")), "?"))
            elif n.get("kind") == "submap":
                if str(n.get("map")) not in ids:
                    continue
                nd["map_id"] = ids[str(n["map"])]
            nodes.append(nd)
        links = []
        for l in m.get("links") or []:
            if not isinstance(l, dict):
                continue
            ld = {k: v for k, v in l.items() if k != "snmp"}
            sn = l.get("snmp") or {}
            if sn and dev.get(str(sn.get("device"))):
                ld["snmp"] = {"device_id": dev[str(sn["device"])], "if_index": sn.get("if_index"), "if_name": sn.get("if_name", "")}
            links.append(ld)
        out[str(m["ref"])] = {"id": ids[str(m["ref"])], "body": {"name": m.get("name") or "Mapa importado", "description": m.get("description") or "",
                                                                  "nodes": nodes, "links": links, "bg": m.get("bg"), "style": m.get("style") or {}}}
    return out
