"""Acesso Web: abre a página de gerência de um equipamento (http/https) pelo BastiON, direto no app.

Cada sessão ganha uma porta própria do BastiON (padrão 8090–8099). O site do equipamento fica na raiz
dessa porta, então não é preciso reescrever caminhos: telas antigas e SPAs funcionam como no acesso direto.

  navegador ──http──▶ BastiON :8090 ──(SSH pelos agentes)──▶ equipamento :80/:443

- Origem: pela cadeia de agentes do equipamento (túnel SSH, como o terminal) ou direto do servidor.
- Acesso: a URL de entrada traz um token de uso único por sessão, trocado por um cookie HttpOnly
  daquela porta. Sem o cookie certo a porta responde 403 — ninguém usa a sessão de outro.
- Portas diferentes = origens diferentes: o JavaScript da página do equipamento não enxerga o login
  do BastiON (que fica no localStorage da origem do app).
- Redirecionamento de http para https (ou para outra porta) do mesmo equipamento é seguido sozinho.
- A sessão fecha depois de WEB_PROXY_IDLE_MIN minutos sem uso (padrão 30) ou quando o usuário fecha.
"""
import asyncio
import hmac
import ipaddress
import logging
import os
import re
import secrets
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlsplit

import httpx

log = logging.getLogger("bastion.web")

IDLE_SECONDS = int(os.environ.get("WEB_PROXY_IDLE_MIN", "30")) * 60
MAX_AGE_SECONDS = 12 * 3600
REWRITE_LIMIT = 8 * 1024 * 1024          # corpo até 8 MB é lido inteiro para trocar URLs absolutas
REWRITE_TYPES = ("text/html", "javascript", "text/css", "json", "xml", "text/plain")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding"}
DROP_RESP = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding",
             "x-frame-options", "content-security-policy", "content-security-policy-report-only",
             "strict-transport-security", "alt-svc", "public-key-pins"}
AUTH_PATH = "/__bastion/auth"


def parse_ports(spec: str) -> List[int]:
    """'8090-8099' ou '8090,8091,9000-9002'."""
    out: List[int] = []
    for part in (spec or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [p for p in dict.fromkeys(out) if 1 <= p <= 65535][:100]


def parse_target(url: str) -> Tuple[str, str, int, str]:
    """'10.0.0.1', 'https://olt:8443/login' → (scheme, host, port, path)."""
    url = (url or "").strip()
    if not url:
        raise ValueError("Informe o endereço")
    if "://" not in url:
        url = "http://" + url
    u = urlsplit(url)
    scheme = (u.scheme or "http").lower()
    if scheme not in ("http", "https"):
        raise ValueError("Use http:// ou https://")
    host = u.hostname or ""
    if not host:
        raise ValueError("Endereço sem host")
    try:
        port = u.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise ValueError("Porta inválida")
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    return scheme, host, port, path


def is_loopback(host: str) -> bool:
    h = host.strip("[]").lower()
    if h in ("localhost", "0.0.0.0", "::") or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_loopback or ip.is_unspecified
    except ValueError:
        return False


def legacy_tls() -> ssl.SSLContext:
    """Equipamento velho: certificado autoassinado, TLS 1.0 e cifras fracas."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    except (ValueError, AttributeError):
        pass
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except ssl.SSLError:
        pass
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    return ctx


def _origin(scheme: str, host: str, port: int) -> str:
    h = f"[{host}]" if ":" in host else host
    default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    return f"{scheme}://{h}" if default else f"{scheme}://{h}:{port}"


def _page(status: int, title: str, text: str) -> Tuple[int, bytes]:
    html = (f"<!doctype html><meta charset=utf-8><title>{title}</title>"
            "<body style=\"background:#0B111C;color:#cbd5e1;font:14px system-ui;display:flex;align-items:center;"
            "justify-content:center;height:90vh\"><div style=\"max-width:520px;text-align:center\">"
            f"<h2 style=\"color:#f1f5f9\">{title}</h2><p>{text}</p></div>")
    return status, html.encode()


class Tunnel:
    """Encaminhamentos locais (127.0.0.1:porta → alvo) sobre uma conexão SSH com a cadeia de agentes."""

    def __init__(self, wrapper):
        self.w = wrapper
        self.fwd: Dict[Tuple[str, int], Tuple[object, int]] = {}

    async def local(self, host: str, port: int) -> int:
        key = (host, port)
        if key not in self.fwd:
            lat = await self.w.tcp_check(host, port, timeout=8)
            if lat is None:
                raise RuntimeError(f"{host}:{port} não responde a partir do agente")
            listener = await self.w.conn.forward_local_port("127.0.0.1", 0, host, port)
            self.fwd[key] = (listener, listener.get_port())
        return self.fwd[key][1]

    async def close(self):
        for listener, _ in self.fwd.values():
            try:
                listener.close()
            except Exception:
                pass
        self.fwd = {}
        try:
            await self.w.close()
        except Exception:
            pass


class WebSession:
    def __init__(self, **kw):
        self.id: str = kw["id"]
        self.user_id: str = kw["user_id"]
        self.user_email: str = kw.get("user_email", "")
        self.port: int = kw["port"]
        self.token: str = kw["token"]
        self.scheme: str = kw["scheme"]
        self.host: str = kw["host"]
        self.tport: int = kw["tport"]
        self.path: str = kw.get("path", "/")
        self.opened_as = (self.scheme, self.tport)      # antes de um redirecionamento http→https
        self.label: str = kw.get("label", "")
        self.via: str = kw.get("via", "")
        self.agent_id: Optional[str] = kw.get("agent_id")
        self.device_id: Optional[str] = kw.get("device_id")
        self.tunnel: Optional[Tunnel] = None
        self.created = time.time()
        self.last = time.time()
        self.requests = 0
        self.cookie_names: set = set()                 # cookies que o equipamento desta sessão criou
        self.lock = asyncio.Lock()
        self.client: Optional[httpx.AsyncClient] = None

    def public(self, with_token: bool = False) -> dict:
        d = {"id": self.id, "port": self.port, "label": self.label, "via": self.via,
             "url": _origin(self.scheme, self.host, self.tport) + (self.path if self.path != "/" else "/"),
             "target": _origin(self.scheme, self.host, self.tport), "device_id": self.device_id,
             "agent_id": self.agent_id, "user_email": self.user_email,
             "created_at": datetime.fromtimestamp(self.created, timezone.utc).isoformat(),
             "idle_seconds": int(time.time() - self.last), "requests": self.requests,
             "entry": f"{AUTH_PATH}?t={self.token}&to={quote(self.path, safe='/?=&%')}" if with_token else None}
        return d


# resolve(agent_id) -> (hops, nome do agente). hops vazio = direto do servidor.
Resolver = Callable[[str], Awaitable[Tuple[list, str]]]
# connect(hops) -> wrapper conectado (SSHClientWrapper)
Connector = Callable[[list], Awaitable[object]]


class WebProxy:
    def __init__(self, db, resolve: Resolver, connect: Connector, ports: Optional[List[int]] = None,
                 bind: str = "0.0.0.0"):
        self.db = db
        self.resolve = resolve
        self.connect = connect
        self.ports = ports if ports is not None else parse_ports(os.environ.get("WEB_PROXY_PORTS", "8090-8099"))
        self.bind = bind
        self.live_ports: List[int] = []
        self.sessions: Dict[str, WebSession] = {}
        self.by_port: Dict[int, WebSession] = {}
        self.server = None
        self._task = None
        self._reaper = None
        self.tls = legacy_tls()

    # ---------------- ciclo de vida ----------------
    async def start(self):
        import uvicorn

        class _Server(uvicorn.Server):          # não mexe nos sinais do servidor principal
            def install_signal_handlers(self):
                pass

            def capture_signals(self):
                import contextlib
                return contextlib.nullcontext()

        socks = []
        for p in self.ports:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((self.bind, p))
            except OSError as e:
                log.warning(f"Acesso Web: porta {p} indisponível ({e}); pulando")
                s.close()
                continue
            s.listen(128)
            s.setblocking(False)
            socks.append(s)
            self.live_ports.append(p)
        if not socks:
            log.warning("Acesso Web desligado: nenhuma porta livre em WEB_PROXY_PORTS")
            return
        cfg = uvicorn.Config(self.asgi, lifespan="off", log_config=None, log_level=None, access_log=False,
                             proxy_headers=False, http="h11", ws="none", timeout_keep_alive=30,
                             interface="asgi3")
        self.server = _Server(cfg)
        self._task = asyncio.create_task(self.server.serve(sockets=socks))
        self._reaper = asyncio.create_task(self._reap_loop())
        log.info(f"Acesso Web nas portas {self.live_ports[0]}–{self.live_ports[-1]}")

    async def stop(self):
        for sid in list(self.sessions):
            await self.close(sid, reason="desligamento")
        if self._reaper:
            self._reaper.cancel()
        if self.server:
            self.server.should_exit = True
            try:
                await asyncio.wait_for(self._task, 5)
            except Exception:
                pass

    async def _reap_loop(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for s in list(self.sessions.values()):
                if now - s.last > IDLE_SECONDS or now - s.created > MAX_AGE_SECONDS:
                    await self.close(s.id, reason="inatividade")

    # ---------------- sessões ----------------
    def list(self, user: Optional[dict] = None) -> List[dict]:
        out = [s for s in self.sessions.values() if user is None or s.user_id == user["id"]]
        return [s.public(with_token=user is not None) for s in sorted(out, key=lambda x: x.created)]

    async def open(self, user: dict, url: str, agent_id: Optional[str] = None, device_id: Optional[str] = None,
                   label: str = "") -> dict:
        if not self.live_ports:
            raise RuntimeError("Acesso Web desligado: nenhuma porta disponível no servidor (WEB_PROXY_PORTS)")
        scheme, host, tport, path = parse_target(url)
        if not agent_id and is_loopback(host):
            raise PermissionError("Endereço local do próprio servidor não é permitido")
        # mesma origem aberta de novo pelo mesmo usuário: reaproveita (mantém o login da página)
        for s in self.sessions.values():
            if s.user_id == user["id"] and (s.agent_id or None) == (agent_id or None) and s.host == host \
                    and (scheme, tport) in ((s.scheme, s.tport), s.opened_as):
                s.path, s.last = path, time.time()
                return s.public(with_token=True)
        free = [p for p in self.live_ports if p not in self.by_port]
        if not free:
            mine = sorted((s for s in self.sessions.values() if s.user_id == user["id"]), key=lambda x: x.last)
            if not mine:
                raise RuntimeError(f"Todas as {len(self.live_ports)} portas do Acesso Web estão em uso. "
                                   "Feche uma sessão aberta (Acesso Web → sessões ativas).")
            await self.close(mine[0].id, reason="substituída")
            free = [p for p in self.live_ports if p not in self.by_port]
        hops, via = await self.resolve(agent_id) if agent_id else ([], "BastiON (direto)")
        s = WebSession(id=secrets.token_hex(8), user_id=user["id"], user_email=user.get("email", ""),
                       port=free[0], token=secrets.token_urlsafe(24), scheme=scheme, host=host, tport=tport,
                       path=path, label=label or host, via=via, agent_id=agent_id, device_id=device_id)
        if hops:
            s.tunnel = Tunnel(await self.connect(hops))
            try:
                await s.tunnel.local(host, tport)
            except Exception:
                await s.tunnel.close()
                raise
        else:
            if not await _tcp_ok(host, tport):
                raise RuntimeError(f"{host}:{tport} não responde a partir do servidor do BastiON")
        s.client = httpx.AsyncClient(verify=self.tls, follow_redirects=False, trust_env=False,
                                     timeout=httpx.Timeout(90, connect=15))
        self.sessions[s.id] = s
        self.by_port[s.port] = s
        try:
            await self.db.sessions.insert_one({
                "id": s.id, "user_id": s.user_id, "user_email": s.user_email, "device_id": device_id,
                "device_name": f"{s.label} (web {_origin(scheme, host, tport)})", "via": via,
                "started_at": datetime.now(timezone.utc).isoformat(), "ended_at": None,
                "duration_seconds": None, "kind": "web"})
        except Exception:
            pass
        return s.public(with_token=True)

    async def close(self, sid: str, reason: str = "") -> bool:
        s = self.sessions.pop(sid, None)
        if not s:
            return False
        if self.by_port.get(s.port) is s:
            self.by_port.pop(s.port, None)
        if s.client:
            try:
                await s.client.aclose()
            except Exception:
                pass
        if s.tunnel:
            await s.tunnel.close()
        try:
            await self.db.sessions.update_one({"id": s.id}, {"$set": {
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": int(time.time() - s.created), "end_reason": reason}})
        except Exception:
            pass
        return True

    async def _upstream_base(self, s: WebSession, scheme: str, port: int, retry: bool) -> str:
        if not s.tunnel:
            return _origin(scheme, s.host, port)
        async with s.lock:
            if retry:                                   # conexão SSH caiu: refaz a cadeia uma vez
                await s.tunnel.close()
                hops, _ = await self.resolve(s.agent_id)
                s.tunnel = Tunnel(await self.connect(hops))
            lport = await s.tunnel.local(s.host, port)
        return f"{scheme}://127.0.0.1:{lport}"

    # ---------------- proxy (ASGI) ----------------
    async def asgi(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1011})
            return
        port = (scope.get("server") or ("", 0))[1]
        s = self.by_port.get(port)
        if not s:
            return await _send(send, *_page(410, "Sessão web encerrada",
                                             "Abra a página de novo pelo BastiON (Acesso Web)."))
        path = scope.get("path") or "/"
        qs = (scope.get("query_string") or b"").decode("latin-1")
        headers = [(k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in scope.get("headers", [])]
        cookie_name = f"bwp{port}"
        if path == AUTH_PATH:
            q = parse_qs(qs)
            tok, to = (q.get("t") or [""])[0], (q.get("to") or ["/"])[0]
            if not to.startswith("/") or to.startswith("//"):
                to = "/"
            if not tok or not hmac.compare_digest(tok, s.token):
                return await _send(send, *_page(403, "Link inválido", "Abra a página pelo BastiON."))
            s.last = time.time()
            return await _send(send, 302, b"", [("location", to), ("cache-control", "no-store"), (
                "set-cookie", f"{cookie_name}={s.token}; Path=/; HttpOnly; SameSite=Lax")])
        cookies = _parse_cookie(dict(headers).get("cookie", ""))
        if not hmac.compare_digest(cookies.get(cookie_name, ""), s.token):
            return await _send(send, *_page(403, "Acesso negado",
                                            "Esta porta pertence a uma sessão do BastiON. Abra a página pelo app."))
        s.last = time.time()
        s.requests += 1

        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break

        host_hdr = dict(headers).get("host", f"127.0.0.1:{port}")
        # atrás do Caddy (HTTPS): ele entrega em http na porta original e avisa o esquema em X-Forwarded-Proto
        peer = (scope.get("client") or ("", 0))[0]
        scheme = scope.get("scheme", "http")
        if peer in ("127.0.0.1", "::1") and dict(headers).get("x-forwarded-proto") == "https":
            scheme = "https"
        our = f"{scheme}://{host_hdr}"
        target = _origin(s.scheme, s.host, s.tport)
        fwd = []
        for k, v in headers:
            if k in HOP or k.startswith("proxy-") or k.startswith("x-forwarded-") or k in ("x-real-ip", "forwarded", "via"):
                continue
            if k == "cookie":
                # o navegador manda para esta porta os cookies de todas as sessões (cookie não separa porta):
                # não repassa o do BastiON nem os criados por outro equipamento aberto
                others = set().union(*(x.cookie_names for x in self.sessions.values() if x is not s)) - s.cookie_names
                v = "; ".join(f"{a}={b}" for a, b in cookies.items() if not re.fullmatch(r"bwp\d+", a) and a not in others)
                if not v:
                    continue
            elif k in ("origin", "referer"):
                v = v.replace(our, target, 1)
            fwd.append((k, v))
        fwd.append(("host", target.split("://", 1)[1]))
        fwd.append(("accept-encoding", "gzip, deflate"))
        full_path = path + ("?" + qs if qs else "")

        resp = None
        for attempt in (0, 1):
            try:
                base = await self._upstream_base(s, s.scheme, s.tport, retry=attempt == 1)
                req = s.client.build_request(scope["method"], base + full_path, headers=fwd, content=body)
                resp = await s.client.send(req, stream=True)
                break
            except (httpx.ConnectError, httpx.RemoteProtocolError, ConnectionError, OSError) as e:
                if attempt == 0 and s.tunnel:
                    continue
                return await _send(send, *_page(502, "Equipamento não respondeu",
                                                f"{target}{path} — {type(e).__name__}: {str(e)[:200]}"))
            except httpx.TimeoutException:
                return await _send(send, *_page(504, "Tempo esgotado", f"{target}{path} demorou demais para responder."))
            except Exception as e:
                return await _send(send, *_page(502, "Falha no Acesso Web", f"{type(e).__name__}: {str(e)[:200]}"))

        try:
            out_headers = []
            for k, v in resp.headers.multi_items():
                lk = k.lower()
                if lk in DROP_RESP:
                    continue
                if lk == "location":
                    v = self._rewrite_location(s, v, our)
                elif lk == "set-cookie":
                    v = _fix_cookie(v, secure=scheme == "https")
                    s.cookie_names.add(v.split("=", 1)[0].strip())
                out_headers.append((lk, v))
            ctype = resp.headers.get("content-type", "").lower()
            clen = int(resp.headers.get("content-length") or 0)
            if resp.status_code in (204, 304) or scope["method"] == "HEAD":
                await send({"type": "http.response.start", "status": resp.status_code,
                            "headers": [(k.encode("latin-1"), v.encode("latin-1", "replace")) for k, v in out_headers]})
                await send({"type": "http.response.body", "body": b""})
                return
            rewrite = any(t in ctype for t in REWRITE_TYPES) and clen <= REWRITE_LIMIT
            if rewrite:
                data = b""
                async for chunk in resp.aiter_bytes():
                    data += chunk
                    if len(data) > REWRITE_LIMIT:
                        rewrite = False
                        break
                if rewrite:
                    data = self._rewrite_body(s, data, our)
                    out_headers.append(("content-length", str(len(data))))
                    await send({"type": "http.response.start", "status": resp.status_code,
                                "headers": [(k.encode("latin-1"), v.encode("latin-1", "replace")) for k, v in out_headers]})
                    await send({"type": "http.response.body", "body": data})
                    return
                # grande demais: manda o que já leu e segue em streaming
                await send({"type": "http.response.start", "status": resp.status_code,
                            "headers": [(k.encode("latin-1"), v.encode("latin-1", "replace")) for k, v in out_headers]})
                await send({"type": "http.response.body", "body": data, "more_body": True})
            else:
                await send({"type": "http.response.start", "status": resp.status_code,
                            "headers": [(k.encode("latin-1"), v.encode("latin-1", "replace")) for k, v in out_headers]})
            async for chunk in resp.aiter_bytes():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await resp.aclose()

    def _rewrite_location(self, s: WebSession, loc: str, our: str) -> str:
        u = urlsplit(loc)
        if not u.scheme or not u.hostname:
            return loc
        if u.hostname.lower() != s.host.lower():
            return loc                                   # outro host: fora do alcance desta sessão
        scheme = u.scheme.lower()
        try:
            port = u.port or (443 if scheme == "https" else 80)
        except ValueError:
            return loc
        if scheme in ("http", "https") and (scheme, port) != (s.scheme, s.tport):
            s.scheme, s.tport = scheme, port             # ex.: http:80 → https:443 do mesmo equipamento
        rest = (u.path or "/") + ("?" + u.query if u.query else "") + ("#" + u.fragment if u.fragment else "")
        return rest

    def _rewrite_body(self, s: WebSession, data: bytes, our: str) -> bytes:
        """Links absolutos para o equipamento (http://10.0.0.1/…) passam a apontar para a porta do BastiON."""
        o = our.encode()
        cands = {_origin(sc, s.host, pt) for sc in ("http", "https") for pt in (s.tport, 80, 443)}
        for c in sorted(cands, key=len, reverse=True):
            cb = c.encode()
            data = re.sub(re.escape(cb) + rb"(?![\w.:-])", o, data) if cb in data else data
            esc = cb.replace(b"/", b"\\/")                 # dentro de JSON: http:\/\/10.0.0.1
            if esc in data:
                data = re.sub(re.escape(esc) + rb"(?![\w.:-])", o.replace(b"/", b"\\/"), data)
        return data


def _parse_cookie(h: str) -> Dict[str, str]:
    out = {}
    for part in h.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _fix_cookie(v: str, secure: bool) -> str:
    """Cookie do equipamento vale no endereço do BastiON: tira Domain; sem HTTPS tira Secure (e SameSite=None)."""
    parts = [p for p in (x.strip() for x in v.split(";")) if p]
    keep = [parts[0]] if parts else []
    for p in parts[1:]:
        lp = p.lower()
        if lp.startswith("domain="):
            continue
        if not secure and lp == "secure":
            continue
        if not secure and lp == "samesite=none":
            p = "SameSite=Lax"
        keep.append(p)
    return "; ".join(keep)


async def _tcp_ok(host: str, port: int, timeout: float = 6) -> bool:
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        w.close()
        return True
    except Exception:
        return False


async def _send(send, status: int, body: bytes, headers: Optional[list] = None):
    h = [(b"content-type", b"text/html; charset=utf-8"), (b"cache-control", b"no-store")]
    for k, v in headers or []:
        h.append((k.encode(), v.encode()))
    h.append((b"content-length", str(len(body)).encode()))
    await send({"type": "http.response.start", "status": status, "headers": h})
    await send({"type": "http.response.body", "body": body})
