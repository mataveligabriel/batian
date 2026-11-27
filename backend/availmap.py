"""Mapa de disponibilidade (estilo The Dude): mapas com equipamentos e submapas, cor pelo ICMP.

- Cada mapa tem nós de equipamento, de submapa (aponta para outro mapa) e de texto, e ligações entre eles.
- O BastiON pinga (ICMP) os equipamentos que estão em algum mapa, do servidor ou do jump do equipamento.
- Estado do equipamento: up / warn (perda ou latência alta) / down (N ciclos seguidos sem resposta) / pending.
- Submapa mostra o estado só do que está dentro dele (mapa só de submapas olha um nível abaixo).
"""
import os
from typing import Dict, List, Optional, Set

KINDS = ("device", "submap", "label", "note", "shape")     # note = caixa de texto; shape = forma (retângulo/elipse)
STATES = ("down", "warn", "up", "pending")          # ordem de gravidade
RANK = {s: i for i, s in enumerate(STATES)}
IGNORED = "ignored"                                  # ping pausado pelo usuário (fora de STATES: não entra na contagem)
IGNORE_MAX_MIN = 60 * 24 * 90
DEFAULTS = {"interval_s": 30, "down_after": 2, "count": 3, "warn_loss": 34, "warn_ms": 150, "alert": True}
BGS = ("grid", "dots", "radar", "plain")
COLOR_KEYS = ("up", "warn", "down", "pending", "bg0", "bg1", "ink", "dim", "panel", "line")
SUB_SHAPES = ("hex", "circle", "square", "diamond")
DEV_SHAPES = ("orb", "square", "diamond", "dot", "card")
SHAPES = ("rect", "round", "ellipse")
_HEX = __import__("re").compile(r"^#[0-9a-fA-F]{6}$")


def _color(v, default=None):
    v = str(v or "").strip()
    return v if _HEX.match(v) or v == "transparent" else default


def _custom(b: dict) -> bool:
    """Aparência gravada antes de existir "own": conta como própria se alguém mexeu em algo."""
    return bool(any(_color(b.get(k)) for k in COLOR_KEYS) or (b.get("preset") or "neon") != "neon" or (b.get("bg") or "grid") != "grid"
                or (b.get("sub_shape") or "hex") != "hex" or (b.get("dev_shape") or "orb") != "orb")


def clean_style(b) -> dict:
    """Aparência do mapa: cores (#rrggbb), fundo, formato dos POPs/equipamentos, tamanho e animação das linhas."""
    b = b if isinstance(b, dict) else {}
    out = {k: _color(b.get(k)) for k in COLOR_KEYS if _color(b.get(k))}
    out.update(line_w=round(_num(b.get("line_w"), 0.5, 8, 2), 1), line_auto=bool(b.get("line_auto", True)), traffic=bool(b.get("traffic", True)),
               bg=b.get("bg") if b.get("bg") in BGS else "grid", sub_shape=b.get("sub_shape") if b.get("sub_shape") in SUB_SHAPES else "hex",
               dev_shape=b.get("dev_shape") if b.get("dev_shape") in DEV_SHAPES else "orb", scale=round(_num(b.get("scale"), 0.5, 2, 1), 2),
               flow=bool(b.get("flow", True)), preset=str(b.get("preset") or "")[:20],
               own=bool(b["own"]) if "own" in b else _custom(b))   # False = submapa segue a aparência do mapa de cima
    return out


class MapError(ValueError):
    pass


def new_id() -> str:
    return os.urandom(6).hex()


def _num(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def clean_settings(b: dict) -> dict:
    return {"interval_s": int(_num(b.get("interval_s"), 10, 600, 30)), "down_after": int(_num(b.get("down_after"), 1, 10, 2)),
            "count": int(_num(b.get("count"), 1, 10, 3)), "warn_loss": int(_num(b.get("warn_loss"), 1, 100, 34)),
            "warn_ms": int(_num(b.get("warn_ms"), 1, 5000, 150)), "alert": bool(b.get("alert", True))}


def clean_map(b: dict, device_ids: Set[str], map_ids: Set[str], self_id: Optional[str] = None) -> dict:
    name = str(b.get("name") or "").strip()[:80]
    if not name:
        raise MapError("Dê um nome ao mapa")
    nodes, seen = [], set()
    for n in (b.get("nodes") or [])[:2000]:
        kind = n.get("kind") if n.get("kind") in KINDS else None
        nid = str(n.get("id") or "")[:40]
        if not kind or not nid or nid in seen:
            continue
        nd = {"id": nid, "kind": kind, "x": round(_num(n.get("x"), -100000, 100000, 0), 1), "y": round(_num(n.get("y"), -100000, 100000, 0), 1),
              "label": str(n.get("label") or "").strip()[:80]}
        if kind == "device":
            if n.get("device_id") not in device_ids:
                continue
            nd["device_id"] = n["device_id"]
        elif kind == "submap":
            if n.get("map_id") not in map_ids or n.get("map_id") == self_id:
                continue
            nd["map_id"] = n["map_id"]
        elif kind in ("note", "shape"):     # x, y = canto de cima à esquerda
            nd.update(w=round(_num(n.get("w"), 20, 4000, 200)), h=round(_num(n.get("h"), 16, 4000, 80)), text=str(n.get("text") or "")[:2000],
                      fill=_color(n.get("fill"), "#0d1d2e" if kind == "note" else "transparent"), stroke=_color(n.get("stroke"), "#2dd4bf"),
                      color=_color(n.get("color"), "#e3eef8"), opacity=round(_num(n.get("opacity"), 0, 1, 0.9 if kind == "note" else 0.25), 2),
                      size=int(_num(n.get("size"), 9, 48, 13)), dashed=bool(n.get("dashed")),
                      shape=n.get("shape") if n.get("shape") in SHAPES else "round")
        elif not nd["label"]:
            continue
        seen.add(nid)
        nodes.append(nd)
    links, lids = [], set()
    for ln in (b.get("links") or [])[:4000]:
        a, z = ln.get("from"), ln.get("to")
        lid = str(ln.get("id") or new_id())[:40]
        if a not in seen or z not in seen or a == z or lid in lids:
            continue                                  # várias ligações entre os mesmos dois itens são permitidas (rotas)
        lids.add(lid)
        out = {"id": lid, "from": a, "to": z, "label": str(ln.get("label") or "").strip()[:60], "snmp": None,
               "points": [{"x": round(_num(p.get("x"), -100000, 100000, 0), 1), "y": round(_num(p.get("y"), -100000, 100000, 0), 1)}
                          for p in (ln.get("points") or [])[:100] if isinstance(p, dict)],
               "shape": "straight" if ln.get("shape") == "straight" else "smooth",
               "width": round(_num(ln.get("width"), 0, 12, 0), 1),            # 0 = automático (pela capacidade)
               "color": _color(ln.get("color"), ""), "dash": ln.get("dash") if ln.get("dash") in ("solid", "dashed", "dotted") else "solid"}
        sn = ln.get("snmp") or {}
        if sn.get("device_id") in device_ids and str(sn.get("if_index", "")).isdigit():
            out["snmp"] = {"device_id": sn["device_id"], "if_index": int(sn["if_index"]), "if_name": str(sn.get("if_name") or "").strip()[:80]}
        links.append(out)
    return {"name": name, "description": str(b.get("description") or "").strip()[:300], "nodes": nodes, "links": links,
            "bg": b.get("bg") if b.get("bg") in BGS else "grid", "style": clean_style(b.get("style") or {"bg": b.get("bg")})}


def next_state(prev: dict, ok: bool, loss: float, ms: Optional[float], cfg: dict) -> dict:
    """Aplica uma leitura ao estado anterior. Devolve {state, fails, changed}."""
    fails = 0 if ok else int(prev.get("fails") or 0) + 1
    old = prev.get("state") or "pending"
    if not ok:
        state = "down" if fails >= cfg["down_after"] else (old if old != "pending" else "pending")
    elif loss >= cfg["warn_loss"] or (ms is not None and ms >= cfg["warn_ms"]):
        state = "warn"
    else:
        state = "up"
    # transição que vale alerta: entrou ou saiu de down (warn <-> up não alarma)
    changed = (state == "down") != (old == "down") and old != "pending"
    return {"state": state, "fails": fails, "changed": changed}


def _direct_devices(m: dict) -> Set[str]:
    return {n["device_id"] for n in m.get("nodes", []) if n["kind"] == "device"}


def _direct_links(m: dict) -> Set[str]:
    return {if_key(l["snmp"]) for l in m.get("links", []) if l.get("snmp")}


def _scope(map_id: str, maps: Dict[str, dict], direct) -> Set[str]:
    """O que conta para o estado do mapa: só o que está DENTRO dele (não soma submapas — no Dude os POPs apontam uns
    para os outros e tudo virava "fora"). Mapa que só tem submapas (índice, ex.: "Rede") olha um nível abaixo."""
    m = maps.get(map_id)
    if not m:
        return set()
    if any(n["kind"] == "device" for n in m.get("nodes", [])):
        return direct(m)
    out: Set[str] = set()
    for n in m.get("nodes", []):
        if n["kind"] == "submap" and n["map_id"] in maps and n["map_id"] != map_id:
            out |= direct(maps[n["map_id"]])
    return out


def devices_of(map_id: str, maps: Dict[str, dict]) -> Set[str]:
    return _scope(map_id, maps, _direct_devices)


def if_key(sn: dict) -> str:
    return f"{sn['device_id']}|{int(sn['if_index'])}"


def links_of(map_id: str, maps: Dict[str, dict]) -> Set[str]:
    """Interfaces (equip|índice) das ligações monitoradas por SNMP dentro do mapa (mesma regra de devices_of)."""
    m = maps.get(map_id)
    if not m:
        return set()
    if any(n["kind"] == "device" for n in m.get("nodes", [])):
        return _direct_links(m)
    out = _direct_links(m)
    for n in m.get("nodes", []):
        if n["kind"] == "submap" and n["map_id"] in maps and n["map_id"] != map_id:
            out |= _direct_links(maps[n["map_id"]])
    return out


def link_state(st: Optional[dict]) -> str:
    """up / down pela interface (ifOperStatus); unknown sem leitura válida."""
    if not st or st.get("oper") is None:
        return "unknown"
    return "up" if st["oper"] == "up" else "down"


def summarize(dev_ids, status: Dict[str, dict], if_keys=(), if_status: Optional[Dict[str, dict]] = None) -> dict:
    c = {s: 0 for s in STATES}
    ignored = 0
    for d in dev_ids:
        s = (status.get(d) or {}).get("state") or "pending"
        if s == IGNORED:                # ignorado (evento, desligado de propósito): não conta nem acende o mapa
            ignored += 1
            continue
        c[s if s in c else "pending"] += 1
    total = sum(c.values())
    links_down = sum(1 for k in if_keys if link_state((if_status or {}).get(k)) == "down")
    worst = next((s for s in STATES if c[s]), "pending") if total else "empty"
    if links_down:
        worst = "down"                                 # rota caída também acende o mapa de vermelho
    return {**c, "total": total, "ignored": ignored, "links_down": links_down, "links": len(if_keys), "state": worst}


def parents(maps: Dict[str, dict]) -> Dict[str, List[str]]:
    """map_id -> mapas que o têm como submapa."""
    out: Dict[str, List[str]] = {}
    for mid, m in maps.items():
        for n in m.get("nodes", []):
            if n["kind"] == "submap":
                out.setdefault(n["map_id"], []).append(mid)
    return out


def descendants(map_id: str, maps: Dict[str, dict]) -> Set[str]:
    """Todos os mapas abaixo deste (submapas, submapas dos submapas...), sem ele mesmo."""
    out: Set[str] = set()
    todo = [map_id]
    while todo:
        m = maps.get(todo.pop())
        for n in (m or {}).get("nodes", []):
            if n["kind"] == "submap" and n["map_id"] != map_id and n["map_id"] not in out:
                out.add(n["map_id"])
                todo.append(n["map_id"])
    return out


def rate_bps(old, new, dt_s) -> Optional[float]:
    """bits/s entre duas leituras de contador; None sem leitura anterior, contador zerado (reboot) ou intervalo inválido."""
    if not isinstance(old, int) or not isinstance(new, int) or not dt_s or dt_s <= 0 or new < old:
        return None
    return round((new - old) * 8 / dt_s, 1)


def ignore_until(minutes, now) -> Optional[str]:
    """Fim do "ignorar": minutos a partir de agora (até 90 dias); 0/vazio = até reativar (None)."""
    from datetime import timedelta
    try:
        m = int(float(minutes or 0))
    except (TypeError, ValueError):
        raise MapError("Tempo inválido")
    if m < 0:
        raise MapError("Tempo inválido")
    return (now + timedelta(minutes=min(m, IGNORE_MAX_MIN))).isoformat() if m else None


def folder_nodes(child_ids: List[str], cols: int = 6, dx: float = 220, dy: float = 200) -> List[dict]:
    """Nós de submapa em grade para uma "pasta" (mapa que só agrupa outros)."""
    return [{"id": new_id(), "kind": "submap", "map_id": cid, "label": "", "x": round((i % cols) * dx, 1), "y": round((i // cols) * dy, 1)}
            for i, cid in enumerate(child_ids)]


def delete_set(ids, maps: Dict[str, dict], with_submaps: bool = False) -> Set[str]:
    """Mapas que saem numa exclusão em lote. Com with_submaps, saem também os submapas abaixo deles, menos os que
    continuam alcançáveis por algum mapa que fica (ex.: um POP que também está dentro de outro mapa)."""
    sel = {i for i in ids if i in maps}
    if not with_submaps or not sel:
        return sel

    def reach(start, stop):
        seen, q = set(), list(start)
        while q:
            mid = q.pop()
            for n in maps.get(mid, {}).get("nodes", []):
                c = n.get("map_id")
                if n.get("kind") == "submap" and c in maps and c not in seen and c not in stop:
                    seen.add(c)
                    q.append(c)
        return seen
    below = reach(sel, set()) - sel
    outside = set(maps) - sel - below
    kept = reach(outside, sel)                    # o que os mapas que ficam ainda alcançam (sem passar pelos que saem)
    return sel | (below - kept)


def place_submaps(nodes: List[dict], child_ids: List[str], cols: int = 6, dx: float = 220, dy: float = 200) -> List[dict]:
    """Nós de submapa novos em grade, logo abaixo do que já existe no mapa de destino."""
    xs = [n.get("x", 0) for n in nodes] or [0]
    ys = [n.get("y", 0) + (n.get("h") or 0) for n in nodes]
    x0, y0 = min(xs), (max(ys) + dy) if ys else 0
    return [{"id": new_id(), "kind": "submap", "map_id": cid, "label": "",
             "x": round(x0 + (i % cols) * dx, 1), "y": round(y0 + (i // cols) * dy, 1)} for i, cid in enumerate(child_ids)]


def link_targets(ids, target_id: str, maps: Dict[str, dict]):
    """Quais mapas podem entrar como submapa do destino. Devolve (ok, motivos): pula o próprio destino, o que já está
    lá e o que faria o destino ficar dentro de si mesmo (mapa que contém o destino)."""
    have = {n.get("map_id") for n in maps.get(target_id, {}).get("nodes", []) if n.get("kind") == "submap"}
    ok, skip = [], {}
    for i in dict.fromkeys(ids):
        if i not in maps:
            continue
        if i == target_id:
            skip[i] = "é o próprio destino"
        elif i in have:
            skip[i] = "já está no destino"
        elif target_id in descendants(i, maps):
            skip[i] = "o destino está dentro dele"
        else:
            ok.append(i)
    return ok, skip


def _unique_name(name: str, taken: Set[str]) -> str:
    if name.lower() not in taken:
        return name
    k = 1
    while True:
        cand = f"{name} (cópia)" if k == 1 else f"{name} (cópia {k})"
        if cand.lower() not in taken:
            return cand
        k += 1


def copy_maps(ids, maps: Dict[str, dict], with_submaps: bool, taken_names: Set[str]):
    """Cópias independentes dos mapas (e, com with_submaps, de tudo abaixo deles). Os equipamentos são os mesmos
    (mesmos hosts); submapas fora da cópia continuam apontando para o original. Devolve (docs novos, {antigo: novo})."""
    sel = [i for i in dict.fromkeys(ids) if i in maps]
    todo = set(sel)
    if with_submaps:
        for i in sel:
            todo |= descendants(i, maps)
    order = sel + sorted(todo - set(sel), key=lambda x: maps[x].get("name", "").lower())
    idmap = {old: new_id() for old in order}
    taken = {n.lower() for n in taken_names}
    out = []
    for old in order:
        m = maps[old]
        nmap = {n["id"]: new_id() for n in m.get("nodes", [])}
        nodes = []
        for n in m.get("nodes", []):
            nn = {**n, "id": nmap[n["id"]]}
            if n.get("kind") == "submap":
                nn["map_id"] = idmap.get(n.get("map_id"), n.get("map_id"))
            nodes.append(nn)
        links = [{**ln, "id": new_id(), "from": nmap[ln["from"]], "to": nmap[ln["to"]]}
                 for ln in m.get("links", []) if ln.get("from") in nmap and ln.get("to") in nmap]
        name = _unique_name(m.get("name") or "Mapa", taken)
        taken.add(name.lower())
        doc = {"id": idmap[old], "name": name, "description": m.get("description", ""), "nodes": nodes, "links": links,
               "bg": m.get("bg", "grid")}
        if m.get("style"):
            doc["style"] = dict(m["style"])
        out.append(doc)
    return out, idmap


def paste_layout(target: dict, source: Optional[dict], pairs: List[tuple], extra=(), dx: float = 220, dy: float = 200):
    """Coloca no destino os itens colados mantendo a disposição que tinham no mapa de origem, e traz junto as
    ligações (porta SNMP, rótulo, traçado) entre os que foram colados — ou entre eles e o que já estava no destino.
    pairs = [(map_id na origem, map_id no destino, criar_nó)] para submapas; extra = ids de nós da origem que não são
    submapa (equipamento, texto, caixa, forma), copiados como estão. Equipamento que já está no destino não duplica:
    as linhas ligam no que já existe. Devolve (nós novos, ligações novas, quantos equipamentos novos)."""
    tnodes = target.get("nodes", [])
    snodes = (source or {}).get("nodes", [])
    src_sub, src_by_id = {}, {n["id"]: n for n in snodes}
    for n in snodes:
        if n.get("kind") == "submap" and n.get("map_id") not in src_sub:
            src_sub[n["map_id"]] = n
    ex_sub, ex_dev = {}, {}
    for n in tnodes:
        if n.get("kind") == "submap":
            ex_sub.setdefault(n.get("map_id"), n["id"])
        elif n.get("kind") == "device":
            ex_dev.setdefault(n.get("device_id"), n["id"])
    by_src = {}                         # id do nó na origem -> id do nó no destino
    todo = []                           # (nó da origem, nó novo sem posição)
    for om, nm, create in pairs:
        sn = src_sub.get(om)
        if create:
            todo.append((sn, {"id": new_id(), "kind": "submap", "map_id": nm, "label": ""}))
        elif sn and nm in ex_sub:
            by_src[sn["id"]] = ex_sub[nm]
    for nid in dict.fromkeys(extra):
        sn = src_by_id.get(nid)
        if not sn or sn.get("kind") == "submap":
            continue
        if sn.get("kind") == "device" and sn.get("device_id") in ex_dev:
            by_src[sn["id"]] = ex_dev[sn["device_id"]]
            continue
        todo.append((sn, {k: v for k, v in sn.items() if k not in ("x", "y")} | {"id": new_id()}))
    xs = [n.get("x", 0) for n in tnodes] or [0]
    ys = [n.get("y", 0) + (n.get("h") or 0) for n in tnodes]
    x0, y0 = min(xs), (max(ys) + dy) if ys else 0
    placed = [(sn, nn) for sn, nn in todo if sn]
    loose = [nn for sn, nn in todo if not sn]
    off = (0.0, 0.0)
    y_loose = y0
    if placed:
        off = (x0 - min(sn["x"] for sn, _ in placed), y0 - min(sn["y"] for sn, _ in placed))
        y_loose = max(sn["y"] + (sn.get("h") or 0) for sn, _ in placed) + off[1] + dy
    new_nodes = []
    for sn, nn in placed:
        nn.update(x=round(sn["x"] + off[0], 1), y=round(sn["y"] + off[1], 1))
        by_src[sn["id"]] = nn["id"]
        new_nodes.append(nn)
    for i, nn in enumerate(loose):
        nn.update(x=round(x0 + (i % 6) * dx, 1), y=round(y_loose + (i // 6) * dy, 1))
        new_nodes.append(nn)
    old_ids = {n["id"] for n in tnodes}
    have = {(l.get("from"), l.get("to")) for l in target.get("links", [])}
    new_links = []
    for l in (source or {}).get("links", []):
        a, z = by_src.get(l.get("from")), by_src.get(l.get("to"))
        if not a or not z or a == z or (a in old_ids and z in old_ids):
            continue
        if (a, z) in have and not l.get("snmp"):
            continue
        new_links.append({**l, "id": new_id(), "from": a, "to": z,
                          "points": [{"x": round(p["x"] + off[0], 1), "y": round(p["y"] + off[1], 1)} for p in (l.get("points") or [])]})
    n_dev = sum(1 for n in new_nodes if n.get("kind") == "device")
    return new_nodes, new_links, n_dev


VOICE_INTROS = ("beeps", "siren", "klaxon", "radar", "none")


def clean_voice(b: dict) -> dict:
    """Alerta falado do mapa (o navegador que está com o mapa aberto fala)."""
    b = b or {}
    txt = lambda k, d: (str(b.get(k) or "").strip()[:200] or d)
    return {"enabled": bool(b.get("enabled")), "voice": str(b.get("voice") or "")[:120],
            "pitch": round(_num(b.get("pitch"), 0, 2, 0.4), 2), "rate": round(_num(b.get("rate"), 0.5, 1.6, 0.92), 2),
            "volume": round(_num(b.get("volume"), 0, 1, 1), 2), "repeat": int(_num(b.get("repeat"), 1, 3, 1)),
            "intro": b.get("intro") if b.get("intro") in VOICE_INTROS else "beeps",
            "say_name": bool(b.get("say_name", True)), "warn": bool(b.get("warn")),
            "msg_device": txt("msg_device", "Alerta de falha. Equipamento indisponível."),
            "msg_link": txt("msg_link", "Alerta de falha. Possível rompimento."),
            "msg_many": txt("msg_many", "Alerta de falha. {n} equipamentos indisponíveis.")}
