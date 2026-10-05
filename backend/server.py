"""SSH BastiON Central - FastAPI backend."""
from dotenv import load_dotenv
from pathlib import Path
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import csv
import io
import os
import re
import asyncio
import time
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect, Query, UploadFile, File, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from auth import (
    hash_password, verify_password, create_access_token, decode_token,
    get_current_user, require_admin,
)
from pydantic import BaseModel
from models import (
    UserCreate, UserOut, LoginPayload, UserUpdate, ChangePasswordPayload,
    AgentCreate, Agent,
    DeviceCreate, DeviceCreateRequest, Device, DEVICE_TYPES,
    DeviceIdsPayload, DeviceExportPayload, DeviceBulkUpdate,
    MapCreate, MapUpdate, MonitorSettings, DeviceMonitorPayload,
    DashboardCreate, DashboardUpdate, OpticsSettings, OpticsTestPayload, SeriesMultiPayload,
    ScriptCreate, Script,
    Session, BatchExecPayload, BatchResultItem, SshKeyConfig, BastionSettings,
    AutomationSettings, BackupRunPayload, AISettings, BackupIdsPayload, BackupCleanupPayload,
)
import ai_assistant
import llm
from ssh_service import SSHClientWrapper, Hop, tcp_ping, LEGACY_TYPES
from telnet_service import TelnetClientWrapper
import vault
import automation
import configsearch
import flowdiscover
import mitigation
import peering
import dailyreport
import qrgen
import security
import webproxy
import cloudsync
import lookingglass
import rpki
import vendorscan
import rdp
import webpush
import sharing
import netanalysis
import mplscli
import netreport
import asndb
import flowagg
import flowstore
import auth as auth_mod
import snmp_service
import monitor as monitoring
import optics as optics_mod

TUNNEL_BIND_HOST = os.environ.get("TUNNEL_BIND_HOST", "127.0.0.1")

# ---------- Mongo ----------
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]


USER_PUBLIC = {"_id": 0, "password_hash": 0, "totp_secret": 0, "totp_pending": 0, "totp_recovery": 0}


async def _load_user(user_id: str):
    return await db.users.find_one({"id": user_id}, USER_PUBLIC)

auth_mod.USER_LOADER = _load_user   # papel/exclusão valem na hora + perfil View

app = FastAPI(title="SSH BastiON Central")
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)   # a URL do Telegram leva o token do bot
logger = logging.getLogger("bastion")


# ---------- Startup ----------
@app.on_event("startup")
async def startup():
    for attempt in range(30):
        try:
            await client.admin.command("ping")
            break
        except Exception as e:
            logger.warning(f"MongoDB indisponível ({e.__class__.__name__}), tentativa {attempt + 1}/30…")
            await asyncio.sleep(2)
    await db.users.create_index("email", unique=True)
    await db.devices.create_index("name")
    await db.agents.create_index("name")
    await seed_admin()
    await seed_sample_data()
    await db.backups.create_index([("device_id", 1), ("created_at", -1)])
    await security.ensure_indexes(db)
    await db.backups.create_index("id")
    await db.sessions.create_index([("device_id", 1), ("started_at", 1)])
    admin = await db.users.find_one({"email": os.environ["ADMIN_EMAIL"].lower()}, {"_id": 0, "id": 1})
    if admin:
        for col in (db.devices, db.agents):
            await col.update_many({"$or": [{"owner_id": None}, {"owner_id": {"$exists": False}}]}, {"$set": {"owner_id": admin["id"]}})
    await db.ai_audit.create_index([("at", -1)])
    await db.ai_pending.create_index("id")
    await _apply_retention()
    await db.if_samples.create_index([("device_id", 1), ("if_index", 1), ("ts", -1)])
    await db.optics_samples.create_index([("device_id", 1), ("if_index", 1), ("ts", -1)])
    await db.dashboards.create_index("owner_id")
    await db.if_monitors.create_index([("device_id", 1), ("if_index", 1)], unique=True)
    await db.if_events.create_index([("at", -1)])
    await db.maps.create_index("owner_id")
    await db.net_reports.create_index([("map_id", 1), ("at", -1)])
    await db.net_reports_raw.create_index("rid")
    scheduler.start()
    assistant.start()
    net_monitor.start()
    optics_collector.start()
    try:
        await flowstore.ensure_indexes(db, await flowstore.get_settings(db))
    except Exception as e:
        logger.warning(f"índices do flow: {e}")
    global _flow_asn_task
    _flow_asn_task = asyncio.create_task(_flow_asn_loop())
    _bg(_auto_discover_loop())
    _bg(_rpki_watch_loop())
    await db.flow_mitigations.create_index([("status", 1), ("created_at", -1)])
    await db.flow_mitigations.create_index("id")
    try:
        await web_proxy.start()
    except Exception as e:
        logger.warning(f"Acesso Web: {e}")


async def seed_admin():
    email = os.environ.get("ADMIN_EMAIL", "admin@example.com").lower()
    pw = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": email})
    if not existing:
        await db.users.insert_one({
            "id": os.urandom(8).hex(),
            "email": email,
            "password_hash": hash_password(pw),
            "name": "Administrador",
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"Seeded admin: {email}")
    elif not verify_password(pw, existing.get("password_hash", "")):
        await db.users.update_one({"email": email}, {"$set": {"password_hash": hash_password(pw)}})


async def seed_sample_data():
    if os.environ.get("SEED_SAMPLE_DATA", "false").lower() != "true":
        return
    if await db.agents.count_documents({}) == 0:
        agents = [
            {"id": "agent-sp-01", "name": "Agente-SP-DC01", "location": "São Paulo - DC01",
             "host": "bastion-sp.example.com", "port": 22, "username": "bastion",
             "description": "BastiON Datacenter São Paulo", "status": "online",
             "latency_ms": 12.4, "last_seen": datetime.now(timezone.utc).isoformat(),
             "created_at": datetime.now(timezone.utc).isoformat()},
            {"id": "agent-rj-01", "name": "Agente-RJ-Filial", "location": "Rio de Janeiro",
             "host": "bastion-rj.example.com", "port": 22, "username": "bastion",
             "description": "Filial RJ - túnel reverso", "status": "online",
             "latency_ms": 28.1, "last_seen": datetime.now(timezone.utc).isoformat(),
             "created_at": datetime.now(timezone.utc).isoformat()},
            {"id": "agent-mg-01", "name": "Agente-MG-Telecom", "location": "Belo Horizonte",
             "host": "bastion-mg.example.com", "port": 22, "username": "bastion",
             "description": "Telecom MG", "status": "offline",
             "latency_ms": None, "last_seen": None,
             "created_at": datetime.now(timezone.utc).isoformat()},
        ]
        await db.agents.insert_many(agents)
    if await db.devices.count_documents({}) == 0:
        devs = [
            {"id": "dev-1", "name": "core-sp-01", "host": "10.10.1.1", "port": 22, "username": "admin",
             "tags": ["Roteador", "Cisco-IOS", "SP"], "agent_id": "agent-sp-01",
             "description": "Core Router SP-01", "status": "online", "latency_ms": 4.2,
             "last_seen": datetime.now(timezone.utc).isoformat(),
             "created_at": datetime.now(timezone.utc).isoformat()},
            {"id": "dev-2", "name": "sw-rj-02", "host": "192.168.20.5", "port": 2222, "username": "admin",
             "tags": ["Switch", "RJ"], "agent_id": "agent-rj-01",
             "description": "Switch Distribuição RJ", "status": "online", "latency_ms": 18.9,
             "last_seen": datetime.now(timezone.utc).isoformat(),
             "created_at": datetime.now(timezone.utc).isoformat()},
            {"id": "dev-3", "name": "fw-mg-edge", "host": "172.16.0.10", "port": 8022, "username": "root",
             "tags": ["Firewall", "MG"], "agent_id": "agent-mg-01",
             "description": "Firewall Edge MG", "status": "offline", "latency_ms": None,
             "last_seen": None, "created_at": datetime.now(timezone.utc).isoformat()},
            {"id": "dev-4", "name": "ap-sp-lobby", "host": "10.10.2.24", "port": 22, "username": "admin",
             "tags": ["Access-Point", "SP"], "agent_id": "agent-sp-01",
             "description": "AP Lobby", "status": "online", "latency_ms": 6.8,
             "last_seen": datetime.now(timezone.utc).isoformat(),
             "created_at": datetime.now(timezone.utc).isoformat()},
        ]
        await db.devices.insert_many(devs)
    if await db.scripts.count_documents({}) == 0:
        scripts = [
            {"id": "s1", "name": "Backup Config", "description": "Executa backup da configuração",
             "content": "show running-config | tee /tmp/backup.cfg", "created_at": datetime.now(timezone.utc).isoformat()},
            {"id": "s2", "name": "Uptime", "description": "Verifica uptime",
             "content": "uptime", "created_at": datetime.now(timezone.utc).isoformat()},
            {"id": "s3", "name": "Interfaces", "description": "Lista interfaces",
             "content": "ip -brief addr show || show ip interface brief", "created_at": datetime.now(timezone.utc).isoformat()},
        ]
        await db.scripts.insert_many(scripts)


# ---------- Auth ----------
async def _security_settings() -> dict:
    doc = await db.config.find_one({"key": "security"}, {"_id": 0}) or {}
    return {"require_2fa": bool(doc.get("require_2fa"))}


def _session_payload(user: dict) -> dict:
    token = create_access_token(user["id"], user["email"], user.get("role", "operator"), user.get("token_version", 0))
    return {"token": token, "user": {"id": user["id"], "email": user["email"], "name": user.get("name", ""),
                                     "role": user.get("role", "operator")}}


@api.post("/auth/login")
async def login(payload: LoginPayload, request: Request):
    email = payload.email.lower()
    ip = security.client_ip(request)
    ua = request.headers.get("user-agent", "")
    wait = await security.locked_for(db, email, ip)
    if wait:
        await security.log_event(db, email, ip, ua, False, "bloqueado")
        raise HTTPException(status_code=429, detail=f"Muitas tentativas. Tente de novo em {max(1, round(wait / 60))} min.")

    async def fail(reason: str, detail: str, user_id: Optional[str] = None):
        n = await security.record_fail(db, email, ip)
        await security.log_event(db, email, ip, ua, False, reason, user_id)
        if n == security.MAX_FAILS_EMAIL:
            try:
                await automation.send_alert(db, "🔐 Login bloqueado por tentativas",
                                            f"{email}: {n} tentativas erradas em 15 min (último IP {ip}). Liberado em 15 min.",
                                            push_url="/users")
            except Exception as e:
                logger.warning(f"alerta de login bloqueado: {e}")
        raise HTTPException(status_code=401, detail=detail)

    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        await fail("senha", "Credenciais inválidas", user["id"] if user else None)
    if user.get("totp_enabled"):
        code = (payload.totp or "").strip()
        if not code:
            return {"need_totp": True}                   # senha certa: o front pede o código
        secret = vault.decrypt(user.get("totp_secret", ""))
        step = security.verify_totp(secret, code, int(user.get("totp_last_step", -1)))
        if step is not None:
            await db.users.update_one({"id": user["id"]}, {"$set": {"totp_last_step": step}})
        else:
            used = security.match_recovery(code, user.get("totp_recovery") or [])
            if not used:
                await fail("2fa", "Código inválido", user["id"])
            await db.users.update_one({"id": user["id"]}, {"$pull": {"totp_recovery": used}})
            left = len(user.get("totp_recovery") or []) - 1
            await automation.send_alert(db, "🔑 Código de recuperação usado",
                                        f"{email} entrou com um código de recuperação (restam {left}). IP {ip}.", push_url="/")
    await security.clear_fails(db, email)
    await security.log_event(db, email, ip, ua, True, "", user["id"])
    await db.users.update_one({"id": user["id"]}, {"$set": {"last_login_at": datetime.now(timezone.utc).isoformat(), "last_login_ip": ip}})
    return _session_payload(user)


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    u = await db.users.find_one({"id": user["id"]})
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    sec = await _security_settings()
    return {"id": u["id"], "email": u["email"], "name": u.get("name", ""), "role": u.get("role", "operator"),
            "totp_enabled": bool(u.get("totp_enabled")), "require_2fa": sec["require_2fa"],
            "modules": u.get("modules") if isinstance(u.get("modules"), list) and u.get("role") == "operator" else None}


# ---------- 2FA (app autenticador) e sessões ----------
class TotpCode(BaseModel):
    code: str = ""
    password: str = ""


@api.get("/auth/2fa")
async def twofa_status(user: dict = Depends(get_current_user)):
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    sec = await _security_settings()
    return {"enabled": bool(u.get("totp_enabled")), "recovery_left": len(u.get("totp_recovery") or []),
            "enabled_at": u.get("totp_enabled_at"), "require_2fa": sec["require_2fa"]}


@api.post("/auth/2fa/setup")
async def twofa_setup(user: dict = Depends(get_current_user)):
    """Gera um segredo novo (ainda não ativo) e o QR para o app (Google Authenticator, Authy, Microsoft…)."""
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if u.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="2FA já está ativo. Desative antes de configurar outro celular.")
    # reabrir a tela mostra o mesmo QR (o app do celular já pode ter lido); renova depois de 30 min
    secret, at = vault.decrypt(u.get("totp_pending", "")), u.get("totp_pending_at") or ""
    fresh = False
    try:
        fresh = bool(secret) and datetime.now(timezone.utc) - datetime.fromisoformat(at) < timedelta(minutes=30)
    except ValueError:
        pass
    if not fresh:
        secret = security.new_secret()
        await db.users.update_one({"id": user["id"]}, {"$set": {"totp_pending": vault.encrypt(secret),
                                                                 "totp_pending_at": datetime.now(timezone.utc).isoformat()}})
    uri = security.otpauth_uri(secret, u["email"])
    return {"secret": secret, "uri": uri, "qr_svg": qrgen.svg(uri, scale=5)}


@api.post("/auth/2fa/enable")
async def twofa_enable(body: TotpCode, user: dict = Depends(get_current_user)):
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    secret = vault.decrypt(u.get("totp_pending", ""))
    if not secret:
        raise HTTPException(status_code=400, detail="Gere o QR code primeiro")
    step = security.verify_totp(secret, body.code)
    if step is None:
        off = security.clock_offset(secret, body.code)
        if off is not None:
            raise HTTPException(status_code=400, detail=(
                f"O código é deste QR, mas o relógio do celular e o do servidor estão {abs(off)} s diferentes "
                f"({'celular adiantado' if off > 0 else 'celular atrasado'} em relação ao servidor). Acerte a hora do "
                "servidor (timedatectl set-ntp true) ou ative a hora automática no celular."))
        raise HTTPException(status_code=400, detail=(
            "Código não confere. Use o código da conta cuja chave é a mostrada aqui (no app autenticador pode haver "
            "uma conta antiga 'Bastion' de outra tentativa — apague-a e leia este QR de novo)."))
    codes, hashes = security.new_recovery_codes()
    await db.users.update_one({"id": user["id"]}, {
        "$set": {"totp_enabled": True, "totp_secret": vault.encrypt(secret), "totp_last_step": step,
                 "totp_recovery": hashes, "totp_enabled_at": datetime.now(timezone.utc).isoformat()},
        "$unset": {"totp_pending": "", "totp_pending_at": ""}})
    return {"enabled": True, "recovery_codes": codes}


async def _check_password_and_code(user_id: str, body: TotpCode) -> dict:
    u = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not u or not verify_password(body.password, u.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Senha incorreta")
    if u.get("totp_enabled"):
        ok = security.verify_totp(vault.decrypt(u.get("totp_secret", "")), body.code) is not None \
            or security.match_recovery(body.code, u.get("totp_recovery") or [])
        if not ok:
            raise HTTPException(status_code=400, detail="Código inválido")
    return u


@api.post("/auth/2fa/disable")
async def twofa_disable(body: TotpCode, user: dict = Depends(get_current_user)):
    sec = await _security_settings()
    if sec["require_2fa"]:
        raise HTTPException(status_code=400, detail="O administrador exige 2FA para todos. Para trocar de celular, peça para ele zerar o seu 2FA.")
    u = await _check_password_and_code(user["id"], body)
    tv = int(u.get("token_version", 0)) + 1
    await db.users.update_one({"id": user["id"]}, {
        "$set": {"totp_enabled": False, "token_version": tv},
        "$unset": {"totp_secret": "", "totp_recovery": "", "totp_last_step": "", "totp_pending": ""}})
    return _session_payload({**u, "token_version": tv})


@api.post("/auth/2fa/recovery-codes")
async def twofa_new_codes(body: TotpCode, user: dict = Depends(get_current_user)):
    u = await _check_password_and_code(user["id"], body)
    if not u.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="2FA não está ativo")
    codes, hashes = security.new_recovery_codes()
    await db.users.update_one({"id": user["id"]}, {"$set": {"totp_recovery": hashes}})
    return {"recovery_codes": codes}


@api.post("/auth/logout-all")
async def logout_all(user: dict = Depends(get_current_user)):
    """Encerra as sessões em todos os aparelhos; esta continua com um token novo."""
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    tv = int(u.get("token_version", 0)) + 1
    await db.users.update_one({"id": user["id"]}, {"$set": {"token_version": tv}})
    return _session_payload({**u, "token_version": tv})


@api.get("/auth/logins")
async def my_logins(user: dict = Depends(get_current_user), limit: int = 20):
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0, "email": 1})
    rows = await db.login_events.find({"email": u["email"]}, {"_id": 0, "ts": 0}).sort("at", -1).to_list(min(max(limit, 1), 100))
    return rows


@api.post("/users/{user_id}/2fa/reset")
async def admin_reset_2fa(user_id: str, current: dict = Depends(require_admin)):
    u = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    await db.users.update_one({"id": user_id}, {
        "$set": {"totp_enabled": False, "token_version": int(u.get("token_version", 0)) + 1},
        "$unset": {"totp_secret": "", "totp_recovery": "", "totp_last_step": "", "totp_pending": ""}})
    logger.info("2FA de %s zerado por %s", u["email"], current.get("email"))
    return {"ok": True}


@api.get("/security/settings")
async def get_security_settings(_: dict = Depends(require_admin)):
    s = await _security_settings()
    users = await db.users.find({}, {"_id": 0, "email": 1, "totp_enabled": 1}).to_list(1000)
    s["users_without_2fa"] = sorted(x["email"] for x in users if not x.get("totp_enabled"))
    return s


class SecuritySettingsIn(BaseModel):
    require_2fa: bool = False


@api.put("/security/settings")
async def put_security_settings(body: SecuritySettingsIn, current: dict = Depends(require_admin)):
    me_ = await db.users.find_one({"id": current["id"]}, {"_id": 0, "totp_enabled": 1})
    if body.require_2fa and not me_.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="Ative o 2FA na sua conta antes de exigir de todos")
    await db.config.update_one({"key": "security"}, {"$set": {"require_2fa": body.require_2fa}}, upsert=True)
    return await get_security_settings(current)


@api.get("/security/logins")
async def all_logins(_: dict = Depends(require_admin), limit: int = 100, failed: bool = False):
    q = {"ok": False} if failed else {}
    return await db.login_events.find(q, {"_id": 0, "ts": 0}).sort("at", -1).to_list(min(max(limit, 1), 500))


@api.post("/auth/logout")
async def logout(user: dict = Depends(get_current_user)):
    return {"ok": True}


# ---------- Users (admin) ----------
@api.get("/users")
async def list_users(_: dict = Depends(require_admin)):
    docs = await db.users.find({}, USER_PUBLIC).to_list(500)
    return docs


@api.post("/users")
async def create_user(payload: UserCreate, _: dict = Depends(require_admin)):
    if await db.users.find_one({"email": payload.email.lower()}):
        raise HTTPException(status_code=400, detail="Email já cadastrado")
    doc = {
        "id": os.urandom(8).hex(),
        "email": payload.email.lower(),
        "password_hash": hash_password(payload.password),
        "name": payload.name,
        "role": payload.role if payload.role in auth_mod.ROLES else "operator",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if payload.modules is not None and doc["role"] == "operator":
        doc["modules"] = auth_mod.clean_modules(payload.modules)
    await db.users.insert_one(doc)
    doc.pop("password_hash")
    doc.pop("_id", None)
    return doc


@api.put("/users/{user_id}")
async def update_user(user_id: str, payload: UserUpdate, current: dict = Depends(require_admin)):
    u = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    update = {}
    if payload.name and payload.name.strip():
        update["name"] = payload.name.strip()
    if payload.role:
        if payload.role not in auth_mod.ROLES:
            raise HTTPException(status_code=400, detail="Papel inválido")
        if user_id == current["id"] and payload.role != "admin":
            raise HTTPException(status_code=400, detail="Você não pode remover seu próprio papel de administrador")
        update["role"] = payload.role
    if payload.password:
        if len(payload.password) < 6:
            raise HTTPException(status_code=400, detail="A senha deve ter pelo menos 6 caracteres")
        update["password_hash"] = hash_password(payload.password)
        update["token_version"] = int(u.get("token_version", 0)) + 1     # a senha trocada derruba as sessões dele
    unset = {}
    if payload.modules_all or (update.get("role") or u.get("role")) != "operator":
        if "modules" in u:
            unset["modules"] = ""                       # todos os módulos (ou papel que não usa a lista)
    elif payload.modules is not None:
        update["modules"] = auth_mod.clean_modules(payload.modules)
    if update or unset:
        await db.users.update_one({"id": user_id}, {**({"$set": update} if update else {}), **({"$unset": unset} if unset else {})})
    return await db.users.find_one({"id": user_id}, USER_PUBLIC)


@api.get("/modules")
async def list_modules(_: dict = Depends(get_current_user)):
    """Módulos que o administrador pode liberar por usuário."""
    return [{"key": k, "label": l, "desc": d} for k, l, d in auth_mod.MODULES]


@api.post("/auth/change-password")
async def change_password(payload: ChangePasswordPayload, current: dict = Depends(get_current_user)):
    u = await db.users.find_one({"id": current["id"]})
    if not u or not verify_password(payload.current_password, u["password_hash"]):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="A nova senha deve ter pelo menos 6 caracteres")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="A nova senha deve ser diferente da atual")
    tv = int(u.get("token_version", 0)) + 1
    await db.users.update_one({"id": current["id"]}, {"$set": {"password_hash": hash_password(payload.new_password), "token_version": tv}})
    return {"ok": True, **_session_payload({**u, "token_version": tv})}


@api.delete("/users/{user_id}")
async def delete_user(user_id: str, current: dict = Depends(require_admin)):
    if user_id == current["id"]:
        raise HTTPException(status_code=400, detail="Não é possível excluir a si mesmo")
    res = await db.users.delete_one({"id": user_id})
    return {"deleted": res.deleted_count}


# ---------- Secrets helpers ----------
SECRET_FIELDS = ("password", "agent_private_key")


def _public(doc: dict) -> dict:
    doc = dict(doc)
    doc["has_password"] = bool(doc.get("password"))
    for f in SECRET_FIELDS:
        doc.pop(f, None)
    doc.pop("_id", None)
    doc.pop("clear_password", None)
    return doc


def _apply_secret(payload: dict, existing: Optional[dict], field: str = "password") -> dict:
    """Encrypt new secret, keep the existing one when blank, drop it when clear flag is set."""
    clear = payload.pop("clear_password", False)
    new_val = payload.pop(field, None)
    if clear:
        payload[field] = ""
    elif new_val:
        payload[field] = vault.encrypt(new_val)
    elif existing is not None:
        payload[field] = existing.get(field, "")
    else:
        payload[field] = ""
    return payload


# ---------- BastiON settings ----------
async def _bastion_settings() -> dict:
    doc = await db.config.find_one({"key": "bastion"}, {"_id": 0})
    if not doc:
        doc = {"key": "bastion", "public_host": "", "ssh_port": 22, "ssh_user": "bastion",
               "sync_token": os.urandom(16).hex()}
        await db.config.insert_one(dict(doc))
    if not doc.get("sync_token"):
        doc["sync_token"] = os.urandom(16).hex()
        await db.config.update_one({"key": "bastion"}, {"$set": {"sync_token": doc["sync_token"]}})
    return doc


@api.get("/bastion/settings")
async def get_bastion_settings(_: dict = Depends(get_current_user)):
    s = await _bastion_settings()
    return {"public_host": s.get("public_host", ""), "ssh_port": s.get("ssh_port", 22),
            "ssh_user": s.get("ssh_user", "bastion"), "tunnel_bind_host": TUNNEL_BIND_HOST}


@api.put("/bastion/settings")
async def put_bastion_settings(payload: BastionSettings, _: dict = Depends(require_admin)):
    await _bastion_settings()
    await db.config.update_one({"key": "bastion"}, {"$set": payload.model_dump()})
    return {"ok": True}


@api.get("/bastion/authorized-keys")
async def bastion_authorized_keys(token: str = Query(...)):
    s = await _bastion_settings()
    if token != s["sync_token"]:
        raise HTTPException(status_code=403, detail="Token inválido")
    agents = await db.agents.find({"mode": "reverse", "agent_public_key": {"$ne": ""}}, {"_id": 0}).to_list(1000)
    lines, seen = [], set()
    for a in sorted(agents, key=lambda x: bool(x.get("copied_from"))):   # original antes das cópias
        k = (a["agent_public_key"].strip(), a.get("tunnel_port"))
        if k in seen:
            continue
        seen.add(k)
        opts = f'restrict,port-forwarding,permitlisten="{TUNNEL_BIND_HOST}:{a.get("tunnel_port")}"'
        lines.append(f'{opts} {a["agent_public_key"].strip()} bastion-agent-{a["id"]}')
    return PlainTextResponse("\n".join(lines) + "\n")


@api.get("/bastion/setup-script")
async def bastion_setup_script(api_url: str = Query(...), _: dict = Depends(require_admin)):
    s = await _bastion_settings()
    user = s.get("ssh_user", "bastion")
    script = f"""#!/usr/bin/env bash
# SSH BastiON Central — preparação do servidor (rode como root no VPS onde o backend está rodando)
# Cria o usuário que recebe os túneis reversos dos agentes e sincroniza as chaves automaticamente.
set -e
BASTION_USER="{user}"
API_URL="{api_url.rstrip('/')}"
SYNC_TOKEN="{s['sync_token']}"

id -u "$BASTION_USER" >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin "$BASTION_USER"
install -d -m 700 -o "$BASTION_USER" -g "$BASTION_USER" "/home/$BASTION_USER/.ssh"

mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/90-bastion-central.conf <<EOF
Match User $BASTION_USER
    AllowTcpForwarding remote
    GatewayPorts no
    X11Forwarding no
    PermitTTY no
    ClientAliveInterval 30
    ClientAliveCountMax 3
EOF

cat > /usr/local/bin/bastion-sync-keys.sh <<EOF
#!/usr/bin/env bash
TMP=\\$(mktemp)
if curl -fsS "$API_URL/api/bastion/authorized-keys?token=$SYNC_TOKEN" -o "\\$TMP"; then
  install -m 600 -o $BASTION_USER -g $BASTION_USER "\\$TMP" /home/$BASTION_USER/.ssh/authorized_keys
fi
rm -f "\\$TMP"
EOF
chmod +x /usr/local/bin/bastion-sync-keys.sh
echo "* * * * * root /usr/local/bin/bastion-sync-keys.sh" > /etc/cron.d/bastion-sync-keys
/usr/local/bin/bastion-sync-keys.sh

sshd -t && (systemctl reload sshd 2>/dev/null || systemctl reload ssh)
echo "BastiON pronto. Usuário $BASTION_USER aceita túneis reversos; chaves sincronizadas a cada minuto."
"""
    return {"script": script}


# ---------- Agents ----------
class VpnDown(RuntimeError):
    pass


async def _agent_chain(agent_id: str, check_vpn: bool = True) -> List[dict]:
    """Return agents from the root (closest to bastion) down to agent_id."""
    chain, seen, cur = [], set(), agent_id
    while cur and cur not in seen:
        seen.add(cur)
        ag = await db.agents.find_one({"id": cur}, {"_id": 0})
        if not ag:
            raise RuntimeError("Agente da cadeia não encontrado")
        chain.append(ag)
        cur = ag.get("parent_agent_id")
    chain.reverse()
    if check_vpn:
        for ag in chain:
            if ag.get("vpn_id"):
                st = await db.vpn_status.find_one({"_id": ag["vpn_id"]}, {"_id": 0, "state": 1}) or {}
                if st.get("state") != "up":
                    prof = await db.vpn_profiles.find_one({"id": ag["vpn_id"]}, {"_id": 0, "name": 1}) or {}
                    raise VpnDown(f"VPN '{prof.get('name', '?')}' desconectada — o agente {ag['name']} depende dela. "
                                  "Conecte informando o token (indicador VPN no menu ou Agentes Remotos → VPNs).")
    return chain


def _agent_hop(ag: dict, priv: str) -> Hop:
    if ag.get("mode") == "reverse":
        host, port = TUNNEL_BIND_HOST, int(ag.get("tunnel_port") or 0)
    else:
        host, port = ag.get("host", ""), int(ag.get("port") or 22)
    pw = vault.decrypt(ag.get("password", ""))
    return Hop(host, port, ag.get("username") or "root", private_key=priv or None,
               password=pw or None, legacy=False, label=f"agente {ag['name']}")


async def _next_tunnel_port() -> int:
    docs = await db.agents.find({"tunnel_port": {"$ne": None}}, {"tunnel_port": 1}).to_list(5000)
    used = {int(d["tunnel_port"]) for d in docs if d.get("tunnel_port")}
    p = 20001
    while p in used:
        p += 1
    return p


def _gen_agent_keypair():
    import asyncssh
    key = asyncssh.generate_private_key("ssh-ed25519")
    return key.export_private_key().decode(), key.export_public_key().decode()


def _own(data: dict, user: dict, existing: Optional[dict] = None) -> dict:
    data["owner_id"] = existing.get("owner_id") if existing else user["id"]
    return data


async def _get_agent_for(user: dict, agent_id: str) -> dict:
    a = await db.agents.find_one({"id": agent_id, **_scope(user)}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Agente não encontrado")
    return a


async def _check_parent(user: dict, parent_id: Optional[str]):
    if parent_id:
        await _get_agent_for(user, parent_id)


@api.get("/agents")
async def list_agents(user: dict = Depends(get_current_user)):
    docs = await db.agents.find(_scope(user), {"_id": 0}).to_list(500)
    return [_public(d) for d in docs]


@api.post("/agents")
async def create_agent(payload: AgentCreate, user: dict = Depends(get_current_user)):
    data = _own(_apply_secret(payload.model_dump(), None), user)
    if data.get("parent_agent_id") == "":
        data["parent_agent_id"] = None
    await _check_parent(user, data.get("parent_agent_id"))
    data["vpn_id"] = data.get("vpn_id") or None
    if data["vpn_id"]:
        await _get_vpn_for(user, data["vpn_id"])
    a = Agent(**data)
    doc = a.model_dump()
    if doc["mode"] == "reverse":
        doc["tunnel_port"] = int(doc.get("tunnel_port") or await _next_tunnel_port())
        priv, pub = _gen_agent_keypair()
        doc["agent_private_key"] = vault.encrypt(priv)
        doc["agent_public_key"] = pub
        doc["host"] = doc.get("host") or "localhost"
    await db.agents.insert_one(doc)
    return _public(doc)


@api.put("/agents/{agent_id}")
async def update_agent(agent_id: str, payload: AgentCreate, user: dict = Depends(get_current_user)):
    existing = await _get_agent_for(user, agent_id)
    if payload.parent_agent_id == agent_id:
        raise HTTPException(status_code=400, detail="Agente não pode ser pai de si mesmo")
    data = _own(_apply_secret(payload.model_dump(), existing), user, existing)
    if data.get("parent_agent_id") == "":
        data["parent_agent_id"] = None
    await _check_parent(user, data.get("parent_agent_id"))
    data["vpn_id"] = data.get("vpn_id") or None
    if data["vpn_id"]:
        await _get_vpn_for(user, data["vpn_id"])
    if data["mode"] == "reverse":
        data["tunnel_port"] = int(data.get("tunnel_port") or existing.get("tunnel_port") or await _next_tunnel_port())
        if not existing.get("agent_public_key"):
            priv, pub = _gen_agent_keypair()
            data["agent_private_key"] = vault.encrypt(priv)
            data["agent_public_key"] = pub
    await db.agents.update_one({"id": agent_id}, {"$set": data})
    return _public(await db.agents.find_one({"id": agent_id}, {"_id": 0}))


@api.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, user: dict = Depends(get_current_user)):
    r = await db.agents.delete_one({"id": agent_id, **_scope(user)})
    await db.agents.update_many({"parent_agent_id": agent_id}, {"$set": {"parent_agent_id": None}})
    return {"deleted": r.deleted_count}


async def _ping_agent(ag: dict) -> Optional[float]:
    priv, _, _ = await _get_ssh_key()
    try:
        chain = await _agent_chain(ag["id"])
    except VpnDown:
        return None
    target = _agent_hop(chain[-1], priv)
    if len(chain) == 1:
        return await tcp_ping(target.host, target.port)
    w = SSHClientWrapper([_agent_hop(a, priv) for a in chain[:-1]])
    try:
        await w.connect()
        return await w.tcp_check(target.host, target.port)
    except Exception:
        return None
    finally:
        await w.close()


@api.post("/agents/{agent_id}/ping")
async def ping_agent(agent_id: str, user: dict = Depends(get_current_user)):
    a = await _get_agent_for(user, agent_id)
    lat = await _ping_agent(a)
    status = "online" if lat is not None else "offline"
    now = datetime.now(timezone.utc).isoformat()
    await db.agents.update_one({"id": agent_id}, {"$set": {"status": status, "latency_ms": lat, "last_seen": now if lat else a.get("last_seen")}})
    return {"agent_id": agent_id, "status": status, "latency_ms": lat}


@api.post("/agents/{agent_id}/test")
async def test_agent(agent_id: str, user: dict = Depends(get_current_user)):
    """Full SSH login through the chain up to this agent."""
    a = await _get_agent_for(user, agent_id)
    priv, _, _ = await _get_ssh_key()
    try:
        chain = await _agent_chain(agent_id)
        w = SSHClientWrapper([_agent_hop(x, priv) for x in chain])
        await w.connect()
        res = await w.run_command("hostname || echo ok", timeout=15)
        await w.close()
        return {"ok": True, "hops": [x["name"] for x in chain], "output": (res.get("stdout") or "").strip()[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@api.get("/agents/{agent_id}/install-script")
async def install_script(agent_id: str, user: dict = Depends(get_current_user)):
    a = await _get_agent_for(user, agent_id)
    if a.get("mode") != "reverse":
        return {"mode": "direct", "bash": "", "powershell": "",
                "note": "Agente em modo direto: o BastiON conecta diretamente em "
                        f"{a.get('host')}:{a.get('port', 22)} (ou através do agente pai). Nenhum instalador necessário — "
                        "apenas garanta que a chave global (ou usuário/senha) esteja autorizada nesse host."}
    s = await _bastion_settings()
    host, sport, suser = s.get("public_host") or "SEU_VPS_IP", s.get("ssh_port", 22), s.get("ssh_user", "bastion")
    tport, lport = a.get("tunnel_port"), a.get("port", 22)
    priv = vault.decrypt(a.get("agent_private_key", ""))
    ssh_opts = (f'-N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 '
                f'-o StrictHostKeyChecking=no -R {TUNNEL_BIND_HOST}:{tport}:localhost:{lport} -p {sport} {suser}@{host}')
    bash = f"""#!/usr/bin/env bash
# SSH BastiON Central — Agente Gateway "{a['name']}" (Linux/macOS)
# Abre um túnel SSH reverso persistente até o BastiON. Requisito: servidor SSH local ativo na porta {lport}
# (Linux: sudo apt install openssh-server | macOS: Ajustes > Geral > Compartilhamento > Login Remoto)
set -e
DIR="$HOME/.bastion-agent"
mkdir -p "$DIR" && chmod 700 "$DIR"
cat > "$DIR/agent_key" <<'KEY'
{priv}KEY
chmod 600 "$DIR/agent_key"
cat > "$DIR/tunnel.sh" <<EOF
#!/usr/bin/env bash
while true; do
  ssh {ssh_opts} -o UserKnownHostsFile=/dev/null -i "$DIR/agent_key"
  sleep 5
done
EOF
chmod +x "$DIR/tunnel.sh"
if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/bastion-agent.service" <<EOF
[Unit]
Description=SSH BastiON Central Agent ({a['name']})
After=network-online.target
[Service]
ExecStart=$DIR/tunnel.sh
Restart=always
RestartSec=5
[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now bastion-agent.service
  loginctl enable-linger "$USER" 2>/dev/null || true
  echo "Serviço bastion-agent ativo (systemctl --user status bastion-agent)."
else
  nohup "$DIR/tunnel.sh" >"$DIR/tunnel.log" 2>&1 &
  echo "Túnel iniciado em segundo plano (log: $DIR/tunnel.log)."
fi
echo "Agente {a['name']} -> {suser}@{host}:{sport} (túnel {TUNNEL_BIND_HOST}:{tport} no BastiON)."
"""
    powershell = f"""# SSH BastiON Central — Agente Gateway "{a['name']}" (Windows, PowerShell como Administrador)
# Requisito: OpenSSH Server instalado e ativo (Configurações > Aplicativos > Recursos opcionais > "Servidor OpenSSH"):
#   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
#   Set-Service sshd -StartupType Automatic; Start-Service sshd
# Windows Server 2016 / sem o recurso opcional: instale o OpenSSH pelo MSI oficial
#   https://github.com/PowerShell/Win32-OpenSSH/releases (OpenSSH-Win64-v9.5.0.0.msi)
$Dir = "$env:ProgramData\\bastion-agent"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$Ssh = (Get-Command ssh.exe -ErrorAction SilentlyContinue).Source
if (-not $Ssh) {{ $Ssh = "C:\\Program Files\\OpenSSH\\ssh.exe" }}
if (-not (Test-Path $Ssh)) {{ Write-Host "ssh.exe não encontrado — instale o OpenSSH (veja acima)" -ForegroundColor Red; return }}
$Key = @"
{priv}"@
if (Test-Path "$Dir\\agent_key") {{ icacls "$Dir\\agent_key" /reset | Out-Null }}
# a chave precisa de quebras de linha LF e terminar com uma quebra de linha ("invalid format" sem isso)
[IO.File]::WriteAllText("$Dir\\agent_key", (($Key -replace "`r`n", "`n").TrimEnd() + "`n"))
icacls "$Dir\\agent_key" /inheritance:r /grant:r "*S-1-5-18:(R)" "*S-1-5-32-544:(R)" | Out-Null    # SYSTEM e Administradores (vale em qualquer idioma)
@"
while (`$true) {{
  & "$Ssh" {ssh_opts} -o UserKnownHostsFile=NUL -i "$Dir\\agent_key"
  Start-Sleep -Seconds 5
}}
"@ | Set-Content -Path "$Dir\\tunnel.ps1"
$Action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Dir\\tunnel.ps1`""
# sobe junto com o Windows, como SYSTEM: não depende de ninguém fazer logon (servidor)
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "BastionAgent" -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName "BastionAgent"
Write-Host "Agente {a['name']} iniciado -> {suser}@{host}:{sport} (túnel {TUNNEL_BIND_HOST}:{tport}). Verifique o status no painel."
"""
    return {"mode": "reverse", "bash": bash, "powershell": powershell, "tunnel_port": tport,
            "public_key": a.get("agent_public_key", ""),
            "note": "" if s.get("public_host") else "Configure o host público do BastiON em 'Configurar BastiON' antes de instalar."}


# ---------- Devices ----------
def _normalize_device(data: dict, user: Optional[dict] = None) -> dict:
    if data.get("agent_id") == "":
        data["agent_id"] = None
    if data.get("device_type") not in DEVICE_TYPES:
        data["device_type"] = "other"
    if data.get("protocol") not in ("ssh", "telnet"):
        data["protocol"] = "ssh"
    if user is not None:
        data["owner_id"] = user["id"]
    return data


def _scope(user: dict) -> dict:
    """Mongo filter: every user (admins included) only sees what they created."""
    return {"owner_id": user["id"]}


def _is_viewer(user: dict) -> bool:
    return user.get("role") == "viewer"


async def _viewer_device_ids(user: dict) -> set:
    return await sharing.viewer_device_ids(db, user.get("view_maps") or [], user.get("view_dashboards") or [])


async def _check_view_device(user: dict, device_id: str):
    """Leitura de tráfego/óptica: dono do equipamento ou View com o equipamento num mapa/dashboard liberado."""
    if _is_viewer(user):
        if device_id not in await _viewer_device_ids(user):
            raise HTTPException(status_code=404, detail="Equipamento não encontrado")
        return
    await _get_device_for(user, device_id)


async def _get_device_for(user: dict, device_id: str) -> dict:
    d = await db.devices.find_one({"id": device_id, **_scope(user)}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    return d


@api.get("/devices")
async def list_devices(user: dict = Depends(get_current_user)):
    if _is_viewer(user):
        ids = await _viewer_device_ids(user)
        return await db.devices.find({"id": {"$in": list(ids)}}, {"_id": 0, "id": 1, "name": 1, "host": 1, "status": 1,
                                                                   "latency_ms": 1, "device_type": 1}).to_list(5000)
    docs = await db.devices.find(_scope(user), {"_id": 0}).to_list(5000)
    return [_public(d) for d in docs]


@api.post("/devices")
async def create_device(payload: DeviceCreateRequest, user: dict = Depends(get_current_user)):
    raw = payload.model_dump()
    src_id = raw.pop("copy_password_from", None)
    data = _normalize_device(_apply_secret(raw, None), user)
    if src_id and not payload.password and not payload.clear_password:
        src = await _get_device_for(user, src_id)  # 404 se não for do usuário
        data["password"] = src.get("password", "")
    if data.get("agent_id"):
        await _get_agent_for(user, data["agent_id"])
    doc = Device(**data).model_dump()
    await db.devices.insert_one(doc)
    return _public(doc)


def _truthy(v, default: bool = True) -> bool:
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "sim", "yes", "y", "s", "on")


@api.post("/devices/import")
async def import_devices(payload: dict, user: dict = Depends(get_current_user)):
    """Bulk import. payload = {"rows": [{name, host, port, username, password, device_type, tags, agent, description}]}"""
    rows = payload.get("rows") or []
    agents = await db.agents.find(_scope(user), {"_id": 0, "id": 1, "name": 1}).to_list(1000)
    agent_by_name = {a["name"].strip().lower(): a["id"] for a in agents}
    agent_ids = {a["id"] for a in agents}
    existing = await db.devices.find(_scope(user), {"_id": 0, "name": 1, "host": 1, "port": 1}).to_list(10000)
    seen = {(d["name"].strip().lower(), d["host"].strip(), int(d.get("port") or 22)) for d in existing}
    created, skipped, errors = 0, 0, []
    to_insert = []
    for i, r in enumerate(rows, start=1):
        name = str(r.get("name") or "").strip()
        host = str(r.get("host") or r.get("ip") or "").strip()
        if not name or not host:
            errors.append(f"Linha {i}: nome e host são obrigatórios")
            continue
        try:
            port = int(str(r.get("port") or 22).strip() or 22)
        except ValueError:
            errors.append(f"Linha {i}: porta inválida '{r.get('port')}'")
            continue
        key = (name.lower(), host, port)
        if key in seen:
            skipped += 1
            continue
        agent_ref = str(r.get("agent") or r.get("agent_id") or "").strip()
        agent_id = None
        if agent_ref:
            agent_id = agent_ref if agent_ref in agent_ids else agent_by_name.get(agent_ref.lower())
            if not agent_id:
                errors.append(f"Linha {i}: agente '{agent_ref}' não encontrado (device importado sem agente)")
        tags_raw = r.get("tags") or ""
        tags = [t.strip() for t in re.split(r"[;|,]", str(tags_raw)) if t.strip()] if not isinstance(tags_raw, list) else tags_raw
        dtype = str(r.get("device_type") or r.get("type") or "linux").strip().lower()
        pw = str(r.get("password") or "").strip()
        proto = str(r.get("protocol") or "ssh").strip().lower()
        doc = Device(**_normalize_device({
            "name": name, "host": host, "port": port, "protocol": proto,
            "username": str(r.get("username") or r.get("user") or "").strip(),
            "password": vault.encrypt(pw) if pw else "",
            "device_type": dtype, "tags": tags, "agent_id": agent_id,
            "description": str(r.get("description") or "").strip(),
            "backup_enabled": _truthy(r.get("backup_enabled"), True),
            "backup_command": str(r.get("backup_command") or "").strip() or None,
            "snmp_community": str(r.get("snmp_community") or "").strip(),
            "snmp_port": int(str(r.get("snmp_port") or 161).strip() or 161) if str(r.get("snmp_port") or "161").strip().isdigit() else 161,
        }, user)).model_dump()
        to_insert.append(doc)
        seen.add(key)
        created += 1
    if to_insert:
        await db.devices.insert_many(to_insert)
    return {"created": created, "skipped": skipped, "errors": errors}


@api.put("/devices/{device_id}")
async def update_device(device_id: str, payload: DeviceCreate, user: dict = Depends(get_current_user)):
    existing = await _get_device_for(user, device_id)
    data = _normalize_device(_apply_secret(payload.model_dump(), existing), user)
    if data.get("agent_id") and data["agent_id"] != existing.get("agent_id"):
        await _get_agent_for(user, data["agent_id"])
    data["owner_id"] = existing.get("owner_id") or user["id"]
    await db.devices.update_one({"id": device_id}, {"$set": data})
    return _public(await db.devices.find_one({"id": device_id}, {"_id": 0}))


@api.delete("/devices/{device_id}")
async def delete_device(device_id: str, user: dict = Depends(get_current_user)):
    r = await db.devices.delete_one({"id": device_id, **_scope(user)})
    return {"deleted": r.deleted_count}


EXPORT_COLUMNS = ["name", "host", "port", "protocol", "username", "password", "device_type",
                  "tags", "agent", "description", "backup_enabled", "backup_command", "snmp_community", "snmp_port"]


@api.post("/devices/export")
async def export_devices(payload: DeviceExportPayload, user: dict = Depends(get_current_user)):
    """CSV no mesmo formato aceito pelo /devices/import (ida e volta)."""
    q = dict(_scope(user))
    if payload.device_ids is not None:
        q["id"] = {"$in": payload.device_ids}
    devs = await db.devices.find(q, {"_id": 0}).sort("name", 1).to_list(10000)
    agents = await db.agents.find(_scope(user), {"_id": 0, "id": 1, "name": 1}).to_list(1000)
    agent_name = {a["id"]: a["name"] for a in agents}
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(EXPORT_COLUMNS)
    for d in devs:
        w.writerow([
            d.get("name", ""), d.get("host", ""), d.get("port", 22), d.get("protocol") or "ssh",
            d.get("username", ""),
            vault.decrypt(d.get("password", "")) if payload.include_passwords else "",
            d.get("device_type") or "linux", ";".join(d.get("tags") or []),
            agent_name.get(d.get("agent_id") or "", ""), d.get("description", ""),
            "true" if d.get("backup_enabled", True) is not False else "false",
            d.get("backup_command") or "",
            d.get("snmp_community") or "", d.get("snmp_port") or 161,
        ])
    if payload.include_passwords:
        logger.info("Export de equipamentos COM senhas por %s (%d itens)", user.get("email"), len(devs))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return PlainTextResponse(
        "\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="equipamentos-{stamp}.csv"',
                 "X-Export-Count": str(len(devs))},
    )


@api.post("/devices/bulk-delete")
async def bulk_delete_devices(payload: DeviceIdsPayload, user: dict = Depends(get_current_user)):
    if not payload.device_ids:
        return {"deleted": 0}
    r = await db.devices.delete_many({"id": {"$in": payload.device_ids}, **_scope(user)})
    return {"deleted": r.deleted_count}


@api.post("/devices/bulk-update")
async def bulk_update_devices(payload: DeviceBulkUpdate, user: dict = Depends(get_current_user)):
    if not payload.device_ids:
        return {"updated": 0}
    q = {"id": {"$in": payload.device_ids}, **_scope(user)}
    setf: dict = {}
    if payload.agent_id is not None:
        if payload.agent_id:
            await _get_agent_for(user, payload.agent_id)
            setf["agent_id"] = payload.agent_id
        else:
            setf["agent_id"] = None
    if payload.device_type:
        if payload.device_type not in DEVICE_TYPES:
            raise HTTPException(status_code=400, detail="Tipo de equipamento inválido")
        setf["device_type"] = payload.device_type
    if payload.protocol:
        if payload.protocol not in ("ssh", "telnet"):
            raise HTTPException(status_code=400, detail="Protocolo inválido")
        setf["protocol"] = payload.protocol
    if payload.port is not None:
        if not 1 <= payload.port <= 65535:
            raise HTTPException(status_code=400, detail="Porta inválida")
        setf["port"] = payload.port
    if payload.username is not None:
        setf["username"] = payload.username.strip()
    if payload.backup_enabled is not None:
        setf["backup_enabled"] = payload.backup_enabled
    add = [t.strip() for t in payload.add_tags if t.strip()]
    rem = [t.strip() for t in payload.remove_tags if t.strip()]
    matched = await db.devices.count_documents(q)
    if setf:
        await db.devices.update_many(q, {"$set": setf})
    # $addToSet e $pull no mesmo campo não podem ir na mesma operação
    if rem:
        await db.devices.update_many(q, {"$pull": {"tags": {"$in": rem}}})
    if add:
        await db.devices.update_many(q, {"$addToSet": {"tags": {"$each": add}}})
    return {"updated": matched}


async def _ping_device(dev: dict) -> Optional[float]:
    host, port = dev["host"], int(dev.get("port") or 22)
    if not dev.get("agent_id"):
        return await tcp_ping(host, port)
    try:
        hops, _ = await _device_hops(dev)
        w = SSHClientWrapper(hops[:-1])
        try:
            await w.connect()
            return await w.tcp_check(host, port)
        finally:
            await w.close()
    except Exception:
        return None


@api.post("/devices/{device_id}/ping")
async def ping_device(device_id: str, user: dict = Depends(get_current_user)):
    d = await _get_device_for(user, device_id)
    lat = await _ping_device(d)
    status = "online" if lat is not None else "offline"
    now = datetime.now(timezone.utc).isoformat()
    await db.devices.update_one({"id": device_id}, {"$set": {"status": status, "latency_ms": lat, "last_seen": now if lat else d.get("last_seen")}})
    return {"device_id": device_id, "status": status, "latency_ms": lat}


@api.post("/devices/ping-all")
async def ping_all_devices(payload: Optional[DeviceIdsPayload] = None, user: dict = Depends(get_current_user)):
    q = dict(_scope(user))
    if payload and payload.device_ids:
        q["id"] = {"$in": payload.device_ids}
    devs = await db.devices.find(q, {"_id": 0}).to_list(5000)
    results = []
    sem = asyncio.Semaphore(20)

    async def _one(dv):
        async with sem:
            lat = await _ping_device(dv)
        st = "online" if lat is not None else "offline"
        await db.devices.update_one({"id": dv["id"]}, {"$set": {"status": st, "latency_ms": lat}})
        results.append({"device_id": dv["id"], "status": st, "latency_ms": lat})
    await asyncio.gather(*[_one(dv) for dv in devs])
    return results


# ---------- SSH Key ----------
@api.get("/ssh-key")
async def get_ssh_key(user: dict = Depends(get_current_user)):
    doc = await db.config.find_one({"key": "ssh_key"}, {"_id": 0}) or {}
    return {
        "private_key": doc.get("private_key", "") if user.get("role") == "admin" else "",
        "public_key": doc.get("public_key", ""),
        "default_username": doc.get("default_username", "root"),
        "has_key": bool(doc.get("private_key")),
        "has_default_password": bool(doc.get("default_password")),
    }


@api.put("/ssh-key")
async def set_ssh_key(payload: SshKeyConfig, _: dict = Depends(require_admin)):
    existing = await db.config.find_one({"key": "ssh_key"}) or {}
    update = {
        "private_key": payload.private_key,
        "public_key": payload.public_key,
        "default_username": payload.default_username,
    }
    if payload.clear_default_password:
        update["default_password"] = ""
    elif payload.default_password:
        update["default_password"] = vault.encrypt(payload.default_password)
    else:
        update["default_password"] = existing.get("default_password", "")
    await db.config.update_one({"key": "ssh_key"}, {"$set": update}, upsert=True)
    return {"ok": True, "has_key": bool(payload.private_key), "has_default_password": bool(update["default_password"])}


# ---------- Scripts ----------
@api.get("/scripts")
async def list_scripts(_: dict = Depends(get_current_user)):
    return await db.scripts.find({}, {"_id": 0}).to_list(500)


@api.post("/scripts")
async def create_script(payload: ScriptCreate, _: dict = Depends(get_current_user)):
    s = Script(**payload.model_dump())
    doc = s.model_dump()
    await db.scripts.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.put("/scripts/{sid}")
async def update_script(sid: str, payload: ScriptCreate, _: dict = Depends(get_current_user)):
    await db.scripts.update_one({"id": sid}, {"$set": payload.model_dump()})
    return await db.scripts.find_one({"id": sid}, {"_id": 0})


@api.delete("/scripts/{sid}")
async def delete_script(sid: str, _: dict = Depends(get_current_user)):
    r = await db.scripts.delete_one({"id": sid})
    return {"deleted": r.deleted_count}


# ---------- Sessions ----------
@api.get("/sessions")
async def list_sessions(user: dict = Depends(get_current_user), limit: int = 100):
    q = {"user_id": user["id"]}
    return await db.sessions.find(q, {"_id": 0}).sort("started_at", -1).to_list(limit)


# ---------- Dashboard stats ----------
@api.get("/stats")
async def stats(user: dict = Depends(get_current_user)):
    sc = _scope(user)
    total_devices = await db.devices.count_documents(sc)
    online_devices = await db.devices.count_documents({**sc, "status": "online"})
    offline_devices = await db.devices.count_documents({**sc, "status": "offline"})
    total_agents = await db.agents.count_documents(sc)
    online_agents = await db.agents.count_documents({**sc, "status": "online"})
    sq = {"user_id": user["id"]}
    sessions_today = await db.sessions.count_documents({**sq,
        "started_at": {"$gte": datetime.now(timezone.utc).date().isoformat()}
    })
    recent = await db.sessions.find(sq, {"_id": 0}).sort("started_at", -1).to_list(8)
    return {
        "total_devices": total_devices,
        "online_devices": online_devices,
        "offline_devices": offline_devices,
        "total_agents": total_agents,
        "online_agents": online_agents,
        "sessions_today": sessions_today,
        "recent_sessions": recent,
    }


# ---------- Batch execution ----------
async def _get_ssh_key():
    doc = await db.config.find_one({"key": "ssh_key"}) or {}
    return doc.get("private_key", ""), doc.get("default_username", "root"), vault.decrypt(doc.get("default_password", ""))


async def _device_hops(dev: dict):
    priv, default_user, default_pw = await _get_ssh_key()
    hops: List[Hop] = []
    if dev.get("agent_id"):
        for ag in await _agent_chain(dev["agent_id"]):
            hops.append(_agent_hop(ag, priv))
    dtype = dev.get("device_type") or "linux"
    pw = vault.decrypt(dev.get("password", "")) or default_pw
    use_key = priv and (dtype == "linux" or not pw)
    hops.append(Hop(dev["host"], int(dev.get("port") or 22), dev.get("username") or default_user,
                    private_key=priv if use_key else None, password=pw or None,
                    legacy=dtype in LEGACY_TYPES, label=dev["name"]))
    return hops, dtype


async def _connect_device(dev: dict):
    hops, dtype = await _device_hops(dev)
    if dev.get("protocol") == "telnet":
        target = hops[-1]
        wrapper = TelnetClientWrapper(hops[:-1], target.host, target.port, username=target.username,
                                      password=target.password, device_type=dtype, label=dev["name"])
    else:
        wrapper = SSHClientWrapper(hops, device_type=dtype)
    await wrapper.connect()
    return wrapper


@api.post("/batch/execute")
async def batch_execute(payload: BatchExecPayload, user: dict = Depends(get_current_user)):
    command = payload.inline_command
    if payload.script_id and not command:
        s = await db.scripts.find_one({"id": payload.script_id}, {"_id": 0})
        if not s:
            raise HTTPException(status_code=404, detail="Script não encontrado")
        command = s["content"]
    if not command:
        raise HTTPException(status_code=400, detail="Comando ou script obrigatório")

    async def _run(dev_id: str):
        dev = await db.devices.find_one({"id": dev_id, **_scope(user)}, {"_id": 0})
        if not dev:
            return BatchResultItem(device_id=dev_id, device_name="?", host="?",
                                   ok=False, exit_status=-1, stdout="", stderr="",
                                   error="Equipamento não encontrado").model_dump()
        try:
            client_ = await _connect_device(dev)
            res = await client_.run_command(command, timeout=payload.timeout)
            await client_.close()
            await db.sessions.insert_one({
                "id": os.urandom(8).hex(),
                "user_id": user["id"], "user_email": user["email"],
                "device_id": dev["id"], "device_name": dev["name"],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": 0, "kind": "batch",
            })
            return BatchResultItem(
                device_id=dev["id"], device_name=dev["name"], host=dev["host"],
                ok=res["ok"], exit_status=res["exit_status"],
                stdout=res["stdout"] if isinstance(res["stdout"], str) else res["stdout"].decode("utf-8", "replace"),
                stderr=res["stderr"] if isinstance(res["stderr"], str) else res["stderr"].decode("utf-8", "replace"),
            ).model_dump()
        except Exception as e:
            return BatchResultItem(
                device_id=dev["id"], device_name=dev["name"], host=dev["host"],
                ok=False, exit_status=-1, stdout="", stderr="", error=str(e)
            ).model_dump()

    results = await asyncio.gather(*[_run(d) for d in payload.device_ids])
    return {"results": results}


# ---------- Looking Glass (interno) ----------
_lg_locks: dict = {}
_lg_hits: dict = {}
LG_PER_MIN = int(os.environ.get("LG_PER_MIN", "20"))
LG_TIMEOUT = int(os.environ.get("LG_TIMEOUT", "90"))


class LGQueryIn(BaseModel):
    device_id: str
    query: str
    target: str = ""
    v6: bool = False


class LGRoutersIn(BaseModel):
    device_ids: List[str]


class LGCommandsIn(BaseModel):
    commands: dict


async def _lg_overrides() -> dict:
    return (await db.config.find_one({"key": "lg"}, {"_id": 0}) or {}).get("commands") or {}


def _lg_router(d: dict) -> dict:
    return {"id": d["id"], "name": d["name"], "device_type": d.get("device_type"), "tags": d.get("tags") or [],
            "status": d.get("status")}


@api.get("/lg/routers")
async def lg_routers(user: dict = Depends(get_current_user)):
    """Roteadores liberados para consulta: valem para todos com o módulo, não só para o dono do equipamento."""
    devs = await db.devices.find({"looking_glass": True, "device_type": {"$in": lookingglass.VENDORS}}, {"_id": 0}).to_list(500)
    devs.sort(key=lambda d: d["name"].lower())
    return {"routers": [_lg_router(d) for d in devs],
            "queries": [{"key": k, "label": v[0], "needs_target": v[1], "allow_host": v[2], "allow_prefix": v[3]}
                        for k, v in lookingglass.QUERIES.items()],
            "can_manage": user.get("role") == "admin"}


@api.post("/lg/query")
async def lg_query(body: LGQueryIn, user: dict = Depends(get_current_user)):
    dev = await db.devices.find_one({"id": body.device_id, "looking_glass": True}, {"_id": 0})
    if not dev:
        raise HTTPException(status_code=404, detail="Roteador não está liberado no Looking Glass")
    try:
        cmd = lookingglass.build(dev.get("device_type") or "", body.query, body.target, await _lg_overrides(), body.v6)
    except lookingglass.LGError as e:
        raise HTTPException(status_code=400, detail=str(e))
    now = time.time()
    hits = [t for t in _lg_hits.get(user["id"], []) if now - t < 60]
    if len(hits) >= LG_PER_MIN:
        raise HTTPException(status_code=429, detail=f"Muitas consultas — limite de {LG_PER_MIN} por minuto. Aguarde um pouco.")
    _lg_hits[user["id"]] = hits + [now]
    lock = _lg_locks.setdefault(dev["id"], asyncio.Lock())
    started = datetime.now(timezone.utc)
    ok, out, err = False, "", ""
    async with lock:                                       # uma consulta por vez em cada roteador
        try:
            c = await _connect_device(dev)
            try:
                res = await c.run_command(cmd, timeout=LG_TIMEOUT, idle=2.0)
            finally:
                await c.close()
            raw = res["stdout"] if isinstance(res["stdout"], str) else res["stdout"].decode("utf-8", "replace")
            out = lookingglass.tidy(raw, cmd)[-60000:]
            ok = bool(res["ok"])
            err = "" if ok else ((res.get("stderr") or "").strip() or "O roteador não respondeu a tempo")
        except Exception as e:
            err = str(e) or type(e).__name__
    ended = datetime.now(timezone.utc)
    await db.sessions.insert_one({
        "id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"],
        "device_id": dev["id"], "device_name": dev["name"], "started_at": started.isoformat(),
        "ended_at": ended.isoformat(), "duration_seconds": int((ended - started).total_seconds()),
        "kind": "lg", "command": cmd,
    })
    return {"ok": ok, "router": dev["name"], "command": cmd, "output": out, "error": err,
            "seconds": round((ended - started).total_seconds(), 1)}


@api.get("/lg/admin")
async def lg_admin(_: dict = Depends(require_admin)):
    devs = await db.devices.find({"device_type": {"$in": lookingglass.VENDORS}}, {"_id": 0}).to_list(5000)
    devs.sort(key=lambda d: d["name"].lower())
    ov = await _lg_overrides()
    return {"devices": [{**_lg_router(d), "host": d.get("host"), "looking_glass": bool(d.get("looking_glass"))} for d in devs],
            "vendors": lookingglass.VENDORS, "template_keys": lookingglass.TEMPLATE_KEYS,
            "defaults": lookingglass.DEFAULT_COMMANDS, "overrides": ov}


@api.put("/lg/admin/routers")
async def lg_set_routers(body: LGRoutersIn, _: dict = Depends(require_admin)):
    ids = list(dict.fromkeys(body.device_ids))[:500]
    await db.devices.update_many({"looking_glass": True, "id": {"$nin": ids}}, {"$set": {"looking_glass": False}})
    if ids:
        await db.devices.update_many({"id": {"$in": ids}, "device_type": {"$in": lookingglass.VENDORS}},
                                     {"$set": {"looking_glass": True}})
    return {"ok": True, "count": await db.devices.count_documents({"looking_glass": True})}


@api.put("/lg/admin/commands")
async def lg_set_commands(body: LGCommandsIn, _: dict = Depends(require_admin)):
    try:
        ov = lookingglass.clean_overrides(body.commands)
    except lookingglass.LGError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.config.update_one({"key": "lg"}, {"$set": {"commands": ov}}, upsert=True)
    return {"ok": True, "overrides": ov}


# ---------- RPKI (Krill ao lado; o BastiON é a tela) ----------
class RpkiCaIn(BaseModel):
    handle: str
    label: str = ""


class RpkiXmlIn(BaseModel):
    xml: str
    name: str = "nicbr"


class RpkiRoasIn(BaseModel):
    added: List[dict] = []
    removed: List[dict] = []


def _rpki_http(e: "rpki.RpkiError"):
    return HTTPException(status_code=e.status, detail=str(e))


async def _rpki_log(user: dict, handle: str, action: str, detail: str):
    await db.rpki_log.insert_one({"id": os.urandom(8).hex(), "at": datetime.now(timezone.utc).isoformat(), "handle": handle,
                                  "user_email": user.get("email"), "action": action, "detail": detail[:2000]})


@api.get("/rpki/status")
async def rpki_status(user: dict = Depends(get_current_user)):
    out = {"configured": rpki.configured(), "online": False, "cas": [], "can_manage": user.get("role") == "admin"}
    if not out["configured"]:
        return out
    out.update(await rpki.info())
    if not out["online"]:
        return out
    try:
        handles = await rpki.list_cas()
    except rpki.RpkiError as e:
        out.update(online=False, error=str(e))
        return out
    labels = {d["handle"]: d.get("label", "") for d in await db.rpki_cas.find({}, {"_id": 0}).to_list(1000)}

    async def one(h):
        try:
            d = await rpki.ca_detail(h)
        except rpki.RpkiError as e:
            d = {"handle": h, "error": str(e), "asns": [], "ipv4": [], "ipv6": [], "parents": [], "repo": False}
        d["label"] = labels.get(h, "")
        return d
    out["cas"] = list(await asyncio.gather(*[one(h) for h in handles]))
    return out


@api.post("/rpki/cas")
async def rpki_create_ca(body: RpkiCaIn, user: dict = Depends(require_admin)):
    try:
        h = rpki.check_handle(body.handle)
        await rpki.create_ca(h)
    except rpki.RpkiError as e:
        raise _rpki_http(e)
    await db.rpki_cas.update_one({"handle": h}, {"$set": {"handle": h, "label": body.label.strip()[:80]}}, upsert=True)
    await _rpki_log(user, h, "ca_criada", body.label.strip()[:80])
    return {"ok": True, "handle": h}


@api.delete("/rpki/cas/{handle}")
async def rpki_delete_ca(handle: str, confirm: str = "", user: dict = Depends(require_admin)):
    if confirm != handle:
        raise HTTPException(status_code=400, detail="Digite o identificador da CA para confirmar")
    try:
        await rpki.delete_ca(handle)
    except rpki.RpkiError as e:
        raise _rpki_http(e)
    await db.rpki_cas.delete_one({"handle": handle})
    await _rpki_log(user, handle, "ca_removida", "")
    return {"ok": True}


@api.get("/rpki/cas/{handle}")
async def rpki_ca(handle: str, user: dict = Depends(get_current_user)):
    try:
        d = await rpki.ca_detail(handle)
        d["roas"] = await rpki.roas(handle) if d["parents"] else []
        an = await rpki.analysis(handle) if d["parents"] else {"available": False, "roa_state": {}, "announcements": []}
        if user.get("role") == "admin":
            d["child_request"] = await rpki.child_request(handle)
            d["publisher_request"] = await rpki.publisher_request(handle)
    except rpki.RpkiError as e:
        raise _rpki_http(e)
    for r in d["roas"]:
        r["status"] = an["roa_state"].get(f"{r['asn']}|{r['prefix']}|{r['max_length']}")
        r["warnings"] = rpki.roa_warnings(r)
    d["analysis_available"] = an.get("available", False)
    d["announcements"] = an["announcements"][:500]
    d["label"] = ((await db.rpki_cas.find_one({"handle": handle}, {"_id": 0})) or {}).get("label", "")
    d["log"] = await db.rpki_log.find({"handle": handle}, {"_id": 0}).sort("at", -1).to_list(30)
    return d


@api.post("/rpki/cas/{handle}/repo")
async def rpki_set_repo(handle: str, body: RpkiXmlIn, user: dict = Depends(require_admin)):
    try:
        await rpki.set_repo(handle, body.xml)
    except rpki.RpkiError as e:
        raise _rpki_http(e)
    await _rpki_log(user, handle, "repositorio_configurado", "")
    return {"ok": True}


@api.post("/rpki/cas/{handle}/parents")
async def rpki_add_parent(handle: str, body: RpkiXmlIn, user: dict = Depends(require_admin)):
    try:
        await rpki.add_parent(handle, body.name or "nicbr", body.xml)
    except rpki.RpkiError as e:
        raise _rpki_http(e)
    await _rpki_log(user, handle, "pai_vinculado", body.name or "nicbr")
    return {"ok": True}


@api.post("/rpki/cas/{handle}/roas/check")
async def rpki_check_roas(handle: str, body: RpkiRoasIn, user: dict = Depends(get_current_user)):
    """Valida e devolve os avisos antes de salvar (nada é alterado)."""
    try:
        d = await rpki.ca_detail(handle)
        added = [rpki.clean_roa(r) for r in body.added]
    except rpki.RpkiError as e:
        raise _rpki_http(e)
    return {"added": [{**r, "warnings": rpki.roa_warnings(r, d)} for r in added]}


@api.post("/rpki/cas/{handle}/roas")
async def rpki_update_roas(handle: str, body: RpkiRoasIn, user: dict = Depends(get_current_user)):
    if not body.added and not body.removed:
        raise HTTPException(status_code=400, detail="Nada para alterar")
    try:
        added = [rpki.clean_roa(r) for r in body.added]
        removed = [rpki.clean_roa(r) for r in body.removed]
        await rpki.update_roas(handle, added, removed)
    except rpki.RpkiError as e:
        raise _rpki_http(e)
    fmt = lambda r: f"AS{r['asn']} {r['prefix']} max /{r['max_length']}"
    await _rpki_log(user, handle, "roas", "; ".join([f"+ {fmt(r)}" for r in added] + [f"- {fmt(r)}" for r in removed]))
    return {"ok": True, "added": len(added), "removed": len(removed)}


RPKI_WATCH_HOURS = float(os.environ.get("RPKI_WATCH_HOURS", "6"))


async def _rpki_watch_once() -> List[str]:
    """Avisa quando um anúncio passa a ser INVÁLIDO ou quando a CA para de sincronizar. Só o que é novo."""
    if not rpki.configured():
        return []
    prev = set((await db.config.find_one({"key": "rpki_watch"}, {"_id": 0}) or {}).get("problems") or [])
    now, lines = set(), {}
    for h in await rpki.list_cas():
        d = await rpki.ca_detail(h)
        for p in d["parents"]:
            if p["ok"] is False:
                k = f"{h}|pai|{p['name']}"
                now.add(k)
                lines[k] = f"- {h}: sem sincronizar com {p['name']} ({p['error'] or 'erro'})"
        if d.get("repo_ok") is False:
            k = f"{h}|repo"
            now.add(k)
            lines[k] = f"- {h}: não está publicando ({d.get('repo_error') or 'erro'})"
        if d["parents"]:
            for a in (await rpki.analysis(h))["announcements"]:
                if a["level"] == "bad":
                    k = f"{h}|{a['asn']}|{a['prefix']}"
                    now.add(k)
                    lines[k] = f"- {h}: AS{a['asn']} {a['prefix']} — {a['text']}"
    await db.config.update_one({"key": "rpki_watch"}, {"$set": {"problems": sorted(now), "at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    fresh = [lines[k] for k in sorted(now - prev)]
    if fresh:
        await automation.send_alert(db, "⚠️ RPKI: anúncio inválido ou CA sem sincronizar", "\n".join(fresh[:30]),
                                    push_url="/rpki", push_tag="rpki")
    return fresh


async def _rpki_watch_loop():
    await asyncio.sleep(120)
    while True:
        try:
            await _rpki_watch_once()
        except Exception as e:
            logger.warning(f"rpki watch: {e}")
        await asyncio.sleep(RPKI_WATCH_HOURS * 3600)


# ---------- Identificar fabricante por lista de IPs ----------
_scan_jobs: dict = {}
SCAN_PARALLEL = int(os.environ.get("SCAN_PARALLEL", "8"))


class ScanIn(BaseModel):
    targets: str
    username: str = ""
    password: str = ""
    protocol: str = "auto"
    port: Optional[int] = None
    agent_id: Optional[str] = None


def _scan_public(j: dict) -> dict:
    return {k: j[k] for k in ("id", "total", "done", "finished", "results", "invalid", "protocol", "username", "agent_id",
                              "agent_name", "error", "started_at")}


async def _scan_run(job: dict, targets: List[dict], password: str, hops: List[Hop]):
    chain = None
    try:
        tunnel = None
        if hops:
            chain = SSHClientWrapper(hops)
            await chain.connect()
            tunnel = chain.conn
        sem = asyncio.Semaphore(SCAN_PARALLEL)

        async def one(i: int, t: dict):
            async with sem:
                if job.get("cancel"):
                    r = {"host": t["host"], "name": t.get("name") or "", "status": "skipped", "error": "cancelado"}
                else:
                    try:
                        r = await asyncio.wait_for(vendorscan.probe(t, job["protocol"], job["username"], password, tunnel), 150)
                    except Exception as e:
                        r = {"host": t["host"], "name": t.get("name") or "", "status": "error",
                             "error": "demorou demais" if isinstance(e, asyncio.TimeoutError) else str(e)[:140]}
            job["results"][i] = r
            job["done"] += 1
        await asyncio.gather(*[one(i, t) for i, t in enumerate(targets)])
    except Exception as e:
        job["error"] = str(e)[:300]
    finally:
        job["finished"] = True
        if chain:
            await chain.close()


@api.post("/discover/scan")
async def scan_start(body: ScanIn, user: dict = Depends(get_current_user)):
    if body.protocol not in ("ssh", "telnet", "auto"):
        raise HTTPException(status_code=400, detail="Protocolo inválido")
    if body.port is not None and not 1 <= body.port <= 65535:
        raise HTTPException(status_code=400, detail="Porta inválida")
    try:
        targets, invalid = vendorscan.parse_targets(body.targets, body.port)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not targets:
        raise HTTPException(status_code=400, detail="Cole pelo menos um IP (um por linha)")
    priv, default_user, default_pw = await _get_ssh_key()
    username, password = body.username.strip() or default_user, body.password or default_pw
    if not username or not password:
        raise HTTPException(status_code=400, detail="Informe usuário e senha (ou defina a credencial padrão em Chave SSH Global)")
    if sum(1 for j in _scan_jobs.values() if j["owner"] == user["id"] and not j["finished"]) >= 1:
        raise HTTPException(status_code=409, detail="Já existe uma identificação sua em andamento — aguarde terminar ou cancele")
    hops, agent_name = [], ""
    if body.agent_id:
        ag = await _get_agent_for(user, body.agent_id)
        agent_name = ag["name"]
        hops = [_agent_hop(a, priv) for a in await _agent_chain(body.agent_id)]
    now = time.time()
    for k in [k for k, j in _scan_jobs.items() if now - j["ts"] > 3600]:
        _scan_jobs.pop(k, None)
    job = {"id": os.urandom(8).hex(), "owner": user["id"], "ts": now, "total": len(targets), "done": 0, "finished": False,
           "results": [None] * len(targets), "invalid": invalid[:50], "protocol": body.protocol, "username": username,
           "agent_id": body.agent_id or None, "agent_name": agent_name, "error": "",
           "started_at": datetime.now(timezone.utc).isoformat()}
    _scan_jobs[job["id"]] = job
    _bg(_scan_run(job, targets, password, hops))
    await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"], "device_id": None,
                                  "device_name": f"Identificação de fabricante — {len(targets)} IP(s)" + (f" via {agent_name}" if agent_name else ""),
                                  "started_at": job["started_at"], "ended_at": job["started_at"], "duration_seconds": 0, "kind": "scan"})
    return _scan_public(job)


def _scan_job(job_id: str, user: dict) -> dict:
    j = _scan_jobs.get(job_id)
    if not j or j["owner"] != user["id"]:
        raise HTTPException(status_code=404, detail="Identificação não encontrada (expira após 1 hora ou ao reiniciar o BastiON)")
    return j


@api.get("/discover/scan/{job_id}")
async def scan_get(job_id: str, user: dict = Depends(get_current_user)):
    return _scan_public(_scan_job(job_id, user))


@api.post("/discover/scan/{job_id}/cancel")
async def scan_cancel(job_id: str, user: dict = Depends(get_current_user)):
    _scan_job(job_id, user)["cancel"] = True
    return {"ok": True}


# ---------- Área de Trabalho Remota (RDP pelo navegador, via guacd) ----------
_rdp_tickets: dict = {}


class RdpHostIn(BaseModel):
    name: str
    host: str
    port: int = 3389
    username: str = ""
    domain: str = ""
    password: Optional[str] = None            # None = não mexe na senha salva; "" = apaga
    agent_id: Optional[str] = None
    security: str = "any"
    layout: str = "pt-br-qwerty"
    tags: List[str] = []


class RdpConnectIn(BaseModel):
    host_id: Optional[str] = None
    host: str = ""
    port: int = 3389
    username: str = ""
    domain: str = ""
    password: str = ""
    agent_id: Optional[str] = None
    security: str = "any"
    layout: str = "pt-br-qwerty"
    admin: bool = False


def _rdp_public(d: dict) -> dict:
    return {**{k: v for k, v in d.items() if k not in ("password", "_id")}, "has_password": bool(d.get("password"))}


async def _rdp_host_doc(body: RdpHostIn, user: dict, existing: Optional[dict] = None) -> dict:
    try:
        host, port = rdp.clean_target(body.host, body.port)
    except rdp.RdpError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Dê um nome para a conexão")
    if body.agent_id:
        await _get_agent_for(user, body.agent_id)
    username, domain = rdp.split_user(body.username, body.domain)
    doc = {"name": body.name.strip()[:80], "host": host, "port": port, "username": username[:120], "domain": domain[:120],
           "agent_id": body.agent_id or None, "security": body.security if body.security in rdp.SECURITY else "any",
           "layout": body.layout if body.layout in rdp.LAYOUTS else "pt-br-qwerty",
           "tags": [t.strip()[:30] for t in body.tags if t.strip()][:10]}
    if body.password is None:
        doc["password"] = (existing or {}).get("password", "")
    else:
        doc["password"] = vault.encrypt(body.password) if body.password else ""
    return doc


@api.get("/rdp/hosts")
async def rdp_hosts(user: dict = Depends(get_current_user)):
    docs = await db.rdp_hosts.find(_scope(user), {"_id": 0}).to_list(2000)
    docs.sort(key=lambda d: d["name"].lower())
    ok = True
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(rdp.GUACD_HOST, rdp.GUACD_PORT), 2)
        w.close()
    except Exception:
        ok = False
    return {"hosts": [_rdp_public(d) for d in docs], "guacd": ok, "layouts": list(rdp.LAYOUTS), "security": list(rdp.SECURITY)}


@api.post("/rdp/hosts")
async def rdp_host_create(body: RdpHostIn, user: dict = Depends(get_current_user)):
    doc = {"id": str(uuid.uuid4()), "owner_id": user["id"], "created_at": datetime.now(timezone.utc).isoformat(),
           **await _rdp_host_doc(body, user)}
    await db.rdp_hosts.insert_one(dict(doc))
    return _rdp_public(doc)


@api.put("/rdp/hosts/{host_id}")
async def rdp_host_update(host_id: str, body: RdpHostIn, user: dict = Depends(get_current_user)):
    cur = await db.rdp_hosts.find_one({"id": host_id, **_scope(user)}, {"_id": 0})
    if not cur:
        raise HTTPException(status_code=404, detail="Conexão não encontrada")
    upd = await _rdp_host_doc(body, user, cur)
    await db.rdp_hosts.update_one({"id": host_id}, {"$set": upd})
    return _rdp_public({**cur, **upd})


@api.delete("/rdp/hosts/{host_id}")
async def rdp_host_delete(host_id: str, user: dict = Depends(get_current_user)):
    r = await db.rdp_hosts.delete_one({"id": host_id, **_scope(user)})
    if not r.deleted_count:
        raise HTTPException(status_code=404, detail="Conexão não encontrada")
    return {"ok": True}


@api.post("/rdp/connect")
async def rdp_connect(body: RdpConnectIn, user: dict = Depends(get_current_user)):
    """Prepara a sessão e devolve um bilhete de uso único (60 s) para o WebSocket. A senha não volta ao navegador."""
    if body.host_id:
        h = await db.rdp_hosts.find_one({"id": body.host_id, **_scope(user)}, {"_id": 0})
        if not h:
            raise HTTPException(status_code=404, detail="Conexão não encontrada")
        t = {"name": h["name"], "host": h["host"], "port": h["port"], "username": body.username.strip() or h.get("username", ""),
             "domain": h.get("domain", ""), "password": body.password or vault.decrypt(h.get("password", "")),
             "agent_id": h.get("agent_id"), "security": h.get("security", "any"), "layout": h.get("layout", "pt-br-qwerty")}
        if body.username.strip():
            t["username"], t["domain"] = rdp.split_user(body.username, body.domain or h.get("domain", ""))
    else:
        try:
            host, port = rdp.clean_target(body.host, body.port)
        except rdp.RdpError as e:
            raise HTTPException(status_code=400, detail=str(e))
        username, domain = rdp.split_user(body.username, body.domain)
        t = {"name": host, "host": host, "port": port, "username": username, "domain": domain, "password": body.password,
             "agent_id": body.agent_id or None, "security": body.security, "layout": body.layout}
    if t["agent_id"]:
        await _get_agent_for(user, t["agent_id"])
    if not t["username"] or not t["password"]:
        raise HTTPException(status_code=400, detail="Informe usuário e senha")
    now = time.time()
    for k in [k for k, v in _rdp_tickets.items() if now - v["ts"] > 60]:
        _rdp_tickets.pop(k, None)
    ticket = os.urandom(18).hex()
    _rdp_tickets[ticket] = {**t, "admin": body.admin, "ts": now, "user_id": user["id"]}
    return {"ticket": ticket, "name": t["name"], "target": f"{t['host']}:{t['port']}"}


@app.websocket("/api/ws/rdp/{ticket}")
async def ws_rdp(ws: WebSocket, ticket: str, token: str = Query(...), w: int = Query(1280), h: int = Query(720)):
    await ws.accept(subprotocol="guacamole")
    await ws.send_text(rdp.tunnel_hello())

    async def fail(msg: str, code: str = "519"):
        try:
            await ws.send_text(rdp.enc("error", msg, code))
            await ws.close()
        except Exception:
            pass
    try:
        payload = decode_token(token)
    except HTTPException:
        return await fail("Sessão do BastiON inválida — entre novamente", "769")
    u = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "role": 1, "token_version": 1, "modules": 1})
    if not u or int(payload.get("tv", 0)) != int(u.get("token_version", 0)) or u.get("role") == "viewer" \
            or not auth_mod.has_module(u, "rdp"):
        return await fail("Seu usuário não tem acesso à Área de Trabalho Remota", "771")
    t = _rdp_tickets.pop(ticket, None)
    if not t or t["user_id"] != payload["sub"] or time.time() - t["ts"] > 60:
        return await fail("Pedido de conexão expirado — clique em Conectar de novo", "776")

    width, height = rdp.clamp_size(w, h)
    session_id, started = os.urandom(8).hex(), datetime.now(timezone.utc)
    await db.sessions.insert_one({"id": session_id, "user_id": payload["sub"], "user_email": payload["email"], "device_id": None,
                                  "device_name": f"RDP {t['name']} ({t['host']}:{t['port']}) como {t['username']}",
                                  "started_at": started.isoformat(), "ended_at": None, "duration_seconds": None, "kind": "rdp"})
    tun, g = None, rdp.Guacd()
    try:
        host, port = t["host"], t["port"]
        if t["agent_id"]:
            priv, _, _ = await _get_ssh_key()
            wrapper = SSHClientWrapper([_agent_hop(a, priv) for a in await _agent_chain(t["agent_id"])])
            await wrapper.connect()
            tun = webproxy.Tunnel(wrapper)
            host, port = "127.0.0.1", await tun.local(t["host"], t["port"])
        await g.open(rdp.rdp_params(host, port, t["username"], t["password"], t["domain"], width, height,
                                    t["security"], t["layout"], t["admin"]), width, height)
        t["password"] = ""

        async def down():
            while True:
                block = await g.read()
                if block is None:
                    break
                await ws.send_text(block)

        async def up():
            while True:
                msg = await ws.receive_text()
                if rdp.is_internal(msg):
                    if "4.ping" in msg[:12]:
                        await ws.send_text(msg)
                    continue
                g.write(msg)
        tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
        for d in done:
            e = d.exception()
            if e and not isinstance(e, WebSocketDisconnect):
                logger.info(f"rdp {t['host']}: {type(e).__name__}: {e}")
    except rdp.RdpError as e:
        await fail(str(e))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await fail(str(e)[:200] or type(e).__name__)
    finally:
        await g.close()
        if tun:
            await tun.close()
        try:
            await ws.close()
        except Exception:
            pass
        ended = datetime.now(timezone.utc)
        await db.sessions.update_one({"id": session_id}, {"$set": {"ended_at": ended.isoformat(),
                                                                  "duration_seconds": int((ended - started).total_seconds())}})


# ---------- Automation: settings, alerts, backups, scheduler ----------
async def _ping_everything() -> List[dict]:
    """Ping all agents then devices through their chains; return status transitions."""
    transitions: List[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    agents = await db.agents.find({}, {"_id": 0}).to_list(1000)
    sem = asyncio.Semaphore(10)

    async def _ag(a):
        async with sem:
            lat = await _ping_agent(a)
        st = "online" if lat is not None else "offline"
        await db.agents.update_one({"id": a["id"]}, {"$set": {"status": st, "latency_ms": lat, "last_seen": now if lat else a.get("last_seen")}})
        if a.get("status") in ("online", "offline") and a.get("status") != st:
            transitions.append({"kind": "agent", "name": a["name"], "status": st,
                                "detail": f"{a.get('location', '')} · {'túnel reverso porta ' + str(a.get('tunnel_port')) if a.get('mode') == 'reverse' else a.get('host', '')}"})
    await asyncio.gather(*[_ag(a) for a in agents])

    devs = await db.devices.find({}, {"_id": 0}).to_list(5000)

    async def _dv(d):
        async with sem:
            lat = await _ping_device(d)
        st = "online" if lat is not None else "offline"
        await db.devices.update_one({"id": d["id"]}, {"$set": {"status": st, "latency_ms": lat, "last_seen": now if lat else d.get("last_seen")}})
        if d.get("status") in ("online", "offline") and d.get("status") != st:
            transitions.append({"kind": "device", "name": d["name"], "status": st, "detail": f"{d['host']}:{d.get('port', 22)}"})
    await asyncio.gather(*[_dv(d) for d in devs])
    return transitions


async def _backup_device(dev: dict) -> dict:
    cmd = automation.backup_command_for(dev)
    if not cmd:
        return {"device_id": dev["id"], "device_name": dev["name"], "ok": False, "skipped": True,
                "error": "Sem comando de backup para este tipo (defina em 'comando de backup')"}
    try:
        c = await _connect_device(dev)
        try:
            res = await c.run_command(cmd, timeout=180)
        finally:
            await c.close()
        out = res["stdout"] if isinstance(res["stdout"], str) else res["stdout"].decode("utf-8", "replace")
        if not res["ok"] or not out.strip():
            err = (res.get("stderr") or "").strip() or "Saída vazia"
            return await automation.store_backup(db, dev, "", False, err)
        return await automation.store_backup(db, dev, out, True)
    except Exception as e:
        return await automation.store_backup(db, dev, "", False, str(e))


async def _backup_many(device_ids: Optional[List[str]] = None) -> List[dict]:
    q = {"id": {"$in": device_ids}} if device_ids else {"backup_enabled": {"$ne": False}}
    devs = await db.devices.find(q, {"_id": 0}).to_list(5000)
    if not device_ids:
        devs = [d for d in devs if automation.backup_command_for(d)]
    sem = asyncio.Semaphore(5)

    async def _one(d):
        async with sem:
            return await _backup_device(d)
    results = await asyncio.gather(*[_one(d) for d in devs])
    return [{k: v for k, v in r.items() if k != "content"} for r in results]


async def _optics_names():
    return await optics_collector.targets()


async def _send_daily_report() -> dict:
    title, text, data = await dailyreport.build(db, _optics_names)
    res = await automation.send_alert(db, title, text[:3800], push_url="/", push_tag="daily-report")
    return {"title": title, "text": text, "data": data, "results": res}


async def _backup_all_then_cloud() -> List[dict]:
    """Backup diário e, em seguida, o envio das tags escolhidas para o drive na nuvem."""
    results = await _backup_many()
    _bg(cloudsync.run(db, automation.send_alert, "backup diário"))
    return results


scheduler = automation.Scheduler(db, _ping_everything, _backup_all_then_cloud, _send_daily_report)


async def _telegram_token() -> str:
    s = await automation.get_settings(db)
    return vault.decrypt(s.get("telegram_bot_token", ""))

assistant = ai_assistant.TelegramAssistant(db, _connect_device, _telegram_token,
                                           snmp_client=lambda dev: _snmp_client(dev),
                                           flow_query=lambda *a: _flow_for_ai(*a))
web_assistant = ai_assistant.WebAssistant(db, _connect_device, snmp_client=lambda dev: _snmp_client(dev),
                                          flow_query=lambda *a: _flow_for_ai(*a))


# ---------- Mapas (weathermap) e monitoramento de interfaces por SNMP ----------
agent_pool = monitoring.SshAgentPool(lambda hops: SSHClientWrapper(hops, device_type="linux"))


async def _snmp_client(dev: dict, settings: Optional[dict] = None):
    s = settings or await monitoring.get_settings(db)
    community = (dev.get("snmp_community") or s.get("default_community") or "").strip()
    if not community:
        raise snmp_service.SnmpError("community SNMP não configurada (no equipamento ou a padrão em Mapas → Configurações)")
    port = int(dev.get("snmp_port") or 161)
    if not dev.get("agent_id"):
        return snmp_service.SnmpClient(dev["host"], community, port)
    # atrás de agente: SNMP (UDP) não passa no túnel SSH -> net-snmp roda no próprio agente
    hops, _ = await _device_hops(dev)
    key = tuple(h.label for h in hops[:-1]) + tuple(f"{h.host}:{h.port}" for h in hops[:-1])
    run = await agent_pool.runner(key, hops[:-1])
    return snmp_service.AgentSnmpClient(run, dev["host"], community, port)


net_monitor = monitoring.Monitor(db, _snmp_client, automation.send_alert)


@api.get("/devices/{device_id}/interfaces")
async def device_interfaces(device_id: str, refresh: bool = False, user: dict = Depends(get_current_user)):
    dev = await _get_device_for(user, device_id)
    cached = await db.device_ifaces.find_one({"device_id": device_id}, {"_id": 0})
    if cached and not refresh:
        return cached
    try:
        info = await asyncio.wait_for(snmp_service.discover_interfaces(await _snmp_client(dev)), timeout=90)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="SNMP demorou demais para responder")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SNMP: {e}")
    doc = {"device_id": device_id, "at": datetime.now(timezone.utc).isoformat(), **info}
    await db.device_ifaces.update_one({"device_id": device_id}, {"$set": doc}, upsert=True)
    return doc


@api.post("/devices/{device_id}/snmp-test")
async def device_snmp_test(device_id: str, user: dict = Depends(get_current_user)):
    dev = await _get_device_for(user, device_id)
    try:
        c = await _snmp_client(dev)
        v = await asyncio.wait_for(c.get([snmp_service.SYS_NAME, snmp_service.SYS_DESCR]), timeout=20)
        name, descr = v.get(snmp_service.SYS_NAME), v.get(snmp_service.SYS_DESCR)
        return {"ok": True, "sys_name": name if isinstance(name, str) else None,
                "sys_descr": (descr if isinstance(descr, str) else "")[:200], "via_agent": bool(dev.get("agent_id"))}
    except Exception as e:
        return {"ok": False, "error": str(e), "via_agent": bool(dev.get("agent_id"))}


# ----- mapas -----
async def _map_for(user: dict, map_id: str) -> dict:
    if _is_viewer(user):
        m = await db.maps.find_one({"id": map_id}, {"_id": 0}) if map_id in (user.get("view_maps") or []) else None
    else:
        m = await db.maps.find_one({"id": map_id, "owner_id": user["id"]}, {"_id": 0})
    if not m:
        raise HTTPException(status_code=404, detail="Mapa não encontrado")
    return m


@api.get("/maps")
async def list_maps(user: dict = Depends(get_current_user)):
    q = {"id": {"$in": user.get("view_maps") or []}} if _is_viewer(user) else {"owner_id": user["id"]}
    maps = await db.maps.find(q, {"_id": 0}).sort("name", 1).to_list(500)
    return [{"id": m["id"], "name": m["name"], "description": m.get("description", ""),
             "nodes": len(m.get("nodes", [])), "links": len(m.get("links", [])), "updated_at": m.get("updated_at")} for m in maps]


@api.post("/maps")
async def create_map(payload: MapCreate, user: dict = Depends(get_current_user)):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Nome obrigatório")
    now = datetime.now(timezone.utc).isoformat()
    doc = {"id": os.urandom(8).hex(), "owner_id": user["id"], "name": payload.name.strip(),
           "description": payload.description.strip(), "nodes": [], "links": [], "created_at": now, "updated_at": now}
    await db.maps.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


@api.get("/maps/{map_id}")
async def get_map(map_id: str, user: dict = Depends(get_current_user)):
    return await _map_for(user, map_id)


@api.put("/maps/{map_id}")
async def update_map(map_id: str, payload: MapUpdate, user: dict = Depends(get_current_user)):
    await _map_for(user, map_id)
    mine = set(await _my_device_ids(user))
    nodes = []
    for n in payload.nodes:
        nd = n.model_dump()
        if nd["kind"] == "device":
            if nd.get("device_id") not in mine:
                raise HTTPException(status_code=400, detail="Equipamento inválido no mapa")
        else:
            nd["device_id"] = None
        nd["x"], nd["y"] = round(float(nd["x"]), 1), round(float(nd["y"]), 1)
        nodes.append(nd)
    node_ids = {n["id"] for n in nodes}
    links = []
    for ln in payload.links:
        ld = ln.model_dump(by_alias=True)
        if ld["from"] not in node_ids or ld["to"] not in node_ids or ld["from"] == ld["to"]:
            continue  # link órfão (nó removido)
        links.append(ld)
    upd = {"name": payload.name.strip() or "Mapa", "description": payload.description.strip(),
           "nodes": nodes, "links": links, "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.maps.update_one({"id": map_id}, {"$set": upd})
    return await _map_for(user, map_id)


@api.delete("/maps/{map_id}")
async def delete_map(map_id: str, user: dict = Depends(get_current_user)):
    r = await db.maps.delete_one({"id": map_id, "owner_id": user["id"]})
    return {"deleted": r.deleted_count}


@api.post("/maps/{map_id}/duplicate")
async def duplicate_map(map_id: str, user: dict = Depends(get_current_user)):
    m = await _map_for(user, map_id)
    now = datetime.now(timezone.utc).isoformat()
    m.update({"id": os.urandom(8).hex(), "name": f"{m['name']} (cópia)", "created_at": now, "updated_at": now})
    await db.maps.insert_one(dict(m))
    m.pop("_id", None)
    return m


@api.get("/maps/{map_id}/live")
async def map_live(map_id: str, user: dict = Depends(get_current_user)):
    m = await _map_for(user, map_id)
    dev_ids = [n["device_id"] for n in m.get("nodes", []) if n.get("device_id")]
    devs = {d["id"]: d for d in await db.devices.find({"id": {"$in": dev_ids}}, {"_id": 0, "id": 1, "name": 1, "host": 1, "status": 1, "latency_ms": 1}).to_list(1000)}
    node_dev = {n["id"]: n.get("device_id") for n in m.get("nodes", [])}
    nodes = {}
    for n in m.get("nodes", []):
        d = devs.get(n.get("device_id"))
        if d:
            nodes[n["id"]] = {"status": d.get("status") or "unknown", "latency_ms": d.get("latency_ms"),
                              "name": d["name"], "host": d["host"], "snmp_error": net_monitor.dev_errors.get(d["id"])}
    links = {}
    for ln in m.get("links", []):
        a_dev, b_dev = node_dev.get(ln["from"]), node_dev.get(ln["to"])
        fi, ti = ln.get("from_if"), ln.get("to_if")
        la = net_monitor.iface_live(a_dev, fi["index"]) if a_dev and fi else None
        lb = net_monitor.iface_live(b_dev, ti["index"]) if b_dev and ti else None
        # tráfego A→B = saída da interface em A (ou entrada da interface em B)
        if la:
            ab, ba = la.get("out_bps"), la.get("in_bps")
        elif lb:
            ab, ba = lb.get("in_bps"), lb.get("out_bps")
        else:
            ab = ba = None
        cap = ln.get("capacity_mbps") or (fi or {}).get("speed_mbps") or (ti or {}).get("speed_mbps")
        util = (lambda v: round(v / (cap * 1e6) * 100, 1) if (v is not None and cap) else None)
        opers = [x.get("oper") for x in (la, lb) if x]
        errors = [net_monitor.dev_errors[d] for d in (a_dev if fi else None, b_dev if ti else None) if d in net_monitor.dev_errors]
        links[ln["id"]] = {
            "ab_bps": ab, "ba_bps": ba, "ab_pct": util(ab), "ba_pct": util(ba), "capacity_mbps": cap,
            "oper_a": la.get("oper") if la else None, "oper_b": lb.get("oper") if lb else None,
            "down": any(o in monitoring.DOWN_STATES for o in opers),
            "stale": bool((la and la.get("stale")) or (lb and lb.get("stale"))),
            "error": errors[0] if errors else None,
            "collecting": not (fi or ti) or (la is None and lb is None),
            "optics_a": optics_collector.get_live(a_dev, fi["index"]) if a_dev and fi else None,
            "optics_b": optics_collector.get_live(b_dev, ti["index"]) if b_dev and ti else None,
        }
    s = await monitoring.get_settings(db)
    return {"nodes": nodes, "links": links, "interval_sec": s["interval_sec"], "enabled": s["enabled"],
            "last_tick": net_monitor.last_tick.isoformat() if net_monitor.last_tick else None}


@api.get("/monitor/history")
async def monitor_history(device_id: str, if_index: int, minutes: int = 60, user: dict = Depends(get_current_user)):
    await _get_device_for(user, device_id)
    since = datetime.now(timezone.utc) - timedelta(minutes=max(5, min(minutes, 48 * 60)))
    rows = await db.if_samples.find({"device_id": device_id, "if_index": int(if_index), "ts": {"$gt": since}},
                                    {"_id": 0, "ts": 1, "in_bps": 1, "out_bps": 1}).sort("ts", 1).to_list(20000)
    return [{"t": r["ts"].isoformat() if hasattr(r["ts"], "isoformat") else r["ts"], "in": r["in_bps"], "out": r["out_bps"]} for r in rows]


# ----- configurações e alarmes -----
@api.get("/monitor/settings")
async def get_monitor_settings(user: dict = Depends(get_current_user)):
    s = await monitoring.get_settings(db)
    if user.get("role") != "admin":
        s.pop("default_community", None)
    return {**s, "last_tick": net_monitor.last_tick.isoformat() if net_monitor.last_tick else None,
            "last_duration": net_monitor.last_duration, "busy": net_monitor.busy,
            "errors": len(net_monitor.dev_errors), "is_admin": user.get("role") == "admin"}


@api.put("/monitor/settings")
async def put_monitor_settings(payload: MonitorSettings, _: dict = Depends(require_admin)):
    data = payload.model_dump()
    data["interval_sec"] = max(10, min(int(data["interval_sec"]), 3600))
    data["confirm_polls"] = max(1, min(int(data["confirm_polls"]), 10))
    data["default_community"] = data["default_community"].strip()
    data["history_days"] = max(1, min(int(data["history_days"]), 90))
    await db.config.update_one({"key": "monitor"}, {"$set": data}, upsert=True)
    await _apply_retention()
    return await monitoring.get_settings(db)


@api.post("/monitor/poll-now")
async def monitor_poll_now(_: dict = Depends(get_current_user)):
    if net_monitor.busy:
        return {"started": False, "reason": "Coleta já em andamento"}
    asyncio.create_task(net_monitor.tick())
    return {"started": True}


@api.get("/monitor/interfaces")
async def monitored_interfaces(user: dict = Depends(get_current_user)):
    mine = await _my_device_ids(user)
    rows = await db.if_monitors.find({"device_id": {"$in": mine}}, {"_id": 0}).to_list(5000)
    names = {d["id"]: d["name"] for d in await db.devices.find({"id": {"$in": list({r["device_id"] for r in rows})}}, {"_id": 0, "id": 1, "name": 1}).to_list(5000)}
    out = []
    for r in rows:
        lv = net_monitor.iface_live(r["device_id"], r["if_index"]) or {}
        out.append({**r, "device_name": names.get(r["device_id"], "?"), "in_bps": lv.get("in_bps"), "out_bps": lv.get("out_bps"),
                    "oper": lv.get("oper") or r.get("last_oper"), "snmp_error": net_monitor.dev_errors.get(r["device_id"])})
    return sorted(out, key=lambda x: (x["device_name"].lower(), x["if_index"]))


@api.put("/devices/{device_id}/monitor")
async def set_device_monitor(device_id: str, payload: DeviceMonitorPayload, user: dict = Depends(get_current_user)):
    dev = await _get_device_for(user, device_id)
    wanted = {i.index: i for i in payload.interfaces}
    current = {r["if_index"] for r in await db.if_monitors.find({"device_id": device_id}, {"_id": 0, "if_index": 1}).to_list(5000)}
    removed = [i for i in current if i not in wanted]
    if removed:
        await db.if_monitors.delete_many({"device_id": device_id, "if_index": {"$in": removed}})
    for idx, i in wanted.items():
        if idx in current:
            await db.if_monitors.update_one({"device_id": device_id, "if_index": idx}, {"$set": {"if_name": i.name, "alias": i.alias}})
        else:
            await db.if_monitors.insert_one({"device_id": device_id, "if_index": idx, "if_name": i.name, "alias": i.alias,
                                             "owner_id": dev.get("owner_id"), "created_at": datetime.now(timezone.utc).isoformat()})
    return {"monitored": len(wanted), "removed": len(removed)}


@api.get("/monitor/events")
async def monitor_events(user: dict = Depends(get_current_user), limit: int = 100):
    mine = await _my_device_ids(user)
    return await db.if_events.find({"device_id": {"$in": mine}}, {"_id": 0}).sort("at", -1).to_list(max(1, min(limit, 1000)))

# ---------- Retenção do histórico (TTL do Mongo acompanha a configuração) ----------
async def _ensure_ttl(coll: str, seconds: int):
    info = await db[coll].index_information()
    idx = info.get("ts_1")
    if idx is None:
        await db[coll].create_index("ts", expireAfterSeconds=seconds)
    elif idx.get("expireAfterSeconds") != seconds:
        await db.command("collMod", coll, index={"keyPattern": {"ts": 1}, "expireAfterSeconds": seconds})


async def _apply_retention():
    s = await monitoring.get_settings(db)
    seconds = int(s.get("history_days") or 7) * 86400
    for coll in ("if_samples", "optics_samples"):
        try:
            await _ensure_ttl(coll, seconds)
        except Exception as e:
            logger.warning(f"TTL de {coll}: {e}")


# ---------- Séries agregadas (gráficos longos) ----------
def _pct(values: List[float], p: float) -> Optional[float]:
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _bucket_seconds(minutes: int, points: int) -> int:
    return max(30, int(minutes * 60 / max(50, points)))


def _stats(vals: List[float]) -> dict:
    v = [x for x in vals if x is not None]
    if not v:
        return {"avg": None, "max": None, "p95": None}
    return {"avg": sum(v) / len(v), "max": max(v), "p95": _pct(v, 0.95)}


@api.get("/monitor/series")
async def monitor_series(device_id: str, if_index: int, minutes: int = 60, points: int = 500,
                         user: dict = Depends(get_current_user)):
    await _check_view_device(user, device_id)
    s = await monitoring.get_settings(db)
    minutes = max(5, min(int(minutes), int(s.get("history_days") or 7) * 1440))
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = await db.if_samples.find({"device_id": device_id, "if_index": int(if_index), "ts": {"$gt": since}},
                                    {"_id": 0, "ts": 1, "in_bps": 1, "out_bps": 1}).sort("ts", 1).to_list(500000)
    step = _bucket_seconds(minutes, points)
    buckets: dict = {}
    for r in rows:
        ts = r["ts"] if isinstance(r["ts"], datetime) else datetime.fromisoformat(str(r["ts"]))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        k = int(ts.timestamp()) // step * step
        b = buckets.setdefault(k, {"in": [], "out": []})
        b["in"].append(r.get("in_bps")); b["out"].append(r.get("out_bps"))
    pts = []
    for k in sorted(buckets):
        b = buckets[k]
        vi = [x for x in b["in"] if x is not None]; vo = [x for x in b["out"] if x is not None]
        pts.append({"t": datetime.fromtimestamp(k, timezone.utc).isoformat(),
                    "in": sum(vi) / len(vi) if vi else None, "out": sum(vo) / len(vo) if vo else None,
                    "in_max": max(vi) if vi else None, "out_max": max(vo) if vo else None})
    lv = net_monitor.iface_live(device_id, if_index) or {}
    cache = await db.device_ifaces.find_one({"device_id": device_id}, {"_id": 0, "interfaces": 1})
    iface = next((i for i in (cache or {}).get("interfaces", []) if i.get("index") == int(if_index)), None)
    return {
        "points": pts, "step_sec": step,
        "stats": {"in": {**_stats([r.get("in_bps") for r in rows]), "cur": lv.get("in_bps")},
                  "out": {**_stats([r.get("out_bps") for r in rows]), "cur": lv.get("out_bps")}},
        "oper": lv.get("oper"), "stale": lv.get("stale"), "error": net_monitor.dev_errors.get(device_id),
        "speed_mbps": (iface or {}).get("speed_mbps"), "alias": (iface or {}).get("alias", ""),
    }


@api.post("/monitor/series-multi")
async def monitor_series_multi(payload: SeriesMultiPayload, user: dict = Depends(get_current_user)):
    """Soma o tráfego de várias interfaces (de equipamentos diferentes): agregado de trânsitos, CDNs, PNIs…"""
    mine = await _viewer_device_ids(user) if _is_viewer(user) else set(await _my_device_ids(user))
    srcs = [s for s in payload.sources if s.device_id in mine][:60]
    if not srcs:
        raise HTTPException(status_code=400, detail="Nenhuma interface válida no agregado")
    s = await monitoring.get_settings(db)
    minutes = max(5, min(int(payload.minutes), int(s.get("history_days") or 7) * 1440))
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    base = max(30, int(s.get("interval_sec") or 30))  # resolução da coleta
    per_src = []
    for src in srcs:
        rows = await db.if_samples.find({"device_id": src.device_id, "if_index": int(src.if_index), "ts": {"$gt": since}},
                                        {"_id": 0, "ts": 1, "in_bps": 1, "out_bps": 1}).to_list(500000)
        b: dict = {}
        for r in rows:
            ts = r["ts"] if isinstance(r["ts"], datetime) else datetime.fromisoformat(str(r["ts"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            k = int(ts.timestamp()) // base * base
            vi, vo = r.get("in_bps"), r.get("out_bps")
            if src.invert:
                vi, vo = vo, vi
            acc = b.setdefault(k, [0.0, 0.0, 0])
            if vi is not None and vo is not None:
                acc[0] += vi; acc[1] += vo; acc[2] += 1
        per_src.append({k: (v[0] / v[2], v[1] / v[2]) for k, v in b.items() if v[2]})
    keys = sorted({k for d in per_src for k in d})
    # soma na resolução da coleta; cada membro repete o último valor por até 3 leituras (coletas desalinhadas)
    last = [None] * len(per_src)
    fine = []
    for k in keys:
        tin = tout = 0.0
        present = 0
        for i, d in enumerate(per_src):
            if k in d:
                last[i] = (k, d[k])
            if last[i] and k - last[i][0] <= 3 * base:
                tin += last[i][1][0]; tout += last[i][1][1]; present += 1
        if present:
            fine.append((k, tin, tout, present == len(per_src)))
    complete = [f for f in fine if f[3]] or fine
    step = max(base, _bucket_seconds(minutes, payload.points))
    disp: dict = {}
    for k, vi, vo, full in fine:
        d = disp.setdefault(k // step * step, {"in": [], "out": [], "partial": False})
        d["in"].append(vi); d["out"].append(vo); d["partial"] |= not full
    pts = [{"t": datetime.fromtimestamp(k, timezone.utc).isoformat(),
            "in": sum(v["in"]) / len(v["in"]), "out": sum(v["out"]) / len(v["out"]),
            "in_max": max(v["in"]), "out_max": max(v["out"]), "partial": v["partial"]} for k, v in sorted(disp.items())]
    # membros: valor atual, velocidade e participação
    devs = {d["id"]: d["name"] for d in await db.devices.find({"id": {"$in": [x.device_id for x in srcs]}}, {"_id": 0, "id": 1, "name": 1}).to_list(100)}
    caches = {c["device_id"]: c for c in await db.device_ifaces.find({"device_id": {"$in": [x.device_id for x in srcs]}}, {"_id": 0}).to_list(100)}
    members, cur_in, cur_out, cap = [], 0.0, 0.0, 0
    any_cur = False
    for src in srcs:
        lv = net_monitor.iface_live(src.device_id, src.if_index) or {}
        vi, vo = lv.get("in_bps"), lv.get("out_bps")
        if src.invert:
            vi, vo = vo, vi
        if vi is not None:
            cur_in += vi; cur_out += vo or 0; any_cur = True
        iface = next((i for i in caches.get(src.device_id, {}).get("interfaces", []) if i.get("index") == int(src.if_index)), {})
        cap += iface.get("speed_mbps") or 0
        members.append({"device_id": src.device_id, "device_name": devs.get(src.device_id, "?"), "if_index": src.if_index,
                        "if_name": src.if_name or iface.get("name", ""), "alias": iface.get("alias", ""), "invert": src.invert,
                        "in_bps": vi, "out_bps": vo, "oper": lv.get("oper"), "speed_mbps": iface.get("speed_mbps"),
                        "error": net_monitor.dev_errors.get(src.device_id)})
    return {
        "points": pts, "step_sec": step, "members": members, "speed_mbps": cap or None,
        "stats": {"in": {**_stats([f[1] for f in complete]), "cur": cur_in if any_cur else None},
                  "out": {**_stats([f[2] for f in complete]), "cur": cur_out if any_cur else None}},
    }


# ---------- Óptica (RX/TX por lane via CLI) ----------
optics_collector = optics_mod.OpticsCollector(db, _connect_device)


@api.get("/optics/series")
async def optics_series(device_id: str, if_index: int, minutes: int = 1440, points: int = 300,
                        user: dict = Depends(get_current_user)):
    await _check_view_device(user, device_id)
    s = await monitoring.get_settings(db)
    minutes = max(30, min(int(minutes), int(s.get("history_days") or 7) * 1440))
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = await db.optics_samples.find({"device_id": device_id, "if_index": int(if_index), "ts": {"$gt": since}},
                                        {"_id": 0, "ts": 1, "lanes": 1}).sort("ts", 1).to_list(200000)
    step = max(300, _bucket_seconds(minutes, points))
    buckets: dict = {}
    nlanes = 0
    for r in rows:
        ts = r["ts"] if isinstance(r["ts"], datetime) else datetime.fromisoformat(str(r["ts"]))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        k = int(ts.timestamp()) // step * step
        lanes = r.get("lanes") or []
        nlanes = max(nlanes, len(lanes))
        b = buckets.setdefault(k, {})
        for i, ln in enumerate(lanes):
            b.setdefault(i, {"rx": [], "tx": []})
            if ln.get("rx") is not None:
                b[i]["rx"].append(ln["rx"])
            if ln.get("tx") is not None:
                b[i]["tx"].append(ln["tx"])
    avg = lambda v: round(sum(v) / len(v), 2) if v else None  # noqa: E731
    pts = [{"t": datetime.fromtimestamp(k, timezone.utc).isoformat(),
            "rx": [avg(buckets[k].get(i, {}).get("rx", [])) for i in range(nlanes)],
            "tx": [avg(buckets[k].get(i, {}).get("tx", [])) for i in range(nlanes)]} for k in sorted(buckets)]
    live = optics_collector.get_live(device_id, if_index)
    rx_all = [x for r in rows for ln in (r.get("lanes") or []) for x in [ln.get("rx")] if x is not None and x > optics_mod.NO_LIGHT]
    return {"points": pts, "lanes": max(nlanes, len(live.get("lanes") or [])), "live": live,
            "stats": {"rx_min": min(rx_all) if rx_all else None, "rx_max": max(rx_all) if rx_all else None}}


@api.post("/devices/{device_id}/optics-test")
async def optics_test(device_id: str, payload: OpticsTestPayload, user: dict = Depends(get_current_user)):
    dev = await _get_device_for(user, device_id)
    s = await optics_mod.get_settings(db)
    try:
        r = await asyncio.wait_for(optics_collector.read_one(dev, payload.if_index, payload.if_name, s), timeout=120)
        return {"ok": r["parsed"]["ok"], "lanes": r["parsed"]["lanes"], "command": r["command"], "raw": (r["raw"] or "")[-6000:],
                "commands_tried": optics_mod.commands_for(dev.get("device_type"), s)}
    except Exception as e:
        return {"ok": False, "error": str(e), "lanes": [], "raw": "", "command": ""}


@api.get("/optics/settings")
async def get_optics_settings(user: dict = Depends(get_current_user)):
    s = await optics_mod.get_settings(db)
    cmds = {t: optics_mod.commands_for(t, s) for t in DEVICE_TYPES}
    return {"optics_enabled": s["optics_enabled"], "optics_interval_sec": s["optics_interval_sec"], "commands": cmds,
            "defaults": optics_mod.DEFAULT_COMMANDS, "last_tick": optics_collector.last_tick.isoformat() if optics_collector.last_tick else None,
            "busy": optics_collector.busy, "errors": len(optics_collector.errors), "is_admin": user.get("role") == "admin"}


@api.put("/optics/settings")
async def put_optics_settings(payload: OpticsSettings, _: dict = Depends(require_admin)):
    data = payload.model_dump()
    data["optics_interval_sec"] = max(60, min(int(data["optics_interval_sec"]), 86400))
    clean = {}
    for t, cmds in (data.get("optics_commands") or {}).items():
        if t not in DEVICE_TYPES:
            continue
        lst = [c.strip() for c in cmds if c and c.strip()]
        if lst != optics_mod.DEFAULT_COMMANDS.get(t, []):
            clean[t] = lst
    data["optics_commands"] = clean
    await db.config.update_one({"key": "optics"}, {"$set": data}, upsert=True)
    return {"ok": True}


@api.post("/optics/poll-now")
async def optics_poll_now(_: dict = Depends(get_current_user)):
    if optics_collector.busy:
        return {"started": False, "reason": "Leitura óptica já em andamento"}
    asyncio.create_task(optics_collector.tick())
    return {"started": True}


# ---------- Dashboards ----------
async def _dash_for(user: dict, dash_id: str) -> dict:
    if _is_viewer(user):
        d = await db.dashboards.find_one({"id": dash_id}, {"_id": 0}) if dash_id in (user.get("view_dashboards") or []) else None
    else:
        d = await db.dashboards.find_one({"id": dash_id, "owner_id": user["id"]}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Dashboard não encontrado")
    return d


@api.get("/dashboards")
async def list_dashboards(user: dict = Depends(get_current_user)):
    q = {"id": {"$in": user.get("view_dashboards") or []}} if _is_viewer(user) else {"owner_id": user["id"]}
    rows = await db.dashboards.find(q, {"_id": 0}).to_list(1000)
    rows.sort(key=lambda d: ((d.get("group") or "Geral").lower(), d["name"].lower()))
    return [{"id": d["id"], "name": d["name"], "group": d.get("group") or "Geral", "widgets": len(d.get("widgets", [])),
             "updated_at": d.get("updated_at")} for d in rows]


@api.post("/dashboards")
async def create_dashboard(payload: DashboardCreate, user: dict = Depends(get_current_user)):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Nome obrigatório")
    now = datetime.now(timezone.utc).isoformat()
    doc = {"id": os.urandom(8).hex(), "owner_id": user["id"], "name": payload.name.strip(),
           "group": payload.group.strip() or "Geral", "widgets": [], "created_at": now, "updated_at": now}
    await db.dashboards.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


@api.get("/dashboards/{dash_id}")
async def get_dashboard(dash_id: str, user: dict = Depends(get_current_user)):
    return await _dash_for(user, dash_id)


@api.put("/dashboards/{dash_id}")
async def update_dashboard(dash_id: str, payload: DashboardUpdate, user: dict = Depends(get_current_user)):
    await _dash_for(user, dash_id)
    mine = set(await _my_device_ids(user))
    widgets = []
    for w in payload.widgets:
        wd = w.model_dump()
        if wd["type"] not in ("traffic", "optics", "aggregate"):
            raise HTTPException(status_code=400, detail="Tipo de widget inválido")
        if wd["type"] == "aggregate":
            if not wd["sources"] or len(wd["sources"]) > 60:
                raise HTTPException(status_code=400, detail="O agregado precisa de 1 a 60 interfaces")
            if any(src["device_id"] not in mine for src in wd["sources"]):
                raise HTTPException(status_code=400, detail="Equipamento inválido no agregado")
            wd["device_id"], wd["if_index"] = None, None
        else:
            if wd["device_id"] not in mine or wd["if_index"] is None:
                raise HTTPException(status_code=400, detail="Equipamento inválido no dashboard")
            wd["sources"] = []
        wd["size"] = "half" if wd["size"] == "half" else "full"
        widgets.append(wd)
    upd = {"name": payload.name.strip() or "Dashboard", "group": payload.group.strip() or "Geral",
           "widgets": widgets, "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.dashboards.update_one({"id": dash_id}, {"$set": upd})
    return await _dash_for(user, dash_id)


@api.delete("/dashboards/{dash_id}")
async def delete_dashboard(dash_id: str, user: dict = Depends(get_current_user)):
    r = await db.dashboards.delete_one({"id": dash_id, "owner_id": user["id"]})
    return {"deleted": r.deleted_count}


@api.post("/dashboards/{dash_id}/duplicate")
async def duplicate_dashboard(dash_id: str, user: dict = Depends(get_current_user)):
    d = await _dash_for(user, dash_id)
    now = datetime.now(timezone.utc).isoformat()
    d.update({"id": os.urandom(8).hex(), "name": f"{d['name']} (cópia)", "created_at": now, "updated_at": now})
    await db.dashboards.insert_one(dict(d))
    d.pop("_id", None)
    return d



# ---------- Assistente IA (chat no sistema + Telegram) ----------
@api.get("/ai/settings")
async def get_ai_settings(_: dict = Depends(require_admin)):
    s = await ai_assistant.get_ai_settings(db)
    out = ai_assistant.public_ai_settings(s)
    auto = await automation.get_settings(db)
    out["has_telegram_token"] = bool(auto.get("telegram_bot_token"))
    out["bot_status"] = assistant.status
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    out["usage"] = await db.ai_usage.find_one({"month": month}, {"_id": 0}) or {"month": month}
    return out


@api.put("/ai/settings")
async def put_ai_settings(payload: AISettings, _: dict = Depends(require_admin)):
    existing = await ai_assistant.get_ai_settings(db)
    data = payload.model_dump()
    prov = data.pop("ai_provider")
    if prov not in llm.PROVIDERS:
        raise HTTPException(status_code=400, detail="Provedor inválido")
    key = (data.pop("api_key", None) or data.pop("anthropic_api_key", None) or "").strip()
    data.pop("anthropic_api_key", None)
    clear = data.pop("clear_api_key", False)
    keys = dict(existing.get("ai_keys") or {})
    if clear:
        keys.pop(prov, None)
    elif key:
        keys[prov] = vault.encrypt(key)
    models = dict(existing.get("ai_models") or {})
    model = (data.pop("ai_model") or "").strip()
    if model:
        models[prov] = model[:120]
    urls = dict(existing.get("ai_base_urls") or {})
    base = (data.pop("ai_base_url") or "").strip().rstrip("/")
    if base and not re.match(r"^https?://", base):
        raise HTTPException(status_code=400, detail="URL da API precisa começar com http:// ou https://")
    if prov in ("ollama", "openai"):
        urls[prov] = base
    valid_users = {u["id"] for u in await db.users.find({}, {"_id": 0, "id": 1}).to_list(1000)}
    seen, users = set(), []
    for u in data.get("ai_users") or []:
        tid = re.sub(r"\D", "", str(u.get("telegram_id", "")))
        if not tid or tid in seen:
            continue
        if u.get("user_id") not in valid_users:
            raise HTTPException(status_code=400, detail=f"Usuário do BastiON inválido para o Telegram ID {tid}")
        seen.add(tid)
        users.append({"telegram_id": tid, "user_id": u["user_id"], "label": (u.get("label") or "").strip()})
    data["ai_users"] = users
    data.update({"ai_provider": prov, "ai_keys": keys, "ai_models": models, "ai_base_urls": urls,
                 "anthropic_api_key": keys.get("anthropic", "")})
    if prov == "anthropic" and models.get("anthropic"):
        data["ai_model"] = models["anthropic"]
    await db.config.update_one({"key": "ai"}, {"$set": data}, upsert=True)
    return await get_ai_settings(_)


class AITestIn(BaseModel):
    provider: str = ""
    model: str = ""
    base_url: str = ""
    key: str = ""


@api.post("/ai/test")
async def test_ai(body: Optional[AITestIn] = None, _: dict = Depends(require_admin)):
    """Testa com o que está no formulário (ainda não salvo); o que faltar vem do salvo."""
    s = await ai_assistant.get_ai_settings(db)
    b = body or AITestIn()
    if b.provider and b.provider not in llm.PROVIDERS:
        return {"ok": False, "error": "Provedor desconhecido"}
    cfg = ai_assistant.llm_cfg(s, b.provider or None)
    cfg["fallbacks"] = []          # o teste é do modelo escolhido, sem reserva
    if b.model.strip():
        cfg["model"] = b.model.strip()
    if b.base_url.strip():
        if not re.match(r"^https?://", b.base_url.strip()):
            return {"ok": False, "error": "URL da API precisa começar com http:// ou https://"}
        cfg["base_url"] = b.base_url.strip().rstrip("/")
    if b.key.strip():
        cfg["key"] = b.key.strip()
    if llm.PROVIDERS.get(cfg["provider"], {}).get("key") and not cfg["key"]:
        return {"ok": False, "error": "Falta a chave da API do provedor escolhido"}
    if not cfg["model"]:
        return {"ok": False, "error": "Falta escolher o modelo"}
    try:
        r = await llm.call(cfg, [{"role": "user", "content": "Responda apenas: OK"}],
                           [{"type": "text", "text": "Teste de conexão."}], max_tokens=10)
        text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()
        return {"ok": True, "model": r.get("model") or cfg["model"], "reply": text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@api.get("/ai/models")
async def ai_models(provider: str = "", key: str = "", base_url: str = "", _: dict = Depends(require_admin)):
    """Modelos disponíveis no provedor (usa a chave digitada ou a salva)."""
    s = await ai_assistant.get_ai_settings(db)
    cfg = ai_assistant.llm_cfg(s, provider or None)
    if key:
        cfg["key"] = key
    if base_url:
        cfg["base_url"] = base_url
    try:
        return {"models": await llm.list_models(cfg)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui listar os modelos: {e}")


# ---------- Chat do assistente dentro do sistema ----------
class ChatIn(BaseModel):
    text: str
    voice: bool = False      # conversa por voz: resposta curta, para ser lida em voz alta


def _chat_user(user: dict):
    if _is_viewer(user):
        raise HTTPException(403, "Sem permissão")


@api.get("/assistant/status")
async def assistant_status(user: dict = Depends(get_current_user)):
    if _is_viewer(user):
        return {"enabled": False, "reason": "perfil View"}
    s = await ai_assistant.get_ai_settings(db)
    why = None if s.get("ai_web_enabled", True) else "chat desativado pelo administrador"
    why = why or ai_assistant.ai_ready(s)
    cfg = ai_assistant.llm_cfg(s)
    return {"enabled": why is None, "reason": why, "provider": llm.PROVIDERS.get(cfg["provider"], {}).get("label", cfg["provider"]).split(" (")[0],
            "model": cfg["model"], "allow_changes": bool(s.get("ai_allow_changes")), "is_admin": user.get("role") == "admin"}


@api.get("/assistant/conversation")
async def assistant_conversation(user: dict = Depends(get_current_user)):
    _chat_user(user)
    return await web_assistant.view(user["id"])


@api.post("/assistant/messages")
async def assistant_message(body: ChatIn, user: dict = Depends(get_current_user)):
    _chat_user(user)
    try:
        return await web_assistant.post(user, body.text, voice=body.voice)
    except ai_assistant.AIError as e:
        raise HTTPException(409 if "trabalhando" in str(e) else 400, str(e))


@api.post("/assistant/proposals/{pid}/{action}")
async def assistant_decide(pid: str, action: str, user: dict = Depends(get_current_user)):
    _chat_user(user)
    try:
        return await web_assistant.decide(user, pid, action)
    except ai_assistant.AIError as e:
        raise HTTPException(400, str(e))


@api.post("/assistant/reset")
async def assistant_reset(user: dict = Depends(get_current_user)):
    _chat_user(user)
    try:
        await web_assistant.reset(user["id"])
    except ai_assistant.AIError as e:
        raise HTTPException(409, str(e))
    return await web_assistant.view(user["id"])


@api.get("/ai/audit")
async def ai_audit(_: dict = Depends(require_admin), limit: int = 50):
    return await db.ai_audit.find({}, {"_id": 0}).sort("at", -1).to_list(max(1, min(limit, 500)))


@api.get("/automation/settings")
async def get_automation_settings(_: dict = Depends(require_admin)):
    s = await automation.get_settings(db)
    out = automation.public_settings(s)
    out["last_ping"] = scheduler.last_ping.isoformat() if scheduler.last_ping else None
    out["ping_running"] = scheduler.busy
    return out


@api.put("/automation/settings")
async def put_automation_settings(payload: AutomationSettings, _: dict = Depends(require_admin)):
    existing = await automation.get_settings(db)
    data = payload.model_dump()
    clear = data.pop("clear_telegram_token", False)
    tok = data.pop("telegram_bot_token", None)
    data["telegram_bot_token"] = "" if clear else (vault.encrypt(tok) if tok else existing.get("telegram_bot_token", ""))
    data["ping_interval_min"] = max(1, int(data["ping_interval_min"]))
    data["backup_hour"] = min(23, max(0, int(data["backup_hour"])))
    data["daily_report_hour"] = min(23, max(0, int(data["daily_report_hour"])))
    await db.config.update_one({"key": "automation"}, {"$set": data}, upsert=True)
    return automation.public_settings(await automation.get_settings(db))


@api.get("/automation/daily-report")
async def preview_daily_report(_: dict = Depends(require_admin)):
    title, text, data = await dailyreport.build(db, _optics_names)
    return {"title": title, "text": text, "data": data}


@api.post("/automation/daily-report/send")
async def send_daily_report(_: dict = Depends(require_admin)):
    return await _send_daily_report()


@api.post("/automation/test-alert")
async def test_alert(_: dict = Depends(require_admin)):
    res = await automation.send_alert(db, "✅ SSH BastiON Central", "Alerta de teste — canal de notificações funcionando.")
    return res


@api.post("/automation/ping-now")
async def ping_now(_: dict = Depends(require_admin)):
    if scheduler.busy:
        return {"started": False, "reason": "Ping já em andamento"}
    asyncio.create_task(scheduler.tick(force_ping=True))
    return {"started": True}


@api.get("/alerts")
async def list_alerts(_: dict = Depends(require_admin), limit: int = 50):
    return await db.alerts.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)


@api.post("/backups/run")
async def run_backups(payload: BackupRunPayload, user: dict = Depends(get_current_user)):
    mine = {d["id"] for d in await db.devices.find(_scope(user), {"_id": 0, "id": 1}).to_list(5000)}
    payload.device_ids = [i for i in (payload.device_ids or list(mine)) if i in mine]
    if not payload.device_ids:
        return {"results": []}
    results = await _backup_many(payload.device_ids)
    if any(r.get("changed") and r.get("prev_id") for r in results):
        _bg(automation.notify_config_changes(db, results, f"backup manual por {user.get('email')}"))
    _bg(cloudsync.run(db, automation.send_alert, "backup manual"))
    return {"results": results}


# ---------- Backups na nuvem (rclone: Google Drive, OneDrive, Dropbox, S3…) ----------
class CloudSettingsIn(BaseModel):
    enabled: bool = False
    remote: str = ""
    folder: str = "BastiON"
    tags: List[str] = []
    only_changed: bool = True


@api.get("/cloud/status")
async def cloud_status(_: dict = Depends(require_admin)):
    return await cloudsync.status(db)


@api.put("/cloud/settings")
async def cloud_put_settings(body: CloudSettingsIn, _: dict = Depends(require_admin)):
    data = body.model_dump()
    data["remote"] = data["remote"].strip().rstrip(":")
    data["folder"] = data["folder"].strip().strip("/")[:200]
    data["tags"] = sorted({t.strip() for t in data["tags"] if t.strip()})
    if data["enabled"] and (not data["remote"] or not data["tags"]):
        raise HTTPException(400, "Para ligar, escolha o drive e pelo menos uma tag")
    await db.config.update_one({"key": "cloud"}, {"$set": data}, upsert=True)
    return await cloudsync.status(db)


@api.post("/cloud/test")
async def cloud_test(body: CloudSettingsIn, _: dict = Depends(require_admin)):
    return await cloudsync.test(body.remote, body.folder)


@api.post("/cloud/run")
async def cloud_run(_: dict = Depends(require_admin)):
    return await cloudsync.run(db, None, "manual", force=True)


_BG_TASKS: set = set()


def _bg(coro):
    """Dispara em segundo plano sem perder a referência (o asyncio só guarda referência fraca)."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


# ---------- Busca em todas as configs ----------
@api.get("/configs/search")
async def configs_search(q: str = "", mode: str = "word", case: bool = False, context: int = 1,
                         device_type: str = "", device: str = "", user: dict = Depends(get_current_user)):
    """Procura em último backup OK de cada equipamento do usuário. mode: text | word | regex."""
    if mode not in ("text", "word", "regex"):
        mode = "word"
    dq = dict(_scope(user))
    if device_type:
        dq["device_type"] = device_type
    devs = await db.devices.find(dq, {"_id": 0, "id": 1, "name": 1, "device_type": 1, "host": 1}).to_list(10000)
    if device.strip():
        w = device.strip().lower()
        devs = [d for d in devs if w in (d.get("name") or "").lower() or w in (d.get("host") or "").lower()]
    try:
        return await configsearch.search(db, devs, q, mode, case, context)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@api.get("/backups")
async def list_backups(user: dict = Depends(get_current_user), device_id: Optional[str] = None, limit: int = 200):
    mine = [d["id"] for d in await db.devices.find(_scope(user), {"_id": 0, "id": 1}).to_list(5000)]
    q = {"device_id": device_id if device_id in mine else {"$in": mine}}
    return await db.backups.find(q, {"_id": 0, "content": 0}).sort("created_at", -1).to_list(limit)


@api.get("/backups/summary")
async def backups_summary(user: dict = Depends(get_current_user)):
    mine = [d["id"] for d in await db.devices.find(_scope(user), {"_id": 0, "id": 1}).to_list(5000)]
    match = {"device_id": {"$in": mine}}
    pipeline = [
        {"$match": match},
        {"$sort": {"created_at": -1}},
        {"$group": {"_id": "$device_id", "device_name": {"$first": "$device_name"}, "device_type": {"$first": "$device_type"},
                    "last_at": {"$first": "$created_at"}, "last_ok": {"$first": "$ok"}, "last_error": {"$first": "$error"},
                    "count": {"$sum": 1}, "ok_count": {"$sum": {"$cond": ["$ok", 1, 0]}}}},
        {"$sort": {"device_name": 1}},
    ]
    rows = await db.backups.aggregate(pipeline).to_list(5000)
    return [{"device_id": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in rows]


async def _my_device_ids(user: dict) -> List[str]:
    return [d["id"] for d in await db.devices.find(_scope(user), {"_id": 0, "id": 1}).to_list(10000)]


async def _backup_for(user: dict, backup_id: str) -> dict:
    """Backup só é visível para o dono do equipamento."""
    b = await db.backups.find_one({"id": backup_id, "device_id": {"$in": await _my_device_ids(user)}}, {"_id": 0})
    if not b:
        raise HTTPException(status_code=404, detail="Backup não encontrado")
    return b


def _remove_backup_files(docs: List[dict]):
    for d in docs:
        p = d.get("file_path")
        if p:
            try:
                os.remove(p)
            except OSError:
                pass


async def _orphan_device_ids() -> List[str]:
    existing = set(await db.devices.distinct("id"))
    return [i for i in await db.backups.distinct("device_id") if i not in existing]


@api.get("/backups/storage")
async def backups_storage(user: dict = Depends(get_current_user)):
    async def _stats(match: dict) -> dict:
        rows = await db.backups.aggregate([
            {"$match": match},
            {"$group": {"_id": None, "count": {"$sum": 1}, "size": {"$sum": {"$ifNull": ["$size", 0]}},
                        "failed": {"$sum": {"$cond": ["$ok", 0, 1]}},
                        "unchanged": {"$sum": {"$cond": [{"$and": ["$ok", {"$eq": ["$changed", False]}]}, 1, 0]}},
                        "oldest": {"$min": "$created_at"}, "devices": {"$addToSet": "$device_id"}}},
        ]).to_list(1)
        r = rows[0] if rows else {}
        return {"count": r.get("count", 0), "size_bytes": r.get("size", 0), "failed": r.get("failed", 0),
                "unchanged": r.get("unchanged", 0), "oldest": r.get("oldest"), "devices": len(r.get("devices", []))}
    out = await _stats({"device_id": {"$in": await _my_device_ids(user)}})
    if user.get("role") == "admin":
        orphans = await _orphan_device_ids()
        o = await _stats({"device_id": {"$in": orphans}}) if orphans else {"count": 0, "size_bytes": 0}
        out["orphans"] = {"count": o["count"], "size_bytes": o["size_bytes"], "devices": len(orphans)}
    return out


@api.get("/backups/all")
async def backups_all(user: dict = Depends(get_current_user), q: str = "", status: str = "all",
                      older_than_days: Optional[int] = None, device_id: Optional[str] = None,
                      skip: int = 0, limit: int = 50):
    mine = await _my_device_ids(user)
    match: dict = {"device_id": device_id if device_id in mine else {"$in": mine}}
    if q.strip():
        match["device_name"] = {"$regex": re.escape(q.strip()), "$options": "i"}
    if status == "ok":
        match["ok"] = True
    elif status == "failed":
        match["ok"] = False
    elif status == "unchanged":
        match["ok"], match["changed"] = True, False
    if older_than_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(older_than_days))).isoformat()
        match["created_at"] = {"$lt": cutoff}
    total = await db.backups.count_documents(match)
    items = await db.backups.find(match, {"_id": 0, "content": 0}).sort("created_at", -1) \
        .skip(max(0, skip)).to_list(max(1, min(limit, 500)))
    return {"total": total, "items": items}


@api.post("/backups/delete")
async def delete_backups(payload: BackupIdsPayload, user: dict = Depends(get_current_user)):
    match = {"id": {"$in": payload.ids}, "device_id": {"$in": await _my_device_ids(user)}}
    docs = await db.backups.find(match, {"_id": 0, "id": 1, "file_path": 1, "size": 1}).to_list(len(payload.ids) + 1)
    r = await db.backups.delete_many(match)
    _remove_backup_files(docs)
    return {"deleted": r.deleted_count, "freed_bytes": sum(d.get("size") or 0 for d in docs)}


@api.post("/backups/cleanup")
async def cleanup_backups(payload: BackupCleanupPayload, user: dict = Depends(get_current_user)):
    """Limpeza por regras. Os `keep_last` backups OK mais recentes de cada equipamento nunca são apagados."""
    mine = await _my_device_ids(user)
    dev_ids = [i for i in (payload.device_ids or mine) if i in mine]
    keep_last = max(1, int(payload.keep_last or 1))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(payload.older_than_days))).isoformat() \
        if payload.older_than_days else None
    if not (cutoff or payload.delete_unchanged or payload.delete_failed or payload.include_orphans):
        raise HTTPException(status_code=400, detail="Escolha ao menos uma regra de limpeza")
    proj = {"_id": 0, "id": 1, "device_id": 1, "created_at": 1, "ok": 1, "changed": 1, "size": 1, "file_path": 1}
    docs = await db.backups.find({"device_id": {"$in": dev_ids}}, proj).sort("created_at", -1).to_list(500000)
    kept: dict = {}
    victims = []
    for d in docs:  # mais novo primeiro
        if d.get("ok") and kept.get(d["device_id"], 0) < keep_last:
            kept[d["device_id"]] = kept.get(d["device_id"], 0) + 1
            continue
        if ((cutoff and d["created_at"] < cutoff)
                or (payload.delete_unchanged and d.get("ok") and d.get("changed") is False)
                or (payload.delete_failed and not d.get("ok"))):
            victims.append(d)
    orphan_docs = []
    if payload.include_orphans:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Somente administradores limpam backups de equipamentos excluídos")
        orphans = await _orphan_device_ids()
        if orphans:
            orphan_docs = await db.backups.find({"device_id": {"$in": orphans}}, proj).to_list(500000)
    all_victims = victims + orphan_docs
    result = {"count": len(all_victims), "size_bytes": sum(d.get("size") or 0 for d in all_victims),
              "devices": len({d["device_id"] for d in victims}), "orphans": len(orphan_docs), "dry_run": payload.dry_run}
    if not payload.dry_run and all_victims:
        ids = [d["id"] for d in all_victims]
        deleted = 0
        for i in range(0, len(ids), 1000):
            deleted += (await db.backups.delete_many({"id": {"$in": ids[i:i + 1000]}})).deleted_count
        _remove_backup_files(all_victims)
        result["deleted"] = deleted
        logger.info("Limpeza de backups por %s: %d apagados (%d bytes)", user.get("email"), deleted, result["size_bytes"])
    return result


@api.get("/backups/{backup_id}")
async def get_backup(backup_id: str, user: dict = Depends(get_current_user)):
    return await _backup_for(user, backup_id)


@api.get("/backups/{backup_id}/diff/{other_id}")
async def diff_backups(backup_id: str, other_id: str, user: dict = Depends(get_current_user)):
    a = await _backup_for(user, other_id)
    b = await _backup_for(user, backup_id)
    diff = automation.unified_diff(a.get("content", ""), b.get("content", ""),
                                   f"{a['device_name']} @ {a['created_at']}", f"{b['device_name']} @ {b['created_at']}")
    return {"diff": diff, "identical": a.get("sha256") == b.get("sha256")}


@api.delete("/backups/{backup_id}")
async def delete_backup(backup_id: str, user: dict = Depends(get_current_user)):
    b = await _backup_for(user, backup_id)
    r = await db.backups.delete_one({"id": backup_id})
    _remove_backup_files([b])
    return {"deleted": r.deleted_count}


# ---------- Acesso Web (página http/https do equipamento pelo BastiON) ----------
async def _web_resolve(agent_id: str):
    priv, _, _ = await _get_ssh_key()
    chain = await _agent_chain(agent_id)
    return [_agent_hop(a, priv) for a in chain], " → ".join(a["name"] for a in chain)


async def _web_connect(hops):
    w = SSHClientWrapper(hops)
    await w.connect()
    return w


web_proxy = webproxy.WebProxy(db, _web_resolve, _web_connect)


class WebOpenIn(BaseModel):
    url: str
    device_id: Optional[str] = None
    agent_id: Optional[str] = None      # sem equipamento: agente escolhido; vazio = direto do servidor (admin)


@api.get("/web/info")
async def web_info(_: dict = Depends(get_current_user)):
    # com BASTION_HTTPS_HOST o Caddy atende cada porta também em HTTPS (porta + WEB_PROXY_TLS_OFFSET)
    return {"ports": web_proxy.live_ports, "enabled": bool(web_proxy.live_ports),
            "idle_minutes": webproxy.IDLE_SECONDS // 60,
            "tls_offset": int(os.environ.get("WEB_PROXY_TLS_OFFSET", "400")) if os.environ.get("BASTION_HTTPS_HOST") else 0}


@api.get("/web/sessions")
async def web_sessions(user: dict = Depends(get_current_user)):
    return web_proxy.list(user)


@api.get("/web/sessions/all")
async def web_sessions_all(_: dict = Depends(require_admin)):
    return web_proxy.list(None)


@api.post("/web/sessions")
async def web_open(payload: WebOpenIn, user: dict = Depends(get_current_user)):
    label, agent_id, device_id = "", payload.agent_id or None, None
    if payload.device_id:
        dev = await _get_device_for(user, payload.device_id)
        device_id, label, agent_id = dev["id"], dev["name"], dev.get("agent_id") or None
    elif agent_id:
        await _get_agent_for(user, agent_id)
    elif user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Abrir direto do servidor é só para administradores — escolha um agente")
    try:
        webproxy.parse_target(payload.url)
        s = await web_proxy.open(user, payload.url, agent_id=agent_id, device_id=device_id, label=label)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except VpnDown as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e) or type(e).__name__)
    if device_id:
        await db.devices.update_one({"id": device_id}, {"$set": {"web_url": payload.url.strip()}})
    return s


@api.delete("/web/sessions/{sid}")
async def web_close(sid: str, user: dict = Depends(get_current_user)):
    s = web_proxy.sessions.get(sid)
    if not s or (s.user_id != user["id"] and user.get("role") != "admin"):
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    await web_proxy.close(sid, reason="fechada pelo usuário")
    return {"ok": True}


# ---------- WebSocket Terminal ----------
@app.websocket("/api/ws/terminal/{device_id}")
async def ws_terminal(ws: WebSocket, device_id: str, token: str = Query(...)):
    await ws.accept()
    try:
        payload = decode_token(token)
    except HTTPException:
        await ws.send_json({"type": "error", "message": "Token inválido"})
        await ws.close()
        return
    user_id = payload["sub"]
    user_email = payload["email"]
    u = await db.users.find_one({"id": user_id}, {"_id": 0, "role": 1, "token_version": 1, "modules": 1})
    if u and int(payload.get("tv", 0)) != int(u.get("token_version", 0)):
        await ws.send_json({"type": "error", "message": "Sessão encerrada — entre novamente"})
        await ws.close()
        return
    if not u or u.get("role") == "viewer" or not auth_mod.has_module(u, "terminal"):
        await ws.send_json({"type": "error", "message": "Seu perfil não tem acesso ao terminal"})
        await ws.close()
        return

    dev = await db.devices.find_one({"id": device_id, "owner_id": user_id}, {"_id": 0})
    if not dev:
        await ws.send_json({"type": "error", "message": "Equipamento não encontrado"})
        await ws.close()
        return

    session_id = os.urandom(8).hex()
    started = datetime.now(timezone.utc)
    await db.sessions.insert_one({
        "id": session_id, "user_id": user_id, "user_email": user_email,
        "device_id": dev["id"], "device_name": dev["name"],
        "started_at": started.isoformat(), "ended_at": None,
        "duration_seconds": None, "kind": "terminal",
    })

    wrapper: Optional[SSHClientWrapper] = None
    try:
        await ws.send_json({"type": "status", "message": f"Conectando em {dev['name']} ({dev['host']}:{dev.get('port',22)}) via {dev.get('protocol','ssh').upper()}..."})
        wrapper = await _connect_device(dev)
        await ws.send_json({"type": "status", "message": "Conectado. Sessão ativa."})
        process = await wrapper.open_shell(cols=120, rows=32)

        async def pump_stdout():
            try:
                while True:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode("utf-8", "replace")
                    await ws.send_json({"type": "data", "data": chunk})
            except Exception as e:
                logger.warning(f"stdout pump ended: {e}")

        async def pump_stderr():
            try:
                while True:
                    chunk = await process.stderr.read(4096)
                    if not chunk:
                        break
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode("utf-8", "replace")
                    await ws.send_json({"type": "data", "data": chunk})
            except Exception:
                pass

        out_task = asyncio.create_task(pump_stdout())
        err_task = asyncio.create_task(pump_stderr())

        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "input":
                data = msg.get("data", "")
                process.stdin.write(data.encode("utf-8"))
            elif msg.get("type") == "resize":
                await wrapper.resize(int(msg.get("cols", 120)), int(msg.get("rows", 32)))
            elif msg.get("type") == "close":
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": f"Erro: {e}"})
        except Exception:
            pass
    finally:
        if wrapper:
            await wrapper.close()
        ended = datetime.now(timezone.utc)
        await db.sessions.update_one({"id": session_id}, {"$set": {
            "ended_at": ended.isoformat(),
            "duration_seconds": int((ended - started).total_seconds()),
        }})
        try:
            await ws.close()
        except Exception:
            pass


# ---------- Análise de rede (OSPF / BGP / interfaces / cenários) ----------
class AnalysisIn(BaseModel):
    ref_bw_mbps: Optional[int] = None       # None = descobre a referência usada na rede
    util_warn: int = 70
    util_crit: int = 90
    sample_sec: int = 10
    mpls: bool = True                       # LDP / VPWS / VPLS / L3VPN pela CLI (SSH)


class MplsSettingsIn(BaseModel):
    mpls_commands: dict = {}                # {tipo: {seção: [comandos]}}


async def _mpls_settings() -> dict:
    return await db.config.find_one({"key": "mpls"}, {"_id": 0}) or {}


async def _mpls_collect(dev: dict, settings: dict) -> dict:
    """Uma sessão SSH por equipamento, todas as seções MPLS em sequência."""
    dtype = dev.get("device_type") or "other"
    if not any(mplscli.commands_for(dtype, settings).values()):
        return {"skipped": True, "device_type": dtype}
    try:
        client = await _connect_device(dev)
    except Exception as e:
        return {"error": f"SSH: {e}", "device_type": dtype}
    try:
        sess = await client.shell()
        try:
            return {"device_type": dtype, "sections": await mplscli.collect(sess, dtype, settings)}
        finally:
            await sess.close()
    except Exception as e:
        return {"error": str(e), "device_type": dtype}
    finally:
        try:
            await client.close()
        except Exception:
            pass


class SimulateIn(BaseModel):
    down_links: List[str] = []
    down_nodes: List[str] = []
    costs: List[dict] = []                  # [{link_id, dir: "ab"|"ba", cost}]


async def _report_for(user: dict, rid: str) -> dict:
    d = await db.net_reports.find_one({"id": rid, "owner_id": user["id"]}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Relatório não encontrado")
    return d


@api.post("/maps/{map_id}/analysis")
async def run_analysis(map_id: str, body: AnalysisIn, user: dict = Depends(get_current_user)):
    m = await _map_for(user, map_id)
    dev_ids = list({n["device_id"] for n in m.get("nodes", []) if n.get("device_id")})
    if not dev_ids:
        raise HTTPException(status_code=400, detail="O mapa não tem equipamentos")
    devs = {d["id"]: d async for d in db.devices.find({"id": {"$in": dev_ids}, "owner_id": user["id"]}, {"_id": 0})}
    settings = await monitoring.get_settings(db)
    sample = max(3, min(int(body.sample_sec), 30))
    sem = asyncio.Semaphore(8)
    started = time.monotonic()

    async def one(did: str):
        dev = devs.get(did)
        if not dev:
            return did, {"ok": False, "error": "equipamento não encontrado"}
        async with sem:
            try:
                client = await _snmp_client(dev, settings)
                return did, await asyncio.wait_for(netanalysis.collect_device(client, sample), timeout=150)
            except asyncio.TimeoutError:
                return did, {"ok": False, "error": "SNMP demorou demais"}
            except Exception as e:
                return did, {"ok": False, "error": str(e)}

    mpls_settings = await _mpls_settings()
    ssh_sem = asyncio.Semaphore(6)

    async def one_mpls(did: str):
        dev = devs.get(did)
        if not dev:
            return did, {"error": "equipamento não encontrado"}
        async with ssh_sem:
            try:
                return did, await asyncio.wait_for(_mpls_collect(dev, mpls_settings), timeout=240)
            except asyncio.TimeoutError:
                return did, {"error": "CLI demorou demais"}

    snmp_task = asyncio.gather(*[one(d) for d in dev_ids])
    mpls_task = asyncio.gather(*[one_mpls(d) for d in dev_ids]) if body.mpls else None
    data = dict(await snmp_task)
    mpls_raw = dict(await mpls_task) if mpls_task else {}
    mpls_data = {d: v for d, v in mpls_raw.items() if not v.get("skipped")}
    live = (await map_live(map_id, user=user)).get("links", {})
    optics = {}
    node_dev = {n["id"]: n.get("device_id") for n in m.get("nodes", [])}
    for ln in m.get("links", []):
        for side, key in (("from_if", "from"), ("to_if", "to")):
            did, iface = node_dev.get(ln[key]), ln.get(side)
            if did and iface:
                optics[(did, iface["index"])] = optics_collector.get_live(did, iface["index"])
    opts = {k: v for k, v in body.model_dump().items() if v is not None and k != "mpls"}
    rep = netanalysis.analyze(m, devs, data, live, optics, opts, mpls=mpls_data or None)
    doc = {"id": os.urandom(8).hex(), "owner_id": user["id"], "map_id": map_id, "map_name": m["name"],
           "at": datetime.now(timezone.utc).isoformat(), "duration_sec": round(time.monotonic() - started, 1),
           "nodes": [{"id": n["id"], "x": n["x"], "y": n["y"], "kind": n.get("kind"),
                      "name": (devs.get(n.get("device_id")) or {}).get("name") or n.get("label") or ""} for n in m.get("nodes", [])
                     if n.get("kind") != "label"],
           "summary": rep["summary"], "report": rep}
    await db.net_reports.insert_one(dict(doc))
    for did, v in mpls_data.items():      # saída bruta da CLI fica à parte (para ajustar comandos/leitores)
        await db.net_reports_raw.insert_one({"rid": doc["id"], "device_id": did, "name": (devs.get(did) or {}).get("name"),
                                             "error": v.get("error"),
                                             "sections": {k: {"command": x.get("command"), "ok": x.get("ok"), "raw": x.get("raw")}
                                                          for k, x in (v.get("sections") or {}).items()}})
    # guarda os 15 últimos por mapa
    old = await db.net_reports.find({"map_id": map_id, "owner_id": user["id"]}, {"_id": 0, "id": 1, "at": 1}).sort("at", -1).to_list(100)
    for x in old[15:]:
        await db.net_reports.delete_one({"id": x["id"]})
        await db.net_reports_raw.delete_many({"rid": x["id"]})
    doc.pop("_id", None)
    return doc


@api.get("/maps/{map_id}/analysis")
async def list_analysis(map_id: str, user: dict = Depends(get_current_user)):
    await _map_for(user, map_id)
    rows = await db.net_reports.find({"map_id": map_id, "owner_id": user["id"]}, {"_id": 0, "id": 1, "at": 1, "summary": 1}).sort("at", -1).to_list(50)
    return [{"id": r["id"], "at": r["at"], "summary": r.get("summary")} for r in rows]


@api.get("/analysis/{rid}")
async def get_analysis(rid: str, user: dict = Depends(get_current_user)):
    return await _report_for(user, rid)


@api.get("/analysis/{rid}/raw/{device_id}")
async def analysis_raw(rid: str, device_id: str, user: dict = Depends(get_current_user)):
    await _report_for(user, rid)
    d = await db.net_reports_raw.find_one({"rid": rid, "device_id": device_id}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Sem saída MPLS guardada para este equipamento")
    return d


@api.get("/mpls/settings")
async def get_mpls_settings(user: dict = Depends(get_current_user)):
    s = await _mpls_settings()
    types = [t for t in DEVICE_TYPES if t in mplscli.DEFAULT_COMMANDS]
    return {"commands": {t: mplscli.commands_for(t, s) for t in types}, "defaults": mplscli.DEFAULT_COMMANDS,
            "sections": mplscli.SECTION_LABEL, "is_admin": user.get("role") == "admin"}


@api.put("/mpls/settings")
async def put_mpls_settings(body: MplsSettingsIn, _: dict = Depends(require_admin)):
    clean = {}
    for t, secs in (body.mpls_commands or {}).items():
        if t not in mplscli.DEFAULT_COMMANDS or not isinstance(secs, dict):
            continue
        for sec, cmds in secs.items():
            if sec not in mplscli.SECTIONS or not isinstance(cmds, list):
                continue
            lst = [str(c).strip() for c in cmds if str(c).strip()][:5]
            if lst != mplscli.DEFAULT_COMMANDS[t].get(sec, []):
                clean.setdefault(t, {})[sec] = lst
    await db.config.update_one({"key": "mpls"}, {"$set": {"mpls_commands": clean}}, upsert=True)
    return {"ok": True}


@api.post("/devices/{device_id}/mpls-test")
async def mpls_test(device_id: str, user: dict = Depends(get_current_user)):
    dev = await _get_device_for(user, device_id)
    try:
        r = await asyncio.wait_for(_mpls_collect(dev, await _mpls_settings()), timeout=240)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="CLI demorou demais")
    if r.get("skipped"):
        raise HTTPException(status_code=400, detail=f"Sem comandos MPLS para o tipo '{r['device_type']}'")
    return r


@api.delete("/analysis/{rid}")
async def delete_analysis(rid: str, user: dict = Depends(get_current_user)):
    r = await db.net_reports.delete_one({"id": rid, "owner_id": user["id"]})
    await db.net_reports_raw.delete_many({"rid": rid})
    return {"deleted": r.deleted_count}


@api.post("/analysis/{rid}/simulate")
async def simulate_analysis(rid: str, body: SimulateIn, user: dict = Depends(get_current_user)):
    d = await _report_for(user, rid)
    costs = {}
    for c in body.costs:
        try:
            if c.get("dir") in ("ab", "ba") and c.get("link_id") and int(c["cost"]) > 0:
                costs[(c["link_id"], c["dir"])] = min(int(c["cost"]), 65535)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Custo inválido")
    links = d["report"]["links"]
    r = netanalysis.simulate(links, set(body.down_links), costs, set(body.down_nodes))
    names = {n["id"]: n.get("name") for n in d.get("nodes", [])}
    r["isolated_names"] = [names.get(n, n) for n in r["cut_nodes"]]
    r["isolated_pairs"] = len(r["isolated_pairs"])
    return r


@api.get("/analysis/{rid}/html")
async def analysis_html(rid: str, user: dict = Depends(get_current_user)):
    d = await _report_for(user, rid)
    return HTMLResponse(netreport.render(d), headers={"Content-Disposition": f'attachment; filename="analise-{rid}.html"'})


# ---------- Enviar itens entre usuários / acesso do perfil View ----------
class TransferIn(BaseModel):
    target_user_id: str
    source_user_id: Optional[str] = None      # admin pode trazer de outro usuário
    device_ids: List[str] = []
    map_ids: List[str] = []
    dashboard_ids: List[str] = []
    include_credentials: bool = True


class AccessIn(BaseModel):
    map_ids: List[str] = []
    dashboard_ids: List[str] = []


class NocLayoutIn(BaseModel):
    maps: List[str] = []
    dashboards: List[str] = []
    rotate_sec: int = 0


def _user_brief(u: dict) -> dict:
    return {"id": u["id"], "name": u.get("name") or u["email"], "email": u["email"], "role": u.get("role") or "operator"}


@api.get("/transfer/targets")
async def transfer_targets(user: dict = Depends(get_current_user)):
    """Para quem posso enviar: admin -> qualquer usuário; demais -> administradores."""
    q = {"id": {"$ne": user["id"]}}
    if user.get("role") != "admin":
        q["role"] = "admin"
    rows = await db.users.find(q, USER_PUBLIC).to_list(1000)
    return sorted((_user_brief(u) for u in rows), key=lambda u: (u["role"] != "admin", u["name"].lower()))


@api.get("/transfer/assets")
async def transfer_assets(user_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    uid = user_id or user["id"]
    if uid != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Só o administrador vê os itens de outro usuário")
    devs = await db.devices.find({"owner_id": uid}, {"_id": 0, "id": 1, "name": 1, "host": 1, "port": 1, "protocol": 1, "tags": 1}).to_list(10000)
    maps = await db.maps.find({"owner_id": uid}, {"_id": 0, "id": 1, "name": 1, "nodes": 1}).to_list(1000)
    dashes = await db.dashboards.find({"owner_id": uid}, {"_id": 0, "id": 1, "name": 1, "group": 1, "widgets": 1}).to_list(1000)
    return {
        "devices": sorted(devs, key=lambda d: d["name"].lower()),
        "maps": sorted(({"id": m["id"], "name": m["name"], "devices": len(set(sharing.map_device_ids(m)))} for m in maps), key=lambda m: m["name"].lower()),
        "dashboards": sorted(({"id": d["id"], "name": d["name"], "group": d.get("group") or "Geral",
                               "devices": len(set(sharing.dash_device_ids(d)))} for d in dashes), key=lambda d: (d["group"].lower(), d["name"].lower())),
    }


@api.post("/transfer")
async def transfer(body: TransferIn, user: dict = Depends(get_current_user)):
    is_admin = user.get("role") == "admin"
    src_id = body.source_user_id or user["id"]
    if src_id != user["id"] and not is_admin:
        raise HTTPException(status_code=403, detail="Só o administrador pode trazer itens de outro usuário")
    src = await db.users.find_one({"id": src_id}, USER_PUBLIC)
    dst = await db.users.find_one({"id": body.target_user_id}, USER_PUBLIC)
    if not src or not dst:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if src_id == dst["id"]:
        raise HTTPException(status_code=400, detail="Origem e destino são o mesmo usuário")
    if src.get("role") == "viewer":
        raise HTTPException(status_code=400, detail="Usuário View não tem itens próprios para enviar")
    if not is_admin and dst.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Você só pode enviar para um administrador")
    if not (body.device_ids or body.map_ids or body.dashboard_ids):
        raise HTTPException(status_code=400, detail="Escolha pelo menos um item")

    if dst.get("role") == "viewer":
        # View não recebe cópia: passa a enxergar (ao vivo, só leitura) os mapas/dashboards escolhidos
        maps = [m["id"] async for m in db.maps.find({"id": {"$in": body.map_ids}, "owner_id": src_id}, {"_id": 0, "id": 1})]
        dashes = [d["id"] async for d in db.dashboards.find({"id": {"$in": body.dashboard_ids}, "owner_id": src_id}, {"_id": 0, "id": 1})]
        if not maps and not dashes:
            raise HTTPException(status_code=400, detail="Para usuário View, escolha mapas ou dashboards (equipamentos não se aplicam)")
        vm = list(dict.fromkeys((dst.get("view_maps") or []) + maps))
        vd = list(dict.fromkeys((dst.get("view_dashboards") or []) + dashes))
        await db.users.update_one({"id": dst["id"]}, {"$set": {"view_maps": vm, "view_dashboards": vd}})
        return {"mode": "shared", "maps": len(maps), "dashboards": len(dashes), "target": _user_brief(dst)}

    res = await sharing.copy_assets(db, src_id, dst["id"], body.device_ids, body.map_ids, body.dashboard_ids,
                                    include_credentials=body.include_credentials, src_label=src.get("name") or src["email"])
    logger.info(f"transferência {src['email']} -> {dst['email']} por {user['email']}: {res}")
    return {"mode": "copied", **res, "target": _user_brief(dst)}


@api.get("/access/catalog")
async def access_catalog(_: dict = Depends(require_admin)):
    """Todos os mapas e dashboards (de todos os usuários) para liberar a um usuário View."""
    owners = {u["id"]: (u.get("name") or u["email"]) async for u in db.users.find({}, {"_id": 0, "id": 1, "name": 1, "email": 1})}
    maps = [{"id": m["id"], "name": m["name"], "owner_id": m["owner_id"], "owner_name": owners.get(m["owner_id"], "?")}
            async for m in db.maps.find({}, {"_id": 0, "id": 1, "name": 1, "owner_id": 1})]
    dashes = [{"id": d["id"], "name": d["name"], "group": d.get("group") or "Geral", "owner_id": d["owner_id"],
               "owner_name": owners.get(d["owner_id"], "?")}
              async for d in db.dashboards.find({}, {"_id": 0, "id": 1, "name": 1, "group": 1, "owner_id": 1})]
    key = lambda x: (x["owner_name"].lower(), x["name"].lower())
    return {"maps": sorted(maps, key=key), "dashboards": sorted(dashes, key=key)}


@api.get("/users/{user_id}/access")
async def get_user_access(user_id: str, _: dict = Depends(require_admin)):
    u = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return {"map_ids": u.get("view_maps") or [], "dashboard_ids": u.get("view_dashboards") or []}


@api.put("/users/{user_id}/access")
async def set_user_access(user_id: str, body: AccessIn, _: dict = Depends(require_admin)):
    u = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    maps = [m["id"] async for m in db.maps.find({"id": {"$in": body.map_ids}}, {"_id": 0, "id": 1})]
    dashes = [d["id"] async for d in db.dashboards.find({"id": {"$in": body.dashboard_ids}}, {"_id": 0, "id": 1})]
    await db.users.update_one({"id": user_id}, {"$set": {"view_maps": maps, "view_dashboards": dashes}})
    return {"map_ids": maps, "dashboard_ids": dashes}


# ---------- Painel NOC: mapas e dashboards escolhidos por usuário ----------
async def _accessible_ids(user: dict):
    if _is_viewer(user):
        return set(user.get("view_maps") or []), set(user.get("view_dashboards") or [])
    maps = {m["id"] async for m in db.maps.find({"owner_id": user["id"]}, {"_id": 0, "id": 1})}
    dashes = {d["id"] async for d in db.dashboards.find({"owner_id": user["id"]}, {"_id": 0, "id": 1})}
    return maps, dashes


@api.get("/noc/layout")
async def get_noc_layout(user: dict = Depends(get_current_user)):
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0, "noc_layout": 1}) or {}
    lay = u.get("noc_layout") or {}
    maps, dashes = await _accessible_ids(user)
    return {"maps": [i for i in lay.get("maps", []) if i in maps],
            "dashboards": [i for i in lay.get("dashboards", []) if i in dashes],
            "rotate_sec": int(lay.get("rotate_sec") or 0)}


@api.put("/noc/layout")
async def put_noc_layout(body: NocLayoutIn, user: dict = Depends(get_current_user)):
    maps, dashes = await _accessible_ids(user)
    lay = {"maps": [i for i in dict.fromkeys(body.maps) if i in maps][:12],
           "dashboards": [i for i in dict.fromkeys(body.dashboards) if i in dashes][:12],
           "rotate_sec": 0 if body.rotate_sec <= 0 else max(10, min(int(body.rotate_sec), 600))}
    await db.users.update_one({"id": user["id"]}, {"$set": {"noc_layout": lay}})
    return lay


# ---------- Notificações push (PWA) ----------
class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeIn(BaseModel):
    endpoint: str
    keys: PushKeys
    device: str = ""


class PushEndpointIn(BaseModel):
    endpoint: str


@api.get("/push/public-key")
async def push_public_key(_: dict = Depends(get_current_user)):
    return {"key": (await webpush.get_vapid(db))["public_key"]}


@api.post("/push/subscribe")
async def push_subscribe(body: PushSubscribeIn, user: dict = Depends(get_current_user)):
    if not body.endpoint.startswith("https://"):
        raise HTTPException(400, "Endpoint de push inválido")
    await db.push_subs.update_one({"endpoint": body.endpoint}, {"$set": {
        "endpoint": body.endpoint, "keys": body.keys.model_dump(), "user_id": user["id"], "user_name": user.get("name"),
        "device": body.device[:120], "updated_at": datetime.now(timezone.utc).isoformat(), "last_error": None,
    }, "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    return {"ok": True}


@api.post("/push/unsubscribe")
async def push_unsubscribe(body: PushEndpointIn, user: dict = Depends(get_current_user)):
    await db.push_subs.delete_one({"endpoint": body.endpoint, "user_id": user["id"]})
    return {"ok": True}


@api.get("/push/devices")
async def push_devices(user: dict = Depends(get_current_user)):
    return [{k: s.get(k) for k in ("device", "created_at", "last_ok", "last_error")} | {"id": s["endpoint"][-16:]}
            async for s in db.push_subs.find({"user_id": user["id"]}, {"_id": 0})]


@api.post("/push/test")
async def push_test(user: dict = Depends(get_current_user)):
    res = await webpush.send(db, "🔔 BastiON", "Notificações ativadas. Os alarmes de interface e de equipamento chegam aqui.",
                             url="/", user_id=user["id"], tag="bastion-test")
    if not res["sent"]:
        raise HTTPException(400, "Nenhum aparelho recebeu. Ative as notificações neste aparelho primeiro."
                            if not res["failed"] else "O serviço de push recusou o envio — desative e ative de novo.")
    return res


# ---------- Flow (NetFlow / IPFIX / sFlow) ----------
FLOW_PRESETS = [
    {"name": "Google / YouTube", "asns": [15169, 36040, 43515, 36384, 36385, 139070, 396982]},
    {"name": "Meta (Facebook, Instagram, WhatsApp)", "asns": [32934, 63293]},
    {"name": "Netflix", "asns": [2906, 40027, 55095]},
    {"name": "Akamai", "asns": [20940, 16625, 16702, 21342, 32787, 33905, 35994]},
    {"name": "Cloudflare", "asns": [13335, 209242]},
    {"name": "Amazon / AWS / Prime Video", "asns": [16509, 14618, 7224, 8987]},
    {"name": "Microsoft (Azure, Xbox, Office)", "asns": [8075, 8068, 12076]},
    {"name": "TikTok / ByteDance", "asns": [396986, 138699]},
    {"name": "Apple", "asns": [714, 6185]},
    {"name": "Fastly", "asns": [54113]},
    {"name": "Valve / Steam", "asns": [32590]},
    {"name": "Twitch", "asns": [46489]},
]
_asn_cache = {"db": None, "ver": None, "checked": 0.0}
_flow_asn_task = None


async def _asn_db():
    if time.time() - _asn_cache["checked"] > 60:
        _asn_cache["checked"] = time.time()
        meta = await asndb.get_meta(db)
        if meta and meta.get("ver") != _asn_cache["ver"]:
            try:
                _asn_cache["db"] = await asndb.load(db, meta)
                _asn_cache["ver"] = meta["ver"]
            except Exception as e:
                logger.warning(f"base de ASN: {e}")
    return _asn_cache["db"]


def _no_viewer(user: dict):
    if _is_viewer(user):
        raise HTTPException(403, "Sem permissão")


class FlowSettingsIn(BaseModel):
    netflow_port: int = 2055
    sflow_port: int = 6343
    retention_days: int = 7
    hourly_days: int = 90
    own_prefixes: List[str] = []
    ignore_prefixes: List[str] = []
    sampling: dict = {}
    attack: dict = {}
    asn_auto: bool = True
    own_asn: int = 0
    auto_discover: bool = False
    discover_min_mbps: float = 5


class FlowIfaceIn(BaseModel):
    device_id: str
    if_index: int
    if_name: str = ""
    exporter: str = ""
    role: str = "transito"
    label: str = ""


class FlowIfaceUpd(BaseModel):
    role: Optional[str] = None
    label: Optional[str] = None
    exporter: Optional[str] = None


class FlowGroupIn(BaseModel):
    name: str
    asns: List[str] = []
    prefixes: List[str] = []
    color: str = ""


class FlowFilter(BaseModel):
    dim: Optional[str] = None
    values: List[str] = []


class FlowQueryIn(BaseModel):
    interfaces: List[str] = []           # ids de flow_ifaces (vazio = todas as minhas)
    minutes: int = 360
    direction: str = "in"
    group_by: str = "interface"
    filter: FlowFilter = FlowFilter()
    top: int = 10
    unit: str = "bps"
    by_block: bool = False


async def _flow_ifaces_of(user: dict) -> List[dict]:
    return await db.flow_ifaces.find({"owner_id": user["id"]}, {"_id": 0}).to_list(2000)


def _valid_ip(s: str) -> str:
    import ipaddress as _ipa
    try:
        return str(_ipa.ip_address(s.strip()))
    except ValueError:
        raise HTTPException(400, f"IP do exportador inválido: {s}")


@api.get("/flow/settings")
async def flow_get_settings(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    s = await flowstore.get_settings(db)
    meta = await asndb.get_meta(db)
    live = await db.flow_live.find_one({"_id": "live"}, {"_id": 0, "at": 1})
    st = await db.config.find_one({"key": "flow_asn_status"}, {"_id": 0}) or {}
    return {**s, "is_admin": user.get("role") == "admin", "roles": flowstore.ROLES, "dims": flowstore.DIMS,
            "presets": FLOW_PRESETS, "attack_defaults": flowagg.ATTACK_DEFAULTS,
            "asn": ({k: meta.get(k) for k in ("at", "source", "ranges_v4", "ranges_v6", "asns")} if meta else None),
            "asn_status": st, "collector_at": (live or {}).get("at")}


@api.put("/flow/settings")
async def flow_put_settings(body: FlowSettingsIn, _: dict = Depends(require_admin)):
    try:
        own = flowstore.clean_prefixes(body.own_prefixes)
        ign = flowstore.clean_prefixes(body.ignore_prefixes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    for p in (body.netflow_port, body.sflow_port):
        if not 1 <= p <= 65535:
            raise HTTPException(400, "Porta inválida")
    if body.netflow_port == body.sflow_port:
        raise HTTPException(400, "NetFlow e sFlow precisam de portas diferentes")
    samp = {}
    for ip, rate in (body.sampling or {}).items():
        try:
            r = int(rate)
        except (TypeError, ValueError):
            raise HTTPException(400, f"Taxa de amostragem inválida para {ip}")
        if r > 0:
            samp[_valid_ip(ip)] = min(r, 1_000_000)
    att = {**flowagg.ATTACK_DEFAULTS}
    for k, v in (body.attack or {}).items():
        if k not in att:
            continue
        if k == "enabled":
            att[k] = bool(v)
        else:
            try:
                att[k] = max(1, int(float(v)))
            except (TypeError, ValueError):
                raise HTTPException(400, f"Valor inválido em {k}")
    att["avg_windows"] = min(att["avg_windows"], 12)
    data = {"netflow_port": body.netflow_port, "sflow_port": body.sflow_port,
            "retention_days": max(1, min(body.retention_days, 60)), "hourly_days": max(7, min(body.hourly_days, 730)),
            "own_prefixes": own, "ignore_prefixes": ign, "sampling": samp, "attack": att, "asn_auto": body.asn_auto,
            "own_asn": body.own_asn if 0 <= body.own_asn < 2 ** 32 else 0, "auto_discover": body.auto_discover,
            "discover_min_mbps": max(0.1, min(float(body.discover_min_mbps or 5), 100000))}
    await db.config.update_one({"key": "flow"}, {"$set": {"key": "flow", **data}}, upsert=True)
    try:
        await flowstore.ensure_indexes(db, data)
    except Exception as e:
        logger.warning(f"índices do flow: {e}")
    return await flowstore.get_settings(db)


@api.get("/flow/exporters")
async def flow_exporters(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    exps = await db.flow_exporters.find({}, {"_id": 1, "kind": 1, "src": 1, "pps": 1, "fps": 1, "rate": 1, "last": 1,
                                             "no_template": 1, "errors": 1, "last_error": 1, "bidir": 1, "ifs": 1}).to_list(5000)
    if user.get("role") != "admin":
        hosts = {d.get("host") async for d in db.devices.find(_scope(user), {"_id": 0, "host": 1})}
        mine = {i["exporter"] for i in await _flow_ifaces_of(user)}
        exps = [e for e in exps if e["_id"] in hosts or e["_id"] in mine or e.get("src") in hosts]
    for e in exps:
        e["ip"] = e.pop("_id")
        e["n_ifs"] = len(e.get("ifs") or {})
        e.pop("ifs", None)
    return sorted(exps, key=lambda e: e.get("last") or "", reverse=True)


@api.get("/flow/devices/{device_id}/candidates")
async def flow_candidates(device_id: str, exporter: str = "", user: dict = Depends(get_current_user)):
    """Interfaces do equipamento com o tráfego que o coletor está vendo (flow ativo) em cada uma."""
    _no_viewer(user)
    dev = await _get_device_for(user, device_id)
    exps = await db.flow_exporters.find({}, {"_id": 1, "src": 1, "ifs": 1, "last": 1, "kind": 1}).to_list(5000)
    match = [e for e in exps if e["_id"] == dev.get("host") or e.get("src") == dev.get("host")]
    exp_ip = exporter.strip() or (match[0]["_id"] if match else dev.get("host", ""))
    exp_doc = next((e for e in exps if e["_id"] == exp_ip), None)
    ifs = (exp_doc or {}).get("ifs") or {}
    cached = await db.device_ifaces.find_one({"device_id": device_id}, {"_id": 0}) or {}
    mon = {(i["exporter"], int(i["if_index"])) for i in await _flow_ifaces_of(user) if i["device_id"] == device_id}
    rows, seen = [], set()
    for itf in cached.get("interfaces") or []:
        r = ifs.get(str(itf["index"])) or [0, 0]
        rows.append({"index": itf["index"], "name": itf.get("name"), "alias": itf.get("alias"), "speed_mbps": itf.get("speed_mbps"),
                     "oper": itf.get("oper"), "in_bps": r[0], "out_bps": r[1], "active": bool(r[0] or r[1]),
                     "monitored": (exp_ip, itf["index"]) in mon})
        seen.add(itf["index"])
    for idx, r in ifs.items():
        if int(idx) not in seen:
            rows.append({"index": int(idx), "name": None, "alias": "", "speed_mbps": None, "oper": None, "in_bps": r[0],
                         "out_bps": r[1], "active": True, "monitored": (exp_ip, int(idx)) in mon})
    rows.sort(key=lambda r: (not r["active"], -(r["in_bps"] + r["out_bps"]), r["index"]))
    return {"device": {"id": dev["id"], "name": dev.get("name"), "host": dev.get("host")}, "exporter": exp_ip,
            "exporter_seen": bool(exp_doc), "exporter_kind": (exp_doc or {}).get("kind"), "exporter_last": (exp_doc or {}).get("last"),
            "suggested": [e["_id"] for e in match], "snmp_cached": bool(cached.get("interfaces")), "interfaces": rows}


@api.get("/flow/interfaces")
async def flow_list_ifaces(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    items = await _flow_ifaces_of(user)
    live = (await db.flow_live.find_one({"_id": "live"}, {"_id": 0}) or {})
    lv = live.get("ifaces") or {}
    names = {d["id"]: d.get("name") async for d in db.devices.find(_scope(user), {"_id": 0, "id": 1, "name": 1})}
    for i in items:
        i["device_name"] = names.get(i["device_id"], "(equipamento removido)")
        i["live"] = lv.get(i["key"]) or {}
    items.sort(key=lambda i: (i["device_name"] or "", i["if_index"]))
    return {"items": items, "live_at": live.get("at")}


@api.post("/flow/interfaces")
async def flow_add_iface(body: FlowIfaceIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    dev = await _get_device_for(user, body.device_id)
    exp = _valid_ip(body.exporter or dev.get("host") or "")
    if body.role not in flowstore.ROLES:
        raise HTTPException(400, "Papel inválido")
    key = flowstore.iface_key(exp, body.if_index)
    if await db.flow_ifaces.find_one({"owner_id": user["id"], "key": key}):
        raise HTTPException(409, "Essa interface já está sendo monitorada")
    doc = {"id": str(uuid.uuid4()), "owner_id": user["id"], "device_id": dev["id"], "exporter": exp,
           "if_index": int(body.if_index), "if_name": body.if_name.strip()[:80] or f"ifIndex {body.if_index}",
           "key": key, "role": body.role, "label": body.label.strip()[:60],
           "created_at": datetime.now(timezone.utc).isoformat()}
    await db.flow_ifaces.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.put("/flow/interfaces/{iid}")
async def flow_upd_iface(iid: str, body: FlowIfaceUpd, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await db.flow_ifaces.find_one({"id": iid, "owner_id": user["id"]}, {"_id": 0})
    if not cur:
        raise HTTPException(404, "Interface não encontrada")
    upd = {}
    if body.role is not None:
        if body.role not in flowstore.ROLES:
            raise HTTPException(400, "Papel inválido")
        upd["role"] = body.role
    if body.label is not None:
        upd["label"] = body.label.strip()[:60]
    if body.exporter:
        upd["exporter"] = _valid_ip(body.exporter)
        upd["key"] = flowstore.iface_key(upd["exporter"], cur["if_index"])
    if upd:
        await db.flow_ifaces.update_one({"id": iid}, {"$set": upd})
    return {**cur, **upd}


@api.delete("/flow/interfaces/{iid}")
async def flow_del_iface(iid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    r = await db.flow_ifaces.delete_one({"id": iid, "owner_id": user["id"]})
    if not r.deleted_count:
        raise HTTPException(404, "Interface não encontrada")
    return {"ok": True}


@api.get("/flow/groups")
async def flow_list_groups(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return await db.flow_groups.find({"owner_id": user["id"]}, {"_id": 0}).sort("name", 1).to_list(1000)


def _group_doc(body: FlowGroupIn) -> dict:
    try:
        asns = flowstore.clean_asns(body.asns)
        pfx = flowstore.clean_prefixes(body.prefixes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not body.name.strip():
        raise HTTPException(400, "Dê um nome ao conteúdo")
    if not asns and not pfx:
        raise HTTPException(400, "Informe pelo menos um ASN ou um bloco")
    return {"name": body.name.strip()[:60], "asns": asns[:200], "prefixes": pfx[:2000], "color": body.color[:16]}


@api.post("/flow/groups")
async def flow_add_group(body: FlowGroupIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    doc = {"id": str(uuid.uuid4()), "owner_id": user["id"], **_group_doc(body),
           "created_at": datetime.now(timezone.utc).isoformat()}
    await db.flow_groups.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.put("/flow/groups/{gid}")
async def flow_upd_group(gid: str, body: FlowGroupIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await db.flow_groups.find_one({"id": gid, "owner_id": user["id"]}, {"_id": 0})
    if not cur:
        raise HTTPException(404, "Conteúdo não encontrado")
    doc = _group_doc(body)
    if doc["asns"] != cur.get("asns") or doc["prefixes"] != cur.get("prefixes"):
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()      # a contagem nova vale daqui para frente
    await db.flow_groups.update_one({"id": gid}, {"$set": doc})
    return {"id": gid, **doc}


@api.delete("/flow/groups/{gid}")
async def flow_del_group(gid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    r = await db.flow_groups.delete_one({"id": gid, "owner_id": user["id"]})
    if not r.deleted_count:
        raise HTTPException(404, "Conteúdo não encontrado")
    return {"ok": True}


def _flow_if_namer(dev_ifaces: dict, exp_dev: dict):
    def name(exp: str, idx: int):
        dev = exp_dev.get(exp)
        if not dev:
            return None
        for i in dev_ifaces.get(dev, []):
            if i.get("index") == idx:
                return i.get("name")
        return None
    return name


@api.post("/flow/query")
async def flow_query(body: FlowQueryIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return await _flow_query_run(body, user)


async def _flow_query_run(body: FlowQueryIn, user: dict) -> dict:
    mine = await _flow_ifaces_of(user)
    sel = [i for i in mine if not body.interfaces or i["id"] in body.interfaces or i["key"] in body.interfaces]
    if not sel:
        raise HTTPException(400, "Escolha pelo menos uma interface monitorada")
    keys = list(dict.fromkeys(i["key"] for i in sel))
    groups = await db.flow_groups.find({"owner_id": user["id"]}, {"_id": 0}).to_list(1000)
    gname = {g["id"]: g["name"] for g in groups}
    filt = body.filter.model_dump()
    if filt.get("dim") == "group":
        filt["values"] = [v for v in filt.get("values") or [] if v in gname]
        if not filt["values"]:
            raise HTTPException(400, "Escolha um conteúdo")
    try:
        res = await flowstore.query(db, keys=keys, minutes=body.minutes, direction=body.direction, group_by=body.group_by,
                                    filt=filt, top=body.top, unit="pps" if body.unit == "pps" else "bps",
                                    group_ids=list(gname), by_block=body.by_block)
    except ValueError as e:
        raise HTTPException(400, str(e))
    names = {d["id"]: d.get("name") async for d in db.devices.find(_scope(user), {"_id": 0, "id": 1, "name": 1})}
    by_key = {}
    for i in sel:
        by_key.setdefault(i["key"], i)
    asn = await _asn_db() if body.group_by in ("sas", "das") else None
    exp_dev = {i["exporter"]: i["device_id"] for i in mine}
    dev_ifaces = {}
    if body.group_by == "peer":
        async for d in db.device_ifaces.find({"device_id": {"$in": list(set(exp_dev.values()))}}, {"_id": 0, "device_id": 1, "interfaces": 1}):
            dev_ifaces[d["device_id"]] = d.get("interfaces") or []
    namer = _flow_if_namer(dev_ifaces, exp_dev)
    for s in res["series"]:
        if s.get("other"):
            s["name"] = "Outros"
        elif body.group_by == "interface":
            k, _, d = s["raw"].partition("#")
            i = by_key.get(k)
            base = f"{names.get(i['device_id'], i['exporter'])} · {i['if_name']}" if i else k
            s["name"] = base + (f" ({i['label']})" if i and i.get("label") else "") + ({"in": " ↓", "out": " ↑"}.get(d, ""))
            s["role"] = (i or {}).get("role")
            s["iface_id"] = (i or {}).get("id")
        elif body.group_by == "group":
            s["name"] = gname.get(s["raw"], s["raw"])
        else:
            raw = s["raw"]
            if body.group_by in ("sas", "das", "proto", "sport", "dport"):
                try:
                    raw = int(raw)
                except (TypeError, ValueError):
                    pass
            s["name"] = flowstore.dim_label(body.group_by, raw, asn.name if asn else None, namer)
        s.pop("raw", None)
    # conteúdo é contado na coleta: criado/alterado depois do início do período -> avisa desde quando vale
    if filt.get("dim") == "group" or body.group_by == "group":
        chosen = [g for g in groups if g["id"] in (filt.get("values") if filt.get("dim") == "group" else gname)]
        newest = max((g.get("updated_at") or g.get("created_at") or "" for g in chosen), default="")
        try:
            res["counting_since"] = newest if newest and datetime.fromisoformat(newest).timestamp() * 1000 > res["ts"][0] else None
        except (ValueError, IndexError):
            res["counting_since"] = None
    names_by_id = {s["id"]: s for s in res["series"]}
    for row in res["table"]:
        s = names_by_id.get(row["id"], {})
        row["name"] = s.get("name", row["id"])
        if s.get("role"):
            row["role"] = s["role"]
    return res


async def _flow_for_ai(user: dict, iface_text: str, group_by: str, direction: str, minutes: int) -> str:
    """Resumo em texto do Flow para o assistente."""
    mine = await _flow_ifaces_of(user)
    if not mine:
        return "Nenhuma interface com Flow monitorada por este usuário (Análise de Flow → Interfaces)."
    names = {d["id"]: d.get("name") async for d in db.devices.find(_scope(user), {"_id": 0, "id": 1, "name": 1})}
    words = [w.lower() for w in iface_text.split() if w.strip()]
    hay = lambda i: f"{names.get(i['device_id'], '')} {i.get('if_name')} {i.get('label')} {i.get('role')}".lower()  # noqa: E731
    sel = [i for i in mine if all(w in hay(i) for w in words)]
    if not sel:
        return ("Nenhuma interface monitorada casa com '" + iface_text + "'. Monitoradas: " +
                "; ".join(f"{names.get(i['device_id'], '?')} {i['if_name']} ({i.get('label') or i['role']})" for i in mine[:30]))
    gb = group_by if group_by in ("interface", "group", *flowstore.DIMS) else "interface"
    try:
        res = await _flow_query_run(FlowQueryIn(interfaces=[i["id"] for i in sel], minutes=minutes, direction=direction,
                                                group_by=gb, top=12), user)
    except HTTPException as e:
        return f"Flow: {e.detail}"
    step = res["step"]
    lines = [f"Flow {'entrada' if direction == 'in' else 'saída'} · últimos {minutes} min · agrupado por "
             f"{'interface' if gb == 'interface' else 'conteúdo' if gb == 'group' else flowstore.DIMS[gb]} · resolução {step // 60} min · "
             f"interfaces: " + ", ".join(f"{names.get(i['device_id'], '?')} {i['if_name']}" + (f" ({i['label']})" if i.get("label") else "") for i in sel[:8])]
    for r in res["table"]:
        lines.append(f"{r['name']}: média {netanalysis.fmt_bps(r['avg'])}, p95 {netanalysis.fmt_bps(r['p95'])}, "
                     f"máx {netanalysis.fmt_bps(r['max'])}, agora {netanalysis.fmt_bps(r['last'])}, {r['share'] * 100:.0f}%")
    if not res["table"]:
        lines.append("(sem dados no período)")
    act = await db.flow_attacks.find({"status": "active", **({} if user.get("role") == "admin" else {"owners": user["id"]})},
                                     {"_id": 0, "victim": 1, "type": 1, "cur_bps": 1, "peak_bps": 1, "start": 1}).to_list(20)
    if act:
        lines.append("ATAQUES EM ANDAMENTO: " + "; ".join(f"{a['victim']} {a['type']} agora {netanalysis.fmt_bps(a.get('cur_bps'))} "
                                                       f"(pico {netanalysis.fmt_bps(a.get('peak_bps'))}, desde {a['start'][11:16]} UTC)" for a in act))
    return "\n".join(lines)


@api.get("/flow/live")
async def flow_live(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    live = await db.flow_live.find_one({"_id": "live"}, {"_id": 0}) or {}
    keys = {i["key"] for i in await _flow_ifaces_of(user)}
    age = None
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(live["at"])).total_seconds()
    except Exception:
        pass
    return {"at": live.get("at"), "age_sec": age, "ifaces": {k: v for k, v in (live.get("ifaces") or {}).items() if k in keys}}


@api.get("/flow/attacks")
async def flow_attacks(status: str = "", limit: int = 100, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    q = {} if user.get("role") == "admin" else {"owners": user["id"]}
    if status in ("active", "ended"):
        q["status"] = status
    items = await db.flow_attacks.find(q, {"_id": 0, "series": 0, "src_ip": 0}).sort("start", -1).to_list(max(1, min(limit, 500)))
    asn = await _asn_db()
    for a in items:
        a["src_as"] = [[x, b, (asn.name(x) if asn and x else "")] for x, b in a.get("src_as") or []]
    return items


@api.get("/flow/attacks/{aid}")
async def flow_attack(aid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    q = {"id": aid} if user.get("role") == "admin" else {"id": aid, "owners": user["id"]}
    a = await db.flow_attacks.find_one(q, {"_id": 0})
    if not a:
        raise HTTPException(404, "Ataque não encontrado")
    asn = await _asn_db()
    a["src_as"] = [[x, b, (asn.name(x) if asn and x else "")] for x, b in a.get("src_as") or []]
    a["src_ip"] = [[ip, b, p, x, (asn.name(x) if asn and x else "")] for ip, b, p, x in a.get("src_ip") or []]
    return a


# ---------- Mitigação de DDoS (blackhole via BGP do BastiON) ----------
class MitigationSettingsIn(BaseModel):
    enabled: bool = False
    local_as: int = 0
    router_id: str = ""
    local_address: str = ""
    hold_time: int = 90
    next_hop: str = "192.0.2.1"
    communities: List[str] = []
    no_export: bool = True
    local_pref: int = 200
    peers: List[dict] = []
    default_minutes: int = 30
    max_minutes: int = 1440
    max_active: int = 20
    protect: List[str] = []


class MitigateIn(BaseModel):
    prefix: str = ""
    attack_id: str = ""
    minutes: int = 0
    reason: str = ""


class MitigationMinutes(BaseModel):
    minutes: int = 30


def _mit_err(e: Exception):
    raise HTTPException(400, str(e))


@api.get("/flow/mitigation/settings")
async def mit_get_settings(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    s = await mitigation.get_settings(db)
    fs = await flowstore.get_settings(db)
    return {**s, "is_admin": user.get("role") == "admin", "durations": mitigation.DURATIONS,
            "own_prefixes": fs.get("own_prefixes") or []}


@api.put("/flow/mitigation/settings")
async def mit_put_settings(body: MitigationSettingsIn, _: dict = Depends(require_admin)):
    try:
        data = mitigation.clean_settings(body.model_dump())
    except mitigation.MitigationError as e:
        _mit_err(e)
    await db.config.update_one({"key": "bgp"}, {"$set": {"key": "bgp", **data}}, upsert=True)
    return await mit_get_settings(_)


@api.get("/flow/mitigation/status")
async def mit_status(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    st = await db.bgp_status.find_one({"_id": "status"}, {"_id": 0}) or {}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(st["at"])).total_seconds() if st.get("at") else None
    except ValueError:
        age = None
    active = await db.flow_mitigations.find({"status": "active"}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {**st, "daemon_age": age, "daemon_ok": age is not None and age < 30, "active": active}


@api.get("/flow/mitigations")
async def mit_list(user: dict = Depends(get_current_user), limit: int = 100):
    _no_viewer(user)
    return await db.flow_mitigations.find({}, {"_id": 0}).sort("created_at", -1).to_list(min(max(limit, 1), 500))


@api.post("/flow/mitigations")
async def mit_create(body: MitigateIn, user: dict = Depends(get_current_user)):
    try:
        m = await mitigation.create(db, user, prefix=body.prefix or None, attack_id=body.attack_id or None,
                                    minutes=body.minutes or None, reason=body.reason, channel="web")
    except mitigation.MitigationError as e:
        _mit_err(e)
    if not m.get("extended"):
        _bg(automation.send_alert(db, f"🛡️ Mitigação ATIVADA: {m['prefix']}",
                                  f"Blackhole na borda por {mitigation.fmt_minutes(m['minutes'])} (por {user.get('email')}).\n"
                                  "O IP fica sem tráfego nenhum até o prazo acabar ou alguém remover.", push_url="/flow?tab=mitigacao"))
    return m


@api.post("/flow/mitigations/{mid}/extend")
async def mit_extend(mid: str, body: MitigationMinutes, user: dict = Depends(get_current_user)):
    try:
        return await mitigation.extend(db, user, mid, body.minutes)
    except mitigation.MitigationError as e:
        _mit_err(e)


@api.post("/flow/mitigations/{mid}/withdraw")
async def mit_withdraw(mid: str, user: dict = Depends(get_current_user)):
    try:
        m = await mitigation.withdraw(db, user, mid)
    except mitigation.MitigationError as e:
        _mit_err(e)
    _bg(automation.send_alert(db, f"🟢 Mitigação removida: {m['prefix']}", f"Removida por {user.get('email')}.", push_url="/flow?tab=mitigacao"))
    return m


@api.get("/flow/mitigation/router-config")
async def mit_router_config(vendor: str = "huawei", bastion_ip: str = "", user: dict = Depends(get_current_user)):
    _no_viewer(user)
    s = await mitigation.get_settings(db)
    fs = await flowstore.get_settings(db)
    return {"vendor": vendor, "config": mitigation.router_config(s, vendor, bastion_ip.strip(), fs.get("own_prefixes") or [])}


# ---------- Sugestão de peering ----------
@api.get("/flow/peering")
async def flow_peering(days: int = 7, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    mine = await _flow_ifaces_of(user)
    tr = list(dict.fromkeys(i["key"] for i in mine if i.get("role") == "transito"))
    ix = list(dict.fromkeys(i["key"] for i in mine if i.get("role") in ("ix", "pni", "cdn")))
    if not tr:
        raise HTTPException(400, "Marque as interfaces de trânsito (papel 'Trânsito') na aba Interfaces")
    fs = await flowstore.get_settings(db)
    asn = await _asn_db()
    return await peering.suggestions(db, transit_keys=tr, ix_keys=ix, days=days, own_asn=int(fs.get("own_asn") or 0),
                                     asn_name=asn.name if asn else None)


# ---------- Descoberta de interfaces ----------
async def _discover_for(user: dict, refresh_snmp: bool = False) -> dict:
    fs = await flowstore.get_settings(db)
    exps = await db.flow_exporters.find({}, {"_id": 1, "src": 1, "ifs": 1, "last": 1, "kind": 1}).to_list(5000)
    devs = await db.devices.find(_scope(user), {"_id": 0, "id": 1, "name": 1, "host": 1, "flow_exporter": 1}).to_list(10000)
    hosts = {e["_id"] for e in exps} | {e.get("src") for e in exps}
    caches, snmp_errors = {}, {}
    for d in devs:
        if d.get("host") not in hosts and d.get("flow_exporter") not in hosts:
            continue
        c = await db.device_ifaces.find_one({"device_id": d["id"]}, {"_id": 0, "interfaces": 1})
        if (not c or refresh_snmp):
            try:
                c = await device_interfaces(d["id"], True, user)
            except HTTPException as e:
                snmp_errors[d["name"]] = str(e.detail)
        caches[d["id"]] = (c or {}).get("interfaces") or []
    monitored = {(i["exporter"], int(i["if_index"])) for i in await _flow_ifaces_of(user)}
    res = flowdiscover.build(exps, devs, caches, monitored, float(fs.get("discover_min_mbps") or 5) * 1e6)
    res["snmp_errors"] = snmp_errors
    res["min_mbps"] = fs.get("discover_min_mbps") or 5
    return res


@api.get("/flow/discover")
async def flow_discover(refresh: bool = False, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return await _discover_for(user, refresh)


class DiscoverItem(BaseModel):
    device_id: str
    exporter: str
    if_index: int
    if_name: str = ""
    role: str = "outro"
    label: str = ""


class DiscoverApplyIn(BaseModel):
    items: List[DiscoverItem] = []


@api.post("/flow/discover/apply")
async def flow_discover_apply(body: DiscoverApplyIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    added, skipped = [], []
    for it in body.items[:500]:
        try:
            doc = await flow_add_iface(FlowIfaceIn(device_id=it.device_id, if_index=it.if_index, if_name=it.if_name,
                                                   exporter=it.exporter, role=it.role if it.role in flowstore.ROLES else "outro",
                                                   label=it.label), user)
            added.append(doc)
        except HTTPException as e:
            skipped.append({"if_name": it.if_name, "reason": str(e.detail)})
    return {"added": len(added), "skipped": skipped}


async def _auto_discover_loop():
    """Com 'descoberta automática' ligada: a cada 6 h adiciona interfaces novas com papel sugerido."""
    await asyncio.sleep(120)
    while True:
        try:
            fs = await flowstore.get_settings(db)
            if fs.get("auto_discover"):
                for u in await db.users.find({"role": {"$in": ["admin", "operator"]}}, {"_id": 0, "id": 1, "email": 1, "role": 1}).to_list(1000):
                    res = await _discover_for(u)
                    items = [DiscoverItem(device_id=d["device_id"], exporter=r["exporter"], if_index=r["if_index"], if_name=r["if_name"],
                                          role=r["role"], label=r["alias"])
                             for d in res["devices"] for r in d["items"] if r["role"]]
                    if items:
                        out = await flow_discover_apply(DiscoverApplyIn(items=items), u)
                        if out["added"]:
                            names = ", ".join(f"{i.if_name} ({flowstore.ROLES.get(i.role, i.role)})" for i in items[:10])
                            await automation.send_alert(db, f"🔎 Flow: {out['added']} interface(s) nova(s) monitorada(s)",
                                                        f"Adicionadas sozinhas para {u['email']}: {names}. Revise em Flow → Interfaces.",
                                                        push_url="/flow?tab=interfaces")
        except Exception as e:
            logger.warning(f"descoberta automática de flow: {e}")
        await asyncio.sleep(6 * 3600)


@api.get("/flow/asn/lookup")
async def flow_asn_lookup(q: str = "", user: dict = Depends(get_current_user)):
    """IP -> ASN e bloco; número do AS -> nome e prefixos; texto -> busca pelo nome."""
    _no_viewer(user)
    a = await _asn_db()
    if not a:
        raise HTTPException(400, "A base de ASN ainda não foi baixada (Flow → Configuração → Base de ASN)")
    q = q.strip()
    import ipaddress as _ipa
    try:
        _ipa.ip_address(q)
        r = a.range_of(q)
        if not r:
            return {"kind": "ip", "ip": q, "asn": None}
        return {"kind": "ip", "ip": q, "asn": r["asn"], "name": a.name(r["asn"]), "prefixes": r["prefixes"]}
    except ValueError:
        pass
    s = q.upper().removeprefix("AS")
    if s.isdigit():
        p = await asyncio.to_thread(a.prefixes_of, int(s))
        return {"kind": "asn", "asn": int(s), "name": a.name(int(s)), **p}
    if len(q) < 2:
        raise HTTPException(400, "Digite um IP, um ASN ou parte do nome")
    return {"kind": "search", "results": a.search(q)}


async def _flow_asn_update(source: str = "download", raw: Optional[bytes] = None) -> dict:
    await db.config.update_one({"key": "flow_asn_status"}, {"$set": {"key": "flow_asn_status", "state": "atualizando",
                                                                      "at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    try:
        if raw is None:
            raw = await asndb.download()
        meta = await asndb.import_raw(db, raw, source)
        _asn_cache["checked"] = 0
        await db.config.update_one({"key": "flow_asn_status"}, {"$set": {"state": "ok", "error": None,
                                                                          "at": datetime.now(timezone.utc).isoformat()}})
        return meta
    except Exception as e:
        await db.config.update_one({"key": "flow_asn_status"}, {"$set": {"state": "erro", "error": str(e)[:300],
                                                                          "at": datetime.now(timezone.utc).isoformat()}})
        raise


@api.post("/flow/asn/update")
async def flow_asn_update(_: dict = Depends(require_admin)):
    try:
        return await _flow_asn_update("iptoasn.com")
    except Exception as e:
        raise HTTPException(502, f"Não consegui atualizar a base: {e}. Se o servidor não acessa iptoasn.com, baixe "
                                 f"ip2asn-combined.tsv.gz em outro computador e envie pelo botão ao lado.")


@api.post("/flow/asn/upload")
async def flow_asn_upload(file: UploadFile = File(...), _: dict = Depends(require_admin)):
    raw = await file.read()
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(400, "Arquivo grande demais")
    try:
        return await _flow_asn_update(f"arquivo {file.filename}", raw)
    except Exception as e:
        raise HTTPException(400, f"Arquivo inválido: {e}")


async def _flow_asn_loop():
    """Atualiza a base de ASN uma vez por semana (se o Flow estiver em uso e a opção ligada)."""
    await asyncio.sleep(120)
    while True:
        try:
            s = await flowstore.get_settings(db)
            if s.get("asn_auto") and await db.flow_ifaces.count_documents({}) > 0:
                meta = await asndb.get_meta(db)
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(meta["at"])).total_seconds() if meta else 1e12
                if age > 7 * 86400:
                    await _flow_asn_update("iptoasn.com (automático)")
        except Exception as e:
            logger.warning(f"atualização automática da base de ASN: {e}")
        await asyncio.sleep(6 * 3600)



# ---------- VPN SSL (FortiGate) no servidor ----------
class VpnProfileIn(BaseModel):
    name: str
    host: str
    port: int = 443
    username: str
    password: Optional[str] = None       # write-only; vazio mantém
    realm: str = ""
    routes: List[str] = []
    trusted_certs: List[str] = []


class VpnConnectIn(BaseModel):
    otp: str = ""


def _vpn_public(p: dict, st: Optional[dict]) -> dict:
    out = {k: v for k, v in p.items() if k not in ("password", "_id")}
    out["has_password"] = bool(p.get("password"))
    st = st or {}
    out["status"] = {k: st.get(k) for k in ("state", "since", "iface", "ip", "routes", "error", "pending_cert", "prompt", "log", "updated", "diag", "diag_at")}
    out["status"]["state"] = st.get("state") or "disconnected"
    return out


async def _vpn_daemon_ok() -> bool:
    d = await db.vpn_status.find_one({"_id": "_daemon"}, {"_id": 0, "at": 1})
    try:
        return bool(d) and (datetime.now(timezone.utc) - datetime.fromisoformat(d["at"])).total_seconds() < 20
    except Exception:
        return False


async def _get_vpn_for(user: dict, vid: str) -> dict:
    p = await db.vpn_profiles.find_one({"id": vid, **_scope(user)}, {"_id": 0})
    if not p:
        raise HTTPException(404, "VPN não encontrada")
    return p


def _vpn_clean(body: VpnProfileIn) -> dict:
    import ipaddress as _ipa
    host = body.host.strip().removeprefix("https://").removeprefix("http://").split("/")[0]
    if ":" in host and host.count(":") == 1:
        host, port = host.split(":")
        body.port = int(port) if port.isdigit() else body.port
    if not host or not re.match(r"^[A-Za-z0-9.\-]+$", host):
        raise HTTPException(400, "Gateway inválido (use o IP ou nome, ex.: vpn.empresa.com.br)")
    if not 1 <= body.port <= 65535:
        raise HTTPException(400, "Porta inválida")
    if not body.name.strip() or not body.username.strip():
        raise HTTPException(400, "Informe nome e usuário")
    routes = []
    for r in body.routes:
        for part in str(r).replace(",", " ").split():
            try:
                n = _ipa.ip_network(part, strict=False)
            except ValueError:
                raise HTTPException(400, f"Rede inválida: {part}")
            if n.prefixlen == 0 or n.version != 4:
                raise HTTPException(400, f"Rede não permitida: {part} (rota padrão/IPv6 derrubariam o acesso ao servidor)")
            routes.append(str(n))
    certs = [c.strip().lower() for c in body.trusted_certs if re.fullmatch(r"[0-9a-fA-F]{64}", c.strip())]
    return {"name": body.name.strip()[:60], "host": host, "port": int(body.port), "username": body.username.strip(),
            "realm": body.realm.strip()[:60], "routes": list(dict.fromkeys(routes)), "trusted_certs": list(dict.fromkeys(certs))}


@api.get("/vpns")
async def list_vpns(user: dict = Depends(get_current_user)):
    if _is_viewer(user):
        return {"items": [], "daemon": False}
    items = await db.vpn_profiles.find(_scope(user), {"_id": 0}).sort("name", 1).to_list(100)
    st = {s["_id"]: s async for s in db.vpn_status.find({"_id": {"$in": [p["id"] for p in items]}})}
    agents = await db.agents.find({**_scope(user), "vpn_id": {"$in": [p["id"] for p in items]}}, {"_id": 0, "name": 1, "vpn_id": 1}).to_list(500)
    out = []
    for p in items:
        o = _vpn_public(p, st.get(p["id"]))
        o["agents"] = [a["name"] for a in agents if a.get("vpn_id") == p["id"]]
        out.append(o)
    return {"items": out, "daemon": await _vpn_daemon_ok()}


@api.post("/vpns")
async def create_vpn(body: VpnProfileIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    if not body.password:
        raise HTTPException(400, "Informe a senha da VPN")
    doc = {"id": str(uuid.uuid4()), "owner_id": user["id"], **_vpn_clean(body), "password": vault.encrypt(body.password),
           "created_at": datetime.now(timezone.utc).isoformat()}
    await db.vpn_profiles.insert_one(doc)
    return _vpn_public(doc, None)


@api.put("/vpns/{vid}")
async def update_vpn(vid: str, body: VpnProfileIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await _get_vpn_for(user, vid)
    data = _vpn_clean(body)
    if body.password:
        data["password"] = vault.encrypt(body.password)
    await db.vpn_profiles.update_one({"id": vid}, {"$set": data})
    return _vpn_public({**cur, **data}, await db.vpn_status.find_one({"_id": vid}))


@api.delete("/vpns/{vid}")
async def delete_vpn(vid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _get_vpn_for(user, vid)
    await db.vpn_profiles.delete_one({"id": vid})
    await db.agents.update_many({"vpn_id": vid}, {"$set": {"vpn_id": None}})
    return {"ok": True}


async def _vpn_cmd(vid: str, action: str, otp: str = ""):
    await db.vpn_cmds.insert_one({"id": uuid.uuid4().hex, "profile_id": vid, "action": action, "otp": otp,
                                  "at": datetime.now(timezone.utc).isoformat(), "ts": time.time()})


@api.post("/vpns/{vid}/connect")
async def connect_vpn(vid: str, body: VpnConnectIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    p = await _get_vpn_for(user, vid)
    if not await _vpn_daemon_ok():
        raise HTTPException(503, "O serviço de VPN não está rodando no servidor. Coloque COMPOSE_PROFILES=vpn no deploy/.env e rode o update.sh.")
    otp = re.sub(r"\s", "", body.otp or "")      # vazio = o gateway manda o token (e-mail/SMS) e o BastiON pede depois
    if len(otp) > 64:
        raise HTTPException(400, "Token inválido")
    await db.vpn_status.update_one({"_id": vid}, {"$set": {"state": "connecting", "error": None, "pending_cert": None, "prompt": None,
                                                           "since": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    await _vpn_cmd(vid, "connect", otp)
    return _vpn_public(p, await db.vpn_status.find_one({"_id": vid}))


@api.post("/vpns/{vid}/otp")
async def vpn_send_otp(vid: str, body: VpnConnectIn, user: dict = Depends(get_current_user)):
    """Token pedido pelo gateway no meio da conexão (ex.: código enviado por e-mail)."""
    _no_viewer(user)
    p = await _get_vpn_for(user, vid)
    st = await db.vpn_status.find_one({"_id": vid}) or {}
    if st.get("state") != "need_otp":
        raise HTTPException(400, "A VPN não está aguardando token agora — clique em Conectar de novo")
    otp = re.sub(r"\s", "", body.otp or "")
    if not otp or len(otp) > 64:
        raise HTTPException(400, "Digite o token")
    await db.vpn_status.update_one({"_id": vid}, {"$set": {"state": "connecting", "prompt": None}})
    await _vpn_cmd(vid, "otp", otp)
    return _vpn_public(p, await db.vpn_status.find_one({"_id": vid}))


@api.post("/vpns/{vid}/diag")
async def vpn_diag(vid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    p = await _get_vpn_for(user, vid)
    if not await _vpn_daemon_ok():
        raise HTTPException(503, "O serviço de VPN não está rodando no servidor")
    await db.vpn_status.update_one({"_id": vid}, {"$set": {"diag": "rodando diagnóstico…", "diag_at": None}}, upsert=True)
    await _vpn_cmd(vid, "diag")
    return _vpn_public(p, await db.vpn_status.find_one({"_id": vid}))


@api.post("/vpns/{vid}/disconnect")
async def disconnect_vpn(vid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    p = await _get_vpn_for(user, vid)
    await db.vpn_status.update_one({"_id": vid, "state": {"$in": ["up", "connecting", "need_otp"]}}, {"$set": {"state": "disconnecting"}})
    await _vpn_cmd(vid, "disconnect")
    return _vpn_public(p, await db.vpn_status.find_one({"_id": vid}))


@api.post("/vpns/{vid}/trust-cert")
async def trust_vpn_cert(vid: str, user: dict = Depends(get_current_user)):
    """Confia no certificado que o gateway apresentou (impressão digital SHA-256 lida na última tentativa)."""
    _no_viewer(user)
    p = await _get_vpn_for(user, vid)
    st = await db.vpn_status.find_one({"_id": vid}) or {}
    fp = st.get("pending_cert")
    if not fp:
        raise HTTPException(400, "Nenhum certificado pendente")
    certs = list(dict.fromkeys((p.get("trusted_certs") or []) + [fp]))
    await db.vpn_profiles.update_one({"id": vid}, {"$set": {"trusted_certs": certs}})
    await db.vpn_status.update_one({"_id": vid}, {"$set": {"pending_cert": None, "error": None, "state": "disconnected"}})
    return {"trusted_certs": certs}


# ---------- Health ----------
@api.get("/")
async def root():
    return {"ok": True, "service": "SSH BastiON Central"}


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    scheduler.stop()
    assistant.stop()
    net_monitor.stop()
    optics_collector.stop()
    if _flow_asn_task:
        _flow_asn_task.cancel()
    await agent_pool.close_all()
    await web_proxy.stop()
    client.close()
