"""Migração de ONUs entre OLTs — qualquer combinação: ZTE C300 -> TITAN (C600/C650/C620), Huawei -> ZTE,
FiberHome -> ZTE, ZTE -> Huawei, ZTE -> ZTE…

1. Origem: as ONUs de cada PON com serial, tipo, nome, descrição e serviços (VLANs, modo das portas, perfil de banda),
   lidos da configuração da OLT (backup ou leitura na hora — ZTE e Huawei), da autorização da FiberHome (sem VLAN: usa
   o serviço padrão) ou de uma planilha CSV (qualquer fabricante).
2. Plano: o usuário escolhe QUAIS PONs migrar (dá para migrar por partes) e para qual PON de destino vai cada uma; a
   política de ID (manter ou renumerar), troca de VLAN, tipo de ONU, perfil de banda / line+srv profile.
3. Saída, por PON: script de autorização no destino, script de remoção na origem (depois que a fibra mudou) e o
   "desfazer" (remove do destino). Nada é aplicado aqui: a API aplica PON por PON, com confirmação.

Senha de PPPoE nunca é lida nem guardada: ONU roteada migra só com a VLAN (a WAN fica na própria ONU / TR-069).
"""
import csv
import io
import re
from typing import Dict, List, Optional, Tuple

import oltfh
import oltgen
import olthw
from oltgen import GenError

VENDORS = {"zte": "ZTE", "huawei": "Huawei", "fiberhome": "FiberHome"}
DEST_KINDS = {"zte-titan": "ZTE TITAN (C600 / C650 / C620 / C610)", "zte-c300": "ZTE C300 / C320",
              "huawei": "Huawei (MA5800 / MA5680T / MA5683T / MA5608T)", "fiberhome": "FiberHome AN5516"}
MODES = {"bridge": "Bridge (VLAN sem tag na porta)", "transparent": "Transparente / com tag", "router": "Roteada (WAN na ONU)"}
MAX_PER_PON = 128
SN_RE = re.compile(r"^[A-Z0-9]{4}[0-9A-F]{8}$")


class MigError(ValueError):
    pass


def norm_sn(v: str) -> str:
    """Qualquer formato -> 4 letras + 8 hexa (o hexa de 16 do Huawei vira HWTC…). Vazio se não é serial."""
    s = re.sub(r"[\s:\"-]", "", str(v or "")).upper()
    if re.fullmatch(r"[0-9A-F]{16}", s):
        try:
            ven = bytes.fromhex(s[:8]).decode("ascii")
            if re.fullmatch(r"[A-Z0-9]{4}", ven):
                return ven + s[8:]
        except (ValueError, UnicodeDecodeError):
            pass
        return ""
    return s if SN_RE.fullmatch(s) and not re.fullmatch(r"[0-9A-F]{12}", s) else ""


def _onu(pon: str, onu: int, sn_raw: str, **kw) -> dict:
    sn = norm_sn(sn_raw)
    d = {"pon": pon, "onu": int(onu), "sn": sn, "sn_raw": str(sn_raw or "")[:40], "type": "", "name": "", "description": "",
         "services": [], "pppoe": [], "tcont": "", "profile": "", "auth": "sn" if sn else "outro"}
    d.update({k: v for k, v in kw.items() if v is not None})
    return d


# =====================================================================================
# Origem: ZTE (C300 e TITAN) — running-config
# =====================================================================================
_Z_OLT = re.compile(r"^interface\s+gpon[-_]olt[-_]1/(\d+)/(\d+)\s*$", re.I)
_Z_ONU = re.compile(r"^interface\s+gpon[-_]onu[-_]1/(\d+)/(\d+):(\d+)\s*$", re.I)
_Z_VPORT = re.compile(r"^interface\s+vport-1/(\d+)/(\d+)\.(\d+):(\d+)\s*$", re.I)
_Z_MNG = re.compile(r"^pon-onu-mng\s+gpon[-_]onu[-_]1/(\d+)/(\d+):(\d+)\s*$", re.I)
_Z_ONULINE = re.compile(r"^\s*onu\s+(\d+)\s+type\s+(\S+)\s+(sn|loid|pw|password)\s+(\S+)", re.I)
_Z_SP = re.compile(r"^\s*service-port\s+(\d+)\s+(?:vport\s+(\d+)\s+)?user-vlan\s+(\d+)\b.*?\bvlan\s+(\d+)", re.I | re.M)
_Z_SVC = re.compile(r"^\s*service\s+(\d+)\s+gemport\s+(\d+)\s+vlan\s+(\d+)", re.I | re.M)
_Z_TCONT = re.compile(r"^\s*tcont\s+\d+\s+(?:name\s+\S+\s+)?profile\s+(\S+)", re.I | re.M)
_Z_NAME = re.compile(r"^\s*name\s+(.+?)\s*$", re.M)
_Z_DESC = re.compile(r"^\s*description\s+(.+?)\s*$", re.M)
_PPPOE = re.compile(r"pppoe\s+(?:username|user)\s+(\S+)", re.I)


def _zte_services(onu_cfg: str, mng_cfg: str, vport_cfg: str) -> List[dict]:
    ex = oltgen.parse_onu_existing(onu_cfg, mng_cfg, vport_cfg)
    sps = [(int(m.group(3)), int(m.group(4))) for m in _Z_SP.finditer(f"{onu_cfg}\n{vport_cfg}")]
    if not sps:                                  # sem service-port: usa os "service N gemport G vlan V" da gerência
        sps = [(s["vlan"], s["vlan"]) for s in ex["services"]]
    ports = ex["ports"]
    out, seen = [], set()
    for uv, vl in sps:
        if (uv, vl) in seen:
            continue
        seen.add((uv, vl))
        untag = sorted(e for e, pc in ports.items() if pc.get("untag") == uv)
        tagged = sorted(e for e, pc in ports.items() if uv in (pc.get("tagged") or []) or (pc.get("transparent") and not pc.get("tagged")))
        if untag:
            out.append({"vlan": vl, "uvlan": uv, "mode": "bridge", "ports": untag})
        elif tagged:
            out.append({"vlan": vl, "uvlan": uv, "mode": "transparent", "ports": tagged})
        else:
            out.append({"vlan": vl, "uvlan": uv, "mode": "router", "ports": []})
    return out


def parse_zte(cfg: str) -> List[dict]:
    blocks: Dict[tuple, Dict[str, List[str]]] = {}
    onus: Dict[tuple, dict] = {}
    ctx = None
    for raw in (cfg or "").splitlines():
        ln = raw.rstrip()
        s = ln.strip()
        if not s:
            continue
        if not ln[:1].isspace():                        # linha na coluna 0: começa (ou termina) um bloco
            ctx = None
            m = _Z_OLT.match(s)
            if m:
                ctx = ("olt", int(m.group(1)), int(m.group(2)))
                continue
            m = _Z_ONU.match(s)
            if m:
                ctx = ("onu", (int(m.group(1)), int(m.group(2)), int(m.group(3))))
                continue
            m = _Z_VPORT.match(s)
            if m:
                ctx = ("vport", (int(m.group(1)), int(m.group(2)), int(m.group(3))))
                continue
            m = _Z_MNG.match(s)
            if m:
                ctx = ("mng", (int(m.group(1)), int(m.group(2)), int(m.group(3))))
            continue
        if not ctx:
            continue
        if ctx[0] == "olt":
            m = _Z_ONULINE.match(ln)
            if m:
                k = (ctx[1], ctx[2], int(m.group(1)))
                auth = m.group(3).lower()
                onus[k] = {"type": m.group(2), "sn_raw": m.group(4), "auth": "sn" if auth == "sn" else auth}
        else:
            blocks.setdefault(ctx[1], {"onu": [], "vport": [], "mng": []})[ctx[0]].append(ln)
    out = []
    for k in sorted(onus):
        o = onus[k]
        b = blocks.get(k, {"onu": [], "vport": [], "mng": []})
        oc, vc, mc = "\n".join(b["onu"]), "\n".join(b["vport"]), "\n".join(b["mng"])
        nm, ds, tc = _Z_NAME.search(oc), _Z_DESC.search(oc), _Z_TCONT.search(oc)
        d = _onu(f"{k[0]}/{k[1]}", k[2], o["sn_raw"] if o["auth"] == "sn" else "", type=o["type"],
                 name=nm.group(1)[:64] if nm else "", description=ds.group(1)[:120] if ds else "",
                 tcont=tc.group(1) if tc else "", services=_zte_services(oc, mc, vc),
                 pppoe=list(dict.fromkeys(_PPPOE.findall(mc + "\n" + oc))))
        if o["auth"] != "sn":
            d["auth"], d["sn_raw"] = o["auth"], o["sn_raw"][:40]
        out.append(d)
    return out


# =====================================================================================
# Origem: Huawei — display current-configuration
# =====================================================================================
_H_SRV = re.compile(r"^\s*ont-srvprofile\s+gpon\s+profile-id\s+(\d+)", re.I)
_H_PORTVLAN = re.compile(r"^\s*port\s+vlan\s+eth\s+(\d+)(?:-(\d+))?\s+(?:translation\s+(\d+)|(\d+))", re.I)
_H_IF = re.compile(r"^\s*interface\s+gpon\s+(\d+)/(\d+)\s*$", re.I)
_H_NATIVE = re.compile(r"^\s*ont\s+port\s+native-vlan\s+(\d+)\s+(\d+)\s+eth\s+(\d+)\s+vlan\s+(\d+)", re.I)


def parse_huawei(cfg: str) -> List[dict]:
    inv = olthw.parse_inventory(cfg)
    srv_ports: Dict[int, Dict[int, List[int]]] = {}       # srv profile -> {vlan: [eth]}
    native: Dict[tuple, Dict[int, List[int]]] = {}         # (f,s,p,ont) -> {vlan: [eth]}
    cur_srv, cur_if = None, None
    for ln in (cfg or "").splitlines():
        m = _H_SRV.match(ln)
        if m:
            cur_srv = int(m.group(1))
            continue
        m = _H_IF.match(ln)
        if m:
            cur_if, cur_srv = (int(m.group(1)), int(m.group(2))), None
            continue
        if re.match(r"^\s*(quit|#)\s*$", ln):
            cur_srv = None
            continue
        if cur_srv is not None:
            m = _H_PORTVLAN.match(ln)
            if m:
                a, b = int(m.group(1)), int(m.group(2) or m.group(1))
                v = int(m.group(3) or m.group(4))
                srv_ports.setdefault(cur_srv, {}).setdefault(v, []).extend(range(a, b + 1))
        if cur_if:
            m = _H_NATIVE.match(ln)
            if m:
                k = (cur_if[0], cur_if[1], int(m.group(1)), int(m.group(2)))
                native.setdefault(k, {}).setdefault(int(m.group(4)), []).append(int(m.group(3)))
    out = []
    for k in sorted(inv):
        d = inv[k]
        f, s, p, o = k
        prof_ports = srv_ports.get(d.get("srv_profile"), {})
        nat = native.get(k, {})
        svcs, seen = [], set()
        for sv in d.get("services") or []:
            uv = sv.get("user_vlan") or sv["vlan"]
            if (uv, sv["vlan"]) in seen:
                continue
            seen.add((uv, sv["vlan"]))
            eth = sorted(set(nat.get(uv) or prof_ports.get(uv) or []))
            svcs.append({"vlan": sv["vlan"], "uvlan": uv, "mode": "bridge" if eth else "router", "ports": eth, "gemport": sv.get("gemport")})
        lp, sp = d.get("line_profile"), d.get("srv_profile")
        out.append(_onu(f"{f}/{s}/{p}", o, d.get("sn") or "", name=(d.get("description") or "")[:64],
                        description=d.get("description") or "", services=svcs, pppoe=d.get("pppoe") or [],
                        profile=f"line {lp} / srv {sp}" if lp is not None else "",
                        type=f"line {lp} / srv {sp}" if lp is not None else ""))
    return out


# =====================================================================================
# Origem: FiberHome (show authorization) e planilha
# =====================================================================================
def from_fiberhome(rows: List[dict]) -> List[dict]:
    return [_onu(f"{r['slot']}/{r['pon']}", r["onu"], r.get("sn") or "", type=r.get("type") or "") for r in rows]


CSV_HELP = "pon;onu;serial;tipo;nome;vlan;modo;portas  (vlan e portas aceitam vários: 100,200 / 1,2) — modo: bridge, transparent ou router"


def parse_csv(text: str, vendor: str = "zte") -> List[dict]:
    """Planilha (separador ; ou , ou tab). Colunas por nome: pon, onu, serial/sn, tipo/type, nome/name, descricao,
    vlan, modo/mode, portas/ports. Uma linha por ONU (VLANs separadas por espaço ou |)."""
    text = (text or "").strip()
    if not text:
        raise MigError("Cole a planilha (com a linha de cabeçalho)")
    first = text.splitlines()[0]
    sep = ";" if first.count(";") >= first.count(",") and ";" in first else "\t" if "\t" in first else ","
    rd = csv.DictReader(io.StringIO(text), delimiter=sep)
    alias = {"pon": "pon", "porta pon": "pon", "onu": "onu", "id": "onu", "onu id": "onu", "serial": "sn", "sn": "sn",
             "tipo": "type", "type": "type", "modelo": "type", "nome": "name", "name": "name", "cliente": "name",
             "descricao": "description", "descrição": "description", "description": "description", "vlan": "vlan",
             "vlans": "vlan", "modo": "mode", "mode": "mode", "portas": "ports", "ports": "ports", "porta": "ports"}
    out = []
    for n, row in enumerate(rd, 2):
        r = {alias.get((k or "").strip().lower()): (v or "").strip() for k, v in row.items() if alias.get((k or "").strip().lower())}
        if not r.get("pon") and not r.get("sn"):
            continue
        m = re.fullmatch(r"(?:\d+/)?(\d+/\d+)|(\d+/\d+/\d+)", r.get("pon", "").replace(" ", ""))
        if not m:
            raise MigError(f"Linha {n}: PON inválida ({r.get('pon')!r}) — use slot/porta (1/3) ou F/S/P (0/1/3)")
        pon = r["pon"].replace(" ", "")
        if vendor != "huawei" and pon.count("/") == 2:
            pon = pon.split("/", 1)[1]                 # ZTE 1/S/P e FiberHome: fica S/P
        elif vendor == "huawei" and pon.count("/") != 2:
            raise MigError(f"Linha {n}: PON do Huawei é F/S/P (ex.: 0/1/3)")
        try:
            onu = int(r.get("onu") or "")
        except ValueError:
            raise MigError(f"Linha {n}: ONU inválida ({r.get('onu')!r})")
        vlans = [int(x) for x in re.split(r"[\s,|/]+", r.get("vlan", "")) if x.isdigit()]
        mode = (r.get("mode") or "bridge").lower()
        mode = {"tag": "bridge", "bridge": "bridge", "transparente": "transparent", "transparent": "transparent",
                "router": "router", "roteada": "router", "pppoe": "router", "route": "router"}.get(mode, "bridge")
        ports = [int(x) for x in re.split(r"[\s,|/]+", r.get("ports", "")) if x.isdigit()] or ([1] if mode != "router" else [])
        svcs = [{"vlan": v, "uvlan": v, "mode": mode, "ports": ports if mode != "router" else []} for v in vlans]
        out.append(_onu(pon, onu, r.get("sn", ""), type=r.get("type", ""), name=r.get("name", "")[:64],
                        description=r.get("description", "")[:120], services=svcs))
        if not out[-1]["sn"]:
            out[-1]["auth"] = "outro"
    if not out:
        raise MigError("Nenhuma ONU na planilha (confira o cabeçalho: " + CSV_HELP + ")")
    return out


def summarize(onus: List[dict]) -> dict:
    pons: Dict[str, dict] = {}
    for o in onus:
        p = pons.setdefault(o["pon"], {"pon": o["pon"], "onus": 0, "types": {}, "vlans": {}, "no_sn": 0, "no_svc": 0})
        p["onus"] += 1
        p["types"][o["type"] or "?"] = p["types"].get(o["type"] or "?", 0) + 1
        for sv in o["services"]:
            p["vlans"][sv["vlan"]] = p["vlans"].get(sv["vlan"], 0) + 1
        p["no_sn"] += 0 if o["sn"] else 1
        p["no_svc"] += 0 if o["services"] else 1

    def key(p):
        return tuple(int(x) for x in p.split("/"))
    types, vlans, tconts = {}, {}, {}
    for o in onus:
        types[o["type"] or "?"] = types.get(o["type"] or "?", 0) + 1
        if o.get("tcont"):
            tconts[o["tcont"]] = tconts.get(o["tcont"], 0) + 1
        for sv in o["services"]:
            vlans[sv["vlan"]] = vlans.get(sv["vlan"], 0) + 1
    return {"onus": len(onus), "pons": [pons[k] for k in sorted(pons, key=key)],
            "types": sorted(([t, n] for t, n in types.items()), key=lambda x: -x[1]),
            "vlans": sorted(([v, n] for v, n in vlans.items()), key=lambda x: (-x[1], x[0])),
            "tconts": sorted(([t, n] for t, n in tconts.items()), key=lambda x: -x[1]),
            "no_sn": sum(1 for o in onus if not o["sn"]), "no_svc": sum(1 for o in onus if not o["services"]),
            "routed": sum(1 for o in onus if any(s["mode"] == "router" for s in o["services"]))}


# =====================================================================================
# Plano
# =====================================================================================
def _dest_pon(kind: str, v: str) -> Tuple[str, tuple]:
    s = str(v or "").strip().replace(" ", "")
    if kind.startswith("zte"):
        m = re.fullmatch(r"(?:1/)?(\d{1,2})/(\d{1,2})", s)
        if not m:
            raise MigError(f"PON de destino {v!r}: use slot/porta (ex.: 1/3) — o 1/ da frente é opcional")
        return f"{int(m.group(1))}/{int(m.group(2))}", (int(m.group(1)), int(m.group(2)))
    if kind == "huawei":
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{1,3})", s)
        if not m:
            raise MigError(f"PON de destino {v!r}: use F/S/P (ex.: 0/1/3)")
        return f"{int(m.group(1))}/{int(m.group(2))}/{int(m.group(3))}", (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", s)
    if not m:
        raise MigError(f"PON de destino {v!r}: use slot/pon (ex.: 3/1)")
    return f"{int(m.group(1))}/{int(m.group(2))}", (int(m.group(1)), int(m.group(2)))


def _id_range(kind: str) -> Tuple[int, int]:
    return (0, 127) if kind == "huawei" else (1, 128)


def _clean_label(v: str, fallback: str) -> str:
    """Nome que a ZTE aceita: letras, números e _ . : / - (até 64), sem espaço."""
    s = re.sub(r"\s+", "_", str(v or "").strip())
    s = re.sub(r"[^A-Za-z0-9_.:/-]", "", s)
    s = re.sub(r"^[^A-Za-z0-9]+", "", s)[:64]
    return s or fallback


def _clean_desc(v: str) -> str:
    return re.sub(r"[\"\\\r\n?]", "", str(v or "")).strip()[:64]


def build_plan(onus: List[dict], opts: dict, src_vendor: str, dest_used: Optional[Dict[str, List[int]]] = None,
               dest_sns: Optional[Dict[str, str]] = None) -> dict:
    """onus: lista normalizada da origem. opts: kind (zte-titan|zte-c300|huawei|fiberhome), pons {src: dst} só com as
    PONs escolhidas, id_mode keep|renumber, vlan_map, type_map, default_type, tcont_map, default_tcont, hw_profiles
    {tipo origem: {line, srv}}, default_line, default_srv, hw_gem {vlan: gemport}, default_service {vlan, mode, ports},
    name_from name|description|pppoe|sn. dest_used: IDs já ocupados no destino por PON; dest_sns: serial -> posição."""
    kind = opts.get("kind")
    if kind not in DEST_KINDS:
        raise MigError("Escolha o tipo da OLT de destino")
    sel = {str(k).strip(): str(v).strip() for k, v in (opts.get("pons") or {}).items() if str(v or "").strip()}
    if not sel:
        raise MigError("Marque pelo menos uma PON para migrar e informe a PON de destino")
    lo, hi = _id_range(kind)
    vlan_map = {int(k): int(v) for k, v in (opts.get("vlan_map") or {}).items() if str(v).strip().isdigit()}
    for v in vlan_map.values():
        if not 1 <= v <= 4094:
            raise MigError(f"VLAN de destino inválida: {v}")
    type_map = {str(k): str(v).strip() for k, v in (opts.get("type_map") or {}).items() if str(v or "").strip()}
    tcont_map = {str(k): str(v).strip() for k, v in (opts.get("tcont_map") or {}).items() if str(v or "").strip()}
    hw_prof = opts.get("hw_profiles") or {}
    hw_gem = {int(k): int(v) for k, v in (opts.get("hw_gem") or {}).items() if str(v).strip().isdigit()}
    dsvc = opts.get("default_service") or {}
    keep = (opts.get("id_mode") or "keep") == "keep"
    name_from = opts.get("name_from") or "name"
    used: Dict[str, set] = {k: set(v) for k, v in (dest_used or {}).items()}
    by_src: Dict[str, List[dict]] = {}
    for o in onus:
        if o["pon"] in sel:
            by_src.setdefault(o["pon"], []).append(o)
    missing = [p for p in sel if p not in by_src]
    groups: Dict[str, dict] = {}
    plan_onus = []
    errors_total = 0
    shift = 0
    if keep and src_vendor == "huawei" and kind != "huawei":
        shift = 1                                   # Huawei numera de 0; ZTE e FiberHome de 1
    elif keep and src_vendor != "huawei" and kind == "huawei":
        shift = -1
    order = sorted(by_src, key=lambda p: tuple(int(x) for x in p.split("/")))
    dst_of = {src: _dest_pon(kind, sel[src])[0] for src in order}
    # IDs por PON de destino em duas passadas: primeiro quem pode manter o ID; depois os que colidiram vão para os livres
    # (assim um ID ocupado no destino não empurra todas as ONUs seguintes)
    assign: Dict[tuple, tuple] = {}
    for dst in dict.fromkeys(dst_of.values()):
        taken = used.setdefault(dst, set())
        members = [(src, o) for src in order if dst_of[src] == dst for o in sorted(by_src[src], key=lambda x: x["onu"]) if o["sn"]]
        pending = []
        for src, o in members:
            want = o["onu"] + shift if keep else None
            if want is not None and lo <= want <= hi and want not in taken:
                taken.add(want)
                assign[(src, o["onu"])] = (want, False)
            else:
                pending.append((src, o))
        for src, o in pending:
            nid = next((i for i in range(lo, hi + 1) if i not in taken), None)
            if nid is not None:
                taken.add(nid)
            assign[(src, o["onu"])] = (nid, keep and nid is not None)
    for src in order:
        dst = dst_of[src]
        g = groups.setdefault(dst, {"dst": dst, "src": [], "onus": [], "errors": 0, "warnings": 0})
        g["src"].append(src)
        for o in sorted(by_src[src], key=lambda x: x["onu"]):
            warns, errs = [], []
            nid, moved = assign.get((src, o["onu"]), (None, False))
            if moved:
                warns.append(f"ID {o['onu'] + shift} já usado/fora da faixa no destino: vai como {nid}")
            if nid is None and o["sn"]:
                errs.append(f"PON {dst} cheia ({MAX_PER_PON} ONUs)")
            sn = o["sn"]
            if not sn:
                errs.append("sem serial (autorizada por " + ({"loid": "LOID", "pw": "senha", "password": "senha"}.get(o.get("auth"), "outro método")) + ") — autorize à mão")
            elif dest_sns and sn in dest_sns:
                errs.append(f"serial já autorizado no destino ({dest_sns[sn]})")
            st = o.get("type") or "?"
            dtype = type_map.get(st) or opts.get("default_type") or ""
            if kind != "huawei" and not dtype:
                same = (kind.startswith("zte") and src_vendor == "zte") or (kind == "fiberhome" and src_vendor == "fiberhome")
                if o.get("type") and (same or src_vendor == "csv"):
                    dtype = o["type"]                     # mesmo fabricante (ou planilha): mesmo tipo
                else:
                    errs.append(f"tipo de ONU no destino não definido (origem: {st})")
            svcs = []
            src_svcs = o["services"]
            if not src_svcs and dsvc.get("vlan"):
                src_svcs = [{"vlan": int(dsvc["vlan"]), "uvlan": int(dsvc.get("user_vlan") or dsvc["vlan"]),
                             "mode": dsvc.get("mode") or "bridge", "ports": [int(x) for x in (dsvc.get("ports") or [1])]}]
                warns.append("sem VLAN na origem: usou o serviço padrão")
            for sv in src_svcs:
                nv = vlan_map.get(sv["vlan"], sv["vlan"])
                nu = vlan_map.get(sv["uvlan"], sv["uvlan"]) if sv["uvlan"] == sv["vlan"] else sv["uvlan"]
                svcs.append({**sv, "vlan": nv, "uvlan": nu, "src_vlan": sv["vlan"]})
            if not svcs:
                errs.append("nenhuma VLAN (informe o serviço padrão)")
            if any(s["mode"] == "router" for s in svcs):
                warns.append("roteada: migra a VLAN; a WAN/PPPoE fica na ONU (confira se ela reconecta sozinha)")
            if len(svcs) > 8:
                errs.append("mais de 8 VLANs")
            raw_name = {"description": o.get("description"), "pppoe": (o.get("pppoe") or [""])[0], "sn": sn}.get(name_from) or o.get("name") or o.get("description") or (o.get("pppoe") or [""])[0]
            name = _clean_label(raw_name, sn or f"onu{o['onu']}")
            tcont = tcont_map.get(o.get("tcont") or "") or opts.get("default_tcont") or o.get("tcont") or ""
            if kind.startswith("zte") and not tcont:
                errs.append("perfil de banda (T-CONT) do destino não definido")
            line = srv = None
            if kind == "huawei":
                pr = hw_prof.get(st) or {}
                line = pr.get("line", opts.get("default_line"))
                srv = pr.get("srv", opts.get("default_srv"))
                if line in (None, "") or srv in (None, ""):
                    errs.append(f"line/srv profile do destino não definidos (origem: {st})")
                for i, s in enumerate(svcs, 1):
                    s["gemport"] = hw_gem.get(s["vlan"]) or s.get("gemport") or i
            item = {"src_pon": src, "src_onu": o["onu"], "dst_pon": dst, "dst_onu": nid, "sn": sn, "sn_raw": o.get("sn_raw"),
                    "src_type": o.get("type") or "", "type": dtype, "name": name, "description": _clean_desc(o.get("description")),
                    "services": svcs, "tcont": tcont, "line": line, "srv": srv, "pppoe": o.get("pppoe") or [],
                    "warnings": warns, "errors": errs, "ok": not errs}
            g["onus"].append(item)
            g["errors"] += bool(errs)
            g["warnings"] += bool(warns)
            errors_total += bool(errs)
            plan_onus.append(item)
    for g in groups.values():
        if len(g["onus"]) > MAX_PER_PON:
            g["errors"] += 1
    return {"kind": kind, "groups": [groups[k] for k in sorted(groups, key=lambda p: tuple(int(x) for x in p.split("/")))],
            "total": len(plan_onus), "ok": sum(1 for x in plan_onus if x["ok"]), "errors": errors_total,
            "missing": missing, "shift": shift}


# =====================================================================================
# Scripts
# =====================================================================================
def _zte_port_mode(sv: dict) -> str:
    return "tag" if sv["mode"] == "bridge" else "transparent"


def script_dest(plan: dict, dst: str, write: bool = True, comments: bool = True) -> dict:
    """Autorização no destino de todas as ONUs (sem erro) que vão para a PON `dst`."""
    kind = plan["kind"]
    g = next((x for x in plan["groups"] if x["dst"] == dst), None)
    if not g:
        raise MigError(f"PON {dst} não está no plano")
    items = [o for o in g["onus"] if o["ok"]]
    if not items:
        raise MigError(f"Nenhuma ONU sem pendência na PON {dst}")
    warns: List[str] = []
    if kind.startswith("zte"):
        plat = "c300" if kind == "zte-c300" else "titan"
        slot, port = (int(x) for x in dst.split("/"))
        out = [f"! Migração: {len(items)} ONU(s) de {', '.join(g['src'])} para {oltgen.pon_if(plat, slot, port)}"] if comments else []
        out += ["configure terminal", f"interface {oltgen.pon_if(plat, slot, port)}"]
        out += [f" onu {o['dst_onu']} type {o['type']} sn {o['sn']}" for o in items]
        out.append("exit")
        for o in items:
            onu_if = oltgen.onu_if_name(plat, slot, port, o["dst_onu"])
            svcs = o["services"]
            out += [f"interface {onu_if}", f" name {o['name']}"]
            if o["description"]:
                out.append(f" description {_clean_label(o['description'], '')}")
            out.append(f" tcont 1 profile {o['tcont']}")
            out += [f" gemport {i} tcont 1" for i in range(1, len(svcs) + 1)]
            if plat == "c300":
                out += [f" service-port {i} vport {i} user-vlan {sv['uvlan']} vlan {sv['vlan']}" for i, sv in enumerate(svcs, 1)]
                out.append("exit")
            else:
                out.append("exit")
                for i, sv in enumerate(svcs, 1):
                    out += [f"interface vport-1/{slot}/{port}.{o['dst_onu']}:{i}", f" service-port {i} user-vlan {sv['uvlan']} vlan {sv['vlan']}", "exit"]
            out.append(f"pon-onu-mng {onu_if}")
            out += [f" service {i} gemport {i} vlan {sv['uvlan']}" for i, sv in enumerate(svcs, 1)]
            bridged = [{"uvlan": sv["uvlan"], "mode": _zte_port_mode(sv), "ports": sv["ports"]} for sv in svcs if sv["mode"] != "router" and sv["ports"]]
            w: List[str] = []
            try:
                out += oltgen._port_lines(bridged, w) if bridged else []
            except GenError as e:
                warns.append(f"{onu_if}: {e} — portas não configuradas")
            out.append("exit")
            for x in w:
                if x not in warns:
                    warns.append(x)
        out.append("end")
        if write:
            out.append("write")
    elif kind == "huawei":
        f, s, p = (int(x) for x in dst.split("/"))
        out = olthw._head(comments, f"Migração: {len(items)} ONT(s) de {', '.join(g['src'])} para {dst}")
        out.append(f"interface gpon {f}/{s}")
        for o in items:
            sn = olthw.sn_hex(o["sn"])
            desc = _clean_desc(o["description"] or o["name"]) or o["sn"]
            out.append(f" ont add {p} {o['dst_onu']} sn-auth {sn} omci ont-lineprofile-id {o['line']} ont-srvprofile-id {o['srv']} desc \"{desc}\"")
            for sv in o["services"]:
                if sv["mode"] == "bridge":
                    out += [f" ont port native-vlan {p} {o['dst_onu']} eth {e} vlan {sv['uvlan']} priority 0" for e in sv["ports"] or [1]]
        out.append(" quit")
        for o in items:
            for sv in o["services"]:
                out.append(f"service-port vlan {sv['vlan']} gpon {f}/{s}/{p} ont {o['dst_onu']} gemport {sv['gemport']} multi-service "
                           f"user-vlan {sv['uvlan']} tag-transform translate")
        out.append("quit")
        if write:
            out.append("save")
        warns.append("Os line/srv profiles e as gem ports de cada VLAN precisam existir no destino (o BastiON não confere os perfis).")
    else:
        sl, pn = (int(x) for x in dst.split("/"))
        out = [f"! Migração: {len(items)} ONU(s) de {', '.join(g['src'])} para slot {sl} pon {pn}"] if comments else []
        for i, o in enumerate(items):
            svcs = [{"vlan": sv["vlan"], "mode": "veip" if sv["mode"] == "router" else "bridge", "ports": sv["ports"] or [1]} for sv in o["services"]]
            r = oltfh.onu_script({"slot": sl, "pon": pn, "onu_id": o["dst_onu"], "sn": o["sn"], "type": o["type"], "name": o["name"],
                                  "services": svcs, "dir": plan.get("fh_dir") or "onu", "comments": False, "write": False})
            lines = r["script"].splitlines()
            out += lines if i == 0 else [x for x in lines if x.strip() != "enable"]
        if write:
            out.append("save")
        warns.append(oltfh.WARN)
    return {"script": "\n".join(out) + "\n", "onus": len(items), "warnings": warns,
            "filename": f"migracao-destino-{dst.replace('/', '-')}.txt"}


def _chunks(xs, n=64):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def script_remove(vendor: str, platform: str, items: List[dict], write: bool = True, comments: bool = True, title: str = "") -> str:
    """Remove ONUs (pon, onu, sn, type). vendor: zte (platform titan|c300) | huawei | fiberhome."""
    parts = []
    if comments and title:
        parts.append(("# " if vendor == "huawei" else "! ") + title)
    if vendor == "zte":
        for ch in _chunks(items):
            its = [{"slot": int(x["pon"].split("/")[-2]), "pon": int(x["pon"].split("/")[-1]), "onu": x["onu"]} for x in ch]
            parts.append(oltgen.onu_remove_script({"platform": platform, "items": its, "write": False, "comments": False})["script"].rstrip())
        if write:
            parts.append("write")
    elif vendor == "huawei":
        keys = [tuple(int(v) for v in x["pon"].split("/")) + (x["onu"],) for x in items]
        parts.append(olthw.bulk_remove_script(keys, write=write, comments=False).rstrip())
    else:
        for ch in _chunks(items):
            its = [{"slot": int(x["pon"].split("/")[0]), "pon": int(x["pon"].split("/")[1]), "onu": x["onu"], "sn": x["sn"], "type": x.get("type") or ""} for x in ch]
            parts.append(oltfh.onu_remove_script({"items": its, "write": False, "comments": False, "dir": platform or "onu"})["script"].rstrip())
        if write:
            parts.append("save")
    return "\n".join(parts) + "\n"


def script_src_remove(plan: dict, dst: str, src_vendor: str, src_platform: str, write: bool = True) -> dict:
    g = next((x for x in plan["groups"] if x["dst"] == dst), None)
    if not g:
        raise MigError(f"PON {dst} não está no plano")
    items = [{"pon": o["src_pon"], "onu": o["src_onu"], "sn": o["sn"], "type": o["src_type"]} for o in g["onus"] if o["ok"]]
    if not items:
        raise MigError("Nada a remover")
    if src_vendor == "huawei":
        bad = [i for i in items if i["pon"].count("/") != 2]
        if bad:
            raise MigError("PON da origem Huawei precisa ser F/S/P")
    s = script_remove(src_vendor, src_platform, items, write, True,
                      f"Migração: remover da OLT de origem as {len(items)} ONU(s) que foram para {dst} (rode depois de mudar a fibra)")
    return {"script": s, "onus": len(items), "filename": f"migracao-remover-origem-{'-'.join(p.replace('/', '-') for p in g['src'])}.txt",
            "warnings": ["Rode só depois de conferir que as ONUs subiram no destino: a ONU some da OLT de origem na hora."]}


def script_rollback(plan: dict, dst: str, write: bool = True) -> dict:
    kind = plan["kind"]
    g = next((x for x in plan["groups"] if x["dst"] == dst), None)
    if not g:
        raise MigError(f"PON {dst} não está no plano")
    items = [{"pon": dst, "onu": o["dst_onu"], "sn": o["sn"], "type": o["type"]} for o in g["onus"] if o["ok"]]
    vendor = "zte" if kind.startswith("zte") else kind
    plat = "c300" if kind == "zte-c300" else "titan" if vendor == "zte" else (plan.get("fh_dir") or "onu")
    s = script_remove(vendor, plat, items, write, True, f"Desfazer a migração da PON {dst} (remove do destino as {len(items)} ONU(s) migradas)")
    return {"script": s, "onus": len(items), "filename": f"migracao-desfazer-{dst.replace('/', '-')}.txt", "warnings": []}


def verify(plan: dict, dst: str, states: Dict[int, dict]) -> dict:
    """states: {onu_id: {off: bool, phase}} lido do destino. Diz quais ONUs migradas subiram."""
    g = next((x for x in plan["groups"] if x["dst"] == dst), None)
    if not g:
        raise MigError(f"PON {dst} não está no plano")
    rows, on, off, miss = [], 0, 0, 0
    for o in g["onus"]:
        if not o["ok"]:
            continue
        st = states.get(o["dst_onu"])
        s = "ausente" if st is None else ("offline" if st.get("off") else "online")
        on += s == "online"
        off += s == "offline"
        miss += s == "ausente"
        rows.append({"dst_onu": o["dst_onu"], "sn": o["sn"], "name": o["name"], "state": s, "phase": (st or {}).get("phase") or ""})
    return {"dst": dst, "online": on, "offline": off, "missing": miss, "rows": rows}


__all__ = ["parse_zte", "parse_huawei", "from_fiberhome", "parse_csv", "summarize", "build_plan", "script_dest",
           "script_src_remove", "script_rollback", "verify", "norm_sn", "MigError", "DEST_KINDS", "MODES", "CSV_HELP"]
