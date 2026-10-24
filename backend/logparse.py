"""Syslog e traps SNMP: leitura das mensagens e motor de regras (sem rede nem banco — testável).

- parse_syslog: RFC 3164 (o "BSD" que quase todo roteador manda) e RFC 5424, com as variações de Cisco (número de
  sequência, "*" no horário), Huawei (ano no horário, %%01MÓDULO/…), Juniper, MikroTik (tópicos "interface,info").
- parse_trap: SNMP v1 e v2c (TRAP e INFORM). v3 não é decodificado (conta como "não suportado").
- RuleEngine: regra = regex (+ regex de normalização opcional), N ocorrências em M segundos, agrupada por
  equipamento + o que a regex capturou (o vizinho BGP, a interface, a PON). Devolve ações open/repeat/ok que o
  coletor transforma em incidentes.
"""
import ipaddress
import re
import time
from collections import deque
from typing import Optional

import snmp_service as sn

DEFAULTS = {
    "enabled": True,
    "syslog_port": 514,
    "trap_port": 162,
    "retention_days": 30,
    "rate_per_source": 200,        # mensagens/s por origem (o excesso é descartado e contado)
    "store_max_severity": 7,       # grava até esta severidade do syslog (7 = tudo, 6 = sem debug)
    "store_unknown": True,         # grava também o que vem de IP que não é equipamento cadastrado
    "trap_community": "",          # vazio = aceita qualquer community
}
SEV_NAMES = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]
FACILITIES = ["kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news", "uucp", "cron", "authpriv", "ftp",
              "ntp", "audit", "alert", "clock", "local0", "local1", "local2", "local3", "local4", "local5", "local6",
              "local7"]
MAX_MSG = 2000

# ---------------------------------------------------------------- syslog
_PRI = re.compile(rb"^\s*<(\d{1,3})>")
_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_TS = re.compile(
    r"^[*.]?(?:"
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"          # ISO
    r"|" + _MON + r"\s+\d{1,2}(?:\s+\d{4})?\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?"              # Oct 24 [2026] 10:00:00[.123]
    r"|\d{4}\s+" + _MON + r"\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?"                  # 2026 Oct 24 10:00:00
    r")(?:\s+(?:[A-Z]{2,5}|[+-]\d{2}:?\d{2}|DST))?:?\s+")
_HOST = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]{0,62})\s+")
_PROG = re.compile(r"^([A-Za-z0-9_./\-]{1,48})(?:\[(\d+)\])?:\s")
_SEQ = re.compile(r"^\d{1,10}:\s+")


def _txt(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def parse_syslog(data: bytes) -> dict:
    """-> {facility, severity, host_name, program, msg}. Sem <PRI> válido: severidade notice, tudo vira msg."""
    m = _PRI.match(data)
    if m and int(m.group(1)) <= 191:
        pri = int(m.group(1))
        rest = _txt(data[m.end():])
    else:
        pri = 13
        rest = _txt(data)
    rest = rest.replace("\x00", "").strip()
    out = {"facility": pri >> 3, "severity": pri & 7, "host_name": "", "program": "", "msg": ""}
    m5 = re.match(r"^1 (\S+) (\S+) (\S+) (\S+) (\S+) (-|(?:\[.*?\])+)\s?(.*)$", rest, re.S)
    if m5:                                                  # RFC 5424
        out["host_name"] = "" if m5.group(2) == "-" else m5.group(2)[:63]
        out["program"] = "" if m5.group(3) == "-" else m5.group(3)[:48]
        msg = m5.group(7).lstrip("﻿")
        if m5.group(6) != "-":
            msg = (msg + " " + m5.group(6)).strip()
        out["msg"] = msg[:MAX_MSG]
        return out
    s = _SEQ.sub("", rest, count=1)                        # Cisco: "123: "
    # Cisco com "hostname: " antes do horário ("router1: *Oct 24 …")
    mh = re.match(r"^([A-Za-z0-9][\w.\-]{0,62}):\s+(?=[*.]?(?:" + _MON + r"|\d{4}-))", s)
    if mh:
        out["host_name"] = mh.group(1)
        s = s[mh.end():]
        s = _SEQ.sub("", s, count=1)
    mt = _TS.match(s)
    if mt:
        s = s[mt.end():]
        if not out["host_name"]:
            h = _HOST.match(s)          # "rpd[1]:", "BGP:", "%SYS…", "interface,info" não casam
            if h and s[h.end():].strip():
                out["host_name"] = h.group(1)
                s = s[h.end():]
    s = _SEQ.sub("", s, count=1)
    p = _PROG.match(s)
    if p:
        out["program"] = p.group(1)
    elif s.startswith("%"):
        mm = re.match(r"^%+(?:\d\d)?([A-Za-z0-9_]+)[/-]", s)
        if mm:
            out["program"] = mm.group(1)[:48]
    out["msg"] = s.strip()[:MAX_MSG]
    return out


# ---------------------------------------------------------------- traps SNMP
TRAP_NAMES = {
    "1.3.6.1.6.3.1.1.5.1": "coldStart", "1.3.6.1.6.3.1.1.5.2": "warmStart", "1.3.6.1.6.3.1.1.5.3": "linkDown",
    "1.3.6.1.6.3.1.1.5.4": "linkUp", "1.3.6.1.6.3.1.1.5.5": "authenticationFailure",
    "1.3.6.1.6.3.1.1.5.6": "egpNeighborLoss",
    "1.3.6.1.2.1.15.7.1": "bgpEstablished", "1.3.6.1.2.1.15.7.2": "bgpBackwardTransition",
    "1.3.6.1.2.1.15.0.1": "bgpEstablishedNotification", "1.3.6.1.2.1.15.0.2": "bgpBackwardTransNotification",
    "1.3.6.1.4.1.9.9.187.0.1": "cbgpFsmStateChange", "1.3.6.1.4.1.9.9.187.0.2": "cbgpBackwardTransition",
    "1.3.6.1.2.1.14.16.2.2": "ospfNbrStateChange", "1.3.6.1.2.1.14.16.2.3": "ospfVirtNbrStateChange",
    "1.3.6.1.4.1.9.9.43.2.0.1": "ciscoConfigManEvent", "1.3.6.1.2.1.47.2.0.1": "entConfigChange",
    "1.3.6.1.4.1.9.9.41.2.0.1": "clogMessageGenerated",
}
TRAP_SEVERITY = {"linkDown": 3, "coldStart": 2, "warmStart": 4, "bgpBackwardTransition": 2,
                 "bgpBackwardTransNotification": 2, "cbgpBackwardTransition": 2, "authenticationFailure": 4,
                 "ospfNbrStateChange": 4, "linkUp": 5, "bgpEstablished": 5, "bgpEstablishedNotification": 5}
# prefixo da coluna -> nome (o resto do OID é o índice)
_VB_NAMES = {
    "1.3.6.1.2.1.2.2.1.1": "ifIndex", "1.3.6.1.2.1.2.2.1.2": "ifDescr", "1.3.6.1.2.1.2.2.1.7": "ifAdminStatus",
    "1.3.6.1.2.1.2.2.1.8": "ifOperStatus", "1.3.6.1.2.1.31.1.1.1.1": "ifName", "1.3.6.1.2.1.31.1.1.1.18": "ifAlias",
    "1.3.6.1.2.1.15.3.1.2": "bgpPeerState", "1.3.6.1.2.1.15.3.1.7": "bgpPeerRemoteAddr",
    "1.3.6.1.2.1.15.3.1.9": "bgpPeerRemoteAs", "1.3.6.1.2.1.15.3.1.14": "bgpPeerLastError",
    "1.3.6.1.4.1.9.9.187.1.2.1.1.7": "cbgpPeerLastErrorTxt", "1.3.6.1.4.1.9.9.187.1.2.1.1.8": "cbgpPeerPrevState",
    "1.3.6.1.2.1.14.10.1.1": "ospfNbrIpAddr", "1.3.6.1.2.1.14.10.1.3": "ospfNbrRtrId",
    "1.3.6.1.2.1.14.10.1.6": "ospfNbrState", "1.3.6.1.2.1.14.1.1": "ospfRouterId",
    "1.3.6.1.4.1.9.9.43.1.1.6.1.3": "ccmHistoryEventCommandSource", "1.3.6.1.4.1.9.9.43.1.1.6.1.5": "ccmHistoryEventConfigDestination",
    "1.3.6.1.4.1.9.9.41.1.2.3.1.2": "clogHistFacility", "1.3.6.1.4.1.9.9.41.1.2.3.1.4": "clogHistMsgName",
    "1.3.6.1.4.1.9.9.41.1.2.3.1.5": "clogHistMsgText",
}
_VB_SCALAR = {"1.3.6.1.2.1.1.3.0": "sysUpTime", "1.3.6.1.6.3.1.1.4.1.0": "snmpTrapOID",
              "1.3.6.1.6.3.18.1.3.0": "snmpTrapAddress", "1.3.6.1.6.3.18.1.4.0": "snmpTrapCommunity",
              "1.3.6.1.6.3.1.1.4.3.0": "snmpTrapEnterprise", "1.3.6.1.2.1.1.5.0": "sysName"}
_ENUMS = {
    "ifOperStatus": {1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant", 6: "notPresent", 7: "lowerLayerDown"},
    "ifAdminStatus": {1: "up", 2: "down", 3: "testing"},
    "bgpPeerState": {1: "idle", 2: "connect", 3: "active", 4: "opensent", 5: "openconfirm", 6: "established"},
    "cbgpPeerPrevState": {1: "idle", 2: "connect", 3: "active", 4: "opensent", 5: "openconfirm", 6: "established"},
    "ospfNbrState": {1: "down", 2: "attempt", 3: "init", 4: "twoWay", 5: "exchangeStart", 6: "exchange",
                     7: "loading", 8: "full"},
}
_SKIP_IN_MSG = {"sysUpTime", "snmpTrapOID", "snmpTrapEnterprise", "snmpTrapCommunity"}


class TrapError(Exception):
    pass


def _vb_name(oid: str):
    """-> (nome, índice) do varbind."""
    if oid in _VB_SCALAR:
        return _VB_SCALAR[oid], ""
    parts = oid.split(".")
    for n in range(len(parts) - 1, 5, -1):
        pre = ".".join(parts[:n])
        if pre in _VB_NAMES:
            return _VB_NAMES[pre], ".".join(parts[n:])
    return "", ""


def _show(v) -> str:
    if isinstance(v, bytes):
        if v and all(32 <= c < 127 for c in v):
            return v.decode()
        return v.hex(" ") if v else ""
    if v is None:
        return ""
    if isinstance(v, str) and v and not v.isprintable():
        return v.encode("latin-1", "replace").hex(" ")
    return str(v)


def _trap_name(oid: str) -> str:
    if oid in TRAP_NAMES:
        return TRAP_NAMES[oid]
    return oid


def parse_trap(data: bytes) -> dict:
    """-> {version, community, trap_oid, trap_name, agent, varbinds:[[oid,nome,valor]], msg, severity,
    inform_reply (bytes|None)}."""
    try:
        tag, msg, _ = sn._dec_tlv(data, 0)
        if tag != 0x30:
            raise TrapError("não é SNMP")
        i = 0
        _, ver_b, i = sn._dec_tlv(msg, i)
        ver = int.from_bytes(ver_b, "big") if ver_b else 0
        if ver == 3:
            raise TrapError("v3")
        _, comm, i = sn._dec_tlv(msg, i)
        pdu_tag, pdu, _ = sn._dec_tlv(msg, i)
        out = {"version": "v1" if ver == 0 else "v2c", "community": _txt(comm), "agent": "", "trap_oid": "",
               "varbinds": [], "inform_reply": None}
        j = 0
        if pdu_tag == 0xA4:                                 # v1
            _, ent, j = sn._dec_tlv(pdu, j)
            enterprise = sn._dec_oid(ent) if ent else ""
            _, addr, j = sn._dec_tlv(pdu, j)
            out["agent"] = ".".join(str(x) for x in addr) if len(addr) == 4 else ""
            _, gen, j = sn._dec_tlv(pdu, j)
            _, spec, j = sn._dec_tlv(pdu, j)
            _, _ts, j = sn._dec_tlv(pdu, j)
            g = int.from_bytes(gen, "big") if gen else 0
            s = int.from_bytes(spec, "big") if spec else 0
            out["trap_oid"] = f"1.3.6.1.6.3.1.1.5.{g + 1}" if g < 6 else f"{enterprise}.0.{s}"
            _, vbl, _ = sn._dec_tlv(pdu, j)
            rid_raw = None
        elif pdu_tag in (0xA7, 0xA6):                       # v2c TRAP / INFORM
            _, rid_raw, j = sn._dec_tlv(pdu, j)
            _, _es, j = sn._dec_tlv(pdu, j)
            _, _ei, j = sn._dec_tlv(pdu, j)
            _, vbl, _ = sn._dec_tlv(pdu, j)
        else:
            raise TrapError(f"PDU 0x{pdu_tag:02x} não é trap")
        k = 0
        while k < len(vbl):
            _, vb, k = sn._dec_tlv(vbl, k)
            _, oid_b, m = sn._dec_tlv(vb, 0)
            vtag, vval, _ = sn._dec_tlv(vb, m)
            oid = sn._dec_oid(oid_b)
            val = sn._dec_value(vtag, vval)
            if oid == "1.3.6.1.6.3.1.1.4.1.0":
                out["trap_oid"] = str(val)
            elif oid == "1.3.6.1.6.3.18.1.3.0" and isinstance(val, str):
                out["agent"] = out["agent"] or val
            out["varbinds"].append((oid, val))
            if len(out["varbinds"]) > 60:
                break
        if pdu_tag == 0xA6:
            pdu_r = sn._tlv(0xA2, sn._tlv(0x02, rid_raw) + sn._enc_int(0) + sn._enc_int(0) + sn._tlv(0x30, vbl))
            out["inform_reply"] = sn._tlv(0x30, sn._enc_int(ver) + sn._tlv(0x04, comm) + pdu_r)
    except TrapError:
        raise
    except Exception as e:  # pacote cortado/estranho
        raise TrapError(f"trap inválido ({e.__class__.__name__})")
    name = _trap_name(out["trap_oid"])
    out["trap_name"] = name
    parts, vbs, peer = [name], [], ""
    for oid, val in out["varbinds"]:
        nm, idx = _vb_name(oid)
        shown = _show(val)
        if nm in _ENUMS and isinstance(val, int):
            shown = f"{_ENUMS[nm].get(val, val)}({val})"
        vbs.append([oid, nm, shown[:300]])
        if nm in _SKIP_IN_MSG:
            continue
        if nm.startswith("bgpPeer") and idx and not peer:
            peer = idx if _is_ip(idx) else ""
        if nm.startswith("ospfNbr") and idx and not peer:
            cand = ".".join(idx.split(".")[:4])
            peer = cand if _is_ip(cand) else ""
        parts.append(f"{nm or oid}={shown[:200]}")
    if peer:
        parts.insert(1, f"peer={peer}")
    out["varbinds"] = vbs
    out["msg"] = " ".join(parts)[:MAX_MSG]
    out["severity"] = TRAP_SEVERITY.get(name, 5)
    return out


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- regras
_IP = r"([0-9A-Fa-f.:]{3,}[0-9A-Fa-f])"
PRESETS = [
    {"preset": "bgp", "name": "Sessão BGP caiu", "severity": "high", "threshold": 1, "window_s": 60,
     "auto_resolve_min": 0,
     "pattern": r"%BGP-\d-ADJCHANGE: neighbor " + _IP + r"(?: vpn vrf \S+)? Down"
                r"|BGP_PEER_STATE_CHANGED.*?peer " + _IP + r".*?changed state from Established to"
                r"|BackwardTransition.*?(?:PeerRemoteAddr|PeerIpAddr|PeerAddr)[=:\s]+\(?" + _IP +
                r"|(?:bgpBackwardTransition|bgpBackwardTransNotification|cbgpBackwardTransition) peer=" + _IP,
     "clear_pattern": r"%BGP-\d-ADJCHANGE: neighbor " + _IP + r"(?: vpn vrf \S+)? Up"
                      r"|BGP_PEER_STATE_CHANGED.*?peer " + _IP + r".*?to Established"
                      r"|(?:hwBgpPeerEstablished|BGP_ESTABLISHED|bgpEstablished|bgpEstablishedNotification).*?(?:PeerRemoteAddr|PeerIpAddr|peer)[=:\s]+\(?" + _IP,
     "title": "BGP caiu: vizinho {group} — {device}"},
    {"preset": "ospf", "name": "Vizinho OSPF caiu", "severity": "high", "threshold": 1, "window_s": 60,
     "auto_resolve_min": 0,
     "pattern": r"%OSPF(?:V3)?-\d-ADJCHG:.*?Nbr (\S+) on \S+ from FULL to DOWN"
                r"|RPD_OSPF_NBRDOWN: OSPF neighbor " + _IP +
                r"|OSPF/\d+/NBR_DOWN\S*.*?(?:NeighborAddress|NbrIpAddress|NeighborRouterId|NbrRtrId)=" + _IP +
                r"|ospfNbrStateChange peer=" + _IP + r".*?ospfNbrState=down",
     "clear_pattern": r"%OSPF(?:V3)?-\d-ADJCHG:.*?Nbr (\S+) on \S+ from LOADING to FULL"
                      r"|RPD_OSPF_NBRUP: OSPF neighbor " + _IP +
                      r"|ospfNbrStateChange peer=" + _IP + r".*?ospfNbrState=full",
     "title": "OSPF caiu: vizinho {group} — {device}"},
    {"preset": "flap", "name": "Interface oscilando (flapping)", "severity": "medium", "threshold": 4,
     "window_s": 600, "auto_resolve_min": 15,
     "pattern": r"Interface ([^,\s]+), changed state to down"
                r"|interface ([\w/.:\-]+) (?:has entered the DOWN state|turned into DOWN state|is DOWN)"
                r"|ifOperStatus down\(2\), ifName ([\w/.:\-]+)"
                r"|linkDown\b.*?(?:ifName|ifDescr)=([^\s,]+)"
                r"|(?:^|\s)([\w.\-]+) link down\b",
     "clear_pattern": "",
     "title": "Interface oscilando: {group} — {device} ({count}x)"},
    {"preset": "los", "name": "LOS em massa na PON", "severity": "critical", "threshold": 8, "window_s": 120,
     "auto_resolve_min": 30,
     "pattern": r"(?i)(?:\bLOS\b|LOSi|dying[ _-]?gasp|ONU.{0,40}(?:offline|down)).*?"
                r"(?:gpon[-_](?:olt|onu)_|xgpon[-_](?:olt|onu)_|epon[-_](?:olt|onu)_|F/S/P[ :=]+|FrameID: ?)(\d+/\d+/\d+)"
                r"|(?i:\bLOS\b|LOSi|LOFi|dying[ _-]?gasp|ONT.{0,40}(?:offline|down)).*?"
                r"FrameID:\s*(?P<f>\d+),\s*SlotID:\s*(?P<s>\d+),\s*PortID:\s*(?P<p>\d+)",
     "clear_pattern": "",
     "title": "LOS em massa na PON {group} — {device} ({count} alarmes) — possível fibra rompida"},
    {"preset": "config", "name": "Configuração alterada", "severity": "info", "threshold": 1, "window_s": 60,
     "auto_resolve_min": 60,
     "pattern": r"%SYS-5-CONFIG_I|%PARSER-5-CFGLOG_LOGGEDCMD|UI_COMMIT(?:_COMPLETED)?:"
                r"|CFG(?:MAN)?/\d+/\S*(?:CHANGE|CHG)|hwCfgChgNotify|CONFIG_CHANGE"
                r"|\b(?:changed|added|removed) by \S+|ciscoConfigManEvent|entConfigChange",
     "clear_pattern": "",
     "title": "Configuração alterada — {device}"},
    {"preset": "auth", "name": "Falhas de login repetidas", "severity": "medium", "threshold": 10, "window_s": 300,
     "auto_resolve_min": 30,
     "pattern": r"%SEC_LOGIN-\d-LOGIN_FAILED|SSHD_LOGIN_FAILED|LOGIN_FAILED|authentication failure|"
                r"login failure for user|Failed password for|authenticationFailure|SSH/\d+/SSH_FAIL|LOGIN_FAIL",
     "clear_pattern": "",
     "title": "Falhas de login repetidas — {device} ({count}x)"},
    {"preset": "reboot", "name": "Equipamento reiniciou", "severity": "high", "threshold": 1, "window_s": 60,
     "auto_resolve_min": 60,
     "pattern": r"\bcoldStart\b|\bwarmStart\b|%SYS-5-RESTART|%SYS-5-RELOAD|SYSTEM_RESTARTED|"
                r"system,info,critical router rebooted|DEV/\d+/SYSTEM_REBOOT|SYSTEM/\d+/SYS_RESTART",
     "clear_pattern": "",
     "title": "Equipamento reiniciou — {device}"},
]
RULE_FIELDS = ("name", "enabled", "pattern", "clear_pattern", "kinds", "device_ids", "max_severity", "threshold",
               "window_s", "severity", "auto_resolve_min", "title", "preset")


def compile_rx(p: str):
    if not p:
        return None
    if len(p) > 2000:
        raise ValueError("regex grande demais")
    return re.compile(p, re.I)


LOS_LEGACY = (r"(?i)(?:\bLOS\b|LOSi|dying[ _-]?gasp|ONU.{0,40}(?:offline|down)).*?"
              r"(?:gpon[-_](?:olt|onu)_|xgpon[-_](?:olt|onu)_|epon[-_](?:olt|onu)_|F/S/P[ :=]+|FrameID: ?)(\d+/\d+/\d+)")


def group_of(m) -> str:
    if not m:
        return ""
    gd = m.groupdict()
    if gd.get("f") is not None and gd.get("s") is not None and gd.get("p") is not None:
        return f"{gd['f']}/{gd['s']}/{gd['p']}"          # Huawei: FrameID: 0, SlotID: 1, PortID: 3
    for g in m.groups():
        if g:
            return g.strip()[:80]
    return ""


def clean_rule(body: dict) -> dict:
    r = {k: body.get(k) for k in RULE_FIELDS if k in body}
    r["name"] = str(r.get("name") or "").strip()[:80] or "Regra"
    r["pattern"] = str(r.get("pattern") or "").strip()
    r["clear_pattern"] = str(r.get("clear_pattern") or "").strip()
    if not r["pattern"]:
        raise ValueError("Informe a expressão (regex) da regra")
    for k in ("pattern", "clear_pattern"):
        try:
            compile_rx(r[k])
        except re.error as e:
            raise ValueError(f"Regex inválida ({'normalização' if k == 'clear_pattern' else 'disparo'}): {e}")
    r["enabled"] = bool(r.get("enabled", True))
    kinds = r.get("kinds") or "any"
    r["kinds"] = kinds if kinds in ("any", "syslog", "trap") else "any"
    r["device_ids"] = [str(x) for x in (r.get("device_ids") or [])][:500]
    r["max_severity"] = max(0, min(7, int(r.get("max_severity", 7) if r.get("max_severity") is not None else 7)))
    r["threshold"] = max(1, min(100000, int(r.get("threshold") or 1)))
    r["window_s"] = max(10, min(86400, int(r.get("window_s") or 60)))
    r["auto_resolve_min"] = max(0, min(10080, int(r.get("auto_resolve_min") or 0)))
    sev = r.get("severity") or "medium"
    r["severity"] = sev if sev in ("info", "low", "medium", "high", "critical") else "medium"
    r["title"] = str(r.get("title") or "").strip()[:200]
    r["preset"] = str(r.get("preset") or "")[:20]
    return r


def test_rule(pattern: str, clear_pattern: str, text: str) -> list:
    rx, cx = compile_rx(pattern), compile_rx(clear_pattern)
    out = []
    for line in (text or "").splitlines()[:200]:
        if not line.strip():
            continue
        # aceita uma linha crua com <PRI>/horário/host: testa o que o coletor guardaria
        msg = parse_syslog(line.encode())["msg"] if line.lstrip().startswith("<") else line
        m = rx.search(msg) if rx else None
        c = cx.search(msg) if cx and not m else None
        out.append({"line": line[:500], "msg": msg[:500], "match": "fire" if m else "clear" if c else "",
                    "group": group_of(m or c)})
    return out


class _Fmt(dict):
    def __missing__(self, k):
        return "{" + k + "}"


class RuleEngine:
    """Estado por chave (regra + equipamento + grupo). feed() a cada mensagem, sweep() de minuto em minuto."""
    REPEAT_EVERY = 60

    def __init__(self):
        self.rules = []
        self.state: dict = {}
        self.hits: dict = {}         # rule id -> contagem desde o último flush

    def load(self, rules: list):
        out = []
        for r in rules:
            if not r.get("enabled", True):
                continue
            try:
                out.append({**r, "_rx": compile_rx(r["pattern"]), "_cx": compile_rx(r.get("clear_pattern") or ""),
                            "_dev": set(r.get("device_ids") or [])})
            except (re.error, ValueError, KeyError):
                continue
        self.rules = out
        ids = {r["id"] for r in out}
        self.state = {k: v for k, v in self.state.items() if v["rule"] in ids}

    def restore(self, key: str, rule_id: str, last: float):
        self.state[key] = {"rule": rule_id, "hits": deque(), "open": True, "last": last, "push": last,
                           "samples": deque(maxlen=5), "group": "", "device": "", "host": "", "device_id": None,
                           "owner_ids": [], "kind": "syslog"}

    def _title(self, r, st) -> str:
        tpl = r.get("title") or ("{rule}: {group} — {device}" if st["group"] else "{rule} — {device}")
        try:
            return tpl.format_map(_Fmt(rule=r["name"], group=st["group"] or "-", device=st["device"] or st["host"],
                                       host=st["host"], count=len(st["hits"]) or 1))[:250]
        except (ValueError, IndexError):
            return f"{r['name']} — {st['device'] or st['host']}"[:250]

    def _action(self, typ, r, key, st):
        return {"type": typ, "key": key, "rule": r, "title": self._title(r, st), "severity": r["severity"],
                "host": st["host"], "host_name": st["device"], "device_id": st["device_id"],
                "owner_ids": st["owner_ids"], "kind": st["kind"], "samples": list(st["samples"]),
                "count": len(st["hits"])}

    def feed(self, ev: dict, now: Optional[float] = None) -> tuple:
        """ev: {kind, severity, msg, host, device_id, device_name, owner_ids}. -> (ações, ids das regras que casaram)"""
        now = now or time.time()
        acts, matched = [], []
        msg = ev.get("msg") or ""
        for r in self.rules:
            if r["kinds"] != "any" and r["kinds"] != ev["kind"]:
                continue
            if ev["kind"] == "syslog" and ev.get("severity", 7) > r.get("max_severity", 7):
                continue
            if r["_dev"] and ev.get("device_id") not in r["_dev"]:
                continue
            m = r["_rx"].search(msg)
            c = None if m else (r["_cx"].search(msg) if r["_cx"] else None)
            if not m and not c:
                continue
            group = group_of(m or c)
            key = f"log:{r['id']}:{ev.get('device_id') or ev.get('host')}:{group}"[:300]
            st = self.state.get(key)
            if st is None:
                if c:            # normalizou algo que nem estava aberto
                    continue
                if len(self.state) > 20000:
                    continue
                st = self.state[key] = {"rule": r["id"], "hits": deque(), "open": False, "last": 0.0, "push": 0.0,
                                        "samples": deque(maxlen=5)}
            st.update(group=group, device=ev.get("device_name") or ev.get("host_name") or "", host=ev.get("host", ""),
                      device_id=ev.get("device_id"), owner_ids=ev.get("owner_ids") or [], kind=ev["kind"])
            if c:
                st["hits"].clear()
                if st["open"]:
                    st["open"] = False
                    st["samples"].append(msg[:300])
                    acts.append(self._action("ok", r, key, st))
                continue
            matched.append(r["id"])
            self.hits[r["id"]] = self.hits.get(r["id"], 0) + 1
            st["hits"].append(now)
            st["samples"].append(msg[:300])
            st["last"] = now
            while st["hits"] and now - st["hits"][0] > r["window_s"]:
                st["hits"].popleft()
            if len(st["hits"]) > 10000:
                st["hits"].popleft()
            if st["open"]:
                if now - st["push"] >= self.REPEAT_EVERY:
                    st["push"] = now
                    acts.append(self._action("repeat", r, key, st))
            elif len(st["hits"]) >= r["threshold"]:
                st["open"] = True
                st["push"] = now
                acts.append(self._action("open", r, key, st))
        return acts, matched

    def sweep(self, now: Optional[float] = None) -> list:
        now = now or time.time()
        rules = {r["id"]: r for r in self.rules}
        acts, drop = [], []
        for key, st in self.state.items():
            r = rules.get(st["rule"])
            if not r:
                drop.append(key)
                continue
            while st["hits"] and now - st["hits"][0] > r["window_s"]:
                st["hits"].popleft()
            if st["open"] and r.get("auto_resolve_min") and now - st["last"] > r["auto_resolve_min"] * 60:
                st["open"] = False
                acts.append(self._action("ok", r, key, st))
            if not st["open"] and not st["hits"]:
                drop.append(key)
        for k in drop:
            self.state.pop(k, None)
        return acts
