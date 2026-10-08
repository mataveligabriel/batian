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
    "C620": {"label": "ZXA10 C620", "note": "Modular 2U: slots 1 e 2 de serviço (16 PON cada), uplinks nas controladoras 4 e 5.",
             "pon": [{"slot": 1, "ports": 16}, {"slot": 2, "ports": 16}],
             "uplinks": [{"slot": 4, "ports": 4, "prefix": "xgei"}, {"slot": 5, "ports": 4, "prefix": "xgei"}]},
    "C600": {"label": "ZXA10 C600", "note": "Chassi grande: use “Ler placas da OLT” ou informe os slots instalados.", "pon": [], "uplinks": []},
    "C650": {"label": "ZXA10 C650", "note": "Chassi médio: use “Ler placas da OLT” ou informe os slots instalados.", "pon": [], "uplinks": []},
    "C610": {"label": "ZXA10 C610", "note": "Compacta: use “Ler placas da OLT” ou informe as portas.", "pon": [], "uplinks": []},
}

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
    pons, ups = clean_boards(p.get("pon"), p.get("uplinks"))
    up_names = [f"{u['prefix']}-1/{u['slot']}/{i + 1}" for u in ups for i in range(u["ports"]) if u["enabled"][i]]
    all_up = {f"{u['prefix']}-1/{u['slot']}/{i + 1}" for u in ups for i in range(u["ports"])}
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
                    out += [f"interface gpon_olt-1/{b['slot']}/{i + 1}", " no shutdown", "exit"]
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
            if in_on and n == i_vlan:
                out.append(" description GERENCIA")
            elif v_desc:
                out.append(f" description {v_desc}")
            out.append("exit")

    if up_names:
        blank(); c("Uplinks")
        for n in up_names:
            out += [f"interface {n}", " no shutdown"]
            for r in _ranges(up_vlans.get(n, [])):
                out.append(f" switchport vlan {r} tag")
            out.append("exit")

    # ---- gerência ----
    if out_on:
        blank(); c("Gerência outband (porta MGMT)")
        out += ["interface mgmt_eth", f" ip address {o_ip} {o_mask}", "exit"]
        if o_gw:
            out.append(f"ip route vrf mng 0.0.0.0 0.0.0.0 {o_gw}")
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
            out.append(f" onu-type {t} gpon max-tcont 5 max-gemport 32 max-switchperslot 2 max-flow-perswitch 8 max-iphost 5")
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
        out.append("exit")
        for name, kbps in speeds:
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
    if sv.get("enabled"):
        t = str(sv.get("time") or "").strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?", t):
            raise GenError("Horário do salvamento automático inválido (HH:MM)")
        if len(t) == 5:
            t += ":00"
        blank(); c("Salvamento automático diário")
        out.append(f"auto-write everyday {t}")

    bk = p.get("backup") or {}
    if bk.get("enabled"):
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
        if a.get("telnet"):
            out.append("line telnet server enable")
            warns.append("Telnet manda usuário e senha sem criptografia; prefira só SSH.")
    if (a.get("user") or "").strip():
        u = str(a["user"]).strip()
        if not _NAME_RE.match(u):
            raise GenError("Usuário local: letras, números, - _ . (até 32)")
        pw = _safe(a.get("password"), "Senha do usuário local")
        if len(pw) < 8:
            raise GenError("Senha do usuário local: mínimo de 8 caracteres")
        blank(); c("Usuário local administrador")
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

    secrets = bool((a.get("user") or "").strip() or bk.get("enabled") or sn.get("enabled"))
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
            ups.append({**info, "prefix": "xgei"})
    return {"pon": pon, "uplinks": ups, "others": others}
