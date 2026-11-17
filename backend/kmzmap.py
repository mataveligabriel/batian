"""Mapas KMZ: lê um KML/KMZ (Google Earth) e transforma em camadas para o mapa do BastiON.

- Pontos viram POP, equipamento (casado pelo nome ou IP com os equipamentos da tag escolhida), caixa (CTO/CEO/poste) ou ponto.
- Linhas viram rotas (cabos), com o traçado simplificado (~1 m) e o comprimento em km.
- Polígonos viram áreas (contorno externo).
- As pastas do KML são mantidas: no mapa dá para ligar/desligar cada uma e dizer o que ela contém.
Nada aqui acessa a rede; só lê o arquivo.
"""
import gzip
import html
import io
import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

MAX_UPLOAD = 40 * 1024 * 1024        # arquivo enviado (KMZ ou KML)
MAX_KML = 80 * 1024 * 1024           # KML descompactado
MAX_POINTS, MAX_LINES, MAX_POLYS = 60000, 25000, 5000
MAX_COORDS = 1_500_000
KINDS = ("auto", "pop", "device", "box", "point", "hidden")

_RE_BOX = re.compile(r"(?<![a-z])(cto|ceo|nap|cx|caixa|caixas|emenda|emendas|poste|postes|splitter|spl|hub|reserva|ancoragem|terminal)s?(?![a-z])", re.I)
_RE_POP = re.compile(r"(?<![a-z])(pop|pops|pts|datacenter|data center|dc|olt|torre|site|central|backbone|bbone|nucleo|núcleo|sede)s?(?![a-z])", re.I)
_RE_IP = re.compile(r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?![\d.])")
_RE_TAG = re.compile(r"<[^>]+>")


class KmzError(ValueError):
    pass


# ------------------------------------------------------------------ leitura do arquivo
def read_upload(raw: bytes) -> str:
    """Devolve o texto do KML (de um .kml ou de dentro de um .kmz)."""
    if len(raw) > MAX_UPLOAD:
        raise KmzError(f"Arquivo maior que {MAX_UPLOAD // 1024 // 1024} MB")
    if raw[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            raise KmzError("KMZ inválido (não é um zip)")
        names = [i for i in z.infolist() if i.filename.lower().endswith(".kml") and not i.is_dir()]
        if not names:
            raise KmzError("O KMZ não tem nenhum arquivo .kml dentro")
        pick = next((i for i in names if i.filename.lower().rsplit("/", 1)[-1] == "doc.kml"), None) or max(names, key=lambda i: i.file_size)
        if pick.file_size > MAX_KML:
            raise KmzError("KML dentro do KMZ é grande demais")
        with z.open(pick) as f:
            data = f.read(MAX_KML + 1)              # não confia no tamanho declarado no zip
        if len(data) > MAX_KML:
            raise KmzError("KML dentro do KMZ é grande demais")
    else:
        data = raw
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    head = text[:4096].lower()
    if "<!doctype" in head or "<!entity" in text[:200000].lower():
        raise KmzError("KML com DOCTYPE/ENTITY não é aceito")
    if "<kml" not in text[:20000].lower():
        raise KmzError("Não parece um KML do Google Earth")
    return text


# ------------------------------------------------------------------ utilidades
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _text(el, name, limit=200) -> str:
    c = _child(el, name)
    return (c.text or "").strip()[:limit] if c is not None and c.text else ""


def kml_color(v: str) -> Optional[str]:
    """KML usa aabbggrr; devolve #rrggbb."""
    v = (v or "").strip().lstrip("#")
    if len(v) != 8 or not re.fullmatch(r"[0-9a-fA-F]{8}", v):
        return None
    return f"#{v[6:8]}{v[4:6]}{v[2:4]}".lower()


def plain(desc: str, limit=600) -> str:
    """Descrição do KML (muitas vezes HTML) em texto simples."""
    t = re.sub(r"<\s*(br|/p|/div|/tr|/li)\s*/?>", "\n", desc or "", flags=re.I)
    t = html.unescape(_RE_TAG.sub(" ", t))
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    return t[:limit]


def parse_coords(s: str) -> List[Tuple[float, float]]:
    out = []
    for tok in (s or "").split():
        p = tok.split(",")
        if len(p) < 2:
            continue
        try:
            lon, lat = float(p[0]), float(p[1])
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out.append((round(lat, 6), round(lon, 6)))
    return out


def haversine_km(a, b) -> float:
    r = 6371.0088
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(h)))


def length_km(pts) -> float:
    return round(sum(haversine_km(pts[i], pts[i + 1]) for i in range(len(pts) - 1)), 3)


def simplify(pts, eps=0.00001):
    """Ramer–Douglas–Peucker em graus (eps 0,00001° ≈ 1 m); mantém início e fim."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (y1, x1), (y2, x2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1e-12
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            y0, x0 = pts[i]
            d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / n
            if d > best:
                best, idx = d, i
        if best > eps and idx > 0:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[áàâãä]", "a", s)
    s = re.sub(r"[éèêë]", "e", s)
    s = re.sub(r"[íìîï]", "i", s)
    s = re.sub(r"[óòôõö]", "o", s)
    s = re.sub(r"[úùûü]", "u", s)
    s = s.replace("ç", "c")
    return re.sub(r"[^a-z0-9]+", "", s)


# ------------------------------------------------------------------ estilos
def _styles(root) -> Dict[str, dict]:
    st: Dict[str, dict] = {}
    for el in root.iter():
        t = _local(el.tag)
        sid = el.get("id")
        if t == "Style" and sid:
            ls, ic, ps = _child(el, "LineStyle"), _child(el, "IconStyle"), _child(el, "PolyStyle")
            d = {}
            if ls is not None:
                d["line"] = kml_color(_text(ls, "color"))
                try:
                    d["width"] = max(0.5, min(12.0, float(_text(ls, "width") or 0))) or None
                except ValueError:
                    pass
            if ic is not None:
                d["icon_color"] = kml_color(_text(ic, "color"))
                icon = _child(ic, "Icon")
                d["icon"] = _text(icon, "href", 300) if icon is not None else ""
            if ps is not None:
                d["fill"] = kml_color(_text(ps, "color"))
            st["#" + sid] = d
    for el in root.iter():                     # StyleMap: usa o "normal"
        if _local(el.tag) == "StyleMap" and el.get("id"):
            for pair in _children(el, "Pair"):
                if _text(pair, "key") == "normal":
                    st["#" + el.get("id")] = st.get(_text(pair, "styleUrl"), {})
    return st


def _style_of(pm, styles) -> dict:
    url = _text(pm, "styleUrl", 300)
    s = dict(styles.get(url, {})) if url.startswith("#") else {}
    inline = _child(pm, "Style")
    if inline is not None:
        tmp = ET.Element("x")
        inline.set("id", "__inline")
        tmp.append(inline)
        s.update({k: v for k, v in _styles(tmp).get("#__inline", {}).items() if v})
    return s


# ------------------------------------------------------------------ leitura do KML
def parse(text: str) -> dict:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise KmzError(f"KML inválido: {e}")
    styles = _styles(root)
    folders: List[dict] = []
    fid_of: Dict[str, str] = {}
    out = {"points": [], "lines": [], "polys": []}
    total = [0]
    doc_name = ""
    for el in root.iter():
        if _local(el.tag) == "Document":
            doc_name = _text(el, "name", 120)
            break

    def folder_id(path: List[str]) -> str:
        key = " / ".join(path) or "(sem pasta)"
        if key not in fid_of:
            fid_of[key] = f"f{len(folders) + 1}"
            folders.append({"id": fid_of[key], "path": key, "name": path[-1] if path else "(sem pasta)", "kind": "auto",
                            "visible": True, "points": 0, "lines": 0, "polys": 0})
        return fid_of[key]

    def ext_data(pm) -> Dict[str, str]:
        ed = _child(pm, "ExtendedData")
        res: Dict[str, str] = {}
        if ed is None:
            return res
        for d in ed.iter():
            t = _local(d.tag)
            if t == "Data" and d.get("name"):
                res[d.get("name")[:40]] = plain(_text(d, "value", 300), 200)
            elif t == "SimpleData" and d.get("name"):
                res[d.get("name")[:40]] = plain(d.text or "", 200)
            if len(res) >= 15:
                break
        return res

    def geoms(g):
        t = _local(g.tag)
        if t == "MultiGeometry":
            for c in g:
                yield from geoms(c)
        elif t in ("Point", "LineString", "LinearRing"):
            yield t, parse_coords(_text(g, "coordinates", 50_000_000))
        elif t == "Polygon":
            ob = _child(g, "outerBoundaryIs")
            ring = _child(ob, "LinearRing") if ob is not None else None
            if ring is not None:
                yield "Polygon", parse_coords(_text(ring, "coordinates", 50_000_000))
        elif t == "Track":                     # gx:Track — vira linha
            pts = []
            for c in g:
                if _local(c.tag) == "coord" and c.text:
                    p = c.text.split()
                    if len(p) >= 2:
                        pts += parse_coords(f"{p[0]},{p[1]}")
            yield "LineString", pts

    def placemark(pm, path):
        fid = folder_id(path)
        f = folders[int(fid[1:]) - 1]
        name = _text(pm, "name", 120)
        desc = plain(_text(pm, "description", 20000))
        st = _style_of(pm, styles)
        ext = ext_data(pm)
        for t, pts in (x for g in pm for x in geoms(g)):
            if not pts:
                continue
            if t == "Point":
                if len(out["points"]) >= MAX_POINTS:
                    continue
                lat, lon = pts[0]
                out["points"].append({"id": f"p{len(out['points']) + 1}", "f": fid, "name": name, "lat": lat, "lon": lon,
                                      "desc": desc, "ext": ext, "color": st.get("icon_color"), "icon": st.get("icon", "")})
                f["points"] += 1
            elif t in ("LineString", "LinearRing"):
                if len(out["lines"]) >= MAX_LINES or len(pts) < 2:
                    continue
                sp = simplify(pts)
                if total[0] + len(sp) > MAX_COORDS:
                    continue
                total[0] += len(sp)
                out["lines"].append({"id": f"l{len(out['lines']) + 1}", "f": fid, "name": name, "desc": desc, "ext": ext,
                                     "pts": [[a, b] for a, b in sp], "km": length_km(pts),
                                     "color": st.get("line"), "width": st.get("width")})
                f["lines"] += 1
            elif t == "Polygon":
                if len(out["polys"]) >= MAX_POLYS or len(pts) < 3:
                    continue
                sp = simplify(pts, 0.00003)
                if total[0] + len(sp) > MAX_COORDS:
                    continue
                total[0] += len(sp)
                out["polys"].append({"id": f"a{len(out['polys']) + 1}", "f": fid, "name": name, "desc": desc,
                                     "pts": [[a, b] for a, b in sp], "color": st.get("line"), "fill": st.get("fill")})
                f["polys"] += 1

    def walk(el, path):
        for c in el:
            t = _local(c.tag)
            if t in ("Folder", "Document"):
                nm = _text(c, "name", 120)
                walk(c, path + [nm] if (t == "Folder" and nm) else path)
            elif t == "Placemark":
                placemark(c, path)

    walk(root, [])
    folders = [f for f in folders if f["points"] + f["lines"] + f["polys"]]
    if not folders:
        raise KmzError("Não achei pontos, linhas ou áreas no KML")
    return {"name": doc_name, "folders": folders, **out}


# ------------------------------------------------------------------ classificação
def classify(data: dict, devices: List[dict]) -> dict:
    """Marca o tipo de cada ponto (pop / device / box / point) e casa com os equipamentos da tag pelo nome ou IP."""
    by_ip = {d["host"]: d for d in devices if d.get("host")}
    by_name = {}
    for d in devices:
        k = _norm(d.get("name"))
        if len(k) >= 3:
            by_name.setdefault(k, d)
    fpath = {f["id"]: f["path"] for f in data["folders"]}
    matched = set()
    for p in data["points"]:
        hay = " ".join([p["name"], p.get("desc", ""), " ".join(p.get("ext", {}).values())])
        dev = None
        for ip in _RE_IP.findall(hay):
            if ip in by_ip:
                dev = by_ip[ip]
                break
        if dev is None:
            dev = by_name.get(_norm(p["name"]))
        if dev:
            p.update(kind="device", device_id=dev["id"])
            matched.add(dev["id"])
            continue
        ctx = f"{p['name']} {fpath.get(p['f'], '')}"
        if _RE_BOX.search(ctx) or _RE_BOX.search(p.get("icon", "")):
            p["kind"] = "box"
        elif _RE_POP.search(ctx):
            p["kind"] = "pop"
        else:
            p["kind"] = "point"
    # pasta: o tipo mais comum dos pontos vira o "auto" mostrado na tela
    for f in data["folders"]:
        kinds = [p["kind"] for p in data["points"] if p["f"] == f["id"]]
        f["auto"] = max(set(kinds), key=kinds.count) if kinds else ("route" if f["lines"] else "area")
    return {"matched": len(matched), "devices": len(devices)}


def bbox(data: dict) -> Optional[dict]:
    lats, lons = [], []
    for p in data["points"]:
        lats.append(p["lat"]); lons.append(p["lon"])
    for ln in data["lines"] + data["polys"]:
        for a, b in ln["pts"]:
            lats.append(a); lons.append(b)
    if not lats:
        return None
    return {"s": min(lats), "w": min(lons), "n": max(lats), "e": max(lons)}


def summary(data: dict) -> dict:
    kinds = {}
    for p in data["points"]:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    return {"points": len(data["points"]), "lines": len(data["lines"]), "polys": len(data["polys"]), "kinds": kinds,
            "km": round(sum(ln["km"] for ln in data["lines"]), 1), "folders": len(data["folders"])}


def pack(data: dict) -> bytes:
    return gzip.compress(json.dumps({k: data[k] for k in ("points", "lines", "polys")}, separators=(",", ":")).encode(), 6)


def unpack(blob: bytes) -> dict:
    return json.loads(gzip.decompress(blob).decode())


def clean_folders(new: list, old: list) -> list:
    """Edição das pastas: só tipo e visível; o resto vem do que foi importado."""
    want = {str(f.get("id")): f for f in (new or []) if isinstance(f, dict)}
    out = []
    for f in old:
        n = want.get(f["id"]) or {}
        out.append({**f, "kind": n.get("kind") if n.get("kind") in KINDS else f.get("kind", "auto"),
                    "visible": bool(n.get("visible", f.get("visible", True)))})
    return out
