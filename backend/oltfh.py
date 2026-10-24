"""OLTs FiberHome AN5516-01/02/04/06 (CLI clássica: "User>" -> enable -> "Admin#", diretórios cd onu / cd lan / cd service).

Scripts: autorizar ONU (whitelist por serial + VLAN na porta LAN ou WAN PPPoE/veip na ONU roteada), desautorizar,
ativação básica (VLAN de serviço no uplink, VLAN de gerência, rota, SNMP, autosave). Leituras tolerantes ao formato:
showcard (placas PON), show discovery (ONUs não autorizadas), show authorization (autorizadas e estado), show
optic_module (sinal).

A sintaxe muda entre versões de firmware (RP0700…RP1200; a AN6000 usa outra CLI). Tudo aqui sai marcado para conferir.
Endereço da ONU: slot S, pon P, onu O (1–128). Uplink: slot:porta (ex.: 19:1). Serial: FHTT + 8 hexa.
"""
import re
from typing import Dict, List, Optional

from oltgen import GenError, _int, _ip, _vlan

DIRS = ("onu", "gpononu")                    # RPs novos: cd onu; antigos: cd gpononu
SN_RE = re.compile(r"\b([A-Z]{4}[0-9A-Fa-f]{8})\b")
PON_CARD = re.compile(r"\b(GC[0-9A-Z]{2}|XG[0-9A-Z]{2}|GPON\w*)\b")
CARD_PORTS = {"GC4B": 4, "GC8B": 8, "GC8E": 8, "GCOB": 16, "GCOD": 16, "GCOG": 16, "XGOD": 16, "XG8A": 8}
ON_WORDS = {"up", "online", "active", "working", "normal"}
OFF_WORDS = {"dn", "down", "offline", "los", "dg", "dying-gasp", "dyinggasp", "deactive", "inactive", "lost"}
WARN = "Sintaxe da AN5516 (CLI clássica); confira na versão do firmware (RP) antes de aplicar — a AN6000 usa outra CLI."


def norm_sn(v: str) -> str:
    s = re.sub(r"[\s:-]", "", str(v or "")).upper()
    if not re.fullmatch(r"[A-Z]{4}[0-9A-F]{8}", s):
        raise GenError("Serial inválido: 4 letras + 8 hexadecimais (ex.: FHTT1A2B3C4D)")
    return s


def _slot(v, what="Slot") -> int:
    n = _int(v, what)
    if not 1 <= n <= 40:
        raise GenError(f"{what}: 1 a 40")
    return n


def _pon(v) -> int:
    n = _int(v, "PON")
    if not 1 <= n <= 16:
        raise GenError("PON: 1 a 16")
    return n


def _onu(v) -> int:
    n = _int(v, "ONU")
    if not 1 <= n <= 128:
        raise GenError("ONU: 1 a 128")
    return n


def _uplink(v: str) -> str:
    s = str(v or "").strip()
    if not re.fullmatch(r"\d{1,2}:\d{1,2}", s):
        raise GenError(f"Uplink {s!r}: use slot:porta (ex.: 19:1)")
    return s


def _word(v, what, need=True, maxlen=32) -> str:
    s = str(v or "").strip()
    if need and not s:
        raise GenError(f"{what}: obrigatório")
    if s and not re.fullmatch(rf"[A-Za-z0-9_.@-]{{1,{maxlen}}}", s):
        raise GenError(f"{what}: até {maxlen} caracteres, sem espaço")
    return s


def _dir(p: dict) -> str:
    d = p.get("dir") or "onu"
    return d if d in DIRS else "onu"


def _head(comments: bool, text: str) -> List[str]:
    return ([f"! {text}"] if comments else []) + ["enable"]


# ---------------------------------------------------------------- scripts
def onu_script(p: dict) -> dict:
    sl, pn, o = _slot(p.get("slot")), _pon(p.get("pon")), _onu(p.get("onu_id"))
    sn = norm_sn(p.get("sn"))
    typ = _word(p.get("type"), "Tipo da ONU (ex.: HG6143D, AN5506-04-F1)")
    d = _dir(p)
    comments = bool(p.get("comments", True))
    name = str(p.get("name") or "").strip()[:64]
    svcs = []
    for n, sv in enumerate(p.get("services") or [], 1):
        mode = sv.get("mode") if sv.get("mode") in ("bridge", "pppoe", "veip") else "bridge"
        ports = [int(x) for x in (sv.get("ports") or []) if str(x).isdigit() and 1 <= int(x) <= 8] or [1]
        svcs.append({"vlan": _vlan(sv.get("vlan"), f"Serviço {n}: VLAN"), "mode": mode, "ports": ports,
                     "user": str(sv.get("pppoe_user") or "").strip(), "password": str(sv.get("pppoe_password") or "")})
    if not svcs:
        raise GenError("Informe ao menos um serviço (VLAN)")
    out = _head(comments, f"Autorizar {sn} ({typ}) em slot {sl} pon {pn} onu {o}" + (f" — {name}" if name else ""))
    out += [f"cd {d}", f"set whitelist phy_addr address {sn} password null action add slot {sl} pon {pn} onu {o} type {typ}", "cd lan"]
    warns = [WARN]
    per_port: Dict[int, int] = {}
    veip, wan = 0, 0
    for sv in svcs:
        if sv["mode"] == "bridge":
            for e in sv["ports"]:
                per_port[e] = per_port.get(e, 0) + 1
    for e, cnt in sorted(per_port.items()):
        out.append(f"set epon slot {sl} pon {pn} onu {o} port {e} service number {cnt}")
    used: Dict[int, int] = {}
    for sv in svcs:
        if sv["mode"] == "bridge":
            for e in sv["ports"]:
                used[e] = used.get(e, 0) + 1
                out.append(f"set epon slot {sl} pon {pn} onu {o} port {e} service {used[e]} vlan_mode tag 0 33024 {sv['vlan']}")
        else:
            if sv["mode"] == "pppoe":
                if not re.fullmatch(r"[^\s\"]{1,64}", sv["user"]) or not re.fullmatch(r"[^\s\"]{1,64}", sv["password"]):
                    raise GenError("PPPoE: informe usuário e senha (sem espaço nem aspas)")
                wan += 1
                out.append(f"set wancfg slot {sl} {pn} {o} index {wan} mode internet type route {sv['vlan']} 0 nat enable qos disable "
                           f"dsp pppoe proxy disable {sv['user']} {sv['password']} null auto entries 6 fe1 fe2 fe3 fe4 ssid1 ssid5")
            veip += 1
            out.append(f"set epon slot {sl} pon {pn} onu {o} port 1 onuveip {veip} 0x8100 {sv['vlan']} 0 33024 65535 65535 "
                       f"33024 65535 65535 0 1 65535 service_type 1")
    if wan:
        out.append(f"apply wancfg slot {sl} {pn} {o}")
        warns.append("WAN PPPoE (set wancfg) muda de formato entre RPs: nos novos entra 'vlanmode tag tvlan disable …' antes de 'dsp'.")
    out.append(f"apply onu {sl} {pn} {o} vlan")
    out.append("cd ..")
    out.append("cd ..")
    if p.get("write"):
        out.append("save")
    return {"script": "\n".join(out) + "\n", "lines": len(out), "warnings": warns,
            "filename": f"onu-fh-{sl}-{pn}-{o}.txt", "interface": f"slot {sl} pon {pn} onu {o}"}


def onu_remove_script(p: dict) -> dict:
    items = p.get("items") or []
    if not items:
        raise GenError("Informe ao menos uma ONU")
    if len(items) > 64:
        raise GenError("Até 64 ONUs por vez")
    rows, seen = [], set()
    for n, it in enumerate(items, 1):
        k = (_slot(it.get("slot")), _pon(it.get("pon")), _onu(it.get("onu")))
        if k in seen:
            raise GenError(f"ONU {k[0]}/{k[1]}/{k[2]} repetida")
        seen.add(k)
        if not it.get("sn"):
            raise GenError(f"Linha {n}: falta o serial — clique em Conferir para ler da OLT (a FiberHome remove pelo serial)")
        typ = _word(it.get("type"), "Tipo", need=False)
        rows.append((*k, norm_sn(it.get("sn")), typ))
    d = _dir(p)
    out = _head(bool(p.get("comments", True)), f"Desautorizar {len(rows)} ONU(s)") + [f"cd {d}"]
    for sl, pn, o, sn, typ in rows:
        out.append(f"set whitelist phy_addr address {sn} password null action delete slot {sl} pon {pn} onu {o}" + (f" type {typ}" if typ else ""))
    out.append("cd ..")
    if p.get("write"):
        out.append("save")
    return {"script": "\n".join(out) + "\n", "lines": len(out), "filename": f"desautorizar-fh-{len(rows)}.txt",
            "warnings": [WARN, "A ONU sai da whitelist; o serviço dela para na hora."]}


def generate(p: dict) -> dict:
    comments = bool(p.get("comments", True))
    out, warns = ["enable"], [WARN]

    def c(t):
        if comments:
            out.append(f"! {t}")
    ups = [_uplink(u) for u in (p.get("uplinks") or []) if str(u).strip()]
    if ups:
        c("Uplinks")
        out.append("cd device")
        out += [f"set uplink port {u} enable" for u in ups]
        out.append("cd ..")
    if p.get("cards_auth", True):
        c("Autoriza as placas instaladas")
        out.append("set cards_all_auth")
    v = p.get("vlans") or {}
    m = p.get("mgmt") or {}
    if v.get("enabled") or m.get("enabled"):
        out.append("cd service")
    if v.get("enabled"):
        start, count = _vlan(v.get("start"), "VLAN inicial"), _int(v.get("count"), "Quantidade de VLANs")
        if not 1 <= count <= 1000 or start + count - 1 > 4094:
            raise GenError("Faixa de VLANs inválida")
        name = _word(v.get("name") or "CLIENTES", "Nome do serviço", maxlen=16)
        if not ups:
            raise GenError("Informe a porta de uplink que leva as VLANs de clientes")
        c(f"VLANs de clientes {start}–{start + count - 1} no uplink")
        for u in ups:
            out.append(f"set service {name} vid_begin {start} vid_end {start + count - 1} uplink {u} tagged service_type 1")
    if m.get("enabled"):
        vid = _vlan(m.get("vlan"), "VLAN de gerência")
        ip = str(m.get("ip") or "").strip()
        if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}/\d{1,2}", ip):
            raise GenError("IP de gerência no formato 192.0.2.10/30")
        _ip(ip.split("/")[0], "IP de gerência")
        gw = _ip(m.get("gw"), "Gateway de gerência")
        port = _uplink(m.get("port") or (ups[0] if ups else ""))
        c("VLAN e IP de gerência")
        out += [f"set manage vlan name GERENCIA vid {vid} inputport {port} tagged", f"set manage vlan name GERENCIA ip {ip} {gw}"]
    if v.get("enabled") or m.get("enabled"):
        if m.get("enabled") and m.get("route", True):
            out.append(f"add static route destination 0.0.0.0 gateway {gw} mask 0.0.0.0")
        snmp = p.get("snmp") or {}
        if snmp.get("enabled"):
            out.append(f"set snmp community readonly {_word(snmp.get('community'), 'Community SNMP')}")
        out.append("cd ..")
    else:
        snmp = p.get("snmp") or {}
        if snmp.get("enabled"):
            out += ["cd service", f"set snmp community readonly {_word(snmp.get('community'), 'Community SNMP')}", "cd .."]
    a = p.get("autosave") or {}
    if a.get("enabled"):
        t = str(a.get("time") or "03:00")
        if not re.fullmatch(r"\d{2}:\d{2}", t):
            raise GenError("Horário do autosave: HH:MM")
        h, mi = (int(x) for x in t.split(":"))
        out.append(f"set auto_save enable period 1440 active_time hour {h} min {mi}")
    if p.get("write", True):
        out.append("save")
    return {"script": "\n".join(out) + "\n", "lines": len(out), "warnings": warns, "filename": "olt-fiberhome.txt"}


# ---------------------------------------------------------------- leitura (tolerante)
def parse_cards(text: str) -> List[dict]:
    """showcard -> placas PON [{slot, name, ports}]."""
    out, seen = [], set()
    for ln in (text or "").splitlines():
        m = PON_CARD.search(ln.upper())
        if not m:
            continue
        nums = [int(x) for x in re.findall(r"\b(\d{1,2})\b", ln[:m.start()] or ln)]
        nums = [x for x in nums if 1 <= x <= 40]
        if not nums or nums[0] in seen:
            continue
        name = m.group(1)
        if re.search(r"(?i)\b(empty|absent|unauth|mismatch)\b", ln):
            continue
        seen.add(nums[0])
        out.append({"slot": nums[0], "name": name, "ports": CARD_PORTS.get(name, 16 if name[2:3] == "O" else 8)})
    return out


def _type_token(tokens: List[str], sn: str) -> str:
    for t in tokens:
        if t.upper() == sn or t.lower() in ON_WORDS | OFF_WORDS:
            continue
        if re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9_.\-]{2,30}", t) and not re.fullmatch(r"[0-9A-Fa-f]{12,}", t):
            return t
    return ""


def parse_discovery(text: str, slot: int, pon: int) -> List[dict]:
    """show discovery slot S pon P: cada linha com serial vira uma ONU esperando autorização."""
    out = []
    for ln in (text or "").splitlines():
        m = SN_RE.search(ln)
        if not m or re.search(r"(?i)phy_addr|address\s*:", ln) and "add" in ln.lower():
            continue
        sn = m.group(1).upper()
        if any(o["sn"] == sn for o in out):
            continue
        tokens = ln.split()
        out.append({"slot": slot, "pon": pon, "sn": sn, "type": _type_token(tokens, sn), "pon_name": f"{slot}/{pon}"})
    return out


def parse_auth(text: str, slot: int, pon: int) -> List[dict]:
    """show authorization slot S pon P: ONU, tipo, serial e estado. Aceita linha começando por 'S P O' ou só 'O'."""
    out = {}
    for ln in (text or "").splitlines():
        m = SN_RE.search(ln)
        if not m:
            continue
        sn = m.group(1).upper()
        nums = [int(x) for x in re.findall(r"(?<![\w.:/-])(\d{1,3})(?![\w.:/-])", ln[:m.start()])]
        if len(nums) >= 3 and nums[0] == slot and nums[1] == pon:
            o = nums[2]
        elif nums:
            o = nums[0] if nums[0] != slot or len(nums) == 1 else nums[-1]
        else:
            continue
        if not 1 <= o <= 128:
            continue
        words = {w.lower().strip(",;()") for w in ln.split()}
        state = "online" if words & ON_WORDS else "offline" if words & OFF_WORDS else ""
        out[o] = {"slot": slot, "pon": pon, "onu": o, "sn": sn, "type": _type_token(ln.split(), sn), "state": state,
                  "off": state == "offline"}
    return [out[k] for k in sorted(out)]


def parse_optic(text: str) -> Optional[float]:
    m = re.search(r"(?i)(?:recv|rx|receive)\w*\s*(?:optical\s*)?power[^\d\-]{0,20}(-?\d+(?:\.\d+)?)", text or "")
    if m:
        v = float(m.group(1))
        return v if -50 < v < 10 else None
    return None


def free_onu_id(used: List[int]) -> Optional[int]:
    return next((i for i in range(1, 129) if i not in set(used)), None)
