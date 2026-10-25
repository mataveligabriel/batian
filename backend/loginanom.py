"""Login anômalo: compara cada login com o histórico do próprio usuário.

Sinais: IP novo, operadora (ASN) nova, país novo, troca de país em pouco tempo ("viagem impossível"), horário fora do
costume, navegador/sistema novo e acerto depois de várias falhas (força bruta que deu certo). O país vem do ASN dono do
IP (base iptoasn), então é o país de registro da operadora — bom o bastante para pegar acesso do exterior.
"""
import ipaddress
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional

SEV_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
DEFAULTS = {"enabled": True, "notify_min": "medium", "ask_user": True, "allowed_countries": [], "trusted_nets": [],
            "geofence_admins": True, "min_history": 5, "travel_hours": 3, "fail_burst": 5, "timezone": "America/Sao_Paulo"}
LABEL = {"new_ip": "IP novo", "new_asn": "Operadora nova", "new_country": "País novo", "impossible_travel": "Viagem impossível",
         "odd_hour": "Horário fora do costume", "new_device": "Navegador/sistema novo", "brute_success": "Acertou depois de várias falhas",
         "geo_block": "Fora dos países permitidos", "ip_reputation": "IP em lista de ameaças"}


def is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
    except ValueError:
        return True


def in_nets(ip: str, nets: List[str]) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in nets or []:
        try:
            if a in ipaddress.ip_network(n, strict=False):
                return True
        except ValueError:
            continue
    return False


def clean_nets(v) -> List[str]:
    out = []
    for n in v or []:
        n = str(n).strip()
        if not n:
            continue
        try:
            out.append(str(ipaddress.ip_network(n, strict=False)))
        except ValueError:
            raise ValueError(f"Rede inválida: {n}")
    return out[:100]


def clean_cc(v) -> List[str]:
    out = []
    for c in v or []:
        c = str(c).strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", c):
            raise ValueError(f"País inválido: {c} (use o código de 2 letras, ex.: BR)")
        out.append(c)
    return sorted(set(out))


def device_of(ua: str) -> str:
    u = ua or ""
    os_ = ("Android" if "Android" in u else "iOS" if re.search(r"iPhone|iPad|iOS", u) else "Windows" if "Windows" in u else
           "macOS" if "Mac OS X" in u or "Macintosh" in u else "Linux" if "Linux" in u else "?")
    br = ("Edge" if "Edg/" in u else "Opera" if "OPR/" in u else "Chrome" if "Chrome/" in u else "Firefox" if "Firefox/" in u else
          "Safari" if "Safari/" in u else "app" if not u else "?")
    return f"{br} / {os_}"


def _dt(s) -> Optional[datetime]:
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def analyze(cur: dict, history: List[dict], fails_before: int, cfg: dict, tz=None) -> List[dict]:
    """cur/history: {ip, cc, asn, asn_name, ua, at}. history = logins com sucesso ANTERIORES (mais novo primeiro).
    Devolve achados [{kind, severity, text}]."""
    out = []
    n = len(history)
    enough = n >= int(cfg.get("min_history") or 5)
    if fails_before >= int(cfg.get("fail_burst") or 5):
        out.append({"kind": "brute_success", "severity": "high",
                    "text": f"{fails_before} senhas/códigos errados nos 30 min antes de entrar"})
    if not history:
        return out
    ips = {h["ip"] for h in history}
    asns = {h.get("asn") for h in history if h.get("asn")}
    ccs = {h.get("cc") for h in history if h.get("cc")}
    if cur.get("cc") and ccs and cur["cc"] not in ccs:
        out.append({"kind": "new_country", "severity": "high", "text": f"Primeiro login do país {cur['cc']} (antes: {', '.join(sorted(ccs))})"})
    elif cur.get("asn") and asns and cur["asn"] not in asns and enough:
        out.append({"kind": "new_asn", "severity": "medium", "text": f"Operadora nova: AS{cur['asn']} {cur.get('asn_name', '')}".strip()})
    elif cur["ip"] not in ips and enough and not is_private(cur["ip"]):
        out.append({"kind": "new_ip", "severity": "low", "text": f"IP novo {cur['ip']}"})
    last = history[0]
    t0, t1 = _dt(last.get("at")), _dt(cur.get("at"))
    if (cur.get("cc") and last.get("cc") and cur["cc"] != last["cc"] and t0 and t1
            and (t1 - t0) < timedelta(hours=float(cfg.get("travel_hours") or 3))):
        mins = int((t1 - t0).total_seconds() // 60)
        out.append({"kind": "impossible_travel", "severity": "high",
                    "text": f"Entrou de {last['cc']} e {mins} min depois de {cur['cc']}"})
    if n >= 20 and t1:
        hours = Counter((_dt(h["at"]).astimezone(tz) if tz else _dt(h["at"])).hour for h in history if _dt(h.get("at")))
        h = (t1.astimezone(tz) if tz else t1).hour
        near = hours[h] + hours[(h - 1) % 24] + hours[(h + 1) % 24]
        if near / max(1, sum(hours.values())) < 0.02:
            out.append({"kind": "odd_hour", "severity": "medium", "text": f"Login às {h:02d}h — fora do horário de costume desse usuário"})
    devs = {device_of(x.get("ua", "")) for x in history}
    if enough and device_of(cur.get("ua", "")) not in devs:
        out.append({"kind": "new_device", "severity": "low", "text": f"Navegador/sistema novo: {device_of(cur.get('ua', ''))}"})
    return out


def overall(findings: List[dict]) -> str:
    if not findings:
        return "info"
    sev = max(findings, key=lambda f: SEV_RANK[f["severity"]])["severity"]
    # dois sinais médios juntos (ex.: operadora nova + horário estranho) sobem para alto
    if sev == "medium" and sum(1 for f in findings if SEV_RANK[f["severity"]] >= 2) >= 2:
        return "high"
    if sev == "high" and sum(1 for f in findings if SEV_RANK[f["severity"]] >= 3) >= 2:
        return "critical"
    return sev


def geofence_blocks(cc: str, ip: str, cfg: dict, is_admin: bool) -> bool:
    allowed = cfg.get("allowed_countries") or []
    if not allowed or not cc or is_private(ip) or in_nets(ip, cfg.get("trusted_nets") or []):
        return False
    if is_admin and not cfg.get("geofence_admins", True):
        return False
    return cc not in allowed
