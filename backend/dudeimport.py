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


# ------------------------------------------------------------------ formato M2 (Dude 6.x/7.x do RouterOS)
# Cada objeto começa com b"M2" e segue com campos [id 3 bytes LE][tipo 1 byte][valor]. Só os campos abaixo são lidos;
# os demais (inclusive usuário/senha/community dos equipamentos) são pulados sem decodificar.
M2_TYPE, M2_NAME = 0xFF0001, 0xFE0010
M2_MAP = 10                     # mapa de rede
M2_ELEM = 74                    # elemento desenhado num mapa (equipamento, submapa, texto, rede ou linha)
M2_DEVICE = 15
M2_DEVTYPE = 14
M2_NETWORK = 16
E_MAP, E_ISLINK, E_KIND, E_ITEM, E_X, E_Y, E_FROM, E_TO = 0x105DC0, 0x105DC2, 0x105DC3, 0x105DC4, 0x105DC5, 0x105DC6, 0x105DDB, 0x105DDC
D_IPS, D_TYPE = 0x101F40, 0x101F4C
M2_WANT = {M2_TYPE, M2_NAME, E_MAP, E_ISLINK, E_KIND, E_ITEM, E_X, E_Y, E_FROM, E_TO, D_IPS, D_TYPE}
NONE32 = 0xFFFFFFFF


def _m2(b: bytes, want=M2_WANT) -> Dict[int, object]:
    i, n, out = 2, len(b), {}
    while i + 4 <= n:
        fid = b[i] | b[i + 1] << 8 | b[i + 2] << 16
        t = b[i + 3]
        i += 4
        base, short = t & 0xF8, t & 1
        v = None
        if base == 0x00:
            v = bool(short)
        elif base == 0x08:
            if short:
                v, i = b[i], i + 1
            else:
                v, i = int.from_bytes(b[i:i + 4], "little"), i + 4
        elif base == 0x10:
            v, i = int.from_bytes(b[i:i + 8], "little"), i + 8
        elif base == 0x18:
            i += 16
        elif base in (0x20, 0x28, 0x30):
            if short:
                ln, i = b[i], i + 1
            else:
                ln, i = int.from_bytes(b[i:i + 2], "little"), i + 2
            if fid in want and base == 0x20:
                v = _s(b[i:i + ln])
            i += ln
        elif base in (0x88, 0x90, 0x98, 0xA0, 0xA8, 0xB0):
            cnt, i = int.from_bytes(b[i:i + 2], "little"), i + 2
            if base == 0x88:
                v = [int.from_bytes(b[i + 4 * k:i + 4 * k + 4], "little") for k in range(cnt)]
                i += 4 * cnt
            elif base == 0x90:
                i += 8 * cnt
            elif base == 0x98:
                i += 16 * cnt
            else:
                for _ in range(cnt):
                    ln = int.from_bytes(b[i:i + 2], "little")
                    i += 2 + ln
        else:
            raise DudeError("campo desconhecido")
        if i > n:
            raise DudeError("objeto truncado")
        if fid in want:
            out[fid] = v
    return out


def _parse_m2(rows) -> dict:
    objs = {}
    for oid, b in rows:
        try:
            d = _m2(bytes(b))
        except Exception:
            continue
        ty = d.get(M2_TYPE) or [None]
        objs[oid] = (ty[0] if ty else None, d)
    kind_of = lambda o: objs.get(o, (None, {}))[0]
    maps, devices, types = {}, {}, {}
    for oid, (ty, d) in objs.items():
        if ty == M2_MAP:
            maps[oid] = {"id": oid, "name": (d.get(M2_NAME) or "").strip() or f"Mapa {oid}", "nodes": [], "links": []}
        elif ty == M2_DEVTYPE:
            types[oid] = (d.get(M2_NAME) or "").strip()
    for oid, (ty, d) in objs.items():
        if ty == M2_DEVICE:
            ips = [socket.inet_ntoa(int(v).to_bytes(4, "little")) for v in (d.get(D_IPS) or []) if v]
            devices[oid] = {"id": oid, "name": (d.get(M2_NAME) or "").strip() or (ips[0] if ips else f"dude-{oid}"), "ips": ips,
                            "type": types.get(d.get(D_TYPE), "")}
    signed = lambda v: v - (1 << 32) if isinstance(v, int) and v >= 1 << 31 else (v or 0)
    for oid, (ty, d) in objs.items():
        if ty != M2_ELEM or d.get(E_MAP) not in maps:
            continue
        m = maps[d[E_MAP]]
        if d.get(E_ISLINK) == 1:                              # linha: de um elemento para outro no mesmo mapa
            a, z = d.get(E_FROM), d.get(E_TO)
            if a not in (None, NONE32) and z not in (None, NONE32):
                m["links"].append({"id": oid, "from": a, "to": z})
            continue
        item, k = d.get(E_ITEM), d.get(E_KIND)
        x, y = signed(d.get(E_X)), signed(d.get(E_Y))
        if k == 0 and kind_of(item) == M2_DEVICE:
            m["nodes"].append({"id": oid, "kind": "device", "ref": item, "x": x, "y": y, "label": ""})
        elif k == 2 and item in maps:
            m["nodes"].append({"id": oid, "kind": "submap", "ref": item, "x": x, "y": y, "label": ""})
        elif k == 3 and (d.get(M2_NAME) or "").strip():
            m["nodes"].append({"id": oid, "kind": "label", "ref": -1, "x": x, "y": y, "label": d[M2_NAME].strip()[:80]})
        elif k == 1 and kind_of(item) == M2_NETWORK:          # "rede" (nuvem) do Dude: vira texto com o nome
            nm = (objs[item][1].get(M2_NAME) or "").strip()
            if nm:
                m["nodes"].append({"id": oid, "kind": "label", "ref": -1, "x": x, "y": y, "label": nm[:80]})
    return {"maps": maps, "devices": devices}


def parse_db(path: str) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute("select id, obj from objs").fetchall()
    except sqlite3.DatabaseError as e:
        raise DudeError(f"Não parece um banco do Dude ({e})")
    finally:
        con.close()
    if sum(1 for _, b in rows[:200] if bytes(b[:2]) == b"M2") > min(len(rows), 200) // 2:
        return _parse_m2(rows)                                 # Dude novo (RouterOS 6.4x / 7)
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

    def bfs(start):
        queue = [start]
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
    for r in roots:
        bfs(r)
    # sobrou (todos apontam uns para os outros, ex.: PRINCIPAL ↔ POPs): o que tem mais submapas vira raiz e desce a partir dele
    while len(seen) < len(maps):
        left = [m for m in maps if m not in seen]
        r = max(left, key=lambda m: (len({x for x in refs[m] if x not in seen}), -left.index(m)))
        roots.append(r)
        bfs(r)

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
