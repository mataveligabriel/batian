"""Coletor de syslog e traps SNMP do BastiON — roda no container "log" (python logd.py).

Escuta syslog (UDP 514) e traps SNMP v1/v2c (UDP 162). Cada mensagem é ligada ao equipamento pelo IP de origem
(ou pelo nome que vem na mensagem, ou por um vínculo manual em Logs → Fontes), gravada no Mongo (coleção "logs",
apagada sozinha depois de N dias) e passa pelas regras: BGP caiu, interface oscilando, LOS em massa na PON… que
abrem e resolvem incidentes na Central de Incidentes.
"""
import asyncio
import ipaddress
import logging
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import incidents  # noqa: E402
import logparse as lp  # noqa: E402

log = logging.getLogger("logd")
RCVBUF = 8 * 1024 * 1024
MAX_SOURCES = 5000
MAX_BUF = 20000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, cb, kind):
        self.cb, self.kind, self.transport = cb, kind, None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.cb(data, addr, self.kind, self.transport)

    def error_received(self, exc):
        pass


def _make_sock(port: int) -> socket.socket:
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("::", port))
    except OSError:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF)
    except OSError:
        pass
    s.setblocking(False)
    return s


def _ip(addr) -> str:
    h = addr[0]
    if h.startswith("::ffff:"):
        h = h[7:]
    return h


class LogCollector:
    def __init__(self, db, alert=None):
        self.db = db
        self.inc = incidents.Incidents(db, alert)
        self.inc.dm = incidents.make_dm(db)
        self.engine = lp.RuleEngine()
        self.cfg = dict(lp.DEFAULTS)
        self.buf: list = []
        self.actions: list = []
        self.src: dict = {}
        self.by_ip: dict = {}
        self.by_name: dict = {}
        self.dev: dict = {}
        self.links: dict = {}
        self.nets: list = []
        self.transports: dict = {}
        self.listen: dict = {}
        self.stats = {"syslog": 0, "trap": 0, "dropped": 0, "invalid": 0, "v3": 0, "community": 0}
        self.ttl_days = None

    # ---------- equipamentos ----------
    async def refresh_devices(self):
        by_ip, by_name, dev = {}, {}, {}
        async for d in self.db.devices.find({}, {"_id": 0, "id": 1, "name": 1, "host": 1, "owner_id": 1}):
            dev[d["id"]] = d
            h = (d.get("host") or "").strip()
            try:
                by_ip.setdefault(str(ipaddress.ip_address(h)), d["id"])
            except ValueError:
                pass
            if d.get("name"):
                by_name.setdefault(d["name"].strip().lower(), d["id"])
        links = {}
        async for s in self.db.log_sources.find({"linked_device_id": {"$nin": [None, ""]}}, {"_id": 0, "ip": 1, "linked_device_id": 1}):
            if s["linked_device_id"] in dev:
                links[s["ip"]] = s["linked_device_id"]
        nets = []
        async for u in self.db.users.find({"incident_nets.0": {"$exists": True}}, {"_id": 0, "id": 1, "incident_nets": 1}):
            for n in u.get("incident_nets") or []:
                try:
                    nets.append((ipaddress.ip_network(n, strict=False), u["id"]))
                except ValueError:
                    pass
        self.by_ip, self.by_name, self.dev, self.links, self.nets = by_ip, by_name, dev, links, nets
        for ip, s in self.src.items():        # recalcula as fontes já vistas
            s.pop("_res", None)

    def resolve(self, ip: str, agent: str, host_name: str) -> dict:
        """-> {device_id, device_name, owner_ids} da origem (cache por fonte + nome)."""
        # vínculo manual > IP do agente do trap > nome igual ao cadastrado (logs repassados por um rsyslog no jump)
        # > IP de origem
        did = self.links.get(ip) or (agent and self.by_ip.get(agent)) or \
            (host_name and self.by_name.get(host_name.lower())) or self.by_ip.get(ip)
        d = self.dev.get(did) if did else None
        owners = set()
        if d and d.get("owner_id"):
            owners.add(d["owner_id"])
        if self.nets:
            try:
                a = ipaddress.ip_address(agent or ip)
                if d:
                    try:
                        a = ipaddress.ip_address((d.get("host") or "").strip())
                    except ValueError:
                        pass
                for n, uid in self.nets:
                    if a.version == n.version and a in n:
                        owners.add(uid)
            except ValueError:
                pass
        return {"device_id": d["id"] if d else None, "device_name": d["name"] if d else "", "owner_ids": sorted(owners)}

    # ---------- recepção ----------
    def _source(self, ip: str, kind: str):
        s = self.src.get(ip)
        now = time.monotonic()
        rate = max(1, int(self.cfg.get("rate_per_source") or 200))
        if s is None:
            if len(self.src) >= MAX_SOURCES:
                self.stats["dropped"] += 1
                return None
            s = self.src[ip] = {"tok": float(rate * 2), "t": now, "count": 0, "dropped": 0, "kinds": set(),
                                "last": None, "host_name": "", "dirty": True}
        s["tok"] = min(rate * 2.0, s["tok"] + (now - s["t"]) * rate)
        s["t"] = now
        if s["tok"] < 1:
            s["dropped"] += 1
            s["dirty"] = True
            self.stats["dropped"] += 1
            return None
        s["tok"] -= 1
        s["count"] += 1
        s["kinds"].add(kind)
        s["last"] = _now()
        s["dirty"] = True
        return s

    def on_packet(self, data: bytes, addr, kind: str, transport=None):
        if not data or len(self.buf) > MAX_BUF:
            self.stats["dropped"] += 1
            return
        ip = _ip(addr)
        s = self._source(ip, kind)
        if s is None:
            return
        agent = ""
        if kind == "syslog":
            p = lp.parse_syslog(data)
            doc = {"kind": "syslog", "facility": p["facility"], "severity": p["severity"], "program": p["program"],
                   "host_name": p["host_name"], "msg": p["msg"]}
        else:
            try:
                t = lp.parse_trap(data)
            except lp.TrapError as e:
                self.stats["v3" if str(e) == "v3" else "invalid"] += 1
                return
            want = self.cfg.get("trap_community") or ""
            if want and t["community"] != want:
                self.stats["community"] += 1
                return
            if t["inform_reply"] and transport is not None:
                try:
                    transport.sendto(t["inform_reply"], addr)
                except OSError:
                    pass
            agent = t["agent"] if t["agent"] and t["agent"] != ip else ""
            doc = {"kind": "trap", "severity": t["severity"], "facility": None, "program": t["trap_name"],
                   "host_name": "", "msg": t["msg"], "trap_oid": t["trap_oid"], "version": t["version"],
                   "varbinds": t["varbinds"][:40]}
            if agent:
                doc["agent"] = agent
        self.stats[kind] += 1
        if doc["host_name"]:
            s["host_name"] = doc["host_name"]
        key = (agent, doc["host_name"])
        res = s.get("_res")
        if res is None or res[0] != key:
            res = (key, self.resolve(ip, agent, doc["host_name"]))
            s["_res"] = res
        r = res[1]
        s["device_id"] = r["device_id"]
        doc.update(host=ip, device_id=r["device_id"], device_name=r["device_name"], owner_ids=r["owner_ids"])
        acts, matched = self.engine.feed(doc)
        if acts:
            self.actions.extend(acts)
        store = kind == "trap" or doc["severity"] <= int(self.cfg.get("store_max_severity", 7))
        if not r["device_id"] and not self.cfg.get("store_unknown", True) and not matched:
            store = False
        if store:
            doc["at"] = _now()
            doc["id"] = os.urandom(8).hex()
            if matched:
                doc["rules"] = matched
            self.buf.append(doc)

    # ---------- banco ----------
    async def flush(self):
        if self.buf:
            batch, self.buf = self.buf, []
            try:
                await self.db.logs.insert_many(batch, ordered=False)
            except Exception as e:
                log.warning(f"gravar logs: {e.__class__.__name__}: {str(e)[:200]}")
        if self.actions:
            acts, self.actions = self.actions, []
            for a in acts:
                await self.apply(a)

    async def apply(self, a: dict):
        if a["type"] == "ok":
            ev = {"key": a["key"], "status": "ok", "severity": a["severity"], "title": a["title"]}
        else:
            samples = "\n".join(a["samples"][-5:])
            ev = {"key": a["key"], "status": "problem", "severity": a["severity"], "title": a["title"],
                  "description": f"Regra \"{a['rule']['name']}\" ({a['count']} ocorrência(s) na janela).\n\nÚltimas mensagens:\n{samples}"[:4000],
                  # com o equipamento identificado, o IP do incidente é o do cadastro (a origem pode ser o jump)
                  "host": (self.dev.get(a["device_id"]) or {}).get("host") or a["host"] if a["device_id"] else a["host"],
                  "host_name": a["host_name"], "device_id": a["device_id"],
                  "owner_ids": a["owner_ids"], "url": ""}
        try:
            await self.inc.ingest(a["kind"], ev)
        except Exception as e:
            log.warning(f"incidente: {e}")

    async def write_sources(self):
        from pymongo import UpdateOne
        ops = []
        for ip, s in self.src.items():
            if not s.get("dirty"):
                continue
            s["dirty"] = False
            d = self.dev.get(s.get("device_id")) if s.get("device_id") else None
            ops.append(UpdateOne({"ip": ip}, {"$set": {
                "ip": ip, "last_at": s["last"], "host_name": s["host_name"], "kinds": sorted(s["kinds"]),
                "device_id": s.get("device_id"), "device_name": (d or {}).get("name", ""),
                "count": s["count"], "dropped": s["dropped"]}}, upsert=True))
        if ops:
            await self.db.log_sources.bulk_write(ops, ordered=False)
        hits, self.engine.hits = self.engine.hits, {}
        for rid, n in hits.items():
            await self.db.log_rules.update_one({"id": rid}, {"$inc": {"hits": n}, "$set": {"last_hit": _now().isoformat()}})
        await self.db.config.update_one({"key": "logs_status"}, {"$set": {
            "key": "logs_status", "updated_at": _now().isoformat(), "listen": self.listen, "stats": self.stats,
            "sources": len(self.src), "rules": len(self.engine.rules), "open_keys": sum(1 for v in self.engine.state.values() if v["open"])}},
            upsert=True)

    async def ensure_indexes(self):
        days = max(1, int(self.cfg.get("retention_days") or 30))
        if days == self.ttl_days:
            return
        secs = days * 86400
        try:
            info = await self.db.logs.index_information()
            if "ttl_at" in info:
                if info["ttl_at"].get("expireAfterSeconds") != secs:
                    await self.db.command("collMod", "logs", index={"name": "ttl_at", "expireAfterSeconds": secs})
            else:
                await self.db.logs.create_index("at", name="ttl_at", expireAfterSeconds=secs)
            await self.db.logs.create_index([("device_id", 1), ("at", -1)])
            await self.db.logs.create_index([("host", 1), ("at", -1)])
            await self.db.logs.create_index([("owner_ids", 1), ("at", -1)])
            await self.db.logs.create_index([("rules", 1), ("at", -1)], sparse=True)
            await self.db.log_sources.create_index("ip", unique=True)
            self.ttl_days = days
        except Exception as e:
            log.warning(f"índices: {e}")

    async def seed_rules(self):
        if await self.db.config.find_one({"key": "logs_seeded"}):
            return
        if await self.db.log_rules.count_documents({}) == 0:
            for p in lp.PRESETS:
                r = lp.clean_rule(p)
                r.update(id=os.urandom(8).hex(), created_at=_now().isoformat(), hits=0, last_hit=None)
                await self.db.log_rules.insert_one(r)
        await self.db.config.update_one({"key": "logs_seeded"}, {"$set": {"key": "logs_seeded", "at": _now().isoformat()}}, upsert=True)

    async def reload(self):
        doc = await self.db.config.find_one({"key": "logs"}, {"_id": 0}) or {}
        self.cfg = {**lp.DEFAULTS, **{k: v for k, v in doc.items() if k in lp.DEFAULTS}}
        rules = await self.db.log_rules.find({}, {"_id": 0}).to_list(1000)
        self.engine.load(rules)
        await self.ensure_indexes()
        await self.bind()

    async def restore_open(self):
        """Incidentes de log ainda abertos voltam para o estado das regras (resolver sozinho continua valendo)."""
        async for i in self.db.incidents.find({"status": {"$in": ["open", "ack"]}, "key": {"$regex": "^log:"}},
                                              {"_id": 0, "key": 1, "last_seen": 1}):
            parts = i["key"].split(":", 2)
            try:
                last = datetime.fromisoformat(i.get("last_seen")).timestamp()
            except (TypeError, ValueError):
                last = time.time()
            if len(parts) == 3:
                self.engine.restore(i["key"], parts[1], last)

    # ---------- portas ----------
    async def bind(self):
        want = {}
        if self.cfg.get("enabled", True):
            want = {"syslog": int(self.cfg.get("syslog_port") or 514), "trap": int(self.cfg.get("trap_port") or 162)}
        loop = asyncio.get_running_loop()
        for kind in ("syslog", "trap"):
            cur = self.transports.get(kind)
            port = want.get(kind) or 0
            if cur and cur[0] == port:
                continue
            if cur:
                cur[1].close()
                self.transports.pop(kind, None)
            if not port:
                self.listen[kind] = {"port": 0, "ok": False, "error": "desligado"}
                continue
            try:
                tr, _ = await loop.create_datagram_endpoint(lambda k=kind: _Proto(self.on_packet, k), sock=_make_sock(port))
                self.transports[kind] = (port, tr)
                self.listen[kind] = {"port": port, "ok": True, "error": ""}
                log.info(f"{kind}: escutando UDP {port}")
            except OSError as e:
                self.listen[kind] = {"port": port, "ok": False, "error": str(e)[:200]}
                log.warning(f"{kind}: não abriu a UDP {port}: {e}")

    # ---------- laço ----------
    async def _safe(self, name, coro):
        try:
            await coro
        except Exception as e:
            log.exception(f"{name}: {e}")

    async def run(self):
        await self._safe("regras", self.seed_rules())
        await self._safe("equipamentos", self.refresh_devices())
        await self._safe("config", self.reload())
        await self._safe("abertos", self.restore_open())
        n = 0
        while True:
            await asyncio.sleep(1)
            n += 1
            await self._safe("flush", self.flush())
            if n % 30 == 0:
                await self._safe("config", self.reload())
                await self._safe("fontes", self.write_sources())
            if n % 60 == 0:
                await self._safe("equipamentos", self.refresh_devices())
                self.actions.extend(self.engine.sweep())


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s logd - %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # não grava o token do Telegram no log
    from motor.motor_asyncio import AsyncIOMotorClient
    import automation
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    for attempt in range(60):
        try:
            await client.admin.command("ping")
            break
        except Exception as e:
            log.warning(f"MongoDB indisponível ({e.__class__.__name__}), tentativa {attempt + 1}/60…")
            await asyncio.sleep(2)
    await LogCollector(db, automation.send_alert).run()


if __name__ == "__main__":
    asyncio.run(main())
