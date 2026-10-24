"""OLTs Huawei SmartAX (MA5800 / EA5800 / MA5608T / MA5683T / MA5600T) — GPON.

Mesmas funções do lado ZTE (oltgen.py): script de ativação (VLANs, uplink, gerência, perfis), autorizar ONU
(autofind + ont add + service-port), desautorizar, limpeza de ONUs off, busca por serial e leitura do estado/sinal
para o módulo GPON.

Convenções do Huawei: porta PON = F/S/P (frame/slot/port, ex.: 0/1/0), ONT ID de 0 a 127, serial em 16 hexa
(48575443A1B2C3D4) ou no formato do fabricante (HWTC-A1B2C3D4). Os comandos começam em `enable` + `undo smart`
(desliga as perguntas interativas) + `config`; `save` grava.
"""
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from oltgen import GenError, _int, _ip, _ip_mask, _speed_kbps, _vlan

MODELS = {"MA5800-X2": 2, "MA5800-X7": 7, "MA5800-X15": 15, "MA5800-X17": 17, "EA5800-X2": 2, "EA5800-X7": 7,
          "EA5800-X15": 15, "MA5608T": 2, "MA5683T": 6, "MA5680T": 16, "MA5600T": 16}
PON_BOARD = re.compile(r"(GP[A-Z]{2}|XG[A-Z]{2}|XE[A-Z]{2}|GPLF|GPUF|GPSF|CGHF|XSHF)", re.I)
PON_PORTS = {"GPBD": 8, "GPBH": 8, "GPBC": 8, "XGBD": 8, "XGHD": 8}           # o resto: 16
OFF_CAUSES = {"los", "losi", "lofi", "dying-gasp", "dyinggasp", "lob", "loki", "sfi", "loami"}
FSP_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{1,3})$")


# ---------------------------------------------------------------- serial
def sn_hex(v: str) -> str:
    """'HWTC-A1B2C3D4', 'HWTCA1B2C3D4' ou '48575443A1B2C3D4' -> '48575443A1B2C3D4'."""
    s = re.sub(r"[\s:-]", "", str(v or "")).upper()
    if re.fullmatch(r"[0-9A-F]{16}", s):
        return s
    if re.fullmatch(r"[A-Z]{4}[0-9A-F]{8}", s) and not re.fullmatch(r"[0-9A-F]{12}", s):
        return s[:4].encode().hex().upper() + s[4:]
    raise GenError("Serial inválido: 16 hexadecimais (48575443A1B2C3D4) ou fabricante + 8 hexa (HWTC-A1B2C3D4)")


def sn_vendor(hex16: str) -> str:
    try:
        v = bytes.fromhex(hex16[:8]).decode("ascii")
        if re.fullmatch(r"[A-Z0-9]{4}", v):
            return f"{v}-{hex16[8:]}"
    except (ValueError, UnicodeDecodeError):
        pass
    return hex16


def looks_like_sn(v: str) -> bool:
    try:
        sn_hex(v)
        return True
    except GenError:
        return False


def fsp(v: str) -> Tuple[int, int, int]:
    m = FSP_RE.match(str(v or "").replace(" ", ""))
    if not m:
        raise GenError("PON no formato F/S/P (ex.: 0/1/0)")
    f, s, p = (int(x) for x in m.groups())
    if not (0 <= f <= 7 and 0 <= s <= 31 and 0 <= p <= 63):
        raise GenError("PON fora da faixa")
    return f, s, p


# ---------------------------------------------------------------- leitura
def parse_board(text: str) -> List[dict]:
    """`display board 0` -> placas PON [{slot, name, ports}]."""
    out = []
    for m in re.finditer(r"^\s*(\d{1,2})\s+(H\d{3}([A-Z0-9]{4})|[A-Z0-9]{4,})\s+(\S+)", text or "", re.M):
        name = m.group(2)
        code = m.group(3) or name[-4:]
        if PON_BOARD.search(code) and not re.search(r"(?i)fail|absent", m.group(4)):
            out.append({"slot": int(m.group(1)), "name": name, "ports": PON_PORTS.get(code.upper(), 16)})
    return out


def parse_autofind(text: str) -> List[dict]:
    """`display ont autofind all` -> ONUs pedindo autorização."""
    out = []
    for blk in re.split(r"(?m)^\s*Number\s*:", text or "")[1:]:
        f = re.search(r"F/S/P\s*:\s*(\d+)/\s*(\d+)/\s*(\d+)", blk)
        s = re.search(r"Ont SN\s*:\s*([0-9A-Fa-f]{16})", blk)
        if not f or not s:
            continue
        g = lambda k: (re.search(rf"{k}\s*:\s*(.*?)\s*$", blk, re.M) or [None, ""])[1]     # noqa: E731
        hx = s.group(1).upper()
        out.append({"pon": f"{f.group(1)}/{f.group(2)}/{f.group(3)}", "frame": int(f.group(1)), "slot": int(f.group(2)), "port": int(f.group(3)),
                    "sn": hx, "sn_vendor": sn_vendor(hx), "model": g(r"Ont EquipmentID"), "version": g(r"Ont SoftwareVersion"),
                    "vendor": g(r"VendorID"), "found_at": g(r"Ont autofind time")})
    return out


_INFO_ROW = re.compile(r"^\s*(\d+)/\s*(\d+)/\s*(\d+)\s+(\d+)\s+([0-9A-Fa-f]{16})\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", re.M)
_DESC_ROW = re.compile(r"^\s*(\d+)/\s*(\d+)/\s*(\d+)\s+(\d+)\s+(.+?)\s*$", re.M)


def parse_ont_info(text: str) -> List[dict]:
    """`display ont info 0 all` (ou de uma PON): estado de cada ONT + descrição."""
    t = text or ""
    rows = {}
    for m in _INFO_ROW.finditer(t):
        k = tuple(int(x) for x in m.groups()[:4])
        rows[k] = {"frame": k[0], "slot": k[1], "port": k[2], "ont": k[3], "sn": m.group(5).upper(), "control": m.group(6).lower(),
                   "run": m.group(7).lower(), "config": m.group(8).lower(), "match": m.group(9).lower()}
    part = t.split("Description", 1)
    if len(part) == 2:
        for m in _DESC_ROW.finditer(part[1]):
            k = tuple(int(x) for x in m.groups()[:4])
            if k in rows and not re.fullmatch(r"-+", m.group(5)):
                rows[k]["description"] = m.group(5)[:120]
    for r in rows.values():
        r["off"] = r["run"] != "online"
    return list(rows.values())


def _dt(s: str) -> Optional[datetime]:
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})", s or "")
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_summary(text: str) -> List[dict]:
    """`display ont info summary F/S/P`: por ONT estado, última queda/causa, serial, modelo, sinal Rx/Tx e descrição."""
    t = text or ""
    fspm = re.search(r"In port\s+(\d+)/\s*(\d+)/\s*(\d+)", t)
    base = tuple(int(x) for x in fspm.groups()) if fspm else (None, None, None)
    st, info = {}, {}
    for m in re.finditer(r"^\s*(\d{1,3})\s+(online|offline)\s+(\S+(?:\s+\d{2}:\d{2}:\d{2})?)\s+(\S+(?:\s+\d{2}:\d{2}:\d{2})?)\s+(\S+)\s*$", t, re.M | re.I):
        st[int(m.group(1))] = {"run": m.group(2).lower(), "last_up": _dt(m.group(3)), "last_down": _dt(m.group(4)),
                               "cause": "" if m.group(5) == "-" else m.group(5)}
    for m in re.finditer(r"^\s*(\d{1,3})\s+([0-9A-Fa-f]{16})\s+(\S+)\s+(\S+)\s+(-?[\d.]+|-)\s*/\s*(-?[\d.]+|-)\s*(.*?)\s*$", t, re.M):
        rx = None if m.group(5) == "-" else float(m.group(5))
        info[int(m.group(1))] = {"sn": m.group(2).upper(), "type": m.group(3), "distance": None if m.group(4) == "-" else m.group(4),
                                 "rx": rx if rx is not None and -50 < rx < 10 else None, "description": m.group(7)[:120]}
    out = []
    for oid in sorted(set(st) | set(info)):
        r = {"frame": base[0], "slot": base[1], "port": base[2], "ont": oid, **st.get(oid, {}), **info.get(oid, {})}
        r["off"] = r.get("run") != "online"
        out.append(r)
    return out


def parse_detail(text: str) -> dict:
    """`display ont info F S P ID`: estado, última queda, serial e descrição."""
    t = text or ""
    g = lambda k: (re.search(rf"^\s*{k}\s*:\s*(.*?)\s*$", t, re.M | re.I) or [None, ""])[1]     # noqa: E731
    sn = re.search(r"SN\s*:\s*([0-9A-Fa-f]{16})", t)
    last = _dt(g(r"Last down time"))
    return {"run": g(r"Run state").lower(), "sn": sn.group(1).upper() if sn else "", "description": g(r"Description"),
            "cause": g(r"Last down cause"), "last_down": last.strftime("%Y-%m-%d %H:%M:%S") if last else None,
            "line_profile": g(r"Line profile ID"), "srv_profile": g(r"Service profile ID")}


def parse_optical(text: str) -> Dict[int, float]:
    """`display ont optical-info P all` (dentro de interface gpon F/S): Rx de cada ONT."""
    out = {}
    for m in re.finditer(r"^\s*(\d{1,3})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)", text or "", re.M):
        v = float(m.group(2))
        if -50 < v < 10:
            out[int(m.group(1))] = v
    return out


def parse_by_sn(text: str) -> Optional[dict]:
    t = text or ""
    f = re.search(r"F/S/P\s*:\s*(\d+)/\s*(\d+)/\s*(\d+)", t)
    o = re.search(r"ONT-ID\s*:\s*(\d+)", t)
    if not f or not o:
        return None
    d = parse_detail(t)
    return {"frame": int(f.group(1)), "slot": int(f.group(2)), "port": int(f.group(3)), "ont": int(o.group(1)),
            "pon": f"{f.group(1)}/{f.group(2)}/{f.group(3)}", **d}


def hw_mac(v: str) -> str:
    """Qualquer formato de MAC -> xxxx-xxxx-xxxx (como o Huawei mostra)."""
    h = re.sub(r"[^0-9a-fA-F]", "", str(v or "")).lower()
    if len(h) != 12:
        raise GenError("MAC inválido: use 12 dígitos hexadecimais (ex.: 7c:8b:ca:11:22:33)")
    return f"{h[0:4]}-{h[4:8]}-{h[8:12]}"


MAC_COMMANDS = ["display mac-address all | include {mac}", "display location {mac}"]


def parse_mac_lookup(text: str, mac: str) -> Optional[dict]:
    """Linha do `display mac-address` com o MAC: '... gpon 0011-2233-4455 dynamic 0 /1 /0  3  1  100' (VPI = ONT, VCI = gem).
    Ou o `display location`: F/S/P + ONT-ID."""
    t = text or ""
    for ln in t.splitlines():
        if mac.lower() not in ln.lower():
            continue
        m = re.search(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s+(\d{1,3})\s+(\d+)\s+(\d{1,4})\s*$", ln)
        if m:
            return {"frame": int(m.group(1)), "slot": int(m.group(2)), "port": int(m.group(3)), "ont": int(m.group(4)),
                    "pon": f"{m.group(1)}/{m.group(2)}/{m.group(3)}", "vlan": int(m.group(6))}
    m = re.search(r"F/S/P\s*:\s*(\d+)/\s*(\d+)/\s*(\d+).*?ONT[- ]?ID\s*:\s*(\d+)", t, re.S | re.I)
    if m:
        return {"frame": int(m.group(1)), "slot": int(m.group(2)), "port": int(m.group(3)), "ont": int(m.group(4)),
                "pon": f"{m.group(1)}/{m.group(2)}/{m.group(3)}"}
    return None


def parse_inventory(cfg: str) -> Dict[Tuple[int, int, int, int], dict]:
    """`display current-configuration` -> {(f, s, p, ont): {sn, description, line_profile, srv_profile, pppoe, services}}."""
    inv: Dict[tuple, dict] = {}
    cur = None
    for ln in (cfg or "").splitlines():
        m = re.match(r"^\s*interface\s+gpon\s+(\d+)/(\d+)\s*$", ln)
        if m:
            cur = (int(m.group(1)), int(m.group(2)))
            continue
        if re.match(r"^\s*(quit|#)\s*$", ln) or (ln[:1].strip() and not ln.startswith("service-port")):
            cur = None
        m = re.match(r"^\s*ont\s+add\s+(\d+)\s+(\d+)\s+(?:sn-auth|password-auth|loid-auth)\s+\"?([0-9A-Fa-f]{16}|[^\s\"]+)\"?(.*)$", ln)
        if m and cur:
            k = (cur[0], cur[1], int(m.group(1)), int(m.group(2)))
            rest = m.group(4)
            d = inv.setdefault(k, {"pppoe": [], "services": []})
            d["sn"] = m.group(3).upper()
            lp = re.search(r"ont-lineprofile-id\s+(\d+)", rest)
            sp = re.search(r"ont-srvprofile-id\s+(\d+)", rest)
            ds = re.search(r"desc\s+\"([^\"]*)\"|desc\s+(\S+)", rest)
            d["line_profile"] = int(lp.group(1)) if lp else None
            d["srv_profile"] = int(sp.group(1)) if sp else None
            if ds:
                d["description"] = (ds.group(1) if ds.group(1) is not None else ds.group(2))[:120]
            continue
        m = re.match(r"^\s*ont\s+ipconfig\s+(\d+)\s+(\d+)\b.*?\bpppoe\b.*?username\s+\"?([^\s\"]+)\"?", ln)
        if m and cur:
            d = inv.setdefault((cur[0], cur[1], int(m.group(1)), int(m.group(2))), {"pppoe": [], "services": []})
            if m.group(3) not in d["pppoe"]:
                d["pppoe"].append(m.group(3)[:80])
            continue
        m = re.match(r"^\s*service-port\s+(\d+)?\s*vlan\s+(\d+)\s+gpon\s+(\d+)/(\d+)/(\d+)\s+ont\s+(\d+)\s+gemport\s+(\d+)(?:.*?user-vlan\s+(\d+))?", ln)
        if m:
            k = (int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6)))
            d = inv.setdefault(k, {"pppoe": [], "services": []})
            d["services"].append({"index": int(m.group(1)) if m.group(1) else None, "vlan": int(m.group(2)), "gemport": int(m.group(7)),
                                  "user_vlan": int(m.group(8)) if m.group(8) else None})
    return inv


def used_ont_ids(info_rows: List[dict], f: int, s: int, p: int) -> List[int]:
    return sorted({r["ont"] for r in info_rows if (r["frame"], r["slot"], r["port"]) == (f, s, p)})


def free_ont_id(used: List[int]) -> Optional[int]:
    return next((i for i in range(0, 128) if i not in set(used)), None)


# ---------------------------------------------------------------- scripts
def _desc(v, what="Descrição", need=False) -> str:
    s = str(v or "").strip()
    if need and not s:
        raise GenError(f"{what}: obrigatório")
    if s and not re.fullmatch(r"[^\"\\\r\n?]{1,64}", s):
        raise GenError(f"{what}: até 64 caracteres, sem aspas nem ?")
    return s


def _head(comments: bool, text: str) -> List[str]:
    return ([f"# {text}"] if comments else []) + ["enable", "undo smart", "config"]


def onu_script(p: dict) -> dict:
    """Autoriza uma ONT: `ont add` com os perfis + porta/WAN + service-port por VLAN."""
    f, s, pt = fsp(p.get("pon"))
    ont = _int(p.get("onu_id"), "ID da ONU")
    if not 0 <= ont <= 127:
        raise GenError("ONT ID: 0 a 127")
    sn = sn_hex(p.get("sn"))
    lp = _int(p.get("line_profile"), "Line profile (ont-lineprofile-id)")
    sp = _int(p.get("srv_profile"), "Service profile (ont-srvprofile-id)")
    if not (0 <= lp <= 8192 and 0 <= sp <= 8192):
        raise GenError("ID de perfil fora da faixa")
    desc = _desc(p.get("name") or p.get("description"), "Nome/descrição do cliente", need=True)
    svcs = []
    for n, sv in enumerate(p.get("services") or [], 1):
        vlan = _vlan(sv.get("vlan"), f"Serviço {n}: VLAN")
        uv = _vlan(sv.get("user_vlan") or vlan, f"Serviço {n}: VLAN do usuário")
        gem = _int(sv.get("gemport") or n, f"Serviço {n}: gemport")
        mode = sv.get("mode") if sv.get("mode") in ("bridge", "pppoe", "none") else "bridge"
        ports = [int(x) for x in (sv.get("ports") or []) if str(x).isdigit() and 1 <= int(x) <= 8]
        svcs.append({"vlan": vlan, "uvlan": uv, "gem": gem, "mode": mode, "ports": ports,
                     "user": str(sv.get("pppoe_user") or "").strip(), "password": str(sv.get("pppoe_password") or "")})
    if not svcs:
        raise GenError("Informe ao menos um serviço (VLAN)")
    warns = []
    comments = bool(p.get("comments", True))
    out = _head(comments, f"Autorizar ONT {sn_vendor(sn)} em {f}/{s}/{pt} ONT {ont} (line {lp} / srv {sp})")
    out += [f"interface gpon {f}/{s}",
            f" ont add {pt} {ont} sn-auth {sn} omci ont-lineprofile-id {lp} ont-srvprofile-id {sp} desc \"{desc}\""]
    for sv in svcs:
        if sv["mode"] == "bridge":
            for e in sv["ports"] or [1]:
                out.append(f" ont port native-vlan {pt} {ont} eth {e} vlan {sv['uvlan']} priority 0")
        elif sv["mode"] == "pppoe":
            if not re.fullmatch(r"[^\s\"]{1,64}", sv["user"]) or not re.fullmatch(r"[^\s\"]{1,64}", sv["password"]):
                raise GenError("PPPoE: informe usuário e senha (sem espaço nem aspas)")
            out += [f" ont ipconfig {pt} {ont} pppoe vlan {sv['uvlan']} priority 0 user-account username {sv['user']} password {sv['password']}",
                    f" ont internet-config {pt} {ont} ip-index 0"]
            warns.append("PPPoE pela OLT (OMCI): o perfil de serviço precisa ser de ONU roteada; confira o modelo da ONU.")
    out.append(" quit")
    for sv in svcs:
        out.append(f"service-port vlan {sv['vlan']} gpon {f}/{s}/{pt} ont {ont} gemport {sv['gem']} multi-service user-vlan {sv['uvlan']}"
                   + " tag-transform translate")
    out.append("quit")
    if p.get("write"):
        out.append("save")
    warns.append("A gem port de cada VLAN precisa existir no line profile (gem mapping); o BastiON não confere os perfis.")
    return {"script": "\n".join(out) + "\n", "lines": len(out), "warnings": warns,
            "filename": f"ont-{desc}-{f}-{s}-{pt}-{ont}.txt".replace(" ", "_"), "interface": f"{f}/{s}/{pt} ONT {ont}"}


def onu_remove_script(p: dict) -> dict:
    items = p.get("items") or []
    if not items:
        raise GenError("Informe ao menos uma ONU (F/S/P e ONT ID)")
    if len(items) > 64:
        raise GenError("Até 64 ONUs por vez")
    keys = []
    for n, it in enumerate(items, 1):
        f, s, pt = fsp(it.get("pon") or f"{it.get('frame', 0)}/{it.get('slot')}/{it.get('port', it.get('pon_port'))}")
        o = _int(it.get("onu"), f"Linha {n}: ONT ID")
        if not 0 <= o <= 127:
            raise GenError(f"Linha {n}: ONT ID de 0 a 127")
        if (f, s, pt, o) in keys:
            raise GenError(f"ONT {f}/{s}/{pt} {o} repetida")
        keys.append((f, s, pt, o))
    out = bulk_remove_script(keys, write=bool(p.get("write")), comments=bool(p.get("comments", True)))
    return {"script": out, "lines": out.count("\n"), "filename": f"desautorizar-{len(keys)}-onts.txt",
            "warnings": ["Os service-ports da ONT são apagados antes; o cliente fica sem serviço até ser autorizado de novo."]}


def bulk_remove_script(keys: List[Tuple[int, int, int, int]], write: bool = True, comments: bool = False) -> str:
    out = _head(comments, f"Desautorizar {len(keys)} ONT(s)")
    for f, s, pt, o in sorted(set(keys)):
        out.append(f"undo service-port port {f}/{s}/{pt} ont {o}")
    by: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for f, s, pt, o in sorted(set(keys)):
        by.setdefault((f, s), []).append((pt, o))
    for (f, s), lst in sorted(by.items()):
        out.append(f"interface gpon {f}/{s}")
        out += [f" ont delete {pt} {o}" for pt, o in lst]
        out.append(" quit")
    out.append("quit")
    if write:
        out.append("save")
    return "\n".join(out) + "\n"


def generate(p: dict) -> dict:
    """Ativação básica da OLT Huawei: hostname, VLANs de clientes no uplink, gerência, perfis DBA/line/service, NTP,
    SNMP e autosave. Placas e portas PON já vêm habilitadas no Huawei."""
    host = str(p.get("hostname") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", host):
        raise GenError("Hostname inválido (letras, números, - _ .)")
    comments = bool(p.get("comments", True))
    ups = []
    for u in p.get("uplinks") or []:
        f, s, pt = fsp(u)
        ups.append((f, s, pt))
    out, warns = [], []

    def c(t):
        if comments:
            out.append(f"# {t}")
    out += ["enable", "undo smart", "config", f"sysname {host}"]
    v = p.get("vlans") or {}
    if v.get("enabled"):
        start, count = _vlan(v.get("start"), "VLAN inicial"), _int(v.get("count"), "Quantidade de VLANs")
        if not 1 <= count <= 1000 or start + count - 1 > 4094:
            raise GenError("Faixa de VLANs inválida")
        end = start + count - 1
        c(f"VLANs de clientes {start}–{end} no uplink")
        out.append(f"vlan {start} to {end} smart" if count > 1 else f"vlan {start} smart")
        dsc = re.sub(r"[^A-Za-z0-9_.-]", "", str(v.get("description") or ""))[:30]
        if dsc and count <= 32:
            out += [f"vlan desc {x} description {dsc}" for x in range(start, end + 1)]
        for f, s, pt in ups:
            out.append(f"port vlan {start} to {end} {f}/{s} {pt}" if count > 1 else f"port vlan {start} {f}/{s} {pt}")
        if not ups:
            warns.append("Sem uplink informado: as VLANs foram criadas mas não passam por nenhuma porta.")
    m = p.get("mgmt") or {}
    if m.get("in_enabled"):
        iv = _vlan(m.get("in_vlan"), "VLAN de gerência")
        ip, mask = _ip_mask(m.get("in_ip"), m.get("in_mask"), "Gerência inband")
        c("Gerência inband")
        out.append(f"vlan {iv} smart")
        for f, s, pt in ups:
            out.append(f"port vlan {iv} {f}/{s} {pt}")
        out += [f"interface vlanif{iv}", f" ip address {ip} {mask}", " quit"]
        if (m.get("in_gw") or "").strip():
            out.append(f"ip route-static 0.0.0.0 0.0.0.0 {_ip(m['in_gw'], 'Gateway inband')}")
    if m.get("out_enabled"):
        ip, mask = _ip_mask(m.get("out_ip"), m.get("out_mask"), "Gerência outband")
        c("Gerência outband (porta ETH da controladora)")
        out += ["interface meth0", f" ip address {ip} {mask}", " quit"]
    speeds = []
    for sp_ in p.get("speeds") or []:
        if isinstance(sp_, str):
            if not sp_.strip():
                continue
            name, kbps = _speed_kbps(sp_)
        else:
            name = re.sub(r"[^A-Za-z0-9_.-]", "", str(sp_.get("name") or ""))[:20]
            kbps = _int(sp_.get("mbps"), "Velocidade (Mbps)") * 1024
        if name and 64 <= kbps <= 10 * 1024000 and name not in [x[0] for x in speeds]:
            speeds.append((name, kbps))
    for i, (name, mb) in enumerate(speeds, 10):
        if i == 10:
            c("Perfis de banda (DBA) e line profiles — um por velocidade, mapeando a 1ª VLAN de clientes")
        out.append(f"dba-profile add profile-id {i} profile-name \"{name}\" type4 max {mb}")
    if speeds and v.get("enabled"):
        for i, (name, mb) in enumerate(speeds, 10):
            out += [f"ont-lineprofile gpon profile-id {i} profile-name \"PLANO-{name}\"", f" tcont 1 dba-profile-id {i}",
                    " gem add 1 eth tcont 1", f" gem mapping 1 0 vlan {int(v.get('start'))}", " commit", " quit"]
    if p.get("srv_profile", True):
        c("Service profile genérico (ONU com portas detectadas automaticamente)")
        out += ["ont-srvprofile gpon profile-id 1 profile-name \"GENERICO\"", " ont-port eth adaptive pots adaptive",
                " commit", " quit"]
    for n in [x for x in (p.get("ntp") or []) if str(x).strip()][:2]:
        out.append(f"ntp-service unicast-server {_ip(n, 'NTP')}")
    snmp = p.get("snmp") or {}
    if snmp.get("enabled"):
        comm = str(snmp.get("community") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.@#-]{4,32}", comm):
            raise GenError("Community SNMP: 4 a 32 caracteres, sem espaço")
        out.append(f"snmp-agent community read {comm}")
        if (snmp.get("trap_host") or "").strip():
            th = _ip(snmp["trap_host"], "Destino dos traps")
            out += [f"snmp-agent target-host trap-hostname BASTION address {th} udp-port 162 trap-paramsname BASTION",
                    f"snmp-agent target-host trap-paramsname BASTION v2c securityname {comm}"]
    a = p.get("autosave") or {}
    if a.get("enabled"):
        t = str(a.get("time") or "03:00")
        if not re.fullmatch(r"\d{2}:\d{2}", t):
            raise GenError("Horário do autosave: HH:MM")
        out += ["autosave time on", f"autosave time {t}:00"]
    out.append("quit")
    if p.get("write", True):
        out.append("save")
    warns.append("Comandos do MA5800/MA5608T; confira na versão do seu equipamento antes de aplicar (alguns pedem confirmação).")
    return {"script": "\n".join(out) + "\n", "lines": len(out), "warnings": warns, "filename": f"olt-huawei-{host}.txt"}
