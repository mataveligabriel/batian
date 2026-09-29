"""Alerts (Telegram / webhook), config-backup helpers and the background scheduler."""
import asyncio
import difflib
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Callable, Awaitable, List, Optional

import httpx

import webpush

import vault

logger = logging.getLogger("bastion.automation")

BACKUP_COMMANDS = {
    "cisco": "show running-config",
    "huawei": "display current-configuration",
    "mikrotik": "/export",
    "datacom": "show running-config",
    "zte": "show running-config",
    "juniper": "show configuration | display set",
    "ubiquiti": "cat /tmp/system.cfg",
    "linux": "",
    "other": "",
}

DEFAULTS = {
    "ping_enabled": True, "ping_interval_min": 5,
    "backup_enabled": True, "backup_hour": 3,
    "notify_agents": True, "notify_devices": False, "notify_config_changes": True,
    "daily_report_enabled": True, "daily_report_hour": 8,
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


async def send_alert(db, title: str, text: str, push_url: str = "/", push_tag: str = None) -> dict:
    s = await get_settings(db)
    token = vault.decrypt(s.get("telegram_bot_token", ""))
    chat = (s.get("telegram_chat_id") or "").strip()
    hook = (s.get("webhook_url") or "").strip()
    results = {"telegram": None, "webhook": None, "push": None}
    # push no celular/PC (PWA) vai para quem ativou "alertas neste aparelho", mesmo sem Telegram
    try:
        pr = await webpush.send(db, title, text, url=push_url, tag=push_tag)
        if pr["sent"] or pr["failed"]:
            results["push"] = f"{pr['sent']} aparelho(s)" + (f", {pr['failed']} falha(s)" if pr["failed"] else "")
    except Exception as e:
        results["push"] = f"erro: {e}"
    if not token and not hook:
        if results["push"]:
            await db.alerts.insert_one({"id": os.urandom(8).hex(), "title": title, "text": text,
                                        "created_at": datetime.now(timezone.utc).isoformat(), "results": results})
            return results
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


# Linhas que mudam sozinhas a cada coleta (carimbo de hora, "última alteração em…"): não contam como mudança.
_NOISE = re.compile(
    r"^\s*(?:"
    r"[!#]+\s*(?:last\s+(?:configuration|config|commit|change|changed)|nvram config last|time:|current time|generated|"
    r"exported|uptime|system time|clock)"           # Cisco/Huawei/Datacom/ZTE/Juniper ("## Last commit: …")
    r"|#\s*\w{3}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s+by\s+routeros"  # MikroTik antigo
    r"|#\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+by\s+routeros"  # MikroTik 7
    r"|ntp\s+clock-period"                          # Cisco
    r"|building configuration"
    r"|current configuration\s*:\s*\d+\s*bytes"
    r")", re.IGNORECASE)


def normalize_config(content: str) -> List[str]:
    return [ln.rstrip() for ln in (content or "").replace("\r", "").split("\n") if not _NOISE.match(ln)]


def _norm_sha(content: str) -> str:
    return hashlib.sha256("\n".join(normalize_config(content)).encode("utf-8", "replace")).hexdigest()


def diff_stats(old: str, new: str) -> dict:
    a, b = normalize_config(old), normalize_config(new)
    added = removed = 0
    for ln in difflib.unified_diff(a, b, lineterm="", n=0):
        if ln.startswith("+") and not ln.startswith("+++"):
            added += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            removed += 1
    return {"added": added, "removed": removed}


def diff_excerpt(old: str, new: str, max_lines: int = 20, width: int = 120) -> List[str]:
    out = []
    for ln in difflib.unified_diff(normalize_config(old), normalize_config(new), lineterm="", n=0):
        if ln.startswith(("+++", "---")):
            continue
        if ln.startswith("@@"):
            if out and out[-1] != "…":
                out.append("…")
            continue
        out.append(ln[:width])
        if len(out) >= max_lines:
            out.append("… (diff completo no Bastion)")
            break
    return out


async def store_backup(db, dev: dict, content: str, ok: bool, error: Optional[str] = None) -> dict:
    prev = await db.backups.find_one({"device_id": dev["id"], "ok": True},
                                     {"_id": 0, "id": 1, "sha256": 1, "norm_sha": 1, "created_at": 1}, sort=[("created_at", -1)])
    sha = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest() if ok else ""
    norm = _norm_sha(content) if ok else ""
    now = datetime.now(timezone.utc)
    changed, stats = False, None
    if ok:
        if not prev:
            changed = True
        elif prev.get("sha256") != sha:
            prev_norm = prev.get("norm_sha")
            prev_content = None
            if not prev_norm or prev_norm != norm:
                pc = await db.backups.find_one({"id": prev["id"]}, {"_id": 0, "content": 1})
                prev_content = (pc or {}).get("content") or ""
                prev_norm = prev_norm or _norm_sha(prev_content)
            changed = prev_norm != norm
            if changed:
                stats = diff_stats(prev_content if prev_content is not None else "", content)
    doc = {
        "id": os.urandom(8).hex(), "device_id": dev["id"], "device_name": dev["name"],
        "device_type": dev.get("device_type", "linux"),
        "created_at": now.isoformat(),
        "content": content if ok else "", "sha256": sha, "norm_sha": norm, "size": len(content) if ok else 0,
        "lines": content.count("\n") + 1 if ok and content else 0,
        "changed": changed, "first": bool(ok and not prev),
        "prev_id": prev["id"] if prev and ok else None, "prev_at": prev.get("created_at") if prev and ok else None,
        "diff_stats": stats,
        "ok": ok, "error": error, "file_path": None,
    }
    if ok:
        doc["file_path"] = write_backup_file(dev["name"], now, content)
    await db.backups.insert_one(dict(doc))
    return doc


_JUNOS_COMMIT = re.compile(r"^##\s*Last commit:\s*(.+?)\s+by\s+(\S+)", re.IGNORECASE | re.MULTILINE)


async def _who(db, device_id: str, since: str, until: str) -> List[str]:
    """Quem esteve no equipamento entre o backup anterior e este (terminal, lote, assistente)."""
    q = {"device_id": device_id, "started_at": {"$lte": until},
         "$or": [{"ended_at": None}, {"ended_at": {"$gte": since}}]}
    seen: dict = {}
    async for s in db.sessions.find(q, {"_id": 0}).sort("started_at", 1).limit(200):
        who = s.get("user_email") or "?"
        kind = {"terminal": "terminal", "batch": "lote", "ia": "assistente (leitura)",
                "ia-alteracao": "assistente (alteração)"}.get(s.get("kind"), s.get("kind") or "sessão")
        key = (who, kind)
        t = _fmt_local(s.get("started_at"))
        if key in seen:
            seen[key][1] += 1
        else:
            seen[key] = [t, 1]
    return [f"{w} — {k}, {n}x, primeira às {t}" if n > 1 else f"{w} — {k} às {t}" for (w, k), (t, n) in seen.items()]


def _fmt_local(iso: Optional[str]) -> str:
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d/%m %H:%M")
    except ValueError:
        return iso[:16]


async def notify_config_changes(db, results: List[dict], trigger: str) -> int:
    """Alerta (Telegram/webhook/push) para cada equipamento cuja configuração mudou desde o backup anterior."""
    s = await get_settings(db)
    if not s.get("notify_config_changes", True):
        return 0
    changed = [r for r in results if r.get("ok") and r.get("changed") and r.get("prev_id")]
    sent = 0
    for r in changed[:10]:
        try:
            cur = await db.backups.find_one({"id": r["id"]}, {"_id": 0, "content": 1})
            prev = await db.backups.find_one({"id": r["prev_id"]}, {"_id": 0, "content": 1})
            new_c, old_c = (cur or {}).get("content") or "", (prev or {}).get("content") or ""
            st = r.get("diff_stats") or diff_stats(old_c, new_c)
            who = await _who(db, r["device_id"], r.get("prev_at") or r["created_at"], r["created_at"])
            m = _JUNOS_COMMIT.search(new_c)
            lines = [f"+{st['added']} / -{st['removed']} linha(s) desde {_fmt_local(r.get('prev_at'))} · {trigger}"]
            if m:
                lines.append(f"Último commit (Junos): {m.group(2)} em {m.group(1)}")
            lines.append("Quem esteve no equipamento no período:")
            lines += [f"  • {w}" for w in who[:8]] or ["  • ninguém pelo Bastion (alteração direta no equipamento?)"]
            ex = diff_excerpt(old_c, new_c)
            if ex:
                lines.append("")
                lines += ex
            await send_alert(db, f"📝 Config alterada: {r['device_name']}", "\n".join(lines)[:3500],
                             push_url=f"/backups?device={r['device_id']}&diff={r['id']}", push_tag=f"cfg-{r['device_id']}")
            sent += 1
        except Exception as e:
            logger.warning(f"alerta de config alterada ({r.get('device_name')}): {e}")
    if len(changed) > 10:
        rest = changed[10:]
        await send_alert(db, f"📝 Config alterada em mais {len(rest)} equipamento(s)",
                         "\n".join(f"• {r['device_name']}" for r in rest[:40]), push_url="/backups")
        sent += 1
    return sent


def write_backup_file(device_name: str, when: datetime, content: str) -> Optional[str]:
    """Also persist the config as a plain file under BACKUP_DIR/<device>/<timestamp>.cfg (if configured)."""
    base = os.environ.get("BACKUP_DIR", "").strip()
    if not base:
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", device_name).strip("_") or "device"
    folder = os.path.join(base, safe)
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, when.strftime("%Y-%m-%d_%H-%M-%S") + ".cfg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path
    except Exception as e:
        logger.warning(f"não foi possível gravar backup em disco ({folder}): {e}")
        return None


def unified_diff(a: str, b: str, label_a: str, label_b: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), fromfile=label_a, tofile=label_b))


class Scheduler:
    """Single background loop: periodic ping-through-chain with alerts, and daily config backup."""

    def __init__(self, db, ping_all: Callable[[], Awaitable[list]], backup_all: Callable[[], Awaitable[list]],
                 daily_report: Optional[Callable[[], Awaitable[dict]]] = None):
        self.db = db
        self.ping_all = ping_all
        self.backup_all = backup_all
        self.daily_report = daily_report
        self.last_report_day: Optional[str] = None
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
        if (self.daily_report and s.get("daily_report_enabled", True) and local_hour == int(s.get("daily_report_hour", 8))
                and self.last_report_day != today):
            st = await self.db.config.find_one({"key": "report_state"}) or {}
            self.last_report_day = today
            if st.get("last_day") != today:
                await self.db.config.update_one({"key": "report_state"}, {"$set": {"last_day": today}}, upsert=True)
                try:
                    await self.daily_report()
                except Exception as e:
                    logger.warning(f"resumo diário falhou: {e}")
        if s["backup_enabled"] and local_hour == int(s["backup_hour"]) and self.last_backup_day != today:
            last = await self.db.config.find_one({"key": "backup_state"}) or {}
            if last.get("last_day") != today:
                self.last_backup_day = today
                await self.db.config.update_one({"key": "backup_state"}, {"$set": {"last_day": today}}, upsert=True)
                results = await self.backup_all()
                failed = [r for r in results if not r.get("ok")]
                changed = [r for r in results if r.get("ok") and r.get("changed")]
                try:
                    await notify_config_changes(self.db, results, "backup diário")
                except Exception as e:
                    logger.warning(f"alertas de config alterada: {e}")
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
