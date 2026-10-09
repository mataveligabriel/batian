"""Túnel TCP temporário para programas que não rodam no navegador (Winbox do MikroTik, porta 8291).

O BastiON abre uma porta própria (WINBOX_PORTS, padrão 8300-8309) só para o IP de quem pediu. O Winbox no computador
do usuário conecta em BASTION:porta e o BastiON repassa a conexão até o equipamento — direto do servidor ou pela
cadeia de agentes (jump servers), sem expor o equipamento. A porta fecha sozinha depois de um tempo sem uso.
"""
import asyncio
import ipaddress
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

log = logging.getLogger("bastion.relay")

IDLE_SECONDS = int(os.environ.get("WINBOX_IDLE_MINUTES", "20")) * 60      # sem nenhuma conexão aberta
MAX_SECONDS = int(os.environ.get("WINBOX_MAX_HOURS", "8")) * 3600         # limite total de uma porta
BUF = 65536


def parse_ports(spec: str) -> List[int]:
    out: List[int] = []
    for part in (spec or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                out.extend(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.append(int(part))
    return [p for p in dict.fromkeys(out) if 1024 <= p <= 65535][:50]


def same_client(peer: str, allowed: str) -> bool:
    """O Winbox precisa sair do mesmo IP que abriu o BastiON no navegador (IPv4 mapeado em IPv6 também vale)."""
    try:
        a, b = ipaddress.ip_address(peer), ipaddress.ip_address(allowed)
        a = a.ipv4_mapped or a if a.version == 6 else a
        b = b.ipv4_mapped or b if b.version == 6 else b
        return a == b
    except ValueError:
        return peer == allowed


async def _pipe(reader, writer, touch: Callable[[int], None]):
    try:
        while True:
            data = await reader.read(BUF)
            if not data:
                break
            touch(len(data))
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


class RelaySession:
    def __init__(self, **kw):
        self.id: str = kw["id"]
        self.user_id: str = kw["user_id"]
        self.user_email: str = kw.get("user_email", "")
        self.port: int = kw["port"]
        self.host: str = kw["host"]
        self.tport: int = kw["tport"]
        self.label: str = kw.get("label", "")
        self.via: str = kw.get("via", "")
        self.agent_id: Optional[str] = kw.get("agent_id")
        self.device_id: Optional[str] = kw.get("device_id")
        self.allowed_ip: str = kw["allowed_ip"]
        self.server: Optional[asyncio.base_events.Server] = None
        self.wrapper = None                     # conexão SSH com a cadeia de agentes (aberta na 1ª conexão)
        self.lock = asyncio.Lock()
        self.created = time.time()
        self.last = time.time()
        self.active = 0
        self.conns = 0
        self.bytes = 0
        self.refused = 0
        self.history_id: Optional[str] = None

    def public(self) -> dict:
        return {"id": self.id, "port": self.port, "label": self.label, "via": self.via, "target": f"{self.host}:{self.tport}",
                "device_id": self.device_id, "agent_id": self.agent_id, "allowed_ip": self.allowed_ip,
                "user_email": self.user_email, "active": self.active, "connections": self.conns, "bytes": self.bytes,
                "refused": self.refused, "idle_seconds": int(time.time() - self.last),
                "created_at": datetime.fromtimestamp(self.created, timezone.utc).isoformat(),
                "closes_in": max(0, int(min(IDLE_SECONDS - (time.time() - self.last) if not self.active else IDLE_SECONDS,
                                            MAX_SECONDS - (time.time() - self.created))))}


class RelayManager:
    """resolve(agent_id) -> (hops, nome) · connect(hops) -> wrapper SSH conectado (com .conn do asyncssh)."""

    def __init__(self, resolve, connect, on_close=None, ports: Optional[List[int]] = None):
        self.resolve = resolve
        self.connect = connect
        self.on_close = on_close
        self.ports = ports if ports is not None else parse_ports(os.environ.get("WINBOX_PORTS", "8300-8309"))
        self.sessions: Dict[str, RelaySession] = {}
        self.by_port: Dict[int, str] = {}
        self._reaper: Optional[asyncio.Task] = None

    def start(self):
        if not self._reaper:
            self._reaper = asyncio.create_task(self._reap())

    def list(self, user: Optional[dict] = None) -> List[dict]:
        out = [s for s in self.sessions.values() if user is None or s.user_id == user["id"]]
        return [s.public() for s in sorted(out, key=lambda x: x.created)]

    async def open(self, user: dict, host: str, tport: int, allowed_ip: str, agent_id: Optional[str] = None,
                   device_id: Optional[str] = None, label: str = "") -> RelaySession:
        if not self.ports:
            raise RuntimeError("Túnel Winbox desligado: nenhuma porta em WINBOX_PORTS")
        # mesmo destino, mesmo usuário e mesmo IP: reaproveita a porta
        for s in self.sessions.values():
            if s.user_id == user["id"] and s.host == host and s.tport == tport and (s.agent_id or None) == (agent_id or None):
                s.allowed_ip, s.last = allowed_ip, time.time()
                return s
        via = "BastiON (direto)"
        if agent_id:
            _, via = await self.resolve(agent_id)          # valida a cadeia (e a VPN) antes de abrir a porta
        last_err = None
        for port in self.ports:
            if port in self.by_port:
                continue
            s = RelaySession(id=secrets.token_hex(8), user_id=user["id"], user_email=user.get("email", ""), port=port,
                             host=host, tport=tport, label=label or host, via=via, agent_id=agent_id,
                             device_id=device_id, allowed_ip=allowed_ip)
            try:
                s.server = await asyncio.start_server(lambda r, w, s=s: self._handle(s, r, w), host="0.0.0.0", port=port)
            except OSError as e:                         # porta ocupada por outro programa: tenta a próxima
                last_err = e
                continue
            self.sessions[s.id] = s
            self.by_port[port] = s.id
            self.start()
            log.info("Winbox: porta %s aberta para %s (%s) → %s:%s via %s", port, user.get("email"), allowed_ip, host, tport, via)
            return s
        raise RuntimeError(f"Todas as portas do túnel Winbox estão em uso ({self.ports[0]}–{self.ports[-1]}). "
                           "Feche um túnel aberto." + (f" ({last_err})" if last_err else ""))

    async def _target(self, s: RelaySession):
        if not s.agent_id:
            return await asyncio.wait_for(asyncio.open_connection(s.host, s.tport), timeout=10)
        async with s.lock:
            if s.wrapper is None or getattr(s.wrapper, "conn", None) is None:
                hops, _ = await self.resolve(s.agent_id)
                s.wrapper = await self.connect(hops)
        return await asyncio.wait_for(s.wrapper.conn.open_connection(s.host, s.tport), timeout=15)

    async def _handle(self, s: RelaySession, reader, writer):
        peer = (writer.get_extra_info("peername") or ("?",))[0]
        if not same_client(peer, s.allowed_ip):
            s.refused += 1
            log.warning("Winbox: conexão recusada na porta %s vinda de %s (liberado só para %s)", s.port, peer, s.allowed_ip)
            writer.close()
            return
        s.active += 1
        s.conns += 1
        s.last = time.time()

        def touch(n: int):
            s.bytes += n
            s.last = time.time()
        try:
            try:
                tr, tw = await self._target(s)
            except Exception as e:
                log.info("Winbox: %s:%s não respondeu via %s: %s", s.host, s.tport, s.via, e)
                s.wrapper = None
                writer.close()
                return
            await asyncio.gather(_pipe(reader, tw, touch), _pipe(tr, writer, touch))
        finally:
            s.active -= 1
            s.last = time.time()

    async def close(self, sid: str, reason: str = ""):
        s = self.sessions.pop(sid, None)
        if not s:
            return
        self.by_port.pop(s.port, None)
        try:
            if s.server:
                s.server.close()
        except Exception:
            pass
        try:
            if s.wrapper:
                await s.wrapper.close()
        except Exception:
            pass
        log.info("Winbox: porta %s fechada (%s)", s.port, reason or "fechada")
        if self.on_close:
            try:
                await self.on_close(s, reason)
            except Exception:
                pass

    async def _reap(self):
        while True:
            await asyncio.sleep(30)
            now = time.time()
            for s in list(self.sessions.values()):
                if now - s.created > MAX_SECONDS:
                    await self.close(s.id, "tempo máximo")
                elif not s.active and now - s.last > IDLE_SECONDS:
                    await self.close(s.id, "sem uso")
