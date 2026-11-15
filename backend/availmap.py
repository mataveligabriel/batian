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


def clean_style(b) -> dict:
    """Aparência do mapa: cores (#rrggbb), fundo, formato dos POPs/equipamentos, tamanho e animação das linhas."""
    b = b if isinstance(b, dict) else {}
    out = {k: _color(b.get(k)) for k in COLOR_KEYS if _color(b.get(k))}
    out.update(bg=b.get("bg") if b.get("bg") in BGS else "grid", sub_shape=b.get("sub_shape") if b.get("sub_shape") in SUB_SHAPES else "hex",
               dev_shape=b.get("dev_shape") if b.get("dev_shape") in DEV_SHAPES else "orb", scale=round(_num(b.get("scale"), 0.5, 2, 1), 2),
               flow=bool(b.get("flow", True)), preset=str(b.get("preset") or "")[:20])
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
               "shape": "straight" if ln.get("shape") == "straight" else "smooth"}
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
    for d in dev_ids:
        c[(status.get(d) or {}).get("state") or "pending"] += 1
    total = sum(c.values())
    links_down = sum(1 for k in if_keys if link_state((if_status or {}).get(k)) == "down")
    worst = next((s for s in STATES if c[s]), "pending") if total else "empty"
    if links_down:
        worst = "down"                                 # rota caída também acende o mapa de vermelho
    return {**c, "total": total, "links_down": links_down, "links": len(if_keys), "state": worst}


def parents(maps: Dict[str, dict]) -> Dict[str, List[str]]:
    """map_id -> mapas que o têm como submapa."""
    out: Dict[str, List[str]] = {}
    for mid, m in maps.items():
        for n in m.get("nodes", []):
            if n["kind"] == "submap":
                out.setdefault(n["map_id"], []).append(mid)
    return out
