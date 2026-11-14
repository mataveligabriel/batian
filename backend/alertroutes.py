"""Grupos de alerta do Telegram.

Além do chat principal (Automação), dá para criar outros destinos: outro grupo com o mesmo bot do BastiON ou com outro
bot. Cada destino escolhe o que recebe: todos os equipamentos, os de algumas tags ou só equipamentos específicos, e de
quais tipos de alerta. Marcando "exclusivo", os alertas desses equipamentos deixam de ir para o chat principal.

Só alertas ligados a um equipamento passam por aqui; avisos do sistema (login, backup do banco…) continuam no principal.
"""
import os
import re
from typing import List, Optional

import vault

KINDS = {"interface": "Interface caiu / voltou", "device": "Equipamento caiu / voltou", "config": "Configuração alterada",
         "incident": "Incidentes"}
_TOKEN = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")
_CHAT = re.compile(r"^(-?\d{4,20}|@[A-Za-z0-9_]{4,40})$")


class RouteError(ValueError):
    pass


def clean(b: dict, existing: Optional[dict] = None) -> dict:
    """Valida o que veio da tela. O token só é trocado quando vem preenchido (nunca volta para a tela)."""
    name = str(b.get("name") or "").strip()[:60]
    if not name:
        raise RouteError("Dê um nome ao grupo")
    chat = str(b.get("chat_id") or "").strip()
    if not _CHAT.match(chat):
        raise RouteError("Chat ID inválido. Grupos começam com - (ex.: -1001234567890); mande /id no grupo para descobrir")
    bot = "custom" if b.get("bot") == "custom" else "default"
    out = {"name": name, "chat_id": chat, "bot": bot, "enabled": bool(b.get("enabled", True)),
           "scope": b.get("scope") if b.get("scope") in ("all", "tags", "devices") else "all",
           "tags": sorted({str(t).strip() for t in (b.get("tags") or []) if str(t).strip()})[:100],
           "device_ids": list(dict.fromkeys(str(d) for d in (b.get("device_ids") or [])))[:2000],
           "kinds": [k for k in (b.get("kinds") or []) if k in KINDS],
           "exclusive": bool(b.get("exclusive")), "thread_id": None}
    th = str(b.get("thread_id") or "").strip()
    if th:
        if not th.isdigit():
            raise RouteError("Tópico (thread) deve ser um número")
        out["thread_id"] = int(th)
    if out["scope"] == "tags" and not out["tags"]:
        raise RouteError("Escolha pelo menos uma tag")
    if out["scope"] == "devices" and not out["device_ids"]:
        raise RouteError("Escolha pelo menos um equipamento")
    if out["scope"] == "all":
        out["exclusive"] = False          # "todos" exclusivo esvaziaria o chat principal sem querer
    token = str(b.get("token") or "").strip()
    if bot == "custom":
        if token:
            if not _TOKEN.match(token):
                raise RouteError("Token do bot inválido (formato 123456:ABC… do @BotFather)")
            out["token_enc"] = vault.encrypt(token)
        elif existing and existing.get("token_enc"):
            out["token_enc"] = existing["token_enc"]
        else:
            raise RouteError("Informe o token do outro bot")
    else:
        out["token_enc"] = ""
    return out


def public(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k not in ("token_enc", "_id")}
    out["has_token"] = bool(r.get("token_enc"))
    return out


def matches(r: dict, dev: Optional[dict], kind: Optional[str]) -> bool:
    if not r.get("enabled") or not dev:
        return False
    if r.get("kinds") and kind not in r["kinds"]:
        return False
    if r.get("scope") == "tags":
        return bool(set(dev.get("tags") or []) & set(r.get("tags") or []))
    if r.get("scope") == "devices":
        return dev.get("id") in (r.get("device_ids") or [])
    return True


async def targets(db, device_id: Optional[str], kind: Optional[str]) -> dict:
    """{routes: [destinos que recebem], skip_main: bool}. Sem equipamento = só o chat principal."""
    if not device_id:
        return {"routes": [], "skip_main": False}
    dev = await db.devices.find_one({"id": device_id}, {"_id": 0, "id": 1, "tags": 1})
    hit: List[dict] = [r async for r in db.alert_routes.find({"enabled": True}, {"_id": 0}) if matches(r, dev, kind)]
    return {"routes": hit, "skip_main": any(r.get("exclusive") for r in hit)}


async def send(client, r: dict, main_token: str, title: str, text: str, buttons: Optional[list] = None) -> str:
    token = vault.decrypt(r.get("token_enc") or "") if r.get("bot") == "custom" else main_token
    if not token:
        return "sem token"
    body = {"chat_id": r["chat_id"], "text": f"{title}\n{text}"[:4000]}
    if r.get("thread_id"):
        body["message_thread_id"] = r["thread_id"]
    if buttons and r.get("bot") != "custom":      # os botões são atendidos pelo bot do BastiON
        body["reply_markup"] = {"inline_keyboard": buttons}
    try:
        resp = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body)
        if resp.status_code == 200:
            return "ok"
        try:
            desc = resp.json().get("description", "")
        except Exception:
            desc = ""
        return f"HTTP {resp.status_code}: {desc[:150]}"
    except Exception as e:
        return f"erro: {e.__class__.__name__}"


def new_id() -> str:
    return os.urandom(6).hex()
