"""Importação de mapas do MikroTik The Dude para o mapa de disponibilidade.

Lê o dude.db (SQLite; cada objeto na tabela `objs` é uma lista de campos: [tamanho do nome][nome][tamanho 2 bytes][valor]).
Do banco só usamos: mapas (nome, lista de elementos), elementos (equipamento / submapa / texto / ligação, com X e Y),
equipamentos (nome, IP, tipo). Senhas, communities e o resto ficam de fora.
"""
import io
import os
import socket
import sqlite3
import tarfile
from typing import Dict, List, Optional

MAX_DB = 400 * 1024 * 1024
SCALE = 1.6          # o Dude desenha mais compacto; abre um pouco para caber o nome embaixo dos círculos


class DudeError(ValueError):
    pass


def _fields(b: bytes) -> Dict[str, bytes]:
    i, out = 0, {}
    n = len(b)
    while i < n:
        kl = b[i]
        i += 1
        k = b[i:i + kl].decode("latin1")
        i += kl
        if i + 2 > n:
            raise DudeError("objeto truncado")
        ln = int.from_bytes(b[i:i + 2], "big")
        i += 2
        out[k] = b[i:i + ln]
        i += ln
    return out


def _i(v: Optional[bytes], default: int = -1) -> int:
    return int.from_bytes(v[:4], "little", signed=True) if v and len(v) >= 4 else default


def _s(v: Optional[bytes]) -> str:
    if not v:
        return ""
    try:
        return v.decode("utf-8").strip()
    except UnicodeDecodeError:
        return v.decode("latin1").strip()


def extract_db(raw_path: str, out_path: str) -> str:
    """Aceita o .tgz do `/dude export-db` (só o dude.db é lido de dentro dele) ou o dude.db direto."""
    with open(raw_path, "rb") as f:
        head = f.read(16)
    if head.startswith(b"SQLite format 3"):
        return raw_path
    try:
        with tarfile.open(raw_path, "r:*") as tf:
            m = next((m for m in tf.getmembers() if m.isfile() and os.path.basename(m.name) == "dude.db"), None)
            if not m:
                raise DudeError("O arquivo não tem o dude.db dentro")
            if m.size > MAX_DB:
                raise DudeError("dude.db grande demais")
            src = tf.extractfile(m)
            with open(out_path, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
    except tarfile.TarError:
        raise DudeError("Arquivo não reconhecido: mande o .tgz do export do Dude ou o dude.db")
    with open(out_path, "rb") as f:
        if not f.read(16).startswith(b"SQLite format 3"):
            raise DudeError("O dude.db de dentro do arquivo não é um banco válido")
    return out_path


def parse_db(path: str) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute("select id, obj from objs").fetchall()
    except sqlite3.DatabaseError as e:
        raise DudeError(f"Não parece um banco do Dude ({e})")
    finally:
        con.close()
    objs = {}
    for oid, b in rows:
        try:
            objs[oid] = _fields(bytes(b))
        except Exception:
            continue
    maps, lists, devices, types = {}, {}, {}, {}
    for oid, d in objs.items():
        if "elementsID" in d and "ackedColor" in d:
            maps[oid] = {"id": oid, "name": _s(d.get("sys-name")) or f"Mapa {oid}", "nodes": [], "links": []}
            lists[_i(d["elementsID"])] = oid
        elif "addresses" in d and "dnsNames" in d:
            a = d.get("addresses") or b""
            ips = [socket.inet_ntoa(a[k:k + 4]) for k in range(0, len(a) - 3, 4)]
            devices[oid] = {"id": oid, "name": _s(d.get("sys-name")) or (ips[0] if ips else f"dude-{oid}"), "ips": ips, "type_id": _i(d.get("typeID"))}
        elif "snmpSpeed" not in d and "allowedServices" in d:
            types[oid] = _s(d.get("sys-name"))
    for dv in devices.values():
        dv["type"] = types.get(dv.pop("type_id"), "")
    for oid, d in objs.items():
        if "itemType" not in d:
            continue
        mid = lists.get(_i(d.get("sys-type")))
        if mid is None:
            continue
        x, y = _i(d.get("itemX"), 0), _i(d.get("itemY"), 0)
        if _i(d.get("type"), 0) == 1:                       # ligação: de elemento para elemento no mesmo mapa
            a, z = _i(d.get("linkFrom")), _i(d.get("linkTo"))
            if a != -1 and z != -1:
                maps[mid]["links"].append({"id": oid, "from": a, "to": z})
            continue
        kind = {0: "device", 2: "submap", 3: "label"}.get(_i(d.get("itemType")))
        if not kind:
            continue
        el = {"id": oid, "kind": kind, "ref": _i(d.get("itemID")), "x": x, "y": y, "label": ""}
        if kind == "label":
            el["label"] = _s(d.get("sys-name"))
        maps[mid]["nodes"].append(el)
    return {"maps": maps, "devices": devices}


def tree(parsed: dict) -> dict:
    """Hierarquia: raiz = mapa 'Local' (padrão do Dude) e os que ninguém aponta. Submapa que aponta para um mapa de cima
    (o botão de 'voltar' que o Dude costuma ter) é ignorado. Devolve {parent: {map: pai}, roots: [...], skip: {(map, elem)}}."""
    maps = parsed["maps"]
    refs = {m: [n["ref"] for n in maps[m]["nodes"] if n["kind"] == "submap" and n["ref"] in maps] for m in maps}
    incoming = {r for rs in refs.values() for r in rs}
    roots = [m for m in maps if maps[m]["name"].lower() == "local"] or []
    roots += [m for m in maps if m not in incoming and m not in roots]
    parent, order, seen = {}, [], set()
    queue = list(roots)
    while queue:
        m = queue.pop(0)
        if m in seen:
            continue
        seen.add(m)
        order.append(m)
        for r in refs[m]:
            if r not in seen and r not in parent:
                parent[r] = m
                queue.append(r)
    for m in maps:                                         # sobrou (ciclo fechado sem raiz): vira raiz também
        if m not in seen:
            roots.append(m)
            order.append(m)
            seen.add(m)

    def ancestors(m):
        out, c = set(), m
        while c in parent and c not in out:
            c = parent[c]
            out.add(c)
        return out

    local = {m for m in maps if maps[m]["name"].lower() == "local"}
    skip = set()
    for m in maps:
        anc = ancestors(m) | {m} | (local - {m})          # apontar para o "Local" é o botão de voltar do Dude
        for n in maps[m]["nodes"]:
            if n["kind"] == "submap" and (n["ref"] in anc or n["ref"] not in maps):
                skip.add((m, n["id"]))
    return {"parent": parent, "roots": roots, "skip": skip, "order": order}


def device_type(dude_type: str) -> str:
    t = (dude_type or "").lower()
    return "mikrotik" if "mikrotik" in t else "linux" if "windows" not in t and "server" in t else "other"


def preview(parsed: dict, existing_by_ip: Dict[str, dict]) -> dict:
    tr = tree(parsed)
    maps, devs = parsed["maps"], parsed["devices"]
    used = {n["ref"] for m in maps.values() for n in m["nodes"] if n["kind"] == "device" and n["ref"] in devs}
    match, new, noip = [], [], []
    for did in sorted(used, key=lambda d: devs[d]["name"].lower()):
        d = devs[did]
        if not d["ips"]:
            noip.append(d["name"])
            continue
        ex = next((existing_by_ip[ip] for ip in d["ips"] if ip in existing_by_ip), None)
        (match if ex else new).append({"name": d["name"], "ip": d["ips"][0], "type": d["type"], **({"bastion_name": ex["name"]} if ex else {})})

    def node(m, depth):
        kids = [n["ref"] for n in maps[m]["nodes"] if n["kind"] == "submap" and (m, n["id"]) not in tr["skip"] and tr["parent"].get(n["ref"]) == m]
        return {"name": maps[m]["name"], "devices": sum(1 for n in maps[m]["nodes"] if n["kind"] == "device"), "links": len(maps[m]["links"]),
                "children": [node(k, depth + 1) for k in kids] if depth < 6 else []}

    return {"maps": len(maps), "devices_in_maps": len(used), "links": sum(len(m["links"]) for m in maps.values()),
            "match": match, "new": new, "no_ip": noip, "tree": [node(r, 0) for r in tr["roots"]]}


def build_maps(parsed: dict, dev_ids: Dict[int, str], map_ids: Dict[int, str]) -> Dict[str, dict]:
    """Converte para o formato do mapa de disponibilidade. dev_ids/map_ids: id do Dude -> id no BastiON."""
    tr = tree(parsed)
    out = {}
    for mid, m in parsed["maps"].items():
        nodes, nid = [], {}
        for n in m["nodes"]:
            key = f"d{n['id']}"
            x, y = round(n["x"] * SCALE / 10) * 10, round(n["y"] * SCALE / 10) * 10
            if n["kind"] == "device":
                if n["ref"] not in dev_ids:
                    continue
                nodes.append({"id": key, "kind": "device", "device_id": dev_ids[n["ref"]], "label": "", "x": x, "y": y})
            elif n["kind"] == "submap":
                if (mid, n["id"]) in tr["skip"] or n["ref"] not in map_ids:
                    continue
                nodes.append({"id": key, "kind": "submap", "map_id": map_ids[n["ref"]], "label": "", "x": x, "y": y})
            else:
                lab = n["label"]
                if not lab or lab in ("-", ".") or "[" in lab:
                    continue
                nodes.append({"id": key, "kind": "label", "label": lab[:80], "x": x, "y": y})
            nid[n["id"]] = key
        links = [{"id": f"l{l['id']}", "from": nid[l["from"]], "to": nid[l["to"]], "label": "", "snmp": None}
                 for l in m["links"] if l["from"] in nid and l["to"] in nid and l["from"] != l["to"]]
        out[map_ids[mid]] = {"name": m["name"][:80], "nodes": nodes, "links": links, "bg": "grid"}
    return out


def read_upload(data: bytes) -> io.BytesIO:
    return io.BytesIO(data)
