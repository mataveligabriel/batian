"""Envio de eventos para SIEM (Wazuh, Graylog, Splunk, Elastic, QRadar, Sentinel…).

Eventos normalizados: {ts, category, action, outcome, severity (0–10), user, src_ip, device, device_ip, message, details}.
Categorias: auth (login), session (terminal/web/RDP/Winbox/lote), command (política de comandos e IA),
change (alterações feitas pela interface/API), incident (abertura/resolução).

Saídas: syslog (UDP/TCP/TLS, RFC 5424) com a mensagem em CEF, JSON ou LEEF; HTTP (JSON em lote, com cabeçalho de
autenticação); Splunk HEC; GELF (Graylog) por UDP/TCP.
"""
import asyncio
import json
import socket
import ssl
from datetime import datetime, timezone
from typing import Dict, List, Optional

VERSION = "2.0"
CATEGORIES = {"auth": "Login", "session": "Sessões de acesso", "command": "Comandos (política e IA)",
              "change": "Alterações na interface/API", "incident": "Incidentes"}
FORMATS = ("cef", "json", "leef")
KINDS = ("syslog", "http", "splunk", "gelf")
PROTOS = ("udp", "tcp", "tls")
SEV_INC = {"info": 2, "low": 3, "medium": 5, "high": 7, "critical": 9}
MAX_QUEUE = 5000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def event(category: str, action: str, outcome: str = "success", severity: int = 3, user: str = "", src_ip: str = "",
          device: str = "", device_ip: str = "", message: str = "", ts: str = "", **details) -> dict:
    return {"ts": ts or now_iso(), "category": category, "action": action, "outcome": outcome,
            "severity": max(0, min(10, int(severity))), "user": user or "", "src_ip": src_ip or "", "device": device or "",
            "device_ip": device_ip or "", "message": (message or "")[:2000],
            "details": {k: v for k, v in details.items() if v not in (None, "", [], {})}}


# ---------------------------------------------------------------- formatos
def _cef_esc_h(v) -> str:
    return str(v).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _cef_esc_x(v) -> str:
    return str(v).replace("\\", "\\\\").replace("=", "\\=").replace("\r", " ").replace("\n", "\\n")


def to_cef(e: dict) -> str:
    sig = f"{e['category']}:{e['action']}"
    ext = {"rt": int(datetime.fromisoformat(e["ts"]).timestamp() * 1000), "cat": e["category"], "act": e["action"],
           "outcome": e["outcome"], "suser": e["user"], "src": e["src_ip"], "dhost": e["device"], "dst": e["device_ip"],
           "msg": e["message"]}
    d = e.get("details") or {}
    for i, (k, v) in enumerate(list(d.items())[:6], 1):
        ext[f"cs{i}Label"] = k
        ext[f"cs{i}"] = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
    ext = {k: v for k, v in ext.items() if v not in ("", None)}
    body = " ".join(f"{k}={_cef_esc_x(v)}" for k, v in ext.items())
    name = e["message"][:120] or sig
    return f"CEF:0|BastiON|BastiON|{VERSION}|{_cef_esc_h(sig)}|{_cef_esc_h(name)}|{e['severity']}|{body}"


def to_leef(e: dict) -> str:
    f = {"devTime": e["ts"], "cat": e["category"], "usrName": e["user"], "src": e["src_ip"], "dstName": e["device"],
         "dst": e["device_ip"], "sev": e["severity"], "outcome": e["outcome"], "msg": e["message"]}
    f = {k: str(v).replace("\t", " ").replace("\n", " ") for k, v in f.items() if v not in ("", None)}
    return f"LEEF:2.0|BastiON|BastiON|{VERSION}|{e['category']}:{e['action']}|^|" + "^".join(f"{k}={v}" for k, v in f.items())


def to_json(e: dict) -> str:
    return json.dumps({"@timestamp": e["ts"], "product": "BastiON", **e}, ensure_ascii=False, default=str)


def to_gelf(e: dict) -> str:
    g = {"version": "1.1", "host": "bastion", "short_message": e["message"][:250] or f"{e['category']} {e['action']}",
         "timestamp": round(datetime.fromisoformat(e["ts"]).timestamp(), 3), "level": max(0, min(7, 7 - round(e["severity"] * 0.7))),
         "_category": e["category"], "_action": e["action"], "_outcome": e["outcome"], "_severity": e["severity"]}
    for k in ("user", "src_ip", "device", "device_ip"):
        if e.get(k):
            g[f"_{k}"] = e[k]
    for k, v in (e.get("details") or {}).items():
        g[f"_{k}"] = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
    return json.dumps(g, ensure_ascii=False, default=str)


def render(e: dict, fmt: str) -> str:
    return {"cef": to_cef, "leef": to_leef}.get(fmt, to_json)(e)


def syslog_line(e: dict, fmt: str, hostname: str = "bastion") -> str:
    """RFC 5424: <PRI>1 TIMESTAMP HOST APP - MSGID - MSG (facility local4)."""
    sev5424 = 2 if e["severity"] >= 9 else 3 if e["severity"] >= 7 else 4 if e["severity"] >= 5 else 5 if e["severity"] >= 3 else 6
    pri = 20 * 8 + sev5424
    return f"<{pri}>1 {e['ts']} {hostname} bastion - {e['category']} - {render(e, fmt)}"


# ---------------------------------------------------------------- envio
def clean_dest(d: dict) -> dict:
    kind = d.get("kind") if d.get("kind") in KINDS else "syslog"
    out = {"id": str(d.get("id") or "")[:32], "name": str(d.get("name") or kind).strip()[:60], "enabled": d.get("enabled", True) is not False,
           "kind": kind, "categories": [c for c in (d.get("categories") or list(CATEGORIES)) if c in CATEGORIES],
           "min_severity": max(0, min(10, int(d.get("min_severity") or 0)))}
    if kind in ("syslog", "gelf"):
        host = str(d.get("host") or "").strip()
        if not host or len(host) > 253 or any(c.isspace() for c in host):
            raise ValueError("Informe o servidor (IP ou nome)")
        port = int(d.get("port") or (12201 if kind == "gelf" else 514))
        if not 1 <= port <= 65535:
            raise ValueError("Porta inválida")
        proto = d.get("proto") if d.get("proto") in PROTOS else "udp"
        if kind == "gelf" and proto == "tls":
            proto = "tcp"
        out.update(host=host, port=port, proto=proto, verify_tls=d.get("verify_tls", True) is not False,
                   format=d.get("format") if d.get("format") in FORMATS else "cef")
    else:
        url = str(d.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL deve começar com http:// ou https://")
        out.update(url=url[:500], header=str(d.get("header") or ("Authorization" if kind == "splunk" else "")).strip()[:80],
                   verify_tls=d.get("verify_tls", True) is not False, index=str(d.get("index") or "")[:60])
    return out


class Sender:
    """Uma saída: fila em memória, envio em lote, reconexão nos transportes TCP/TLS."""

    def __init__(self, dest: dict, secret: str = ""):
        self.d, self.secret = dest, secret
        self.q: List[dict] = []
        self.sent = self.failed = self.dropped = 0
        self.last_ok: Optional[str] = None
        self.last_error = ""
        self._w = None
        self.lock = asyncio.Lock()

    def wants(self, e: dict) -> bool:
        return self.d.get("enabled", True) and e["category"] in self.d["categories"] and e["severity"] >= self.d.get("min_severity", 0)

    def push(self, e: dict):
        self.q.append(e)
        if len(self.q) > MAX_QUEUE:
            n = len(self.q) - MAX_QUEUE
            del self.q[:n]
            self.dropped += n

    async def flush(self, http=None):
        async with self.lock:
            if not self.q:
                return
            batch, self.q = self.q[:500], self.q[500:]
            try:
                await self._send(batch, http)
                self.sent += len(batch)
                self.last_ok, self.last_error = now_iso(), ""
            except Exception as e:
                self.q = batch + self.q
                if len(self.q) > MAX_QUEUE:
                    self.dropped += len(self.q) - MAX_QUEUE
                    self.q = self.q[-MAX_QUEUE:]
                self.failed += 1
                self.last_error = (str(e) or type(e).__name__)[:200]
                await self._drop_conn()

    async def _drop_conn(self):
        if self._w:
            try:
                self._w.close()
            except Exception:
                pass
        self._w = None

    async def _send(self, batch: List[dict], http):
        d = self.d
        if d["kind"] in ("syslog", "gelf"):
            lines = [syslog_line(e, d["format"]) if d["kind"] == "syslog" else to_gelf(e) for e in batch]
            if d["proto"] == "udp":
                loop = asyncio.get_running_loop()
                infos = await loop.getaddrinfo(d["host"], d["port"], type=socket.SOCK_DGRAM)
                fam, _, _, _, addr = infos[0]
                s = socket.socket(fam, socket.SOCK_DGRAM)
                try:
                    for ln in lines:
                        s.sendto(ln.encode("utf-8")[:64000], addr)
                finally:
                    s.close()
                return
            if not self._w:
                ctx = None
                if d["proto"] == "tls":
                    ctx = ssl.create_default_context()
                    if not d.get("verify_tls", True):
                        ctx.check_hostname = False
                        ctx.verify_mode = ssl.CERT_NONE
                _, self._w = await asyncio.wait_for(asyncio.open_connection(d["host"], d["port"], ssl=ctx), 10)
            sep = b"\0" if d["kind"] == "gelf" else b"\n"
            self._w.write(b"".join(ln.encode("utf-8") + sep for ln in lines))
            await asyncio.wait_for(self._w.drain(), 15)
            return
        headers = {"Content-Type": "application/json"}
        if self.secret:
            if d["kind"] == "splunk":
                headers["Authorization"] = f"Splunk {self.secret}"
            elif d.get("header"):
                headers[d["header"]] = self.secret
        if d["kind"] == "splunk":
            body = "\n".join(json.dumps({"time": datetime.fromisoformat(e["ts"]).timestamp(), "sourcetype": "bastion",
                                         "source": "bastion", **({"index": d["index"]} if d.get("index") else {}), "event": e},
                                        ensure_ascii=False, default=str) for e in batch)
        else:
            body = json.dumps([json.loads(to_json(e)) for e in batch], ensure_ascii=False)
        r = await http.post(d["url"], content=body.encode("utf-8"), headers=headers, timeout=20)
        if r.status_code >= 300:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")

    def status(self) -> dict:
        return {"id": self.d["id"], "sent": self.sent, "failed": self.failed, "dropped": self.dropped, "queued": len(self.q),
                "last_ok": self.last_ok, "last_error": self.last_error}


# ---------------------------------------------------------------- mapeamento das coleções
def from_login(doc: dict) -> dict:
    ok = bool(doc.get("ok"))
    reason = doc.get("reason") or ""
    sev = 2 if ok else 7 if reason == "bloqueado" else 5
    return event("auth", "login" if ok else "login_failed", "success" if ok else "failure", sev, user=doc.get("email", ""),
                 src_ip=doc.get("ip", ""), ts=doc.get("at", ""), message=("Login" if ok else f"Falha de login ({reason or 'senha'})") + f" — {doc.get('email', '')}",
                 user_agent=doc.get("ua", ""), reason=reason)


_KIND = {"terminal": "terminal SSH/Telnet", "web": "acesso web", "rdp": "RDP", "winbox": "Winbox", "batch": "comandos em lote"}


def from_session(doc: dict, ended: bool) -> dict:
    k = doc.get("kind") or "terminal"
    if k == "batch":
        cmds = doc.get("commands") or []
        return event("session", "batch", "success" if doc.get("ok", True) is not False else "failure", 4, user=doc.get("user_email", ""),
                     device=doc.get("device_name", ""), ts=doc.get("started_at", ""),
                     message=f"Comandos em lote em {doc.get('device_name', '')}" + (f": {' | '.join(cmds[:3])}" if cmds else ""),
                     commands=cmds[:50], channel=doc.get("oltgen") or "")
    if ended:
        return event("session", "end", "success", 3, user=doc.get("user_email", ""), device=doc.get("device_name", ""),
                     ts=doc.get("ended_at", ""), message=f"Fim de {_KIND.get(k, k)} em {doc.get('device_name', '')}",
                     kind=k, duration_s=doc.get("duration_seconds"), session_id=doc.get("id"))
    return event("session", "start", "success", 3, user=doc.get("user_email", ""), device=doc.get("device_name", ""),
                 ts=doc.get("started_at", ""), message=f"Início de {_KIND.get(k, k)} em {doc.get('device_name', '')}", kind=k,
                 session_id=doc.get("id"))


_CMD_SEV = {"block": 8, "denied": 6, "approval-requested": 6, "approved": 5, "approved-run": 5, "alert": 6}


def from_cmd_event(doc: dict) -> dict:
    a = doc.get("action", "")
    return event("command", a, "failure" if a in ("block", "denied") else "success", _CMD_SEV.get(a, 5), user=doc.get("user_email", ""),
                 device=doc.get("device_name", ""), device_ip=doc.get("host", ""), ts=doc.get("at", ""),
                 message=f"Política de comandos: {a} — {' | '.join((doc.get('commands') or [])[:3])}",
                 commands=(doc.get("commands") or [])[:50], rules=doc.get("rules"), channel=doc.get("channel"),
                 approval_id=doc.get("approval_id"), decided_by=doc.get("decided_by"))


def from_ftp(doc: dict) -> dict:
    ok = doc.get("ok", True)
    act = doc.get("action", "")
    return event("file", f"ftp_{act}", "success" if ok else "failure", 3 if ok else 5, user=doc.get("user", ""), src_ip=doc.get("peer", ""),
                 ts=doc.get("at", ""), message=f"FTP {act} {doc.get('path', '')} ({doc.get('bytes', 0)} bytes) via {doc.get('via', '')}"
                 + (f" — {doc.get('error')}" if doc.get("error") else ""), path=doc.get("path"), bytes=doc.get("bytes"), access=doc.get("access_name"))


def from_tacacs(doc: dict) -> dict:
    t, res = doc.get("type", ""), doc.get("result", "")
    ok = res not in ("fail", "error", "deny")
    sev = {"authen": 3 if ok else 6, "author": 3 if ok else 6}.get(t, 2)
    what = {"authen": "login", "author": "autorização", "acct": "contabilidade"}.get(t, t)
    msg = f"TACACS+ {what} {res}: {doc.get('user', '')} em {doc.get('device_name') or doc.get('nas_ip', '')}"
    if doc.get("cmd"):
        msg += f" — {doc['cmd']}"
    if doc.get("reason"):
        msg += f" ({doc['reason']})"
    return event("authentication" if t == "authen" else "command", f"tacacs_{t}", "success" if ok else "failure", sev,
                 user=doc.get("user_email") or doc.get("user", ""), src_ip=doc.get("rem_addr", ""), device=doc.get("device_name", ""),
                 device_ip=doc.get("nas_ip", ""), ts=doc.get("at", ""), message=msg, command=doc.get("cmd"), client=doc.get("client"))


def from_ai(doc: dict) -> dict:
    return event("command", f"ai_{doc.get('kind', 'query')}", "success" if doc.get("ok") else "failure",
                 6 if doc.get("kind") == "change" else 3, user=doc.get("user_email", ""), device=doc.get("device_name", ""),
                 ts=doc.get("at", ""), message=f"Assistente IA ({doc.get('channel', '')}) em {doc.get('device_name', '')}: "
                 + " | ".join((doc.get("commands") or [])[:3]), commands=(doc.get("commands") or [])[:50], channel=doc.get("channel"))


def from_change(doc: dict) -> dict:
    ok = int(doc.get("status") or 0) < 400
    return event("change", doc.get("method", "").lower(), "success" if ok else "failure", 4 if ok else 5, user=doc.get("user_email", ""),
                 src_ip=doc.get("ip", ""), ts=doc.get("at", ""), message=f"{doc.get('method')} {doc.get('path')} → {doc.get('status')}",
                 path=doc.get("path"), status=doc.get("status"))


def from_incident(doc: dict, resolved: bool) -> dict:
    sev = SEV_INC.get(doc.get("severity", "medium"), 5)
    return event("incident", "resolved" if resolved else "opened", "success", max(2, sev - 3) if resolved else sev,
                 device=doc.get("device_name") or doc.get("host_name", ""), device_ip=doc.get("host", ""),
                 ts=doc.get("resolved_at" if resolved else "created_at", ""),
                 message=f"Incidente #{doc.get('number')} {'resolvido' if resolved else 'aberto'}: {doc.get('title', '')}",
                 number=doc.get("number"), source=doc.get("source"), incident_severity=doc.get("severity"))


def sample_events() -> List[dict]:
    return [event("auth", "login_failed", "failure", 5, user="operador@exemplo.net", src_ip="203.0.113.50", message="Falha de login (senha) — operador@exemplo.net"),
            event("command", "block", "failure", 8, user="operador@exemplo.net", device="BORDA-01", device_ip="192.0.2.1",
                  message="Política de comandos: block — reset saved-configuration", commands=["reset saved-configuration"], channel="terminal")]
