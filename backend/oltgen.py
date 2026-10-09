"""Gerador de script de ativação para OLT ZTE da linha TITAN (C600, C650, C620, C610).

Recebe os parâmetros do formulário e devolve o script em texto (comandos de configuração). Nada é enviado para a OLT
aqui: o script é para revisar e aplicar. Os comandos marcados em CHECK são os que variam mais entre versões de firmware;
o script sai com um comentário avisando para conferir.

As placas (slots e quantidade de portas) vêm do modelo escolhido ou da leitura do `show card` da própria OLT.
"""
import ipaddress
import re
from typing import Dict, List, Optional, Tuple

# Mapas de placas. Só o C620 tem mapa fixo conferido (2 slots de serviço com 16 PON, controladoras 4 e 5 com 4 uplinks).
# Nos chassis maiores a quantidade e a posição das placas mudam de OLT para OLT: lê-se o `show card` ou preenche à mão.
MODELS: Dict[str, dict] = {
    "C620": {"label": "ZXA10 C620", "platform": "titan", "note": "Modular 2U: slots 1 e 2 de serviço (16 PON cada), uplinks nas controladoras 4 e 5.",
             "pon": [{"slot": 1, "ports": 16}, {"slot": 2, "ports": 16}],
             "uplinks": [{"slot": 4, "ports": 4, "prefix": "xgei"}, {"slot": 5, "ports": 4, "prefix": "xgei"}]},
    "C600": {"label": "ZXA10 C600", "platform": "titan", "note": "Chassi grande: use “Ler placas da OLT” ou informe os slots instalados.", "pon": [], "uplinks": []},
    "C650": {"label": "ZXA10 C650", "platform": "titan", "note": "Chassi médio: use “Ler placas da OLT” ou informe os slots instalados.", "pon": [], "uplinks": []},
    "C610": {"label": "ZXA10 C610", "platform": "titan", "note": "Compacta: use “Ler placas da OLT” ou informe as portas.", "pon": [], "uplinks": []},
    # série anterior (ZXAN V2.x): nomes gpon-olt_1/x/y e comandos próprios
    "C300": {"label": "ZXA10 C300", "platform": "c300", "note": "Chassi C300: use “Ler placas da OLT” ou informe os slots (PON GTGO/GTGH; uplink HUVQ/GUFQ ou controladora).",
             "pon": [], "uplinks": []},
    "C320": {"label": "ZXA10 C320", "platform": "c300", "note": "C320: use “Ler placas da OLT” ou informe os slots (PON e uplinks da controladora SMXA).",
             "pon": [], "uplinks": []},
}
PLATFORMS = {"titan": "TITAN (C600/C650/C620/C610)", "c300": "C300 / C320"}


def pon_if(plat: str, slot: int, port: int) -> str:
    return f"gpon-olt_1/{slot}/{port}" if plat == "c300" else f"gpon_olt-1/{slot}/{port}"


def onu_if_name(plat: str, slot: int, port: int, onu: int) -> str:
    return f"gpon-onu_1/{slot}/{port}:{onu}" if plat == "c300" else f"gpon_onu-1/{slot}/{port}:{onu}"


def up_if(plat: str, prefix: str, slot: int, port: int) -> str:
    return f"{prefix}_1/{slot}/{port}" if plat == "c300" else f"{prefix}-1/{slot}/{port}"

# perfis de ONU: nome -> (portas ethernet, portas pots, wifi)
ONU_TYPES: Dict[str, Tuple[int, int, int]] = {
    "ZTE-F601": (1, 0, 0), "ZTE-F612": (1, 1, 0), "ZTE-F660": (4, 2, 1), "ZTE-F670L": (4, 1, 1),
    "ZTE-F680": (4, 2, 1), "ZTE-F6600P": (4, 1, 1), "ZTE-F6201B": (1, 0, 1), "ZTE-F6640": (4, 1, 1),
    "GENERIC-1ETH": (1, 0, 0), "GENERIC-4ETH": (4, 0, 0),
}

_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
_SAFE_RE = re.compile(r"^[^\s\"'\\;|&`$<>]{1,64}$")             # senhas, comunidades: sem espaço nem aspas
_IFACE_RE = re.compile(r"^(xgei|gei|cgei|xxvgei|smartgroup)-?[0-9/]*$")


class GenError(ValueError):
    pass


def _ip(v: str, what: str) -> str:
    try:
        return str(ipaddress.IPv4Address((v or "").strip()))
    except ValueError:
        raise GenError(f"{what}: IPv4 inválido")


def _mask(v: str, what: str) -> str:
    v = str(v or "").strip().lstrip("/")
    try:
        if v.isdigit():
            return str(ipaddress.IPv4Network(f"0.0.0.0/{int(v)}").netmask)
        n = ipaddress.IPv4Network(f"0.0.0.0/{v}")
        return str(n.netmask)
    except ValueError:
        raise GenError(f"{what}: máscara inválida (use 255.255.255.0 ou 24)")


def _ip_mask(v: str, mask: str, what: str) -> Tuple[str, str]:
    """Aceita '10.0.0.2' + máscara, ou '10.0.0.2/24' sozinho."""
    v = (v or "").strip()
    if "/" in v:
        v, mask = v.split("/", 1)
    return _ip(v, what), _mask(mask, what)


def _vlan(v, what: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise GenError(f"{what}: VLAN inválida")
    if not 2 <= n <= 4094:
        raise GenError(f"{what}: VLAN fora de 2–4094")
    return n


def _safe(v: str, what: str) -> str:
    v = str(v or "")
    if not _SAFE_RE.match(v):
        raise GenError(f"{what}: use até 64 caracteres, sem espaço, aspas ou ; | & $ < >")
    return v


def _ranges(nums: List[int]) -> List[str]:
    """[100,101,102,999] -> ['100-102', '999']"""
    out, nums = [], sorted(set(nums))
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return out


def _speed_kbps(s: str) -> Tuple[str, int]:
    m = re.fullmatch(r"\s*(\d{1,5})\s*([MmGg])\s*", s or "")
    if not m:
        raise GenError(f"Velocidade inválida: {s!r} (use 100M, 500M, 1G…)")
    n, u = int(m.group(1)), m.group(2).upper()
    kbps = n * (1024000 if u == "G" else 1024)
    if not 64 <= kbps <= 10 * 1024000:
        raise GenError(f"Velocidade fora da faixa: {s}")
    return f"{n}{u}", kbps


def _int(v, what: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        raise GenError(f"{what}: informe um número")


def clean_boards(pon: list, uplinks: list) -> Tuple[List[dict], List[dict]]:
    pons, ups, seen = [], [], set()
    for b in pon or []:
        slot, ports = _int(b.get("slot"), "Slot da placa PON"), _int(b.get("ports"), "Portas da placa PON")
        if not (1 <= slot <= 32 and 1 <= ports <= 64) or ("p", slot) in seen:
            raise GenError(f"Placa PON inválida ou repetida (slot {slot})")
        seen.add(("p", slot))
        en = [bool(x) for x in (b.get("enabled") or [])][:ports]
        en += [True] * (ports - len(en)) if b.get("enabled") is None else [False] * (ports - len(en))
        pons.append({"slot": slot, "ports": ports, "enabled": en})
    for b in uplinks or []:
        slot, ports = _int(b.get("slot"), "Slot da placa de uplink"), _int(b.get("ports"), "Portas da placa de uplink")
        prefix = str(b.get("prefix") or "xgei")
        if prefix not in ("xgei", "gei", "cgei", "xxvgei"):
            raise GenError("Tipo de uplink inválido")
        if not (1 <= slot <= 32 and 1 <= ports <= 32) or ("u", slot) in seen:
            raise GenError(f"Placa de uplink inválida ou repetida (slot {slot})")
        seen.add(("u", slot))
        en = [bool(x) for x in (b.get("enabled") or [])][:ports]
        en += [False] * (ports - len(en))
        ups.append({"slot": slot, "ports": ports, "prefix": prefix, "enabled": en})
    return pons, ups


def generate(p: dict) -> dict:
    """Monta o script. Devolve {script, lines, warnings, filename}."""
    host = str(p.get("hostname") or "").strip()
    if not _HOST_RE.match(host):
        raise GenError("Hostname inválido (letras, números, - _ .)")
    model = str(p.get("model") or "C620")
    if model not in MODELS:
        raise GenError("Modelo desconhecido")
    comments = bool(p.get("comments", True))
    plat = MODELS[model]["platform"]
    c300 = plat == "c300"
    pons, ups = clean_boards(p.get("pon"), p.get("uplinks"))
    up_names = [up_if(plat, u["prefix"], u["slot"], i + 1) for u in ups for i in range(u["ports"]) if u["enabled"][i]]
    all_up = {up_if(plat, u["prefix"], u["slot"], i + 1) for u in ups for i in range(u["ports"])}
    warns: List[str] = []
    out: List[str] = []

    def c(text: str):
        if comments:
            out.append(f"! {text}")

    def blank():
        if out and out[-1] != "":
            out.append("")

    def pick_ups(lst, what) -> List[str]:
        sel = [x for x in (lst or []) if x]
        bad = [x for x in sel if x not in all_up]
        if bad:
            raise GenError(f"{what}: uplink {bad[0]} não existe nas placas informadas")
        return sel

    # ---- gerência e VLANs (validados antes de escrever) ----
    m = p.get("mgmt") or {}
    out_on, in_on = bool(m.get("out_enabled")), bool(m.get("in_enabled"))
    if out_on:
        o_ip, o_mask = _ip_mask(m.get("out_ip"), m.get("out_mask"), "Gerência outband")
        o_gw = _ip(m.get("out_gw"), "Gateway outband") if (m.get("out_gw") or "").strip() else ""
    if in_on:
        i_vlan = _vlan(m.get("in_vlan"), "VLAN de gerência")
        i_ip, i_mask = _ip_mask(m.get("in_ip"), m.get("in_mask"), "Gerência inband")
        i_gw = _ip(m.get("in_gw"), "Gateway inband") if (m.get("in_gw") or "").strip() else ""
        i_ups = pick_ups(m.get("in_uplinks"), "Gerência inband")
        if not i_ups:
            raise GenError("Gerência inband: escolha a uplink por onde a VLAN de gerência chega")
    if c300 and out_on and in_on and o_gw and i_gw:
        warns.append("No C300 as duas rotas padrão ficam na mesma tabela: deixe só um gateway (outband ou inband).")
    if (out_on or in_on) and not ((out_on and o_gw) or (in_on and i_gw)):
        warns.append("Nenhum gateway de gerência informado: a OLT só será alcançada pela rede local da gerência.")

    v = p.get("vlans") or {}
    vlan_ids: List[int] = []
    v_desc, v_ups = "", []
    if v.get("enabled"):
        start = _vlan(v.get("start"), "VLAN inicial")
        try:
            count = int(v.get("count") or 0)
        except (TypeError, ValueError):
            raise GenError("Quantidade de VLANs inválida")
        if not 1 <= count <= 512 or start + count - 1 > 4094:
            raise GenError("Quantidade de VLANs inválida (1–512, sem passar de 4094)")
        vlan_ids = list(range(start, start + count))
        if in_on and i_vlan in vlan_ids:
            raise GenError(f"A VLAN de gerência {i_vlan} está dentro da faixa de VLANs de acesso")
        v_ups = pick_ups(v.get("uplinks"), "VLANs de acesso")
        if not v_ups:
            raise GenError("VLANs de acesso: escolha ao menos uma uplink")
        v_desc = str(v.get("description") or "").strip()
        if v_desc and not _NAME_RE.match(v_desc):
            raise GenError("Descrição das VLANs: letras, números, - _ . (até 32)")

    # ---- cabeçalho ----
    c(f"Script BastiON · {MODELS[model]['label']} · {host}")
    c("Revise antes de aplicar. Comandos marcados com CONFERIR variam conforme a versão do firmware.")
    out.append("configure terminal")
    out.append(f"hostname {host}")

    # ---- portas ----
    if pons:
        blank(); c("Portas PON")
        for b in pons:
            for i, on in enumerate(b["enabled"]):
                if on:
                    out += [f"interface {pon_if(plat, b['slot'], i + 1)}", " no shutdown", "exit"]
    sel_up = set(up_names)
    up_vlans: Dict[str, List[int]] = {}
    if v.get("enabled"):
        for n in v_ups:
            up_vlans.setdefault(n, []).extend(vlan_ids)
    if in_on:
        for n in i_ups:
            up_vlans.setdefault(n, []).append(i_vlan)
    for n in up_vlans:
        if n not in sel_up:
            warns.append(f"{n} recebe VLANs mas não está marcada para ativar: incluída no script.")
            sel_up.add(n)
            up_names.append(n)

    # VLANs primeiro (a uplink só aceita VLAN que já existe)
    all_vlans = sorted(set(vlan_ids + ([i_vlan] if in_on else [])))
    if all_vlans:
        blank(); c("VLANs")
        for n in all_vlans:
            out.append(f"vlan {n}")
            kw = "name" if c300 else "description"
            if in_on and n == i_vlan:
                out.append(f" {kw} GERENCIA")
            elif v_desc:
                out.append(f" {kw} {v_desc}")
            out.append("exit")

    if up_names:
        blank(); c("Uplinks")
        for n in up_names:
            out += [f"interface {n}", " no shutdown"]
            if c300 and up_vlans.get(n):
                out.append(" switchport mode trunk")
            for r in _ranges(up_vlans.get(n, [])):
                out.append(f" switchport vlan {r} tag")
            out.append("exit")

    # ---- gerência ----
    if out_on:
        blank(); c("Gerência outband (porta MGMT)")
        out += [f"interface {'mng1' if c300 else 'mgmt_eth'}", f" ip address {o_ip} {o_mask}", "exit"]
        if o_gw:
            out.append(f"ip route 0.0.0.0 0.0.0.0 {o_gw}" if c300 else f"ip route vrf mng 0.0.0.0 0.0.0.0 {o_gw}")
    if in_on:
        blank(); c(f"Gerência inband (VLAN {i_vlan})")
        out += [f"interface vlan{i_vlan}", f" ip address {i_ip} {i_mask}", "exit"]
        if i_gw:
            out.append(f"ip route 0.0.0.0 0.0.0.0 {i_gw}")

    # ---- perfis ----
    onus = [t for t in (p.get("onu_types") or []) if t in ONU_TYPES]
    if onus:
        blank(); c("Perfis de ONU (CONFERIR: nomes e portas aceitos pela versão do firmware)")
        out.append("pon")
        for t in onus:
            eth, pots, wifi = ONU_TYPES[t]
            out.append(f" onu-type {t} gpon max-tcont 7 max-gemport 32 max-switch-perslot 8 max-flow-perswitch 8 max-iphost 2" if c300
                       else f" onu-type {t} gpon max-tcont 5 max-gemport 32 max-switchperslot 2 max-flow-perswitch 8 max-iphost 5")
            out += [f" onu-type-if {t} eth_0/{i}" for i in range(1, eth + 1)]
            out += [f" onu-type-if {t} pots_0/{i}" for i in range(1, pots + 1)]
            if wifi:
                out.append(f" onu-type-if {t} wifi_0/1")
        out.append("exit")

    speeds = []
    for s in (p.get("speeds") or []):
        name, kbps = _speed_kbps(s)
        if name not in [x[0] for x in speeds]:
            speeds.append((name, kbps))
    if speeds:
        blank(); c("Perfis de banda (T-CONT e tráfego)")
        out.append("gpon")
        for name, kbps in speeds:
            out.append(f" profile tcont {name} type 4 maximum {kbps}")
        if c300:
            for name, kbps in speeds:
                out.append(f" profile traffic {name} sir {kbps} pir {kbps}")
        out.append("exit")
        for name, kbps in (speeds if not c300 else []):
            out.append(f"traffic-profile {name} cir {kbps} cbs 1024 pir {kbps} pbs 1024 color-mode blind "
                       "policer-type enhanced_mef coupling-flag enable")

    # ---- sistema ----
    ntp = [x for x in (p.get("ntp") or []) if str(x or "").strip()][:2]
    if ntp:
        blank(); c("NTP (CONFERIR)")
        out.append("ntp enable")
        for x in ntp:
            out.append(f"ntp server {_ip(x, 'Servidor NTP')}")

    sv = p.get("autosave") or {}
    if sv.get("enabled") and c300:
        warns.append("Salvamento automático diário não está disponível para C300 neste gerador: grave com write.")
    elif sv.get("enabled"):
        t = str(sv.get("time") or "").strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?", t):
            raise GenError("Horário do salvamento automático inválido (HH:MM)")
        if len(t) == 5:
            t += ":00"
        blank(); c("Salvamento automático diário")
        out.append(f"auto-write everyday {t}")

    bk = p.get("backup") or {}
    if bk.get("enabled") and c300:
        warns.append("Backup por FTP não está disponível para C300 neste gerador: use os Backups do BastiON.")
    elif bk.get("enabled"):
        srv = _ip(bk.get("server"), "Servidor de backup")
        bu, bp = _safe(bk.get("username"), "Usuário do FTP"), _safe(bk.get("password"), "Senha do FTP")
        path = str(bk.get("path") or "").strip() or host
        if not _NAME_RE.match(path):
            raise GenError("Pasta do backup: letras, números, - _ . (até 32)")
        blank(); c("Backup automático da configuração por FTP")
        out.append(f"startrun backup-to server {srv} transport-type ftp username {bu} password {bp} path {path}")

    # ---- acesso ----
    a = p.get("access") or {}
    if a.get("ssh") or a.get("telnet"):
        blank(); c("Acesso remoto")
        if a.get("ssh"):
            out.append("ssh server enable")
            if c300:
                out.append("ssh server version 2")
        if a.get("telnet") and not c300:
            out.append("line telnet server enable")
        if a.get("telnet"):
            warns.append("Telnet manda usuário e senha sem criptografia; prefira só SSH.")
    if (a.get("user") or "").strip():
        u = str(a["user"]).strip()
        if not _NAME_RE.match(u):
            raise GenError("Usuário local: letras, números, - _ . (até 32)")
        pw = _safe(a.get("password"), "Senha do usuário local")
        if len(pw) < 8:
            raise GenError("Senha do usuário local: mínimo de 8 caracteres")
        blank(); c("Usuário local administrador")
        if c300:
            out.append(f"username {u} password {pw} privilege 15")
        else:
            out += ["system-user", f" user-name {u}", f"  password {pw}", "  bind authentication-template 1",
                    "  bind authorization-template 1", " exit", "exit"]

    sn = p.get("snmp") or {}
    if sn.get("enabled"):
        com = _safe(sn.get("community"), "Comunidade SNMP")
        if com.lower() in ("public", "private"):
            warns.append("Comunidade SNMP padrão (public/private): troque por uma própria.")
        blank(); c("SNMP v2c, somente leitura (CONFERIR)")
        out += ["snmp-server view AllView internet included", f"snmp-server community {com} view AllView ro"]
        if (sn.get("trap_host") or "").strip():
            out.append(f"snmp-server host {_ip(sn['trap_host'], 'Destino dos traps')} trap version 2c {com}")
            out.append("snmp-server enable trap")

    blank()
    out.append("end")
    if p.get("write", True):
        c("grava a configuração (responda yes se a OLT pedir confirmação)")
        out.append("write")

    secrets = bool((a.get("user") or "").strip() or (bk.get("enabled") and not c300) or sn.get("enabled"))
    if secrets:
        warns.append("O script tem senha/comunidade em texto: não guarde nem envie esse arquivo por canais abertos.")
    return {"script": "\n".join(out) + "\n", "lines": len(out), "warnings": warns,
            "filename": f"{host}-{model}.txt", "has_secrets": secrets}


# ---------- leitura do `show card` ----------
_CARD_ROW = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+([A-Z][A-Z0-9_-]*)\s+(?:([A-Z][A-Z0-9_-]*)\s+)?(\d+)\s+(.*)$")


def parse_show_card(text: str) -> dict:
    """Separa as placas PON e as que têm uplink. Ventilação, energia e slots vazios ficam de fora."""
    pon, ups, others = [], [], []
    for line in (text or "").splitlines():
        mm = _CARD_ROW.match(line)
        if not mm:
            continue
        slot, cfg, real, ports = int(mm.group(3)), mm.group(4), mm.group(5) or mm.group(4), int(mm.group(6))
        status = mm.group(7).split()[-1] if mm.group(7).split() else ""
        card = real or cfg
        info = {"slot": slot, "card": card, "ports": ports, "status": status}
        if ports <= 0 or card.startswith(("PR", "FAN", "PW", "FC")):
            others.append(info)
        elif card.startswith(("GF", "GT", "XG", "GP", "XF")):
            pon.append(info)
        else:
            ups.append({**info, "prefix": "gei" if card.startswith(("GU", "GE")) else "xgei"})
    return {"pon": pon, "uplinks": ups, "others": others}


# ---------- autorização de ONU ----------
ONU_MODES = {
    "tag": "VLAN tag (porta da ONU entrega sem tag; a ONU marca a VLAN)",
    "hybrid": "Híbrida (VLAN padrão sem tag na porta + demais com tag)",
    "transparent": "Transparente (a ONU repassa o que vier, com ou sem tag)",
}
_SN_RE = re.compile(r"^[A-Za-z0-9]{4}[0-9A-Fa-f]{8}$")
_PON_RE = re.compile(r"^(?:gpon_olt-|gpon-olt_)?1/(\d{1,2})/(\d{1,2})$")


def _pon(v: str) -> Tuple[int, int]:
    m = _PON_RE.match(str(v or "").strip())
    if not m:
        raise GenError("Porta PON inválida (ex.: 1/1/3)")
    return int(m.group(1)), int(m.group(2))


def _label(v: str, what: str, need: bool = False) -> str:
    v = re.sub(r"\s+", "_", str(v or "").strip())
    if not v:
        if need:
            raise GenError(f"{what}: obrigatório")
        return ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,63}", v):
        raise GenError(f"{what}: letras, números e - _ . : / (até 64)")
    return v


def onu_script(p: dict) -> dict:
    """Script de autorização de uma ONU.
    TITAN: gpon_onu + vport + pon-onu-mng · C300: gpon-onu (service-port com vport) + pon-onu-mng."""
    plat = str(p.get("platform") or "titan")
    if plat not in PLATFORMS:
        raise GenError("Série da OLT inválida")
    c300 = plat == "c300"
    slot, port = _pon(p.get("pon"))
    onu_id = _int(p.get("onu_id"), "ID da ONU")
    if not 1 <= onu_id <= 128:
        raise GenError("ID da ONU: 1 a 128")
    otype = str(p.get("type") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", otype):
        raise GenError("Tipo da ONU inválido (o nome do onu-type cadastrado na OLT)")
    sn = str(p.get("sn") or "").strip().upper()
    if not _SN_RE.match(sn):
        raise GenError("Serial inválido: 4 letras + 8 hexadecimais (ex.: ZTEGC1A2B3C4)")
    name = _label(p.get("name"), "Nome do cliente", need=True)
    desc = _label(p.get("description"), "Descrição")
    tcont = _label(p.get("tcont_profile"), "Perfil de banda (T-CONT)", need=True)
    eth_total = ONU_TYPES.get(otype, (4, 0, 0))[0] or 1
    warns: List[str] = []

    # serviços: cada VLAN vira um gemport + service-port; as portas da ONU são montadas a partir deles
    raw = p.get("services")
    if not raw:                                            # formato antigo: uma VLAN só
        raw = [{"vlan": p.get("vlan"), "user_vlan": p.get("user_vlan"), "mode": p.get("mode"), "ports": p.get("ports") or [1]}]
    if not isinstance(raw, list) or len(raw) > 8:
        raise GenError("Até 8 VLANs por ONU")
    svcs, seen_v = [], set()
    for n, r in enumerate(raw, 1):
        r = r or {}
        what = f"VLAN {n}"
        vlan = _vlan(r.get("vlan"), what)
        mode = str(r.get("mode") or "tag")
        if mode not in ONU_MODES:
            raise GenError(f"{what}: modo inválido")
        uvlan = _vlan(r.get("user_vlan") or vlan, f"{what} (na ONU)")
        if mode == "transparent" and uvlan != vlan:
            raise GenError(f"{what}: no modo transparente a VLAN passa como está — não dá para traduzir {uvlan}→{vlan}")
        if uvlan in seen_v:
            raise GenError(f"VLAN {uvlan} repetida na mesma ONU")
        seen_v.add(uvlan)
        ports = sorted({_int(x, "Porta ethernet") for x in (r.get("ports") or [])})
        if not ports:
            raise GenError(f"{what}: marque ao menos uma porta ethernet")
        if any(not 1 <= x <= 8 for x in ports):
            raise GenError("Porta ethernet da ONU: 1 a 8")
        if otype in ONU_TYPES and ports[-1] > eth_total:
            warns.append(f"{otype} tem {eth_total} porta(s) ethernet; confira as portas da VLAN {vlan}.")
        svcs.append({"vlan": vlan, "uvlan": uvlan, "mode": mode, "ports": ports})

    # por porta: no máximo uma VLAN sem tag (tag ou híbrida); transparentes passam com tag
    port_cfg: Dict[int, dict] = {}
    for sv in svcs:
        for e in sv["ports"]:
            pc = port_cfg.setdefault(e, {"untag": None, "tagged": [], "transparent": False})
            if sv["mode"] in ("tag", "hybrid"):
                if pc["untag"] is not None:
                    raise GenError(f"eth {e}: só uma VLAN pode sair sem tag por porta "
                                   f"({pc['untag']['uvlan']} e {sv['uvlan']}); deixe uma delas como transparente")
                pc["untag"] = sv
            else:
                pc["transparent"] = True
                pc["tagged"].append(sv["uvlan"])
    lines_port: List[str] = []
    hybrid_used = False
    for e in sorted(port_cfg):
        pc = port_cfg[e]
        u = pc["untag"]
        if u and not pc["tagged"] and u["mode"] == "tag":
            lines_port.append(f" vlan port eth_0/{e} mode tag vlan {u['uvlan']}")
        elif u:
            # uma sem tag + outras com tag na mesma porta = híbrida
            hybrid_used = True
            lines_port.append(f" vlan port eth_0/{e} mode hybrid def-vlan {u['uvlan']}")
            for r in _ranges(pc["tagged"]):
                lines_port.append(f" vlan port eth_0/{e} vlan {r}")
            if u["mode"] == "tag" and pc["tagged"]:
                warns.append(f"eth {e}: VLAN {u['uvlan']} sem tag junto com {', '.join(map(str, pc['tagged']))} com tag — a porta foi montada como híbrida.")
        else:
            lines_port.append(f" vlan port eth_0/{e} mode transparent")
    if hybrid_used:
        warns.append("Porta híbrida: confira no firmware a sintaxe de def-vlan e das VLANs com tag na porta da ONU.")
    if any(sv["mode"] == "transparent" for sv in svcs):
        warns.append("VLANs transparentes chegam com tag no equipamento do cliente: ele precisa estar configurado com essas VLANs.")

    comments = bool(p.get("comments", True))
    onu_if = onu_if_name(plat, slot, port, onu_id)
    out: List[str] = []
    if comments:
        resumo = ", ".join(f"{sv['uvlan']} {sv['mode']}" for sv in svcs)
        out.append(f"! Autorização da ONU {sn} ({otype}) em {onu_if} · VLANs: {resumo}")
    out.append("configure terminal")
    out += [f"interface {pon_if(plat, slot, port)}", f" onu {onu_id} type {otype} sn {sn}", "exit"]
    out += [f"interface {onu_if}", f" name {name}"]
    if desc:
        out.append(f" description {desc}")
    out.append(f" tcont 1 profile {tcont}")
    out += [f" gemport {i} tcont 1" for i in range(1, len(svcs) + 1)]
    if c300:
        out += [f" service-port {i} vport {i} user-vlan {sv['uvlan']} vlan {sv['vlan']}" for i, sv in enumerate(svcs, 1)]
        out.append("exit")
    else:
        out.append("exit")
        for i, sv in enumerate(svcs, 1):
            out += [f"interface vport-1/{slot}/{port}.{onu_id}:{i}", f" service-port {i} user-vlan {sv['uvlan']} vlan {sv['vlan']}", "exit"]
    out.append(f"pon-onu-mng {onu_if}")
    out += [f" service {i} gemport {i} vlan {sv['uvlan']}" for i, sv in enumerate(svcs, 1)]
    out += lines_port
    out += ["exit", "end"]
    if p.get("write"):
        out.append("write")
    return {"script": "\n".join(out) + "\n", "lines": len(out), "warnings": warns,
            "filename": f"onu-{name}-{slot}-{port}-{onu_id}.txt", "interface": onu_if}


_UNCFG_SN = re.compile(r"\b([A-Z]{4}[0-9A-F]{8})\b")
_PON_ANY = re.compile(r"gpon[_-](?:olt|onu)[_-]?(\d+)/(\d+)/(\d+)", re.I)
_ONU_CFG = re.compile(r"^\s*onu\s+(\d+)\s+type\s+(\S+)\s+sn\s+(\S+)", re.M | re.I)


def parse_uncfg(text: str) -> List[dict]:
    """ONUs pedindo autorização: [{pon: '1/1/3', sn}]."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        pm, sm = _PON_ANY.search(line), _UNCFG_SN.search(line.upper())
        if pm and sm:
            pon = f"{pm.group(1)}/{pm.group(2)}/{pm.group(3)}"
            if (pon, sm.group(1)) not in seen:
                seen.add((pon, sm.group(1)))
                out.append({"pon": pon, "sn": sm.group(1)})
    return out


def used_onu_ids(running_cfg: str) -> List[int]:
    return sorted({int(m.group(1)) for m in _ONU_CFG.finditer(running_cfg or "")})


def free_onu_id(used: List[int]) -> Optional[int]:
    s = set(used)
    return next((i for i in range(1, 129) if i not in s), None)


# ---------- desautorizar ONU ----------
def onu_remove_script(p: dict) -> dict:
    """`no onu N` na PON de cada ONU informada (slot, pon, onu). Remove a ONU e a configuração dela."""
    plat = str(p.get("platform") or "titan")
    if plat not in PLATFORMS:
        raise GenError("Série da OLT inválida")
    items = p.get("items") or []
    if not isinstance(items, list) or not items:
        raise GenError("Informe ao menos uma ONU (slot, PON e ONU)")
    if len(items) > 64:
        raise GenError("Até 64 ONUs por vez")
    by_pon: Dict[Tuple[int, int], List[int]] = {}
    for n, it in enumerate(items, 1):
        it = it or {}
        slot = _int(it.get("slot"), f"Linha {n}: slot")
        pon = _int(it.get("pon"), f"Linha {n}: PON")
        onu = _int(it.get("onu"), f"Linha {n}: ONU")
        if not 1 <= slot <= 32 or not 1 <= pon <= 64:
            raise GenError(f"Linha {n}: slot ou PON fora da faixa")
        if not 1 <= onu <= 128:
            raise GenError(f"Linha {n}: ONU de 1 a 128")
        lst = by_pon.setdefault((slot, pon), [])
        if onu in lst:
            raise GenError(f"ONU {slot}/{pon}:{onu} repetida")
        lst.append(onu)
    out: List[str] = []
    total = sum(len(v) for v in by_pon.values())
    if p.get("comments", True):
        out.append(f"! Desautorizar {total} ONU(s) — remove a ONU e toda a configuração dela (service-port, VLANs)")
    out.append("configure terminal")
    for (slot, pon), onus in sorted(by_pon.items()):
        out.append(f"interface {pon_if(plat, slot, pon)}")
        out += [f" no onu {o}" for o in sorted(onus)]
        out.append("exit")
    out.append("end")
    if p.get("write"):
        out.append("write")
    first = sorted(by_pon.items())[0]
    fname = f"desautorizar-{first[0][0]}-{first[0][1]}-{sorted(first[1])[0]}.txt" if total == 1 else f"desautorizar-{total}-onus.txt"
    return {"script": "\n".join(out) + "\n", "lines": len(out), "filename": fname,
            "warnings": ["A ONU some da OLT na hora: o cliente fica sem serviço até ser autorizada de novo."]}


_ONU_NAME = re.compile(r"^\s*name\s+(\S+)", re.M)
_ONU_DESC = re.compile(r"^\s*description\s+(.+)$", re.M)


def find_onu(pon_cfg: str, onu_cfg: str, onu: int) -> Optional[dict]:
    """Da running-config da PON e da ONU: tipo, serial, nome e descrição da ONU N (None se não existe)."""
    for m in _ONU_CFG.finditer(pon_cfg or ""):
        if int(m.group(1)) == onu:
            nm, ds = _ONU_NAME.search(onu_cfg or ""), _ONU_DESC.search(onu_cfg or "")
            return {"type": m.group(2), "sn": m.group(3), "name": nm.group(1) if nm else "",
                    "description": ds.group(1).strip() if ds else ""}
    return None


# ---------- procurar ONU pelo MAC ----------
_MAC_HEX = re.compile(r"[^0-9a-fA-F]")
_ONU_REF = re.compile(r"gpon[-_]onu[-_]1/(\d+)/(\d+):(\d+)|vport[-_]1/(\d+)/(\d+)\.(\d+):\d+", re.I)


def norm_mac(v: str) -> str:
    """Qualquer formato (aa:bb:cc:dd:ee:ff, aa-bb-…, aabb.ccdd.eeff) -> aabb.ccdd.eeff, como a ZTE mostra."""
    h = _MAC_HEX.sub("", str(v or "")).lower()
    if len(h) != 12:
        raise GenError("MAC inválido: use 12 dígitos hexadecimais (ex.: 7c:8b:ca:11:22:33)")
    return f"{h[0:4]}.{h[4:8]}.{h[8:12]}"


MAC_COMMANDS = ["show mac {mac}", "show mac address {mac}", "show mac-address address {mac}"]


def parse_mac_lookup(text: str, mac: str) -> Optional[dict]:
    """Acha na saída do `show mac` a ONU (slot, pon, onu) e a VLAN onde o MAC foi aprendido."""
    flat = mac.replace(".", "")
    for line in (text or "").splitlines():
        if flat not in _MAC_HEX.sub("", line).lower():
            continue
        m = _ONU_REF.search(line)
        if not m:
            continue
        g = m.groups()
        slot, pon, onu = (int(x) for x in (g[0:3] if g[0] else g[3:6]))
        plat = "c300" if re.search(r"gpon-onu_", line, re.I) else "titan"
        vlan = None
        rest = line.replace(m.group(0), " ")
        for tok in re.findall(r"\b(\d{1,4})\b", rest):
            if 1 <= int(tok) <= 4094 and tok not in mac:
                vlan = int(tok)
                break
        return {"slot": slot, "pon": pon, "onu": onu, "platform": plat, "vlan": vlan, "line": line.strip()}
    return None


# ---------- procurar ONU pelo serial (SN) ----------
SN_COMMANDS = ["show gpon onu by sn {sn}", "show pon onu by sn {sn}"]


def norm_sn(v: str) -> str:
    """ZTEGD4F3D9EC, ztegd4f3d9ec ou ZTEG-D4F3D9EC -> ZTEGD4F3D9EC."""
    s = re.sub(r"[\s:-]", "", str(v or "")).upper()
    # 4 letras do fabricante (ZTEG, FHTT, HWTC…) + 8 hexa; 12 dígitos só hexa é MAC, não serial
    if not re.fullmatch(r"[A-Z]{4}[0-9A-F]{8}", s) or re.fullmatch(r"[0-9A-F]{12}", s):
        raise GenError("Serial inválido: 4 letras + 8 hexadecimais (ex.: ZTEGD4F3D9EC)")
    return s


def looks_like_sn(v: str) -> bool:
    try:
        norm_sn(v)
        return True
    except GenError:
        return False


def parse_sn_lookup(text: str) -> Optional[dict]:
    """Saída do `show gpon onu by sn`: a primeira interface de ONU que aparecer."""
    for line in (text or "").splitlines():
        m = _ONU_REF.search(line)
        if m:
            g = m.groups()
            slot, pon, onu = (int(x) for x in (g[0:3] if g[0] else g[3:6]))
            plat = "c300" if re.search(r"gpon-onu_", line, re.I) else "titan"
            return {"slot": slot, "pon": pon, "onu": onu, "platform": plat, "vlan": None, "line": line.strip()}
    return None
