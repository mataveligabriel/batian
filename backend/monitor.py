"""Coletor de tráfego (weathermap) e alarmes de interface.

A cada `interval_sec` lê por SNMP só as interfaces necessárias:
  - as usadas nos links dos mapas (tráfego ao vivo)
  - as marcadas para monitorar (alarme de queda no Telegram)
Guarda bps ao vivo em memória, histórico no Mongo (expira sozinho) e dispara alertas de up/down
com confirmação (N leituras seguidas) para não alarmar por oscilação de uma leitura só.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Optional, Tuple

import snmp_service as S

logger = logging.getLogger("bastion.monitor")

DEFAULTS = {
    "enabled": True,
    "interval_sec": 30,
    "default_community": "",
    "confirm_polls": 2,     # leituras seguidas iguais para confirmar mudança de estado
    "alert_up": True,       # avisar também quando volta
    "history_days": 7,      # retenção do histórico de tráfego/óptica
}
DOWN_STATES = {"down", "lowerLayerDown", "notPresent", "dormant"}


async def get_settings(db) -> dict:
    doc = await db.config.find_one({"key": "monitor"}, {"_id": 0}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if k != "key"}}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_bps(bps: Optional[float]) -> str:
    if bps is None:
        return "—"
    for unit, div in (("Gbps", 1e9), ("Mbps", 1e6), ("Kbps", 1e3)):
        if bps >= div:
            return f"{bps / div:.2f} {unit}"
    return f"{bps:.0f} bps"


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


class Monitor:
    def __init__(self, db, client_for: Callable[[dict, dict], Awaitable], send_alert: Callable[..., Awaitable]):
        self.db = db
        self.client_for = client_for      # async (device, settings) -> SnmpClient | AgentSnmpClient
        self.send_alert = send_alert      # async (db, title, text)
        self.live: Dict[Tuple[str, int], dict] = {}
        self.prev: Dict[Tuple[str, int], Tuple[float, int, int]] = {}
        self.dev_errors: Dict[str, str] = {}
        self.last_tick: Optional[datetime] = None
        self.last_duration: float = 0
        self.busy = False
        self._task: Optional[asyncio.Task] = None

    # ---------- ciclo ----------
    def start(self):
        if not self._task:
            self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        await asyncio.sleep(8)
        while True:
            s = await get_settings(self.db)
            started = time.monotonic()
            if s["enabled"]:
                try:
                    await self.tick(s)
                except Exception as e:
                    logger.warning(f"monitor tick falhou: {e}")
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(3.0, max(10, int(s["interval_sec"])) - elapsed))

    async def targets(self) -> Dict[str, set]:
        """device_id -> índices de interface a coletar."""
        want: Dict[str, set] = {}
        async for m in self.db.maps.find({}, {"_id": 0, "nodes": 1, "links": 1}):
            node_dev = {n["id"]: n.get("device_id") for n in m.get("nodes", [])}
            for ln in m.get("links", []):
                for side, node_key in (("from_if", "from"), ("to_if", "to")):
                    iface, dev = ln.get(side), node_dev.get(ln.get(node_key))
                    if iface and dev and iface.get("index") is not None:
                        want.setdefault(dev, set()).add(int(iface["index"]))
        async for d in self.db.dashboards.find({}, {"_id": 0, "widgets": 1}):
            for w in d.get("widgets", []):
                if w.get("type") == "traffic" and w.get("device_id") and w.get("if_index") is not None:
                    want.setdefault(w["device_id"], set()).add(int(w["if_index"]))
        async for mon in self.db.if_monitors.find({}, {"_id": 0, "device_id": 1, "if_index": 1}):
            want.setdefault(mon["device_id"], set()).add(int(mon["if_index"]))
        return want

    async def tick(self, s: Optional[dict] = None):
        if self.busy:
            return
        self.busy = True
        started = time.monotonic()
        try:
            s = s or await get_settings(self.db)
            want = await self.targets()
            if not want:
                return
            devs = {d["id"]: d async for d in self.db.devices.find({"id": {"$in": list(want)}}, {"_id": 0})}
            sem = asyncio.Semaphore(10)
            samples = []

            async def one(dev_id: str, idxs: set):
                dev = devs.get(dev_id)
                if not dev:
                    return
                async with sem:
                    try:
                        client = await self.client_for(dev, s)
                        res = await S.poll_counters(client, sorted(idxs))
                        self.dev_errors.pop(dev_id, None)
                    except Exception as e:
                        self.dev_errors[dev_id] = str(e)
                        for i in idxs:
                            if (dev_id, i) in self.live:
                                self.live[(dev_id, i)]["stale"] = True
                        return
                t = time.time()
                for i, r in res.items():
                    key = (dev_id, i)
                    in_bps = out_bps = None
                    prev = self.prev.get(key)
                    if r["in_octets"] is not None and r["out_octets"] is not None:
                        if prev and t > prev[0]:
                            dt = t - prev[0]
                            d_in, d_out = r["in_octets"] - prev[1], r["out_octets"] - prev[2]
                            if d_in >= 0 and d_out >= 0:  # negativo = contador zerou (reboot)
                                in_bps, out_bps = d_in * 8 / dt, d_out * 8 / dt
                        self.prev[key] = (t, r["in_octets"], r["out_octets"])
                    old = self.live.get(key, {})
                    self.live[key] = {
                        "in_bps": in_bps if in_bps is not None else old.get("in_bps"),
                        "out_bps": out_bps if out_bps is not None else old.get("out_bps"),
                        "oper": r["oper"], "admin": r["admin"], "ts": t, "stale": False,
                    }
                    if in_bps is not None:
                        samples.append({"device_id": dev_id, "if_index": i, "ts": _now(),
                                        "in_bps": round(in_bps), "out_bps": round(out_bps)})
                await self._check_alarms(dev, res, s)

            await asyncio.gather(*[one(d, i) for d, i in want.items()])
            if samples:
                await self.db.if_samples.insert_many(samples)
            self.last_tick = _now()
        finally:
            self.last_duration = round(time.monotonic() - started, 2)
            self.busy = False

    # ---------- alarmes ----------
    async def _check_alarms(self, dev: dict, res: Dict[int, dict], s: dict):
        mons = [m async for m in self.db.if_monitors.find({"device_id": dev["id"], "if_index": {"$in": list(res)}}, {"_id": 0})]
        confirm = max(1, int(s.get("confirm_polls") or 1))
        for m in mons:
            r = res.get(int(m["if_index"]))
            if not r or r["oper"] == "unknown":
                continue
            observed = "up" if r["oper"] == "up" else ("down" if r["oper"] in DOWN_STATES else None)
            if observed is None:
                continue
            state = m.get("state")
            upd: dict = {"last_oper": r["oper"], "last_admin": r["admin"], "last_seen": _now().isoformat()}
            if state is None:  # primeira leitura: só registra, não alarma
                upd.update(state=observed, since=_now().isoformat(), pending=None, pending_count=0)
            elif observed == state:
                upd.update(pending=None, pending_count=0)
            else:
                count = (m.get("pending_count") or 0) + 1 if m.get("pending") == observed else 1
                if count >= confirm:
                    since = m.get("since")
                    dur = (_now() - datetime.fromisoformat(since)).total_seconds() if since else None
                    upd.update(state=observed, since=_now().isoformat(), pending=None, pending_count=0)
                    await self._alarm(dev, m, observed, r, dur, s)
                else:
                    upd.update(pending=observed, pending_count=count)
            await self.db.if_monitors.update_one({"device_id": m["device_id"], "if_index": m["if_index"]}, {"$set": upd})

    async def _alarm(self, dev: dict, m: dict, status: str, r: dict, prev_duration: Optional[float], s: dict):
        name = m.get("if_name") or f"if{m['if_index']}"
        alias = (m.get("alias") or "").strip()
        when = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        admin_down = status == "down" and r.get("admin") == "down"
        await self.db.if_events.insert_one({
            "id": os.urandom(8).hex(), "at": _now().isoformat(), "owner_id": dev.get("owner_id"),
            "device_id": dev["id"], "device_name": dev["name"], "if_index": m["if_index"], "if_name": name, "alias": alias,
            "status": status, "admin_down": admin_down, "prev_duration_s": int(prev_duration) if prev_duration else None,
        })
        if status == "down":
            title = f"🔴 Interface DOWN — {dev['name']} · {name}"
            detail = "desativada administrativamente (shutdown)" if admin_down else f"status: {r.get('oper')}"
            text = "\n".join(x for x in [alias and f"Descrição: {alias}", detail, when] if x)
        else:
            if not s.get("alert_up", True):
                return
            title = f"🟢 Interface UP — {dev['name']} · {name}"
            text = "\n".join(x for x in [alias and f"Descrição: {alias}",
                                         prev_duration and f"Ficou fora por {fmt_duration(prev_duration)}", when] if x)
        try:
            await self.send_alert(self.db, title, text)
        except Exception as e:
            logger.warning(f"falha ao enviar alerta: {e}")

    # ---------- leitura para a API ----------
    def iface_live(self, device_id: str, index: Optional[int]) -> Optional[dict]:
        if index is None:
            return None
        return self.live.get((device_id, int(index)))


class SshAgentPool:
    """Mantém uma conexão SSH por cadeia de agentes para rodar net-snmp sem reconectar a cada coleta."""

    def __init__(self, make_wrapper: Callable[[list], object]):
        self.make_wrapper = make_wrapper
        self.conns: Dict[tuple, object] = {}
        self.locks: Dict[tuple, asyncio.Lock] = {}

    async def runner(self, key: tuple, hops: list):
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            w = self.conns.get(key)
            if w is None:
                w = self.make_wrapper(hops)
                await w.connect()
                self.conns[key] = w

        async def run(cmd: str, timeout: int) -> str:
            try:
                res = await w.run_command(cmd, timeout=timeout)
            except Exception:
                self.conns.pop(key, None)
                try:
                    await w.close()
                except Exception:
                    pass
                raise
            out = res.get("stdout") or ""
            return out.decode("utf-8", "replace") if isinstance(out, bytes) else out
        return run

    async def close_all(self):
        for w in list(self.conns.values()):
            try:
                await w.close()
            except Exception:
                pass
        self.conns.clear()
