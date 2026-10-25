"""Conformidade de configuração e inventário de firmware, a partir dos backups de configuração.

Regra = fabricante + "precisa ter" / "não pode ter" + regex (por linha, sem diferenciar maiúsculas) + peso +
como corrigir. Nota do equipamento = peso das regras cumpridas ÷ peso das regras que valem para ele.
Versão do firmware lida do cabeçalho/linhas da configuração (Huawei, Cisco IOS/XE/XR, Juniper, MikroTik, Datacom).
"""
import re
from typing import Dict, List, Optional

SEVERITY_WEIGHT = {"low": 1, "medium": 2, "high": 3}
VENDORS = {"any": "Todos", "huawei": "Huawei", "cisco": "Cisco", "juniper": "Juniper", "mikrotik": "MikroTik",
           "datacom": "Datacom", "zte": "ZTE", "linux": "Linux", "ubiquiti": "Ubiquiti", "other": "Outros"}

P = []


def _r(vendor, key, name, kind, pattern, severity, fix, why=""):
    P.append({"preset": f"{vendor}-{key}", "vendor": vendor, "name": name, "kind": kind, "pattern": pattern,
              "severity": severity, "fix": fix, "why": why})


# Huawei VRP
_r("huawei", "ntp", "NTP configurado", "must", r"^\s*ntp(-service)?\s+unicast-server\b", "medium", "ntp-service unicast-server <IP_NTP>", "horário certo nos logs e no syslog")
_r("huawei", "telnet", "Telnet desligado", "must_not", r"^\s*telnet\s+(ipv6\s+)?server\s+enable", "high", "undo telnet server enable", "senha trafega aberta")
_r("huawei", "vty-telnet", "VTY só com SSH", "must_not", r"^\s*protocol\s+inbound\s+(all|telnet)\b", "high", "user-interface vty 0 4\n protocol inbound ssh")
_r("huawei", "ssh", "SSH ativo", "must", r"^\s*stelnet\s+(ipv4\s+)?server\s+enable", "medium", "stelnet server enable")
_r("huawei", "snmp-public", "SNMP sem community padrão", "must_not", r"^\s*snmp-agent\s+community\s+(read|write)\s+(cipher\s+)?(public|private)\b", "high", "undo snmp-agent community public")
_r("huawei", "aaa", "AAA centralizado (TACACS/RADIUS)", "must", r"^\s*(hwtacacs-server\s+template|radius-server\s+template)\b", "low", "hwtacacs-server template <NOME> ...")
_r("huawei", "syslog", "Syslog enviado para servidor", "must", r"^\s*info-center\s+loghost\s+\S+", "low", "info-center loghost <IP_DO_BASTION>")
# Cisco IOS/XE
_r("cisco", "ntp", "NTP configurado", "must", r"^\s*ntp\s+server\s+\S+", "medium", "ntp server <IP_NTP>")
_r("cisco", "sshv2", "SSH versão 2", "must", r"^\s*ip\s+ssh\s+version\s+2\b", "high", "ip ssh version 2")
_r("cisco", "telnet", "VTY sem telnet", "must_not", r"^\s*transport\s+input\s+(.*\btelnet\b|all)", "high", "line vty 0 15\n transport input ssh")
_r("cisco", "snmp-public", "SNMP sem community padrão", "must_not", r"^\s*snmp-server\s+community\s+(public|private)\b", "high", "no snmp-server community public")
_r("cisco", "aaa", "AAA ativo", "must", r"^\s*aaa\s+new-model\b", "medium", "aaa new-model")
_r("cisco", "pwenc", "Senhas criptografadas", "must", r"^\s*service\s+password-encryption\b", "medium", "service password-encryption")
_r("cisco", "http", "Servidor HTTP desligado", "must_not", r"^\s*ip\s+http\s+server\b", "medium", "no ip http server")
_r("cisco", "syslog", "Syslog enviado para servidor", "must", r"^\s*logging\s+(host\s+)?\d", "low", "logging host <IP_DO_BASTION>")
# Juniper (display set)
_r("juniper", "ntp", "NTP configurado", "must", r"^set\s+system\s+ntp\s+server\s+", "medium", "set system ntp server <IP_NTP>")
_r("juniper", "telnet", "Telnet desligado", "must_not", r"^set\s+system\s+services\s+telnet\b", "high", "delete system services telnet")
_r("juniper", "root-ssh", "Root não loga por SSH", "must_not", r"^set\s+system\s+services\s+ssh\s+root-login\s+allow\b", "high", "set system services ssh root-login deny")
_r("juniper", "snmp-public", "SNMP sem community padrão", "must_not", r"^set\s+snmp\s+community\s+(public|private)\b", "high", "delete snmp community public")
_r("juniper", "aaa", "AAA centralizado (TACACS/RADIUS)", "must", r"^set\s+system\s+(tacplus-server|radius-server)\s+", "low", "set system tacplus-server <IP> secret <...>")
_r("juniper", "syslog", "Syslog enviado para servidor", "must", r"^set\s+system\s+syslog\s+host\s+\S+", "low", "set system syslog host <IP_DO_BASTION> any notice")
# MikroTik (/export)
_r("mikrotik", "telnet", "Telnet desligado", "must", r"^set\s+telnet\b[^\n]*disabled=yes", "high", "/ip service set telnet disabled=yes", "no RouterOS o telnet vem ligado")
_r("mikrotik", "ftp", "FTP desligado", "must", r"^set\s+ftp\b[^\n]*disabled=yes", "medium", "/ip service set ftp disabled=yes")
_r("mikrotik", "www", "Página web (www) desligada", "must", r"^set\s+www\b[^\n]*disabled=yes", "low", "/ip service set www disabled=yes")
_r("mikrotik", "ntp", "NTP/SNTP configurado", "must", r"^/system (ntp|sntp) client\b[\s\S]*?(enabled=yes|servers=|primary-ntp=)", "medium", "/system ntp client set enabled=yes servers=<IP_NTP>")
_r("mikrotik", "syslog", "Log remoto configurado", "must", r"^/system logging action\b[\s\S]*?target=remote", "low", "/system logging action add name=bastion target=remote remote=<IP_DO_BASTION>")
# Datacom DmOS / ZTE (formatos mais variados: o básico)
_r("datacom", "ntp", "NTP configurado", "must", r"^\s*(sntp|ntp)\s+(server|client)\b", "medium", "sntp server <IP_NTP>")
_r("datacom", "telnet", "Telnet desligado", "must_not", r"^\s*telnet-server\s+enabled\b|^\s*ip\s+telnet\s+server\b", "high", "no telnet-server")
_r("datacom", "snmp-public", "SNMP sem community padrão", "must_not", r"^\s*snmp(-server)?\s+community\s+(public|private)\b", "high", "no snmp community public")
_r("zte", "ntp", "NTP configurado", "must", r"^\s*(ntp|sntp)\s+(server|enable)\b", "medium", "ntp server <IP_NTP>")
_r("zte", "telnet", "Telnet desligado", "must_not", r"^\s*(ip\s+)?telnet\s+(server\s+)?enable\b", "high", "no telnet enable (conforme a versão)")
_r("zte", "snmp-public", "SNMP sem community padrão", "must_not", r"^\s*snmp-server\s+community\s+(public|private)\b", "high", "no snmp-server community public")
# Serviços que viram refletor/amplificador (notificações do CERT.br)
_CB = "CERT.br: serviço aberto para a internet vira refletor de DDoS"
_r("mikrotik", "dns-open", "DNS não atende a internet", "must_not", r"^/ip dns\s*\n(?:[^/\n][^\n]*\n)*?[^\n]*allow-remote-requests=yes", "high",
   "/ip dns set allow-remote-requests=no  (ou bloquear UDP/TCP 53 de fora no /ip firewall filter, chain input)", _CB)
_r("mikrotik", "ntp-server", "Servidor NTP só para a rede interna", "must_not", r"^/system ntp server\s*\n(?:[^/\n][^\n]*\n)*?[^\n]*enabled=yes", "medium",
   "/system ntp server set enabled=no  (ou bloquear UDP 123 de fora na chain input)", _CB)
_r("mikrotik", "upnp", "UPnP desligado", "must_not", r"^/ip upnp\s*\n(?:[^/\n][^\n]*\n)*?[^\n]*enabled=yes", "medium", "/ip upnp set enabled=no", _CB + " (SSDP)")
_r("mikrotik", "socks", "SOCKS desligado", "must_not", r"^/ip socks\s*\n(?:[^/\n][^\n]*\n)*?[^\n]*enabled=yes", "high", "/ip socks set enabled=no", "proxy aberto é usado para abuso")
_r("cisco", "dns-open", "Servidor DNS desligado", "must_not", r"^\s*ip\s+dns\s+server\b", "high", "no ip dns server", _CB)
_r("cisco", "ntp-master", "Não é servidor NTP para a internet", "must_not", r"^\s*ntp\s+master\b", "medium", "no ntp master  (ou ntp access-group peer <ACL>)", _CB)
_r("huawei", "dns-proxy", "DNS proxy desligado", "must_not", r"^\s*dns\s+proxy\s+enable\b", "medium", "undo dns proxy enable", _CB)
_r("juniper", "dns-open", "Serviço DNS desligado", "must_not", r"^set\s+system\s+services\s+dns\b", "medium", "delete system services dns", _CB)
PRESETS = P


def compile_rule(r: dict):
    return re.compile(r["pattern"], re.I | re.M)


def check(content: str, rules: List[dict]) -> List[dict]:
    out = []
    for r in rules:
        try:
            rx = compile_rule(r)
        except re.error as e:
            out.append({"rule_id": r.get("id"), "ok": None, "error": f"regex inválida: {e}"})
            continue
        m = rx.search(content or "")
        ok = bool(m) if r["kind"] == "must" else not m
        ev = ""
        if m:
            line_start = (content or "").rfind("\n", 0, m.start()) + 1
            line_end = (content or "").find("\n", m.end())
            ev = (content or "")[line_start:line_end if line_end != -1 else None].strip()[:200]
        out.append({"rule_id": r.get("id"), "ok": ok, "evidence": ev})
    return out


def score(results: List[dict], rules_by_id: Dict[str, dict]) -> Optional[float]:
    tot = got = 0
    for x in results:
        if x.get("ok") is None:
            continue
        w = SEVERITY_WEIGHT.get((rules_by_id.get(x["rule_id"]) or {}).get("severity", "medium"), 2)
        tot += w
        got += w if x["ok"] else 0
    return round(100.0 * got / tot, 1) if tot else None


_FW = [
    ("huawei", r"!\s*Software\s+Version\s+(V\d+R\d+C\d+\S*)"),
    ("huawei", r"VRP\s*\(R\)\s*software,\s*Version\s+([\d.]+\s*\(\S+\))"),
    ("cisco", r"!!\s*IOS\s+XR\s+Configuration\s+([\d.]+\S*)"),
    ("cisco", r"^\s*version\s+(\d+\.\d+\S*)"),
    ("juniper", r"^set\s+version\s+(\S+)"),
    ("juniper", r"^\s*version\s+([\dA-Z.\-]+);"),
    ("mikrotik", r"by\s+RouterOS\s+(\d+\.\d+(?:\.\d+)?\S*)"),
    ("datacom", r"(?:DmOS|Software\s+version)\s*[:\s]\s*([\d.]+\S*)"),
    ("zte", r"(?:Software\s+Version|software\s+version)\s*[:\s]\s*(V\S+)"),
    ("any", r"(?i)\bversion\s*[:=]\s*([\w.\-()]+)"),
]
_MODEL = [r"#\s*model\s*=\s*(\S+)", r"!\s*Product\s+(?:name|Version)\s*[:\s]\s*(\S+)", r"^set\s+system\s+host-name\s+\S+\s*$"]


def firmware(device_type: str, content: str) -> dict:
    t = content or ""
    head = t[:20000]
    ver = None
    for vendor, rx in _FW:
        if vendor not in (device_type, "any"):
            continue
        m = re.search(rx, head, re.M)
        if m:
            ver = m.group(1).strip()
            break
    model = None
    m = re.search(_MODEL[0], head) or re.search(_MODEL[1], head)
    if m:
        model = m.group(1)
    return {"version": ver, "model": model}


def version_cmp(a: str, b: str) -> int:
    """-1 / 0 / 1 comparando por partes numéricas (V800R021C10 < V800R022C00, 7.14.3 > 7.9)."""
    ka = [int(x) for x in re.findall(r"\d+", a or "")]
    kb = [int(x) for x in re.findall(r"\d+", b or "")]
    return (ka > kb) - (ka < kb)


def clean_rule(b: dict) -> dict:
    r = {"name": str(b.get("name") or "").strip()[:100], "vendor": b.get("vendor") if b.get("vendor") in VENDORS else "any",
         "kind": "must_not" if b.get("kind") == "must_not" else "must", "pattern": str(b.get("pattern") or "").strip()[:2000],
         "severity": b.get("severity") if b.get("severity") in SEVERITY_WEIGHT else "medium",
         "fix": str(b.get("fix") or "")[:1000], "why": str(b.get("why") or "")[:300], "enabled": bool(b.get("enabled", True))}
    if not r["name"] or not r["pattern"]:
        raise ValueError("Informe nome e expressão")
    try:
        compile_rule(r)
    except re.error as e:
        raise ValueError(f"Regex inválida: {e}")
    return r
