"""Mitigação de DDoS por blackhole (RTBH só na borda): regras, validações e registro.

O Bastion anuncia o /32 atacado por iBGP para as bordas com next-hop de descarte (ex.: 192.0.2.1 → NULL0),
a community combinada e NO_EXPORT (a rota não sai para trânsitos/IX). Quem fala BGP é o bgpd.py (container
"bgp"); aqui ficam as regras usadas pela API, pelo Telegram e pelo próprio bgpd.

Coleções: config {key:"bgp"}, flow_mitigations, bgp_status {_id:"status"}.
"""
import ipaddress
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

DEFAULTS = {
    "enabled": False,
    "local_as": 0,
    "router_id": "",
    "local_address": "",         # IP de origem das sessões (vazio = o sistema escolhe)
    "hold_time": 90,
    "next_hop": "192.0.2.1",     # na borda: rota estática 192.0.2.1/32 → NULL0
    "communities": ["65535:666"],  # BLACKHOLE (RFC 7999); use a que a sua política de import espera
    "no_export": True,           # "só na borda": nunca sai para trânsito/IX
    "local_pref": 200,
    "peers": [],                 # [{id, ip, name, remote_as, enabled}]
    "default_minutes": 30,
    "max_minutes": 1440,
    "max_active": 20,
    "protect": [],               # nunca fazer blackhole destes blocos (DNS, servidores, loopbacks…)
}
DURATIONS = [15, 30, 60, 120, 360, 720, 1440]
_COMM = re.compile(r"^\d{1,10}:\d{1,10}(:\d{1,10})?$")


class MitigationError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_settings(db) -> dict:
    doc = await db.config.find_one({"key": "bgp"}, {"_id": 0}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if k in DEFAULTS}}


def clean_settings(data: dict) -> dict:
    """Valida o que vem da tela. Lança MitigationError com mensagem para o usuário."""
    out = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
    try:
        out["local_as"] = int(out["local_as"] or 0)
    except (TypeError, ValueError):
        raise MitigationError("AS local inválido")
    if out["enabled"] and not (0 < out["local_as"] < 2 ** 32):
        raise MitigationError("Informe o AS (o mesmo das bordas, para iBGP)")
    for f, label in (("router_id", "Router-ID"), ("next_hop", "Next-hop de descarte"), ("local_address", "IP de origem")):
        v = str(out.get(f) or "").strip()
        if v:
            try:
                if ipaddress.ip_address(v).version != 4:
                    raise ValueError
            except ValueError:
                raise MitigationError(f"{label} precisa ser um IPv4")
        out[f] = v
    if out["enabled"] and not out["router_id"]:
        raise MitigationError("Informe o Router-ID (normalmente o IP do servidor do Bastion)")
    comms = []
    for c in out.get("communities") or []:
        c = str(c).strip()
        if not c:
            continue
        parts = c.split(":") if _COMM.match(c) else []
        if len(parts) == 2 and any(int(x) > 65535 for x in parts):
            raise MitigationError(f"Community {c}: ASN de 4 bytes não cabe em community comum — use a forma grande "
                                  f"(large community) {parts[0]}:{parts[1]}:0, ou 65535:666")
        if not parts or any(int(x) >= 2 ** 32 for x in parts):
            raise MitigationError(f"Community inválida: {c} (formatos: 65535:666 ou, para AS de 4 bytes, 263009:666:0)")
        comms.append(c)
    out["communities"] = comms
    out["hold_time"] = max(9, min(int(out.get("hold_time") or 90), 240))
    out["local_pref"] = max(0, min(int(out.get("local_pref") or 200), 2 ** 32 - 1))
    out["default_minutes"] = max(5, min(int(out.get("default_minutes") or 30), 1440))
    out["max_minutes"] = max(out["default_minutes"], min(int(out.get("max_minutes") or 1440), 10080))
    out["max_active"] = max(1, min(int(out.get("max_active") or 20), 500))
    prot = []
    for p in out.get("protect") or []:
        for part in str(p).replace(",", " ").split():
            try:
                prot.append(str(ipaddress.ip_network(part, strict=False)))
            except ValueError:
                raise MitigationError(f"Bloco protegido inválido: {part}")
    out["protect"] = prot
    peers, seen = [], set()
    for p in out.get("peers") or []:
        ip = str(p.get("ip") or "").strip()
        try:
            if ipaddress.ip_address(ip).version != 4:
                raise ValueError
        except ValueError:
            raise MitigationError(f"IP de vizinho inválido: {ip or '(vazio)'}")
        if ip in seen:
            raise MitigationError(f"Vizinho repetido: {ip}")
        seen.add(ip)
        ras = int(p.get("remote_as") or out["local_as"] or 0)
        peers.append({"id": p.get("id") or os.urandom(4).hex(), "ip": ip, "name": str(p.get("name") or "").strip()[:60],
                      "remote_as": ras, "enabled": p.get("enabled", True) is not False})
    out["peers"] = peers
    return out


def _in_any(ip: ipaddress.IPv4Address, blocks: List[str]) -> Optional[str]:
    for b in blocks or []:
        try:
            n = ipaddress.ip_network(b, strict=False)
        except ValueError:
            continue
        if n.version == ip.version and ip in n:
            return b
    return None


def check_target(prefix: str, own_prefixes: List[str], s: dict) -> str:
    """Normaliza para /32 e confere se pode sofrer blackhole."""
    raw = str(prefix or "").strip()
    try:
        net = ipaddress.ip_network(raw if "/" in raw else f"{raw}/32", strict=False)
    except ValueError:
        raise MitigationError(f"IP inválido: {raw or '(vazio)'}")
    if net.version != 4:
        raise MitigationError("Por enquanto a mitigação é só para IPv4")
    if net.prefixlen != 32:
        raise MitigationError("Só um IP (/32) por vez — blackhole de bloco inteiro derrubaria clientes que não estão sob ataque")
    ip = net.network_address
    if not own_prefixes:
        raise MitigationError("Cadastre os seus blocos em Flow → Configuração → 'Blocos próprios' antes de mitigar")
    if not _in_any(ip, own_prefixes):
        raise MitigationError(f"{ip} não está nos seus blocos próprios — o Bastion só faz blackhole de IP seu")
    hit = _in_any(ip, s.get("protect") or [])
    if hit:
        raise MitigationError(f"{ip} está na lista de protegidos ({hit}) — remova de lá se quiser mesmo mitigar")
    peer_ips = {p["ip"] for p in s.get("peers") or []}
    if str(ip) in peer_ips or str(ip) in (s.get("router_id"), s.get("local_address"), s.get("next_hop")):
        raise MitigationError(f"{ip} é usado pela própria sessão BGP/Bastion — não dá para mitigar")
    return f"{ip}/32"


async def create(db, user: dict, *, prefix: Optional[str] = None, attack_id: Optional[str] = None,
                 minutes: Optional[int] = None, reason: str = "", channel: str = "web") -> dict:
    s = await get_settings(db)
    if not s["enabled"] or not any(p.get("enabled", True) for p in s["peers"]):
        raise MitigationError("Mitigação desligada: configure o BGP e pelo menos uma borda em Flow → Mitigação")
    if user.get("role") == "viewer":
        raise MitigationError("Perfil View não pode mitigar")
    attack = None
    if attack_id:
        attack = await db.flow_attacks.find_one({"id": attack_id}, {"_id": 0, "series": 0})
        if not attack:
            raise MitigationError("Ataque não encontrado")
        if user.get("role") != "admin" and user["id"] not in (attack.get("owners") or []):
            raise MitigationError("Esse ataque não é das suas interfaces")
        prefix = attack["victim"]
    fs = await db.config.find_one({"key": "flow"}, {"_id": 0, "own_prefixes": 1}) or {}
    target = check_target(prefix, fs.get("own_prefixes") or [], s)
    minutes = int(minutes or s["default_minutes"])
    if not 5 <= minutes <= s["max_minutes"]:
        raise MitigationError(f"Duração entre 5 e {s['max_minutes']} minutos")
    now = _now()
    active = await db.flow_mitigations.find({"status": "active"}, {"_id": 0}).to_list(1000)
    cur = next((m for m in active if m["prefix"] == target), None)
    if cur:
        exp = max(datetime.fromisoformat(cur["expires_at"]), now + timedelta(minutes=minutes))
        await db.flow_mitigations.update_one({"id": cur["id"]}, {"$set": {"expires_at": exp.isoformat()},
                                                                  "$push": {"log": {"at": now.isoformat(), "by": user.get("email"), "what": f"estendida para {minutes} min"}}})
        return {**cur, "expires_at": exp.isoformat(), "extended": True}
    if len(active) >= s["max_active"]:
        raise MitigationError(f"Limite de {s['max_active']} mitigações ativas atingido")
    doc = {"id": os.urandom(8).hex(), "prefix": target, "status": "active", "attack_id": attack_id,
           "attack_type": (attack or {}).get("type"), "peak_bps": (attack or {}).get("peak_bps"),
           "reason": (reason or "").strip()[:200], "channel": channel,
           "created_at": now.isoformat(), "created_by": user.get("email"), "user_id": user["id"],
           "minutes": minutes, "expires_at": (now + timedelta(minutes=minutes)).isoformat(),
           "log": [{"at": now.isoformat(), "by": user.get("email"), "what": f"ativada por {minutes} min ({channel})"}]}
    await db.flow_mitigations.insert_one(dict(doc))
    return doc


async def extend(db, user: dict, mid: str, minutes: int) -> dict:
    s = await get_settings(db)
    m = await db.flow_mitigations.find_one({"id": mid, "status": "active"}, {"_id": 0})
    if not m:
        raise MitigationError("Mitigação não está ativa")
    if user.get("role") == "viewer" or (user.get("role") != "admin" and m.get("user_id") != user["id"]):
        raise MitigationError("Sem permissão para alterar esta mitigação")
    minutes = max(5, min(int(minutes), s["max_minutes"]))
    exp = _now() + timedelta(minutes=minutes)
    await db.flow_mitigations.update_one({"id": mid}, {"$set": {"expires_at": exp.isoformat()},
                                                        "$push": {"log": {"at": _now().isoformat(), "by": user.get("email"), "what": f"prazo: mais {minutes} min"}}})
    return {**m, "expires_at": exp.isoformat()}


async def withdraw(db, user: dict, mid: str) -> dict:
    m = await db.flow_mitigations.find_one({"id": mid}, {"_id": 0})
    if not m:
        raise MitigationError("Mitigação não encontrada")
    if user.get("role") == "viewer" or (user.get("role") != "admin" and m.get("user_id") != user["id"]):
        raise MitigationError("Sem permissão para remover esta mitigação")
    if m["status"] != "active":
        return m
    now = _now().isoformat()
    await db.flow_mitigations.update_one({"id": mid, "status": "active"}, {"$set": {"status": "withdrawn", "ended_at": now, "ended_by": user.get("email")},
                                                                           "$push": {"log": {"at": now, "by": user.get("email"), "what": "removida"}}})
    return {**m, "status": "withdrawn", "ended_at": now}


def fmt_minutes(m: int) -> str:
    return f"{m // 60} h" if m >= 60 and m % 60 == 0 else f"{m} min"


def router_config(s: dict, vendor: str, bastion_ip: str, own_prefixes: List[str]) -> str:
    """Configuração de referência para a borda aceitar o blackhole do Bastion."""
    asn = s.get("local_as") or "SEU_AS"
    nh = s.get("next_hop") or "192.0.2.1"
    comm = (s.get("communities") or ["65535:666"])[0]
    large = comm.count(":") == 2
    src = bastion_ip or s.get("local_address") or s.get("router_id") or "IP_DO_BASTION"
    blocks = own_prefixes or ["SEU_BLOCO/20"]
    if vendor == "juniper":
        out = [f"# Juniper — aceitar só /32 dos seus blocos com a community {comm}, descartar no next-hop {nh}",
               f"set routing-options static route {nh}/32 discard",
               f"set policy-options community BASTION-BH members {'large:' + comm if large else comm}",
               "set policy-options policy-statement BASTION-IN term bh from community BASTION-BH"]
        out += [f"set policy-options policy-statement BASTION-IN term bh from route-filter {b} prefix-length-range /32-/32" for b in blocks]
        out += ["set policy-options policy-statement BASTION-IN term bh then accept",
                "set policy-options policy-statement BASTION-IN term resto then reject",
                "set policy-options policy-statement BASTION-OUT then reject",
                "set protocols bgp group BASTION-RTBH type internal",
                'set protocols bgp group BASTION-RTBH description "Bastion - blackhole"',
                "set protocols bgp group BASTION-RTBH import BASTION-IN",
                "set protocols bgp group BASTION-RTBH export BASTION-OUT",
                f"set protocols bgp group BASTION-RTBH neighbor {src}",
                "# a rota vem com NO_EXPORT: não é repassada para trânsitos/IX.",
                f"# libere TCP/179 vindo de {src} no filtro do plano de controle (lo0), se houver.",
                f"# conferir: show bgp neighbor {src} | show route community {comm}"]
        return "\n".join(out) + "\n"
    lines = [f"# Huawei VRP — aceitar só /32 dos seus blocos com a community {comm}, descartar no next-hop {nh}",
             f"ip route-static {nh} 255.255.255.255 NULL0 description BASTION-BLACKHOLE"]
    for i, b in enumerate(blocks, 1):
        n = ipaddress.ip_network(b, strict=False) if "/" in b and b[0].isdigit() else None
        if n:
            lines.append(f"ip ip-prefix BASTION-BH index {i * 10} permit {n.network_address} {n.prefixlen} greater-equal 32 less-equal 32")
        else:
            lines.append(f"ip ip-prefix BASTION-BH index {i * 10} permit {b} greater-equal 32 less-equal 32")
    lines += [(f"ip large-community-filter basic BASTION-BH index 10 permit {comm}" if large
               else f"ip community-filter basic BASTION-BH index 10 permit {comm}"),
              "route-policy BASTION-IN permit node 10",
              " if-match ip-prefix BASTION-BH",
              " if-match large-community-filter BASTION-BH" if large else " if-match community-filter BASTION-BH",
              "route-policy BASTION-IN deny node 100",
              "route-policy BASTION-OUT deny node 10",
              f"bgp {asn}",
              f" peer {src} as-number {asn}",
              f" peer {src} description BASTION-RTBH",
              " ipv4-family unicast",
              f"  peer {src} enable",
              f"  peer {src} route-policy BASTION-IN import",
              f"  peer {src} route-policy BASTION-OUT export",
              "# a rota vem com NO_EXPORT: não é repassada para trânsitos/IX.",
              f"# libere TCP/179 vindo de {src} (ACL de CPU-defend / plano de controle), se houver.",
              "# conferir: display bgp peer | display bgp routing-table community " + comm]
    return "\n".join(lines) + "\n"
