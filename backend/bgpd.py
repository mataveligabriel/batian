"""Speaker BGP mínimo do Bastion — roda no container "bgp" (python bgpd.py).

Só anuncia: abre iBGP com as bordas cadastradas e anuncia os /32 das mitigações ativas (blackhole) com next-hop
de descarte, community e NO_EXPORT. Tudo que a borda manda é lido e descartado (a borda deve ter export deny).
Implementa o necessário da RFC 4271 (OPEN, KEEPALIVE, UPDATE, NOTIFICATION), capacidades MP-BGP IPv4 unicast,
route-refresh (RFC 2918) e AS de 4 bytes (RFC 6793).
"""
import asyncio
import ipaddress
import logging
import os
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import mitigation  # noqa: E402

log = logging.getLogger("bgpd")
MARKER = b"\xff" * 16
OPEN, UPDATE, NOTIFICATION, KEEPALIVE, ROUTE_REFRESH = 1, 2, 3, 4, 5
AS_TRANS = 23456
NO_EXPORT = 0xFFFFFF01
BGP_PORT = int(os.environ.get("BGP_PORT", "179"))
MAX_MSG = 4096
NOTIF_TEXT = {1: "erro no cabeçalho", 2: "erro no OPEN", 3: "erro no UPDATE", 4: "hold timer expirou",
              5: "erro na máquina de estados", 6: "sessão encerrada pelo vizinho (Cease)"}
OPEN_SUB = {2: "AS do vizinho não confere", 3: "Router-ID inválido", 6: "hold time inválido", 7: "capacidade não suportada"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ mensagens
def msg(mtype: int, body: bytes = b"") -> bytes:
    return MARKER + struct.pack("!HB", 19 + len(body), mtype) + body


def build_open(local_as: int, hold: int, router_id: str) -> bytes:
    caps = (bytes([1, 4]) + struct.pack("!HBB", 1, 0, 1)          # MP-BGP: IPv4 unicast
            + bytes([2, 0])                                         # route refresh
            + bytes([65, 4]) + struct.pack("!I", local_as))         # AS de 4 bytes
    opt = bytes([2, len(caps)]) + caps
    body = struct.pack("!BHH", 4, local_as if local_as <= 65535 else AS_TRANS, hold)
    body += ipaddress.IPv4Address(router_id).packed + bytes([len(opt)]) + opt
    return msg(OPEN, body)


def parse_open(body: bytes) -> dict:
    if len(body) < 10:
        raise ValueError("OPEN curto")
    ver, my_as, hold = struct.unpack("!BHH", body[:5])
    rid = str(ipaddress.IPv4Address(body[5:9]))
    optlen = body[9]
    opt = body[10:10 + optlen]
    caps: Dict[int, bytes] = {}
    i = 0
    while i + 2 <= len(opt):
        ptype, plen = opt[i], opt[i + 1]
        val = opt[i + 2:i + 2 + plen]
        if ptype == 2:
            j = 0
            while j + 2 <= len(val):
                c, cl = val[j], val[j + 1]
                caps[c] = val[j + 2:j + 2 + cl]
                j += 2 + cl
        i += 2 + plen
    as4 = struct.unpack("!I", caps[65])[0] if 65 in caps and len(caps[65]) == 4 else None
    return {"version": ver, "as": as4 if as4 is not None else my_as, "hold": hold, "router_id": rid, "caps": caps}


def _prefix_bytes(p: str) -> bytes:
    n = ipaddress.IPv4Network(p)
    nbytes = (n.prefixlen + 7) // 8
    return bytes([n.prefixlen]) + n.network_address.packed[:nbytes]


def _attr(flags: int, code: int, value: bytes) -> bytes:
    if len(value) > 255:
        return bytes([flags | 0x10, code]) + struct.pack("!H", len(value)) + value
    return bytes([flags, code, len(value)]) + value


def path_attrs(cfg: dict, ibgp: bool, as4: bool) -> bytes:
    a = _attr(0x40, 1, b"\x00")                                                  # ORIGIN IGP
    if ibgp:
        a += _attr(0x40, 2, b"")                                                 # AS_PATH vazio
    else:
        asn = cfg["local_as"]
        seg = struct.pack("!BBI", 2, 1, asn) if as4 else struct.pack("!BBH", 2, 1, asn if asn <= 65535 else AS_TRANS)
        a += _attr(0x40, 2, seg)
    a += _attr(0x40, 3, ipaddress.IPv4Address(cfg["next_hop"]).packed)          # NEXT_HOP
    if ibgp:
        a += _attr(0x40, 5, struct.pack("!I", int(cfg.get("local_pref") or 100)))  # LOCAL_PREF
    comms, large = [], []
    for c in cfg.get("communities") or []:
        parts = [int(x) for x in c.split(":")]
        if len(parts) == 3:
            large.append(parts)
        else:
            comms.append((parts[0] << 16) | parts[1])
    if cfg.get("no_export", True):
        comms.append(NO_EXPORT)
    if comms:
        a += _attr(0xC0, 8, b"".join(struct.pack("!I", c) for c in comms))      # COMMUNITIES
    if large:
        a += _attr(0xC0, 32, b"".join(struct.pack("!III", *x) for x in large))  # LARGE_COMMUNITY (RFC 8092)
    return a


def build_updates(announce: List[str], withdraw: List[str], attrs: bytes) -> List[bytes]:
    """Divide em mensagens de até 4096 bytes."""
    out = []
    wd = [_prefix_bytes(p) for p in sorted(withdraw)]
    while wd:
        chunk, size = [], 0
        while wd and 19 + 4 + size + len(wd[0]) <= MAX_MSG:
            size += len(wd[0])
            chunk.append(wd.pop(0))
        blob = b"".join(chunk)
        out.append(msg(UPDATE, struct.pack("!H", len(blob)) + blob + struct.pack("!H", 0)))
    nl = [_prefix_bytes(p) for p in sorted(announce)]
    while nl:
        chunk, size = [], 0
        while nl and 19 + 4 + len(attrs) + size + len(nl[0]) <= MAX_MSG:
            size += len(nl[0])
            chunk.append(nl.pop(0))
        out.append(msg(UPDATE, struct.pack("!H", 0) + struct.pack("!H", len(attrs)) + attrs + b"".join(chunk)))
    return out


def notification(code: int, sub: int = 0, data: bytes = b"") -> bytes:
    return msg(NOTIFICATION, bytes([code, sub]) + data)


class BgpClosed(Exception):
    pass


async def read_msg(reader: asyncio.StreamReader, timeout: Optional[float]) -> Tuple[int, bytes]:
    head = await asyncio.wait_for(reader.readexactly(19), timeout)
    if head[:16] != MARKER:
        raise BgpClosed("cabeçalho BGP inválido")
    length, mtype = struct.unpack("!HB", head[16:])
    if not 19 <= length <= 65535:
        raise BgpClosed("tamanho de mensagem inválido")
    body = await asyncio.wait_for(reader.readexactly(length - 19), timeout) if length > 19 else b""
    return mtype, body


# ------------------------------------------------------------------ sessão
class Peer:
    def __init__(self, daemon: "BgpDaemon", conf: dict):
        self.d = daemon
        self.conf = conf
        self.state = "Idle"
        self.since = _now()
        self.last_error: Optional[str] = None
        self.remote_id: Optional[str] = None
        self.hold = 0
        self.adv: Set[str] = set()
        self.task: Optional[asyncio.Task] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.stop = False
        self.refresh = False
        self.msgs_in = 0
        self.established_at: Optional[str] = None

    def set_state(self, st: str):
        if st != self.state:
            self.state, self.since = st, _now()
            log.info(f"{self.conf['ip']}: {st}")

    def status(self) -> dict:
        return {"id": self.conf["id"], "ip": self.conf["ip"], "name": self.conf.get("name"), "state": self.state,
                "since": self.since, "last_error": self.last_error, "remote_id": self.remote_id, "hold": self.hold,
                "advertised": sorted(self.adv), "established_at": self.established_at}

    async def run(self):
        backoff = 5
        while not self.stop:
            try:
                await self.session()
                backoff = 5
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = str(e) or e.__class__.__name__
                log.warning(f"{self.conf['ip']}: {self.last_error}")
            finally:
                await self.close()
                self.adv = set()
                self.established_at = None
            if self.stop:
                break
            self.set_state("Idle")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def close(self):
        w, self.writer = self.writer, None
        if w:
            try:
                w.close()
                await asyncio.wait_for(w.wait_closed(), 3)
            except Exception:
                pass

    async def send(self, data: bytes):
        if not self.writer:
            raise BgpClosed("sem conexão")
        self.writer.write(data)
        await self.writer.drain()

    async def session(self):
        cfg = self.d.cfg
        ip, ras = self.conf["ip"], int(self.conf.get("remote_as") or cfg["local_as"])
        self.set_state("Connect")
        local = (cfg["local_address"], 0) if cfg.get("local_address") else None
        try:
            reader, self.writer = await asyncio.wait_for(asyncio.open_connection(ip, BGP_PORT, local_addr=local), 10)
        except (OSError, asyncio.TimeoutError) as e:
            raise BgpClosed(f"sem conexão TCP em {ip}:{BGP_PORT} ({e.__class__.__name__}: {e}) — confira rota/ACL e o peer na borda")
        await self.send(build_open(cfg["local_as"], cfg["hold_time"], cfg["router_id"]))
        self.set_state("OpenSent")
        mtype, body = await read_msg(reader, 30)
        if mtype == NOTIFICATION:
            raise BgpClosed(self._notif_text(body))
        if mtype != OPEN:
            await self.send(notification(5))
            raise BgpClosed("vizinho não respondeu com OPEN")
        o = parse_open(body)
        if o["as"] != ras:
            await self.send(notification(2, 2))
            raise BgpClosed(f"AS do vizinho é {o['as']}, esperado {ras}")
        if o["hold"] and o["hold"] < 3:
            await self.send(notification(2, 6))
            raise BgpClosed("hold time do vizinho inválido")
        self.remote_id = o["router_id"]
        as4 = 65 in o["caps"]
        self.hold = min(cfg["hold_time"], o["hold"]) if o["hold"] else 0
        await self.send(msg(KEEPALIVE))
        self.set_state("OpenConfirm")
        while True:
            mtype, body = await read_msg(reader, self.hold or 90)
            if mtype == KEEPALIVE:
                break
            if mtype == NOTIFICATION:
                raise BgpClosed(self._notif_text(body))
        self.set_state("Established")
        self.last_error = None
        self.established_at = _now()
        ibgp = ras == cfg["local_as"]
        attrs = path_attrs(cfg, ibgp, as4)
        last_rx = last_tx = time.monotonic()
        rx_task = asyncio.create_task(read_msg(reader, None))
        try:
            while not self.stop:
                # anúncios pendentes
                want = set(self.d.desired)
                if self.refresh:
                    self.refresh, self.adv = False, set()
                if want != self.adv:
                    for m in build_updates(sorted(want - self.adv), sorted(self.adv - want), attrs):
                        await self.send(m)
                    self.adv = want
                    last_tx = time.monotonic()
                    self.d.dirty = True
                if self.hold and time.monotonic() - last_tx >= self.hold / 3:
                    await self.send(msg(KEEPALIVE))
                    last_tx = time.monotonic()
                done, _ = await asyncio.wait({rx_task}, timeout=1)
                if rx_task in done:
                    mtype, body = rx_task.result()
                    last_rx = time.monotonic()
                    self.msgs_in += 1
                    if mtype == NOTIFICATION:
                        raise BgpClosed(self._notif_text(body))
                    if mtype == ROUTE_REFRESH:
                        self.refresh = True
                    rx_task = asyncio.create_task(read_msg(reader, None))   # UPDATE/KEEPALIVE: só lê e descarta
                if self.hold and time.monotonic() - last_rx > self.hold:
                    await self.send(notification(4))
                    raise BgpClosed("hold timer expirou (a borda parou de responder)")
            await self.send(notification(6, 2))       # Cease / administrative shutdown
        finally:
            rx_task.cancel()

    @staticmethod
    def _notif_text(body: bytes) -> str:
        code = body[0] if body else 0
        sub = body[1] if len(body) > 1 else 0
        txt = NOTIF_TEXT.get(code, f"código {code}")
        if code == 2 and sub in OPEN_SUB:
            txt += f": {OPEN_SUB[sub]}"
        elif sub:
            txt += f" (sub {sub})"
        return f"a borda encerrou a sessão — {txt}"


# ------------------------------------------------------------------ daemon
class BgpDaemon:
    def __init__(self, db, send_alert=None):
        self.db = db
        self.send_alert = send_alert
        self.cfg: dict = dict(mitigation.DEFAULTS)
        self.peers: Dict[str, Peer] = {}
        self.desired: Set[str] = set()
        self.dirty = True
        self._sig = None

    def _peer_sig(self, cfg: dict, p: dict) -> tuple:
        return (p["ip"], int(p.get("remote_as") or cfg["local_as"]), cfg["local_as"], cfg["router_id"], cfg["local_address"],
                cfg["hold_time"], cfg["next_hop"], tuple(cfg["communities"]), cfg["no_export"], cfg["local_pref"])

    async def reconcile_peers(self):
        cfg = self.cfg
        want = {}
        if cfg["enabled"] and cfg["local_as"] and cfg["router_id"]:
            want = {p["id"]: p for p in cfg["peers"] if p.get("enabled", True)}
        for pid in list(self.peers):
            p = self.peers[pid]
            if pid not in want or self._peer_sig(cfg, want[pid]) != p.sig:
                p.stop = True
                if p.task:
                    p.task.cancel()
                await p.close()
                del self.peers[pid]
                self.dirty = True
        for pid, pc in want.items():
            if pid not in self.peers:
                p = Peer(self, pc)
                p.sig = self._peer_sig(cfg, pc)
                p.task = asyncio.create_task(p.run())
                self.peers[pid] = p
                self.dirty = True

    async def tick(self):
        self.cfg = await mitigation.get_settings(self.db)
        now = datetime.now(timezone.utc)
        active = await self.db.flow_mitigations.find({"status": "active"}, {"_id": 0}).to_list(1000)
        desired = set()
        for m in active:
            if datetime.fromisoformat(m["expires_at"]) <= now:
                r = await self.db.flow_mitigations.update_one({"id": m["id"], "status": "active"}, {"$set": {"status": "expired", "ended_at": now.isoformat()}})
                if r.modified_count and self.send_alert:
                    try:
                        await self.send_alert(self.db, f"🟢 Mitigação encerrada: {m['prefix']}",
                                              f"O prazo acabou; o blackhole foi retirado das bordas.\nAtivada por {m.get('created_by')} em "
                                              f"{datetime.fromisoformat(m['created_at']).astimezone().strftime('%d/%m %H:%M')}.",
                                              push_url="/flow?tab=mitigacao")
                    except Exception as e:
                        log.warning(f"alerta: {e}")
                continue
            desired.add(m["prefix"])
        if not self.cfg["enabled"]:
            desired = set()
        if desired != self.desired:
            self.desired = desired
            self.dirty = True
        await self.reconcile_peers()

    async def publish(self, force=False):
        doc = {"_id": "status", "at": _now(), "enabled": self.cfg["enabled"], "desired": sorted(self.desired),
               "peers": [p.status() for p in self.peers.values()]}
        await self.db.bgp_status.replace_one({"_id": "status"}, doc, upsert=True)
        self.dirty = False

    async def run(self):
        log.info("bgpd iniciado")
        n = 0
        while True:
            try:
                await self.tick()
                await self.publish()
            except Exception as e:
                log.exception(f"tick: {e}")
            n += 1
            await asyncio.sleep(2)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s bgpd - %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from motor.motor_asyncio import AsyncIOMotorClient
    import automation
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    for _ in range(60):
        try:
            await client.admin.command("ping")
            break
        except Exception:
            await asyncio.sleep(2)
    await BgpDaemon(db, automation.send_alert).run()


if __name__ == "__main__":
    asyncio.run(main())
