"""Central de incidentes do NOC.

Junta num lugar só os alarmes do Zabbix, do LibreNMS, de qualquer sistema que mande um webhook e do próprio BastiON
(equipamento/agente fora, interface caiu). Cada problema vira um incidente numerado:

- o mesmo alarme repetido não abre outro incidente (soma no contador e na linha do tempo);
- quando a origem avisa que normalizou, o incidente é resolvido sozinho (com a duração);
- o NOC reconhece, assume, comenta e resolve pela tela ou pelos botões do Telegram.

Incidentes são da equipe (não por usuário): todo mundo com o módulo "Incidentes" vê os mesmos.
"""
import os
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from pymongo import ReturnDocument

SEVERITIES = ["info", "low", "medium", "high", "critical"]
SEV_LABEL = {"info": "Info", "low": "Baixa", "medium": "Média", "high": "Alta", "critical": "Crítica"}
SEV_ICON = {"info": "ℹ️", "low": "🔵", "medium": "🟡", "high": "🟠", "critical": "🔴"}
SOURCES = {"zabbix": "Zabbix", "librenms": "LibreNMS", "generic": "Webhook", "bastion": "BastiON", "manual": "Manual"}
STATUSES = ("open", "ack", "resolved")

DEFAULTS = {
    "notify_min_severity": "high",     # a partir de qual severidade avisa no Telegram/push
    "notify_resolved": True,           # avisa quando resolve sozinho
    "internal": True,                  # cria incidentes dos alarmes do próprio BastiON
    "notify_internal": False,          # o BastiON já avisa esses alarmes do jeito de sempre
    "token": "",
}

# Zabbix: Not classified, Information, Warning, Average, High, Disaster
_ZBX_SEV = {"not classified": "info", "information": "info", "warning": "low", "average": "medium",
            "high": "high", "disaster": "critical", "0": "info", "1": "info", "2": "low", "3": "medium", "4": "high",
            "5": "critical"}
_LNMS_SEV = {"ok": "info", "info": "info", "notice": "low", "warning": "medium", "critical": "critical", "error": "high"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_sev(v, table=None) -> str:
    s = str(v or "").strip().lower()
    if table and s in table:
        return table[s]
    if s in SEVERITIES:
        return s
    return {"crit": "critical", "critica": "critical", "crítica": "critical", "alta": "high", "media": "medium",
            "média": "medium", "baixa": "low", "warn": "medium", "disaster": "critical"}.get(s, "medium")


def sev_rank(s: str) -> int:
    return SEVERITIES.index(s) if s in SEVERITIES else 2


def _txt(v, n=500) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v if v is not None else "")).strip()[:n]


def parse_event(source: str, body: dict) -> dict:
    """Converte o JSON de cada origem para o formato interno: {key, status, severity, title, host, description, url}."""
    b = {str(k).lower(): v for k, v in (body or {}).items()}
    if source == "zabbix":
        # media type webhook do Zabbix (script em Configurações → Integrações): event_value 1 = problema, 0 = resolvido
        val = str(b.get("event_value", b.get("value", "1"))).strip()
        status = "ok" if val in ("0", "ok", "resolved") or str(b.get("event_status", "")).lower() == "resolved" else "problem"
        return {"key": f"zabbix:{_txt(b.get('event_id') or b.get('eventid'), 80) or _txt(b.get('trigger'), 200)}",
                "status": status, "severity": norm_sev(b.get("severity"), _ZBX_SEV),
                "title": _txt(b.get("trigger") or b.get("subject") or b.get("name"), 300) or "Alarme do Zabbix",
                "host": _txt(b.get("host_ip") or b.get("host"), 120), "host_name": _txt(b.get("host"), 120),
                "description": _txt(b.get("message") or b.get("opdata"), 4000), "url": _txt(b.get("url"), 500)}
    if source == "librenms":
        # API transport do LibreNMS: state 0 = recuperado, 1 = alerta, 2 = reconhecido, 3 = piorou, 4 = melhorou
        st = str(b.get("state", "1")).strip()
        return {"key": f"librenms:{_txt(b.get('id') or b.get('alert_id'), 80) or _txt(b.get('hostname'), 100) + ':' + _txt(b.get('rule') or b.get('title'), 150)}",
                "status": "ok" if st == "0" else "problem", "severity": norm_sev(b.get("severity"), _LNMS_SEV),
                "title": _txt(b.get("title") or b.get("rule") or b.get("name"), 300) or "Alarme do LibreNMS",
                "host": _txt(b.get("hostname") or b.get("host"), 120), "host_name": _txt(b.get("sysname") or b.get("hostname"), 120),
                "description": _txt(b.get("msg") or b.get("message"), 4000), "url": _txt(b.get("url"), 500)}
    # genérico: {key, status (problem|ok), severity, title, host, description, url}
    title = _txt(b.get("title") or b.get("subject") or b.get("text"), 300)
    key = _txt(b.get("key") or b.get("id"), 200) or f"{_txt(b.get('host'), 100)}:{title}"
    st = str(b.get("status", "problem")).lower()
    return {"key": f"generic:{key}", "status": "ok" if st in ("ok", "resolved", "recovery", "0", "up") else "problem",
            "severity": norm_sev(b.get("severity")), "title": title or "Alarme",
            "host": _txt(b.get("host"), 120), "host_name": _txt(b.get("host_name") or b.get("host"), 120),
            "description": _txt(b.get("description") or b.get("message"), 4000), "url": _txt(b.get("url"), 500)}


def fmt_dur(seconds: float) -> str:
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{d}d {h}h {m}min" if d else f"{h}h {m}min" if h else f"{m}min {s}s" if m else f"{s}s"


def duration(inc: dict) -> float:
    end = inc.get("resolved_at") or now_iso()
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(inc["created_at"])).total_seconds()
    except Exception:
        return 0


class Incidents:
    def __init__(self, db, alert: Optional[Callable[..., Awaitable]] = None):
        self.db = db
        self.alert = alert                  # automation.send_alert(db, title, text, push_url, push_tag, buttons)

    async def settings(self) -> dict:
        doc = await self.db.config.find_one({"key": "incidents"}, {"_id": 0}) or {}
        return {**DEFAULTS, **{k: v for k, v in doc.items() if k != "key"}}

    async def _number(self) -> int:
        r = await self.db.counters.find_one_and_update({"_id": "incidents"}, {"$inc": {"n": 1}}, upsert=True,
                                                       return_document=ReturnDocument.AFTER)
        return int(r["n"])

    async def _device(self, host: str, name: str = "") -> Optional[dict]:
        cands = [x for x in {host, name} if x]
        if not cands:
            return None
        return await self.db.devices.find_one({"$or": [{"host": {"$in": cands}}, {"name": {"$in": cands}}]},
                                              {"_id": 0, "id": 1, "name": 1, "host": 1})

    async def _notify(self, inc: dict, kind: str, s: dict):
        if not self.alert:
            return
        if inc["source"] == "bastion" and not s.get("notify_internal"):
            return
        if kind == "open" and sev_rank(inc["severity"]) < sev_rank(s.get("notify_min_severity", "high")):
            return
        if kind == "resolved" and (not s.get("notify_resolved") or not inc.get("notified")):
            return
        where = inc.get("device_name") or inc.get("host") or ""
        if kind == "open":
            title = f"{SEV_ICON[inc['severity']]} #{inc['number']} {SEV_LABEL[inc['severity']].upper()} — {inc['title']}"
            text = "\n".join(x for x in [where and f"📍 {where}", f"Origem: {SOURCES.get(inc['source'], inc['source'])}",
                                         (inc.get("description") or "")[:600]] if x)
            buttons = [[{"text": "👀 Reconhecer", "callback_data": f"inc:ack:{inc['id']}"},
                        {"text": "✅ Resolver", "callback_data": f"inc:res:{inc['id']}"}]]
        else:
            title = f"✅ #{inc['number']} resolvido — {inc['title']}"
            text = "\n".join(x for x in [where and f"📍 {where}", f"Durou {fmt_dur(duration(inc))}"] if x)
            buttons = None
        try:
            await self.alert(self.db, title, text, push_url=f"/incidents?id={inc['id']}", push_tag=f"inc-{inc['id']}",
                             buttons=buttons)
            if kind == "open":
                await self.db.incidents.update_one({"id": inc["id"]}, {"$set": {"notified": True}})
        except Exception:
            pass

    async def ingest(self, source: str, ev: dict) -> dict:
        """Abre, soma ou resolve o incidente do evento. Devolve {action, incident}."""
        s = await self.settings()
        now = now_iso()
        key = ev["key"][:300]
        cur = await self.db.incidents.find_one({"key": key, "status": {"$in": ["open", "ack"]}}, {"_id": 0})
        if ev["status"] == "ok":
            if not cur:
                return {"action": "ignored", "incident": None}
            upd = {"status": "resolved", "resolved_at": now, "resolved_by": SOURCES.get(source, source), "updated_at": now}
            await self.db.incidents.update_one({"id": cur["id"]}, {
                "$set": upd, "$push": {"timeline": {"at": now, "by": SOURCES.get(source, source), "type": "resolved",
                                                    "text": "Normalizou na origem"}}})
            cur.update(upd)
            await self._notify(cur, "resolved", s)
            return {"action": "resolved", "incident": cur}
        if cur:
            upd = {"last_seen": now, "updated_at": now}
            if sev_rank(ev["severity"]) > sev_rank(cur["severity"]):
                upd["severity"] = ev["severity"]
            await self.db.incidents.update_one({"id": cur["id"]}, {"$set": upd, "$inc": {"count": 1}})
            cur.update(upd)
            cur["count"] = cur.get("count", 1) + 1
            return {"action": "repeated", "incident": cur}
        dev = await self._device(ev.get("host", ""), ev.get("host_name", ""))
        inc = {"id": os.urandom(8).hex(), "number": await self._number(), "key": key, "source": source,
               "status": "open", "severity": ev["severity"] if ev["severity"] in SEVERITIES else "medium",
               "title": ev["title"], "description": ev.get("description", ""), "url": ev.get("url", ""),
               "host": ev.get("host", ""), "host_name": ev.get("host_name", ""),
               "device_id": (dev or {}).get("id"), "device_name": (dev or {}).get("name") or ev.get("host_name") or "",
               "created_at": now, "updated_at": now, "last_seen": now, "count": 1,
               "ack_by": None, "ack_at": None, "assignee": None, "resolved_at": None, "resolved_by": None,
               "timeline": [{"at": now, "by": SOURCES.get(source, source), "type": "open", "text": "Aberto"}]}
        await self.db.incidents.insert_one(dict(inc))
        await self._notify(inc, "open", s)
        return {"action": "opened", "incident": inc}

    async def act(self, inc_id: str, action: str, who: str, note: str = "", assignee: Optional[dict] = None) -> Optional[dict]:
        inc = await self.db.incidents.find_one({"id": inc_id}, {"_id": 0})
        if not inc:
            return None
        now = now_iso()
        sets, text = {"updated_at": now}, ""
        if action == "ack":
            if inc["status"] != "open":
                return inc
            sets.update(status="ack", ack_by=who, ack_at=now)
            if not inc.get("assignee"):
                sets["assignee"] = assignee
            text = "Reconhecido"
        elif action == "resolve":
            if inc["status"] == "resolved":
                return inc
            sets.update(status="resolved", resolved_at=now, resolved_by=who)
            text = "Resolvido"
        elif action == "reopen":
            sets.update(status="open", resolved_at=None, resolved_by=None)
            text = "Reaberto"
        elif action == "assign":
            sets["assignee"] = assignee
            text = f"Responsável: {(assignee or {}).get('name') or 'ninguém'}"
        elif action == "comment":
            text = ""
        else:
            return None
        entry = {"at": now, "by": who, "type": action, "text": (text + (f" — {note}" if note and text else note))[:2000]}
        await self.db.incidents.update_one({"id": inc_id}, {"$set": sets, "$push": {"timeline": entry}})
        inc.update(sets)
        inc.setdefault("timeline", []).append(entry)
        return inc
