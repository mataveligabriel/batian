"""Alerts (Telegram / webhook), config-backup helpers and the background scheduler."""
import asyncio
import difflib
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Callable, Awaitable, Optional

import httpx

import vault

logger = logging.getLogger("bastion.automation")

BACKUP_COMMANDS = {
    "cisco": "show running-config",
    "huawei": "display current-configuration",
    "mikrotik": "/export",
    "datacom": "show running-config",
    "zte": "show running-config",
    "ubiquiti": "cat /tmp/system.cfg",
    "linux": "",
    "other": "",
}

DEFAULTS = {
    "ping_enabled": True, "ping_interval_min": 5,
    "backup_enabled": True, "backup_hour": 3,
    "notify_agents": True, "notify_devices": False,
    "telegram_bot_token": "", "telegram_chat_id": "", "webhook_url": "",
}


async def get_settings(db) -> dict:
    doc = await db.config.find_one({"key": "automation"}, {"_id": 0}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if k != "key"}}


def public_settings(s: dict) -> dict:
    out = {k: v for k, v in s.items() if k != "telegram_bot_token"}
    out["has_telegram_token"] = bool(s.get("telegram_bot_token"))
    return out


def backup_command_for(dev: dict) -> str:
    return (dev.get("backup_command") or BACKUP_COMMANDS.get(dev.get("device_type") or "linux", "")).strip()


async def send_alert(db, title: str, text: str) -> dict:
    s = await get_settings(db)
    token = vault.decrypt(s.get("telegram_bot_token", ""))
    chat = (s.get("telegram_chat_id") or "").strip()
    hook = (s.get("webhook_url") or "").strip()
    results = {"telegram": None, "webhook": None}
    if not token and not hook:
        return {**results, "error": "Nenhum canal configurado"}
    async with httpx.AsyncClient(timeout=15) as client:
        if token and chat:
            try:
                r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                      json={"chat_id": chat, "text": f"{title}\n{text}"})
                results["telegram"] = "ok" if r.status_code == 200 else f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:
                results["telegram"] = f"erro: {e}"
        if hook:
            try:
                r = await client.post(hook, json={"text": f"*{title}*\n{text}", "title": title, "message": text})
                results["webhook"] = "ok" if r.status_code < 300 else f"HTTP {r.status_code}"
            except Exception as e:
                results["webhook"] = f"erro: {e}"
    await db.alerts.insert_one({"id": os.urandom(8).hex(), "title": title, "text": text,
                                "created_at": datetime.now(timezone.utc).isoformat(), "results": results})
    return results


async def store_backup(db, dev: dict, content: str, ok: bool, error: Optional[str] = None) -> dict:
    prev = await db.backups.find_one({"device_id": dev["id"], "ok": True}, {"_id": 0, "sha256": 1}, sort=[("created_at", -1)])
    sha = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest() if ok else ""
    doc = {
        "id": os.urandom(8).hex(), "device_id": dev["id"], "device_name": dev["name"],
        "device_type": dev.get("device_type", "linux"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "content": content if ok else "", "sha256": sha, "size": len(content) if ok else 0,
        "lines": content.count("\n") + 1 if ok and content else 0,
        "changed": bool(ok and (not prev or prev.get("sha256") != sha)),
        "ok": ok, "error": error,
    }
    await db.backups.insert_one(dict(doc))
    return doc


def unified_diff(a: str, b: str, label_a: str, label_b: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), fromfile=label_a, tofile=label_b))


class Scheduler:
    """Single background loop: periodic ping-through-chain with alerts, and daily config backup."""

    def __init__(self, db, ping_all: Callable[[], Awaitable[list]], backup_all: Callable[[], Awaitable[list]]):
        self.db = db
        self.ping_all = ping_all
        self.backup_all = backup_all
        self._task: Optional[asyncio.Task] = None
        self.last_ping: Optional[datetime] = None
        self.last_backup_day: Optional[str] = None
        self.busy = False

    def start(self):
        if not self._task:
            self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        await asyncio.sleep(10)
        while True:
            try:
                await self.tick()
            except Exception as e:
                logger.warning(f"scheduler tick failed: {e}")
            await asyncio.sleep(30)

    async def tick(self, force_ping: bool = False):
        s = await get_settings(self.db)
        now = datetime.now(timezone.utc)
        if s["ping_enabled"] and (force_ping or not self.last_ping or
                                  (now - self.last_ping).total_seconds() >= int(s["ping_interval_min"]) * 60):
            self.last_ping = now
            self.busy = True
            try:
                transitions = await self.ping_all()
                await self._notify(s, transitions)
            finally:
                self.busy = False
        local_hour = datetime.now().hour
        today = datetime.now().date().isoformat()
        if s["backup_enabled"] and local_hour == int(s["backup_hour"]) and self.last_backup_day != today:
            last = await self.db.config.find_one({"key": "backup_state"}) or {}
            if last.get("last_day") != today:
                self.last_backup_day = today
                await self.db.config.update_one({"key": "backup_state"}, {"$set": {"last_day": today}}, upsert=True)
                results = await self.backup_all()
                failed = [r for r in results if not r.get("ok")]
                changed = [r for r in results if r.get("ok") and r.get("changed")]
                if failed and s["notify_devices"]:
                    await send_alert(self.db, "⚠️ Backup de configuração com falhas",
                                     "\n".join(f"- {r['device_name']}: {r.get('error')}" for r in failed[:20]))
                logger.info(f"nightly backup: {len(results)} devices, {len(changed)} changed, {len(failed)} failed")

    async def _notify(self, s: dict, transitions: list):
        for t in transitions:
            if t["kind"] == "agent" and not s["notify_agents"]:
                continue
            if t["kind"] == "device" and not s["notify_devices"]:
                continue
            icon = "🔴" if t["status"] == "offline" else "🟢"
            label = "Agente" if t["kind"] == "agent" else "Equipamento"
            await send_alert(self.db, f"{icon} {label} {t['name']} está {t['status'].upper()}",
                             f"{t.get('detail', '')}\n{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
