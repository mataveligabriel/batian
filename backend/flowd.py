"""Coletor de flows do Bastion — roda no container "flow" (python flowd.py).

Escuta NetFlow v5/v9/IPFIX (UDP 2055) e sFlow v5 (UDP 6343), agrega em memória (flowagg) e grava no Mongo
(ver flowstore). Relê a configuração a cada 30 s: interfaces monitoradas, conteúdos, prefixos próprios, limites de
ataque e a base de ASN. Ataques detectados viram alerta (Telegram / webhook / push do app).
"""
import asyncio
import logging
import math
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import asndb  # noqa: E402
import flowagg  # noqa: E402
import flowproto  # noqa: E402
import flowstore  # noqa: E402

log = logging.getLogger("flowd")
RCVBUF = 32 * 1024 * 1024
MAX_EXPORTERS = 2000


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, cb, sflow: bool):
        self.cb, self.sflow = cb, sflow

    def datagram_received(self, data, addr):
        self.cb(data, addr, self.sflow)

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


class Collector:
    def __init__(self, db, alert=None):
        self.db = db
        self.alert = alert                  # async (db, title, text, push_url, push_tag)
        self.agg = flowagg.Aggregator()
        self.cache = flowproto.TemplateCache()
        self.cfg = dict(flowstore.DEFAULTS)
        self.exp: dict = {}
        self.transports = []
        self.ports = None
        self.asn = None
        self.asn_ver = None
        self.attack_ids: dict = {}
        self.attack_series: dict = {}
        self.labels: dict = {}              # chave -> "R1 · xe-0/0/1 (Trânsito X)"
        self.owners: dict = {}              # chave -> {owner_id}
        self._ret = None
        self._tpl_sig = None
        self._bg = set()

    # ---------- recepção ----------
    def on_datagram(self, data: bytes, addr, sflow: bool):
        ip = addr[0]
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        ov = int((self.cfg.get("sampling") or {}).get(ip) or 0)
        try:
            flows, info = flowproto.parse_datagram(data, ip, self.cache, ov, sflow=sflow)
        except Exception as e:     # pacote estranho não derruba o coletor
            st = self._exp(ip, "sflow" if sflow else "?")
            if st is not None:
                st["errors"] += 1
                st["last_error"] = str(e)[:160]
            return
        st = self._exp(info["exporter"], info["kind"])
        if st is None:
            return
        st["src"] = ip
        st["pkts"] += 1
        st["flows"] += len(flows)
        st["no_tpl"] += info["no_template"]
        if info.get("rate"):
            st["rate"] = info["rate"]
        st["last"] = time.time()
        add = self.agg.add
        for f in flows:
            add(f)

    def _exp(self, ip: str, kind: str):
        st = self.exp.get(ip)
        if st is None:
            if len(self.exp) >= MAX_EXPORTERS:
                return None
            st = self.exp[ip] = {"kind": kind, "pkts": 0, "flows": 0, "no_tpl": 0, "errors": 0, "rate": None,
                                 "first": time.time(), "last": time.time(), "last_error": None, "src": ip}
        if kind != "?":
            st["kind"] = kind
        return st

    async def bind(self, ports):
        for t in self.transports:
            t.close()
        self.transports = []
        loop = asyncio.get_running_loop()
        for port, sflow in ((ports[0], False), (ports[1], True)):
            try:
                t, _ = await loop.create_datagram_endpoint(lambda s=sflow: _Proto(self.on_datagram, s), sock=_make_sock(port))
                self.transports.append(t)
                log.info(f"escutando UDP {port} ({'sFlow' if sflow else 'NetFlow/IPFIX'})")
            except OSError as e:
                log.error(f"não consegui abrir a porta UDP {port}: {e}")
        self.ports = ports

    # ---------- configuração ----------
    async def reload(self):
        db = self.db
        cfg = await flowstore.get_settings(db)
        ifaces = await db.flow_ifaces.find({}, {"_id": 0}).to_list(10000)
        groups = await db.flow_groups.find({}, {"_id": 0, "id": 1, "asns": 1, "prefixes": 1}).to_list(5000)
        monitored, roles, labels, owners = {}, {}, {}, {}
        dev_ids = {i.get("device_id") for i in ifaces}
        devs = {d["id"]: d.get("name") for d in await db.devices.find({"id": {"$in": list(dev_ids)}}, {"_id": 0, "id": 1, "name": 1}).to_list(10000)}
        for i in ifaces:
            k = i["key"]
            monitored[(i["exporter"], int(i["if_index"]))] = k
            roles[k] = i.get("role") or "outro"
            labels[k] = f"{devs.get(i.get('device_id'), i['exporter'])} · {i.get('if_name') or i['if_index']}" + (
                f" ({i['label']})" if i.get("label") else "")
            owners.setdefault(k, set()).add(i.get("owner_id"))
        self.agg.configure(monitored, roles, groups, cfg.get("own_prefixes") or [], cfg.get("attack"),
                           cfg.get("ignore_prefixes") or [])
        self.labels, self.owners, self.cfg = labels, owners, cfg
        ports = (int(cfg["netflow_port"]), int(cfg["sflow_port"]))
        if ports != self.ports:
            await self.bind(ports)
        meta = await asndb.get_meta(db)
        if meta and meta.get("ver") != self.asn_ver:
            try:
                a = await asndb.load(db, meta)
                if a:
                    self.asn, self.asn_ver = a, meta["ver"]
                    self.agg.asn_lookup = a.lookup
                    log.info(f"base de ASN carregada ({len(a)} faixas)")
            except Exception as e:
                log.warning(f"base de ASN: {e}")
        ret = (cfg.get("retention_days"), cfg.get("hourly_days"))
        if ret != self._ret:
            await flowstore.ensure_indexes(db, cfg)
            self._ret = ret

    # ---------- gravação ----------
    async def save_templates(self):
        d = self.cache.dump()
        sig = (len(d["t"]), len(d["o"]), tuple(sorted(d["s"].items())), hash(str(d["t"])))
        if sig != self._tpl_sig:
            await self.db.flow_templates.replace_one({"_id": "cache"}, {"_id": "cache", **d, "at": _iso(time.time())}, upsert=True)
            self._tpl_sig = sig

    async def load_templates(self):
        doc = await self.db.flow_templates.find_one({"_id": "cache"})
        if doc:
            self.cache.load(doc)
            log.info(f"templates NetFlow v9/IPFIX recuperados: {len(self.cache.templates)}")

    async def write_exporters(self, span: float):
        seen = self.agg.take_seen()
        per = {}
        for (exp, idx), (bi, bo) in seen.items():
            per.setdefault(exp, {})[str(idx)] = [round(bi * 8 / span), round(bo * 8 / span)]
        now = time.time()
        for ip, st in self.exp.items():
            doc = {"_id": ip, "kind": st["kind"], "src": st["src"], "pps": round(st["pkts"] / span, 1),
                   "fps": round(st["flows"] / span, 1), "rate": st["rate"], "no_template": st["no_tpl"],
                   "errors": st["errors"], "last_error": st["last_error"], "last": _iso(st["last"]),
                   "first": _iso(st["first"]), "bidir": self.agg.is_bidir(ip), "at": _iso(now)}
            if ip in per:
                doc["ifs"] = per[ip]
            elif now - st["last"] < span * 2:
                doc["ifs"] = {}
            upd = {"$set": doc}
            await self.db.flow_exporters.update_one({"_id": ip}, upd, upsert=True)
            st["pkts"] = st["flows"] = st["no_tpl"] = 0

    async def tick10(self, now: float):
        live = self.agg.flush_live(10)
        await self.db.flow_live.replace_one({"_id": "live"}, {"_id": "live", "at": _iso(now), "ifaces": live}, upsert=True)
        events = self.agg.attack_tick(10, now)
        if events:
            await self.handle_attacks(events, now)

    async def tick60(self, now: float):
        docs = self.agg.flush_1m()
        if docs:
            ts = datetime.fromtimestamp(now - 60, timezone.utc)
            for d in docs:
                d["ts"] = ts
            await self.db.flow_1m.insert_many(docs, ordered=False)

    async def tick300(self, now: float):
        raw = self.agg.take_5m()
        if not raw:
            return
        docs = await asyncio.to_thread(flowagg.format_5m, raw)
        ts = datetime.fromtimestamp(now - 300, timezone.utc)
        for d in docs:
            d["ts"] = ts
        await self.db.flow_5m.insert_many(docs, ordered=False)

    async def tick_hour(self, now: float):
        await flowstore.rollup_hour(self.db, flowstore.hour_floor(now - 3600))

    # ---------- ataques ----------
    def _where(self, keys) -> str:
        return ", ".join(self.labels.get(k, k) for k in keys[:4]) + (f" e mais {len(keys) - 4}" if len(keys) > 4 else "")

    async def handle_attacks(self, events, now: float):
        starts = []
        for ev, a in events:
            v = a["victim"]
            owners = sorted({o for k in a["ifaces"] for o in self.owners.get(k, ()) if o})
            fields = {"type": a["type"], "peak_bps": a["peak_bps"], "peak_pps": a["peak_pps"], "cur_bps": a["cur_bps"],
                      "cur_pps": a["cur_pps"], "ifaces": a["ifaces"], "src_as": a["src_as"], "sport": a["sport"],
                      "dport": a["dport"], "proto": a["proto"], "owners": owners, "updated": _iso(now),
                      "if_labels": {k: self.labels.get(k, k) for k in a["ifaces"]}}
            # série própria do ataque (média de 30 s a cada 10 s): o gráfico do ataque não depende do top-K de 5 min
            ser = self.attack_series.setdefault(v, [])
            ser.append([int(now * 1000), round(a["cur_bps"]), round(a["cur_pps"])])
            if len(ser) > 2160:                     # 6 h a cada 10 s; depois disso, um ponto a cada 20 s
                del ser[:len(ser) - 2160:2]
            fields["series"] = ser
            if ev == "start":
                aid = uuid.uuid4().hex[:12]
                self.attack_ids[v] = aid
                await self.db.flow_attacks.insert_one({"id": aid, "victim": v, "status": "active",
                                                       "start": _iso(a["start"]), "end": None, **fields})
                starts.append(a)
            elif ev == "update" and v in self.attack_ids:
                await self.db.flow_attacks.update_one({"id": self.attack_ids[v]}, {"$set": fields})
            elif ev == "end" and v in self.attack_ids:
                aid = self.attack_ids.pop(v)
                self.attack_series.pop(v, None)
                await self.db.flow_attacks.update_one({"id": aid}, {"$set": {**fields, "status": "ended", "end": _iso(now)}})
                dur = max(0, now - a["start"])
                await self._alert("✅ Ataque encerrado: " + v,
                                  f"{a['type']} · durou {int(dur // 60)} min {int(dur % 60)} s · pico "
                                  f"{flowstore.fmt_bps(a['peak_bps'])} / {flowstore.fmt_pps(a['peak_pps'])}", f"ddos-{v}")
        if len(starts) > 3:
            top = sorted(starts, key=lambda a: -a["peak_bps"])
            await self._alert(f"🚨 Ataque DDoS em {len(starts)} IPs",
                              "; ".join(f"{a['victim']} ({a['type']}, {flowstore.fmt_bps(a['peak_bps'])})" for a in top[:5])
                              + (f" e mais {len(starts) - 5}" if len(starts) > 5 else ""), "ddos-multi")
        else:
            for a in starts:
                src = ", ".join(f"AS{x}" for x, _ in a["src_as"][:3] if x)
                await self._alert(f"🚨 Ataque DDoS: {a['victim']}",
                                  f"{a['type']} · {flowstore.fmt_bps(a['peak_bps'])} / {flowstore.fmt_pps(a['peak_pps'])}"
                                  f" · entrando por {self._where(a['ifaces'])}" + (f" · origem: {src}" if src else ""),
                                  f"ddos-{a['victim']}")

    async def _alert(self, title: str, text: str, tag: str):
        if not self.alert:
            return
        try:
            await self.alert(self.db, title, text, push_url="/flow?tab=ataques", push_tag=tag)
        except Exception as e:
            log.warning(f"alerta: {e}")

    # ---------- laço principal ----------
    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _safe(self, name, coro):
        try:
            await coro
        except Exception as e:
            log.exception(f"{name}: {e}")

    async def run(self):
        await self.db.flow_attacks.update_many({"status": "active"},
                                               {"$set": {"status": "ended", "end": _iso(time.time()), "note": "coletor reiniciado"}})
        await self._safe("templates", self.load_templates())
        await self._safe("config", self.reload())
        if self.ports is None:
            await self.bind((int(self.cfg["netflow_port"]), int(self.cfg["sflow_port"])))
        nxt = math.ceil(time.time() / 10) * 10
        while True:
            await asyncio.sleep(max(0.0, nxt - time.time()))
            now = nxt
            nxt += 10
            if time.time() - nxt > 10:            # atrasou demais (máquina travada): realinha
                nxt = math.ceil(time.time() / 10) * 10
            t = int(now)
            await self._safe("10s", self.tick10(now))
            if t % 60 == 0:
                await self._safe("1min", self.tick60(now))
            if t % 300 == 0:
                self._spawn(self._safe("5min", self.tick300(now)))
            if t % 30 == 0:
                await self._safe("status", self.write_exporters(30))
                await self._safe("templates", self.save_templates())
                await self._safe("config", self.reload())
            if t % 3600 == 120:
                self._spawn(self._safe("1h", self.tick_hour(now)))


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s flowd - %(message)s")
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
    await Collector(db, automation.send_alert).run()


if __name__ == "__main__":
    asyncio.run(main())
