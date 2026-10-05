"""Identificar fabricante por lista: entra em cada IP (SSH e/ou Telnet) com o usuário e a senha informados e
descobre se é Huawei, Cisco, Datacom, Juniper, ZTE, MikroTik…

Pistas, da mais barata para a mais cara: identificação do servidor SSH → texto de boas-vindas e formato do
prompt → resposta a um comando de versão (só leitura). Uma tentativa de login por protocolo, para não travar conta.
"""
import asyncio
import ipaddress
import re
from typing import List, Optional, Tuple

import asyncssh

from ssh_service import LEGACY_ALGS, _clean_ansi, learn_prompt, read_reply
from telnet_service import TelnetClientWrapper

MAX_TARGETS = 1024
CONNECT_TIMEOUT = 8
VENDOR_LABEL = {"huawei": "Huawei", "cisco": "Cisco", "datacom": "Datacom", "juniper": "Juniper", "zte": "ZTE",
                "mikrotik": "MikroTik", "ubiquiti": "Ubiquiti", "linux": "Linux", "other": "Outro"}

# (fabricante, padrão, peso) aplicados a tudo que o equipamento mostrou. Soma de pesos decide.
_RULES = [
    ("huawei", r"Huawei Versatile Routing Platform|VRP \(R\) software|HUAWEI TECH|Huawei Technologies", 10),
    ("huawei", r"Error: Unrecognized command found at '\^' position|Info: The max number of VTY users", 6),
    ("juniper", r"JUNOS|Junos:|Juniper Networks|--- JUNOS", 10),
    ("cisco", r"Cisco IOS|IOS[ -]XE|IOS XR|Cisco Nexus|NX-OS|Cisco Internetwork Operating System|cisco Systems, Inc", 10),
    ("cisco", r"% Invalid input detected at '\^' marker", 3),
    ("datacom", r"DmOS|DATACOM|Datacom|DmSwitch|Welcome to the DmOS CLI|\bDM(4\d{3}|2\d{3}|1\d{3}|3\d{3}|8\d{3})\b", 10),
    ("datacom", r"syntax error: (unknown command|expecting)", 4),
    ("zte", r"ZXR10|ZXA10|ZXROS|ZTE Corporation|ZTE ZX|\bC3[02]0\b.*ZTE|ZXAN", 10),
    ("zte", r"%Error \d+: |%Code \d+: ", 4),
    ("mikrotik", r"MikroTik|RouterOS|bad command name", 10),
    ("ubiquiti", r"EdgeOS|EdgeSwitch|Ubiquiti|UniFi|\bairOS\b|Vyatta", 10),
    ("linux", r"\bLinux \S+ \d+\.\d+|GNU/Linux|Ubuntu|Debian|CentOS|BusyBox", 5),
    ("other:H3C/HPE Comware", r"H3C Comware|HPE? Comware|New H3C Technologies|Hangzhou H3C", 12),
    ("other:Fiberhome", r"FiberHome|Fiberhome|AN5[05]\d\d", 10),
    ("other:Parks", r"\bParks\b|Fiberlink", 8),
    ("other:Intelbras", r"Intelbras", 8),
    ("other:Extreme", r"ExtremeXOS|Extreme Networks", 10),
    ("other:Fortinet", r"FortiGate|FortiOS", 10),
    ("other:Nokia", r"TiMOS|Nokia 7\d\d\d|ALCATEL SR", 10),
    ("other:Arista", r"Arista (Networks|vEOS|DCS)", 10),
    ("other:V-Solution/OLT genérica", r"V-SOL|VSOL", 8),
]
_RULES_C = [(v, re.compile(p, re.I if v in ("linux",) else 0), w) for v, p, w in _RULES]
_SSH_ID = [("huawei", r"HUAWEI|VRP"), ("cisco", r"Cisco"), ("mikrotik", r"ROSSSH"), ("zte", r"ZTE"),
           ("other:H3C/HPE Comware", r"Comware"), ("datacom", r"DMOS|Datacom"), ("other:Fortinet", r"FortiSSH")]
_DETAIL = re.compile(r"^.*(VRP \(R\) software, Version.*|JUNOS .*|Junos: .*|Cisco IOS.*Version [^,\s]+.*|NX-OS.*version.*|IOS XR.*Version.*|"
                     r"DmOS[ -]?\d.*|Software version\s*:.*|ZX[AR]10.*Version.*|RouterOS \d.*|version: \d\S+.*|Model\s*:.*|Comware Software, Version.*)$",
                     re.I | re.M)
_AUTH_FAIL = re.compile(r"(?i)(login incorrect|authentication fail|access denied|bad password|login invalid|invalid (user|login|password)|"
                        r"wrong password|username or password|% ?bad (secrets|passwords)|user was locked|error: (password|failed to))")
_ASK_LOGIN = re.compile(r"(?i)(user ?name|login|pass(word|phrase)?)\s*:\s*$")


def parse_targets(text: str, default_port: Optional[int] = None) -> Tuple[List[dict], List[str]]:
    """Uma entrada por linha: IP ou nome, opcionalmente `:porta` e um nome depois (espaço, vírgula, ; ou tab).
    Aceita também rede em CIDR (até /22). Devolve (alvos, linhas recusadas)."""
    out, bad, seen = [], [], set()
    for raw in (text or "").replace("\r", "\n").split("\n"):
        line = raw.split("#")[0].strip()
        if not line:
            continue
        parts = [p for p in re.split(r"[\s,;]+", line) if p]
        ipish = [i for i, p in enumerate(parts) if re.match(r"^\d{1,3}(\.\d{1,3}){3}(:\d+|/\d+)?$", p)]
        k = ipish[0] if ipish else 0                       # aceita "nome IP" além de "IP nome"
        host, name = parts[k], " ".join(parts[:k] + parts[k + 1:])[:80]
        port = default_port
        m = re.match(r"^(\[[0-9a-fA-F:]+\]|[^:\s]+):(\d{1,5})$", host)
        if m:
            host, port = m.group(1).strip("[]"), int(m.group(2))
            if not 1 <= port <= 65535:
                bad.append(line)
                continue
        hosts = []
        if "/" in host:
            try:
                net = ipaddress.ip_network(host, strict=False)
                if net.num_addresses > 1024:
                    bad.append(f"{line} (rede grande demais — máximo /22)")
                    continue
                hosts = [str(h) for h in (net.hosts() if net.num_addresses > 2 else net)]
            except ValueError:
                bad.append(line)
                continue
        else:
            try:
                hosts = [str(ipaddress.ip_address(host))]
            except ValueError:
                if re.search(r"[a-zA-Z]", host) and re.match(r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$", host):
                    hosts = [host.lower()]
                else:
                    bad.append(line)
                    continue
        for h in hosts:
            if (h, port) in seen:
                continue
            seen.add((h, port))
            out.append({"host": h, "port": port, "name": name if len(hosts) == 1 else ""})
            if len(out) > MAX_TARGETS:
                raise ValueError(f"Lista grande demais — máximo de {MAX_TARGETS} endereços por vez")
    return out, bad


def classify(ssh_id: str, text: str) -> dict:
    """Decide o fabricante pelo que foi coletado. Devolve {vendor, label, confidence, detail, evidence}."""
    score, why = {}, {}
    for v, pat in _SSH_ID:
        if re.search(pat, ssh_id or "", re.I):
            score[v] = score.get(v, 0) + 6
            why.setdefault(v, f"servidor SSH “{ssh_id}”")
    for v, rx, w in _RULES_C:
        m = rx.search(text or "")
        if m:
            score[v] = score.get(v, 0) + w
            why.setdefault(v, f"“{m.group(0).strip()[:60]}”")
    clean = [l.strip() for l in (text or "").replace("\r", "\n").split("\n") if l.strip()]
    last = clean[-1] if clean else ""
    # formato do prompt: pista fraca, só desempata ou vale sozinha com confiança baixa
    shape = None
    if re.match(r"^<[^<>\s]{1,64}>$", last):
        shape = "huawei"
    elif re.match(r"^(\{\w+(:\d+)?\}\s*)?[\w.-]+@[\w.-]+[>#%]$", last):
        shape = "juniper"
    elif re.match(r"^\[[\w.-]+@[^\]]+\] ?>$", last):
        shape = "mikrotik"
    if shape:
        score[shape] = score.get(shape, 0) + 3
        why.setdefault(shape, f"prompt “{last[:40]}”")
    if not score:
        return {"vendor": None, "label": "", "confidence": "", "detail": "", "evidence": ""}
    best = max(score, key=lambda k: score[k])
    if best == "linux" and len(score) > 1:                 # Junos, DmOS, EdgeOS… rodam sobre Linux/BSD
        others = {k: s for k, s in score.items() if k != "linux"}
        best = max(others, key=lambda k: others[k])
    vendor, _, sub = best.partition(":")
    m = _DETAIL.search(text or "")
    detail = re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else ""
    return {"vendor": vendor, "label": sub or VENDOR_LABEL.get(vendor, vendor),
            "confidence": "alta" if score[best] >= 10 else "média" if score[best] >= 6 else "baixa",
            "detail": detail, "evidence": why.get(best, "")}


def probes_for(text: str) -> List[str]:
    """Comandos de versão (só leitura) coerentes com o prompt visto."""
    clean = [l.strip() for l in text.replace("\r", "\n").split("\n") if l.strip()]
    last = clean[-1] if clean else ""
    if re.match(r"^<.*>$", last) or re.match(r"^\[[^@\]]+\]$", last):
        return ["display version"]
    if re.match(r"^\[[\w.-]+@[^\]]+\] ?>$", last):
        return ["/system resource print"]
    if re.match(r"^(\{\w+(:\d+)?\}\s*)?[\w.-]+@[\w.-]+[>%]$", last):
        return ["show version"]
    if last.endswith("$") or re.search(r"[:~][#$]$", last):
        return ["uname -a"]
    return ["show version", "show platform", "show system"]


def _decided(c: dict) -> bool:
    return bool(c["vendor"]) and c["confidence"] == "alta"


async def _session(read, send, ssh_id: str) -> Tuple[dict, str]:
    """Com a sessão aberta: lê as boas-vindas, manda os comandos de versão e classifica."""
    banner = await read_reply(read, send, 1.5, 10)
    text = _clean_ansi(banner.decode("utf-8", "replace"))
    prompt = learn_prompt(banner)
    c = classify(ssh_id, text)
    for cmd in probes_for(text):
        if _decided(c) and c["detail"]:
            break
        send((cmd + "\n").encode())
        out = await read_reply(read, send, 1.5, 12, prompt)
        text += "\n" + _clean_ansi(out.decode("utf-8", "replace"))[-20000:]
        c = classify(ssh_id, text)
    c["hostname"] = (prompt or "").split("@")[-1]
    return c, text


def _why(e: BaseException) -> Tuple[str, str]:
    """(situação, mensagem curta) a partir do erro de conexão."""
    if isinstance(e, asyncssh.PermissionDenied):
        return "auth", "usuário ou senha recusados"
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "down", "sem resposta (tempo esgotado)"
    if isinstance(e, ConnectionRefusedError) or "refused" in str(e).lower() or isinstance(e, asyncssh.ChannelOpenError):
        return "closed", "porta fechada"
    if isinstance(e, OSError):
        return "down", (str(e) or type(e).__name__)[:120]
    if isinstance(e, (asyncssh.KeyExchangeFailed, asyncssh.ProtocolError, asyncssh.ConnectionLost, asyncssh.DisconnectError)):
        return "error", f"SSH não negociou: {str(e)[:120]}"
    return "error", (str(e) or type(e).__name__)[:140]


async def probe_ssh(host: str, port: int, username: str, password: str, tunnel=None) -> dict:
    kw = dict(username=username, password=password, known_hosts=None, client_keys=None, connect_timeout=CONNECT_TIMEOUT,
              login_timeout=20, preferred_auth="password,keyboard-interactive", **LEGACY_ALGS)
    conn = None
    try:
        conn = await (tunnel.connect_ssh(host, port=port, **kw) if tunnel else asyncssh.connect(host, port=port, **kw))
    except Exception as e:
        st, msg = _why(e)
        return {"status": st, "error": msg}
    try:
        ssh_id = str(conn.get_extra_info("server_version") or "")
        proc = await conn.create_process(term_type="vt100", term_size=(200, 100), encoding=None)
        try:
            c, _ = await asyncio.wait_for(
                _session(lambda t: asyncio.wait_for(proc.stdout.read(65536), timeout=t), proc.stdin.write, ssh_id), 60)
        finally:
            proc.close()
        return {"status": "ok" if c["vendor"] else "unknown", "ssh_id": ssh_id, **c}
    except Exception as e:
        c = classify(str(conn.get_extra_info("server_version") or ""), "")
        if c["vendor"]:                                    # entrou, mas a shell não abriu: vale a pista do servidor SSH
            return {"status": "ok", "hostname": "", **c}
        return {"status": "unknown", "error": f"entrou, mas não deu para ler a sessão: {str(e)[:100] or type(e).__name__}"}
    finally:
        conn.close()


async def probe_telnet(host: str, port: int, username: str, password: str, tunnel=None) -> dict:
    t = TelnetClientWrapper([], host, port, username=username, password=password, device_type="other")
    try:
        opener = tunnel.open_connection(host, port) if tunnel else asyncio.open_connection(host, port)
        t.reader, t.writer = await asyncio.wait_for(opener, timeout=CONNECT_TIMEOUT)
    except Exception as e:
        st, msg = _why(e)
        return {"status": st, "error": msg}
    try:
        await t.open_shell(200, 100)
        read = lambda tm: asyncio.wait_for(t.queue.get(), timeout=tm)
        first = await read_reply(read, t._send, 2.5, 20)
        head = _clean_ansi(first.decode("utf-8", "replace"))
        if not head.strip():
            return {"status": "error", "error": "a porta abriu, mas o equipamento não mostrou nada"}
        tail = head.strip()[-200:]
        if _AUTH_FAIL.search(head[-600:]) or _ASK_LOGIN.search(tail):
            pre = classify("", head)                        # mesmo sem entrar, o texto de login às vezes entrega o fabricante
            return {"status": "auth", "error": "usuário ou senha recusados", **({k: pre[k] for k in ("vendor", "label", "confidence", "evidence")} if pre["vendor"] else {})}
        c = classify("", head)
        prompt = learn_prompt(first)
        text = head
        for cmd in probes_for(head):
            if _decided(c) and c["detail"]:
                break
            t._send(cmd.encode() + b"\r\n")
            out = await read_reply(read, t._send, 1.5, 12, prompt)
            text += "\n" + _clean_ansi(out.decode("utf-8", "replace"))[-20000:]
            c = classify("", text)
        c["hostname"] = (prompt or "").split("@")[-1]
        return {"status": "ok" if c["vendor"] else "unknown", **c}
    except Exception as e:
        return {"status": "error", "error": (str(e) or type(e).__name__)[:140]}
    finally:
        try:
            if t._pump:
                t._pump.cancel()
            if t.writer:
                t.writer.close()
        except Exception:
            pass


async def probe(target: dict, protocol: str, username: str, password: str, tunnel=None) -> dict:
    """protocol: ssh | telnet | auto (SSH primeiro; Telnet só se o SSH não abrir)."""
    host = target["host"]
    res = {"host": host, "name": target.get("name") or ""}
    if protocol in ("ssh", "auto"):
        port = target.get("port") or 22
        r = await probe_ssh(host, port, username, password, tunnel)
        if protocol == "ssh" or r["status"] in ("ok", "unknown", "auth"):
            return {**res, "protocol": "ssh", "port": port, **r}
        ssh_err = r.get("error", "")
        port = 23
        r = await probe_telnet(host, port, username, password, tunnel)
        if r["status"] in ("down", "closed"):
            return {**res, "protocol": "", "port": None, "status": "down", "error": f"SSH: {ssh_err} · Telnet: {r.get('error')}"}
        return {**res, "protocol": "telnet", "port": port, **r}
    port = target.get("port") or 23
    return {**res, "protocol": "telnet", "port": port, **(await probe_telnet(host, port, username, password, tunnel))}
