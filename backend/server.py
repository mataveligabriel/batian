"""SSH BastiON Central - FastAPI backend."""
from dotenv import load_dotenv
from pathlib import Path
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import csv
import io
import json
import os
import posixpath
import re
import hashlib
import hmac
import httpx
import asyncio
import time
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect, Query, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, FileResponse, RedirectResponse
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
from ssh_service import SSHClientWrapper, Hop, tcp_ping, LEGACY_TYPES, JUMP_POOL
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
import dbbackup
import webdomain
import incidents as incmod
import oncall as oncallmod
import recording
import logparse
import probes as probemod
import sla as slamod
import maint as maintmod
import bgpmon as bgpmod
import gpon as gponmod
import compliance as compmod
import ipam as ipammod
import shlex
import ipaddress
import lookingglass
import rpki
import vendorscan
import rdp
import oltgen
import olthw
import oltfh
import cmdpolicy
import siem as siemmod
import loginanom
import threatintel
import certbr
import cves as cvemod
import sso as ssomod
import tacacs as tacmod
import ftpserver as ftpmod
import ipamdoc
import tcprelay
import runbooks
import teopt
import vpnconf
import botnet
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
    await db.cmd_approvals.create_index("id")
    await db.cmd_approvals.create_index([("hash", 1), ("status", 1)])
    await db.cmd_events.create_index([("at", -1)])
    await db.audit_events.create_index("ts", expireAfterSeconds=180 * 86400)
    await db.audit_events.create_index([("at", -1)])
    await db.login_anomalies.create_index([("at", -1)])
    await db.ti_hits.create_index("key", unique=True)
    await db.ti_hits.create_index([("day", -1), ("dir", 1)])
    await db.ti_hits.create_index("alerted")
    await db.cert_items.create_index("key", unique=True)
    await db.cve_results.create_index("device_id", unique=True)
    await db.sso_states.create_index("ts", expireAfterSeconds=900)
    await db.sso_codes.create_index("ts", expireAfterSeconds=300)
    await db.cert_flow.create_index("key", unique=True)
    await db.cert_flow.create_index("ts", expireAfterSeconds=30 * 86400)
    await db.tacacs_events.create_index([("at", -1)])
    await db.ftp_accesses.create_index("id")
    await db.ipam_docgroups.create_index("id")
    await db.ipam_docsubnets.create_index([("group_id", 1), ("prefix", 1)])
    await db.ipam_docips.create_index([("group_id", 1), ("ip", 1), ("vrf", 1)])
    await db.ipam_docips.create_index("subnet_id")
    await db.ipam_docips.create_index("ip")
    await db.ftp_events.create_index([("at", -1)])
    await db.ftp_events.create_index("ts", expireAfterSeconds=180 * 86400)
    await db.tacacs_accounts.create_index("id")
    try:
        await flowstore.ensure_ttl(db, "tacacs_events", (await _tac_cfg())["retention_days"] * 86400)
    except Exception as e:
        logger.warning(f"TACACS TTL: {e}")
    try:
        await flowstore.ensure_ttl(db, "ti_hits", (await _ti_cfg())["retention_days"] * 86400)
    except Exception as e:
        logger.warning(f"threat intel TTL: {e}")
    await db.login_events.create_index([("email", 1), ("at", -1)])
    await db.sessions.create_index("ended_at")
    await _apply_retention()
    await db.if_samples.create_index([("device_id", 1), ("if_index", 1), ("ts", -1)])
    await db.optics_samples.create_index([("device_id", 1), ("if_index", 1), ("ts", -1)])
    await db.dashboards.create_index("owner_id")
    await db.if_monitors.create_index([("device_id", 1), ("if_index", 1)], unique=True)
    await db.if_events.create_index([("at", -1)])
    await db.bgp_peers.create_index("key", unique=True)
    await db.gpon_onus.create_index("key", unique=True)
    await db.gpon_onus.create_index([("olt_id", 1), ("slot", 1), ("pon", 1)])
    await db.gpon_onus.create_index("sn")
    await db.ipam_addresses.create_index("device_id")
    await db.ipam_addresses.create_index("ip")
    await db.gpon_pons.create_index("key", unique=True)
    await db.gpon_samples.create_index([("key", 1), ("at", 1)])
    await db.bgp_events.create_index([("key", 1), ("at", -1)])
    await db.maps.create_index("owner_id")
    await db.net_reports.create_index([("map_id", 1), ("at", -1)])
    await db.net_reports_raw.create_index("rid")
    scheduler.start()
    _bg_tasks.append(asyncio.create_task(inc_center.backfill()))
    _bg_tasks.append(asyncio.create_task(_oncall_loop()))
    _bg_tasks.append(asyncio.create_task(_rec_retention_loop()))
    _bg_tasks.append(asyncio.create_task(_probe_loop()))
    _bg_tasks.append(asyncio.create_task(_sla_monthly_loop()))
    _bg_tasks.append(asyncio.create_task(_maint_loop()))
    _bg_tasks.append(asyncio.create_task(_bgp_loop()))
    _bg_tasks.append(asyncio.create_task(_ftp_loop()))
    _bg_tasks.append(asyncio.create_task(_gpon_loop()))
    _bg_tasks.append(asyncio.create_task(_siem_loop()))
    _bg_tasks.append(asyncio.create_task(_ti_loop()))
    _bg_tasks.append(asyncio.create_task(_cert_loop()))
    _bg_tasks.append(asyncio.create_task(_cve_loop()))
    _bg_tasks.append(asyncio.create_task(_comp_loop()))
    _bg_tasks.append(asyncio.create_task(_ipam_loop()))
    _bg_tasks.append(asyncio.create_task(dbbackup.auto_loop(db, vault.decrypt, _db_cloud_upload, _db_alert)))
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
    _bg(_botnet_lookup_loop())
    _bg(_prune_all_copied_agents_once())
    await db.flow_mitigations.create_index([("status", 1), ("created_at", -1)])
    await db.flow_mitigations.create_index("id")
    await db.flow_botnet.create_index([("status", 1), ("start", -1)])
    await db.flow_botnet.create_index("id")
    await db.lg_public_log.create_index("ts", expireAfterSeconds=30 * 86400)
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
    if not ssomod.password_allowed(user, await db.config.find_one({"key": "sso"}, {"_id": 0, "password_login": 1}) or {}) and not (
            user.get("role") == "admin" and await db.users.count_documents({"role": "admin"}) <= 1):      # o único admin sempre entra
        await security.log_event(db, email, ip, ua, False, "senha: só login único", user["id"])
        raise HTTPException(status_code=403, detail="Este usuário entra pelo login único (botão abaixo), não com senha")
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
    await _login_gate(user, ip, ua)
    await security.clear_fails(db, email)
    await security.log_event(db, email, ip, ua, True, "", user["id"])
    _bg(_login_anomaly(user, ip, ua))
    await db.users.update_one({"id": user["id"]}, {"$set": {"last_login_at": datetime.now(timezone.utc).isoformat(), "last_login_ip": ip}})
    return _session_payload(user)


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    u = await db.users.find_one({"id": user["id"]})
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    sec = await _security_settings()
    return {"id": u["id"], "email": u["email"], "name": u.get("name", ""), "role": u.get("role", "operator"),
            "totp_enabled": bool(u.get("totp_enabled")), "require_2fa": sec["require_2fa"] and not u.get("sso"),
            "sso": u.get("sso") or None,
            "modules": u.get("modules") if isinstance(u.get("modules"), list) and u.get("role") == "operator" else None,
            "theme": u.get("theme") or None, "incidents_scope": u.get("incidents_scope") or "own"}


class ThemeIn(BaseModel):
    mode: str = "dark"          # dark | light | system
    accent: str = "#2F6FCB"     # cor de destaque
    tone: str = "grafite"       # tom dos neutros
    scale: float = 1.0          # tamanho do texto


@api.put("/auth/me/theme")
async def set_my_theme(body: ThemeIn, user: dict = Depends(get_current_user)):
    """Aparência escolhida pelo usuário (vale em qualquer navegador em que ele entrar)."""
    if body.mode not in ("dark", "light", "system"):
        raise HTTPException(status_code=400, detail="Modo inválido")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", body.accent or ""):
        raise HTTPException(status_code=400, detail="Cor inválida")
    if body.tone not in ("grafite", "azul", "neutro", "quente", "roxo"):
        raise HTTPException(status_code=400, detail="Tom inválido")
    theme = {"mode": body.mode, "accent": body.accent.upper(), "tone": body.tone, "scale": round(min(1.3, max(0.8, body.scale)), 2)}
    await db.users.update_one({"id": user["id"]}, {"$set": {"theme": theme}})
    return theme


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
    if payload.login_mode is not None:
        if payload.login_mode not in ssomod.LOGIN_MODES:
            raise HTTPException(status_code=400, detail="Modo de login inválido")
        if user_id == current["id"] and payload.login_mode == "sso":
            raise HTTPException(status_code=400, detail="Você não pode deixar a sua própria conta só com login único (risco de ficar trancado fora)")
        update["login_mode"] = payload.login_mode
    if payload.incidents_scope is not None:
        if payload.incidents_scope not in ("own", "all"):
            raise HTTPException(status_code=400, detail="Escopo de incidentes inválido")
        update["incidents_scope"] = payload.incidents_scope
    if payload.incident_nets is not None:
        try:
            update["incident_nets"] = incmod.clean_nets(payload.incident_nets)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if update or unset:
        await db.users.update_one({"id": user_id}, {**({"$set": update} if update else {}), **({"$unset": unset} if unset else {})})
    if payload.incident_nets is not None:
        asyncio.create_task(_inc_reown())          # redes mudaram: refaz os donos dos incidentes ativos
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


# Coleções com dados pessoais do usuário (por user_id) que somem junto com ele; logs (sessions, login_events) ficam.
_USER_PERSONAL = ("push_subs", "ai_web", "ai_pending")
_ITEM_LABELS = {"agents": "agentes/jump servers", "devices": "equipamentos", "maps": "mapas", "dashboards": "dashboards",
                "scripts": "scripts", "runbooks": "roteiros", "rdp_hosts": "acessos RDP", "web_saved": "páginas web salvas",
                "te_runs": "análises de tráfego", "vpn_profiles": "VPNs", "flow_groups": "grupos de flow",
                "flow_ifaces": "interfaces de flow", "net_reports": "relatórios", "if_monitors": "monitores de interface"}


async def _owned_counts(owner_ids) -> Dict[str, Dict[str, int]]:
    """{owner_id: {coleção: quantidade}} de tudo que tem owner_id, em qualquer coleção do banco."""
    out: Dict[str, Dict[str, int]] = {}
    for col in sorted(await db.list_collection_names()):
        if col in ("users", "sessions", "login_events") or col.startswith("system."):
            continue
        q = {"owner_id": {"$in": list(owner_ids)}} if owner_ids is not None else {"owner_id": {"$exists": True, "$nin": [None, ""]}}
        async for r in db[col].aggregate([{"$match": q}, {"$group": {"_id": "$owner_id", "n": {"$sum": 1}}}]):
            out.setdefault(str(r["_id"]), {})[col] = r["n"]
    return out


async def _owner_cleanup(owner_id: str, action: str, to_user: Optional[dict]) -> Dict[str, int]:
    """Transfere (action=transfer) ou apaga (action=delete) tudo que é de owner_id. Devolve {coleção: quantidade}."""
    done: Dict[str, int] = {}
    counts = (await _owned_counts([owner_id])).get(owner_id, {})
    for col in counts:
        if action == "transfer":
            sets = {"owner_id": to_user["id"]}
            if col == "runbooks":
                sets["owner_email"] = to_user.get("email", "")
            r = await db[col].update_many({"owner_id": owner_id}, {"$set": sets})
            done[col] = r.modified_count
        else:
            r = await db[col].delete_many({"owner_id": owner_id})
            done[col] = r.deleted_count
    if action == "delete":
        # equipamentos de outros usuários que passavam por um agente apagado ficam sem agente (acesso direto)
        live = [None, ""] + [a["id"] for a in await db.agents.find({}, {"_id": 0, "id": 1}).to_list(10000)]
        await db.devices.update_many({"agent_id": {"$exists": True, "$nin": live}}, {"$set": {"agent_id": None}})
        await db.agents.update_many({"parent_agent_id": {"$exists": True, "$nin": live}}, {"$set": {"parent_agent_id": None}})
    return done


def _items_text(counts: Dict[str, int]) -> str:
    parts = [f"{n} {_ITEM_LABELS.get(c, c)}" for c, n in counts.items() if n]
    return ", ".join(parts) or "nada"


@api.get("/users/{user_id}/items")
async def user_items(user_id: str, _: dict = Depends(require_admin)):
    """O que o usuário tem (para o diálogo de exclusão escolher entre transferir e apagar)."""
    counts = (await _owned_counts([user_id])).get(user_id, {})
    return {"counts": counts, "text": _items_text(counts), "labels": _ITEM_LABELS}


@api.delete("/users/{user_id}")
async def delete_user(user_id: str, items: str = Query("transfer"), current: dict = Depends(require_admin)):
    """Exclui o usuário e resolve o que era dele: items=transfer passa tudo para quem está excluindo (agentes, equipamentos,
    mapas…); items=delete apaga. Sem isso os agentes dele continuavam com o túnel reverso aceito e aparecendo online."""
    if user_id == current["id"]:
        raise HTTPException(status_code=400, detail="Não é possível excluir a si mesmo")
    if items not in ("transfer", "delete"):
        raise HTTPException(status_code=400, detail="items deve ser transfer ou delete")
    u = await db.users.find_one({"id": user_id}, {"_id": 0, "email": 1})
    res = await db.users.delete_one({"id": user_id})
    if not res.deleted_count:
        return {"deleted": 0}
    done = await _owner_cleanup(user_id, items, current)
    for col in _USER_PERSONAL:
        await db[col].delete_many({"user_id": user_id})
    await db.config.update_one({"key": "ai"}, {"$pull": {"ai_users": {"user_id": user_id}}})
    logger.info(f"usuário {(u or {}).get('email')} excluído por {current.get('email')}: {items} {done}")
    return {"deleted": 1, "items": items, "done": done, "text": _items_text(done)}


@api.get("/users-orphans")
async def users_orphans(_: dict = Depends(require_admin)):
    """Itens cujo dono não existe mais (usuários excluídos antes de o BastiON resolver os itens na exclusão)."""
    alive = {u["id"] for u in await db.users.find({}, {"_id": 0, "id": 1}).to_list(10000)}
    allc = await _owned_counts(None)
    out = []
    for oid, counts in allc.items():
        if oid in alive:
            continue
        ags = await db.agents.find({"owner_id": oid}, {"_id": 0, "name": 1, "status": 1}).to_list(50)
        hint = next((d.get("owner_email") for d in await db.runbooks.find({"owner_id": oid}, {"_id": 0, "owner_email": 1}).to_list(1)), "")
        if not hint:
            s = await db.sessions.find_one({"user_id": oid, "user_email": {"$ne": None}}, {"_id": 0, "user_email": 1}, sort=[("started_at", -1)])
            hint = (s or {}).get("user_email") or ""
        out.append({"owner_id": oid, "email": hint, "counts": counts, "text": _items_text(counts),
                    "agents": [{"name": a.get("name"), "status": a.get("status")} for a in ags]})
    return out


class OrphanResolve(BaseModel):
    owner_id: str
    action: str   # transfer | delete


@api.post("/users-orphans/resolve")
async def users_orphans_resolve(body: OrphanResolve, current: dict = Depends(require_admin)):
    if body.action not in ("transfer", "delete"):
        raise HTTPException(status_code=400, detail="Ação inválida")
    if await db.users.find_one({"id": body.owner_id}, {"_id": 1}):
        raise HTTPException(status_code=400, detail="Esse usuário ainda existe — use a tela de usuários")
    done = await _owner_cleanup(body.owner_id, body.action, current)
    for col in _USER_PERSONAL:
        await db[col].delete_many({"user_id": body.owner_id})
    await db.config.update_one({"key": "ai"}, {"$pull": {"ai_users": {"user_id": body.owner_id}}})
    return {"ok": True, "done": done, "text": _items_text(done)}


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
    alive = {u["id"] for u in await db.users.find({}, {"_id": 0, "id": 1}).to_list(10000)}
    # agente de usuário excluído não tem mais túnel aceito (agentes antigos, sem dono gravado, continuam valendo)
    agents = [a for a in agents if not a.get("owner_id") or a.get("owner_id") in alive]
    lines, seen = [], set()
    for a in sorted(agents, key=lambda x: bool(x.get("copied_from"))):   # original antes das cópias
        k = (a["agent_public_key"].strip(), a.get("tunnel_port"))
        if k in seen:
            continue
        seen.add(k)
        opts = f'restrict,port-forwarding,permitlisten="{TUNNEL_BIND_HOST}:{a.get("tunnel_port")}"'
        lines.append(f'{opts} {a["agent_public_key"].strip()} bastion-agent-{a["id"]}')
    return PlainTextResponse("\n".join(lines) + "\n")


# Sincroniza as chaves dos túneis reversos (cron, a cada minuto, como root no servidor do BastiON). Além de gravar o
# authorized_keys, derruba o túnel que continua conectado com uma chave que saiu da lista (agente apagado ou de usuário
# excluído): o sshd só confere a chave no login, então sem isso o túnel antigo ficava de pé até cair sozinho.
_SYNC_KEYS_SCRIPT = r"""cat > /usr/local/bin/bastion-sync-keys.sh <<'EOS'
#!/usr/bin/env bash
. /etc/bastion-sync.conf
TMP=$(mktemp)
if curl -fsS "$API_URL/api/bastion/authorized-keys?token=$SYNC_TOKEN" -o "$TMP"; then
  install -m 600 -o "$BASTION_USER" -g "$BASTION_USER" "$TMP" "/home/$BASTION_USER/.ssh/authorized_keys"
  ALLOWED=" $(grep -o 'permitlisten="[^"]*"' "$TMP" | grep -o '[0-9]*"$' | tr -d '"' | tr '\n' ' ') "
  ss -Htlnp 2>/dev/null | while read -r _ _ _ LOCAL _ PROC; do
    PORT="${LOCAL##*:}"
    for PID in $(echo "$PROC" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
      [ "$(ps -o user= -p "$PID" 2>/dev/null | tr -d ' ')" = "$BASTION_USER" ] || continue
      case "$ALLOWED" in
        *" $PORT "*) ;;
        *) kill "$PID" 2>/dev/null && logger -t bastion-sync "túnel reverso da porta $PORT encerrado (agente removido)" ;;
      esac
    done
  done
fi
rm -f "$TMP"
EOS
"""


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

cat > /etc/bastion-sync.conf <<EOF
API_URL="$API_URL"
SYNC_TOKEN="$SYNC_TOKEN"
BASTION_USER="$BASTION_USER"
EOF
chmod 600 /etc/bastion-sync.conf
""" + _SYNC_KEYS_SCRIPT + f"""chmod +x /usr/local/bin/bastion-sync-keys.sh
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
               password=pw or None, legacy=False, label=f"agente {ag['name']}", shared=True)


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
        w = SSHClientWrapper([_agent_hop(x, priv) for x in chain], pool=False)   # teste = login de verdade
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
    if (data.get("snmp_community") or "") != (existing.get("snmp_community") or "") or \
            int(data.get("snmp_port") or 161) != int(existing.get("snmp_port") or 161):
        data["snmp_status"] = "none" if not (data.get("snmp_community") or "").strip() else None   # testar de novo
    await db.devices.update_one({"id": device_id}, {"$set": data})
    return _public(await db.devices.find_one({"id": device_id}, {"_id": 0}))


async def _prune_copied_agents(owner_id: str) -> int:
    """Apaga os jumps que este usuário RECEBEU (copiados no Enviar) e que não servem mais a nenhum equipamento ou
    conexão de Área de Trabalho dele. Os jumps que o próprio usuário criou nunca são apagados."""
    agents = {a["id"]: a async for a in db.agents.find({"owner_id": owner_id}, {"_id": 0, "id": 1, "parent_agent_id": 1, "copied_from": 1})}
    copies = [aid for aid, a in agents.items() if a.get("copied_from")]
    if not copies:
        return 0
    used = {d["agent_id"] async for d in db.devices.find({"owner_id": owner_id, "agent_id": {"$nin": [None, ""]}}, {"_id": 0, "agent_id": 1})}
    used |= {h["agent_id"] async for h in db.rdp_hosts.find({"owner_id": owner_id, "agent_id": {"$nin": [None, ""]}}, {"_id": 0, "agent_id": 1})}
    keep = set()
    for aid in used:                                       # o jump usado e toda a cadeia de saltos acima dele
        seen = 0
        while aid and aid in agents and aid not in keep and seen < 10:
            keep.add(aid)
            aid = agents[aid].get("parent_agent_id")
            seen += 1
    drop = [aid for aid in copies if aid not in keep]
    if drop:
        await db.agents.delete_many({"id": {"$in": drop}, "owner_id": owner_id})
        await db.agents.update_many({"owner_id": owner_id, "parent_agent_id": {"$in": drop}}, {"$set": {"parent_agent_id": None}})
        logger.info(f"jumps recebidos sem uso removidos ({owner_id}): {len(drop)}")
    return len(drop)


async def _prune_all_copied_agents_once():
    """Uma vez: limpa os jumps recebidos que já tinham ficado sobrando antes desta versão."""
    if await db.config.find_one({"key": "agents_pruned_v1"}):
        return
    total = 0
    for uid in await db.agents.distinct("owner_id", {"copied_from": {"$nin": [None, ""]}}):
        total += await _prune_copied_agents(uid)
    await db.config.update_one({"key": "agents_pruned_v1"}, {"$set": {"at": datetime.now(timezone.utc).isoformat(), "removed": total}}, upsert=True)


@api.post("/agents/prune")
async def prune_agents(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return {"removed": await _prune_copied_agents(user["id"])}


@api.delete("/devices/{device_id}")
async def delete_device(device_id: str, user: dict = Depends(get_current_user)):
    r = await db.devices.delete_one({"id": device_id, **_scope(user)})
    if r.deleted_count:
        await _prune_copied_agents(user["id"])
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
    if r.deleted_count:
        await _prune_copied_agents(user["id"])
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
    if payload.snmp_community is not None:
        com = payload.snmp_community.strip()
        if len(com) > 64 or re.search(r"[\s\"'\\]", com):
            raise HTTPException(status_code=400, detail="Community SNMP inválida (até 64 caracteres, sem espaço nem aspas)")
        setf["snmp_community"] = com
        setf["snmp_status"] = "none" if not com else None      # community nova: precisa testar de novo
    if payload.snmp_port is not None:
        if not 1 <= payload.snmp_port <= 65535:
            raise HTTPException(status_code=400, detail="Porta SNMP inválida")
        setf["snmp_port"] = payload.snmp_port
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
async def list_sessions(user: dict = Depends(get_current_user), limit: int = 100, all: bool = False, kind: str = "",
                        q: str = "", recorded: bool = False, user_id: str = ""):
    """Histórico. Administrador pode ver de todos (all=1) para auditoria; os demais veem as próprias sessões."""
    f: dict = {} if (all and user.get("role") == "admin") else {"user_id": user["id"]}
    if user_id and user.get("role") == "admin":
        f["user_id"] = user_id
    if kind:
        f["kind"] = kind
    if recorded:
        f["recording.file"] = {"$exists": True}
    if q.strip():
        rx = {"$regex": re.escape(q.strip()[:80]), "$options": "i"}
        f["$or"] = [{"device_name": rx}, {"user_email": rx}]
    rows = await db.sessions.find(f, {"_id": 0}).sort("started_at", -1).to_list(min(1000, max(1, limit)))
    for r in rows:
        if isinstance(r.get("commands"), list):
            r["commands_count"] = len(r["commands"])
    return rows


async def _rec_settings() -> dict:
    doc = await db.config.find_one({"key": "recording"}, {"_id": 0}) or {}
    return {**recording.DEFAULTS, **{k: v for k, v in doc.items() if k != "key"}}


async def _session_for(user: dict, sid: str) -> dict:
    s = await db.sessions.find_one({"id": sid}, {"_id": 0})
    if not s or (s.get("user_id") != user["id"] and user.get("role") != "admin"):
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return s


@api.get("/sessions/{sid}/recording")
async def session_recording(sid: str, user: dict = Depends(get_current_user)):
    """Gravação da sessão (asciicast v2) para o replay. Dono da sessão ou administrador."""
    s = await _session_for(user, sid)
    rec = s.get("recording") or {}
    text = recording.read_text(rec.get("file", "")) if rec.get("file") else None
    if text is None:
        raise HTTPException(status_code=404, detail="Esta sessão não tem gravação (ou ela já foi apagada pela retenção)")
    await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"],
                                  "device_id": s.get("device_id"), "device_name": s.get("device_name"), "kind": "replay",
                                  "started_at": datetime.now(timezone.utc).isoformat(), "ended_at": datetime.now(timezone.utc).isoformat(),
                                  "duration_seconds": 0, "replay_of": sid, "replay_user": s.get("user_email")})
    return PlainTextResponse(text, media_type="application/x-asciicast")


@api.get("/sessions/{sid}")
async def session_get(sid: str, user: dict = Depends(get_current_user)):
    return await _session_for(user, sid)


class RecSettingsIn(BaseModel):
    enabled: bool = True
    input: bool = False
    retention_days: int = 180
    max_mb: int = 50


@api.get("/recording-settings")
async def rec_settings_get(_: dict = Depends(require_admin)):
    return {**await _rec_settings(), "usage": recording.disk_usage()}


@api.put("/recording-settings")
async def rec_settings_put(body: RecSettingsIn, _: dict = Depends(require_admin)):
    d = {"enabled": body.enabled, "input": body.input, "retention_days": min(3650, max(1, body.retention_days)),
         "max_mb": min(1000, max(1, body.max_mb))}
    await db.config.update_one({"key": "recording"}, {"$set": d}, upsert=True)
    return {**d, "usage": recording.disk_usage()}


async def _rec_retention_loop():
    await asyncio.sleep(90)
    try:
        recording.finalize_orphans()
    except Exception:
        pass
    while True:
        try:
            cfg = await _rec_settings()
            n = await asyncio.get_running_loop().run_in_executor(None, recording.purge, int(cfg["retention_days"]))
            if n:
                logger.info(f"gravações: {n} arquivo(s) com mais de {cfg['retention_days']} dias apagados")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"retenção das gravações: {e}")
        await asyncio.sleep(6 * 3600)


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
        d = await cmd_policy(user, dev, cmdpolicy.command_lines(command), "batch")
        if d["action"] != "allow":
            return BatchResultItem(device_id=dev["id"], device_name=dev["name"], host=dev["host"], ok=False, exit_status=-1,
                                   stdout="", stderr="", error=d["message"]).model_dump()
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


# ---------- Gerador de script de OLT (ZTE TITAN) ----------
@api.get("/oltgen/models")
async def oltgen_models(_: dict = Depends(get_current_user)):
    return {"modes": oltgen.ONU_MODES, "platforms": oltgen.PLATFORMS, "models": [{"key": k, **v} for k, v in oltgen.MODELS.items()],
            "onu_types": [{"name": k, "eth": e, "pots": p, "wifi": bool(w)} for k, (e, p, w) in oltgen.ONU_TYPES.items()]}


@api.post("/oltgen/generate")
async def oltgen_generate(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    try:
        return oltgen.generate(body or {})
    except (oltgen.GenError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e) or "Parâmetros inválidos")


class OltCardsIn(BaseModel):
    device_id: str


@api.post("/oltgen/read-cards")
async def oltgen_read_cards(body: OltCardsIn, user: dict = Depends(get_current_user)):
    """Lê o `show card` de uma OLT cadastrada para montar as placas do formulário (só leitura)."""
    _no_viewer(user)
    dev = await _get_device_for(user, body.device_id)
    try:
        cli = await _connect_device(dev)
        try:
            res = await cli.run_command("show card", timeout=60)
        finally:
            await cli.close()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui ler a OLT: {e}"[:300])
    text = res.get("stdout") or ""
    if not isinstance(text, str):
        text = text.decode("utf-8", "replace")
    cards = oltgen.parse_show_card(text)
    if not cards["pon"] and not cards["uplinks"]:
        raise HTTPException(status_code=422, detail="A saída do show card não trouxe placas reconhecíveis — preencha os slots à mão.")
    await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"],
                                  "device_id": dev["id"], "device_name": dev["name"], "kind": "batch",
                                  "started_at": datetime.now(timezone.utc).isoformat(),
                                  "ended_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": 0})
    return {**cards, "device": dev["name"], "raw": text[-6000:]}


@api.post("/oltgen/onu")
async def oltgen_onu(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    try:
        return oltgen.onu_script(body or {})
    except (oltgen.GenError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e) or "Parâmetros inválidos")


def _out_text(res: dict) -> str:
    t = res.get("stdout") or ""
    return t if isinstance(t, str) else t.decode("utf-8", "replace")


@api.post("/oltgen/uncfg")
async def oltgen_uncfg(body: OltCardsIn, user: dict = Depends(get_current_user)):
    """ONUs pedindo autorização numa OLT cadastrada + o próximo ID livre de cada PON (só leitura)."""
    _no_viewer(user)
    dev = await _get_device_for(user, body.device_id)
    try:
        cli = await _connect_device(dev)
        try:
            text = _out_text(await cli.run_command("show gpon onu uncfg", timeout=60))
            if not oltgen.parse_uncfg(text) and re.search(r"invalid|error|%", text, re.I):
                text = _out_text(await cli.run_command("show pon onu uncfg", timeout=60))
            onus = oltgen.parse_uncfg(text)
            plat = "c300" if re.search(r"gpon-(onu|olt)_", text) else "titan"
            free: dict = {}
            for pon in list(dict.fromkeys(o["pon"] for o in onus))[:16]:
                sl, pt = (int(x) for x in pon.split("/")[1:3])
                cfg = _out_text(await cli.run_command(f"show running-config interface {oltgen.pon_if(plat, sl, pt)}", timeout=60))
                used = set(oltgen.used_onu_ids(cfg))
                free[pon] = [i for i in range(1, 129) if i not in used]
        finally:
            await cli.close()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui ler a OLT: {e}"[:300])
    await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"],
                                  "device_id": dev["id"], "device_name": dev["name"], "kind": "batch",
                                  "started_at": datetime.now(timezone.utc).isoformat(),
                                  "ended_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": 0})
    # cada ONU da mesma PON recebe um ID livre diferente, na ordem
    out = []
    for o in onus:
        ids = free.get(o["pon"]) or []
        out.append({**o, "free_id": ids.pop(0) if ids else None})
    return {"device": dev["name"], "platform": plat, "onus": out, "raw": text[-4000:]}


@api.post("/oltgen/onu-remove")
async def oltgen_onu_remove(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    try:
        return oltgen.onu_remove_script(body or {})
    except (oltgen.GenError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e) or "Parâmetros inválidos")


class OnuInfoIn(BaseModel):
    device_id: str
    platform: str = "titan"
    items: List[dict] = []


@api.post("/oltgen/onu-info")
async def oltgen_onu_info(body: OnuInfoIn, user: dict = Depends(get_current_user)):
    """Confere na OLT quem é cada ONU antes de desautorizar (tipo, serial, nome). Só leitura."""
    _no_viewer(user)
    if body.platform not in oltgen.PLATFORMS:
        raise HTTPException(status_code=400, detail="Série da OLT inválida")
    try:
        items = [(int(i["slot"]), int(i["pon"]), int(i["onu"])) for i in body.items[:64]]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Informe slot, PON e ONU em números")
    if not items:
        raise HTTPException(status_code=400, detail="Informe ao menos uma ONU")
    dev = await _get_device_for(user, body.device_id)
    out = []
    try:
        cli = await _connect_device(dev)
        try:
            pon_cfg: dict = {}
            for sl, pn, on in items:
                if (sl, pn) not in pon_cfg:
                    pon_cfg[(sl, pn)] = _out_text(await cli.run_command(
                        f"show running-config interface {oltgen.pon_if(body.platform, sl, pn)}", timeout=60))
                info = None
                if oltgen.find_onu(pon_cfg[(sl, pn)], "", on):
                    onu_cfg = _out_text(await cli.run_command(
                        f"show running-config interface {oltgen.onu_if_name(body.platform, sl, pn, on)}", timeout=60))
                    info = oltgen.find_onu(pon_cfg[(sl, pn)], onu_cfg, on)
                out.append({"slot": sl, "pon": pn, "onu": on, "found": bool(info), **(info or {})})
        finally:
            await cli.close()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui ler a OLT: {e}"[:300])
    await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"],
                                  "device_id": dev["id"], "device_name": dev["name"], "kind": "batch",
                                  "started_at": datetime.now(timezone.utc).isoformat(),
                                  "ended_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": 0})
    return {"device": dev["name"], "items": out}


@api.post("/oltgen/onu-add")
async def oltgen_onu_add(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    try:
        return oltgen.onu_add_script(body or {})
    except (oltgen.GenError, TypeError, ValueError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=str(e) or "Parâmetros inválidos")


class OnuReadIn(BaseModel):
    device_id: str
    platform: str = "titan"
    pon: str
    onu_id: int


@api.post("/oltgen/onu-read")
async def oltgen_onu_read(body: OnuReadIn, user: dict = Depends(get_current_user)):
    """Lê a configuração atual de uma ONU (VLANs, gemports, portas) para acrescentar VLAN sem conflito. Só leitura."""
    _no_viewer(user)
    if body.platform not in oltgen.PLATFORMS:
        raise HTTPException(status_code=400, detail="Série da OLT inválida")
    try:
        sl, pt = oltgen._pon(body.pon)
    except oltgen.GenError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not 1 <= body.onu_id <= 128:
        raise HTTPException(status_code=400, detail="ID da ONU: 1 a 128")
    dev = await _get_device_for(user, body.device_id)
    onu_if = oltgen.onu_if_name(body.platform, sl, pt, body.onu_id)
    try:
        cli = await _connect_device(dev)
        try:
            pon_cfg = _out_text(await cli.run_command(f"show running-config interface {oltgen.pon_if(body.platform, sl, pt)}", timeout=60))
            onu_cfg = _out_text(await cli.run_command(f"show running-config interface {onu_if}", timeout=60))
            info = oltgen.find_onu(pon_cfg, onu_cfg, body.onu_id)
            if not info:
                raise HTTPException(status_code=404, detail=f"A ONU {onu_if} não existe nessa OLT")
            mng = ""
            for cmd in oltgen.MNG_COMMANDS:
                mng = _out_text(await cli.run_command(cmd.format(onu=onu_if), timeout=60))
                if re.search(r"^\s*(service|vlan port)\s", mng, re.M) or not re.search(r"invalid|unrecognized|error|%", mng, re.I):
                    break
            vport_cfg = ""
            if body.platform == "titan":
                gems = oltgen.parse_onu_existing(onu_cfg, "")["gemports"] or [1]
                for k in gems[:8]:
                    vport_cfg += _out_text(await cli.run_command(
                        f"show running-config interface vport-1/{sl}/{pt}.{body.onu_id}:{k}", timeout=60)) + "\n"
        finally:
            await cli.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui ler a OLT: {e}"[:300])
    ex = oltgen.parse_onu_existing(onu_cfg, mng, vport_cfg)
    await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"],
                                  "device_id": dev["id"], "device_name": dev["name"], "kind": "batch",
                                  "started_at": datetime.now(timezone.utc).isoformat(),
                                  "ended_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": 0})
    return {"device": dev["name"], "interface": onu_if, **info, "existing": ex}


# ---------- aplicar direto na OLT ----------
_VENDOR_NAME = {"zte": "ZTE", "huawei": "Huawei", "fiberhome": "FiberHome"}
_ENABLE_PW = re.compile(r"pass(word)?\s*:\s*$", re.I)


def _enable_password(dev: dict) -> str:
    """FiberHome pede senha no enable: usa a de enable cadastrada (se houver) ou a mesma do login."""
    return vault.decrypt(dev.get("enable_password", "")) or vault.decrypt(dev.get("password", "")) or ""


_OLT_ERR = re.compile(r"%\s*(error|invalid|unrecognized|incomplete|ambiguous|unknown|code|parameter|too\s+many)|^\s*error\b|\berror\s*[:\[]|"
                      r"^\s*failure\b|\bfailure\s*:|\bunknown\s+command\b|\bcommand\s+not\s+found\b|\binvalid\s+(?:input|parameter|command)\b|"
                      r"not\s+exist|does\s+not\s+exist|already\s+exist|failed|is\s+in\s+use|out\s+of\s+range", re.I | re.M)
_OLT_CONFIRM = re.compile(r"(yes/no|\[y/n\]|\(y/n\)|y/n)\s*\]?\s*(\[[yn]\])?\s*:?\s*$", re.I)
_olt_apply_locks: dict = {}


class OltApplyIn(BaseModel):
    device_id: str
    kind: str                    # onu | onu-add | onu-remove
    params: dict = {}


async def _olt_run_script(dev: dict, script: str, timeout_for=None) -> dict:
    """Manda o script linha a linha numa sessão só. Para no primeiro erro (sai do modo de configuração e não grava);
    responde 'yes' quando a OLT pede confirmação (ex.: write)."""
    steps, failed = [], None
    hw = (dev.get("device_type") or "") == "huawei"
    cli = await _connect_device(dev)
    try:
        sess = await cli.shell()
        try:
            for line in script.splitlines():
                cmd = line.rstrip()
                if not cmd.strip() or cmd.lstrip().startswith(("!", "#")):
                    continue
                out = await sess.run(cmd, timeout=240 if cmd.strip() in ("save", "write") else 90)
                if cmd.strip() == "enable" and _ENABLE_PW.search(out.strip()[-40:]):
                    out += await sess.run(_enable_password(dev), timeout=30)     # senha não vai para o histórico
                if _OLT_CONFIRM.search(out.strip()[-60:]):
                    out += await sess.run("y" if hw else "yes", timeout=120)
                # a própria linha ecoada não conta como erro
                body = out.replace(cmd.strip(), "", 1)
                if _OLT_ERR.search(body):
                    failed = {"command": cmd.strip(), "output": out.strip()[-1500:]}
                    steps.append({"command": cmd.strip(), "ok": False})
                    try:
                        await sess.run("return" if hw else "end", timeout=20)
                    except Exception:
                        pass
                    break
                steps.append({"command": cmd.strip(), "ok": True})
        finally:
            try:
                await sess.close()
            except Exception:
                pass
    finally:
        await cli.close()
    return {"ok": failed is None, "steps": steps, "failed": failed}


@api.post("/oltgen/apply")
async def oltgen_apply(body: OltApplyIn, user: dict = Depends(get_current_user)):
    """Gera o script no servidor (a partir dos mesmos parâmetros da tela) e aplica direto na OLT, gravando no final."""
    _no_viewer(user)
    gen = {"onu": oltgen.onu_script, "onu-add": oltgen.onu_add_script, "onu-remove": oltgen.onu_remove_script,
           "hw-onu": olthw.onu_script, "hw-onu-remove": olthw.onu_remove_script,
           "fh-onu": oltfh.onu_script, "fh-onu-remove": oltfh.onu_remove_script}.get(body.kind)
    if not gen:
        raise HTTPException(status_code=400, detail="Tipo de script não pode ser aplicado direto")
    try:
        res = gen({**(body.params or {}), "write": True, "comments": False})
    except (oltgen.GenError, TypeError, ValueError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=str(e) or "Parâmetros inválidos")
    dev = await _get_device_for(user, body.device_id)
    want = "huawei" if body.kind.startswith("hw-") else "fiberhome" if body.kind.startswith("fh-") else "zte"
    if (dev.get("device_type") or "") != want:
        raise HTTPException(status_code=400, detail=f"Esse equipamento não está cadastrado como {_VENDOR_NAME[want]}")
    await _cmdpol_guard(user, dev, cmdpolicy.command_lines(res["script"]), "olt")
    lock = _olt_apply_locks.setdefault(dev["id"], asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="Já tem um script sendo aplicado nessa OLT; espere terminar")
    started = datetime.now(timezone.utc)
    async with lock:
        try:
            r = await _olt_run_script(dev, res["script"])
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Não consegui aplicar na OLT: {e}"[:300])
    await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"],
                                  "device_id": dev["id"], "device_name": dev["name"], "kind": "batch",
                                  "started_at": started.isoformat(), "ended_at": datetime.now(timezone.utc).isoformat(),
                                  "duration_seconds": int((datetime.now(timezone.utc) - started).total_seconds()),
                                  "commands": [s["command"] for s in r["steps"]], "oltgen": body.kind, "ok": r["ok"]})
    logger.info("Script de OLT (%s) aplicado por %s em %s: %s", body.kind, user.get("email"), dev["name"], "ok" if r["ok"] else "ERRO")
    return {**r, "device": dev["name"], "saved": r["ok"] and any(s["command"] in ("write", "save") for s in r["steps"]),
            "total": len([l for l in res["script"].splitlines() if l.strip() and not l.lstrip().startswith(("!", "#"))])}


# ---------- Limpeza de ONUs off há mais de N dias ----------
OLT_CLEAN_JOBS: Dict[str, dict] = {}       # job_id -> estado (varredura ou remoção), só do dono
OLT_CLEAN_MAX = 2000                        # ONUs off detalhadas por varredura


def _clean_job(user: dict, kind: str, dev: dict) -> dict:
    for k, j in list(OLT_CLEAN_JOBS.items()):              # descarta jobs com mais de 6 h
        if time.time() - j["_t"] > 6 * 3600:
            OLT_CLEAN_JOBS.pop(k, None)
    j = {"id": os.urandom(8).hex(), "kind": kind, "state": "running", "device_id": dev["id"], "device": dev["name"],
         "owner": user["id"], "step": "conectando", "done": 0, "total": 0, "_t": time.time(),
         "started_at": datetime.now(timezone.utc).isoformat()}
    OLT_CLEAN_JOBS[j["id"]] = j
    return j


def _job_out(j: dict) -> dict:
    return {k: v for k, v in j.items() if not k.startswith("_") and k != "owner"}


class _OltSess:
    """Sessão que lembra o último comando e a saída (para mostrar no erro o que a OLT respondeu)."""
    def __init__(self, sess, j):
        self.sess, self.j = sess, j

    async def run(self, cmd, timeout=60):
        out = await self.sess.run(cmd, timeout=timeout)
        self.j["_last"] = {"command": cmd, "output": (out or "")[-1500:]}
        return out


def _olt_err(out: str, cmd: str) -> bool:
    return bool(_OLT_ERR.search((out or "").replace(cmd, "", 1)))


async def _olt_detect_plat(sess, slot: int, port: int) -> str:
    """C300 usa gpon-olt_1/S/P; TITAN (C600/C620/C650) usa gpon_olt-1/S/P. Testa os dois."""
    for plat in ("c300", "titan"):
        cmd = f"show gpon onu state {oltgen.pon_if(plat, slot, port)}"
        out = await sess.run(cmd, timeout=60)
        if not _olt_err(out, cmd) and (oltgen.parse_onu_state(out) or "OnuIndex" in out or "No related" in out or not out.strip()):
            return plat
    raise RuntimeError(f"A OLT não aceitou 'show gpon onu state' nem no formato do C300 nem no do TITAN (PON {slot}/{port})")


async def _olt_offline_scan(j: dict, dev: dict, days: int, only_pons: List[str]):
    try:
        cli = await _connect_device(dev)
        try:
            raw = await cli.shell()
            sess = _OltSess(raw, j)
            try:
                j["step"] = "lendo placas e relógio da OLT"
                clock = oltgen.parse_olt_clock(await sess.run("show clock", timeout=30))
                want = {tuple(int(x) for x in p.split("/")) for p in only_pons} if only_pons else None
                # 1º: todas as ONUs de uma vez (TITAN e versões novas do C300 aceitam sem a interface)
                all_out = await sess.run("show gpon onu state", timeout=180)
                rows = [] if _olt_err(all_out, "show gpon onu state") else oltgen.parse_onu_state(all_out)
                if rows:
                    pons = sorted({(r["slot"], r["pon"]) for r in rows})
                else:
                    card_out = await sess.run("show card", timeout=60)
                    cards = oltgen.parse_show_card(card_out)
                    pons = [(c["slot"], p) for c in cards["pon"] for p in range(1, c["ports"] + 1)]
                    if not pons:
                        raise RuntimeError("Não achei placas PON no 'show card' nem ONUs no 'show gpon onu state'")
                if want:
                    pons = [x for x in pons if x in want]
                    rows = [r for r in rows if (r["slot"], r["pon"]) in want]
                if not pons:
                    raise RuntimeError("Nenhuma das PONs informadas existe nessa OLT")
                plat = await _olt_detect_plat(sess, *pons[0])
                j.update({"platform": plat, "olt_clock": clock.strftime("%Y-%m-%d %H:%M:%S") if clock else None,
                          "pons": len(pons), "total": len(pons)})
                if not rows:
                    for i, (sl, pt) in enumerate(pons, 1):
                        j.update(step=f"estado das ONUs da PON {sl}/{pt}", done=i)
                        rows += oltgen.parse_onu_state(await sess.run(f"show gpon onu state {oltgen.pon_if(plat, sl, pt)}", timeout=90))
                total_onus = len(rows)
                offs = [o for o in rows if o["off"] or o["phase"].lower() not in ("working", "online")][:OLT_CLEAN_MAX]
                j.update(total=len(offs), done=0, onus_total=total_onus)
                now = clock or datetime.now()
                res = []
                for i, o in enumerate(offs, 1):
                    j.update(step=f"detalhes {o['slot']}/{o['pon']}:{o['onu']}", done=i)
                    d = oltgen.parse_onu_detail(await sess.run(
                        f"show gpon onu detail-info {oltgen.onu_if_name(plat, o['slot'], o['pon'], o['onu'])}", timeout=60))
                    last = datetime.strptime(d["last_down"], "%Y-%m-%d %H:%M:%S") if d["last_down"] else None
                    off_days = round((now - last).total_seconds() / 86400, 1) if last else None
                    res.append({**o, **{k: d[k] for k in ("name", "type", "sn", "description", "last_down", "cause")},
                                "phase": d["phase"] or o["phase"], "days": off_days,
                                "eligible": bool(o["off"] and off_days is not None and off_days >= days)})
                res.sort(key=lambda r: (-(r["days"] if r["days"] is not None else -1), r["slot"], r["pon"], r["onu"]))
                # amostra do detalhe da 1ª ONU off, para conferir a leitura se as datas vierem vazias
                j.update(state="done", step="", onus=res, days=days,
                         eligible=sum(1 for r in res if r["eligible"]), ended_at=datetime.now(timezone.utc).isoformat(),
                         sample=(j.get("_last") or {}) if res and all(r["days"] is None for r in res) else None)
            finally:
                try:
                    await raw.close()
                except Exception:
                    pass
        finally:
            await cli.close()
    except Exception as e:
        j.update(state="error", error=f"{e}"[:300] or type(e).__name__, diag=j.get("_last"))


class OltCleanScanIn(BaseModel):
    device_id: str
    days: int = 15
    pons: List[str] = []         # opcional: só estas PONs ("2/1", "2/2"…)


@api.post("/oltgen/offline-scan")
async def oltgen_offline_scan(body: OltCleanScanIn, user: dict = Depends(get_current_user)):
    """Procura as ONUs off (LOS, DyingGasp, OffLine) e há quantos dias cada uma caiu. Só leitura."""
    _no_viewer(user)
    dev = await _get_device_for(user, body.device_id)
    vt = dev.get("device_type") or ""
    if vt == "fiberhome":
        raise HTTPException(status_code=400, detail="A limpeza de ONUs off ainda não está disponível para FiberHome (falta confirmar como a OLT mostra a hora da queda)")
    if vt not in ("zte", "huawei"):
        raise HTTPException(status_code=400, detail="Esse equipamento não está cadastrado como OLT ZTE ou Huawei")
    if any(j["device_id"] == dev["id"] and j["state"] == "running" for j in OLT_CLEAN_JOBS.values()):
        raise HTTPException(status_code=409, detail="Já tem uma varredura ou limpeza rodando nessa OLT")
    j = _clean_job(user, "scan", dev)
    days = max(1, min(3650, body.days))
    if vt == "huawei":
        asyncio.create_task(_hw_offline_scan(j, dev, days, [p for p in body.pons if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{1,3}", p)]))
    else:
        asyncio.create_task(_olt_offline_scan(j, dev, days, [p for p in body.pons if re.fullmatch(r"\d{1,2}/\d{1,2}", p)]))
    return _job_out(j)


@api.get("/oltgen/clean-jobs/{job_id}")
async def oltgen_clean_job(job_id: str, user: dict = Depends(get_current_user)):
    j = OLT_CLEAN_JOBS.get(job_id)
    if not j or j["owner"] != user["id"]:
        raise HTTPException(status_code=404, detail="Varredura não encontrada (o BastiON reiniciou?) — rode de novo")
    return _job_out(j)


class OltCleanRemoveIn(BaseModel):
    scan_id: str
    items: List[dict]            # [{slot, pon, onu}] escolhidas na varredura


async def _olt_offline_remove(j: dict, dev: dict, user: dict, scan: dict, picked: List[dict]):
    """Confere cada ONU de novo (ainda off e com o mesmo serial), guarda a configuração dela e remove as que passaram."""
    plat = scan.get("platform") or "titan"
    keep, skipped = [], []
    try:
        cli = await _connect_device(dev)
        try:
            raw = await cli.shell()
            sess = _OltSess(raw, j)
            try:
                for i, o in enumerate(picked, 1):
                    onu_if = oltgen.onu_if_name(plat, o["slot"], o["pon"], o["onu"])
                    j.update(step=f"conferindo {o['slot']}/{o['pon']}:{o['onu']}", done=i)
                    d = oltgen.parse_onu_detail(await sess.run(f"show gpon onu detail-info {onu_if}", timeout=60))
                    if (d["phase"] or "").lower() not in oltgen.OFF_PHASES:
                        skipped.append({**o, "why": f"agora está {d['phase'] or 'sem resposta'}"})
                        continue
                    if o.get("sn") and d["sn"] and d["sn"] != o["sn"]:
                        skipped.append({**o, "why": f"serial mudou ({d['sn']})"})
                        continue
                    cfg = await sess.run(f"show running-config interface {onu_if}", timeout=60)
                    mng = ""
                    for c in oltgen.MNG_COMMANDS:
                        mng = await sess.run(c.format(onu=onu_if), timeout=60)
                        if not _OLT_ERR.search(mng.replace(onu_if, "", 1)):
                            break
                    keep.append({**o, "sn": d["sn"] or o.get("sn"), "name": d["name"] or o.get("name"),
                                 "config": (cfg.strip() + "\n!\n" + mng.strip())[-20000:]})
            finally:
                try:
                    await raw.close()
                except Exception:
                    pass
        finally:
            await cli.close()
        if not keep:
            j.update(state="done", step="", removed=[], skipped=skipped, ok=True)
            return
        now = datetime.now(timezone.utc).isoformat()
        # guarda antes de remover: dá para reautorizar com a mesma configuração
        await db.olt_removed_onus.insert_many([{"id": os.urandom(8).hex(), "owner_id": user["id"], "device_id": dev["id"],
                                                "device_name": dev["name"], "at": now, "by": user.get("email", ""),
                                                "batch": j["id"], **{k: o.get(k) for k in ("slot", "pon", "onu", "sn", "name",
                                                "description", "type", "days", "last_down", "config")}} for o in keep])
        j.update(step=f"removendo {len(keep)} ONU(s) e gravando")
        r = await _olt_run_script(dev, oltgen.bulk_remove_script(plat, keep))
        await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"],
                                      "device_id": dev["id"], "device_name": dev["name"], "kind": "batch",
                                      "started_at": j["started_at"], "ended_at": datetime.now(timezone.utc).isoformat(),
                                      "duration_seconds": int(time.time() - j["_t"]),
                                      "commands": [s["command"] for s in r["steps"]], "oltgen": "offline-clean", "ok": r["ok"]})
        done, cur = set(), None
        for st in r["steps"]:                       # qual PON cada "no onu N" que deu certo estava
            m = re.match(r"interface\s+gpon[-_]olt[-_]1/(\d+)/(\d+)", st["command"])
            if m:
                cur = (int(m.group(1)), int(m.group(2)))
            m = re.match(r"no\s+onu\s+(\d+)$", st["command"])
            if m and st["ok"] and cur:
                done.add((*cur, int(m.group(1))))
        removed = [o for o in keep if (o["slot"], o["pon"], o["onu"]) in done]
        if not r["ok"]:
            await db.olt_removed_onus.delete_many({"batch": j["id"], "$or": [
                {"slot": o["slot"], "pon": o["pon"], "onu": o["onu"]} for o in keep if o not in removed] or [{"_never": 1}]})
        logger.info("Limpeza de ONUs off por %s em %s: %d removidas, %d puladas, %s", user.get("email"), dev["name"],
                    len(removed), len(skipped), "ok" if r["ok"] else "ERRO")
        j.update(state="done", step="", ok=r["ok"], failed=r["failed"], saved=r["ok"],
                 removed=[{k: o[k] for k in ("slot", "pon", "onu", "sn", "name")} for o in removed], skipped=skipped)
    except Exception as e:
        j.update(state="error", error=f"{e}"[:300] or type(e).__name__, skipped=skipped, diag=j.get("_last"))


@api.post("/oltgen/offline-remove")
async def oltgen_offline_remove(body: OltCleanRemoveIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    scan = OLT_CLEAN_JOBS.get(body.scan_id)
    if not scan or scan["owner"] != user["id"] or scan["kind"] != "scan" or scan["state"] != "done":
        raise HTTPException(status_code=404, detail="Varredura não encontrada — rode a busca de novo")
    if time.time() - scan["_t"] > 3600:
        raise HTTPException(status_code=409, detail="Essa varredura tem mais de 1 hora — rode a busca de novo antes de remover")
    by_key = {(o["slot"], o["pon"], o["onu"]): o for o in scan.get("onus") or []}
    hw = scan.get("platform") == "huawei"
    picked = []
    for it in body.items:
        try:
            k = (int(it["slot"]), int(it["pon"]), int(it["onu"]))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Itens inválidos")
        o = by_key.get(k)
        if not o:
            raise HTTPException(status_code=400, detail=f"A ONU {k[0]}/{k[1]}:{k[2]} não está na varredura")
        if not o["off"]:
            raise HTTPException(status_code=400, detail=f"A ONU {k[0]}/{k[1]}:{k[2]} não está off")
        picked.append(o)
    if not picked:
        raise HTTPException(status_code=400, detail="Escolha ao menos uma ONU")
    dev = await _get_device_for(user, scan["device_id"])
    lock = _olt_apply_locks.setdefault(dev["id"], asyncio.Lock())
    if lock.locked() or any(j["device_id"] == dev["id"] and j["state"] == "running" for j in OLT_CLEAN_JOBS.values()):
        raise HTTPException(status_code=409, detail="Já tem um script ou limpeza rodando nessa OLT; espere terminar")
    j = _clean_job(user, "remove", dev)
    j["total"] = len(picked)

    async def run():
        async with lock:
            await (_hw_offline_remove if hw else _olt_offline_remove)(j, dev, user, scan, picked)
    asyncio.create_task(run())
    return _job_out(j)


@api.get("/oltgen/removed")
async def oltgen_removed(device_id: str = "", limit: int = 500, user: dict = Depends(get_current_user)):
    """ONUs removidas pela limpeza, com a configuração guardada (para reautorizar se o cliente voltar)."""
    q = {"owner_id": user["id"], **({"device_id": device_id} if device_id else {})}
    return await db.olt_removed_onus.find(q, {"_id": 0}).sort("at", -1).to_list(min(2000, max(1, limit)))


class FindOnuIn(BaseModel):
    query: str = ""              # serial (ZTEGD4F3D9EC) ou MAC
    mac: str = ""                # compatibilidade com a tela antiga
    device_id: str = ""          # vazio ou "all" = procura em todas as OLTs ZTE/Huawei do usuário
    vendor: str = ""             # zte | huawei: só as OLTs desse fabricante (vazio = as duas)


@api.post("/oltgen/find-onu")
@api.post("/oltgen/find-mac")
async def oltgen_find_onu(body: FindOnuIn, user: dict = Depends(get_current_user)):
    """Acha a ONU pelo serial (show gpon onu by sn) ou pelo MAC (show mac) e diz quem é. Só leitura."""
    _no_viewer(user)
    q = (body.query or body.mac or "").strip()
    try:
        if oltgen.looks_like_sn(q) or olthw.looks_like_sn(q):
            hx = olthw.sn_hex(q)
            kind, key = "sn", olthw.sn_vendor(hx).replace("-", "")
        else:
            kind, key = "mac", oltgen.norm_mac(q)
    except oltgen.GenError:
        raise HTTPException(status_code=400, detail="Informe o serial da ONU (ex.: ZTEGD4F3D9EC, HWTC-A1B2C3D4) ou o MAC do equipamento do cliente")
    if body.device_id and body.device_id != "all":
        devs = [await _get_device_for(user, body.device_id)]
    else:
        types = [body.vendor] if body.vendor in ("zte", "huawei") else ["zte", "huawei"]
        devs = await db.devices.find({**_scope(user), "device_type": {"$in": types}}, {"_id": 0}).to_list(200)
        hws = [d for d in devs if d.get("device_type") == "huawei"]
        olt_like = [d for d in hws if _HW_OLT_RE.search(" ".join([d.get("name") or "", d.get("model") or "", d.get("description") or "",
                                                                  " ".join(d.get("tags") or [])]))]
        if olt_like:                       # Huawei também é roteador/switch (VRP): procura só nos que parecem OLT
            devs = [d for d in devs if d.get("device_type") != "huawei" or d in olt_like]
        devs = devs[:40]
        if not devs:
            raise HTTPException(status_code=404, detail="Nenhuma OLT " + (" ou ".join(t.upper() if t == "zte" else "Huawei" for t in types)) + " cadastrada")
    searched, errors = [], []
    for dev in devs:
        if (dev.get("device_type") or "") == "huawei":
            try:
                hit = await _hw_find(dev, kind, hx if kind == "sn" else olthw.hw_mac(key))
                searched.append(dev["name"])
                if hit:
                    await _olt_log(user, dev)
                    return {"found": True, "kind": kind, "query": key, "mac": key if kind == "mac" else "", "vendor": "huawei",
                            "device": dev["name"], "device_id": dev["id"], **hit, "searched": searched}
            except Exception as e:
                errors.append(f"{dev['name']}: {e}"[:160])
            continue
        if kind == "sn" and not oltgen.looks_like_sn(key):
            continue
        cmds = oltgen.SN_COMMANDS if kind == "sn" else oltgen.MAC_COMMANDS
        parse = (lambda raw: oltgen.parse_sn_lookup(raw)) if kind == "sn" else (lambda raw: oltgen.parse_mac_lookup(raw, key))
        try:
            cli = await _connect_device(dev)
        except Exception as e:
            errors.append(f"{dev['name']}: {e}"[:160])
            continue
        try:
            hit, raw = None, ""
            for cmd in cmds:
                raw = _out_text(await cli.run_command(cmd.format(mac=key, sn=key), timeout=60))
                hit = parse(raw)
                if hit or not re.search(r"invalid|unrecognized|error|%", raw, re.I):
                    break
            searched.append(dev["name"])
            if not hit:
                continue
            pon_cfg = _out_text(await cli.run_command(
                f"show running-config interface {oltgen.pon_if(hit['platform'], hit['slot'], hit['pon'])}", timeout=60))
            onu_cfg = _out_text(await cli.run_command(
                f"show running-config interface {oltgen.onu_if_name(hit['platform'], hit['slot'], hit['pon'], hit['onu'])}", timeout=60))
            info = oltgen.find_onu(pon_cfg, onu_cfg, hit["onu"]) or {}
            await _olt_log(user, dev)
            return {"found": True, "kind": kind, "query": key, "mac": key if kind == "mac" else "", "vendor": "zte",
                    "device": dev["name"], "device_id": dev["id"], **hit, **info, "searched": searched}
        except Exception as e:
            errors.append(f"{dev['name']}: {e}"[:160])
        finally:
            try:
                await cli.close()
            except Exception:
                pass
    return {"found": False, "kind": kind, "query": key, "mac": key if kind == "mac" else "", "searched": searched, "errors": errors}


async def _olt_log(user: dict, dev: dict, started: Optional[datetime] = None, **extra):
    now = datetime.now(timezone.utc)
    st = started or now
    await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"],
                                  "device_id": dev["id"], "device_name": dev["name"], "kind": "batch",
                                  "started_at": st.isoformat(), "ended_at": now.isoformat(),
                                  "duration_seconds": int((now - st).total_seconds()), **extra})


# ---------- OLT Huawei (MA5800 / MA5608T / MA5683T) ----------
_HW_OLT_RE = re.compile(r"olt|ma5[68]\d\d|ea5[68]\d\d|smartax|gpon", re.I)
def _hw_dev_check(dev: dict):
    if (dev.get("device_type") or "") != "huawei":
        raise HTTPException(status_code=400, detail="Esse equipamento não está cadastrado como Huawei")


class _HwSess:
    """Sessão numa OLT Huawei já no modo config (enable + config). Guarda o último comando para o diagnóstico."""
    def __init__(self, dev: dict, j: Optional[dict] = None):
        self.dev, self.j, self.cli, self.sess = dev, j, None, None

    async def __aenter__(self):
        self.cli = await _connect_device(self.dev)
        try:
            self.sess = await self.cli.shell()
            await self.sess.run("enable", timeout=20)
            await self.sess.run("config", timeout=20)
        except Exception:
            await self.cli.close()
            raise
        return self

    async def run(self, cmd: str, timeout: int = 90) -> str:
        out = await self.sess.run(cmd, timeout=timeout)
        if self.j is not None:
            self.j["_last"] = {"command": cmd, "output": (out or "")[-1500:]}
        return out or ""

    async def __aexit__(self, *a):
        try:
            await self.sess.close()
        except Exception:
            pass
        await self.cli.close()


def _hw_err(out: str, cmd: str) -> bool:
    return bool(_OLT_ERR.search((out or "").replace(cmd, "", 1)))


async def _hw_find(dev: dict, kind: str, key: str) -> Optional[dict]:
    async with _HwSess(dev) as s:
        if kind == "sn":
            hit = olthw.parse_by_sn(await s.run(f"display ont info by-sn {key}", timeout=60))
        else:
            hit = None
            for c in olthw.MAC_COMMANDS:
                out = await s.run(c.format(mac=key), timeout=120)
                hit = olthw.parse_mac_lookup(out, key)
                if hit:
                    break
            if hit:
                hit.update(olthw.parse_detail(await s.run(f"display ont info {hit['frame']} {hit['slot']} {hit['port']} {hit['ont']}", timeout=60)))
    if not hit:
        return None
    return {**hit, "platform": "huawei", "onu": hit["ont"], "name": hit.get("description") or "",
            "interface": f"{hit['pon']} ONT {hit['ont']}", "sn_vendor": olthw.sn_vendor(hit.get("sn") or "") if hit.get("sn") else ""}


@api.get("/oltgen/hw/models")
async def oltgen_hw_models(_: dict = Depends(get_current_user)):
    return {"models": [{"key": k, "slots": v} for k, v in olthw.MODELS.items()]}


def _hw_gen(fn, body):
    try:
        return fn(body or {})
    except (oltgen.GenError, TypeError, ValueError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=str(e) or "Parâmetros inválidos")


@api.post("/oltgen/hw/generate")
async def oltgen_hw_generate(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return _hw_gen(olthw.generate, body)


@api.post("/oltgen/hw/onu")
async def oltgen_hw_onu(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return _hw_gen(olthw.onu_script, body)


@api.post("/oltgen/hw/onu-remove")
async def oltgen_hw_onu_remove(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return _hw_gen(olthw.onu_remove_script, body)


@api.post("/oltgen/hw/read-board")
async def oltgen_hw_read_board(body: OltCardsIn, user: dict = Depends(get_current_user)):
    """`display board 0`: placas PON (slot e quantidade de portas) para o formulário. Só leitura."""
    _no_viewer(user)
    dev = await _get_device_for(user, body.device_id)
    _hw_dev_check(dev)
    try:
        async with _HwSess(dev) as s:
            text = await s.run("display board 0", timeout=60)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui ler a OLT: {e}"[:300])
    boards = olthw.parse_board(text)
    if not boards:
        raise HTTPException(status_code=422, detail="O display board 0 não trouxe placas PON reconhecíveis")
    await _olt_log(user, dev)
    return {"pon": boards, "device": dev["name"], "raw": text[-6000:]}


@api.post("/oltgen/hw/autofind")
async def oltgen_hw_autofind(body: OltCardsIn, user: dict = Depends(get_current_user)):
    """ONTs pedindo autorização (`display ont autofind all`) + o próximo ONT ID livre de cada PON. Só leitura."""
    _no_viewer(user)
    dev = await _get_device_for(user, body.device_id)
    _hw_dev_check(dev)
    try:
        async with _HwSess(dev) as s:
            text = await s.run("display ont autofind all", timeout=120)
            onus = olthw.parse_autofind(text)
            info = olthw.parse_ont_info(await s.run("display ont info 0 all", timeout=180)) if onus else []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui ler a OLT: {e}"[:300])
    await _olt_log(user, dev)
    taken: dict = {}
    out = []
    for o in onus:
        used = taken.setdefault(o["pon"], set(olthw.used_ont_ids(info, o["frame"], o["slot"], o["port"])))
        fid = olthw.free_ont_id(sorted(used))
        if fid is not None:
            used.add(fid)
        out.append({**o, "free_id": fid})
    return {"device": dev["name"], "platform": "huawei", "onus": out, "raw": text[-4000:]}


class HwOnuInfoIn(BaseModel):
    device_id: str
    items: List[dict] = []       # [{pon: "0/1/0", onu: 3}]


@api.post("/oltgen/hw/onu-info")
async def oltgen_hw_onu_info(body: HwOnuInfoIn, user: dict = Depends(get_current_user)):
    """Confere na OLT quem é cada ONT antes de desautorizar (serial, descrição, estado). Só leitura."""
    _no_viewer(user)
    try:
        items = [(*olthw.fsp(i.get("pon")), int(i["onu"])) for i in body.items[:64]]
    except (KeyError, TypeError, ValueError, oltgen.GenError):
        raise HTTPException(status_code=400, detail="Informe a PON (F/S/P) e o ONT ID")
    if not items:
        raise HTTPException(status_code=400, detail="Informe ao menos uma ONU")
    dev = await _get_device_for(user, body.device_id)
    _hw_dev_check(dev)
    out = []
    try:
        async with _HwSess(dev) as s:
            for f, sl, pt, o in items:
                d = olthw.parse_detail(await s.run(f"display ont info {f} {sl} {pt} {o}", timeout=60))
                out.append({"pon": f"{f}/{sl}/{pt}", "onu": o, "found": bool(d["sn"]), **d,
                            "sn_vendor": olthw.sn_vendor(d["sn"]) if d["sn"] else "", "name": d["description"]})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui ler a OLT: {e}"[:300])
    await _olt_log(user, dev)
    return {"device": dev["name"], "items": out}


async def _hw_offline_scan(j: dict, dev: dict, days: int, only_pons: List[str]):
    try:
        async with _HwSess(dev, j) as s:
            j.update(platform="huawei", step="lendo relógio e ONTs da OLT")
            clock = olthw._dt(await s.run("display time", timeout=30))
            rows = olthw.parse_ont_info(await s.run("display ont info 0 all", timeout=300))
            if only_pons:
                want = {tuple(int(x) for x in p.split("/")) for p in only_pons}
                rows = [r for r in rows if (r["frame"], r["slot"], r["port"]) in want]
            if not rows:
                raise RuntimeError("Não achei ONTs no 'display ont info 0 all'")
            offs = [r for r in rows if r["off"]][:OLT_CLEAN_MAX]
            pons = sorted({(r["frame"], r["slot"], r["port"]) for r in offs})
            j.update(olt_clock=clock.strftime("%Y-%m-%d %H:%M:%S") if clock else None, onus_total=len(rows),
                     pons=len({(r["frame"], r["slot"], r["port"]) for r in rows}), total=len(pons), done=0)
            summ = {}
            for i, (f, sl, pt) in enumerate(pons, 1):
                j.update(step=f"última queda das ONTs da PON {f}/{sl}/{pt}", done=i)
                for r in olthw.parse_summary(await s.run(f"display ont info summary {f}/{sl}/{pt}", timeout=120)):
                    summ[(f, sl, pt, r["ont"])] = r
        now = clock or datetime.now()
        res = []
        for o in offs:
            d = summ.get((o["frame"], o["slot"], o["port"], o["ont"])) or {}
            last = d.get("last_down")
            off_days = round((now - last).total_seconds() / 86400, 1) if last else None
            res.append({"frame": o["frame"], "slot": o["slot"], "pon": o["port"], "onu": o["ont"], "off": True,
                        "sn": o["sn"], "name": o.get("description") or d.get("description") or "", "description": o.get("description") or "",
                        "type": d.get("type") or "", "phase": o["run"], "cause": d.get("cause") or "",
                        "last_down": last.strftime("%Y-%m-%d %H:%M:%S") if last else None, "days": off_days,
                        "eligible": off_days is not None and off_days >= days})
        res.sort(key=lambda r: (-(r["days"] if r["days"] is not None else -1), r["slot"], r["pon"], r["onu"]))
        j.update(state="done", step="", onus=res, days=days, eligible=sum(1 for r in res if r["eligible"]),
                 ended_at=datetime.now(timezone.utc).isoformat(),
                 sample=(j.get("_last") or {}) if res and all(r["days"] is None for r in res) else None)
    except Exception as e:
        j.update(state="error", error=f"{e}"[:300] or type(e).__name__, diag=j.get("_last"))


async def _hw_offline_remove(j: dict, dev: dict, user: dict, scan: dict, picked: List[dict]):
    keep, skipped = [], []
    try:
        async with _HwSess(dev, j) as s:
            for i, o in enumerate(picked, 1):
                f = o.get("frame", 0)
                j.update(step=f"conferindo {f}/{o['slot']}/{o['pon']} ONT {o['onu']}", done=i)
                d = olthw.parse_detail(await s.run(f"display ont info {f} {o['slot']} {o['pon']} {o['onu']}", timeout=60))
                if d["run"] != "offline":
                    skipped.append({**o, "why": f"agora está {d['run'] or 'sem resposta'}"})
                    continue
                if o.get("sn") and d["sn"] and d["sn"] != o["sn"]:
                    skipped.append({**o, "why": f"serial mudou ({d['sn']})"})
                    continue
                cfg = await s.run(f"display current-configuration ont {f}/{o['slot']}/{o['pon']} {o['onu']}", timeout=90)
                sp = await s.run(f"display service-port port {f}/{o['slot']}/{o['pon']} ont {o['onu']}", timeout=60)
                keep.append({**o, "frame": f, "sn": d["sn"] or o.get("sn"), "name": d["description"] or o.get("name"),
                             "config": (cfg.strip() + "\n#\n" + sp.strip())[-20000:]})
        if not keep:
            j.update(state="done", step="", removed=[], skipped=skipped, ok=True)
            return
        now = datetime.now(timezone.utc).isoformat()
        await db.olt_removed_onus.insert_many([{"id": os.urandom(8).hex(), "owner_id": user["id"], "device_id": dev["id"],
                                                "device_name": dev["name"], "at": now, "by": user.get("email", ""), "vendor": "huawei",
                                                "batch": j["id"], **{k: o.get(k) for k in ("frame", "slot", "pon", "onu", "sn", "name",
                                                "description", "type", "days", "last_down", "config")}} for o in keep])
        j.update(step=f"removendo {len(keep)} ONT(s) e gravando")
        r = await _olt_run_script(dev, olthw.bulk_remove_script([(o["frame"], o["slot"], o["pon"], o["onu"]) for o in keep]))
        await _olt_log(user, dev, datetime.fromisoformat(j["started_at"]), commands=[x["command"] for x in r["steps"]],
                       oltgen="offline-clean", ok=r["ok"])
        done, cur = set(), None
        for st in r["steps"]:
            m = re.match(r"interface\s+gpon\s+(\d+)/(\d+)$", st["command"])
            if m:
                cur = (int(m.group(1)), int(m.group(2)))
            m = re.match(r"ont\s+delete\s+(\d+)\s+(\d+)$", st["command"])
            if m and st["ok"] and cur:
                done.add((cur[1], int(m.group(1)), int(m.group(2))))
        removed = [o for o in keep if (o["slot"], o["pon"], o["onu"]) in done]
        if not r["ok"]:
            await db.olt_removed_onus.delete_many({"batch": j["id"], "$or": [
                {"slot": o["slot"], "pon": o["pon"], "onu": o["onu"]} for o in keep if o not in removed] or [{"_never": 1}]})
        logger.info("Limpeza de ONTs off (Huawei) por %s em %s: %d removidas, %d puladas, %s", user.get("email"), dev["name"],
                    len(removed), len(skipped), "ok" if r["ok"] else "ERRO")
        j.update(state="done", step="", ok=r["ok"], failed=r["failed"], saved=r["ok"],
                 removed=[{k: o.get(k) for k in ("frame", "slot", "pon", "onu", "sn", "name")} for o in removed], skipped=skipped)
    except Exception as e:
        j.update(state="error", error=f"{e}"[:300] or type(e).__name__, skipped=skipped, diag=j.get("_last"))


# ---------- OLT FiberHome (AN5516, CLI clássica) ----------
def _fh_dev_check(dev: dict):
    if (dev.get("device_type") or "") != "fiberhome":
        raise HTTPException(status_code=400, detail="Esse equipamento não está cadastrado como FiberHome")


class _FhSess:
    """Sessão numa OLT FiberHome: enable (com a senha de enable, se pedir) e o diretório de ONU (cd onu ou cd gpononu)."""
    def __init__(self, dev: dict, j: Optional[dict] = None):
        self.dev, self.j, self.cli, self.sess, self.dir = dev, j, None, None, None

    async def __aenter__(self):
        self.cli = await _connect_device(self.dev)
        try:
            self.sess = await self.cli.shell()
            out = await self.sess.run("enable", timeout=20)
            if _ENABLE_PW.search(out.strip()[-40:]):
                out = await self.sess.run(_enable_password(self.dev), timeout=20)
            # o prompt muda de "User>" para "Admin#" (ou "<nome>#"): ensina o leitor, senão cada comando espera o silêncio
            last = [l.strip() for l in (out or "").replace("\r", "\n").split("\n") if l.strip()]
            if last and last[-1][-1:] in "#>":
                base = re.split(r"[\\\\#>(]", last[-1])[0].strip()
                if len(base) >= 2:
                    if hasattr(self.sess, "prompt"):
                        self.sess.prompt = base
                    if hasattr(self.cli, "_prompt"):
                        self.cli._prompt = base
        except Exception:
            await self.cli.close()
            raise
        return self

    async def run(self, cmd: str, timeout: int = 90) -> str:
        out = await self.sess.run(cmd, timeout=timeout)
        if self.j is not None:
            self.j["_last"] = {"command": cmd, "output": (out or "")[-1500:]}
        return out or ""

    async def cd_onu(self, prefer: str = "") -> str:
        """Entra no diretório de ONU: 'cd onu' nos firmwares novos, 'cd gpononu' nos antigos."""
        if self.dir:
            return self.dir
        for d in ([prefer] if prefer in oltfh.DIRS else []) + [x for x in oltfh.DIRS if x != prefer]:
            out = await self.run(f"cd {d}", timeout=20)
            if not _olt_err(out, f"cd {d}") and not re.search(r"(?i)not\s+(exist|found)|no\s+such|error", out.replace(f"cd {d}", "", 1)):
                self.dir = d
                return d
        raise RuntimeError("A OLT não aceitou 'cd onu' nem 'cd gpononu' — confira a versão do firmware (a AN6000 tem outra CLI)")

    async def pons(self, only: Optional[List[tuple]] = None) -> List[tuple]:
        cards = oltfh.parse_cards(await self.run("showcard", timeout=60))
        if not cards:
            raise RuntimeError("Não achei placas GPON no 'showcard'")
        ps = [(c["slot"], p) for c in cards for p in range(1, c["ports"] + 1)]
        return [x for x in ps if x in set(only)] if only else ps

    async def __aexit__(self, *a):
        try:
            await self.sess.close()
        except Exception:
            pass
        await self.cli.close()


@api.post("/oltgen/fh/generate")
async def oltgen_fh_generate(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return _hw_gen(oltfh.generate, body)


@api.post("/oltgen/fh/onu")
async def oltgen_fh_onu(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return _hw_gen(oltfh.onu_script, body)


@api.post("/oltgen/fh/onu-remove")
async def oltgen_fh_onu_remove(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return _hw_gen(oltfh.onu_remove_script, body)


@api.post("/oltgen/fh/read-cards")
async def oltgen_fh_read_cards(body: OltCardsIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    dev = await _get_device_for(user, body.device_id)
    _fh_dev_check(dev)
    j: dict = {}
    try:
        async with _FhSess(dev, j) as s:
            text = await s.run("showcard", timeout=60)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui ler a OLT: {e}"[:300])
    cards = oltfh.parse_cards(text)
    if not cards:
        raise HTTPException(status_code=422, detail="O showcard não trouxe placas GPON reconhecíveis. Resposta: " + text.strip()[-400:])
    await _olt_log(user, dev)
    return {"pon": cards, "device": dev["name"], "raw": text[-6000:]}


class FhDiscIn(BaseModel):
    device_id: str
    pons: List[str] = []         # "1/2" (slot/pon); vazio = todas as PONs das placas GPON
    dir: str = ""


def _fh_pons(lst: List[str]) -> List[tuple]:
    out = []
    for p in lst[:64]:
        m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})\s*", p or "")
        if not m:
            raise HTTPException(status_code=400, detail="PONs no formato slot/pon, ex.: 1/2")
        out.append((int(m.group(1)), int(m.group(2))))
    return out


@api.post("/oltgen/fh/discovery")
async def oltgen_fh_discovery(body: FhDiscIn, user: dict = Depends(get_current_user)):
    """ONUs pedindo autorização (show discovery) + o próximo ID livre (show authorization) de cada PON. Só leitura."""
    _no_viewer(user)
    dev = await _get_device_for(user, body.device_id)
    _fh_dev_check(dev)
    only = _fh_pons(body.pons)
    j: dict = {}
    onus, raw = [], ""
    try:
        async with _FhSess(dev, j) as s:
            pons = await s.pons(only or None)
            d = await s.cd_onu(body.dir)
            for sl, pn in pons[:64]:
                out = await s.run(f"show discovery slot {sl} pon {pn}", timeout=60)
                if _olt_err(out, "show discovery") and not oltfh.SN_RE.search(out):
                    out = await s.run(f"show discovery slot {sl} link {pn}", timeout=60)
                found = oltfh.parse_discovery(out, sl, pn)
                if found:
                    raw = raw or out
                    used = [a["onu"] for a in oltfh.parse_auth(await s.run(f"show authorization slot {sl} pon {pn}", timeout=60), sl, pn)]
                    for o in found:
                        fid = oltfh.free_onu_id(used)
                        if fid:
                            used.append(fid)
                        onus.append({**o, "free_id": fid})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui ler a OLT: {e}"[:300])
    await _olt_log(user, dev)
    return {"device": dev["name"], "platform": "fiberhome", "dir": d, "onus": onus, "pons": len(pons),
            "raw": (raw or (j.get("_last") or {}).get("output") or "")[-3000:]}


class FhInfoIn(BaseModel):
    device_id: str
    items: List[dict] = []       # [{slot, pon, onu}]
    dir: str = ""


@api.post("/oltgen/fh/onu-info")
async def oltgen_fh_onu_info(body: FhInfoIn, user: dict = Depends(get_current_user)):
    """Confere na OLT cada ONU (serial, tipo, estado) pelo show authorization da PON. Só leitura."""
    _no_viewer(user)
    try:
        items = [(int(i["slot"]), int(i["pon"]), int(i["onu"])) for i in body.items[:64]]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Informe slot, PON e ONU em números")
    if not items:
        raise HTTPException(status_code=400, detail="Informe ao menos uma ONU")
    dev = await _get_device_for(user, body.device_id)
    _fh_dev_check(dev)
    out, raw = [], ""
    try:
        async with _FhSess(dev) as s:
            d = await s.cd_onu(body.dir)
            cache: dict = {}
            for sl, pn, o in items:
                if (sl, pn) not in cache:
                    txt = await s.run(f"show authorization slot {sl} pon {pn}", timeout=60)
                    raw = raw or txt
                    cache[(sl, pn)] = {a["onu"]: a for a in oltfh.parse_auth(txt, sl, pn)}
                a = cache[(sl, pn)].get(o)
                out.append({"slot": sl, "pon": pn, "onu": o, "found": bool(a), **(a or {})})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não consegui ler a OLT: {e}"[:300])
    await _olt_log(user, dev)
    return {"device": dev["name"], "dir": d, "items": out, "raw": raw[-3000:] if not any(x["found"] for x in out) else ""}


async def _gpon_read_fh(dev: dict, j: dict, power: bool):
    """FiberHome: estado de cada ONU pelo show authorization de cada PON (o sinal só na consulta da ONU)."""
    async with _FhSess(dev, j) as s:
        pons = await s.pons()
        await s.cd_onu(_gpon_rt["fhdir"].get(dev["id"], ""))
        _gpon_rt["fhdir"][dev["id"]] = s.dir
        rows = []
        for sl, pn in pons:
            for a in oltfh.parse_auth(await s.run(f"show authorization slot {sl} pon {pn}", timeout=90), sl, pn):
                rows.append({"slot": sl, "pon": pn, "onu": a["onu"], "admin": "", "phase": a["state"] or "?", "off": a["off"],
                             "sn": a["sn"], "type": a["type"]})
    if not rows and pons:
        raise RuntimeError("Não reconheci nenhuma ONU no 'show authorization' — veja a resposta da OLT no diagnóstico")
    return rows, sorted(set(pons)), {}, "fiberhome"


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
    pub = await _lg_public_cfg()
    plog = await db.lg_public_log.find({}, {"_id": 0}).sort("ts", -1).to_list(30)
    for x in plog:
        x["ts"] = x["ts"].isoformat() if hasattr(x["ts"], "isoformat") else str(x["ts"])
    p24 = await db.lg_public_log.count_documents({"ts": {"$gte": datetime.now(timezone.utc) - timedelta(hours=24)}})
    return {"devices": [{**_lg_router(d), "host": d.get("host"), "looking_glass": bool(d.get("looking_glass"))} for d in devs],
            "vendors": lookingglass.VENDORS, "template_keys": lookingglass.TEMPLATE_KEYS,
            "defaults": lookingglass.DEFAULT_COMMANDS, "overrides": ov,
            "public": {**{k: v for k, v in pub.items() if k != "password"}, "has_password": bool(pub.get("password"))},
            "public_port": LG_PUBLIC_PORT, "public_log": plog, "public_24h": p24}


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


# ---------- Looking Glass público (sem login, numa porta própria) ----------
LG_PUBLIC_PORT = int(os.environ.get("LG_PUBLIC_PORT", "8089") or 0)
_lgp_hits: dict = {}        # ip -> [instantes]
_lgp_day: dict = {}         # (dia, ip) -> contagem
_lgp_wait: dict = {}        # roteador -> quantos esperando


async def _lg_public_cfg() -> dict:
    doc = (await db.config.find_one({"key": "lg"}, {"_id": 0}) or {}).get("public") or {}
    return {**lookingglass.PUBLIC_DEFAULTS, **doc}


async def _lg_public_routers(cfg: dict) -> List[dict]:
    if not cfg["routers"]:
        return []
    devs = await db.devices.find({"id": {"$in": cfg["routers"]}, "looking_glass": True,
                                  "device_type": {"$in": lookingglass.VENDORS}}, {"_id": 0}).to_list(200)
    devs.sort(key=lambda d: d["name"].lower())
    return devs


@api.get("/public/lg")
async def lg_public_info():
    cfg = await _lg_public_cfg()
    if not cfg["enabled"]:
        return {"enabled": False}
    devs = await _lg_public_routers(cfg)
    return {"enabled": True, "title": cfg["title"], "contact": cfg["contact"],
            "routers": [{"id": d["id"], "name": d["name"]} for d in devs],
            "queries": [{"key": k, "label": lookingglass.QUERIES[k][0], "needs_target": lookingglass.QUERIES[k][1],
                         "allow_prefix": lookingglass.QUERIES[k][3]} for k in cfg["queries"]],
            "per_min": cfg["per_min"]}


class LGPublicIn(BaseModel):
    router: str
    query: str
    target: str = ""
    v6: bool = False


@api.post("/public/lg/query")
async def lg_public_query(body: LGPublicIn, request: Request):
    cfg = await _lg_public_cfg()
    if not cfg["enabled"]:
        raise HTTPException(status_code=404, detail="Looking Glass público desligado")
    ip = security.client_ip(request)
    if body.query not in cfg["queries"]:
        raise HTTPException(status_code=400, detail="Consulta não disponível")
    if body.router not in cfg["routers"]:
        raise HTTPException(status_code=404, detail="Roteador não disponível")
    dev = await db.devices.find_one({"id": body.router, "looking_glass": True}, {"_id": 0})
    if not dev:
        raise HTTPException(status_code=404, detail="Roteador não disponível")
    try:
        lookingglass.check_public_target(body.target, body.query)
        cmd = lookingglass.build(dev.get("device_type") or "", body.query, body.target, await _lg_overrides(), body.v6)
    except lookingglass.LGError as e:
        raise HTTPException(status_code=400, detail=str(e))
    now = time.time()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    if len(_lgp_hits) > 20000 or len(_lgp_day) > 50000:      # limpeza barata
        for k in [k for k, v in _lgp_hits.items() if not v or now - v[-1] > 60]:
            _lgp_hits.pop(k, None)
        for k in [k for k in _lgp_day if k[0] != day]:
            _lgp_day.pop(k, None)
    hits = [t for t in _lgp_hits.get(ip, []) if now - t < 60]
    if len(hits) >= cfg["per_min"]:
        raise HTTPException(status_code=429, detail=f"Muitas consultas — o limite é de {cfg['per_min']} por minuto. Aguarde um pouco.")
    if _lgp_day.get((day, ip), 0) >= cfg["per_day"]:
        raise HTTPException(status_code=429, detail="Limite diário de consultas atingido para o seu endereço.")
    if _lgp_wait.get(dev["id"], 0) >= 3:
        raise HTTPException(status_code=429, detail="Este roteador está ocupado com outras consultas. Tente em instantes.")
    _lgp_hits[ip] = hits + [now]
    _lgp_day[(day, ip)] = _lgp_day.get((day, ip), 0) + 1
    if cfg["username"]:                                    # credencial só de leitura, própria do Looking Glass público
        dev = {**dev, "username": cfg["username"], "password": cfg.get("password") or ""}
    lock = _lg_locks.setdefault(dev["id"], asyncio.Lock())
    started = datetime.now(timezone.utc)
    ok, out, err = False, "", ""
    _lgp_wait[dev["id"]] = _lgp_wait.get(dev["id"], 0) + 1
    try:
        async with lock:
            try:
                c = await _connect_device(dev)
                try:
                    res = await c.run_command(cmd, timeout=60, idle=2.0)
                finally:
                    await c.close()
                raw = res["stdout"] if isinstance(res["stdout"], str) else res["stdout"].decode("utf-8", "replace")
                out = lookingglass.tidy(raw, cmd)[-30000:]
                ok = bool(res["ok"])
                err = "" if ok else "O roteador não respondeu a tempo."
            except Exception as e:
                logger.info(f"lg público {dev['name']}: {e}")
                err = "Não foi possível consultar este roteador agora."      # detalhes internos não vão para fora
    finally:
        _lgp_wait[dev["id"]] = max(0, _lgp_wait.get(dev["id"], 1) - 1)
    ended = datetime.now(timezone.utc)
    await db.lg_public_log.insert_one({"ts": started, "ip": ip, "router": dev["name"], "query": body.query,
                                       "target": body.target.strip()[:80], "ok": ok,
                                       "seconds": round((ended - started).total_seconds(), 1)})
    return {"ok": ok, "router": dev["name"], "output": out, "error": err, "seconds": round((ended - started).total_seconds(), 1)}


class LGPublicCfgIn(BaseModel):
    enabled: bool = False
    title: str = "Looking Glass"
    contact: str = ""
    queries: List[str] = []
    per_min: int = 6
    per_day: int = 200
    username: str = ""
    password: Optional[str] = None            # None = mantém; "" = apaga
    routers: List[str] = []


@api.put("/lg/admin/public")
async def lg_set_public(body: LGPublicCfgIn, _: dict = Depends(require_admin)):
    cur = await _lg_public_cfg()
    try:
        cfg = lookingglass.clean_public(body.model_dump(exclude={"password"}), cur)
    except lookingglass.LGError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if body.password is not None:
        cfg["password"] = vault.encrypt(body.password) if body.password else ""
    if not cfg["username"]:
        cfg["password"] = ""
    if cfg["enabled"] and not cfg["routers"]:
        raise HTTPException(status_code=400, detail="Marque pelo menos um roteador como público")
    await db.config.update_one({"key": "lg"}, {"$set": {"public": cfg}}, upsert=True)
    return {"ok": True}


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


async def _scan_run(job: dict, targets: List[dict], password: str, hops: List[Hop], work=None, per_host: float = 150):
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
                        coro = work(t, tunnel) if work else vendorscan.probe(t, job["protocol"], job["username"], password, tunnel)
                        r = await asyncio.wait_for(coro, per_host)
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


class AdhocBatchIn(ScanIn):
    command: str = ""
    script_id: Optional[str] = None
    device_type: str = "auto"
    timeout: int = 60


@api.post("/batch/adhoc")
async def batch_adhoc(body: AdhocBatchIn, user: dict = Depends(get_current_user)):
    """Execução em lote numa lista de IPs, com usuário/senha informados na hora — nada é cadastrado."""
    command = body.command
    if body.script_id and not command.strip():
        sc = await db.scripts.find_one({"id": body.script_id}, {"_id": 0})
        if not sc:
            raise HTTPException(status_code=404, detail="Script não encontrado")
        command = sc["content"]
    if not command.strip():
        raise HTTPException(status_code=400, detail="Comando ou script obrigatório")
    if body.protocol not in ("ssh", "telnet", "auto"):
        raise HTTPException(status_code=400, detail="Protocolo inválido")
    if body.device_type != "auto" and body.device_type not in DEVICE_TYPES:
        raise HTTPException(status_code=400, detail="Tipo de equipamento inválido")
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
        raise HTTPException(status_code=409, detail="Já existe uma execução/identificação sua em andamento — aguarde terminar ou pare")
    hops, agent_name = [], ""
    if body.agent_id:
        ag = await _get_agent_for(user, body.agent_id)
        agent_name = ag["name"]
        hops = [_agent_hop(a, priv) for a in await _agent_chain(body.agent_id)]
    timeout = max(5, min(int(body.timeout or 60), 600))
    now = time.time()
    for k in [k for k, j in _scan_jobs.items() if now - j["ts"] > 3600]:
        _scan_jobs.pop(k, None)
    job = {"id": os.urandom(8).hex(), "owner": user["id"], "ts": now, "total": len(targets), "done": 0, "finished": False,
           "results": [None] * len(targets), "invalid": invalid[:50], "protocol": body.protocol, "username": username,
           "agent_id": body.agent_id or None, "agent_name": agent_name, "error": "",
           "started_at": datetime.now(timezone.utc).isoformat()}
    _scan_jobs[job["id"]] = job
    lines = len([l for l in command.splitlines() if l.strip()])

    def work(t, tunnel):
        return vendorscan.run(t, body.protocol, username, password, tunnel, body.device_type, command, timeout)
    _bg(_scan_run(job, targets, password, hops, work, per_host=60 + timeout * lines + 40))
    await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"], "device_id": None,
                                  "device_name": f"Lote por IP — {len(targets)} endereço(s)" + (f" via {agent_name}" if agent_name else ""),
                                  "started_at": job["started_at"], "ended_at": job["started_at"], "duration_seconds": 0,
                                  "kind": "batch", "command": command[:2000]})
    return _scan_public(job)


@api.get("/batch/adhoc/{job_id}")
async def batch_adhoc_get(job_id: str, user: dict = Depends(get_current_user)):
    return _scan_public(_scan_job(job_id, user))


@api.post("/batch/adhoc/{job_id}/cancel")
async def batch_adhoc_cancel(job_id: str, user: dict = Depends(get_current_user)):
    _scan_job(job_id, user)["cancel"] = True
    return {"ok": True}


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


# ---------- Botnet nos assinantes (flow dos BNGs) ----------
async def _botnet_cfg() -> dict:
    doc = await db.config.find_one({"key": "flow_botnet"}, {"_id": 0}) or {}
    return {**botnet.DEFAULTS, **{k: v for k, v in doc.items() if k in botnet.DEFAULTS}}


async def _botnet_device(cfg: dict, exporter: str) -> Optional[dict]:
    """Equipamento (BNG) por trás de um exportador: o escolhido na configuração, o que tem esse IP, ou o das interfaces de flow."""
    did = (cfg.get("devices") or {}).get(exporter)
    if did:
        d = await db.devices.find_one({"id": did}, {"_id": 0})
        if d:
            return d
    d = await db.devices.find_one({"host": exporter}, {"_id": 0})
    if d:
        return d
    i = await db.flow_ifaces.find_one({"exporter": exporter}, {"_id": 0, "device_id": 1})
    return await db.devices.find_one({"id": i["device_id"]}, {"_id": 0}) if i else None


async def _botnet_lookup(inc: dict, cfg: dict) -> dict:
    """Pergunta ao BNG quem está com o IP do incidente (login PPPoE/IPoE, MAC, interface)."""
    now = datetime.now(timezone.utc).isoformat()
    dev = await _botnet_device(cfg, inc.get("exporter") or "")
    if not dev:
        sub = {"found": False, "reason": "o BNG deste flow não está ligado a um equipamento cadastrado (configure em Botnet → BNGs)", "at": now}
    else:
        vendor = dev.get("device_type") or "other"
        try:
            cmd = botnet.lookup_command(vendor, inc["ip"], cfg.get("lookup_commands"))
        except ValueError:
            cmd = None
        if not cmd:
            sub = {"found": False, "reason": f"sem comando de consulta para o tipo {vendor} (defina em Botnet → Configurar)", "at": now, "device": dev["name"]}
        else:
            try:
                c = await _connect_device(dev)
                try:
                    res = await c.run_command(cmd, timeout=25)
                finally:
                    await c.close()
                raw = res["stdout"] if isinstance(res["stdout"], str) else res["stdout"].decode("utf-8", "replace")
                sub = {**botnet.parse_subscriber(raw), "raw": raw[-3000:], "command": cmd, "at": now, "device": dev["name"]}
                if not sub["found"] and not sub.get("reason"):
                    sub["reason"] = "não reconheci o login na resposta do BNG — veja a saída e ajuste o comando"
            except Exception as e:
                sub = {"found": False, "reason": f"não consegui consultar o BNG: {str(e)[:160]}", "at": now, "device": dev["name"]}
    await db.flow_botnet.update_one({"id": inc["id"]}, {"$set": {"sub": sub}, "$inc": {"sub_tries": 1}})
    return sub


async def _botnet_lookup_loop():
    await asyncio.sleep(45)
    sem = asyncio.Semaphore(3)
    while True:
        try:
            cfg = await _botnet_cfg()
            if cfg["enabled"] and cfg["lookup"]:
                todo = await db.flow_botnet.find({"status": "active", "sub.found": {"$ne": True}, "sub_tries": {"$not": {"$gte": 2}}},
                                                 {"_id": 0}).sort("start", -1).to_list(20)

                async def one(i):
                    async with sem:
                        await _botnet_lookup(i, cfg)
                await asyncio.gather(*[one(i) for i in todo])
        except Exception as e:
            logger.warning(f"botnet lookup: {e}")
        await asyncio.sleep(30)


@api.get("/flow/botnet")
async def flow_botnet(user: dict = Depends(get_current_user), days: int = 7):
    cfg = await _botnet_cfg()
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))).isoformat()
    items = await db.flow_botnet.find({"$or": [{"status": "active"}, {"start": {"$gte": since}}]}, {"_id": 0}).sort("start", -1).to_list(1000)
    for i in items:
        i["label"] = botnet.KINDS.get(i["kind"], i["kind"])
        i["severity"] = botnet.SEVERITY.get(i["kind"], 1)
        if i.get("sub"):
            i["sub"] = {k: v for k, v in i["sub"].items() if k != "raw" or user.get("role") != "viewer"}
    items.sort(key=lambda i: (i["status"] != "active", bool(i.get("ack")), -i["severity"]))     # estável: mantém os mais novos primeiro
    exps = await db.flow_exporters.find({}, {"_id": 1, "kind": 1, "fps": 1, "last": 1}).to_list(2000)
    devs = await db.devices.find({}, {"_id": 0, "id": 1, "name": 1, "host": 1, "device_type": 1}).to_list(10000) if user.get("role") == "admin" else []
    feeds = await db.config.find_one({"key": "flow_botnet_feeds"}, {"_id": 0, "ips": 0}) or {}
    return {"settings": cfg if user.get("role") == "admin" else {"enabled": cfg["enabled"], "exporters": cfg["exporters"]},
            "status": await db.flow_live.find_one({"_id": "botnet"}, {"_id": 0}) or {}, "items": items,
            "kinds": botnet.KINDS, "scan_ports": {str(k): v for k, v in botnet.SCAN_PORTS.items()},
            "seen_exporters": [{"ip": e["_id"], "kind": e.get("kind"), "fps": e.get("fps"), "last": e.get("last")} for e in exps],
            "devices": devs, "lookup_defaults": botnet.LOOKUP_COMMANDS, "feeds": feeds, "can_manage": user.get("role") == "admin"}


@api.put("/flow/botnet/settings")
async def flow_botnet_settings(body: dict, _: dict = Depends(require_admin)):
    try:
        cfg = botnet.clean_settings(body or {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    if cfg["enabled"] and not cfg["exporters"]:
        raise HTTPException(400, "Escolha pelo menos um BNG (o IP que exporta o flow)")
    if cfg["enabled"] and not cfg["subscriber_prefixes"]:
        raise HTTPException(400, "Informe as faixas de IP dos assinantes")
    await db.config.update_one({"key": "flow_botnet"}, {"$set": cfg}, upsert=True)
    return cfg


@api.post("/flow/botnet/{iid}/lookup")
async def flow_botnet_lookup(iid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    inc = await db.flow_botnet.find_one({"id": iid}, {"_id": 0})
    if not inc:
        raise HTTPException(404, "Incidente não encontrado")
    return await _botnet_lookup(inc, await _botnet_cfg())


class BotnetBulkAckIn(BaseModel):
    ids: List[str]


@api.post("/flow/botnet/ack-bulk")
async def flow_botnet_ack_bulk(body: BotnetBulkAckIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    ids = list(dict.fromkeys(body.ids))[:2000]
    r = await db.flow_botnet.update_many({"id": {"$in": ids}}, {"$set": {"ack": True, "ack_by": user.get("email"),
                                                                        "ack_at": datetime.now(timezone.utc).isoformat()}})
    return {"ok": True, "count": r.modified_count}


class BotnetAckIn(BaseModel):
    ack: bool = True
    note: str = ""


@api.post("/flow/botnet/{iid}/ack")
async def flow_botnet_ack(iid: str, body: BotnetAckIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    r = await db.flow_botnet.update_one({"id": iid}, {"$set": {"ack": bool(body.ack), "ack_note": body.note.strip()[:300],
                                                               "ack_by": user.get("email"), "ack_at": datetime.now(timezone.utc).isoformat()}})
    if not r.matched_count:
        raise HTTPException(404, "Incidente não encontrado")
    return {"ok": True}


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
            transitions.append({"kind": "agent", "id": a["id"], "host": a.get("host", ""), "name": a["name"], "status": st,
                                "detail": f"{a.get('location', '')} · {'túnel reverso porta ' + str(a.get('tunnel_port')) if a.get('mode') == 'reverse' else a.get('host', '')}"})
    await asyncio.gather(*[_ag(a) for a in agents])

    devs = await db.devices.find({}, {"_id": 0}).to_list(5000)

    async def _dv(d):
        async with sem:
            lat = await _ping_device(d)
        st = "online" if lat is not None else "offline"
        await db.devices.update_one({"id": d["id"]}, {"$set": {"status": st, "latency_ms": lat, "last_seen": now if lat else d.get("last_seen")}})
        if d.get("status") in ("online", "offline") and d.get("status") != st:
            transitions.append({"kind": "device", "id": d["id"], "host": d["host"], "name": d["name"], "status": st,
                                "detail": f"{d['host']}:{d.get('port', 22)}"})
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
            out = res["stdout"] if isinstance(res["stdout"], str) else res["stdout"].decode("utf-8", "replace")
            # OLT Huawei (SmartAX): no modo usuário ('>') o display current-configuration não existe — entra no enable e repete
            if (dev.get("device_type") == "huawei" and not dev.get("backup_command") and len(out) < 2000
                    and re.search(r"unknown command|error locates|incomplete command|% ?parameter error", out, re.I)):
                res = await c.run_command("enable\ndisplay current-configuration", timeout=240)
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
# ---------- Roteiros automatizados (vários equipamentos em sequência; tela de Lote e bot do Telegram) ----------
_runbook_locks: dict = {}


def _runbook_scope(user: dict) -> dict:
    """Os do usuário e os que o dono compartilhou (o administrador vê todos)."""
    if user.get("role") == "admin":
        return {}
    return {"$or": [{"owner_id": user["id"]}, {"shared": True}]}


async def _runbook_for(user: dict, rid: str) -> dict:
    rb = await db.runbooks.find_one({"id": rid, **_runbook_scope(user)}, {"_id": 0})
    if not rb:
        raise HTTPException(status_code=404, detail="Roteiro não encontrado")
    return rb


async def _runbook_validate(user: dict, data: dict) -> dict:
    try:
        rb = runbooks.clean(data or {})
    except runbooks.RunbookError as e:
        raise HTTPException(status_code=400, detail=str(e))
    for i, st in enumerate(rb["steps"], 1):
        if not await db.devices.find_one({"id": st["device_id"], **_scope(user)}, {"_id": 0, "id": 1}):
            raise HTTPException(status_code=400, detail=f"Passo {i}: equipamento não encontrado entre os seus")
    return rb


async def run_runbook(rb: dict, user: dict, via: str) -> dict:
    """Executa (um de cada vez por roteiro) e guarda no histórico. Os equipamentos são os do dono do roteiro."""
    lock = _runbook_locks.setdefault(rb["id"], asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail=f"O roteiro {rb['name']} já está rodando — espere terminar")
    owner = rb.get("owner_id")

    async def get_dev(did):
        return await db.devices.find_one({"id": did, "owner_id": owner}, {"_id": 0})
    waiting = []
    for i, st in enumerate(rb.get("steps") or [], 1):
        dv = await get_dev(st.get("device_id"))
        d = await cmd_policy(user, dv, runbooks.command_lines(st.get("commands") or ""), "runbook")
        if d["action"] == "block":
            raise HTTPException(status_code=403, detail=f"Passo {i}: {d['message']}")
        if d["action"] == "approve":
            waiting.append(f"passo {i} (pedido #{d['approval']['number']})")
    if waiting:
        raise HTTPException(status_code=409, detail="Precisa de aprovação: " + ", ".join(waiting) +
                            ". Um administrador aprova em SOC → Aprovações ou no Telegram; depois rode de novo.")
    started = datetime.now(timezone.utc)
    async with lock:
        res = await runbooks.run(rb, get_dev, _connect_device)
    ended = datetime.now(timezone.utc)
    run_doc = {"id": os.urandom(8).hex(), "runbook_id": rb["id"], "name": rb["name"], "user_id": user["id"],
               "user_email": user.get("email", ""), "via": via, "ok": res["ok"], "started_at": started.isoformat(),
               "ended_at": ended.isoformat(), "steps": res["steps"]}
    await db.runbook_runs.insert_one(dict(run_doc))
    await db.runbooks.update_one({"id": rb["id"]}, {"$set": {"last_run": {"at": ended.isoformat(), "ok": res["ok"],
                                                                         "by": user.get("email", ""), "via": via}}})
    for st in res["steps"]:
        if not st["skipped"] and st.get("host"):
            await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user.get("email", ""),
                                          "device_id": st["device_id"], "device_name": f"{st['device']} (roteiro {rb['name']})",
                                          "started_at": started.isoformat(), "ended_at": ended.isoformat(),
                                          "duration_seconds": int((ended - started).total_seconds()), "kind": "batch",
                                          "commands": runbooks.command_lines(rb["steps"][st["index"] - 1]["commands"]), "ok": st["ok"]})
    logger.info("Roteiro %s executado por %s (%s): %s", rb["name"], user.get("email"), via, "ok" if res["ok"] else "FALHOU")
    return {**res, "run_id": run_doc["id"], "name": rb["name"], "started_at": run_doc["started_at"], "ended_at": run_doc["ended_at"]}


async def _runbooks_for_telegram(user: dict) -> list:
    return await db.runbooks.find({**_runbook_scope(user), "telegram": True}, {"_id": 0}).sort("name", 1).to_list(100)


async def _runbook_telegram_run(user: dict, rid: str) -> str:
    rb = await db.runbooks.find_one({"id": rid, **_runbook_scope(user), "telegram": True}, {"_id": 0})
    if not rb:
        return "Roteiro não encontrado (ou não liberado para o Telegram)."
    if user.get("role") == "viewer" or not auth_mod.has_module(user, "batch"):
        return "Seu usuário não tem acesso à Execução em Lote."
    try:
        res = await run_runbook(rb, user, "telegram")
    except HTTPException as e:
        return str(e.detail)
    return runbooks.summary(rb, res)


assistant.runbook_list = _runbooks_for_telegram
assistant.runbook_run = _runbook_telegram_run


@api.get("/runbooks")
async def runbook_list(user: dict = Depends(get_current_user)):
    rows = await db.runbooks.find(_runbook_scope(user), {"_id": 0}).sort("name", 1).to_list(500)
    names = {d["id"]: d["name"] async for d in db.devices.find({"id": {"$in": list({s["device_id"] for r in rows for s in r["steps"]})}},
                                                               {"_id": 0, "id": 1, "name": 1})}
    for r in rows:
        r["mine"] = r.get("owner_id") == user["id"]
        for s in r["steps"]:
            s["device_name"] = names.get(s["device_id"], "?")
    return rows


@api.post("/runbooks")
async def runbook_create(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    rb = await _runbook_validate(user, body)
    if await db.runbooks.find_one({"owner_id": user["id"], "name_key": runbooks.norm_name(rb["name"])}):
        raise HTTPException(status_code=400, detail="Já existe um roteiro com esse nome")
    rb.update({"id": str(uuid.uuid4()), "owner_id": user["id"], "owner_email": user.get("email", ""),
               "name_key": runbooks.norm_name(rb["name"]), "created_at": datetime.now(timezone.utc).isoformat()})
    await db.runbooks.insert_one(dict(rb))
    return rb


@api.put("/runbooks/{rid}")
async def runbook_update(rid: str, body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await _runbook_for(user, rid)
    if cur.get("owner_id") != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Só o dono do roteiro altera")
    owner = {"id": cur["owner_id"], "role": "operator"} if cur.get("owner_id") != user["id"] else user
    rb = await _runbook_validate(owner, body)
    dup = await db.runbooks.find_one({"owner_id": cur["owner_id"], "name_key": runbooks.norm_name(rb["name"]), "id": {"$ne": rid}})
    if dup:
        raise HTTPException(status_code=400, detail="Já existe um roteiro com esse nome")
    rb["name_key"] = runbooks.norm_name(rb["name"])
    await db.runbooks.update_one({"id": rid}, {"$set": rb})
    return {**cur, **rb}


@api.delete("/runbooks/{rid}")
async def runbook_delete(rid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await _runbook_for(user, rid)
    if cur.get("owner_id") != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Só o dono do roteiro apaga")
    await db.runbooks.delete_one({"id": rid})
    return {"ok": True}


@api.post("/runbooks/{rid}/run")
async def runbook_run(rid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    rb = await _runbook_for(user, rid)
    return await run_runbook(rb, user, "web")


@api.get("/runbooks/{rid}/runs")
async def runbook_runs(rid: str, user: dict = Depends(get_current_user)):
    await _runbook_for(user, rid)
    return await db.runbook_runs.find({"runbook_id": rid}, {"_id": 0}).sort("started_at", -1).to_list(10)


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


async def _snmp_check(dev: dict, save: bool = True, own_only: bool = False) -> dict:
    """Testa a community (sysName/sysDescr) e guarda no equipamento: snmp_status ok | fail | none (sem community própria)."""
    now = datetime.now(timezone.utc).isoformat()
    if own_only and not (dev.get("snmp_community") or "").strip():
        res = {"ok": None, "status": "none", "error": "sem community configurada no equipamento"}
    else:
        try:
            c = await _snmp_client(dev)
            v = await asyncio.wait_for(c.get([snmp_service.SYS_NAME, snmp_service.SYS_DESCR]), timeout=20)
            name, descr = v.get(snmp_service.SYS_NAME), v.get(snmp_service.SYS_DESCR)
            if not isinstance(name, str) and not isinstance(descr, str):
                raise snmp_service.SnmpError("sem resposta (community recusada ou SNMP desligado)")
            res = {"ok": True, "status": "ok", "sys_name": name if isinstance(name, str) else None,
                   "sys_descr": (descr if isinstance(descr, str) else "")[:200]}
        except asyncio.TimeoutError:
            res = {"ok": False, "status": "fail", "error": "SNMP não respondeu (timeout)"}
        except Exception as e:
            res = {"ok": False, "status": "fail", "error": (str(e) or type(e).__name__)[:200]}
    res["via_agent"] = bool(dev.get("agent_id"))
    res["checked_at"] = now
    if save:
        await db.devices.update_one({"id": dev["id"]}, {"$set": {
            "snmp_status": res["status"], "snmp_checked_at": now, "snmp_error": res.get("error") or "",
            "snmp_sys_name": res.get("sys_name") or ""}})
    return res


@api.post("/devices/{device_id}/snmp-test")
async def device_snmp_test(device_id: str, own_only: bool = False, user: dict = Depends(get_current_user)):
    dev = await _get_device_for(user, device_id)
    return await _snmp_check(dev, own_only=own_only)


@api.post("/devices/snmp-test-all")
async def devices_snmp_test_all(payload: DeviceIdsPayload, user: dict = Depends(get_current_user)):
    """Testa a community dos equipamentos escolhidos (só os que têm community própria; os outros ficam cinza)."""
    ids = list(dict.fromkeys(payload.device_ids))[:500]
    devs = await db.devices.find({"id": {"$in": ids}, **_scope(user)}, {"_id": 0}).to_list(500)
    sem = asyncio.Semaphore(12)

    async def one(d):
        async with sem:
            r = await _snmp_check(d, own_only=True)
            return {"id": d["id"], "name": d["name"], **r}
    return await asyncio.gather(*[one(d) for d in devs])


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


# ---------- Central de incidentes ----------
inc_center = incmod.Incidents(db, automation.send_alert)


async def _inc_internal(kind: str, data: dict):
    """Alarmes do próprio BastiON viram incidentes (equipamento/agente fora, interface caiu)."""
    s = await inc_center.settings()
    if not s.get("internal", True):
        return
    if kind == "transition":
        what = "Agente" if data["kind"] == "agent" else "Equipamento"
        ev = {"key": f"bastion:{data['kind']}:{data.get('id') or data['name']}", "status": "ok" if data["status"] == "online" else "problem",
              "severity": "critical" if data["kind"] == "agent" else "high", "title": f"{what} {data['name']} sem resposta",
              "host": data.get("host", ""), "host_name": data["name"], "description": data.get("detail", ""),
              "device_id": data.get("id") if data["kind"] == "device" else ""}
        if data["kind"] == "agent":
            ag = await db.agents.find_one({"id": data.get("id")}, {"_id": 0, "owner_id": 1}) if data.get("id") else None
            ev["owner_ids"] = [ag["owner_id"]] if ag and ag.get("owner_id") else []
    else:
        dev = data["device"]
        nm = f"{data['if_name']}" + (f" ({data['alias']})" if data.get("alias") else "")
        ev = {"key": f"bastion:if:{dev['id']}:{data['if_index']}", "status": "ok" if data["status"] == "up" else "problem",
              "severity": "high", "title": f"Interface {nm} DOWN", "host": dev.get("host", ""), "host_name": dev["name"],
              "description": f"status: {data.get('oper') or 'down'}", "device_id": dev["id"],
              "owner_ids": [dev["owner_id"]] if dev.get("owner_id") else []}
    await inc_center.ingest("bastion", ev)


automation.INCIDENT_HOOK = _inc_internal


async def _inc_scope(user: dict) -> dict:
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0, "id": 1, "role": 1, "incidents_scope": 1})
    return incmod.scope_filter(u or user)


async def _inc_reown():
    """Recalcula os donos dos incidentes ativos (depois de mudar as redes de algum usuário)."""
    try:
        async for i in db.incidents.find({"status": {"$in": ["open", "ack"]}}, {"_id": 0}):
            o = await inc_center.owners(i.get("host", ""), i.get("host_name") or i.get("device_name", ""), i.get("device_id") or "")
            await db.incidents.update_one({"id": i["id"]}, {"$set": {"owner_ids": sorted(set(o["owner_ids"]) | set(i.get("owner_ids") or []))}})
    except Exception as e:
        logger.warning(f"donos dos incidentes: {e}")


_inc_dm = incmod.make_dm(db)       # privado no Telegram de cada dono


inc_center.dm = _inc_dm
net_monitor.incident_hook = _inc_internal
secrets_compare = hmac.compare_digest


# Duas áreas: rede/backbone (Incidentes) e segurança (Painel SOC → Incidentes de segurança, origem "soc")
SOC_KINDS = {"ti": "^soc:ti", "certbr": "^soc:certbr:", "cmd": "^soc:cmd:", "login": "^soc:login", "cve": "^soc:cve:"}


def _inc_kind(key: str) -> str:
    k = str(key or "")
    return next((n for n, rx in SOC_KINDS.items() if re.match(rx, k)), "outro") if k.startswith("soc:") else ""


def _inc_area(area: str) -> dict:
    return {"source": "soc"} if area == "soc" else {"source": {"$ne": "soc"}} if area == "net" else {}


def _inc_public(i: dict, full: bool = False) -> dict:
    out = {k: v for k, v in i.items() if k not in ("_id", "key") and (full or k != "timeline")}
    out["duration_s"] = int(incmod.duration(i))
    if i.get("source") == "soc":
        out["kind"] = _inc_kind(i.get("key", ""))
    return out


async def _inc_get(inc_id: str, scope: Optional[dict] = None) -> dict:
    i = await db.incidents.find_one({"id": inc_id, **(scope or {})}, {"_id": 0})
    if not i:
        raise HTTPException(status_code=404, detail="Incidente não encontrado")
    return i


@api.get("/incidents")
async def incidents_list(status: str = "active", severity: str = "", source: str = "", q: str = "", limit: int = 200,
                         area: str = "", kind: str = "", user: dict = Depends(get_current_user)):
    _no_viewer(user)
    f: dict = {**(await _inc_scope(user)), **_inc_area(area)}
    if area == "soc" and kind in SOC_KINDS:
        f["key"] = {"$regex": SOC_KINDS[kind]}
    if status == "active":
        f["status"] = {"$in": ["open", "ack"]}
    elif status in incmod.STATUSES:
        f["status"] = status
    if severity in incmod.SEVERITIES:
        f["severity"] = severity
    if source in incmod.SOURCES:
        f["source"] = source
    if q.strip():
        rx = {"$regex": re.escape(q.strip()[:80]), "$options": "i"}
        f.setdefault("$and", []).append({"$or": [{"title": rx}, {"host": rx}, {"device_name": rx}, {"description": rx}] + \
                   ([{"number": int(q.strip().lstrip("#"))}] if q.strip().lstrip("#").isdigit() else [])})
    if source in incmod.SOURCES and area:
        f["source"] = source if (source == "soc") == (area == "soc") else "__nenhum__"
    rows = await db.incidents.find(f, {"_id": 0, "timeline": 0}).sort("created_at", -1).to_list(min(1000, max(1, limit)))
    if status == "active":   # ativos: mais graves e mais antigos primeiro
        rows.sort(key=lambda r: (-incmod.sev_rank(r["severity"]), r["status"] != "open", r["created_at"]))
    # grupos (tags) do equipamento, para agrupar a lista
    dids = list({r["device_id"] for r in rows if r.get("device_id")})
    tags = {d["id"]: d.get("tags") or [] async for d in db.devices.find({"id": {"$in": dids}}, {"_id": 0, "id": 1, "tags": 1})} if dids else {}
    out = []
    for r in rows:
        x = _inc_public(r)
        x["tags"] = tags.get(r.get("device_id"), [])
        out.append(x)
    return out


class IncidentBulk(BaseModel):
    ids: List[str]
    action: str
    note: str = ""


@api.post("/incidents-bulk")
async def incidents_bulk(body: IncidentBulk, user: dict = Depends(get_current_user)):
    """Reconhecer / assumir / resolver / reabrir vários incidentes de uma vez."""
    _no_viewer(user)
    if body.action not in ("ack", "resolve", "reopen", "assign"):
        raise HTTPException(status_code=400, detail="Ação desconhecida")
    ids = list(dict.fromkeys(body.ids))[:500]
    if not ids:
        raise HTTPException(status_code=400, detail="Nenhum incidente selecionado")
    who = user.get("name") or user["email"]
    me = {"id": user["id"], "name": who}
    scope = await _inc_scope(user)
    done, skipped = 0, 0
    for iid in ids:
        before = await db.incidents.find_one({"id": iid, **scope}, {"_id": 0, "status": 1, "assignee": 1})
        if not before:
            skipped += 1
            continue
        if (body.action == "ack" and before["status"] != "open") or (body.action == "resolve" and before["status"] == "resolved") \
                or (body.action == "reopen" and before["status"] != "resolved"):
            skipped += 1
            continue
        if await inc_center.act(iid, body.action, who, body.note.strip()[:2000], me, scope=scope):
            done += 1
    return {"done": done, "skipped": skipped}


@api.get("/incidents-summary")
async def incidents_summary(area: str = "", user: dict = Depends(get_current_user)):
    """Contadores para o menu e o Painel NOC. area=net|soc filtra; 'areas' traz as duas para os contadores do menu."""
    def blank():
        return {"open": 0, "ack": 0, "by_severity": {s: 0 for s in incmod.SEVERITIES}, "kinds": {}}
    areas = {"net": blank(), "soc": blank()}
    async for r in db.incidents.find({"status": {"$in": ["open", "ack"]}, **(await _inc_scope(user))},
                                     {"_id": 0, "status": 1, "severity": 1, "source": 1, "key": 1}):
        a = areas["soc" if r.get("source") == "soc" else "net"]
        a[r["status"]] += 1
        a["by_severity"][r["severity"]] = a["by_severity"].get(r["severity"], 0) + 1
        if r.get("source") == "soc":
            k = _inc_kind(r.get("key", ""))
            a["kinds"][k] = a["kinds"].get(k, 0) + 1
    for a in areas.values():
        a["active"] = a["open"] + a["ack"]
        a["worst"] = next((s for s in reversed(incmod.SEVERITIES) if a["by_severity"].get(s)), None)
    if area in areas:
        out = dict(areas[area])
    else:
        out = {"open": areas["net"]["open"] + areas["soc"]["open"], "ack": areas["net"]["ack"] + areas["soc"]["ack"],
               "by_severity": {s: areas["net"]["by_severity"][s] + areas["soc"]["by_severity"][s] for s in incmod.SEVERITIES}}
        out["active"] = out["open"] + out["ack"]
        out["worst"] = next((s for s in reversed(incmod.SEVERITIES) if out["by_severity"].get(s)), None)
    out["areas"] = {k: {x: v[x] for x in ("active", "open", "ack", "worst")} for k, v in areas.items()}
    return out


@api.get("/incidents/{inc_id}")
async def incidents_get(inc_id: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return _inc_public(await _inc_get(inc_id, await _inc_scope(user)), full=True)


class IncidentIn(BaseModel):
    title: str
    severity: str = "medium"
    description: str = ""
    device_id: Optional[str] = None


@api.post("/incidents")
async def incidents_create(body: IncidentIn, user: dict = Depends(get_current_user)):
    """Incidente aberto à mão (cliente ligou, problema na operadora…)."""
    _no_viewer(user)
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Dê um título ao incidente")
    dev = await db.devices.find_one({"id": body.device_id}, {"_id": 0, "name": 1, "host": 1}) if body.device_id else None
    r = await inc_center.ingest("manual", {"key": f"manual:{os.urandom(6).hex()}", "status": "problem",
                                           "severity": incmod.norm_sev(body.severity), "title": body.title.strip()[:300],
                                           "description": body.description[:4000], "host": (dev or {}).get("host", ""),
                                           "host_name": (dev or {}).get("name", ""), "device_id": body.device_id or "",
                                           "owner_ids": [user["id"]]})
    inc = r["incident"]
    await db.incidents.update_one({"id": inc["id"]}, {"$set": {"timeline.0.by": user.get("name") or user["email"]}})
    return _inc_public(await _inc_get(inc["id"]), full=True)


class IncidentAct(BaseModel):
    note: str = ""
    user_id: Optional[str] = None       # assign: para quem (vazio = eu)


@api.post("/incidents/{inc_id}/{action}")
async def incidents_act(inc_id: str, action: str, body: IncidentAct, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    if action not in ("ack", "resolve", "reopen", "assign", "comment"):
        raise HTTPException(status_code=404, detail="Ação desconhecida")
    if action == "comment" and not body.note.strip():
        raise HTTPException(status_code=400, detail="Escreva o comentário")
    who = user.get("name") or user["email"]
    me = {"id": user["id"], "name": who}
    assignee = me
    if action == "assign" and body.user_id and body.user_id != user["id"]:
        u = await db.users.find_one({"id": body.user_id}, {"_id": 0, "id": 1, "name": 1, "email": 1})
        if not u:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        assignee = {"id": u["id"], "name": u.get("name") or u["email"]}
    inc = await inc_center.act(inc_id, action, who, body.note.strip()[:2000], assignee, scope=await _inc_scope(user))
    if not inc:
        raise HTTPException(status_code=404, detail="Incidente não encontrado")
    return _inc_public(inc, full=True)


@api.get("/incidents-assignees")
async def incidents_assignees(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return [{"id": u["id"], "name": u.get("name") or u["email"]} for u in
            await db.users.find({"role": {"$ne": "viewer"}}, {"_id": 0, "id": 1, "name": 1, "email": 1}).to_list(500)]


class IncidentSettingsIn(BaseModel):
    notify_min_severity: str = "high"
    notify_resolved: bool = True
    internal: bool = True
    notify_internal: bool = False
    notify_owners_private: bool = False


async def _inc_settings_public() -> dict:
    s = await inc_center.settings()
    if not s.get("token"):
        s["token"] = os.urandom(18).hex()
        await db.config.update_one({"key": "incidents"}, {"$set": {"token": s["token"]}}, upsert=True)
    return s


@api.get("/incidents-settings")
async def incidents_settings(_: dict = Depends(require_admin)):
    return await _inc_settings_public()


@api.put("/incidents-settings")
async def incidents_settings_put(body: IncidentSettingsIn, _: dict = Depends(require_admin)):
    if body.notify_min_severity not in incmod.SEVERITIES:
        raise HTTPException(status_code=400, detail="Severidade inválida")
    await db.config.update_one({"key": "incidents"}, {"$set": body.model_dump()}, upsert=True)
    return await _inc_settings_public()


@api.post("/incidents-settings/rotate-token")
async def incidents_rotate(_: dict = Depends(require_admin)):
    await db.config.update_one({"key": "incidents"}, {"$set": {"token": os.urandom(18).hex()}}, upsert=True)
    return await _inc_settings_public()


@api.post("/hooks/incidents/{token}/{source}")
async def incidents_hook(token: str, source: str, request: Request):
    """Entrada dos alarmes externos (sem login: a URL tem o token). Zabbix, LibreNMS ou genérico."""
    s = await inc_center.settings()
    if not s.get("token") or not secrets_compare(token, s["token"]):
        raise HTTPException(status_code=403, detail="Token inválido")
    if source not in ("zabbix", "librenms", "generic"):
        raise HTTPException(status_code=404, detail="Origem desconhecida (use zabbix, librenms ou generic)")
    try:
        body = await request.json()
    except Exception:
        form = await request.form()
        body = dict(form)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Mande um objeto JSON")
    r = await inc_center.ingest(source, incmod.parse_event(source, body))
    inc = r["incident"] or {}
    return {"ok": True, "action": r["action"], "number": inc.get("number"), "id": inc.get("id")}


# ---------- Plantão e escalonamento ----------
async def _oncall_cfg() -> dict:
    doc = await db.config.find_one({"key": "oncall"}, {"_id": 0}) or {}
    return {**oncallmod.DEFAULTS, **{k: v for k, v in doc.items() if k != "key"}}


async def _user_names(ids) -> Dict[str, str]:
    us = await db.users.find({"id": {"$in": list(ids)}}, {"_id": 0, "id": 1, "name": 1, "email": 1}).to_list(500)
    return {u["id"]: u.get("name") or u["email"] for u in us}


async def _oncall_view(cfg: dict) -> dict:
    now = datetime.now(timezone.utc)
    sh = oncallmod.shifts(cfg, now, 8)
    ids = set(cfg["members"]) | {o["user_id"] for o in cfg["overrides"]} | \
          {l["target"][5:] for l in cfg["levels"] if l["target"].startswith("user:")}
    names = await _user_names(ids)
    cur = oncallmod.who(cfg, now)
    _, _, end = oncallmod._slot(cfg, now)
    ov = oncallmod.override_at(cfg, now)
    if ov:
        end = oncallmod._parse_local(ov["end"], cfg)
    tg = {l["user_id"] for l in ((await db.config.find_one({"key": "ai"}, {"_id": 0, "ai_users": 1})) or {}).get("ai_users") or []}
    return {**cfg, "names": names, "now": {"user_id": cur, "name": names.get(cur), "until": oncallmod._local(end, cfg).isoformat(),
                                           "override": ov, "next": oncallmod.next_after(cfg, now)},
            "shifts": sh, "telegram_ok": sorted(i for i in ids if i in tg)}


@api.get("/oncall")
async def oncall_get(user: dict = Depends(get_current_user)):
    """Quem está de plantão agora, os próximos turnos e as regras de escalonamento."""
    _no_viewer(user)
    return await _oncall_view(await _oncall_cfg())


@api.put("/oncall")
async def oncall_put(body: dict, _: dict = Depends(require_admin)):
    users = {u["id"] for u in await db.users.find({"role": {"$ne": "viewer"}}, {"_id": 0, "id": 1}).to_list(1000)}
    try:
        cfg = oncallmod.clean(body or {}, users, incmod.SEVERITIES)
    except (oncallmod.OncallError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e) or "Dados inválidos")
    prev = await _oncall_cfg()
    # ao ligar, só incidentes abertos daqui para frente escalam (os antigos não disparam todos os níveis de uma vez)
    cfg["enabled_at"] = (prev.get("enabled_at") if prev.get("enabled") else None) or (incmod.now_iso() if cfg["enabled"] else None)
    await db.config.update_one({"key": "oncall"}, {"$set": cfg}, upsert=True)
    return await _oncall_view(cfg)


async def _escalate(inc: dict, level_idx: int, cfg: dict) -> List[str]:
    lv = cfg["levels"][level_idx]
    uids = [u for u in oncallmod.targets_for(cfg, lv) if u]
    names = await _user_names(uids)
    now = incmod.now_iso()
    label = {"oncall": "plantão", "next": "próximo da escala", "all": "toda a escala"}.get(lv["target"], "usuário")
    sev = inc["severity"]
    title = f"📟 {incmod.SEV_ICON.get(sev, '')} #{inc['number']} {incmod.SEV_LABEL.get(sev, sev).upper()} — {inc['title']}"
    text = "\n".join(x for x in [
        f"Você foi acionado ({label}{', nível ' + str(level_idx + 1) if level_idx else ''}).",
        (inc.get("device_name") or inc.get("host")) and f"📍 {inc.get('device_name') or inc.get('host')}",
        f"Aberto há {incmod.fmt_dur(incmod.duration(inc))} e ainda sem reconhecimento." if level_idx else "",
        (inc.get("description") or "")[:400]] if x)
    buttons = [[{"text": "👀 Reconhecer", "callback_data": f"inc:ack:{inc['id']}"},
                {"text": "✅ Resolver", "callback_data": f"inc:res:{inc['id']}"}]]
    if uids:
        await _inc_dm(uids, title, text, buttons)
        for u in uids:
            try:
                await webpush.send(db, title, text, url=f"/incidents?id={inc['id']}", user_id=u, tag=f"inc-{inc['id']}")
            except Exception:
                pass
    who = ", ".join(names.get(u, u) for u in uids) or "ninguém (escala vazia)"
    await db.incidents.update_one({"id": inc["id"]}, {
        "$addToSet": {"escalated": level_idx, "watchers": {"$each": uids}},
        "$push": {"timeline": {"at": now, "by": "Plantão", "type": "escalate",
                               "text": f"Acionado: {who} ({label}{', nível ' + str(level_idx + 1) if level_idx else ''})"}}})
    return uids


async def _oncall_loop():
    """A cada 30 s: incidentes abertos (sem reconhecimento) que passaram do tempo de cada nível acionam o próximo."""
    await asyncio.sleep(20)
    while True:
        try:
            cfg = await _oncall_cfg()
            if cfg.get("enabled") and cfg.get("levels"):
                minrank = incmod.sev_rank(cfg.get("min_severity", "high"))
                since = cfg.get("enabled_at") or incmod.now_iso()
                async for inc in db.incidents.find({"status": "open", "created_at": {"$gte": since}, "maintenance": {"$exists": False}}, {"_id": 0, "timeline": 0}):
                    if incmod.sev_rank(inc["severity"]) < minrank:
                        continue
                    for idx in oncallmod.due_levels(cfg, inc):
                        await _escalate(inc, idx, cfg)
                        inc.setdefault("escalated", []).append(idx)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"plantão: {e}")
        await asyncio.sleep(30)


async def _oncall_text(user: dict) -> str:
    """/plantao no Telegram."""
    v = await _oncall_view(await _oncall_cfg())
    if not v["members"]:
        return "Ainda não há escala de plantão. Configure em Incidentes → Plantão."
    n = v["now"]
    lines = [f"📟 De plantão agora: {n['name'] or '—'} (até {datetime.fromisoformat(n['until']).strftime('%d/%m %H:%M')})"]
    if n.get("override"):
        lines.append(f"   troca pontual{': ' + n['override']['note'] if n['override'].get('note') else ''}")
    if n.get("next"):
        lines.append(f"Próximo se ninguém responder: {v['names'].get(n['next'], '—')}")
    lines.append("")
    for s_ in v["shifts"][1:5]:
        lines.append(f"• {datetime.fromisoformat(s_['start']).strftime('%d/%m %H:%M')} — {v['names'].get(s_['user_id'], '—')}")
    if not v["enabled"]:
        lines.append("\n(escalonamento desligado)")
    return "\n".join(lines)


async def _inc_telegram(user: dict, action: str, inc_id: str) -> str:
    """Botões do Telegram (Reconhecer / Resolver) no alerta do incidente."""
    who = user.get("name") or user["email"]
    act = {"ack": "ack", "res": "resolve"}.get(action)
    if not act:
        return "Ação desconhecida."
    inc = await inc_center.act(inc_id, act, f"{who} (Telegram)", "", {"id": user["id"], "name": who},
                               scope=incmod.scope_filter(user))
    if not inc:
        return "Incidente não encontrado."
    return f"{'👀 Reconhecido' if act == 'ack' else '✅ Resolvido'} por {who}: #{inc['number']} {inc['title']}"


assistant.incident_action = _inc_telegram
assistant.oncall_text = _oncall_text


# ---------- Configurações → Banco de dados (backup / importação) ----------
_bg_tasks: list = []
DB_JOB: dict = {"state": "idle"}           # um backup ou importação por vez
_DL_TICKETS: Dict[str, tuple] = {}         # link de download de uso único: ticket -> (arquivo, validade)
_DB_UPLOADS: Dict[str, dict] = {}          # arquivo enviado aguardando confirmação


async def _db_cloud_upload(path: str):
    return await cloudsync.upload_file(db, path)


async def _db_alert(title: str, text: str):
    try:
        await automation.send_alert(db, title, text, push_url="/settings?tab=banco", push_tag="db-backup")
    except Exception:
        pass


def _job_public() -> dict:
    return {k: v for k, v in DB_JOB.items() if not k.startswith("_")}


def _db_busy():
    if DB_JOB.get("state") == "running":
        raise HTTPException(status_code=409, detail=f"Já existe {DB_JOB.get('what', 'uma operação')} em andamento")


async def _db_settings() -> dict:
    s = await db.config.find_one({"key": "dbbackup"}, {"_id": 0}) or {}
    return {"auto": bool(s.get("auto")), "hour": int(s.get("hour", 3)), "keep": int(s.get("keep", 7) or 7),
            "history": bool(s.get("history")), "config_backups": bool(s.get("config_backups", True)),
            "cloud": bool(s.get("cloud")), "has_password": bool(s.get("password")),
            "last_auto_at": s.get("last_auto_at"), "last_auto_ok": s.get("last_auto_ok"),
            "last_auto_error": s.get("last_auto_error") or "", "last_cloud_ok": s.get("last_cloud_ok"),
            "last_cloud_error": s.get("last_cloud_error") or ""}


@api.get("/system/db")
async def system_db(_: dict = Depends(require_admin)):
    """Coleções do banco (com o grupo de cada uma), backups guardados no servidor, agenda e operação em andamento."""
    cols = []
    for c in sorted(await db.list_collection_names()):
        g = dbbackup.classify(c)
        if g == "never":
            continue
        try:
            st = await db.command("collStats", c)
            size, n = int(st.get("size", 0)), int(st.get("count", 0))
        except Exception:
            size, n = 0, await db[c].estimated_document_count()
        cols.append({"name": c, "group": g, "docs": n, "size": size})
    try:
        dbstats = await db.command("dbStats")
    except Exception:
        dbstats = {}
    return {"collections": cols, "files": dbbackup.list_files(), "settings": await _db_settings(), "job": _job_public(),
            "dir": dbbackup.BACKUP_DIR, "data_size": int(dbstats.get("dataSize", 0)),
            "storage_size": int(dbstats.get("storageSize", 0)), "cloud": await cloudsync.status(db)}


class DbBackupIn(BaseModel):
    password: str
    history: bool = False
    metrics: bool = False
    config_backups: bool = True


async def _run_job(what: str, coro_factory):
    DB_JOB.clear()
    DB_JOB.update({"state": "running", "what": what, "started_at": datetime.now(timezone.utc).isoformat(), "step": "", "docs": 0})

    def progress(col, n):
        DB_JOB.update({"step": col, "docs": n})
    try:
        res = await coro_factory(progress)
        DB_JOB.update({"state": "done", "result": res, "ended_at": datetime.now(timezone.utc).isoformat()})
    except dbbackup.BackupError as e:
        DB_JOB.update({"state": "error", "error": str(e)})
    except Exception as e:
        logger.exception(f"{what} falhou")
        DB_JOB.update({"state": "error", "error": f"{type(e).__name__}: {e}"[:400]})


@api.post("/system/db-backups")
async def system_db_backup(body: DbBackupIn, user: dict = Depends(require_admin)):
    _db_busy()
    if len(body.password or "") < 8:
        raise HTTPException(status_code=400, detail="A senha do backup precisa ter pelo menos 8 caracteres")
    asyncio.create_task(_run_job("um backup", lambda pr: dbbackup.export(
        db, body.password, history=body.history, metrics=body.metrics, config_backups=body.config_backups,
        kind="manual", by=user.get("email", ""), progress=pr)))
    await asyncio.sleep(0.05)
    return _job_public()


@api.get("/system/db-job")
async def system_db_job(_: dict = Depends(require_admin)):
    return _job_public()


@api.post("/system/db-backups/{name}/link")
async def system_db_link(name: str, _: dict = Depends(require_admin)):
    """Link de download de uso único (60 s): o navegador baixa direto, sem passar o arquivo pela memória da página."""
    try:
        path = dbbackup.path_of(name)
    except dbbackup.BackupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    now = time.time()
    for k, (_, exp) in list(_DL_TICKETS.items()):
        if exp < now:
            _DL_TICKETS.pop(k, None)
    t = os.urandom(24).hex()
    _DL_TICKETS[t] = (path, now + 60)
    return {"url": f"/api/system/db-download/{t}"}


@api.get("/system/db-download/{ticket}")
async def system_db_download(ticket: str):
    path, exp = _DL_TICKETS.pop(ticket, (None, 0))
    if not path or exp < time.time() or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Link expirado — clique em baixar de novo")
    return FileResponse(path, media_type="application/octet-stream", filename=os.path.basename(path))


@api.delete("/system/db-backups/{name}")
async def system_db_delete(name: str, _: dict = Depends(require_admin)):
    try:
        dbbackup.remove(name)
    except dbbackup.BackupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


def _restore_preview(m: dict) -> dict:
    c = m.get("collections") or {}
    return {"from_host": m.get("host"), "created_at": m.get("created_at"), "groups": m.get("groups"),
            "docs": sum(c.values()), "devices": c.get("devices", 0), "agents": c.get("agents", 0),
            "users": c.get("users", 0), "maps": c.get("maps", 0), "collections": c,
            "same_server": m.get("vault_key", "").encode() == dbbackup.vault_key()}


@api.post("/system/db-restore/upload")
async def system_db_restore_upload(file: UploadFile = File(...), password: str = Form(...), _: dict = Depends(require_admin)):
    """1º passo da importação: recebe o arquivo, confere a senha e mostra o que tem nele (nada é alterado ainda)."""
    _db_busy()
    dbbackup.ensure_dir()
    uid = os.urandom(8).hex()
    path = os.path.join(dbbackup.BACKUP_DIR, f".upload-{uid}.bastionbk")
    with open(path, "wb") as out:
        os.chmod(path, 0o600)
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    try:
        m = await asyncio.get_running_loop().run_in_executor(None, dbbackup.read_manifest, path, password)
    except dbbackup.BackupError as e:
        os.remove(path)
        raise HTTPException(status_code=400, detail=str(e))
    for k, u in list(_DB_UPLOADS.items()):                 # descarta envios antigos não confirmados
        if time.time() - u["at"] > 3600:
            _DB_UPLOADS.pop(k, None)
            try:
                os.remove(u["path"])
            except FileNotFoundError:
                pass
    _DB_UPLOADS[uid] = {"path": path, "at": time.time(), "name": file.filename}
    return {"upload_id": uid, "file": file.filename, **_restore_preview(m)}


class DbRestoreIn(BaseModel):
    password: str
    upload_id: Optional[str] = None     # arquivo enviado no 1º passo
    name: Optional[str] = None          # ou um backup que já está no servidor


@api.post("/system/db-restore/preview")
async def system_db_restore_preview(body: DbRestoreIn, _: dict = Depends(require_admin)):
    try:
        m = await asyncio.get_running_loop().run_in_executor(None, dbbackup.read_manifest, dbbackup.path_of(body.name or ""), body.password)
    except dbbackup.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"name": body.name, **_restore_preview(m)}


@api.post("/system/db-restore")
async def system_db_restore(body: DbRestoreIn, user: dict = Depends(require_admin)):
    """2º passo: guarda uma cópia do banco atual (mesma senha), importa e reinicia o backend."""
    _db_busy()
    if body.upload_id:
        up = _DB_UPLOADS.get(body.upload_id)
        if not up:
            raise HTTPException(status_code=404, detail="Envio expirado — mande o arquivo de novo")
        path = up["path"]
    else:
        try:
            path = dbbackup.path_of(body.name or "")
        except dbbackup.BackupError as e:
            raise HTTPException(status_code=404, detail=str(e))
    try:
        await asyncio.get_running_loop().run_in_executor(None, dbbackup.read_manifest, path, body.password)
    except dbbackup.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))

    async def work(progress):
        DB_JOB["step"] = "cópia do banco atual"
        safety = await dbbackup.export(db, body.password, history=True, metrics=False, config_backups=True,
                                       kind="pre-import", by=user.get("email", ""))
        res = await dbbackup.restore(db, path, body.password, progress=progress)
        res["safety_backup"] = safety["name"]
        if body.upload_id:
            _DB_UPLOADS.pop(body.upload_id, None)
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        logger.warning(f"banco importado por {user.get('email')}: {res['docs']} documentos de {res.get('from_host')}; reiniciando")
        res["restarting"] = True
        # reinicia para recarregar agenda, bot, monitores e caches com o banco novo (o Docker sobe de novo sozinho)
        asyncio.get_running_loop().call_later(4, os._exit, 0)
        return res
    asyncio.create_task(_run_job("uma importação", work))
    await asyncio.sleep(0.05)
    return _job_public()


class DbSettingsIn(BaseModel):
    auto: bool = False
    hour: int = 3
    keep: int = 7
    history: bool = False
    config_backups: bool = True
    cloud: bool = False
    password: Optional[str] = None      # vazio = mantém a guardada


@api.put("/system/db-settings")
async def system_db_settings(body: DbSettingsIn, _: dict = Depends(require_admin)):
    cur = await db.config.find_one({"key": "dbbackup"}, {"_id": 0}) or {}
    data = {"auto": body.auto, "hour": min(23, max(0, body.hour)), "keep": min(90, max(1, body.keep)),
            "history": body.history, "config_backups": body.config_backups, "cloud": body.cloud}
    if body.password:
        if len(body.password) < 8:
            raise HTTPException(status_code=400, detail="A senha do backup precisa ter pelo menos 8 caracteres")
        data["password"] = vault.encrypt(body.password)
    if body.auto and not (data.get("password") or cur.get("password")):
        raise HTTPException(status_code=400, detail="Defina a senha dos backups automáticos")
    await db.config.update_one({"key": "dbbackup"}, {"$set": data}, upsert=True)
    return await _db_settings()


# ---------- Configurações → Domínio ----------
class DomainsIn(BaseModel):
    domains: List[str] = []


def _domain_state() -> dict:
    return {"domains": webdomain.read_domains(), "caddy": webdomain.caddy_status(),
            "env_domain": os.environ.get("BASTION_DOMAIN", ""), "public_url": os.environ.get("PUBLIC_URL", ""),
            "https_host": os.environ.get("BASTION_HTTPS_HOST", "")}


@api.get("/system/domain")
async def system_domain(_: dict = Depends(require_admin)):
    return {**_domain_state(), "server_ip": await webdomain.public_ip()}


@api.put("/system/domain")
async def system_domain_put(body: DomainsIn, user: dict = Depends(require_admin)):
    try:
        doms = webdomain.write_domains(body.domains)
    except webdomain.DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"domínios do painel: {doms} (por {user.get('email')})")
    return {**_domain_state(), "server_ip": await webdomain.public_ip()}


class DomainCheckIn(BaseModel):
    domain: str


@api.post("/system/domain/check")
async def system_domain_check(body: DomainCheckIn, _: dict = Depends(require_admin)):
    try:
        return await webdomain.check(body.domain)
    except webdomain.DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))


@api.get("/system/domain-probe/{nonce}")
async def system_domain_probe(nonce: str):
    """Sem login: o teste do domínio pede esta URL pelo próprio domínio para saber se chega neste BastiON."""
    if not webdomain.probe_ok(nonce):
        raise HTTPException(status_code=404, detail="not found")
    return PlainTextResponse(nonce)


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
    name: Optional[str] = None          # nome mostrado na aba (páginas salvas)


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
        s = await web_proxy.open(user, payload.url, agent_id=agent_id, device_id=device_id,
                                 label=_web_name(payload.name) or label)
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


def _web_name(v: Optional[str]) -> str:
    return re.sub(r"[\r\n<>]", " ", str(v or "")).strip()[:60]


class WebSavedIn(BaseModel):
    name: str
    url: str
    device_id: Optional[str] = None
    agent_id: Optional[str] = None      # vazio e sem equipamento = direto do servidor (admin)


async def _web_saved_check(body: WebSavedIn, user: dict) -> dict:
    name = _web_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="Dê um nome para a página")
    url = (body.url or "").strip()
    try:
        webproxy.parse_target(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    device_id, agent_id = body.device_id or None, body.agent_id or None
    if device_id:
        await _get_device_for(user, device_id)
        agent_id = None
    elif agent_id:
        await _get_agent_for(user, agent_id)
    elif user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Abrir direto do servidor é só para administradores — escolha um agente")
    return {"name": name, "url": url[:2000], "device_id": device_id, "agent_id": agent_id}


@api.get("/web/saved")
async def web_saved_list(user: dict = Depends(get_current_user)):
    rows = await db.web_saved.find({"owner_id": user["id"]}, {"_id": 0, "owner_id": 0}).to_list(500)
    return sorted(rows, key=lambda r: r["name"].lower())


@api.post("/web/saved")
async def web_saved_add(body: WebSavedIn, user: dict = Depends(get_current_user)):
    doc = await _web_saved_check(body, user)
    if await db.web_saved.count_documents({"owner_id": user["id"]}) >= 500:
        raise HTTPException(status_code=400, detail="Limite de 500 páginas salvas")
    doc.update({"id": str(uuid.uuid4()), "owner_id": user["id"], "created_at": datetime.now(timezone.utc).isoformat()})
    await db.web_saved.insert_one(dict(doc))
    doc.pop("owner_id", None)
    return doc


@api.put("/web/saved/{sid}")
async def web_saved_edit(sid: str, body: WebSavedIn, user: dict = Depends(get_current_user)):
    doc = await _web_saved_check(body, user)
    r = await db.web_saved.update_one({"id": sid, "owner_id": user["id"]}, {"$set": doc})
    if not r.matched_count:
        raise HTTPException(status_code=404, detail="Página salva não encontrada")
    return {**doc, "id": sid}


@api.delete("/web/saved/{sid}")
async def web_saved_del(sid: str, user: dict = Depends(get_current_user)):
    r = await db.web_saved.delete_one({"id": sid, "owner_id": user["id"]})
    if not r.deleted_count:
        raise HTTPException(status_code=404, detail="Página salva não encontrada")
    return {"ok": True}


@api.delete("/web/sessions/{sid}")
async def web_close(sid: str, user: dict = Depends(get_current_user)):
    s = web_proxy.sessions.get(sid)
    if not s or (s.user_id != user["id"] and user.get("role") != "admin"):
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    await web_proxy.close(sid, reason="fechada pelo usuário")
    return {"ok": True}


# ---------- Túnel Winbox (TCP 8291 do MikroTik pelo BastiON, inclusive pelos jump servers) ----------
async def _relay_closed(s, reason: str):
    if s.history_id:
        ended = datetime.now(timezone.utc)
        await db.sessions.update_one({"id": s.history_id}, {"$set": {
            "ended_at": ended.isoformat(), "duration_seconds": int(ended.timestamp() - s.created)}})


winbox_relay = tcprelay.RelayManager(_web_resolve, _web_connect, on_close=_relay_closed)


class WinboxOpenIn(BaseModel):
    device_id: Optional[str] = None
    host: Optional[str] = None          # sem equipamento: IP/nome e agente (vazio = direto, só admin)
    port: int = 8291
    agent_id: Optional[str] = None


@api.get("/winbox/info")
async def winbox_info(request: Request, _: dict = Depends(get_current_user)):
    return {"ports": winbox_relay.ports, "enabled": bool(winbox_relay.ports), "your_ip": security.client_ip(request),
            "idle_minutes": tcprelay.IDLE_SECONDS // 60, "max_hours": tcprelay.MAX_SECONDS // 3600}


@api.get("/winbox/sessions")
async def winbox_sessions(user: dict = Depends(get_current_user)):
    return winbox_relay.list(None if user.get("role") == "admin" else user)


@api.post("/winbox/sessions")
async def winbox_open(body: WinboxOpenIn, request: Request, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    label, agent_id, device_id = "", body.agent_id or None, None
    port = int(body.port or 8291)
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="Porta inválida")
    if body.device_id:
        dev = await _get_device_for(user, body.device_id)
        device_id, label, agent_id, host = dev["id"], dev["name"], dev.get("agent_id") or None, dev["host"]
        port = int(dev.get("winbox_port") or body.port or 8291)
    else:
        host = (body.host or "").strip()
        if not host or len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9.:_-]+", host):
            raise HTTPException(status_code=400, detail="Informe o IP ou nome do equipamento")
        if agent_id:
            await _get_agent_for(user, agent_id)
        elif user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Sair direto do servidor é só para administradores — escolha um agente")
    if not agent_id and webproxy.is_loopback(host):
        raise HTTPException(status_code=403, detail="Endereço local do próprio servidor não é permitido")
    ip = security.client_ip(request)
    try:
        s = await winbox_relay.open(user, host, port, ip, agent_id=agent_id, device_id=device_id, label=label)
    except VpnDown as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e) or type(e).__name__)
    if not s.history_id:
        s.history_id = os.urandom(8).hex()
        await db.sessions.insert_one({"id": s.history_id, "user_id": user["id"], "user_email": user["email"], "device_id": device_id,
                                      "device_name": f"Winbox {label or host} ({host}:{port}) via {s.via}",
                                      "started_at": datetime.now(timezone.utc).isoformat(), "ended_at": None,
                                      "duration_seconds": None, "kind": "winbox"})
    if device_id and port != 8291:
        await db.devices.update_one({"id": device_id}, {"$set": {"winbox_port": port}})
    return s.public()


@api.delete("/winbox/sessions/{sid}")
async def winbox_close(sid: str, user: dict = Depends(get_current_user)):
    s = winbox_relay.sessions.get(sid)
    if not s or (s.user_id != user["id"] and user.get("role") != "admin"):
        raise HTTPException(status_code=404, detail="Túnel não encontrado")
    await winbox_relay.close(sid, reason="fechado pelo usuário")
    return {"ok": True}


# ---------- SOC: política de comandos e aprovação ----------
_cmdpol_cache: dict = {"at": 0.0, "rules": [], "cfg": None}
_cmd_waiters: Dict[str, asyncio.Event] = {}


async def _cmdpol_cfg() -> dict:
    doc = await db.config.find_one({"key": "cmdpolicy"}, {"_id": 0}) or {}
    return {**cmdpolicy.DEFAULTS, **{k: v for k, v in doc.items() if k in cmdpolicy.DEFAULTS}}


async def _cmdpol_rules(force: bool = False) -> tuple:
    if force or time.time() - _cmdpol_cache["at"] > 15 or _cmdpol_cache["cfg"] is None:
        _cmdpol_cache["rules"] = await db.cmd_rules.find({"enabled": {"$ne": False}}, {"_id": 0}).sort("order", 1).to_list(500)
        _cmdpol_cache["cfg"] = await _cmdpol_cfg()
        _cmdpol_cache["at"] = time.time()
    return _cmdpol_cache["rules"], _cmdpol_cache["cfg"]


async def _cmdpol_event(user: dict, dev: Optional[dict], channel: str, action: str, hits: list, **extra) -> dict:
    ev = {"id": os.urandom(8).hex(), "at": datetime.now(timezone.utc).isoformat(), "user_id": user.get("id"),
          "user_email": user.get("email", ""), "device_id": (dev or {}).get("id"), "device_name": (dev or {}).get("name", ""),
          "host": (dev or {}).get("host", ""), "channel": channel, "action": action,
          "commands": [h["command"] for h in hits][:50], "rules": sorted({h["rule"] for h in hits}), **extra}
    await db.cmd_events.insert_one(dict(ev))
    return ev


async def _cmdpol_incident(ev: dict, hits: list, kind: str):
    sev = max((h.get("severity", "medium") for h in hits), key=lambda s: incmod.sev_rank(s), default="medium")
    what = {"block": "Comando bloqueado", "alert": "Comando sensível", "denied": "Aprovação negada"}.get(kind, kind)
    try:
        await inc_center.ingest("soc", {"key": f"soc:cmd:{ev['id']}", "status": "problem", "severity": sev,
                                        "device_id": ev.get("device_id"), "host": ev.get("host", ""), "host_name": ev.get("device_name", ""),
                                        "title": f"{what}: {ev['commands'][0][:80] if ev['commands'] else ''} — {ev['user_email']} em {ev['device_name'] or '?'}",
                                        "description": f"Canal: {ev['channel']}. Regra(s): {', '.join(ev['rules'])}.\n" + "\n".join(ev["commands"][:20])})
    except Exception as e:
        logger.warning(f"SOC incidente: {e}")


async def _cmdpol_notify(ap: dict):
    lines = "\n".join(f"  {c}" for c in ap["commands"][:15])
    text = (f"Pedido #{ap['number']} de {ap['user_email']} em {ap['device_name']} ({ap['channel']}):\n{lines}\n"
            f"Regra: {', '.join(ap['rules'])}. Vale até {datetime.fromisoformat(ap['expires_at']).astimezone().strftime('%H:%M')}.")
    try:
        await automation.send_alert(db, "🛂 Aprovação de comando", text, push_url="/soc/commands", push_tag=f"cap-{ap['id']}",
                                    buttons=[[{"text": "✅ Aprovar", "callback_data": f"cap:{ap['id']}"},
                                              {"text": "❌ Negar", "callback_data": f"cdn:{ap['id']}"}]])
    except Exception as e:
        logger.warning(f"SOC aviso de aprovação: {e}")


async def cmd_policy(user: dict, dev: Optional[dict], commands: List[str], channel: str, wait_terminal: bool = False) -> dict:
    """Decide antes de mandar comandos ao equipamento. Devolve {action: allow|block|approve, hits, approval?, message}.
    'approve': cria (ou reaproveita) o pedido; uma aprovação já dada para os mesmos comandos/equipamento libera uma vez."""
    rules, cfg = await _cmdpol_rules()
    if not cfg.get("enabled") or not rules or not commands:
        return {"action": "allow", "hits": []}
    ev = cmdpolicy.evaluate(rules, user, dev or {}, commands, channel)
    act, hits = ev["action"], ev["hits"]
    if act == "allow":
        return {"action": "allow", "hits": []}
    if act == "alert":
        e = await _cmdpol_event(user, dev, channel, "alert", hits)
        await _cmdpol_incident(e, hits, "alert")
        return {"action": "allow", "hits": hits, "alerted": True}
    if act == "block":
        e = await _cmdpol_event(user, dev, channel, "block", [h for h in hits if h["action"] == "block"])
        await _cmdpol_incident(e, [h for h in hits if h["action"] == "block"], "block")
        cmd = next(h["command"] for h in hits if h["action"] == "block")
        rule = next(h["rule"] for h in hits if h["action"] == "block")
        return {"action": "block", "hits": hits, "message": f"Comando bloqueado pela política ({rule}): {cmd}"}
    need = [h for h in hits if h["action"] == "approve"]
    cmds = [h["command"] for h in need]
    h = cmdpolicy.grant_hash(user["id"], (dev or {}).get("id", ""), cmds)
    now = datetime.now(timezone.utc)
    if not wait_terminal:
        g = await db.cmd_approvals.find_one_and_update(
            {"hash": h, "status": "approved", "used": False, "grant_until": {"$gt": now.isoformat()}},
            {"$set": {"used": True, "used_at": now.isoformat()}}, projection={"_id": 0})
        if g:
            await _cmdpol_event(user, dev, channel, "approved-run", need, approval_id=g["id"])
            return {"action": "allow", "hits": hits, "approval": g}
    cur = await db.cmd_approvals.find_one({"hash": h, "status": "pending", "expires_at": {"$gt": now.isoformat()}}, {"_id": 0})
    if not cur:
        last = await db.cmd_approvals.find_one({}, {"_id": 0, "number": 1}, sort=[("number", -1)])
        cur = {"id": os.urandom(8).hex(), "number": int((last or {}).get("number") or 0) + 1, "hash": h, "status": "pending",
               "user_id": user["id"], "user_email": user.get("email", ""), "device_id": (dev or {}).get("id"),
               "device_name": (dev or {}).get("name", ""), "host": (dev or {}).get("host", ""), "channel": channel,
               "commands": cmds, "rules": sorted({x["rule"] for x in need}), "created_at": now.isoformat(),
               "expires_at": (now + timedelta(minutes=int(cfg.get("wait_min") or 10))).isoformat(), "used": False,
               "decided_by": None, "decided_at": None, "note": ""}
        await db.cmd_approvals.insert_one(dict(cur))
        await _cmdpol_event(user, dev, channel, "approval-requested", need, approval_id=cur["id"])
        if cfg.get("notify", True):
            _bg(_cmdpol_notify(cur))
    msg = (f"Precisa de aprovação (pedido #{cur['number']}, regra {', '.join(cur['rules'])}). Um administrador aprova em "
           f"SOC → Aprovações ou no Telegram" + ("." if wait_terminal else "; depois de aprovado, execute de novo."))
    return {"action": "approve", "hits": hits, "approval": cur, "message": msg}


async def _cmdpol_guard(user: dict, dev: Optional[dict], commands: List[str], channel: str):
    """Para os endpoints: bloqueio = 403, aprovação pendente = 409 com o número do pedido."""
    d = await cmd_policy(user, dev, commands, channel)
    if d["action"] == "block":
        raise HTTPException(status_code=403, detail=d["message"])
    if d["action"] == "approve":
        raise HTTPException(status_code=409, detail=d["message"])
    return d


async def cmd_decide(approver: dict, aid: str, approve: bool, note: str = "") -> dict:
    if approver.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Só administradores aprovam comandos")
    ap = await db.cmd_approvals.find_one({"id": aid}, {"_id": 0})
    if not ap:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    cfg = await _cmdpol_cfg()
    if ap["user_id"] == approver["id"] and not cfg.get("self_approve"):
        raise HTTPException(status_code=403, detail="Quem pediu não pode aprovar o próprio pedido (ative em Configurações da política, se for o único admin)")
    now = datetime.now(timezone.utc)
    st = "approved" if approve else "denied"
    upd = {"status": st, "decided_by": approver.get("email", ""), "decided_by_id": approver["id"], "decided_at": now.isoformat(),
           "note": str(note or "")[:300]}
    if approve:
        upd["grant_until"] = (now + timedelta(minutes=int(cfg.get("grant_min") or 30))).isoformat()
    r = await db.cmd_approvals.update_one({"id": aid, "status": "pending", "expires_at": {"$gt": now.isoformat()}}, {"$set": upd})
    if not r.modified_count:
        cur = await db.cmd_approvals.find_one({"id": aid}, {"_id": 0, "status": 1})
        raise HTTPException(status_code=409, detail=f"Esse pedido já está {(cur or {}).get('status', '?') if (cur or {}).get('status') != 'pending' else 'expirado'}")
    ap.update(upd)
    req = await db.users.find_one({"id": ap["user_id"]}, {"_id": 0, "id": 1, "email": 1}) or {"id": ap["user_id"], "email": ap["user_email"]}
    dev = await db.devices.find_one({"id": ap.get("device_id")}, {"_id": 0}) if ap.get("device_id") else None
    e = await _cmdpol_event(req, dev, ap["channel"], st, [{"command": c, "rule": ", ".join(ap["rules"])} for c in ap["commands"]],
                            approval_id=aid, decided_by=approver.get("email", ""))
    if not approve:
        await _cmdpol_incident(e, [{"severity": "medium"}], "denied")
    w = _cmd_waiters.get(aid)
    if w:
        w.set()
    try:
        await _inc_dm([ap["user_id"]], f"{'✅ Aprovado' if approve else '❌ Negado'}: pedido #{ap['number']}",
                      f"{ap['device_name']}: {' | '.join(ap['commands'][:5])}\npor {approver.get('email', '')}" + (f" — {note}" if note else ""), None)
    except Exception:
        pass
    logger.info("Pedido de comando #%s %s por %s", ap["number"], st, approver.get("email"))
    return ap


async def _cmd_decide_tg(user: dict, aid: str, approve: bool) -> str:
    try:
        ap = await cmd_decide(user, aid, approve)
        return f"Pedido #{ap['number']} {'aprovado' if approve else 'negado'}."
    except HTTPException as e:
        return str(e.detail)


assistant.cmd_decide = _cmd_decide_tg
assistant.cmd_policy = cmd_policy
web_assistant.cmd_policy = cmd_policy


class CmdRuleIn(BaseModel):
    name: str = ""
    pattern: str = ""
    action: str = "alert"
    device_types: List[str] = []
    tags: List[str] = []
    device_ids: List[str] = []
    roles: List[str] = []
    user_ids: List[str] = []
    channels: List[str] = []
    severity: str = "medium"
    enabled: bool = True
    description: str = ""


@api.get("/cmdpolicy/rules")
async def cmdpol_rules(_: dict = Depends(require_admin)):
    return await db.cmd_rules.find({}, {"_id": 0}).sort("order", 1).to_list(500)


@api.post("/cmdpolicy/rules")
async def cmdpol_rule_create(body: CmdRuleIn, _: dict = Depends(require_admin)):
    try:
        r = cmdpolicy.clean_rule(body.model_dump())
    except cmdpolicy.PolicyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    last = await db.cmd_rules.find_one({}, {"_id": 0, "order": 1}, sort=[("order", -1)])
    r.update(id=os.urandom(8).hex(), order=int((last or {}).get("order") or 0) + 10, created_at=datetime.now(timezone.utc).isoformat())
    await db.cmd_rules.insert_one(dict(r))
    await _cmdpol_rules(True)
    return r


@api.put("/cmdpolicy/rules/{rid}")
async def cmdpol_rule_update(rid: str, body: CmdRuleIn, _: dict = Depends(require_admin)):
    cur = await db.cmd_rules.find_one({"id": rid}, {"_id": 0})
    if not cur:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    try:
        r = cmdpolicy.clean_rule({**body.model_dump(), "preset": cur.get("preset", "")})
    except cmdpolicy.PolicyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.cmd_rules.update_one({"id": rid}, {"$set": r})
    await _cmdpol_rules(True)
    return {**cur, **r}


@api.delete("/cmdpolicy/rules/{rid}")
async def cmdpol_rule_delete(rid: str, _: dict = Depends(require_admin)):
    await db.cmd_rules.delete_one({"id": rid})
    await _cmdpol_rules(True)
    return {"ok": True}


class CmdOrderIn(BaseModel):
    ids: List[str]


@api.put("/cmdpolicy/rules-order")
async def cmdpol_rules_order(body: CmdOrderIn, _: dict = Depends(require_admin)):
    for i, rid in enumerate(body.ids[:500]):
        await db.cmd_rules.update_one({"id": rid}, {"$set": {"order": (i + 1) * 10}})
    await _cmdpol_rules(True)
    return {"ok": True}


@api.post("/cmdpolicy/rules/presets")
async def cmdpol_presets(_: dict = Depends(require_admin)):
    have = {r.get("preset") async for r in db.cmd_rules.find({}, {"_id": 0, "preset": 1})}
    last = await db.cmd_rules.find_one({}, {"_id": 0, "order": 1}, sort=[("order", -1)])
    order, added = int((last or {}).get("order") or 0), 0
    for p in cmdpolicy.PRESETS:
        if p["preset"] in have:
            continue
        order += 10
        r = cmdpolicy.clean_rule(p)
        r.update(id=os.urandom(8).hex(), order=order, created_at=datetime.now(timezone.utc).isoformat())
        await db.cmd_rules.insert_one(r)
        added += 1
    await _cmdpol_rules(True)
    return {"added": added}


class CmdTestIn(BaseModel):
    commands: str
    device_id: str = ""
    user_id: str = ""
    channel: str = "terminal"


@api.post("/cmdpolicy/test")
async def cmdpol_test(body: CmdTestIn, admin: dict = Depends(require_admin)):
    """Simula a política (não registra nada): qual regra pega cada linha."""
    rules = await db.cmd_rules.find({"enabled": {"$ne": False}}, {"_id": 0}).sort("order", 1).to_list(500)
    u = await db.users.find_one({"id": body.user_id}, {"_id": 0, "id": 1, "role": 1, "email": 1}) if body.user_id else None
    dev = await db.devices.find_one({"id": body.device_id}, {"_id": 0}) if body.device_id else {}
    out = []
    for c in cmdpolicy.command_lines(body.commands)[:100]:
        r = cmdpolicy.match(rules, u or admin, dev or {}, c, body.channel if body.channel in cmdpolicy.CHANNELS else "terminal")
        out.append({"command": c, "action": r["action"] if r else "allow", "rule": r["name"] if r else ""})
    return {"results": out}


@api.get("/cmdpolicy/settings")
async def cmdpol_settings(_: dict = Depends(require_admin)):
    return await _cmdpol_cfg()


class CmdSettingsIn(BaseModel):
    enabled: bool = True
    wait_min: int = 10
    grant_min: int = 30
    self_approve: bool = False
    notify: bool = True


@api.put("/cmdpolicy/settings")
async def cmdpol_settings_put(body: CmdSettingsIn, _: dict = Depends(require_admin)):
    d = {"enabled": body.enabled, "wait_min": max(1, min(120, body.wait_min)), "grant_min": max(5, min(1440, body.grant_min)),
         "self_approve": body.self_approve, "notify": body.notify}
    await db.config.update_one({"key": "cmdpolicy"}, {"$set": d}, upsert=True)
    await _cmdpol_rules(True)
    return d


def _ap_out(a: dict) -> dict:
    a = {k: v for k, v in a.items() if k != "hash"}
    if a.get("status") == "pending" and a.get("expires_at", "") <= datetime.now(timezone.utc).isoformat():
        a["status"] = "expired"
    return a


@api.get("/cmdpolicy/approvals")
async def cmdpol_approvals(status: str = "", limit: int = 200, user: dict = Depends(get_current_user)):
    """Admin vê todos; os demais, só os próprios pedidos."""
    q: dict = {} if user.get("role") == "admin" else {"user_id": user["id"]}
    if status == "pending":
        q.update(status="pending", expires_at={"$gt": datetime.now(timezone.utc).isoformat()})
    elif status in ("approved", "denied", "cancelled"):
        q["status"] = status
    rows = await db.cmd_approvals.find(q, {"_id": 0}).sort("created_at", -1).to_list(min(1000, max(1, limit)))
    return [_ap_out(a) for a in rows]


class CmdDecideIn(BaseModel):
    approve: bool
    note: str = ""


@api.post("/cmdpolicy/approvals/{aid}/decide")
async def cmdpol_decide(aid: str, body: CmdDecideIn, user: dict = Depends(require_admin)):
    return _ap_out(await cmd_decide(user, aid, body.approve, body.note))


@api.post("/cmdpolicy/approvals/{aid}/cancel")
async def cmdpol_cancel(aid: str, user: dict = Depends(get_current_user)):
    r = await db.cmd_approvals.update_one({"id": aid, "user_id": user["id"], "status": "pending"},
                                          {"$set": {"status": "cancelled", "decided_at": datetime.now(timezone.utc).isoformat()}})
    if not r.modified_count:
        raise HTTPException(status_code=404, detail="Pedido não encontrado ou já decidido")
    w = _cmd_waiters.get(aid)
    if w:
        w.set()
    return {"ok": True}


@api.get("/cmdpolicy/events")
async def cmdpol_events(q: str = "", action: str = "", limit: int = 300, _: dict = Depends(require_admin)):
    f: dict = {}
    if action:
        f["action"] = action
    if q:
        rx = {"$regex": re.escape(q[:80]), "$options": "i"}
        f["$or"] = [{"user_email": rx}, {"device_name": rx}, {"commands": rx}]
    return await db.cmd_events.find(f, {"_id": 0}).sort("at", -1).to_list(min(2000, max(1, limit)))


# Terminal: o Enter só vai para o equipamento depois da política
_KILL_LINE = {"mikrotik": "\x03", "huawei": "\x05\x18"}


class _TermPolicy:
    def __init__(self, user: dict, dev: dict, process, ws):
        self.user, self.dev, self.p, self.ws = user, dev, process, ws
        self.tracker = cmdpolicy.LineTracker()
        self.typed: Optional[List[str]] = []
        self.mark = True
        self.held: Optional[dict] = None
        self.task: Optional[asyncio.Task] = None
        self.active = False
        self.checked = 0.0

    async def refresh(self):
        if time.time() - self.checked > 30:
            rules, cfg = await _cmdpol_rules()
            self.active = bool(cfg.get("enabled") and rules)
            self.checked = time.time()

    def out(self, chunk: str):
        self.tracker.feed(chunk)

    async def say(self, text: str, color: str = "33"):
        await self.ws.send_json({"type": "data", "data": f"\r\n\x1b[{color}m[BastiON] {text}\x1b[0m\r\n"})

    def kill_line(self):
        self.p.stdin.write(_KILL_LINE.get(self.dev.get("device_type") or "", "\x05\x15").encode())

    async def input(self, data: str):
        await self.refresh()
        if self.held:
            if "\x03" in data:
                await db.cmd_approvals.update_one({"id": self.held["id"], "status": "pending"},
                                                  {"$set": {"status": "cancelled", "decided_at": datetime.now(timezone.utc).isoformat()}})
                if self.task:
                    self.task.cancel()
                self.held = None
                self.kill_line()
                await self.say("Pedido cancelado.")
            else:
                await self.say(f"Aguardando a aprovação do pedido #{self.held['number']} (Ctrl+C cancela).")
            return
        if not self.active:
            self.p.stdin.write(data.encode("utf-8"))
            return
        chunk = []
        for ch in data:
            if ch in "\r\n":
                if chunk:
                    self.p.stdin.write("".join(chunk).encode("utf-8"))
                    chunk = []
                if ch == "\n" and data.endswith("\r\n"):
                    continue
                await self._enter()
                if self.held:
                    return
                continue
            if self.mark:
                self.tracker.mark_prompt()
                self.mark = False
            if ch in ("\x7f", "\b"):
                if self.typed:
                    self.typed.pop()
            elif ch in ("\x15", "\x03"):
                self.typed = []
                if ch == "\x03":
                    self.mark = True
            elif ch in ("\x1b", "\t") or (ord(ch) < 32):
                self.typed = None
            elif self.typed is not None:
                self.typed.append(ch)
            chunk.append(ch)
        if chunk:
            self.p.stdin.write("".join(chunk).encode("utf-8"))

    async def _enter(self):
        prompt_line = self.tracker.prompt or self.tracker.line()
        cands = []
        if self.typed:
            cands.append("".join(self.typed).strip())
        tc = self.tracker.command()
        if tc:
            cands.append(tc)
        cands = [c for c in dict.fromkeys(cands) if c]
        self.typed, self.mark = [], True
        if not cands or cmdpolicy.at_password(prompt_line):
            self.p.stdin.write(b"\r")
            return
        d = await cmd_policy(self.user, self.dev, cands, "terminal", wait_terminal=True)
        if d["action"] == "allow":
            self.p.stdin.write(b"\r")
            if d.get("alerted"):
                await self.say("Comando registrado pelo SOC.", "36")
            return
        if d["action"] == "block":
            self.kill_line()
            await self.say(d["message"], "31")
            return
        ap = d["approval"]
        self.held = ap
        ev = _cmd_waiters.setdefault(ap["id"], asyncio.Event())
        await self.say(f"{d['message']} Aguardando até {datetime.fromisoformat(ap['expires_at']).astimezone().strftime('%H:%M')} — Ctrl+C cancela.")
        self.task = asyncio.create_task(self._wait(ap, ev))

    async def _wait(self, ap: dict, ev: asyncio.Event):
        try:
            left = (datetime.fromisoformat(ap["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
            try:
                await asyncio.wait_for(ev.wait(), timeout=max(1.0, left))
            except asyncio.TimeoutError:
                pass
            cur = await db.cmd_approvals.find_one({"id": ap["id"]}, {"_id": 0}) or {}
            if cur.get("status") == "approved":
                await db.cmd_approvals.update_one({"id": ap["id"]}, {"$set": {"used": True, "used_at": datetime.now(timezone.utc).isoformat()}})
                await self.say(f"Aprovado por {cur.get('decided_by', '')}. Executando.", "32")
                await _cmdpol_event(self.user, self.dev, "terminal", "approved-run",
                                    [{"command": c, "rule": ", ".join(cur.get("rules") or [])} for c in cur.get("commands") or []], approval_id=ap["id"])
                self.p.stdin.write(b"\r")
            else:
                if cur.get("status") == "pending":
                    await db.cmd_approvals.update_one({"id": ap["id"], "status": "pending"}, {"$set": {"status": "expired"}})
                self.kill_line()
                why = {"denied": f"Negado por {cur.get('decided_by', '')}" + (f": {cur.get('note')}" if cur.get("note") else ""),
                       "cancelled": "Pedido cancelado"}.get(cur.get("status"), "O pedido expirou sem resposta")
                await self.say(f"{why}. O comando não foi enviado.", "31")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"SOC terminal: {e}")
        finally:
            self.held = None
            _cmd_waiters.pop(ap["id"], None)

    def close(self):
        if self.task:
            self.task.cancel()


# ---------- SOC: envio para SIEM ----------
_siem_rt: dict = {"senders": {}, "sig": None, "http": None, "cfg": None}
_AUDIT_SKIP = re.compile(r"^/api/(auth/(login|refresh|2fa)|ws/|push/|assistant(/.*)?/chat|.*/test$|cmdpolicy/test|soc/certbr/inbound/|tacacs/internal/|"
                         r"oltgen/(hw/|fh/)?(generate|onu|onu-remove|onu-add)$|incidents/webhook|logs/ingest|siem/preview)")


@app.middleware("http")
async def _audit_mw(request: Request, call_next):
    resp = await call_next(request)
    try:
        path = request.url.path
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/") and not _AUDIT_SKIP.match(path):
            tok = auth_mod.extract_token(request)
            if tok:
                try:
                    pl = decode_token(tok)
                except HTTPException:
                    pl = None
                if pl:
                    now = datetime.now(timezone.utc)
                    await db.audit_events.insert_one({"id": os.urandom(8).hex(), "ts": now, "at": now.isoformat(), "user_id": pl.get("sub"),
                                                      "user_email": pl.get("email", ""), "method": request.method, "path": path[:300],
                                                      "status": resp.status_code, "ip": security.client_ip(request)})
    except Exception as e:
        logger.debug(f"auditoria: {e}")
    return resp


async def _siem_cfg() -> dict:
    doc = await db.config.find_one({"key": "siem"}, {"_id": 0}) or {}
    return {"enabled": bool(doc.get("enabled")), "destinations": doc.get("destinations") or [], "secrets": doc.get("secrets") or {}}


def _siem_senders(cfg: dict) -> dict:
    sig = json.dumps([cfg["destinations"], sorted(cfg["secrets"].items())], sort_keys=True, default=str)
    if sig != _siem_rt["sig"]:
        old = _siem_rt["senders"]
        new = {}
        for d in cfg["destinations"]:
            s = siemmod.Sender(d, vault.decrypt(cfg["secrets"].get(d["id"], "")))
            if d["id"] in old:
                o = old[d["id"]]
                s.q, s.sent, s.failed, s.dropped, s.last_ok, s.last_error = o.q, o.sent, o.failed, o.dropped, o.last_ok, o.last_error
            new[d["id"]] = s
        _siem_rt["senders"], _siem_rt["sig"] = new, sig
    return _siem_rt["senders"]


_SIEM_SRC = [
    ("login_events", "at", lambda d: siemmod.from_login(d)),
    ("sessions", "started_at", lambda d: siemmod.from_session(d, False)),
    ("sessions", "ended_at", lambda d: None if (d.get("kind") or "") == "batch" else siemmod.from_session(d, True)),
    ("cmd_events", "at", lambda d: siemmod.from_cmd_event(d)),
    ("ai_audit", "at", lambda d: siemmod.from_ai(d)),
    ("audit_events", "at", lambda d: siemmod.from_change(d)),
    ("tacacs_events", "at", lambda d: siemmod.from_tacacs(d)),
    ("ftp_events", "at", lambda d: siemmod.from_ftp(d)),
    ("incidents", "created_at", lambda d: siemmod.from_incident(d, False)),
    ("incidents", "resolved_at", lambda d: siemmod.from_incident(d, True) if d.get("status") == "resolved" else None),
]


async def _siem_collect(cp: dict) -> list:
    """Eventos novos desde o último ponto de cada coleção (o ponto anda mesmo sem saída configurada)."""
    out = []
    for col, field, fn in _SIEM_SRC:
        k = f"{col}.{field}"
        since = cp.get(k)
        if not since:
            cp[k] = datetime.now(timezone.utc).isoformat()
            continue
        rows = await db[col].find({field: {"$gt": since}}, {"_id": 0, "output": 0, "timeline": 0, "ts": 0}).sort(field, 1).to_list(2000)
        for r in rows:
            try:
                e = fn(r)
            except Exception as ex:
                logger.debug(f"SIEM {col}: {ex}")
                e = None
            if e:
                out.append(e)
        if rows:
            cp[k] = rows[-1][field]
    out.sort(key=lambda e: e["ts"])
    return out


async def _siem_loop():
    await asyncio.sleep(20)
    import httpx
    while True:
        try:
            cfg = await _siem_cfg()
            cpdoc = await db.config.find_one({"key": "siem_cp"}, {"_id": 0}) or {}
            cp = dict(cpdoc.get("cp") or {})
            events = await _siem_collect(cp)
            await db.config.update_one({"key": "siem_cp"}, {"$set": {"cp": cp}}, upsert=True)
            if cfg["enabled"] and cfg["destinations"]:
                senders = _siem_senders(cfg)
                for e in events:
                    for s in senders.values():
                        if s.wants(e):
                            s.push(e)
                if not _siem_rt["http"]:
                    _siem_rt["http"] = httpx.AsyncClient(verify=True)
                for s in senders.values():
                    if s.q:
                        http = _siem_rt["http"]
                        if s.d.get("kind") in ("http", "splunk") and not s.d.get("verify_tls", True):
                            http = httpx.AsyncClient(verify=False)
                        try:
                            await s.flush(http)
                        finally:
                            if http is not _siem_rt["http"]:
                                await http.aclose()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"SIEM: {e}")
        await asyncio.sleep(10)


def _siem_public(cfg: dict) -> dict:
    st = {k: s.status() for k, s in _siem_rt["senders"].items()}
    return {"enabled": cfg["enabled"], "categories": siemmod.CATEGORIES, "formats": siemmod.FORMATS,
            "destinations": [{**d, "has_secret": bool(cfg["secrets"].get(d["id"])), "status": st.get(d["id"])} for d in cfg["destinations"]]}


@api.get("/siem/settings")
async def siem_settings(_: dict = Depends(require_admin)):
    return _siem_public(await _siem_cfg())


class SiemIn(BaseModel):
    enabled: bool = False
    destinations: List[dict] = []


@api.put("/siem/settings")
async def siem_settings_put(body: SiemIn, _: dict = Depends(require_admin)):
    cur = await _siem_cfg()
    dests, secrets = [], {}
    for i, d in enumerate(body.destinations[:10], 1):
        d = dict(d)
        d["id"] = re.sub(r"[^a-f0-9]", "", str(d.get("id") or ""))[:16] or os.urandom(6).hex()
        try:
            c = siemmod.clean_dest(d)
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=f"Destino {i}: {e}")
        dests.append(c)
        sec = d.get("secret")
        if sec:
            secrets[c["id"]] = vault.encrypt(str(sec))
        elif not d.get("clear_secret") and cur["secrets"].get(c["id"]):
            secrets[c["id"]] = cur["secrets"][c["id"]]
    await db.config.update_one({"key": "siem"}, {"$set": {"enabled": body.enabled, "destinations": dests, "secrets": secrets}}, upsert=True)
    cfg = await _siem_cfg()
    _siem_senders(cfg)
    return _siem_public(cfg)


class SiemTestIn(BaseModel):
    id: str


@api.post("/siem/test")
async def siem_test(body: SiemTestIn, _: dict = Depends(require_admin)):
    cfg = await _siem_cfg()
    s = _siem_senders(cfg).get(body.id)
    if not s:
        raise HTTPException(status_code=404, detail="Destino não encontrado — salve antes de testar")
    import httpx
    t = siemmod.Sender(s.d, s.secret)
    e = siemmod.event("auth", "test", "success", 1, user="bastion", message="Teste de envio do BastiON para o SIEM")
    t.push(e)
    async with httpx.AsyncClient(verify=s.d.get("verify_tls", True)) as http:
        await t.flush(http)
    if t.last_error:
        raise HTTPException(status_code=502, detail=f"Falhou: {t.last_error}")
    return {"ok": True, "sent": siemmod.syslog_line(e, s.d.get("format", "json")) if s.d["kind"] == "syslog" else
            (siemmod.to_gelf(e) if s.d["kind"] == "gelf" else siemmod.to_json(e))}


@api.get("/siem/preview")
async def siem_preview(format: str = "cef", _: dict = Depends(require_admin)):
    return {"lines": [siemmod.syslog_line(e, format) for e in siemmod.sample_events()],
            "gelf": siemmod.to_gelf(siemmod.sample_events()[1]), "json": siemmod.to_json(siemmod.sample_events()[1])}


@api.get("/soc/audit")
async def soc_audit(q: str = "", limit: int = 300, _: dict = Depends(require_admin)):
    """Alterações feitas pela interface/API (quem, o quê, quando, de onde)."""
    f: dict = {}
    if q:
        rx = {"$regex": re.escape(q[:80]), "$options": "i"}
        f["$or"] = [{"user_email": rx}, {"path": rx}, {"ip": rx}]
    return await db.audit_events.find(f, {"_id": 0, "ts": 0}).sort("at", -1).to_list(min(2000, max(1, limit)))


# ---------- SOC: login anômalo ----------
async def _la_cfg() -> dict:
    doc = await db.config.find_one({"key": "loginanom"}, {"_id": 0}) or {}
    return {**loginanom.DEFAULTS, **{k: v for k, v in doc.items() if k in loginanom.DEFAULTS}}


async def _geo(ip: str) -> dict:
    """País/operadora do IP pela base de ASN do Flow (vazio se a base não foi importada ou o IP é interno)."""
    if loginanom.is_private(ip):
        return {}
    try:
        a = await _asn_db()
        if not a:
            return {}
        addr = ipaddress.ip_address(ip)
        asn = a.lookup(addr.version, int(addr))
        if not asn:
            return {}
        nm = a.names.get(int(asn)) or ("", "")
        return {"asn": int(asn), "asn_name": nm[0], "cc": (nm[1] or "").upper()}
    except Exception:
        return {}


async def _login_gate(user: dict, ip: str, ua: str):
    """Depois da senha/2FA certos: bloqueia fora dos países permitidos (cerca geográfica)."""
    cfg = await _la_cfg()
    if not cfg.get("enabled") or not cfg.get("allowed_countries"):
        return
    g = await _geo(ip)
    if loginanom.geofence_blocks(g.get("cc", ""), ip, cfg, user.get("role") == "admin"):
        await security.log_event(db, user["email"], ip, ua, False, f"país {g['cc']}", user["id"])
        f = [{"kind": "geo_block", "severity": "high", "text": f"Login de {g['cc']} (AS{g.get('asn')} {g.get('asn_name', '')}) bloqueado — permitidos: {', '.join(cfg['allowed_countries'])}"}]
        _bg(_la_record(user, ip, ua, g, f, "high", blocked=True))
        raise HTTPException(status_code=403, detail="Login não permitido a partir deste país. Fale com o administrador.")


async def _la_record(user: dict, ip: str, ua: str, g: dict, findings: list, sev: str, blocked: bool = False) -> dict:
    cfg = await _la_cfg()
    now = datetime.now(timezone.utc).isoformat()
    doc = {"id": os.urandom(8).hex(), "at": now, "user_id": user["id"], "email": user["email"], "ip": ip, "ua": (ua or "")[:200],
           "device": loginanom.device_of(ua), **g, "findings": findings, "severity": sev, "blocked": blocked,
           "status": "blocked" if blocked else "open", "decided_by": None}
    await db.login_anomalies.insert_one(dict(doc))
    if loginanom.SEV_RANK[sev] < loginanom.SEV_RANK.get(cfg.get("notify_min") or "medium", 2):
        return doc
    where = f"{ip}" + (f" ({g.get('cc')}, AS{g.get('asn')} {g.get('asn_name', '')})" if g.get("cc") else "")
    try:
        await inc_center.ingest("soc", {"key": f"soc:login:{doc['id']}", "status": "problem", "severity": sev,
                                        "title": f"{'Login bloqueado' if blocked else 'Login suspeito'}: {user['email']} de {where}",
                                        "description": "\n".join(f"• {f['text']}" for f in findings) + f"\nNavegador: {doc['device']}"})
    except Exception as e:
        logger.warning(f"SOC login: {e}")
    if cfg.get("ask_user", True) and not blocked:
        try:
            await _inc_dm([user["id"]], "🔐 Foi você?", f"Login na sua conta do BastiON de {where}, {doc['device']}.\n"
                          + "\n".join(f"• {f['text']}" for f in findings),
                          [[{"text": "✅ Fui eu", "callback_data": f"lay:{doc['id']}"}, {"text": "🚨 Não fui eu", "callback_data": f"lan:{doc['id']}"}]])
        except Exception:
            pass
    return doc


async def _login_anomaly(user: dict, ip: str, ua: str):
    try:
        cfg = await _la_cfg()
        if not cfg.get("enabled") or loginanom.in_nets(ip, cfg.get("trusted_nets") or []):
            return
        now = datetime.now(timezone.utc)
        hist_raw = await db.login_events.find({"user_id": user["id"], "ok": True}, {"_id": 0, "ip": 1, "ua": 1, "at": 1}).sort("at", -1).to_list(301)
        hist_raw = hist_raw[1:] if hist_raw and hist_raw[0].get("ip") == ip and hist_raw[0].get("at", "") >= (now - timedelta(seconds=30)).isoformat() else hist_raw
        geo_cache: dict = {}
        hist = []
        for h in hist_raw[:300]:
            if h["ip"] not in geo_cache:
                geo_cache[h["ip"]] = await _geo(h["ip"])
            hist.append({**h, **geo_cache[h["ip"]]})
        fails = await db.login_events.count_documents({"email": user["email"], "ok": False,
                                                       "at": {"$gte": (now - timedelta(minutes=30)).isoformat()}})
        g = await _geo(ip)
        cur = {"ip": ip, "ua": ua, "at": now.isoformat(), **g}
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(cfg.get("timezone") or "America/Sao_Paulo")
        except Exception:
            tz = None
        f = loginanom.analyze(cur, hist, fails, cfg, tz)
        try:
            intel = await _ti_intel()
            rep_ = intel.lookup(ip) if intel and not loginanom.is_private(ip) else []
        except Exception:
            rep_ = []
        if rep_:
            cat = threatintel.worst_category(rep_)
            f.append({"kind": "ip_reputation", "severity": "medium" if cat in ("tor", "scanner") else "high",
                      "text": f"IP em lista de ameaças: {', '.join(x['name'] for x in rep_[:3])}"})
        if f:
            await _la_record(user, ip, ua, g, f, loginanom.overall(f))
    except Exception as e:
        logger.warning(f"login anômalo: {e}")


async def _la_decide(who: dict, aid: str, action: str) -> dict:
    a = await db.login_anomalies.find_one({"id": aid}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Registro não encontrado")
    if who.get("role") != "admin" and who["id"] != a["user_id"]:
        raise HTTPException(status_code=403, detail="Sem permissão")
    now = datetime.now(timezone.utc).isoformat()
    if action == "benign":
        upd = {"status": "benign", "decided_by": who.get("email"), "decided_at": now}
    elif action == "revoke":
        await db.users.update_one({"id": a["user_id"]}, {"$inc": {"token_version": 1}})
        upd = {"status": "compromised", "decided_by": who.get("email"), "decided_at": now}
        try:
            await inc_center.ingest("soc", {"key": f"soc:login-revoke:{aid}", "status": "problem", "severity": "critical",
                                            "title": f"Conta possivelmente comprometida: {a['email']} — sessões encerradas",
                                            "description": f"Login de {a['ip']} ({a.get('cc', '?')}) marcado como 'não fui eu' por {who.get('email')}. Troque a senha e confira o 2FA."})
        except Exception:
            pass
        logger.warning("Sessões de %s encerradas por login suspeito (%s)", a["email"], who.get("email"))
    else:
        raise HTTPException(status_code=400, detail="Ação inválida")
    await db.login_anomalies.update_one({"id": aid}, {"$set": upd})
    if action == "benign":
        await db.incidents.update_many({"key": f"soc:login:{aid}", "status": {"$in": ["open", "ack"]}}, {"$set": {
            "status": "resolved", "resolved_at": now, "resolved_by": who.get("email"), "updated_at": now}})
    return {**a, **upd}


async def _la_tg(user: dict, aid: str, me: bool) -> str:
    try:
        await _la_decide(user, aid, "benign" if me else "revoke")
        return "Obrigado — registrado." if me else "Sessões encerradas. Troque sua senha agora e avise o administrador."
    except HTTPException as e:
        return str(e.detail)


assistant.login_anom = _la_tg


@api.get("/soc/logins/settings")
async def la_settings(_: dict = Depends(require_admin)):
    meta = await asndb.get_meta(db)
    return {**await _la_cfg(), "geo_ready": bool(meta)}


class LaSettingsIn(BaseModel):
    enabled: bool = True
    notify_min: str = "medium"
    ask_user: bool = True
    allowed_countries: List[str] = []
    trusted_nets: List[str] = []
    geofence_admins: bool = True
    min_history: int = 5
    travel_hours: float = 3
    fail_burst: int = 5
    timezone: str = "America/Sao_Paulo"


@api.put("/soc/logins/settings")
async def la_settings_put(body: LaSettingsIn, request: Request, admin: dict = Depends(require_admin)):
    try:
        d = {**body.model_dump(), "allowed_countries": loginanom.clean_cc(body.allowed_countries), "trusted_nets": loginanom.clean_nets(body.trusted_nets)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if d["notify_min"] not in loginanom.SEV_RANK:
        d["notify_min"] = "medium"
    d["min_history"] = max(1, min(100, d["min_history"]))
    d["fail_burst"] = max(2, min(100, d["fail_burst"]))
    d["travel_hours"] = max(0.5, min(48.0, d["travel_hours"]))
    if d["allowed_countries"]:
        ip = security.client_ip(request)
        g = await _geo(ip)
        if loginanom.geofence_blocks(g.get("cc", ""), ip, d, True):
            raise HTTPException(status_code=400, detail=f"Você está entrando de {g['cc']}: incluir {g['cc']} na lista (ou sua rede nas confiáveis) para não se trancar fora")
    await db.config.update_one({"key": "loginanom"}, {"$set": d}, upsert=True)
    return d


@api.get("/soc/logins/anomalies")
async def la_list(status: str = "", limit: int = 300, _: dict = Depends(require_admin)):
    q = {"status": status} if status else {}
    return await db.login_anomalies.find(q, {"_id": 0}).sort("at", -1).to_list(min(2000, max(1, limit)))


class LaActIn(BaseModel):
    action: str


@api.post("/soc/logins/anomalies/{aid}")
async def la_act(aid: str, body: LaActIn, admin: dict = Depends(require_admin)):
    return await _la_decide(admin, aid, body.action)


@api.get("/soc/logins/recent")
async def la_recent(email: str = "", limit: int = 200, _: dict = Depends(require_admin)):
    q = {"email": email.lower()} if email else {}
    rows = await db.login_events.find(q, {"_id": 0, "ts": 0}).sort("at", -1).to_list(min(1000, max(1, limit)))
    cache: dict = {}
    for r in rows:
        if r.get("ip") not in cache:
            cache[r.get("ip")] = await _geo(r.get("ip") or "")
        r.update(cache[r.get("ip")])
        r["device"] = loginanom.device_of(r.get("ua", ""))
    return rows


# ---------- SOC: threat intel no Flow ----------
_ti_rt: dict = {"intel": None, "sig": None, "busy": False}


async def _ti_cfg() -> dict:
    return threatintel.clean_settings(await db.config.find_one({"key": "threatintel"}, {"_id": 0}) or {})


async def _ti_intel():
    """Conjunto carregado no backend (consulta de IP e login anômalo); recarrega quando as listas mudam."""
    cfg = await _ti_cfg()
    meta = await db.config.find_one({"key": "ti_meta"}, {"_id": 0}) or {}
    sig = (str(sorted((k, str(v)) for k, v in cfg.items())), meta.get("at"))
    if sig != _ti_rt["sig"]:
        _ti_rt["sig"] = sig
        if not cfg["enabled"]:
            _ti_rt["intel"] = None
        else:
            feeds = threatintel.feed_list(cfg)
            lists = {}
            for f in feeds:
                d = await db.ti_lists.find_one({"_id": f["id"]}, {"blob": 1})
                if d and d.get("blob"):
                    lists[f["id"]] = threatintel.unpack(d["blob"])
            _ti_rt["intel"] = await asyncio.to_thread(threatintel.Intel, lists, {f["id"]: f for f in feeds}, cfg["manual"], cfg["ignore"])
    return _ti_rt["intel"]


async def _ti_refresh(force: bool = False) -> dict:
    if _ti_rt["busy"]:
        return {"busy": True}
    _ti_rt["busy"] = True
    done = {}
    try:
        cfg = await _ti_cfg()
        if not cfg["enabled"]:
            return {}
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": "BastiON-threat-intel/2.0"}) as c:
            for f in threatintel.feed_list(cfg):
                cur = await db.ti_lists.find_one({"_id": f["id"]}, {"_id": 0, "at": 1, "url": 1})
                fresh = cur and cur.get("url") == f["url"] and cur.get("at", "") > (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
                if fresh and not force:
                    continue
                st = {"fid": f["id"], "url": f["url"], "name": f["name"], "category": f["category"], "at": datetime.now(timezone.utc).isoformat()}
                try:
                    r = await c.get(f["url"])
                    r.raise_for_status()
                    cidrs = threatintel.parse(r.text[:20_000_000])
                    if not cidrs:
                        raise ValueError("nenhum IP reconhecido na lista")
                    st.update(blob=threatintel.pack(cidrs), count=len(cidrs), error="", ok_at=st["at"])
                except Exception as e:
                    st["error"] = f"{type(e).__name__}: {str(e)[:150]}"
                await db.ti_lists.update_one({"_id": f["id"]}, {"$set": st}, upsert=True)
                done[f["id"]] = st.get("count") or st["error"]
        if done:
            await db.config.update_one({"key": "ti_meta"}, {"$set": {"at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
            logger.info(f"threat intel: listas atualizadas {done}")
    finally:
        _ti_rt["busy"] = False
    return done


def _ti_in(ip: str, nets: List[str]) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in nets or []:
        try:
            if a in ipaddress.ip_network(n, strict=False):
                return True
        except ValueError:
            pass
    return False


async def _ti_alerts():
    """Um incidente por host local + sentido + categoria (não um por IP ruim). Saída fraca (DROP, atacante…) só com volume;
    respostas a varreduras já chegam do coletor como entrada. O incidente resolve sozinho quando o tráfego para."""
    cfg = await _ti_cfg()
    q_out = {"alerted": False, "dir": "out", "$or": [{"category": {"$in": list(threatintel.STRONG)}}, {"pkts": {"$gte": cfg["out_min_pkts"]}}]}
    rows = await db.ti_hits.find(q_out, {"_id": 0}).sort("first", 1).to_list(2000)
    if cfg["alert_in_min_flows"]:
        rows += await db.ti_hits.find({"alerted": False, "dir": "in", "flows": {"$gte": cfg["alert_in_min_flows"]}}, {"_id": 0}).to_list(1000)
    groups: Dict[tuple, list] = {}
    for h in rows:
        await db.ti_hits.update_one({"key": h["key"]}, {"$set": {"alerted": True}})
        sev = threatintel.OUT_SEVERITY.get(h.get("category"), "medium") if h["dir"] == "out" else "low"
        if h["dir"] == "out" and (not cfg["alert_out"] or sev == "info"):
            continue
        if _ti_in(h["local_ip"], cfg["ignore_local"]):
            continue
        groups.setdefault((h["local_ip"], h["dir"], h.get("category") or "custom"), []).append(h)
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    for (local, d, cat), hs in groups.items():
        allh = await db.ti_hits.find({"local_ip": local, "dir": d, "category": cat, "day": {"$gte": since}},
                                     {"_id": 0, "bad_ip": 1, "flows": 1, "pkts": 1, "bytes": 1, "ports": 1, "feeds": 1, "first": 1, "last": 1}).to_list(20000)
        if d == "out":
            allh = [a for a in allh if cat in threatintel.STRONG or (a.get("pkts") or 0) >= cfg["out_min_pkts"]] or hs
        bad = {}
        for a in allh:
            b = bad.setdefault(a["bad_ip"], 0)
            bad[a["bad_ip"]] = b + (a.get("flows") or 0)
        top = sorted(bad.items(), key=lambda x: -x[1])[:10]
        ports = sorted({p for a in allh for p in a.get("ports") or []})[:15]
        feeds = ", ".join(sorted({(threatintel.FEED_BY_ID.get(f) or {}).get("name", f) for a in allh for f in a.get("feeds") or []}))
        label = threatintel.CATEGORIES.get(cat, cat)
        n = len(bad)
        sev = threatintel.OUT_SEVERITY.get(cat, "medium") if d == "out" else "low"
        who = f"{n} IPs" if n > 1 else top[0][0]
        title = (f"Host {local} falando com {label[:1].lower() + label[1:]}: {who}" if d == "out" else f"{label}: {who} insistindo em {local}")
        desc = (f"Listas: {feeds}.\nIPs remotos (24 h): {n} — mais ativos: " + ", ".join(f"{ip} ({fl} flows)" for ip, fl in top) +
                f".\nPortas: {', '.join(ports)}.\nPrimeiro registro {min(a.get('first', '') for a in allh)[:16].replace('T', ' ')} UTC (flow)."
                + ("\nSe for resposta a varredura ou serviço legítimo, ponha o host em 'hosts ignorados' no Threat intel."
                   if d == "out" and cat not in threatintel.STRONG else ""))
        key = f"soc:ti2:{d}:{cat}:{local}"
        try:
            r = await inc_center.ingest("soc", {"key": key, "status": "problem", "severity": sev, "host": local, "title": title,
                                                "description": desc})
            inc = r.get("incident") or {}
            if inc.get("id"):
                await db.incidents.update_one({"id": inc["id"]}, {"$set": {"title": title, "description": desc,
                                                                           "ti": {"local": local, "dir": d, "category": cat, "remotes": n}}})
        except Exception as e:
            logger.warning(f"threat intel incidente: {e}")


async def _ti_housekeep():
    """Fecha sozinho o incidente cujo tráfego parou, e aposenta os incidentes antigos (um por IP ruim) sem disparar aviso."""
    cfg = await _ti_cfg()
    now = datetime.now(timezone.utc)
    cur = await db.incidents.find({"source": "soc", "status": {"$in": ["open", "ack"]}}, {"_id": 0, "id": 1, "key": 1, "ti": 1}).to_list(20000)
    old = [i["id"] for i in cur if str(i.get("key", "")).startswith("soc:ti:")]
    if old:
        at = now.isoformat()
        await db.incidents.update_many({"id": {"$in": old}}, {
            "$set": {"status": "resolved", "resolved_at": at, "resolved_by": "BastiON", "updated_at": at},
            "$push": {"timeline": {"at": at, "by": "BastiON", "type": "resolved",
                                   "text": "Substituído pelo agrupamento por host (o detector novo reabre se continuar)"}}})
        logger.info(f"threat intel: {len(old)} incidente(s) do formato antigo encerrados")
    if not cfg["auto_resolve_hours"]:
        return
    lim = (now - timedelta(hours=cfg["auto_resolve_hours"])).isoformat()
    for i in cur:
        t = i.get("ti")
        if not t or not str(i.get("key", "")).startswith("soc:ti2:"):
            continue
        last = await db.ti_hits.find({"local_ip": t["local"], "dir": t["dir"], "category": t["category"]}, {"_id": 0, "last": 1}).sort("last", -1).to_list(1)
        if not last or (last[0].get("last") or "") < lim:
            try:
                await inc_center.ingest("soc", {"key": i["key"], "status": "ok", "severity": "info", "title": ""})
            except Exception as e:
                logger.warning(f"threat intel resolve: {e}")


async def _ti_loop():
    await asyncio.sleep(60)
    n = 0
    while True:
        try:
            if n % 10 == 0:
                await _ti_refresh()
            await _ti_alerts()
            if n % 5 == 0:
                await _ti_housekeep()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"threat intel: {e}")
        n += 1
        await asyncio.sleep(60)


@api.get("/soc/ti/settings")
async def ti_settings(_: dict = Depends(require_admin)):
    cfg = await _ti_cfg()
    if not cfg["block_token"]:
        cfg["block_token"] = os.urandom(16).hex()
        await db.config.update_one({"key": "threatintel"}, {"$set": {"block_token": cfg["block_token"]}}, upsert=True)
    st = {d.get("fid") or d.get("_id"): {k: v for k, v in d.items() if k != "_id"} async for d in db.ti_lists.find({}, {"blob": 0})}
    feeds = [{**f, "on": f["id"] in cfg["feeds"], "status": st.get(f["id"])} for f in threatintel.FEEDS]
    custom = [{**c, "status": st.get(c["id"])} for c in cfg["custom"]]
    intel = await _ti_intel()
    return {**cfg, "catalog": feeds, "custom": custom, "categories": threatintel.CATEGORIES, "networks": intel.count if intel else 0,
            "flow_own_prefixes": len((await flowstore.get_settings(db)).get("own_prefixes") or [])}


@api.put("/soc/ti/settings")
async def ti_settings_put(body: dict, _: dict = Depends(require_admin)):
    try:
        cfg = threatintel.clean_settings({**(body or {}), "block_token": (await _ti_cfg())["block_token"]})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.config.update_one({"key": "threatintel"}, {"$set": cfg}, upsert=True)
    await flowstore.ensure_ttl(db, "ti_hits", cfg["retention_days"] * 86400)
    _bg(_ti_refresh())
    return cfg


@api.post("/soc/ti/block-token")
async def ti_block_token(_: dict = Depends(require_admin)):
    t = os.urandom(16).hex()
    await db.config.update_one({"key": "threatintel"}, {"$set": {"block_token": t}}, upsert=True)
    return {"token": t}


async def _ti_blocknets() -> List[str]:
    cfg = await _ti_cfg()
    intel = await _ti_intel()
    if not intel or not cfg["block_categories"]:
        return []
    fs = await flowstore.get_settings(db)
    mit = await mitigation.get_settings(db)
    own = list(fs.get("own_prefixes") or []) + list(mit.get("protect") or [])
    return await asyncio.to_thread(threatintel.blocklist, intel, cfg["block_categories"], own)


@api.get("/soc/ti/blocklist/preview")
async def ti_block_preview(_: dict = Depends(require_admin)):
    nets = await _ti_blocknets()
    return {"count": len(nets), "v4": sum(1 for n in nets if ":" not in n), "v6": sum(1 for n in nets if ":" in n), "sample": nets[:20]}


@api.get("/soc/ti/blocklist/{token}")
async def ti_blocklist(token: str, fmt: str = "txt", name: str = "bastion-ti"):
    """Lista para o roteador baixar sozinho (MikroTik /tool fetch, script no Junos/Cisco…). Protegida pelo token."""
    cfg = await _ti_cfg()
    if not cfg["block_token"] or not hmac.compare_digest(token, cfg["block_token"]):
        raise HTTPException(status_code=403, detail="Token inválido")
    name = re.sub(r"[^A-Za-z0-9_-]", "", name)[:30] or "bastion-ti"
    nets = await _ti_blocknets()
    return PlainTextResponse(threatintel.render_blocklist(nets, fmt if fmt in ("txt", "routeros", "juniper", "cisco") else "txt", name))


@api.post("/soc/ti/refresh")
async def ti_refresh(_: dict = Depends(require_admin)):
    r = await _ti_refresh(force=True)
    if r.get("busy"):
        raise HTTPException(status_code=409, detail="Já está atualizando as listas")
    return {"updated": r}


@api.get("/soc/ti/hits")
async def ti_hits(direction: str = "", q: str = "", days: int = 7, category: str = "", limit: int = 500, _: dict = Depends(require_admin)):
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(365, days)))).strftime("%Y-%m-%d")
    f: dict = {"day": {"$gte": since}}
    if direction in ("in", "out"):
        f["dir"] = direction
    if category:
        f["category"] = category
    if q:
        f["$or"] = [{"bad_ip": {"$regex": "^" + re.escape(q.strip())}}, {"local_ip": {"$regex": "^" + re.escape(q.strip())}}]
    rows = await db.ti_hits.find(f, {"_id": 0, "ts": 0}).sort("last", -1).to_list(min(3000, max(1, limit)))
    return rows


@api.get("/soc/ti/summary")
async def ti_summary(days: int = 1, _: dict = Depends(require_admin)):
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(90, days)))).strftime("%Y-%m-%d")
    rows = await db.ti_hits.find({"day": {"$gte": since}}, {"_id": 0, "dir": 1, "category": 1, "local_ip": 1, "bad_ip": 1, "flows": 1, "bytes": 1}).to_list(200000)
    by: dict = {}
    hosts_out: dict = {}
    for r in rows:
        k = (r["dir"], r.get("category"))
        b = by.setdefault(k, {"dir": r["dir"], "category": r.get("category"), "hits": 0, "flows": 0, "bytes": 0, "bad": set(), "local": set()})
        b["hits"] += 1
        b["flows"] += r.get("flows", 0)
        b["bytes"] += r.get("bytes", 0)
        b["bad"].add(r["bad_ip"])
        b["local"].add(r["local_ip"])
        if r["dir"] == "out":
            h = hosts_out.setdefault(r["local_ip"], {"ip": r["local_ip"], "bad": set(), "flows": 0, "categories": set()})
            h["bad"].add(r["bad_ip"])
            h["flows"] += r.get("flows", 0)
            h["categories"].add(r.get("category"))
    cats = [{**v, "bad": len(v["bad"]), "local": len(v["local"])} for v in by.values()]
    top = sorted(({**h, "bad": len(h["bad"]), "categories": sorted(h["categories"])} for h in hosts_out.values()), key=lambda h: -h["flows"])[:20]
    return {"by_category": sorted(cats, key=lambda c: (c["dir"], -c["hits"])), "top_hosts_out": top}


@api.get("/soc/ti/lookup")
async def ti_lookup(ip: str, _: dict = Depends(require_admin)):
    try:
        a = ipaddress.ip_address(ip.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="IP inválido")
    intel = await _ti_intel()
    feeds = intel.lookup(str(a)) if intel else []
    hits = await db.ti_hits.find({"$or": [{"bad_ip": str(a)}, {"local_ip": str(a)}]}, {"_id": 0, "ts": 0}).sort("last", -1).to_list(200)
    return {"ip": str(a), "feeds": feeds, "geo": await _geo(str(a)), "hits": hits, "loaded": bool(intel)}


# ---------- SOC: auditoria CERT.br (passiva) ----------
async def _cert_cfg() -> dict:
    return certbr.clean_settings(await db.config.find_one({"key": "certbr"}, {"_id": 0}) or {})


async def _cert_upsert(ip: str, service: str, source: str, at: str, **extra) -> dict:
    key = f"{ip}|{service}"
    cur = await db.cert_items.find_one({"key": key}, {"_id": 0})
    now = datetime.now(timezone.utc).isoformat()
    if not cur:
        cur = {"id": os.urandom(8).hex(), "key": key, "ip": ip, "service": service, "status": "open", "sources": [source],
               "first": at or now, "last": at or now, "notices": [], "flow": None, "reopened": 0, "note": "", "created_at": now}
        await db.cert_items.insert_one(dict(cur))
        if (await _cert_cfg())["alert"]:
            svc = certbr.SERVICES.get(service, {}).get("name", service)
            try:
                await inc_center.ingest("soc", {"key": f"soc:certbr:{key}", "status": "problem", "severity": "medium", "host": ip,
                                                "title": f"{svc}: {ip}" + (" (aviso do CERT.br)" if source == "certbr" else " (visto no flow)"),
                                                "description": certbr.SERVICES.get(service, {}).get("fix", "")})
            except Exception as e:
                logger.warning(f"CERT.br incidente: {e}")
    upd: dict = {"$set": {"last": max(cur.get("last") or "", at or now)}, "$addToSet": {"sources": source}}
    if cur["status"] == "resolved" and (at or now) > (cur.get("resolved_at") or ""):
        upd["$set"].update(status="open", reopened_at=now)
        upd["$inc"] = {"reopened": 1}
    if source == "certbr":
        upd["$push"] = {"notices": {"$each": [{"at": at or now, "line": extra.get("line", ""), "subject": extra.get("subject", "")[:200]}], "$slice": -20}}
    if extra.get("flow"):
        upd["$set"]["flow"] = extra["flow"]
    await db.cert_items.update_one({"key": key}, upd)
    return cur


async def _cert_cycle():
    cfg = await _cert_cfg()
    since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    rows = await db.cert_flow.find({"day": {"$gte": since}, "remotes_sum": {"$gte": cfg["min_remotes"]}}, {"_id": 0, "ts": 0}).to_list(20000)
    for r in rows:
        await _cert_upsert(r["ip"], r["service"], "flow", r.get("last"), flow={"day": r["day"], "last": r.get("last"), "bytes": r.get("bytes", 0),
                                                                            "remotes": r.get("remotes_sum", 0), "minutes": r.get("minutes", 0),
                                                                            "sample": r.get("sample") or []})
    # só flow e sem resposta para fora há N dias: resolvido sozinho
    limit = (datetime.now(timezone.utc) - timedelta(days=cfg["auto_resolve_days"])).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    async for it in db.cert_items.find({"status": "open", "sources": ["flow"], "last": {"$lt": limit}}, {"_id": 0, "key": 1}):
        await db.cert_items.update_one({"key": it["key"]}, {"$set": {"status": "resolved", "resolved_at": now, "resolved_by": "sem resposta para fora no flow"}})
        await db.incidents.update_many({"key": f"soc:certbr:{it['key']}", "status": {"$in": ["open", "ack"]}},
                                       {"$set": {"status": "resolved", "resolved_at": now, "resolved_by": "SOC", "updated_at": now}})


async def _cert_loop():
    await asyncio.sleep(90)
    while True:
        try:
            if (await _cert_cfg())["enabled"]:
                await _cert_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"CERT.br: {e}")
        await asyncio.sleep(300)


async def _cert_import(text: str, subject: str, via: str) -> dict:
    found = certbr.parse_notice(text, subject)
    own = (await flowstore.get_settings(db)).get("own_prefixes") or []
    nets = []
    for p in own:
        try:
            nets.append(ipaddress.ip_network(p, strict=False))
        except ValueError:
            pass
    now = datetime.now(timezone.utc).isoformat()
    added, outside = 0, []
    for x in found:
        a = ipaddress.ip_address(x["ip"])
        if nets and not any(a.version == n.version and a in n for n in nets):
            outside.append(x["ip"])
            continue
        await _cert_upsert(x["ip"], x["service"], "certbr", now, line=x["line"], subject=subject)
        added += 1
    await db.cert_notices.insert_one({"id": os.urandom(8).hex(), "at": now, "via": via, "subject": subject[:200], "count": added,
                                      "outside": outside[:200], "text": text[:20000]})
    return {"found": len(found), "added": added, "outside": outside[:50], "items": found[:200]}


@api.get("/soc/certbr/settings")
async def cert_settings(_: dict = Depends(require_admin)):
    cfg = await _cert_cfg()
    if not cfg["token"]:
        cfg["token"] = os.urandom(16).hex()
        await db.config.update_one({"key": "certbr"}, {"$set": {"token": cfg["token"]}}, upsert=True)
    return {**cfg, "services": {k: {"name": v["name"], "port": v["port"], "fix": v["fix"]} for k, v in certbr.SERVICES.items()},
            "own_prefixes": len((await flowstore.get_settings(db)).get("own_prefixes") or [])}


@api.put("/soc/certbr/settings")
async def cert_settings_put(body: dict, _: dict = Depends(require_admin)):
    cur = await _cert_cfg()
    try:
        cfg = certbr.clean_settings({**body, "token": cur["token"]})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.config.update_one({"key": "certbr"}, {"$set": cfg}, upsert=True)
    return cfg


@api.post("/soc/certbr/token")
async def cert_token(_: dict = Depends(require_admin)):
    t = os.urandom(16).hex()
    await db.config.update_one({"key": "certbr"}, {"$set": {"token": t}}, upsert=True)
    return {"token": t}


class CertImportIn(BaseModel):
    text: str
    subject: str = ""
    preview: bool = False


@api.post("/soc/certbr/import")
async def cert_import(body: CertImportIn, _: dict = Depends(require_admin)):
    if body.preview:
        return {"items": certbr.parse_notice(body.text[:200000], body.subject)[:500]}
    return await _cert_import(body.text[:200000], body.subject, "tela")


@api.post("/soc/certbr/inbound/{token}")
async def cert_inbound(token: str, request: Request):
    """Encaminhamento do e-mail do CERT.br (n8n, regra do servidor de e-mail…): JSON {subject, text} ou o texto puro."""
    cfg = await _cert_cfg()
    if not cfg["token"] or not hmac.compare_digest(token, cfg["token"]):
        raise HTTPException(status_code=403, detail="Token inválido")
    raw = (await request.body())[:500000].decode("utf-8", "replace")
    subject, text = "", raw
    try:
        j = json.loads(raw)
        if isinstance(j, dict):
            subject, text = str(j.get("subject") or ""), str(j.get("text") or j.get("body") or j.get("html") or "")
    except ValueError:
        pass
    text = re.sub(r"<[^>]+>", " ", text)
    r = await _cert_import(text, subject, "webhook")
    return {"found": r["found"], "added": r["added"]}


def _ipam_owner(ip: str, prefixes: list) -> str:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    best, desc = -1, ""
    for p in prefixes:
        try:
            n = ipaddress.ip_network(p["prefix"], strict=False)
        except (ValueError, KeyError):
            continue
        if n.version == a.version and a in n and n.prefixlen > best and (p.get("description") or "").strip():
            best, desc = n.prefixlen, f"{p['prefix']} — {p['description'].strip()}"
    return desc


@api.get("/soc/certbr/items")
async def cert_items(status: str = "open", _: dict = Depends(require_admin)):
    q = {"status": status} if status else {}
    rows = await db.cert_items.find(q, {"_id": 0}).sort("last", -1).to_list(3000)
    pfx = await db.ipam_prefixes.find({}, {"_id": 0, "prefix": 1, "description": 1}).to_list(20000)
    for r in rows:
        r["owner"] = _ipam_owner(r["ip"], pfx)
    return rows


class CertActIn(BaseModel):
    action: str
    note: str = ""


@api.post("/soc/certbr/items/{iid}")
async def cert_item_act(iid: str, body: CertActIn, admin: dict = Depends(require_admin)):
    st = {"resolve": "resolved", "ignore": "ignored", "reopen": "open"}.get(body.action)
    if not st:
        raise HTTPException(status_code=400, detail="Ação inválida")
    now = datetime.now(timezone.utc).isoformat()
    it = await db.cert_items.find_one({"id": iid}, {"_id": 0})
    if not it:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    await db.cert_items.update_one({"id": iid}, {"$set": {"status": st, "note": body.note[:300], "resolved_at": now if st != "open" else None,
                                                          "resolved_by": admin.get("email") if st != "open" else None}})
    if st != "open":
        await db.incidents.update_many({"key": f"soc:certbr:{it['key']}", "status": {"$in": ["open", "ack"]}},
                                       {"$set": {"status": "resolved", "resolved_at": now, "resolved_by": admin.get("email"), "updated_at": now}})
    return {"ok": True, "status": st}


@api.get("/soc/certbr/notices")
async def cert_notices(_: dict = Depends(require_admin)):
    return await db.cert_notices.find({}, {"_id": 0, "text": 0}).sort("at", -1).to_list(200)


# ---------- SOC: CVEs do firmware ----------
_cve_rt: dict = {"busy": False, "step": ""}


async def _cve_cfg() -> dict:
    doc = await db.config.find_one({"key": "cves"}, {"_id": 0}) or {}
    out = {**cvemod.DEFAULTS, **{k: v for k, v in doc.items() if k in cvemod.DEFAULTS}}
    out["has_key"] = bool(doc.get("nvd_key"))
    return out


async def _cve_devices() -> List[dict]:
    devs = await db.devices.find({}, {"_id": 0, "id": 1, "name": 1, "host": 1, "device_type": 1, "owner_id": 1}).to_list(5000)
    comp = {c["device_id"]: (c.get("firmware") or {}) async for c in db.compliance_results.find({}, {"_id": 0, "device_id": 1, "firmware": 1})}
    ovr = {o["device_id"]: o async for o in db.cve_overrides.find({}, {"_id": 0})}
    out = []
    for d in devs:
        fw = comp.get(d["id"]) or {}
        o = ovr.get(d["id"])
        if o and o.get("product") and o.get("version"):
            prods = [(o["product"], o["version"])]
        else:
            head = ""
            if d.get("device_type") == "cisco" and fw.get("version"):
                b = await db.backups.find_one({"device_id": d["id"], "ok": {"$ne": False}}, {"_id": 0, "content": 1}, sort=[("created_at", -1)])
                head = ((b or {}).get("content") or "")[:3000]
            prods = cvemod.products_for(d.get("device_type"), fw.get("version") or "", fw.get("model") or "", head)
        out.append({**d, "version": fw.get("version"), "model": fw.get("model"), "products": prods, "override": bool(o)})
    return out


async def _cve_refresh(force: bool = False) -> dict:
    if _cve_rt["busy"]:
        return {"busy": True}
    _cve_rt["busy"] = True
    done = {}
    try:
        cfgdoc = await db.config.find_one({"key": "cves"}, {"_id": 0}) or {}
        cfg = await _cve_cfg()
        key = vault.decrypt(cfgdoc.get("nvd_key", "")) if cfgdoc.get("nvd_key") else ""
        headers = {"User-Agent": "BastiON-cve/2.0", **({"apiKey": key} if key else {})}
        pause = 0.8 if key else 6.5
        needed = sorted({p for d in await _cve_devices() for p, _ in d["products"]})
        limit = (datetime.now(timezone.utc) - timedelta(days=cfg["refresh_days"])).isoformat()
        async with httpx.AsyncClient(timeout=90, headers=headers, follow_redirects=True) as c:
            kev = await db.config.find_one({"key": "cve_kev"}, {"_id": 0, "at": 1}) or {}
            if force or (kev.get("at") or "") < (datetime.now(timezone.utc) - timedelta(days=1)).isoformat():
                _cve_rt["step"] = "catálogo KEV (CISA)"
                try:
                    r = await c.get(cvemod.KEV_URL)
                    r.raise_for_status()
                    ids = sorted({v.get("cveID") for v in (r.json().get("vulnerabilities") or []) if v.get("cveID")})
                    await db.config.update_one({"key": "cve_kev"}, {"$set": {"ids": ids, "count": len(ids), "at": datetime.now(timezone.utc).isoformat(), "error": ""}}, upsert=True)
                except Exception as e:
                    await db.config.update_one({"key": "cve_kev"}, {"$set": {"error": f"{type(e).__name__}: {str(e)[:150]}"}}, upsert=True)
            for prod in needed:
                cur = await db.cve_db.find_one({"_id": prod}, {"_id": 0, "at": 1})
                if cur and (cur.get("at") or "") > limit and not force:
                    continue
                vendor, product = prod.split(":", 1)
                items, start, total, err = [], 0, None, ""
                try:
                    while total is None or start < total:
                        _cve_rt["step"] = f"NVD {cvemod.PRODUCT_LABEL.get(prod, prod)} ({start}/{total or '?'})"
                        r = await c.get(cvemod.NVD_URL, params={"virtualMatchString": f"cpe:2.3:o:{vendor}:{product}", "resultsPerPage": 2000, "startIndex": start})
                        if r.status_code == 404:
                            total = 0
                            break
                        r.raise_for_status()
                        js = r.json()
                        total = int(js.get("totalResults") or 0)
                        items += cvemod.compact_nvd(js, prod)
                        start += int(js.get("resultsPerPage") or 2000) or 2000
                        await asyncio.sleep(pause)
                except Exception as e:
                    err = f"{type(e).__name__}: {str(e)[:150]}"
                st = {"at": datetime.now(timezone.utc).isoformat(), "error": err}
                if not err:
                    st.update(items=items, count=len(items), ok_at=st["at"])
                await db.cve_db.update_one({"_id": prod}, {"$set": {"product": prod, **st}}, upsert=True)
                done[prod] = len(items) if not err else err
                await asyncio.sleep(pause)
        await _cve_evaluate()
    finally:
        _cve_rt["busy"], _cve_rt["step"] = False, ""
    return done


async def _cve_evaluate():
    cfg = await _cve_cfg()
    kev = set((await db.config.find_one({"key": "cve_kev"}, {"_id": 0, "ids": 1}) or {}).get("ids") or [])
    cache: dict = {}
    now = datetime.now(timezone.utc).isoformat()
    for d in await _cve_devices():
        found, prods = [], []
        for prod, ver in d["products"]:
            if prod not in cache:
                cache[prod] = (await db.cve_db.find_one({"_id": prod}, {"_id": 0, "items": 1}) or {}).get("items") or []
            found += cvemod.matches(cache[prod], ver, prod, kev)
            prods.append({"product": prod, "label": cvemod.PRODUCT_LABEL.get(prod, prod), "version": ver, "loaded": bool(cache[prod])})
        prev = await db.cve_results.find_one({"device_id": d["id"]}, {"_id": 0, "ids": 1, "version": 1}) or {}
        ids = [c["id"] for c in found]
        await db.cve_results.update_one({"device_id": d["id"]}, {"$set": {
            "device_id": d["id"], "device_name": d["name"], "device_type": d.get("device_type"), "host": d.get("host"), "model": d.get("model"),
            "version": d.get("version"), "products": prods, "override": d["override"], "summary": cvemod.summary(found), "cves": found[:400],
            "ids": ids, "at": now}}, upsert=True)
        before = set(prev.get("ids") or [])
        old = before if prev.get("version") == d.get("version") else set()   # firmware mudou: reavalia o que continua
        for c in found:
            if c["id"] in old:
                continue
            if not ((cfg["alert_kev"] and c["kev"]) or (c["score"] or 0) >= float(cfg["alert_min_score"] or 99)):
                continue
            try:
                await inc_center.ingest("soc", {"key": f"soc:cve:{d['id']}:{c['id']}", "status": "problem",
                                                "severity": "high" if c["kev"] else "medium", "device_id": d["id"], "host": d.get("host", ""),
                                                "host_name": d["name"],
                                                "title": f"{c['id']}{' (explorada ativamente — KEV)' if c['kev'] else ''} em {d['name']} — {d.get('version')}",
                                                "description": f"CVSS {c['score']}. {c['desc'][:300]}\nhttps://nvd.nist.gov/vuln/detail/{c['id']}"})
            except Exception as e:
                logger.warning(f"CVE incidente: {e}")
        for gone in before - set(ids):
            await db.incidents.update_many({"key": f"soc:cve:{d['id']}:{gone}", "status": {"$in": ["open", "ack"]}},
                                           {"$set": {"status": "resolved", "resolved_at": now, "resolved_by": "firmware atualizado", "updated_at": now}})


async def _cve_loop():
    await asyncio.sleep(180)
    while True:
        try:
            if (await _cve_cfg())["enabled"]:
                await _cve_refresh()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"CVE: {e}")
        await asyncio.sleep(3600)


@api.get("/soc/cves/summary")
async def cve_summary(_: dict = Depends(require_admin)):
    rows = await db.cve_results.find({}, {"_id": 0, "cves": 0, "ids": 0}).to_list(5000)
    ids = {d["id"] for d in await db.devices.find({}, {"_id": 0, "id": 1}).to_list(5000)}
    rows = [r for r in rows if r["device_id"] in ids]
    rows.sort(key=lambda r: (-(r["summary"] or {}).get("kev", 0), -(r["summary"] or {}).get("max_score", 0), r["device_name"]))
    db_st = await db.cve_db.find({}, {"_id": 0, "items": 0}).to_list(100)
    kev = await db.config.find_one({"key": "cve_kev"}, {"_id": 0, "ids": 0}) or {}
    return {"devices": rows, "products": db_st, "kev": kev, "busy": _cve_rt["busy"], "step": _cve_rt["step"], "settings": await _cve_cfg(),
            "labels": cvemod.PRODUCT_LABEL}


@api.get("/soc/cves/device/{device_id}")
async def cve_device(device_id: str, _: dict = Depends(require_admin)):
    r = await db.cve_results.find_one({"device_id": device_id}, {"_id": 0, "ids": 0})
    if not r:
        raise HTTPException(status_code=404, detail="Sem análise para esse equipamento ainda")
    return r


class CveOverrideIn(BaseModel):
    product: str = ""
    version: str = ""


@api.put("/soc/cves/override/{device_id}")
async def cve_override(device_id: str, body: CveOverrideIn, _: dict = Depends(require_admin)):
    if not body.product or not body.version:
        await db.cve_overrides.delete_one({"device_id": device_id})
    else:
        if not re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_.-]+", body.product.strip().lower()):
            raise HTTPException(status_code=400, detail="Produto no formato fornecedor:produto da NVD (ex.: mikrotik:routeros)")
        await db.cve_overrides.update_one({"device_id": device_id}, {"$set": {"device_id": device_id, "product": body.product.strip().lower(),
                                                                             "version": body.version.strip()[:60]}}, upsert=True)
    _bg(_cve_refresh())
    return {"ok": True}


class CveSettingsIn(BaseModel):
    enabled: bool = True
    alert_kev: bool = True
    alert_min_score: float = 9.0
    refresh_days: int = 3
    nvd_key: str = ""
    clear_key: bool = False


@api.put("/soc/cves/settings")
async def cve_settings_put(body: CveSettingsIn, _: dict = Depends(require_admin)):
    d = {"enabled": body.enabled, "alert_kev": body.alert_kev, "alert_min_score": max(0.0, min(10.1, body.alert_min_score)),
         "refresh_days": max(1, min(30, body.refresh_days))}
    if body.nvd_key.strip():
        d["nvd_key"] = vault.encrypt(body.nvd_key.strip())
    elif body.clear_key:
        d["nvd_key"] = ""
    await db.config.update_one({"key": "cves"}, {"$set": d}, upsert=True)
    return await _cve_cfg()


@api.post("/soc/cves/refresh")
async def cve_refresh(_: dict = Depends(require_admin)):
    if _cve_rt["busy"]:
        raise HTTPException(status_code=409, detail=f"Já está atualizando ({_cve_rt['step']})")
    _bg(_cve_refresh(force=True))
    return {"started": True}


# ---------- SOC: login único (SSO / OpenID Connect) ----------
_sso_cache: dict = {}


async def _sso_cfg() -> dict:
    doc = await db.config.find_one({"key": "sso"}, {"_id": 0}) or {}
    return {"providers": doc.get("providers") or [], "secrets": doc.get("secrets") or {},
            "public_url": (doc.get("public_url") or os.environ.get("PUBLIC_URL", "")).rstrip("/"),
            "password_login": doc.get("password_login") if doc.get("password_login") in ("all", "admins") else "all",
            "sso_users": doc.get("sso_users") if doc.get("sso_users") in ("all", "selected") else "all"}


async def _sso_get(url: str, ttl: int = 3600, force: bool = False) -> dict:
    hit = _sso_cache.get(url)
    if hit and not force and time.time() - hit[0] < ttl:
        return hit[1]
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        js = r.json()
    _sso_cache[url] = (time.time(), js)
    return js


async def _sso_disc(prov: dict, force: bool = False) -> dict:
    d = await _sso_get(prov["issuer"].rstrip("/") + "/.well-known/openid-configuration", force=force)
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not d.get(k):
            raise ssomod.SsoError(f"Discovery do provedor sem {k}")
    return d


def _sso_redirect_uri(cfg: dict, pid: str) -> str:
    return f"{cfg['public_url']}/api/auth/sso/{pid}/callback"


def _sso_fail(cfg: dict, msg: str):
    from urllib.parse import quote
    return RedirectResponse(f"{cfg['public_url'] or ''}/login?sso_error={quote(msg[:200])}", status_code=302)


@api.get("/auth/sso/providers")
async def sso_providers():
    cfg = await _sso_cfg()
    return {"providers": [{"id": p["id"], "name": p["name"]} for p in cfg["providers"] if p.get("enabled")],
            "password_login": cfg["password_login"]}


@api.get("/auth/sso/{pid}/start")
async def sso_start(pid: str):
    cfg = await _sso_cfg()
    prov = next((p for p in cfg["providers"] if p["id"] == pid and p.get("enabled")), None)
    if not prov:
        return _sso_fail(cfg, "Provedor de login não encontrado")
    if not cfg["public_url"]:
        return _sso_fail(cfg, "Endereço público do BastiON não configurado (PUBLIC_URL)")
    try:
        disc = await _sso_disc(prov)
    except Exception as e:
        return _sso_fail(cfg, f"Não consegui falar com o provedor: {e}")
    state, nonce = ssomod.b64url(os.urandom(24)), ssomod.b64url(os.urandom(16))
    verifier, challenge = ssomod.pkce()
    await db.sso_states.insert_one({"state": state, "nonce": nonce, "verifier": verifier, "pid": pid, "ts": datetime.now(timezone.utc)})
    return RedirectResponse(ssomod.auth_url(disc, prov, _sso_redirect_uri(cfg, pid), state, nonce, challenge), status_code=302)


@api.get("/auth/sso/{pid}/callback")
async def sso_callback(pid: str, request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    cfg = await _sso_cfg()
    ip = security.client_ip(request)
    ua = request.headers.get("user-agent", "")
    if error:
        return _sso_fail(cfg, f"O provedor recusou: {error_description or error}")
    st = await db.sso_states.find_one_and_delete({"state": state, "pid": pid}) if state else None
    if not st or (datetime.now(timezone.utc) - (st["ts"] if st["ts"].tzinfo else st["ts"].replace(tzinfo=timezone.utc))).total_seconds() > 600:
        return _sso_fail(cfg, "Login expirado ou repetido — tente de novo")
    prov = next((p for p in cfg["providers"] if p["id"] == pid and p.get("enabled")), None)
    if not prov:
        return _sso_fail(cfg, "Provedor de login não encontrado")
    email = ""
    try:
        disc = await _sso_disc(prov)
        secret = vault.decrypt(cfg["secrets"].get(pid, ""))
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": _sso_redirect_uri(cfg, pid),
                "client_id": prov["client_id"], "code_verifier": st["verifier"]}
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(disc["token_endpoint"], data={**data, **({"client_secret": secret} if secret else {})}, headers={"Accept": "application/json"})
            if r.status_code in (400, 401) and secret:
                r = await c.post(disc["token_endpoint"], data=data, auth=(prov["client_id"], secret), headers={"Accept": "application/json"})
            if r.status_code >= 300:
                raise ssomod.SsoError(f"troca do código falhou (HTTP {r.status_code}): {r.text[:150]}")
            tok = r.json()
            if not tok.get("id_token"):
                raise ssomod.SsoError("o provedor não devolveu id_token (escopo openid?)")
            jwks = await _sso_get(disc["jwks_uri"])
            try:
                claims = ssomod.verify_id_token(tok["id_token"], jwks, prov, disc.get("issuer") or prov["issuer"], st["nonce"])
            except ssomod.SsoError as e:
                if "Assinatura" not in str(e):
                    raise
                jwks = await _sso_get(disc["jwks_uri"], force=True)                # chave girou
                claims = ssomod.verify_id_token(tok["id_token"], jwks, prov, disc.get("issuer") or prov["issuer"], st["nonce"])
            info = None
            if not claims.get("email") and disc.get("userinfo_endpoint") and tok.get("access_token"):
                ui = await c.get(disc["userinfo_endpoint"], headers={"Authorization": f"Bearer {tok['access_token']}"})
                if ui.status_code < 300:
                    info = ui.json()
        who = ssomod.identity(claims, info)
        email = who["email"]
        if not email:
            raise ssomod.SsoError("o provedor não informou o e-mail da conta")
        if not who["verified"]:
            raise ssomod.SsoError("e-mail não verificado no provedor")
        if not ssomod.domain_ok(prov, email):
            raise ssomod.SsoError(f"domínio de {email} não é permitido")
        groups = ssomod.groups_of(who["claims"], prov.get("groups_claim") or "groups")
        mapped = ssomod.role_from_groups(prov, groups)
        if prov.get("require_group") and not mapped:
            raise ssomod.SsoError("sua conta não está em nenhum grupo liberado para o BastiON")
        user = await db.users.find_one({"email": email}, {"_id": 0})
        if user and not ssomod.sso_allowed(user, cfg):
            raise ssomod.SsoError(f"o login único não está liberado para {email} — entre com a senha ou peça ao administrador")
        if not user:
            if cfg["sso_users"] == "selected":
                raise ssomod.SsoError(f"{email} não tem usuário liberado para o login único — peça ao administrador")
            if not prov.get("auto_create"):
                raise ssomod.SsoError(f"{email} não tem usuário no BastiON — peça ao administrador")
            user = {"id": os.urandom(8).hex(), "email": email, "name": who["name"] or email.split("@")[0], "role": mapped or prov["default_role"],
                    "password_hash": hash_password(os.urandom(24).hex()), "sso": pid, "created_at": datetime.now(timezone.utc).isoformat()}
            await db.users.insert_one(dict(user))
            logger.info("Usuário %s criado pelo login único (%s)", email, pid)
        elif mapped and mapped != user.get("role"):
            admins = await db.users.count_documents({"role": "admin"})
            if not (user.get("role") == "admin" and admins <= 1):
                await db.users.update_one({"id": user["id"]}, {"$set": {"role": mapped}})
                user["role"] = mapped
        await _login_gate(user, ip, ua)
    except HTTPException as e:
        return _sso_fail(cfg, str(e.detail))
    except Exception as e:
        await security.log_event(db, email or f"sso:{pid}", ip, ua, False, f"sso: {str(e)[:80]}")
        return _sso_fail(cfg, f"Login único falhou: {e}")
    await security.log_event(db, user["email"], ip, ua, True, f"sso:{pid}", user["id"])
    await db.users.update_one({"id": user["id"]}, {"$set": {"last_login_at": datetime.now(timezone.utc).isoformat(), "last_login_ip": ip}})
    _bg(_login_anomaly(user, ip, ua))
    one = ssomod.b64url(os.urandom(24))
    await db.sso_codes.insert_one({"code": one, "user_id": user["id"], "ts": datetime.now(timezone.utc)})
    return RedirectResponse(f"{cfg['public_url']}/login?sso={one}", status_code=302)


class SsoExchangeIn(BaseModel):
    code: str


@api.post("/auth/sso/exchange")
async def sso_exchange(body: SsoExchangeIn):
    d = await db.sso_codes.find_one_and_delete({"code": body.code}) if body.code else None
    if not d or (datetime.now(timezone.utc) - (d["ts"] if d["ts"].tzinfo else d["ts"].replace(tzinfo=timezone.utc))).total_seconds() > 90:
        raise HTTPException(status_code=401, detail="Login expirado — entre de novo")
    user = await db.users.find_one({"id": d["user_id"]})
    if not user:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")
    return _session_payload(user)


@api.get("/soc/sso/settings")
async def sso_settings(_: dict = Depends(require_admin)):
    cfg = await _sso_cfg()
    return {"providers": [{**p, "has_secret": bool(cfg["secrets"].get(p["id"])), "redirect_uri": _sso_redirect_uri(cfg, p["id"])} for p in cfg["providers"]],
            "public_url": cfg["public_url"], "password_login": cfg["password_login"], "sso_users": cfg["sso_users"], "presets": ssomod.PRESETS,
            "users": [{"id": u["id"], "email": u["email"], "role": u.get("role"), "login_mode": u.get("login_mode") or "default"}
                      async for u in db.users.find({}, {"_id": 0, "id": 1, "email": 1, "role": 1, "login_mode": 1})]}


class SsoSettingsIn(BaseModel):
    providers: List[dict] = []
    public_url: str = ""
    password_login: str = "all"
    sso_users: str = "all"


@api.put("/soc/sso/settings")
async def sso_settings_put(body: SsoSettingsIn, admin: dict = Depends(require_admin)):
    cur = await _sso_cfg()
    provs, secrets, seen = [], {}, set()
    for p in body.providers[:10]:
        try:
            c = ssomod.clean_provider(p)
        except ssomod.SsoError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if c["id"] in seen:
            raise HTTPException(status_code=400, detail=f"Identificador repetido: {c['id']}")
        seen.add(c["id"])
        provs.append(c)
        if p.get("client_secret"):
            secrets[c["id"]] = vault.encrypt(str(p["client_secret"]))
        elif not p.get("clear_secret") and cur["secrets"].get(c["id"]):
            secrets[c["id"]] = cur["secrets"][c["id"]]
    pub = body.public_url.strip().rstrip("/")
    if pub and not re.fullmatch(r"https?://[A-Za-z0-9.:\-]+", pub):
        raise HTTPException(status_code=400, detail="Endereço público: https://nome.do.bastion (sem caminho)")
    mode = body.password_login if body.password_login in ("all", "admins") else "all"
    if mode == "admins" and admin.get("role") != "admin":
        mode = "all"
    scope = body.sso_users if body.sso_users in ("all", "selected") else "all"
    await db.config.update_one({"key": "sso"}, {"$set": {"providers": provs, "secrets": secrets, "public_url": pub, "password_login": mode,
                                                         "sso_users": scope}}, upsert=True)
    return await sso_settings(_=admin)


@api.post("/soc/sso/test/{pid}")
async def sso_test(pid: str, _: dict = Depends(require_admin)):
    cfg = await _sso_cfg()
    prov = next((p for p in cfg["providers"] if p["id"] == pid), None)
    if not prov:
        raise HTTPException(status_code=404, detail="Salve o provedor antes de testar")
    try:
        disc = await _sso_disc(prov, force=True)
        jwks = await _sso_get(disc["jwks_uri"], force=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falhou: {e}"[:300])
    if (disc.get("issuer") or "").rstrip("/") != prov["issuer"].rstrip("/") and "{tenantid}" not in (disc.get("issuer") or ""):
        raise HTTPException(status_code=400, detail=f"O provedor diz que o emissor é {disc.get('issuer')} — use exatamente esse valor")
    return {"ok": True, "issuer": disc.get("issuer"), "keys": len(jwks.get("keys") or []), "authorization_endpoint": disc["authorization_endpoint"],
            "pkce": "S256" in (disc.get("code_challenge_methods_supported") or ["S256"])}


# ---------- FTP (arquivos dos equipamentos, direto ou pelos jumps) ----------
def _ftp_root() -> str:
    root = os.environ.get("FTP_ROOT") or ("/data/ftp" if os.path.isdir("/data") else str(ROOT_DIR / "ftp_data"))
    os.makedirs(root, exist_ok=True)
    return root


async def _ftp_cfg() -> dict:
    doc = await db.config.find_one({"key": "ftp"}, {"_id": 0}) or {}
    try:
        return ftpmod.clean_settings({}, doc)
    except ValueError:
        return dict(ftpmod.DEFAULTS)


def _ftp_folder(v: str) -> str:
    parts = [p for p in re.split(r"[\\/]+", str(v or "").strip()) if p not in ("", ".", "..")]
    clean = "/".join(re.sub(r"[^\w.\- ]", "_", p)[:80] for p in parts)[:300]
    return clean


def _ftp_abs(rel: str) -> str:
    root = os.path.realpath(_ftp_root())
    p = os.path.realpath(os.path.join(root, _ftp_folder(rel)))
    if p != root and not p.startswith(root + os.sep):
        raise HTTPException(status_code=400, detail="Caminho fora da área de arquivos")
    return p


def _ftp_active(a: dict, now: Optional[str] = None) -> bool:
    now = now or datetime.now(timezone.utc).isoformat()
    return a.get("enabled", True) is not False and (not a.get("expires_at") or a["expires_at"] > now)


async def _ftp_auth(scope: str, user: str, password: str, ip: str):
    cfg = await _ftp_cfg()
    a = await db.ftp_accesses.find_one({"username_lc": (user or "").lower(), "via": scope}, {"_id": 0})
    if not a or not _ftp_active(a) or not hmac.compare_digest(vault.decrypt(a.get("password", "")).encode(), (password or "").encode()):
        return None
    if a.get("allow_ips"):
        try:
            ipa = ipaddress.ip_address(ip)
            if not any(ipa in ipaddress.ip_network(n, strict=False) for n in a["allow_ips"]):
                return None
        except ValueError:
            return None
    await db.ftp_accesses.update_one({"id": a["id"]}, {"$set": {"last_used": datetime.now(timezone.utc).isoformat(), "last_ip": ip}})
    return ftpmod.Account(a["id"], a["name"], _ftp_abs(a.get("folder") or ""), bool(a.get("write")), cfg["max_mb"] * 1024 * 1024)


async def _ftp_event(e: dict):
    now = datetime.now(timezone.utc)
    acc = await db.ftp_accesses.find_one({"id": e.get("access_id")}, {"_id": 0, "name": 1, "folder": 1}) if e.get("access_id") else None
    await db.ftp_events.insert_one({"id": os.urandom(8).hex(), "ts": now, "at": now.isoformat(), **e,
                                    "access_name": (acc or {}).get("name", ""), "folder": (acc or {}).get("folder", "")})


async def _ftp_connect_jump(agent_id: str):
    priv, _, _ = await _get_ssh_key()
    chain = await _agent_chain(agent_id)
    w = SSHClientWrapper([_agent_hop(a, priv) for a in chain])
    await w.connect()
    return w.conn, w.close, " → ".join(a["name"] for a in chain)


ftp_mgr = ftpmod.Manager(_ftp_auth, _ftp_event, _ftp_connect_jump, "")


async def _ftp_reconcile():
    cfg = await _ftp_cfg()
    now = datetime.now(timezone.utc).isoformat()
    acc = [a for a in await db.ftp_accesses.find({}, {"_id": 0, "via": 1, "enabled": 1, "expires_at": 1}).to_list(5000) if _ftp_active(a, now)]
    await ftp_mgr.reconcile(cfg, any(a["via"] == "local" for a in acc), sorted({a["via"] for a in acc if a["via"] != "local"}))


async def _ftp_loop():
    await asyncio.sleep(15)
    while True:
        try:
            await _ftp_reconcile()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"FTP: {e}")
        await asyncio.sleep(20)


def _ftp_public(a: dict, show_pw: bool = True) -> dict:
    out = {k: v for k, v in a.items() if k not in ("password", "username_lc")}
    if show_pw:
        out["password"] = vault.decrypt(a.get("password", ""))
    out["active"] = _ftp_active(a)
    return out


def _ftp_scope(user: dict) -> dict:
    return {} if user.get("role") == "admin" else {"owner_id": user["id"]}


@api.get("/ftp/settings")
async def ftp_settings(_: dict = Depends(require_admin)):
    return {**await _ftp_cfg(), "root": _ftp_root(), "public_host": urlparse(os.environ.get("PUBLIC_URL", "")).hostname or ""}


@api.put("/ftp/settings")
async def ftp_settings_put(body: dict, _: dict = Depends(require_admin)):
    cur = await db.config.find_one({"key": "ftp"}, {"_id": 0}) or {}
    try:
        cfg = ftpmod.clean_settings(body or {}, cur)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.config.update_one({"key": "ftp"}, {"$set": {"key": "ftp", **cfg}}, upsert=True)
    for b in list(ftp_mgr.jumps.values()):
        await b.stop()                       # portas/IP novos: reabre tudo
    _bg(_ftp_reconcile())
    return cfg


@api.get("/ftp/status")
async def ftp_status(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cfg = await _ftp_cfg()
    st = ftp_mgr.status()
    names = {a["id"]: a["name"] async for a in db.agents.find({}, {"_id": 0, "id": 1, "name": 1})}
    for aid, j in st["jumps"].items():
        j["name"] = names.get(aid, aid)
        j["port"] = cfg["jump_port"]
    st["local"]["host"] = cfg["local_ip"] or urlparse(os.environ.get("PUBLIC_URL", "")).hostname or ""
    st["enabled"] = cfg["enabled"]
    st["local_enabled"] = cfg["local_enabled"]
    return st


class FtpAccessIn(BaseModel):
    name: str
    via: str = "local"                 # "local" ou id do agente (jump)
    folder: str = ""
    write: bool = True
    hours: int = 24                    # 0 = sem prazo (backup automático do equipamento)
    allow_ips: List[str] = []
    username: str = ""
    password: str = ""


@api.get("/ftp/accesses")
async def ftp_accesses(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    rows = await db.ftp_accesses.find(_ftp_scope(user), {"_id": 0}).sort("created_at", -1).to_list(1000)
    return [_ftp_public(a) for a in rows]


@api.post("/ftp/accesses")
async def ftp_access_add(body: FtpAccessIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    name = body.name.strip()[:60]
    if not name:
        raise HTTPException(status_code=400, detail="Dê um nome (ex.: Backup OLT Guaçuí)")
    if body.via != "local":
        await _get_agent_for(user, body.via) if user.get("role") != "admin" else None
        if not await db.agents.find_one({"id": body.via}, {"_id": 0, "id": 1}):
            raise HTTPException(status_code=404, detail="Jump não encontrado")
    nets = []
    for x in body.allow_ips:
        x = x.strip()
        if x:
            try:
                nets.append(str(ipaddress.ip_network(x, strict=False)))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"IP/rede inválido: {x}")
    uname = (body.username.strip() or ("ftp" + os.urandom(3).hex()))[:32]
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", uname):
        raise HTTPException(status_code=400, detail="Usuário: 3 a 32 caracteres (letras, números, . _ -)")
    if await db.ftp_accesses.find_one({"username_lc": uname.lower(), "via": body.via}):
        raise HTTPException(status_code=409, detail=f"Já existe o usuário {uname} nesse caminho")
    pw = body.password.strip() or tacmod.gen_secret(12)
    if len(pw) < 8 or not re.fullmatch(r"[A-Za-z0-9._-]+", pw):
        raise HTTPException(status_code=400, detail="Senha: 8+ caracteres só com letras, números, . _ - (os equipamentos se perdem com @ : / na URL)")
    folder = _ftp_folder(body.folder) or _ftp_folder(name)
    os.makedirs(_ftp_abs(folder), exist_ok=True)
    now = datetime.now(timezone.utc)
    doc = {"id": os.urandom(8).hex(), "name": name, "username": uname, "username_lc": uname.lower(), "password": vault.encrypt(pw),
           "via": body.via, "folder": folder, "write": body.write, "allow_ips": nets[:50], "enabled": True,
           "expires_at": (now + timedelta(hours=max(1, min(24 * 90, body.hours)))).isoformat() if body.hours else None,
           "owner_id": user["id"], "created_by": user.get("email", ""), "created_at": now.isoformat(), "last_used": None}
    await db.ftp_accesses.insert_one(dict(doc))
    _bg(_ftp_reconcile())
    return _ftp_public(doc)


async def _ftp_get(aid: str, user: dict) -> dict:
    a = await db.ftp_accesses.find_one({"id": aid, **_ftp_scope(user)}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Acesso não encontrado")
    return a


class FtpRenewIn(BaseModel):
    hours: int = 24
    enabled: bool = True


@api.post("/ftp/accesses/{aid}/renew")
async def ftp_access_renew(aid: str, body: FtpRenewIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _ftp_get(aid, user)
    exp = (datetime.now(timezone.utc) + timedelta(hours=max(1, min(24 * 90, body.hours)))).isoformat() if body.hours else None
    await db.ftp_accesses.update_one({"id": aid}, {"$set": {"expires_at": exp, "enabled": body.enabled}})
    _bg(_ftp_reconcile())
    return _ftp_public(await _ftp_get(aid, user))


@api.delete("/ftp/accesses/{aid}")
async def ftp_access_del(aid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _ftp_get(aid, user)
    await db.ftp_accesses.delete_one({"id": aid})
    _bg(_ftp_reconcile())
    return {"ok": True}


@api.get("/ftp/events")
async def ftp_events(access_id: str = "", limit: int = 300, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    f: dict = {}
    if access_id:
        await _ftp_get(access_id, user)
        f["access_id"] = access_id
    elif user.get("role") != "admin":
        ids = [a["id"] async for a in db.ftp_accesses.find({"owner_id": user["id"]}, {"_id": 0, "id": 1})]
        f["access_id"] = {"$in": ids}
    return await db.ftp_events.find(f, {"_id": 0, "ts": 0}).sort("at", -1).to_list(max(1, min(2000, limit)))


# --- arquivos pela web (a mesma pasta que o FTP usa)
@api.get("/ftp/files")
async def ftp_files(path: str = "", user: dict = Depends(get_current_user)):
    _no_viewer(user)
    p = _ftp_abs(path)
    if not os.path.isdir(p):
        raise HTTPException(status_code=404, detail="Pasta não existe")
    out = []
    for n in sorted(os.listdir(p), key=str.lower):
        if n.startswith(".") or n.endswith(".parcial"):
            continue
        st = os.stat(os.path.join(p, n))
        out.append({"name": n, "dir": os.path.isdir(os.path.join(p, n)), "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    du = sum(f.stat().st_size for f in Path(_ftp_root()).rglob("*") if f.is_file())
    return {"path": _ftp_folder(path), "items": sorted(out, key=lambda x: (not x["dir"], x["name"].lower())), "total_bytes": du}


@api.get("/ftp/files/download")
async def ftp_file_download(path: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    p = _ftp_abs(path)
    if not os.path.isfile(p):
        raise HTTPException(status_code=404, detail="Arquivo não existe")
    await _ftp_event({"action": "download", "path": "/" + _ftp_folder(path), "ok": True, "bytes": os.path.getsize(p), "user": user.get("email", ""),
                      "peer": "", "via": "web", "scope": "web"})
    return FileResponse(p, media_type="application/octet-stream", filename=os.path.basename(p))


@api.post("/ftp/files/upload")
async def ftp_file_upload(path: str = Form(""), file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cfg = await _ftp_cfg()
    d = _ftp_abs(path)
    os.makedirs(d, exist_ok=True)
    name = re.sub(r"[^\w.\- ]", "_", os.path.basename(file.filename or "arquivo"))[:120] or "arquivo"
    dst = os.path.join(d, name)
    tmp = dst + ".parcial"
    size, lim = 0, cfg["max_mb"] * 1024 * 1024
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > lim:
                    raise HTTPException(status_code=413, detail=f"Arquivo maior que {cfg['max_mb']} MB (ajuste em Ajustes)")
                out.write(chunk)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    await _ftp_event({"action": "upload", "path": "/" + _ftp_folder(os.path.join(path, name)), "ok": True, "bytes": size,
                      "user": user.get("email", ""), "peer": "", "via": "web", "scope": "web"})
    return {"name": name, "size": size}


class FtpPathIn(BaseModel):
    path: str
    to: str = ""


@api.post("/ftp/files/mkdir")
async def ftp_file_mkdir(body: FtpPathIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    if not _ftp_folder(body.path):
        raise HTTPException(status_code=400, detail="Nome de pasta inválido")
    os.makedirs(_ftp_abs(body.path), exist_ok=True)
    return {"ok": True}


@api.post("/ftp/files/rename")
async def ftp_file_rename(body: FtpPathIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    src, dst = _ftp_abs(body.path), _ftp_abs(body.to)
    if not os.path.exists(src) or not _ftp_folder(body.to):
        raise HTTPException(status_code=400, detail="Origem ou destino inválido")
    if os.path.exists(dst):
        raise HTTPException(status_code=409, detail="Já existe um item com esse nome")
    os.replace(src, dst)
    return {"ok": True}


@api.delete("/ftp/files")
async def ftp_file_delete(path: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    p = _ftp_abs(path)
    if p == os.path.realpath(_ftp_root()):
        raise HTTPException(status_code=400, detail="Não dá para apagar a raiz")
    if os.path.isdir(p):
        if os.listdir(p):
            raise HTTPException(status_code=409, detail="A pasta não está vazia")
        os.rmdir(p)
    elif os.path.isfile(p):
        os.remove(p)
    else:
        raise HTTPException(status_code=404, detail="Não existe")
    await _ftp_event({"action": "delete", "path": "/" + _ftp_folder(path), "ok": True, "user": user.get("email", ""), "peer": "", "via": "web", "scope": "web"})
    return {"ok": True}


# --- arquivos direto com o equipamento (SFTP/SCP pelo mesmo caminho do SSH: BastiON → jump → equipamento)
# Serve quando o equipamento não alcança o BastiON nem o jump (NAT no meio): quem abre a conexão é o BastiON.
_xfer_jobs: Dict[str, dict] = {}


def _xfer_public(j: dict) -> dict:
    return {k: v for k, v in j.items() if not k.startswith("_")}


async def _xfer_device(user: dict, device_id: str) -> dict:
    _no_viewer(user)
    dev = await _get_device_for(user, device_id)
    if dev.get("protocol") == "telnet":
        raise HTTPException(status_code=400, detail="Equipamento por Telnet: SFTP/SCP precisam de SSH")
    return dev


async def _xfer_connect(dev: dict):
    hops, dtype = await _device_hops(dev)
    w = SSHClientWrapper(hops, device_type=dtype)
    await w.connect()
    return w


def _sftp_hint(dev: dict) -> str:
    t = (dev.get("device_type") or "").lower()
    return {"huawei": "No Huawei: 'sftp server enable' e o usuário com 'service-type ssh' + 'ssh user <nome> service-type all'.",
            "cisco": "No Cisco: 'ip scp server enable' (o BastiON tenta SCP quando não há SFTP).",
            "zte": "Na ZTE: confira se o servidor SFTP/SCP está ligado na versão (ssh server … sftp).",
            "juniper": "No Junos SFTP já vem ligado; confira 'set system services ssh sftp-server'.",
            "mikrotik": "No MikroTik o SFTP vem com o SSH (/ip service ssh)."}.get(t, "Ligue o servidor SFTP (ou SCP) no equipamento.")


@api.get("/ftp/device/ls")
async def xfer_ls(device_id: str, path: str = ".", user: dict = Depends(get_current_user)):
    dev = await _xfer_device(user, device_id)
    try:
        w = await _xfer_connect(dev)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não conectou no equipamento: {str(e)[:200]}")
    try:
        async with w.conn.start_sftp_client() as sftp:
            base = await sftp.realpath(path or ".")
            items = []
            for n in await sftp.readdir(base):
                name = n.filename if isinstance(n.filename, str) else n.filename.decode("utf-8", "replace")
                if name in (".", ".."):
                    continue
                a = n.attrs
                isdir = bool(a.permissions is not None and (a.permissions & 0o170000) == 0o040000) or (a.type == 2)
                items.append({"name": name, "dir": isdir, "size": a.size or 0,
                              "mtime": datetime.fromtimestamp(a.mtime, timezone.utc).isoformat() if a.mtime else None})
            base = base if isinstance(base, str) else base.decode("utf-8", "replace")
            return {"path": base, "items": sorted(items, key=lambda x: (not x["dir"], x["name"].lower())), "method": "sftp"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"O equipamento não abriu SFTP para listar ({type(e).__name__}: {str(e)[:120]}). "
                                                    f"{_sftp_hint(dev)} Sem listar, ainda dá para trazer/enviar informando o caminho (tenta SCP).")
    finally:
        await w.close()


class XferIn(BaseModel):
    device_id: str
    direction: str                     # pull = equipamento → BastiON; push = BastiON → equipamento
    remote_path: str
    local_path: str = ""               # pull: pasta de destino na área de arquivos; push: arquivo da área


async def _xfer_run(job: dict, dev: dict, local_abs: str, remote: str):
    import asyncssh
    t0 = time.monotonic()

    def prog(_s, _d, done, total):
        job["bytes"], job["total"] = int(done), int(total or 0)
    tmp = local_abs + ".parcial"
    errors = []
    w = None
    try:
        job["state"] = "conectando"
        w = await _xfer_connect(dev)
        job["state"] = "copiando"
        for method in ("sftp", "scp"):
            try:
                if method == "sftp":
                    async with w.conn.start_sftp_client() as sftp:
                        if job["direction"] == "pull":
                            await sftp.get(remote, tmp, progress_handler=prog)
                        else:
                            await sftp.put(local_abs, remote, progress_handler=prog)
                else:
                    if job["direction"] == "pull":
                        await asyncssh.scp((w.conn, remote), tmp, progress_handler=prog)
                    else:
                        await asyncssh.scp(local_abs, (w.conn, remote), progress_handler=prog)
                job["method"] = method
                break
            except Exception as e:
                errors.append(f"{method.upper()}: {type(e).__name__} {str(e)[:150]}")
        else:
            raise RuntimeError(" · ".join(errors) + f". {_sftp_hint(dev)}")
        if job["direction"] == "pull":
            os.replace(tmp, local_abs)
            job["bytes"] = job["total"] = os.path.getsize(local_abs)
        job["state"], job["ok"] = "pronto", True
    except Exception as e:
        job["state"], job["ok"], job["error"] = "erro", False, str(e)[:600]
        try:
            os.remove(tmp)
        except OSError:
            pass
    finally:
        if w:
            try:
                await w.close()
            except Exception:
                pass
        job["secs"] = round(time.monotonic() - t0, 1)
        job["ended_at"] = datetime.now(timezone.utc).isoformat()
        await _ftp_event({"action": "download" if job["direction"] == "push" else "upload", "path": job["local"], "ok": job.get("ok", False),
                          "bytes": job.get("bytes", 0), "secs": job["secs"], "error": job.get("error", "")[:120],
                          "user": job["user"], "peer": dev.get("host", ""), "via": f"{job['method'] or 'ssh'} {dev.get('name', '')}",
                          "scope": "device", "remote": remote})


@api.post("/ftp/device/transfer")
async def xfer_start(body: XferIn, user: dict = Depends(get_current_user)):
    dev = await _xfer_device(user, body.device_id)
    remote = body.remote_path.strip()
    if not remote or len(remote) > 500 or "\n" in remote:
        raise HTTPException(status_code=400, detail="Informe o caminho no equipamento (ex.: /datadisk0/DATA0/startrun.dat)")
    if body.direction == "pull":
        folder = _ftp_folder(body.local_path) or f"equipamentos/{_ftp_folder(dev['name'])}"
        d = _ftp_abs(folder)
        os.makedirs(d, exist_ok=True)
        name = re.sub(r"[^\w.\- ]", "_", posixpath.basename(remote.rstrip("/")))[:120] or "arquivo"
        dst = os.path.join(d, name)
        if os.path.exists(dst):
            stem, ext = os.path.splitext(name)
            name = f"{stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{ext}"
            dst = os.path.join(d, name)
        local_abs, local_rel = dst, f"{folder}/{name}"
    elif body.direction == "push":
        local_abs = _ftp_abs(body.local_path)
        if not os.path.isfile(local_abs):
            raise HTTPException(status_code=404, detail="Escolha um arquivo da área de arquivos")
        local_rel = _ftp_folder(body.local_path)
        if remote.endswith("/"):
            remote += os.path.basename(local_abs)
    else:
        raise HTTPException(status_code=400, detail="direction: pull ou push")
    running = [j for j in _xfer_jobs.values() if j["state"] in ("conectando", "copiando") and j["device_id"] == dev["id"]]
    if len(running) >= 2:
        raise HTTPException(status_code=409, detail="Já há transferências em andamento nesse equipamento")
    jid = os.urandom(6).hex()
    job = {"id": jid, "device_id": dev["id"], "device_name": dev["name"], "direction": body.direction, "remote": remote, "local": local_rel,
           "state": "na fila", "bytes": 0, "total": 0, "method": "", "ok": None, "error": "", "user": user.get("email", ""),
           "owner_id": user["id"], "started_at": datetime.now(timezone.utc).isoformat()}
    _xfer_jobs[jid] = job
    for k in sorted(_xfer_jobs, key=lambda k: _xfer_jobs[k]["started_at"])[:-100]:
        _xfer_jobs.pop(k, None)
    _bg(_xfer_run(job, dev, local_abs, remote))
    return _xfer_public(job)


@api.get("/ftp/device/jobs")
async def xfer_jobs(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    rows = [j for j in _xfer_jobs.values() if user.get("role") == "admin" or j["owner_id"] == user["id"]]
    return [_xfer_public(j) for j in sorted(rows, key=lambda j: j["started_at"], reverse=True)[:30]]


@api.get("/ftp/files/all")
async def ftp_files_all(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    root = Path(_ftp_root())
    out = []
    for f in root.rglob("*"):
        if f.is_file() and not f.name.endswith(".parcial") and not any(p.startswith(".") for p in f.relative_to(root).parts):
            out.append({"path": str(f.relative_to(root)).replace(os.sep, "/"), "size": f.stat().st_size})
            if len(out) >= 3000:
                break
    return sorted(out, key=lambda x: x["path"].lower())


# ---------- TACACS+ (login nos equipamentos com os usuários do BastiON) ----------
_tac_rt: dict = {"devs": {}, "devs_at": 0.0}


def _tac_internal_key() -> str:
    return hmac.new(os.environ["JWT_SECRET"].encode(), b"bastion-tacacs-internal", hashlib.sha256).hexdigest()


def _tac_check(request: Request):
    k = request.headers.get("x-bastion-internal", "")
    host = (request.client.host if request.client else "") or ""
    if not k or not hmac.compare_digest(k, _tac_internal_key()) or host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Só o serviço TACACS+ do BastiON")


async def _tac_cfg() -> dict:
    doc = await db.config.find_one({"key": "tacacs"}, {"_id": 0}) or {}
    return tacmod.clean_settings({}, doc)


def _tac_public(cfg: dict) -> dict:
    return {**cfg, "clients": [{**{k: v for k, v in c.items() if k != "secret"}, "has_secret": bool(c.get("secret"))} for c in cfg["clients"]]}


async def _tac_device(ip: str) -> Optional[dict]:
    if time.time() - _tac_rt["devs_at"] > 60:
        _tac_rt["devs"] = {d.get("host"): d async for d in db.devices.find({}, {"_id": 0, "id": 1, "name": 1, "host": 1, "device_type": 1, "tags": 1})}
        _tac_rt["devs_at"] = time.time()
    return _tac_rt["devs"].get(ip)


async def _tac_lookup(name: str) -> Optional[dict]:
    """Conta TACACS pelo nome: usuário do BastiON com TACACS ligado (nome TACACS ou e-mail) ou conta de serviço."""
    lc = (name or "").strip().lower()
    if not lc:
        return None
    u = await db.users.find_one({"tacacs.username_lc": lc, "tacacs.enabled": True}, {"_id": 0}) or \
        await db.users.find_one({"email": lc, "tacacs.enabled": True}, {"_id": 0})
    if u:
        return {"kind": "user", "id": u["id"], "email": u["email"], "name": u.get("name") or u["email"], "role": u.get("role", "operator"),
                "tac": u.get("tacacs") or {}, "doc": u}
    a = await db.tacacs_accounts.find_one({"username_lc": lc, "enabled": {"$ne": False}}, {"_id": 0})
    if a:
        return {"kind": "service", "id": f"svc:{a['id']}", "email": f"{a['username']} (conta de serviço)", "name": a["username"],
                "role": a.get("role", "operator"), "tac": a, "doc": a}
    return None


async def _tac_event(kind: str, result: str, client: dict, ctx: dict, acct: Optional[dict] = None, **extra):
    now = datetime.now(timezone.utc)
    dev = await _tac_device(ctx.get("nas_ip", ""))
    doc = {"id": os.urandom(8).hex(), "ts": now, "at": now.isoformat(), "type": kind, "result": result,
           "user": ctx.get("user", ""), "user_id": (acct or {}).get("id"), "user_email": (acct or {}).get("email", ""),
           "role": (acct or {}).get("role"), "nas_ip": ctx.get("nas_ip", ""), "client": client.get("name", ""), "client_id": client.get("id"),
           "device_id": (dev or {}).get("id"), "device_name": (dev or {}).get("name", ""), "rem_addr": ctx.get("rem_addr", ""),
           "port": ctx.get("port", ""), **{k: v for k, v in extra.items() if v not in (None, "")}}
    await db.tacacs_events.insert_one(dict(doc))
    return doc


def _tac_src_ip(ctx: dict) -> str:
    for v in (ctx.get("rem_addr"), ctx.get("nas_ip")):
        try:
            return str(ipaddress.ip_address(str(v or "").split("%")[0]))
        except ValueError:
            continue
    return ctx.get("nas_ip", "") or "?"


async def _tac_authen(client: dict, ctx: dict, name: str, password: str) -> dict:
    cfg = await _tac_cfg()
    ip = _tac_src_ip(ctx)
    lkey = f"tacacs:{(name or '').strip().lower()}"
    ua = f"TACACS+ {client.get('name', '')} {ctx.get('nas_ip', '')} {ctx.get('port', '')}".strip()
    deny = {"ok": False, "msg": "Usuário ou senha inválidos"}
    if not cfg["enabled"]:
        return {"error": True, "msg": "TACACS+ desligado no BastiON"}
    if await security.locked_for(db, lkey, ip):
        await _tac_event("authen", "fail", client, ctx, None, reason="bloqueado por tentativas erradas")
        return {"ok": False, "msg": "Muitas tentativas erradas; aguarde 15 minutos"}
    a = await _tac_lookup(name)
    reason = ""
    if not a:
        reason = "usuário não existe ou sem TACACS ligado"
    elif a["tac"].get("clients") and client.get("id") != "__local__" and client.get("id") not in a["tac"]["clients"]:
        reason = f"sem acesso ao grupo {client.get('name', '')}"
    elif ctx.get("service") == "enable" and a["role"] == "viewer":
        reason = "perfil visualização não entra em modo privilegiado"
    else:
        pw, code = password or "", ""
        need_otp = cfg["otp"] == "if_enabled" and a["kind"] == "user" and a["doc"].get("totp_enabled")
        if need_otp:
            pw, code = tacmod.split_otp(pw)
        src = cfg["password_source"]
        good = False
        if a["kind"] == "service":
            good = bool(a["tac"].get("password_hash")) and verify_password(pw, a["tac"]["password_hash"])
        else:
            if src in ("tacacs", "both") and a["tac"].get("password_hash"):
                good = verify_password(pw, a["tac"]["password_hash"])
            if not good and src in ("bastion", "both") and a["doc"].get("password_hash"):
                good = verify_password(pw, a["doc"]["password_hash"])
        if not good:
            reason = "senha errada" + (" (sem senha TACACS definida)" if a["kind"] == "user" and src == "tacacs" and not a["tac"].get("password_hash") else "")
        elif need_otp:
            if not code:
                reason = "faltou o código do 2FA no fim da senha"
            else:
                step = security.verify_totp(vault.decrypt(a["doc"].get("totp_secret", "")), code, int(a["doc"].get("totp_last_step", -1)))
                if step is None:
                    reason = "código 2FA inválido"
                else:
                    await db.users.update_one({"id": a["id"]}, {"$set": {"totp_last_step": step}})
    if reason:
        await security.record_fail(db, lkey, ip)
        await security.log_event(db, (a or {}).get("email") or name, ip, ua, False, f"tacacs: {reason}", (a or {}).get("id"))
        await _tac_event("authen", "fail", client, ctx, a, reason=reason, method=ctx.get("authen_type"))
        return deny
    await security.clear_fails(db, lkey)
    await security.log_event(db, a["email"], ip, ua, True, "tacacs", a["id"] if a["kind"] == "user" else None)
    now = datetime.now(timezone.utc).isoformat()
    if a["kind"] == "user":
        await db.users.update_one({"id": a["id"]}, {"$set": {"tacacs.last_login": now, "tacacs.last_device": ctx.get("nas_ip", "")}})
    else:
        await db.tacacs_accounts.update_one({"id": a["tac"]["id"]}, {"$set": {"last_login": now, "last_device": ctx.get("nas_ip", "")}})
    await _tac_event("authen", "pass", client, ctx, a, method=ctx.get("authen_type"), service=ctx.get("service"))
    dev = await _tac_device(ctx.get("nas_ip", ""))
    return {"ok": True, "msg": f"BastiON: olá, {a['name']}" + (f" — {dev['name']}" if dev else "")}


async def _tac_author(client: dict, ctx: dict, args: List[str]) -> dict:
    cfg = await _tac_cfg()
    a = await _tac_lookup(ctx.get("user", ""))
    if not a:
        await _tac_event("author", "fail", client, ctx, None, reason="usuário não existe ou sem TACACS ligado")
        return {"status": tacmod.AU_FAIL, "msg": "Usuário sem acesso"}
    d = tacmod.args_dict(args)
    service = ((d.get("service") or [""])[0] or "").lower()
    cmd = tacmod.command_of(args)
    prof = cfg["profiles"].get(a["role"]) or cfg["profiles"]["viewer"]
    vendor = "juniper" if service == "junos-exec" else (client.get("vendor") or "generic")
    if service in ("shell", "junos-exec", "exec", "") and not cmd:
        attrs = tacmod.role_attrs(vendor, prof)
        await _tac_event("author", "pass", client, ctx, a, reason=f"sessão ({a['role']}: {', '.join(attrs)})")
        return {"status": tacmod.AU_PASS_ADD, "args": attrs}
    if service not in ("shell", "exec", "junos-exec"):
        await _tac_event("author", "fail", client, ctx, a, reason=f"serviço não liberado: {service}")
        return {"status": tacmod.AU_FAIL, "msg": f"Serviço {service} não liberado"}
    if a["role"] == "viewer" and cfg["readonly_viewer"] and not tacmod.READ_ONLY.match(cmd):
        await _tac_event("author", "deny", client, ctx, a, cmd=cmd, reason="perfil visualização: só consulta")
        return {"status": tacmod.AU_FAIL, "msg": "Perfil visualização: só comandos de consulta (show/display/ping)"}
    if not cfg["cmd_author"]:
        return {"status": tacmod.AU_PASS_ADD, "args": []}
    dev = await _tac_device(ctx.get("nas_ip", ""))
    who = {"id": a["id"], "email": a["email"], "name": a["name"], "role": a["role"]}
    try:
        dec = await cmd_policy(who, dev or {"id": "", "name": ctx.get("nas_ip", ""), "host": ctx.get("nas_ip", ""),
                                            "device_type": client.get("vendor")}, [cmd], "tacacs")
    except Exception as e:
        logger.warning(f"TACACS política de comandos: {e}")
        return {"status": tacmod.AU_PASS_ADD if cfg["fail_open_cmd"] else tacmod.AU_ERROR, "args": []}
    if dec["action"] == "allow":
        return {"status": tacmod.AU_PASS_ADD, "args": []}
    await _tac_event("author", "deny", client, ctx, a, cmd=cmd, reason=dec.get("message", dec["action"]))
    return {"status": tacmod.AU_FAIL, "msg": dec.get("message", "Comando não autorizado")[:240]}


@api.post("/tacacs/internal/authen")
async def tac_int_authen(body: dict, request: Request):
    _tac_check(request)
    return await _tac_authen(body.get("client") or {}, body.get("ctx") or {}, str(body.get("user") or ""), str(body.get("password") or ""))


@api.post("/tacacs/internal/author")
async def tac_int_author(body: dict, request: Request):
    _tac_check(request)
    return await _tac_author(body.get("client") or {}, body.get("ctx") or {}, [str(x) for x in body.get("args") or []][:255])


@api.post("/tacacs/internal/acct")
async def tac_int_acct(body: dict, request: Request):
    _tac_check(request)
    ctx, args = body.get("ctx") or {}, [str(x) for x in body.get("args") or []][:255]
    fl = int(ctx.get("flags") or 0)
    res = "start" if fl & tacmod.ACCT_START else "stop" if fl & tacmod.ACCT_STOP else "watchdog" if fl & tacmod.ACCT_WATCHDOG else "?"
    d = tacmod.args_dict(args)
    a = await _tac_lookup(ctx.get("user", ""))
    await _tac_event("acct", res, body.get("client") or {}, ctx, a, cmd=tacmod.command_of(args) or (d.get("cmd") or [""])[0],
                     task_id=(d.get("task_id") or [""])[0], elapsed=(d.get("elapsed_time") or [""])[0], service=(d.get("service") or [""])[0])
    return {"ok": True}


# --- telas (admin)
@api.get("/tacacs/settings")
async def tac_settings(_: dict = Depends(require_admin)):
    cfg = await _tac_cfg()
    return {**_tac_public(cfg), "vendors": tacmod.VENDORS, "notes": tacmod.TEMPLATE_NOTE, "default_templates": tacmod.TEMPLATES,
            "server_ip_hint": urlparse(os.environ.get("PUBLIC_URL", "")).hostname or ""}


@api.put("/tacacs/settings")
async def tac_settings_put(body: dict, _: dict = Depends(require_admin)):
    cur = await db.config.find_one({"key": "tacacs"}, {"_id": 0}) or {}
    try:
        cfg = tacmod.clean_settings(body or {}, cur)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    for c in cfg["clients"]:
        if not c.get("secret"):
            c["secret"] = vault.encrypt(tacmod.gen_secret())       # cliente novo já nasce com chave própria
    await db.config.update_one({"key": "tacacs"}, {"$set": {"key": "tacacs", **cfg}}, upsert=True)
    await flowstore.ensure_ttl(db, "tacacs_events", cfg["retention_days"] * 86400)
    return _tac_public(cfg)


class TacSecretIn(BaseModel):
    secret: str = ""


@api.get("/tacacs/clients/{cid}/secret")
async def tac_client_secret(cid: str, _: dict = Depends(require_admin)):
    cfg = await _tac_cfg()
    c = next((x for x in cfg["clients"] if x["id"] == cid), None)
    if not c:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return {"secret": vault.decrypt(c.get("secret", ""))}


@api.post("/tacacs/clients/{cid}/secret")
async def tac_client_secret_set(cid: str, body: TacSecretIn, _: dict = Depends(require_admin)):
    s = body.secret.strip() or tacmod.gen_secret()
    if len(s) < 8 or len(s) > 64 or not re.fullmatch(r"[\x21-\x7e]+", s) or re.search(r"[\"'?\s]", s):
        raise HTTPException(status_code=400, detail="Chave: 8 a 64 caracteres, sem espaço, aspas ou '?' (alguns equipamentos não aceitam)")
    cur = await db.config.find_one({"key": "tacacs"}, {"_id": 0}) or {}
    cl = cur.get("clients") or []
    for c in cl:
        if c.get("id") == cid:
            c["secret"] = vault.encrypt(s)
            break
    else:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    await db.config.update_one({"key": "tacacs"}, {"$set": {"clients": cl}})
    return {"secret": s}


@api.get("/tacacs/status")
async def tac_status(_: dict = Depends(require_admin)):
    st = await db.config.find_one({"key": "tacacs_status"}, {"_id": 0}) or {}
    alive = False
    if st.get("at"):
        try:
            alive = (datetime.now(timezone.utc) - datetime.fromisoformat(st["at"])).total_seconds() < 30
        except ValueError:
            pass
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cnt = {}
    for e in await db.tacacs_events.find({"at": {"$gte": since}}, {"_id": 0, "type": 1, "result": 1}).to_list(100000):
        k = f"{e.get('type')}:{e.get('result')}"
        cnt[k] = cnt.get(k, 0) + 1
    return {**{k: v for k, v in st.items() if k != "key"}, "alive": alive, "last24h": cnt}


def _tac_user_row(u: dict) -> dict:
    t = u.get("tacacs") or {}
    return {"id": u["id"], "name": u.get("name", ""), "email": u["email"], "role": u.get("role", "operator"),
            "totp_enabled": bool(u.get("totp_enabled")), "sso_only": (u.get("login_mode") == "sso"),
            "tacacs": {"enabled": bool(t.get("enabled")), "username": t.get("username") or tacmod.default_username(u["email"]),
                       "has_password": bool(t.get("password_hash")), "password_set_at": t.get("password_set_at"),
                       "last_login": t.get("last_login"), "last_device": t.get("last_device"), "clients": t.get("clients") or []}}


async def _tac_name_taken(lc: str, user_id: str = "", acc_id: str = "") -> bool:
    u = await db.users.find_one({"tacacs.username_lc": lc}, {"_id": 0, "id": 1})
    if u and u["id"] != user_id:
        return True
    a = await db.tacacs_accounts.find_one({"username_lc": lc}, {"_id": 0, "id": 1})
    return bool(a and a["id"] != acc_id)


@api.get("/tacacs/users")
async def tac_users(_: dict = Depends(require_admin)):
    users = await db.users.find({}, {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "tacacs": 1, "totp_enabled": 1, "login_mode": 1}).sort("email", 1).to_list(5000)
    accs = await db.tacacs_accounts.find({}, {"_id": 0, "password_hash": 0}).sort("username", 1).to_list(1000)
    return {"users": [_tac_user_row(u) for u in users], "accounts": accs}


class TacUserIn(BaseModel):
    enabled: bool = True
    username: str = ""
    password: str = ""
    generate: bool = False
    clients: List[str] = []


def _tac_pw_ok(pw: str):
    if len(pw) < 10 or len(pw) > 128:
        raise HTTPException(status_code=400, detail="Senha TACACS: de 10 a 128 caracteres")
    if pw.isdigit() or pw.isalpha():
        raise HTTPException(status_code=400, detail="Senha TACACS: misture letras e números")


@api.put("/tacacs/users/{uid}")
async def tac_user_put(uid: str, body: TacUserIn, _: dict = Depends(require_admin)):
    u = await db.users.find_one({"id": uid}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    try:
        name = tacmod.clean_username(body.username or (u.get("tacacs") or {}).get("username") or tacmod.default_username(u["email"]))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if await _tac_name_taken(name.lower(), user_id=uid):
        raise HTTPException(status_code=409, detail=f"O nome {name} já é de outro usuário ou conta de serviço")
    upd = {"tacacs.enabled": body.enabled, "tacacs.username": name, "tacacs.username_lc": name.lower(),
           "tacacs.clients": [c for c in body.clients if isinstance(c, str)][:100]}
    new_pw = ""
    if body.generate:
        new_pw = tacmod.gen_secret(14)
    elif body.password:
        _tac_pw_ok(body.password)
        new_pw = body.password
    if new_pw:
        upd["tacacs.password_hash"] = hash_password(new_pw)
        upd["tacacs.password_set_at"] = datetime.now(timezone.utc).isoformat()
    await db.users.update_one({"id": uid}, {"$set": upd})
    u = await db.users.find_one({"id": uid}, {"_id": 0})
    return {**_tac_user_row(u), **({"password": new_pw} if body.generate else {})}


class TacAccountIn(BaseModel):
    username: str
    role: str = "operator"
    description: str = ""
    enabled: bool = True
    password: str = ""
    generate: bool = False
    clients: List[str] = []


@api.post("/tacacs/accounts")
async def tac_account_add(body: TacAccountIn, _: dict = Depends(require_admin)):
    return await _tac_account_save(None, body)


@api.put("/tacacs/accounts/{aid}")
async def tac_account_put(aid: str, body: TacAccountIn, _: dict = Depends(require_admin)):
    return await _tac_account_save(aid, body)


async def _tac_account_save(aid: Optional[str], body: TacAccountIn) -> dict:
    try:
        name = tacmod.clean_username(body.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if body.role not in tacmod.ROLES:
        raise HTTPException(status_code=400, detail="Perfil inválido")
    if await _tac_name_taken(name.lower(), acc_id=aid or ""):
        raise HTTPException(status_code=409, detail=f"O nome {name} já é de outro usuário ou conta de serviço")
    cur = await db.tacacs_accounts.find_one({"id": aid}, {"_id": 0}) if aid else None
    if aid and not cur:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    new_pw = tacmod.gen_secret(16) if body.generate or (not aid and not body.password) else body.password
    if new_pw and not body.generate and (aid or body.password):
        _tac_pw_ok(new_pw)
    doc = {"username": name, "username_lc": name.lower(), "role": body.role, "description": body.description.strip()[:200],
           "enabled": body.enabled, "clients": [c for c in body.clients if isinstance(c, str)][:100]}
    if new_pw:
        doc["password_hash"] = hash_password(new_pw)
        doc["password_set_at"] = datetime.now(timezone.utc).isoformat()
    if cur:
        await db.tacacs_accounts.update_one({"id": aid}, {"$set": doc})
    else:
        aid = os.urandom(6).hex()
        await db.tacacs_accounts.insert_one({"id": aid, "created_at": datetime.now(timezone.utc).isoformat(), **doc})
    out = await db.tacacs_accounts.find_one({"id": aid}, {"_id": 0, "password_hash": 0})
    return {**out, **({"password": new_pw} if new_pw and (body.generate or not body.password) else {})}


@api.delete("/tacacs/accounts/{aid}")
async def tac_account_del(aid: str, _: dict = Depends(require_admin)):
    await db.tacacs_accounts.delete_one({"id": aid})
    return {"ok": True}


# --- a própria pessoa
@api.get("/tacacs/me")
async def tac_me(user: dict = Depends(get_current_user)):
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    cfg = await _tac_cfg()
    row = _tac_user_row(u)
    return {**row["tacacs"], "server_enabled": cfg["enabled"], "password_source": cfg["password_source"], "otp": cfg["otp"]}


class TacMyPw(BaseModel):
    password: str
    current: str = ""


@api.put("/tacacs/me/password")
async def tac_me_password(body: TacMyPw, user: dict = Depends(get_current_user)):
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not (u.get("tacacs") or {}).get("enabled"):
        raise HTTPException(status_code=403, detail="Seu acesso TACACS não está ligado; peça a um administrador")
    if u.get("password_hash") and u.get("login_mode") != "sso" and not verify_password(body.current, u["password_hash"]):
        raise HTTPException(status_code=401, detail="Senha atual do BastiON incorreta")
    _tac_pw_ok(body.password)
    await db.users.update_one({"id": user["id"]}, {"$set": {"tacacs.password_hash": hash_password(body.password),
                                                            "tacacs.password_set_at": datetime.now(timezone.utc).isoformat()}})
    return {"ok": True}


# --- configuração dos equipamentos
@api.get("/tacacs/config")
async def tac_config(client_id: str = "", vendor: str = "", source: str = "", cmd: bool = True, acct: bool = True,
                     _: dict = Depends(require_admin)):
    cfg = await _tac_cfg()
    c = next((x for x in cfg["clients"] if x["id"] == client_id), None)
    if client_id and not c:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    v = vendor if vendor in tacmod.VENDORS else (c or {}).get("vendor") or "generic"
    secret = vault.decrypt((c or {}).get("secret", "")) or "<CHAVE_DO_CLIENTE>"
    if not re.fullmatch(r"[\w./:-]{0,64}", source.strip()):
        raise HTTPException(status_code=400, detail="Interface/IP de origem inválido")
    params = tacmod.config_params(cfg, c or {}, secret, source, cmd and cfg["cmd_author"], acct)
    text = tacmod.render(v, params, cfg["templates"].get(v))
    return {"vendor": v, "text": text, "note": tacmod.TEMPLATE_NOTE.get(v, ""), "custom_template": v in cfg["templates"],
            "server_ip": cfg["server_ip"], "client": (c or {}).get("name")}


class TacTemplateIn(BaseModel):
    text: str = ""


@api.put("/tacacs/templates/{vendor}")
async def tac_template_put(vendor: str, body: TacTemplateIn, _: dict = Depends(require_admin)):
    if vendor not in tacmod.TEMPLATES:
        raise HTTPException(status_code=404, detail="Fabricante desconhecido")
    cur = await db.config.find_one({"key": "tacacs"}, {"_id": 0}) or {}
    tpl = dict(cur.get("templates") or {})
    if body.text.strip() and body.text.strip() != tacmod.TEMPLATES[vendor].strip():
        tpl[vendor] = body.text[:20000]
    else:
        tpl.pop(vendor, None)
    await db.config.update_one({"key": "tacacs"}, {"$set": {"templates": tpl}}, upsert=True)
    return {"custom": vendor in tpl}


@api.get("/tacacs/events")
async def tac_events(type: str = "", result: str = "", q: str = "", limit: int = 300, _: dict = Depends(require_admin)):
    f: dict = {}
    if type in ("authen", "author", "acct"):
        f["type"] = type
    if result:
        f["result"] = result
    if q.strip():
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        f["$or"] = [{"user": rx}, {"nas_ip": rx}, {"device_name": rx}, {"cmd": rx}, {"rem_addr": rx}]
    return await db.tacacs_events.find(f, {"_id": 0, "ts": 0}).sort("at", -1).to_list(max(1, min(2000, limit)))


class TacTestIn(BaseModel):
    username: str
    password: str
    command: str = ""


@api.post("/tacacs/test")
async def tac_test(body: TacTestIn, _: dict = Depends(require_admin)):
    """Login de verdade no serviço TACACS+ (pela própria máquina): confere se está no ar, usuário, senha e perfil."""
    cfg = await _tac_cfg()
    c = tacmod.Client("127.0.0.1", cfg["port"], _tac_internal_key(), timeout=8)
    try:
        r = await c.pap(body.username.strip(), body.password)
        out = {"reachable": True, "login": r["ok"], "message": r["msg"]}
        if r["ok"]:
            a = await c.author(body.username.strip(), ["service=shell", "cmd="])
            out["profile"] = a["args"]
            if body.command.strip():
                parts = body.command.strip().split()
                a = await c.author(body.username.strip(), ["service=shell", f"cmd={parts[0]}"] + [f"cmd-arg={x}" for x in parts[1:]] + ["cmd-arg=<cr>"])
                out["command"] = {"allowed": a["status"] in (tacmod.AU_PASS_ADD, tacmod.AU_PASS_REPL), "message": a["msg"]}
        return out
    except Exception as e:
        return {"reachable": False, "login": False, "message": f"O serviço TACACS+ não respondeu na porta {cfg['port']}: {str(e)[:160]}"}


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
    rec: Optional[recording.Recorder] = None
    rcfg = await _rec_settings()
    try:
        await ws.send_json({"type": "status", "message": f"Conectando em {dev['name']} ({dev['host']}:{dev.get('port',22)}) via {dev.get('protocol','ssh').upper()}..."})
        wrapper = await _connect_device(dev)
        if rcfg["enabled"]:
            try:
                rec = recording.Recorder(session_id, 120, 32, f"{dev['name']} — {user_email}", rcfg["input"], rcfg["max_mb"])
            except OSError as e:
                logger.warning(f"gravação da sessão não iniciou: {e}")
        if rec:
            await db.sessions.update_one({"id": session_id}, {"$set": {"recording": {"file": rec.rel + ".gz", "live": True,
                                                                                     "input": rec.input}}})
        await ws.send_json({"type": "status", "message": "Conectado. Sessão ativa." + (" (gravada para auditoria)" if rec else "")})
        process = await wrapper.open_shell(cols=120, rows=32)
        pol = _TermPolicy({"id": user_id, "email": user_email, "role": u.get("role")}, dev, process, ws)
        await pol.refresh()

        async def pump_stdout():
            try:
                while True:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode("utf-8", "replace")
                    if rec:
                        rec.output(chunk)
                    pol.out(chunk)
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
                    if rec:
                        rec.output(chunk)
                    await ws.send_json({"type": "data", "data": chunk})
            except Exception:
                pass

        out_task = asyncio.create_task(pump_stdout())
        err_task = asyncio.create_task(pump_stderr())

        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "input":
                data = msg.get("data", "")
                if rec:
                    rec.typed(data)
                await pol.input(data)
            elif msg.get("type") == "resize":
                await wrapper.resize(int(msg.get("cols", 120)), int(msg.get("rows", 32)))
                if rec:
                    rec.resize(int(msg.get("cols", 120)), int(msg.get("rows", 32)))
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
        try:
            pol.close()
        except NameError:
            pass
        if wrapper:
            await wrapper.close()
        ended = datetime.now(timezone.utc)
        recinfo = None
        if rec:
            try:
                recinfo = rec.close()
            except Exception as e:
                logger.warning(f"gravação da sessão: {e}")
        await db.sessions.update_one({"id": session_id}, {"$set": {
            "ended_at": ended.isoformat(),
            "duration_seconds": int((ended - started).total_seconds()),
            **({"recording": recinfo} if recinfo else {}),
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


# ---------- Engenharia de tráfego: SPF/CSPF, simulação e otimização de custos OSPF ----------
class TeRunIn(BaseModel):
    device_ids: List[str] = []
    map_id: Optional[str] = None        # alternativa: usa a última análise do mapa
    sample_sec: int = 10
    name: str = ""
    default_cost: Optional[int] = None  # enlace sem custo lido (SNMP/CLI) entra com esse custo


async def _te_for(user: dict, rid: str) -> dict:
    doc = await db.te_runs.find_one({"id": rid, "owner_id": user["id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Topologia não encontrada")
    return doc


def _te_public(doc: dict) -> dict:
    links = doc.get("links") or []
    return {**{k: doc.get(k) for k in ("id", "name", "source", "at", "duration_sec", "nodes", "errors", "notes", "map_id")},
            "links": links, "utilization": teopt.utilization(links),
            "summary": {"devices": len(doc.get("nodes") or []), "links": len(links),
                        "ospf_links": sum(1 for L in links if L.get("cost_ab") is not None and L.get("cost_ba") is not None),
                        "over80": sum(1 for u in teopt.utilization(links) if (u["pct"] or 0) >= 80),
                        "assumed": sum(1 for L in links if L.get("cost_assumed"))}}


@api.post("/te/runs")
async def te_run(body: TeRunIn, user: dict = Depends(get_current_user)):
    """Monta a topologia: lê os equipamentos escolhidos por SNMP (enlaces descobertos pelas sub-redes ponto a ponto)
    ou aproveita a última análise de um mapa."""
    _no_viewer(user)
    started = time.monotonic()
    errors, notes = [], []
    if body.map_id:
        m = await _map_for(user, body.map_id)
        rep = await db.net_reports.find_one({"map_id": body.map_id, "owner_id": user["id"]}, {"_id": 0}, sort=[("at", -1)])
        if not rep:
            raise HTTPException(status_code=400, detail="Esse mapa ainda não tem análise de rede — rode a análise no mapa primeiro")
        node_dev = {n["id"]: n.get("device_id") for n in m.get("nodes", [])}
        dids = [d for d in node_dev.values() if d]
        devs = {d["id"]: d async for d in db.devices.find({"id": {"$in": dids}, "owner_id": user["id"]}, {"_id": 0})}
        links, _ = teopt.from_analysis(rep["report"])
        for L in links:
            for side in ("a", "b"):
                did = node_dev.get(L[side]) or L[side]
                L[side] = did
                L[f"{side}_type"] = (devs.get(did) or {}).get("device_type")
        nodes = sorted({(L["a"], L["a_name"]) for L in links} | {(L["b"], L["b_name"]) for L in links}, key=lambda x: x[1])
        name, source = body.name or f"Mapa {m['name']}", {"kind": "map", "map_id": body.map_id, "analysis_at": rep.get("at")}
        node_rows = [{"id": i, "name": n, "type": (devs.get(i) or {}).get("device_type")} for i, n in nodes]
    else:
        ids = list(dict.fromkeys(body.device_ids))[:150]
        if len(ids) < 2:
            raise HTTPException(status_code=400, detail="Escolha ao menos 2 equipamentos")
        devs = {d["id"]: d async for d in db.devices.find({"id": {"$in": ids}, **_scope(user)}, {"_id": 0})}
        settings = await monitoring.get_settings(db)
        sample = max(3, min(int(body.sample_sec or 10), 30))
        sem = asyncio.Semaphore(8)

        async def one(did):
            dev = devs.get(did)
            if not dev:
                return did, {"ok": False, "error": "equipamento não encontrado"}
            async with sem:
                try:
                    client = await _snmp_client(dev, settings)
                    return did, await asyncio.wait_for(teopt.collect(client, sample), timeout=180)
                except asyncio.TimeoutError:
                    return did, {"ok": False, "error": "SNMP demorou demais"}
                except Exception as e:
                    return did, {"ok": False, "error": str(e)[:200]}
        data = dict(await asyncio.gather(*[one(d) for d in ids]))
        for did, d in data.items():
            if not d.get("ok"):
                errors.append({"device": (devs.get(did) or {}).get("name", did), "error": d.get("error", "sem leitura SNMP")})
        links, notes = teopt.discover_links(devs, data)
        # custo OSPF que o SNMP não trouxe (OSPF-MIB fechada na view, outro processo…): lê pela CLI
        need = sorted({L[s] for L in links for s, k in (("a", "cost_ab"), ("b", "cost_ba")) if L.get(k) is None})
        cli_costs: dict = {}
        cli_err: dict = {}
        cli_raw: dict = {}
        ssh_sem = asyncio.Semaphore(6)

        async def cli_one(did):
            dev = devs.get(did)
            cmds = teopt.OSPF_CLI.get((dev or {}).get("device_type") or "")
            if not dev or not cmds:
                cli_err[did] = "sem comando de OSPF para esse fabricante"
                return
            async with ssh_sem:
                try:
                    cli = await _connect_device(dev)
                    try:
                        for cmd in cmds:
                            out = _out_text(await asyncio.wait_for(cli.run_command(cmd, timeout=60), timeout=90))
                            cli_raw[did] = f"$ {cmd}\n{out}"[-4000:]
                            got = teopt.parse_ospf_cli(out)
                            if got:
                                cli_costs[did] = got
                                return
                        cli_err[did] = "a saída do comando não trouxe custos OSPF"
                    finally:
                        await cli.close()
                except Exception as e:
                    cli_err[did] = (str(e) or type(e).__name__)[:160]
        if need:
            await asyncio.gather(*[cli_one(d) for d in need])
            teopt.fill_costs(links, cli_costs)
            notes = [n for n in notes if "sem OSPF" not in n.get("note", "")]
            if body.default_cost and 1 <= body.default_cost <= 65535:
                for L in links:
                    for k in ("cost_ab", "cost_ba"):
                        if L.get(k) is None:
                            L[k] = int(body.default_cost)
                            L["cost_assumed"] = True
            for L in links:
                if L.get("cost_ab") is None or L.get("cost_ba") is None:
                    miss = [L[f"{x}_name"] for x, k in (("a", "cost_ab"), ("b", "cost_ba")) if L.get(k) is None]
                    notes.append({"net": L.get("net"), "note": f"{L['a_name']} {L['a_if']} ↔ {L['b_name']} {L['b_if']}: "
                                  f"sem custo OSPF em {', '.join(miss)} (nem SNMP nem CLI) — fora do SPF"})
        node_rows = [{"id": did, "name": (devs.get(did) or {}).get("name", "?"), "type": (devs.get(did) or {}).get("device_type"),
                      "router_id": (data.get(did) or {}).get("router_id"), "ok": bool((data.get(did) or {}).get("ok")),
                      "ospf_snmp": len(((data.get(did) or {}).get("ospf") or {}).get("ifaces") or {}),
                      "ospf_cli": len(cli_costs.get(did) or {}) if did in need else None, "cli_error": cli_err.get(did),
                      "cli_raw": cli_raw.get(did),
                      "links": sum(1 for L in links if did in (L["a"], L["b"]))} for did in ids]
        name, source = body.name or f"{len(ids)} equipamentos", {"kind": "devices", "device_ids": ids}
    doc = {"id": os.urandom(8).hex(), "owner_id": user["id"], "name": name[:80], "source": source,
           "map_id": body.map_id, "at": datetime.now(timezone.utc).isoformat(), "duration_sec": round(time.monotonic() - started, 1),
           "nodes": node_rows, "links": links, "errors": errors, "notes": notes}
    await db.te_runs.insert_one(dict(doc))
    old = await db.te_runs.find({"owner_id": user["id"]}, {"_id": 0, "id": 1}).sort("at", -1).to_list(200)
    for x in old[20:]:
        await db.te_runs.delete_one({"id": x["id"]})
    return _te_public(doc)


@api.get("/te/runs")
async def te_runs(user: dict = Depends(get_current_user)):
    rows = await db.te_runs.find({"owner_id": user["id"]}, {"_id": 0, "id": 1, "name": 1, "at": 1, "source": 1, "nodes": 1, "links": 1}
                                 ).sort("at", -1).to_list(20)
    return [{"id": r["id"], "name": r["name"], "at": r["at"], "kind": (r.get("source") or {}).get("kind"),
             "devices": len(r.get("nodes") or []), "links": len(r.get("links") or [])} for r in rows]


@api.get("/te/runs/{rid}")
async def te_get(rid: str, user: dict = Depends(get_current_user)):
    return _te_public(await _te_for(user, rid))


@api.delete("/te/runs/{rid}")
async def te_delete(rid: str, user: dict = Depends(get_current_user)):
    await _te_for(user, rid)
    await db.te_runs.delete_one({"id": rid})
    return {"ok": True}


class TeOptimizeIn(BaseModel):
    target_pct: float = 80
    max_changes: int = 4
    symmetric: bool = True
    max_cost: int = 65535


@api.post("/te/runs/{rid}/optimize")
async def te_optimize(rid: str, body: TeOptimizeIn, user: dict = Depends(get_current_user)):
    doc = await _te_for(user, rid)
    if not 30 <= body.target_pct <= 100:
        raise HTTPException(status_code=400, detail="Meta de utilização entre 30% e 100%")
    links = doc["links"]
    return await asyncio.get_running_loop().run_in_executor(
        None, lambda: teopt.optimize(links, body.target_pct, max(1, min(body.max_changes, 10)), body.symmetric, 1, max(2, body.max_cost)))


class TeSimIn(BaseModel):
    costs: List[dict] = []              # [{link, dir, cost}]
    down_links: List[str] = []
    down_nodes: List[str] = []


@api.post("/te/runs/{rid}/simulate")
async def te_simulate(rid: str, body: TeSimIn, user: dict = Depends(get_current_user)):
    doc = await _te_for(user, rid)
    links = doc["links"]
    ids = {L["id"] for L in links}
    costs = {}
    for c in body.costs:
        try:
            if c["link"] in ids and c["dir"] in ("ab", "ba") and 1 <= int(c["cost"]) <= 65535:
                costs[(c["link"], c["dir"])] = int(c["cost"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Custo inválido")
    r = netanalysis.simulate(links, set(body.down_links) & ids, costs, set(body.down_nodes))
    return {"utilization": teopt.utilization(links, r["links"]), "isolated_pairs": r["isolated_pairs"],
            "cut_nodes": r["cut_nodes"], "changed_pairs": r["changed_pairs"],
            "peak": max([u["pct"] or 0 for u in teopt.utilization(links, r["links"])] or [0])}


class TePathIn(BaseModel):
    src: str
    dst: str
    bw_mbps: float = 0
    max_pct: float = 90
    exclude_links: List[str] = []
    exclude_nodes: List[str] = []
    include_nodes: List[str] = []


@api.post("/te/runs/{rid}/path")
async def te_path(rid: str, body: TePathIn, user: dict = Depends(get_current_user)):
    doc = await _te_for(user, rid)
    links = doc["links"]
    nodes = {n["id"]: n for n in doc.get("nodes") or []}
    if body.src not in nodes or body.dst not in nodes or body.src == body.dst:
        raise HTTPException(status_code=400, detail="Escolha origem e destino diferentes")
    if body.bw_mbps < 0 or not 10 <= body.max_pct <= 100:
        raise HTTPException(status_code=400, detail="Banda ou limite inválido")
    spf = teopt.spf_path(links, body.src, body.dst)
    if not spf.get("reachable"):
        netanalysis.build_graph(links)
        why = []
        for nid in (body.src, body.dst):
            mine = [L for L in links if nid in (L["a"], L["b"])]
            ok = [L for L in mine if L.get("in_spf")]
            if not mine:
                why.append(f"{nodes[nid]['name']}: nenhum enlace ponto a ponto descoberto com os outros escolhidos")
            elif not ok:
                nocost = sum(1 for L in mine if L.get("cost_ab") is None or L.get("cost_ba") is None)
                down = sum(1 for L in mine if L.get("oper_a") not in (None, "up", "unknown") or L.get("oper_b") not in (None, "up", "unknown"))
                parts = []
                if nocost:
                    parts.append(f"{nocost} sem custo OSPF lido numa das pontas")
                if down:
                    parts.append(f"{down} com interface fora (down)")
                why.append(f"{nodes[nid]['name']}: {len(mine)} enlace(s) — " + (", ".join(parts) or "nenhum utilizável")
                           + ". Veja “Equipamentos lidos” (saída da CLI) ou use “custo para enlace sem custo lido”.")
        if not why:
            why.append("os dois têm enlaces com OSPF, mas os grupos não se ligam — faltam equipamentos do meio do caminho na seleção")
        spf["why"] = why
    byid = {L["id"]: L for L in links}
    for h in spf.get("hops", []):
        L = byid[h["link"]]
        h.update({"from": L["a_name"] if h["dir"] == "ab" else L["b_name"], "to": L["b_name"] if h["dir"] == "ab" else L["a_name"],
                  "pct_now": L.get(f"{h['dir']}_pct")})
    c = teopt.cspf(links, body.src, body.dst, body.bw_mbps, body.max_pct, set(body.exclude_links), set(body.exclude_nodes),
                   [n for n in body.include_nodes if n in nodes])
    if not spf.get("reachable") and not c.get("found"):
        c["why"] = "sem caminho OSPF entre os dois (veja ao lado)"
    if c.get("found"):
        src = nodes[body.src]
        lsp = re.sub(r"[^A-Za-z0-9-]", "-", f"BASTION-{src['name']}-{nodes[body.dst]['name']}")[:40]
        c["config"] = teopt.te_config(src.get("type"), lsp, c["hops"], nodes[body.dst].get("router_id"), body.bw_mbps)
        c["lsp_name"] = lsp
    return {"spf": spf, "cspf": c}


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
    out = sorted((_user_brief(u) for u in rows), key=lambda u: (u["role"] != "admin", u["name"].lower()))
    if user.get("role") != "admin":            # quem não é admin vê só o nome do administrador, sem e-mail
        out = [{"id": u["id"], "name": u["name"], "email": "", "role": u["role"]} for u in out]
    return out


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


async def _flow_sharers(user: dict) -> dict:
    """Donos que compartilharam o Flow (interfaces e conteúdos) com este usuário: {owner_id: nome}."""
    owners = [s["owner_id"] async for s in db.flow_shares.find({"user_ids": user["id"]}, {"_id": 0, "owner_id": 1}) if s["owner_id"] != user["id"]]
    if not owners:
        return {}
    return {u["id"]: (u.get("name") or u["email"]) async for u in db.users.find({"id": {"$in": owners}}, {"_id": 0, "id": 1, "name": 1, "email": 1})}


async def _flow_ifaces_of(user: dict) -> List[dict]:
    """As interfaces do usuário e as compartilhadas com ele (só leitura, marcadas com shared/owner_name)."""
    mine = await db.flow_ifaces.find({"owner_id": user["id"]}, {"_id": 0}).to_list(2000)
    sharers = await _flow_sharers(user)
    if sharers:
        have = {i["key"] for i in mine}
        for i in await db.flow_ifaces.find({"owner_id": {"$in": list(sharers)}}, {"_id": 0}).to_list(4000):
            if i["key"] not in have:
                have.add(i["key"])
                mine.append({**i, "shared": True, "owner_name": sharers.get(i["owner_id"], "?")})
    return mine


async def _flow_groups_of(user: dict) -> List[dict]:
    mine = await db.flow_groups.find({"owner_id": user["id"]}, {"_id": 0}).to_list(1000)
    sharers = await _flow_sharers(user)
    if sharers:
        for g in await db.flow_groups.find({"owner_id": {"$in": list(sharers)}}, {"_id": 0}).to_list(2000):
            mine.append({**g, "shared": True, "owner_name": sharers.get(g["owner_id"], "?")})
    return mine


class FlowShareIn(BaseModel):
    user_ids: List[str] = []


@api.get("/flow/shares")
async def flow_get_share(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    doc = await db.flow_shares.find_one({"owner_id": user["id"]}, {"_id": 0}) or {}
    users = []
    if user.get("role") == "admin":            # só o administrador vê a lista de usuários e compartilha
        users = await db.users.find({"id": {"$ne": user["id"]}, "role": {"$ne": "viewer"}}, {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1}).to_list(1000)
        users.sort(key=lambda u: (u.get("name") or u["email"]).lower())
    return {"user_ids": (doc.get("user_ids") or []) if user.get("role") == "admin" else [], "users": users,
            "can_share": user.get("role") == "admin",
            "from": [{"id": k, "name": v} for k, v in (await _flow_sharers(user)).items()]}


@api.put("/flow/shares")
async def flow_put_share(body: FlowShareIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Só o administrador compartilha o Flow")
    valid = {u["id"] async for u in db.users.find({"id": {"$in": body.user_ids}, "role": {"$ne": "viewer"}}, {"_id": 0, "id": 1})}
    ids = [i for i in dict.fromkeys(body.user_ids) if i in valid and i != user["id"]]
    await db.flow_shares.update_one({"owner_id": user["id"]}, {"$set": {"owner_id": user["id"], "user_ids": ids}}, upsert=True)
    return {"ok": True, "user_ids": ids}


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
    names = {d["id"]: d.get("name") async for d in db.devices.find({"id": {"$in": list({i["device_id"] for i in items})}}, {"_id": 0, "id": 1, "name": 1})}
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
    return sorted(await _flow_groups_of(user), key=lambda g: (bool(g.get("shared")), g["name"].lower()))


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
    groups = await _flow_groups_of(user)
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
    type: str = "fortinet"               # fortinet | openvpn | pptp | l2tp  (sempre cliente)
    host: str = ""
    port: Optional[int] = None
    username: str = ""
    password: Optional[str] = None       # write-only; vazio mantém
    realm: str = ""
    routes: List[str] = []
    trusted_certs: List[str] = []
    ovpn_config: Optional[str] = None    # OpenVPN: conteúdo do .ovpn (write-only; vazio mantém)
    psk: Optional[str] = None            # L2TP/IPsec: chave pré-compartilhada (write-only; None mantém, "" apaga)
    mppe: bool = True                    # PPTP: exigir criptografia MPPE-128


class VpnConnectIn(BaseModel):
    otp: str = ""


def _vpn_public(p: dict, st: Optional[dict]) -> dict:
    out = {k: v for k, v in p.items() if k not in ("password", "_id", "psk", "ovpn_config")}
    out["type"] = p.get("type") or "fortinet"
    out["has_password"] = bool(p.get("password"))
    out["has_psk"] = bool(p.get("psk"))
    out["has_config"] = bool(p.get("ovpn_config"))
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


def _vpn_clean(body: VpnProfileIn, cur: Optional[dict] = None) -> dict:
    import ipaddress as _ipa
    kind = body.type if body.type in vpnconf.TYPES else None
    if not kind:
        raise HTTPException(400, "Tipo de VPN inválido")
    if cur and (cur.get("type") or "fortinet") != kind:
        raise HTTPException(400, "Não dá para trocar o tipo de uma VPN já criada — apague e crie outra")
    extra: dict = {"type": kind}
    if kind == "openvpn":
        if body.ovpn_config and body.ovpn_config.strip():
            try:
                cfg, rhost, rport, dropped, needs_auth = vpnconf.clean_ovpn(body.ovpn_config)
            except vpnconf.VpnConfError as e:
                raise HTTPException(400, str(e))
            extra.update(ovpn_config=vault.encrypt(cfg), ovpn_dropped=dropped[:30], ovpn_auth=needs_auth)
            body.host, body.port = rhost, rport
        elif cur and cur.get("ovpn_config"):
            body.host, body.port = cur["host"], cur["port"]
        else:
            raise HTTPException(400, "Cole o conteúdo do arquivo .ovpn")
    if body.port is None:
        body.port = vpnconf.DEFAULT_PORT[kind]
    if kind == "pptp":
        body.port = 1723
        extra["mppe"] = bool(body.mppe)
    if kind == "l2tp":
        body.port = 1701
        if body.psk is not None:
            if len(body.psk) > 256 or "\n" in body.psk:
                raise HTTPException(400, "Chave pré-compartilhada inválida")
            extra["psk"] = vault.encrypt(body.psk) if body.psk else ""
    host = body.host.strip().removeprefix("https://").removeprefix("http://").split("/")[0]
    if ":" in host and host.count(":") == 1:
        host, port = host.split(":")
        body.port = int(port) if port.isdigit() else body.port
    if not host or not re.match(r"^[A-Za-z0-9.\-]+$", host):
        raise HTTPException(400, "Gateway inválido (use o IP ou nome, ex.: vpn.empresa.com.br)")
    if not 1 <= body.port <= 65535:
        raise HTTPException(400, "Porta inválida")
    if not body.name.strip():
        raise HTTPException(400, "Informe o nome da VPN")
    if not body.username.strip() and kind != "openvpn":
        raise HTTPException(400, "Informe o usuário")
    if re.search(r"[\r\n]", body.username):
        raise HTTPException(400, "Usuário inválido")
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
            "realm": body.realm.strip()[:60], "routes": list(dict.fromkeys(routes)), "trusted_certs": list(dict.fromkeys(certs)), **extra}


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
    d = await db.vpn_status.find_one({"_id": "_daemon"}, {"_id": 0, "caps": 1}) or {}
    return {"items": out, "daemon": await _vpn_daemon_ok(), "caps": d.get("caps") or {},
            "types": [{"key": k, "label": v} for k, v in vpnconf.TYPES.items()]}


@api.post("/vpns")
async def create_vpn(body: VpnProfileIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    data = _vpn_clean(body)
    needs_pw = data["type"] != "openvpn" or bool(data["username"])
    if needs_pw and not body.password:
        raise HTTPException(400, "Informe a senha da VPN")
    if data["type"] == "openvpn" and data.get("ovpn_auth") and not data["username"]:
        raise HTTPException(400, "Este .ovpn pede usuário e senha (auth-user-pass) — preencha os dois")
    doc = {"id": str(uuid.uuid4()), "owner_id": user["id"], **data, "password": vault.encrypt(body.password) if body.password else "",
           "created_at": datetime.now(timezone.utc).isoformat()}
    await db.vpn_profiles.insert_one(doc)
    return _vpn_public(doc, None)


@api.put("/vpns/{vid}")
async def update_vpn(vid: str, body: VpnProfileIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await _get_vpn_for(user, vid)
    data = _vpn_clean(body, cur)
    if body.password:
        data["password"] = vault.encrypt(body.password)
    if data["type"] == "openvpn" and (data.get("ovpn_auth", cur.get("ovpn_auth"))) and not data["username"]:
        raise HTTPException(400, "Este .ovpn pede usuário e senha (auth-user-pass) — preencha os dois")
    if data["username"] and not (body.password or cur.get("password")):
        raise HTTPException(400, "Informe a senha da VPN")
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


# ---------- Syslog e traps SNMP (coletor no container "log") ----------
def _iso_dt(v: str) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"Data inválida: {v}")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def _logs_scope(user: dict) -> dict:
    """Mesma regra dos incidentes: admin / "vê tudo" = tudo; os demais, só o que é dos equipamentos e redes deles."""
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0, "id": 1, "role": 1, "incidents_scope": 1}) or user
    if u.get("role") == "admin" or u.get("incidents_scope") == "all":
        return {}
    return {"owner_ids": u["id"]}


def _log_public(d: dict) -> dict:
    d.pop("_id", None)
    at = d.get("at")
    if isinstance(at, datetime):
        d["at"] = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()
    d.pop("owner_ids", None)
    return d


@api.get("/logs")
async def logs_list(q: str = "", device_id: str = "", host: str = "", kind: str = "", max_sev: int = 7, rule: str = "",
                    since: str = "", until: str = "", after: str = "", limit: int = 300,
                    user: dict = Depends(get_current_user)):
    _no_viewer(user)
    f: dict = dict(await _logs_scope(user))
    if device_id == "none":
        f["device_id"] = None
    elif device_id:
        f["device_id"] = device_id
    if host.strip():
        f["host"] = host.strip()
    if kind in ("syslog", "trap"):
        f["kind"] = kind
    if 0 <= max_sev < 7:
        f["severity"] = {"$lte": max_sev}
    if rule == "any":
        f["rules.0"] = {"$exists": True}
    elif rule:
        f["rules"] = rule
    rng = {}
    if after:
        rng["$gt"] = _iso_dt(after)
    elif since:
        rng["$gte"] = _iso_dt(since)
    elif q.strip():                     # busca de texto sem período: últimas 24 h
        rng["$gte"] = datetime.now(timezone.utc) - timedelta(hours=24)
    if until:
        rng["$lte"] = _iso_dt(until)
    if rng:
        f["at"] = rng
    if q.strip():
        rx = {"$regex": re.escape(q.strip()[:120]), "$options": "i"}
        f.setdefault("$and", []).append({"$or": [{"msg": rx}, {"host_name": rx}, {"program": rx}, {"device_name": rx}]})
    rows = await db.logs.find(f, {"_id": 0, "owner_ids": 0}).sort("at", -1).to_list(min(1000, max(1, limit)))
    return [_log_public(r) for r in rows]


@api.get("/logs/summary")
async def logs_summary(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    st = await db.config.find_one({"key": "logs_status"}, {"_id": 0}) or {}
    online = False
    try:
        online = (datetime.now(timezone.utc) - datetime.fromisoformat(st.get("updated_at"))).total_seconds() < 120
    except (TypeError, ValueError):
        pass
    scope = await _logs_scope(user)
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    by_sev, top = {}, []
    try:
        async for r in db.logs.aggregate([{"$match": {**scope, "at": {"$gte": since}}},
                                          {"$group": {"_id": {"k": "$kind", "s": "$severity"}, "n": {"$sum": 1}}}]):
            k = "trap" if r["_id"].get("k") == "trap" else str(r["_id"].get("s"))
            by_sev[k] = by_sev.get(k, 0) + r["n"]
        async for r in db.logs.aggregate([{"$match": {**scope, "at": {"$gte": since}}},
                                          {"$group": {"_id": "$host", "n": {"$sum": 1}, "dev": {"$last": "$device_name"},
                                                      "did": {"$last": "$device_id"}}},
                                          {"$sort": {"n": -1}}, {"$limit": 10}]):
            top.append({"host": r["_id"], "count": r["n"], "device_name": r.get("dev") or "", "device_id": r.get("did")})
    except Exception as e:
        logger.warning(f"resumo dos logs: {e}")
    out = {"online": online, "last_hour": by_sev, "top": top}
    if user.get("role") == "admin":
        out.update(listen=st.get("listen") or {}, stats=st.get("stats") or {}, sources=st.get("sources", 0),
                   updated_at=st.get("updated_at"), open_keys=st.get("open_keys", 0))
    return out


class LogSettingsIn(BaseModel):
    enabled: bool = True
    syslog_port: int = 514
    trap_port: int = 162
    retention_days: int = 30
    rate_per_source: int = 200
    store_max_severity: int = 7
    store_unknown: bool = True
    trap_community: str = ""


@api.get("/logs-settings")
async def logs_settings_get(_: dict = Depends(require_admin)):
    doc = await db.config.find_one({"key": "logs"}, {"_id": 0}) or {}
    return {**logparse.DEFAULTS, **{k: v for k, v in doc.items() if k in logparse.DEFAULTS}}


@api.put("/logs-settings")
async def logs_settings_put(body: LogSettingsIn, _: dict = Depends(require_admin)):
    for p in (body.syslog_port, body.trap_port):
        if not 1 <= p <= 65535:
            raise HTTPException(400, "Porta inválida")
    if body.syslog_port == body.trap_port:
        raise HTTPException(400, "Syslog e traps precisam de portas diferentes")
    d = {"enabled": body.enabled, "syslog_port": body.syslog_port, "trap_port": body.trap_port,
         "retention_days": min(3650, max(1, body.retention_days)), "rate_per_source": min(100000, max(1, body.rate_per_source)),
         "store_max_severity": min(7, max(0, body.store_max_severity)), "store_unknown": body.store_unknown,
         "trap_community": body.trap_community.strip()[:64]}
    await db.config.update_one({"key": "logs"}, {"$set": d}, upsert=True)
    return d


@api.get("/log-sources")
async def log_sources(_: dict = Depends(require_admin)):
    rows = await db.log_sources.find({}, {"_id": 0}).sort("last_at", -1).to_list(5000)
    for r in rows:
        if isinstance(r.get("last_at"), datetime):
            r["last_at"] = r["last_at"].replace(tzinfo=r["last_at"].tzinfo or timezone.utc).isoformat()
    return rows


class LogSourceLink(BaseModel):
    device_id: Optional[str] = None


@api.put("/log-sources/{ip}")
async def log_source_link(ip: str, body: LogSourceLink, _: dict = Depends(require_admin)):
    """Liga um IP de origem a um equipamento (ex.: o equipamento manda pelo IP de loopback ou sai NATeado)."""
    did = (body.device_id or "").strip() or None
    if did and not await db.devices.find_one({"id": did}, {"_id": 1}):
        raise HTTPException(404, "Equipamento não encontrado")
    r = await db.log_sources.update_one({"ip": ip}, {"$set": {"linked_device_id": did}})
    if not r.matched_count:
        raise HTTPException(404, "Origem não encontrada")
    return {"ip": ip, "linked_device_id": did}


@api.delete("/log-sources/{ip}")
async def log_source_forget(ip: str, _: dict = Depends(require_admin)):
    await db.log_sources.delete_one({"ip": ip})
    return {"ok": True}


class LogRuleIn(BaseModel):
    name: str = ""
    enabled: bool = True
    pattern: str = ""
    clear_pattern: str = ""
    kinds: str = "any"
    device_ids: List[str] = []
    max_severity: int = 7
    threshold: int = 1
    window_s: int = 60
    severity: str = "medium"
    auto_resolve_min: int = 0
    title: str = ""


def _clean_log_rule(body) -> dict:
    try:
        return logparse.clean_rule(body.model_dump() if hasattr(body, "model_dump") else body.dict())
    except ValueError as e:
        raise HTTPException(400, str(e))


@api.get("/log-rules")
async def log_rules_list(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return await db.log_rules.find({}, {"_id": 0}).sort("created_at", 1).to_list(1000)


@api.post("/log-rules")
async def log_rules_create(body: LogRuleIn, user: dict = Depends(require_admin)):
    r = _clean_log_rule(body)
    r.update(id=uuid.uuid4().hex[:16], created_at=datetime.now(timezone.utc).isoformat(), created_by=user["email"],
             hits=0, last_hit=None)
    await db.log_rules.insert_one(dict(r))
    r.pop("_id", None)
    return r


@api.put("/log-rules/{rid}")
async def log_rules_update(rid: str, body: LogRuleIn, _: dict = Depends(require_admin)):
    cur = await db.log_rules.find_one({"id": rid}, {"_id": 0})
    if not cur:
        raise HTTPException(404, "Regra não encontrada")
    r = _clean_log_rule(body)
    r["preset"] = cur.get("preset", "")
    await db.log_rules.update_one({"id": rid}, {"$set": r})
    return {**cur, **r}


@api.delete("/log-rules/{rid}")
async def log_rules_delete(rid: str, _: dict = Depends(require_admin)):
    await db.log_rules.delete_one({"id": rid})
    return {"ok": True}


@api.post("/log-rules/presets")
async def log_rules_presets(_: dict = Depends(require_admin)):
    """Recoloca os modelos prontos que foram apagados (não mexe nos que existem)."""
    have = {r.get("preset") async for r in db.log_rules.find({}, {"_id": 0, "preset": 1})}
    added = 0
    for p in logparse.PRESETS:
        if p["preset"] in have:
            continue
        r = logparse.clean_rule(p)
        r.update(id=uuid.uuid4().hex[:16], created_at=datetime.now(timezone.utc).isoformat(), hits=0, last_hit=None)
        await db.log_rules.insert_one(r)
        added += 1
    return {"added": added}


class LogRuleTest(BaseModel):
    pattern: str = ""
    clear_pattern: str = ""
    text: str = ""


@api.post("/log-rules/test")
async def log_rules_test(body: LogRuleTest, _: dict = Depends(require_admin)):
    """Testa a regex com linhas coladas (ou com as últimas mensagens gravadas, se não colar nada)."""
    text = body.text[:100000]
    if not text.strip():
        rows = await db.logs.find({}, {"_id": 0, "msg": 1}).sort("at", -1).to_list(500)
        text = "\n".join(r.get("msg", "") for r in rows)
    try:
        compiled = logparse.compile_rx(body.pattern), logparse.compile_rx(body.clear_pattern)
    except (re.error, ValueError) as e:
        raise HTTPException(400, f"Regex inválida: {e}")
    if not compiled[0]:
        raise HTTPException(400, "Informe a regex")
    try:
        res = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(
            None, logparse.test_rule, body.pattern, body.clear_pattern, text), 5)
    except asyncio.TimeoutError:
        raise HTTPException(400, "A regex demorou demais (backtracking); simplifique a expressão")
    hits = [r for r in res if r["match"]]
    return {"lines": len(res), "fire": sum(1 for r in hits if r["match"] == "fire"),
            "clear": sum(1 for r in hits if r["match"] == "clear"), "results": (hits if not body.text.strip() else res)[:300]}


# ---------- Sondas ativas (ping/DNS/HTTP/TCP do servidor e dos jumps) ----------
_probe_tracker = probemod.Tracker()
_probe_last: Dict[str, float] = {}
_probe_busy: set = set()
_probe_ttl = {"days": None}


async def _probe_settings() -> dict:
    doc = await db.config.find_one({"key": "probes"}, {"_id": 0}) or {}
    return {**probemod.DEFAULTS, **{k: v for k, v in doc.items() if k in probemod.DEFAULTS}}


async def _probe_indexes(days: int):
    if _probe_ttl["days"] == days:
        return
    try:
        info = await db.probe_results.index_information()
        if "ttl_at" in info:
            if info["ttl_at"].get("expireAfterSeconds") != days * 86400:
                await db.command("collMod", "probe_results", index={"name": "ttl_at", "expireAfterSeconds": days * 86400})
        else:
            await db.probe_results.create_index("at", name="ttl_at", expireAfterSeconds=days * 86400)
        await db.probe_results.create_index([("probe_id", 1), ("source", 1), ("at", -1)])
        await db.probe_hourly.create_index([("probe_id", 1), ("source", 1), ("hour", 1)], unique=True)
        await db.probe_hourly.create_index("hour", name="ttl_hour", expireAfterSeconds=400 * 86400)
        await db.probe_status.create_index("key", unique=True)
        _probe_ttl["days"] = days
    except Exception as e:
        logger.warning(f"índices das sondas: {e}")


def _probe_host(p: dict) -> str:
    if p["type"] == "http":
        m = re.match(r"^https?://\[?([^\]/:]+)", p["target"])
        return m.group(1) if m else p["target"]
    return p["target"]


async def _probe_source_names() -> Dict[str, str]:
    names = {"server": "BastiON"}
    async for a in db.agents.find({}, {"_id": 0, "id": 1, "name": 1}):
        names[a["id"]] = a["name"]
    return names


async def _probe_store(p: dict, source: str, r: dict, src_name: str):
    now = datetime.now(timezone.utc)
    key = f"{p['id']}:{source}"
    bad, reason = probemod.evaluate(p, r)
    doc = {"probe_id": p["id"], "source": source, "at": now, "ok": bool(r.get("ok")), "ms": r.get("ms"),
           "loss": r.get("loss"), "min": r.get("min"), "max": r.get("max"), "jitter": r.get("jitter"),
           "samples": r.get("samples"), "code": r.get("code"), "error": (r.get("error") or "")[:200],
           "bad": bad, "skipped": bool(r.get("skipped"))}
    await db.probe_results.insert_one({k: v for k, v in doc.items() if v is not None})
    if not r.get("skipped"):
        hour = now.replace(minute=0, second=0, microsecond=0)
        inc = {"n": 1, "ok_n": 0 if bad else 1, "fail_n": 0 if r.get("ok") else 1}
        upd = {"$inc": inc, "$setOnInsert": {"probe_id": p["id"], "source": source, "hour": hour}}
        if p["type"] == "ping":
            inc["sent"] = r.get("sent") or 0
            inc["recv"] = len(r.get("samples") or [])
        if r.get("ms") is not None:
            inc["ms_sum"] = float(r["ms"])
            inc["ms_n"] = 1
            upd["$min"] = {"min": r.get("min") if r.get("min") is not None else r["ms"]}
            upd["$max"] = {"max": r.get("max") if r.get("max") is not None else r["ms"]}
        await db.probe_hourly.update_one({"probe_id": p["id"], "source": source, "hour": hour}, upd, upsert=True)
    act = _probe_tracker.feed(key, p, bad, reason)
    st = _probe_tracker.state(key) if not r.get("skipped") else "skipped"
    await db.probe_status.update_one({"key": key}, {"$set": {
        "key": key, "probe_id": p["id"], "source": source, "at": now.isoformat(), "ok": bool(r.get("ok")),
        "ms": r.get("ms"), "loss": r.get("loss"), "jitter": r.get("jitter"), "code": r.get("code"),
        "error": (r.get("error") or "")[:200], "state": st, "reason": reason},
        "$push": {"spark": {"$each": [r.get("ms") if not bad or r.get("ok") else None], "$slice": -40}}}, upsert=True)
    if act and p.get("alert", True):
        ev = {"key": f"probe:{key}", "severity": p.get("severity", "high"), "host": _probe_host(p), "host_name": "",
              "title": f"Sonda {p['name']}: {reason or 'falhou'} — de {src_name}"[:250]}
        if act == "open":
            ev.update(status="problem", description=f"{probemod.TYPE_LABEL[p['type']]} para {p['target']} a partir de "
                                                    f"{src_name}: {p['fail_count']} ciclo(s) ruins seguidos.\nÚltimo: {reason}")
        else:
            ev["status"] = "ok"
        try:
            await inc_center.ingest("probe", ev)
        except Exception as e:
            logger.warning(f"incidente da sonda: {e}")


async def _probe_on_agent(agent_id: str, items: list) -> dict:
    """Roda as sondas no jump (um script por ciclo, conexão compartilhada do pool)."""
    ag = await db.agents.find_one({"id": agent_id}, {"_id": 0, "status": 1, "name": 1})
    if not ag:
        return {k: {"ok": False, "skipped": True, "error": "agente removido"} for k, _ in items}
    if ag.get("status") == "offline":
        return {k: {"ok": False, "skipped": True, "error": "agente offline"} for k, _ in items}
    priv, _, _ = await _get_ssh_key()
    script = probemod.agent_script(items)
    budget = max(int(p.get("timeout_s", 5)) + int(p.get("count", 1)) // 2 for _, p in items) + 20
    w = None
    try:
        chain = await _agent_chain(agent_id)
        w = SSHClientWrapper([_agent_hop(x, priv) for x in chain])
        await w.connect()
        res = await w.run_command("bash -c " + shlex.quote(script), timeout=budget)
    except VpnDown as e:
        return {k: {"ok": False, "skipped": True, "error": str(e)[:150]} for k, _ in items}
    except Exception as e:
        return {k: {"ok": False, "skipped": True, "error": f"sem acesso ao jump: {str(e)[:120]}"} for k, _ in items}
    finally:
        if w:
            await w.close()
    out = probemod.parse_agent_output(res.get("stdout", ""), items)
    if "@@B " not in (res.get("stdout") or ""):
        err = ((res.get("stderr") or "").strip().splitlines() or ["o jump não executou o script (precisa de bash)"])[-1][:150]
        for v in out.values():
            v["error"] = err
    return out


async def _probe_cycle(p_by_id: dict, due: list, names: dict):
    server_items = [(k, p_by_id[pid]) for k, pid, src in due if src == "server"]
    agents: Dict[str, list] = {}
    for k, pid, src in due:
        if src != "server":
            agents.setdefault(src, []).append((k, p_by_id[pid]))
    sem = asyncio.Semaphore(20)

    async def one(k, p):
        async with sem:
            try:
                r = await probemod.run_local(p)
            except Exception as e:
                r = {"ok": False, "ms": None, "error": str(e)[:150]}
            await _probe_store(p, "server", r, names.get("server", "BastiON"))
            _probe_busy.discard(k)

    async def agent(aid, items):
        try:
            res = await _probe_on_agent(aid, [(k.replace(":", "_"), p) for k, p in items])
            for k, p in items:
                await _probe_store(p, aid, res.get(k.replace(":", "_")) or {"ok": False, "skipped": True, "error": "sem resultado"},
                                   names.get(aid, "jump"))
        finally:
            for k, _ in items:
                _probe_busy.discard(k)

    await asyncio.gather(*[one(k, p) for k, p in server_items], *[agent(a, it) for a, it in agents.items()],
                         return_exceptions=True)


async def _probe_loop():
    await asyncio.sleep(25)
    try:
        async for i in db.incidents.find({"status": {"$in": ["open", "ack"]}, "key": {"$regex": "^probe:"}}, {"_id": 0, "key": 1}):
            _probe_tracker.restore(i["key"][6:])
    except Exception:
        pass
    tick = 0
    while True:
        try:
            cfg = await _probe_settings()
            if tick % 60 == 0:
                await _probe_indexes(max(1, int(cfg.get("retention_days") or 14)))
            tick += 1
            if cfg.get("enabled", True):
                probes = await db.probes.find({"enabled": True}, {"_id": 0}).to_list(2000)
                now = time.monotonic()
                due, by_id = [], {}
                for p in probes:
                    by_id[p["id"]] = p
                    for src in p.get("sources") or ["server"]:
                        k = f"{p['id']}:{src}"
                        if k in _probe_busy or now - _probe_last.get(k, 0) < p.get("interval_s", 60):
                            continue
                        _probe_last[k] = now
                        _probe_busy.add(k)
                        due.append((k, p["id"], src))
                if due:
                    _bg(_probe_cycle(by_id, due, await _probe_source_names()))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"sondas: {e}")
        await asyncio.sleep(5)


class ProbeIn(BaseModel):
    name: str = ""
    type: str = "ping"
    target: str = ""
    group: str = ""
    enabled: bool = True
    sources: List[str] = ["server"]
    interval_s: int = 60
    count: int = 10
    timeout_s: int = 5
    max_loss: int = 20
    max_ms: int = 0
    fail_count: int = 3
    recover_count: int = 2
    severity: str = "high"
    alert: bool = True
    query: str = ""
    qtype: str = "A"
    port: int = 0
    expect_status: int = 0
    contains: str = ""
    insecure: bool = False


async def _clean_probe(body: ProbeIn) -> dict:
    try:
        p = probemod.clean_probe(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    ids = {a["id"] async for a in db.agents.find({}, {"_id": 0, "id": 1})}
    bad = [s for s in p["sources"] if s != "server" and s not in ids]
    if bad:
        raise HTTPException(400, "Origem (agente) não encontrada")
    return p


@api.get("/probes")
async def probes_list(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    rows = await db.probes.find({}, {"_id": 0}).sort([("group", 1), ("name", 1)]).to_list(2000)
    st: Dict[str, dict] = {}
    async for s in db.probe_status.find({}, {"_id": 0}):
        st.setdefault(s["probe_id"], {})[s["source"]] = s
    for r in rows:
        r["status"] = st.get(r["id"], {})
    return {"probes": rows, "sources": await _probe_source_names()}


@api.get("/probes-sources")
async def probes_sources(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    out = [{"id": "server", "name": "BastiON (servidor)", "status": "online"}]
    async for a in db.agents.find({}, {"_id": 0, "id": 1, "name": 1, "status": 1, "location": 1}).sort("name", 1):
        out.append({"id": a["id"], "name": a["name"], "status": a.get("status", "unknown"), "location": a.get("location", "")})
    return out


@api.post("/probes")
async def probes_create(body: ProbeIn, user: dict = Depends(require_admin)):
    p = await _clean_probe(body)
    p.update(id=uuid.uuid4().hex[:16], created_at=datetime.now(timezone.utc).isoformat(), created_by=user["email"])
    await db.probes.insert_one(dict(p))
    return p


@api.put("/probes/{pid}")
async def probes_update(pid: str, body: ProbeIn, _: dict = Depends(require_admin)):
    if not await db.probes.find_one({"id": pid}, {"_id": 1}):
        raise HTTPException(404, "Sonda não encontrada")
    p = await _clean_probe(body)
    await db.probes.update_one({"id": pid}, {"$set": p})
    for k in [k for k in _probe_last if k.startswith(pid + ":")]:
        _probe_last.pop(k, None)               # roda de novo já com a configuração nova
    await db.probe_status.delete_many({"probe_id": pid, "source": {"$nin": p["sources"]}})
    return {**p, "id": pid}


@api.delete("/probes/{pid}")
async def probes_delete(pid: str, _: dict = Depends(require_admin)):
    await db.probes.delete_one({"id": pid})
    for col in (db.probe_status, db.probe_results, db.probe_hourly):
        await col.delete_many({"probe_id": pid})
    return {"ok": True}


class ProbePresetsIn(BaseModel):
    dns_servers: List[str] = []
    sources: List[str] = ["server"]
    public: bool = True


@api.post("/probes/presets")
async def probes_presets(body: ProbePresetsIn, user: dict = Depends(require_admin)):
    """Modelos: DNS recursivos da rede (os IPs informados) + DNS públicos, trânsito e HTTP."""
    have = {(r["type"], r["target"]) async for r in db.probes.find({}, {"_id": 0, "type": 1, "target": 1})}
    items = []
    for i, ip in enumerate(x.strip() for x in body.dns_servers if x.strip()):
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise HTTPException(400, f"IP inválido: {ip}")
        items += [{"name": f"DNS recursivo {i + 1} ({ip})", "type": "dns", "target": ip, "query": "google.com",
                   "group": "DNS recursivos", "max_ms": 100, "severity": "critical"},
                  {"name": f"Ping DNS recursivo {i + 1} ({ip})", "type": "ping", "target": ip, "group": "DNS recursivos",
                   "max_loss": 10, "max_ms": 50}]
    if body.public:
        items += probemod.PRESETS
    added = 0
    for it in items:
        if (it["type"], it["target"]) in have:
            continue
        try:
            p = await _clean_probe(ProbeIn(**{**it, "sources": body.sources or ["server"]}))
        except HTTPException:
            continue
        p.update(id=uuid.uuid4().hex[:16], created_at=datetime.now(timezone.utc).isoformat(), created_by=user["email"])
        await db.probes.insert_one(p)
        have.add((p["type"], p["target"]))
        added += 1
    return {"added": added}


class ProbeTestIn(ProbeIn):
    source: str = "server"


@api.post("/probes/test")
async def probes_test(body: ProbeTestIn, _: dict = Depends(require_admin)):
    """Roda a sonda uma vez (sem gravar) — botão Testar do cadastro."""
    p = await _clean_probe(body)
    if body.source == "server":
        r = await probemod.run_local(p)
    else:
        if not await db.agents.find_one({"id": body.source}, {"_id": 1}):
            raise HTTPException(404, "Agente não encontrado")
        r = (await _probe_on_agent(body.source, [("t", p)]))["t"]
    bad, reason = probemod.evaluate(p, r)
    return {**r, "bad": bad, "reason": reason}


@api.get("/probes/{pid}/series")
async def probes_series(pid: str, source: str = "server", hours: int = 24, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    p = await db.probes.find_one({"id": pid}, {"_id": 0})
    if not p:
        raise HTTPException(404, "Sonda não encontrada")
    hours = max(1, min(24 * 400, hours))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    def iso(d):
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()
    if hours <= 48:
        rows = await db.probe_results.find({"probe_id": pid, "source": source, "at": {"$gte": since}},
                                           {"_id": 0, "probe_id": 0, "source": 0}).sort("at", 1).to_list(6000)
        for r in rows:
            r["at"] = iso(r["at"])
        return {"kind": "raw", "probe": p, "points": rows}
    rows = await db.probe_hourly.find({"probe_id": pid, "source": source, "hour": {"$gte": since}}, {"_id": 0}).sort("hour", 1).to_list(10000)
    pts = []
    for r in rows:
        sent = r.get("sent") or 0
        pts.append({"at": iso(r["hour"]), "ms": round(r["ms_sum"] / r["ms_n"], 2) if r.get("ms_n") else None,
                    "min": r.get("min"), "max": r.get("max"),
                    "loss": round(100.0 * (sent - (r.get("recv") or 0)) / sent, 1) if sent else None,
                    "avail": round(100.0 * r.get("ok_n", 0) / r["n"], 2) if r.get("n") else None, "n": r.get("n", 0),
                    "ok": r.get("fail_n", 0) < r.get("n", 0)})
    return {"kind": "hourly", "probe": p, "points": pts}


class ProbeSettingsIn(BaseModel):
    enabled: bool = True
    retention_days: int = 14


@api.get("/probes-settings")
async def probes_settings_get(_: dict = Depends(require_admin)):
    return await _probe_settings()


@api.put("/probes-settings")
async def probes_settings_put(body: ProbeSettingsIn, _: dict = Depends(require_admin)):
    d = {"enabled": body.enabled, "retention_days": min(365, max(1, body.retention_days))}
    await db.config.update_one({"key": "probes"}, {"$set": d}, upsert=True)
    return d


# ---------- SLA e disponibilidade ----------
class SlaComponent(BaseModel):
    type: str                       # probe | device | interface
    probe_id: Optional[str] = None
    source: Optional[str] = "server"
    device_id: Optional[str] = None
    if_index: Optional[int] = None


class SlaItemIn(BaseModel):
    name: str
    kind: str = "link"
    target: float = 99.5
    customer: str = ""
    notes: str = ""
    components: List[SlaComponent] = []


def _sla_scope(user: dict) -> dict:
    return {} if user.get("role") == "admin" else {"owner_id": user["id"]}


async def _sla_clean(body: SlaItemIn, user: dict) -> dict:
    if not body.name.strip():
        raise HTTPException(400, "Informe o nome")
    comps = []
    my_devs = {d["id"]: d async for d in db.devices.find(_scope(user), {"_id": 0, "id": 1, "name": 1})}
    for c in body.components[:50]:
        if c.type == "probe":
            p = await db.probes.find_one({"id": c.probe_id}, {"_id": 0, "id": 1, "name": 1, "sources": 1})
            if not p:
                raise HTTPException(400, "Sonda não encontrada")
            src = c.source or "server"
            if src not in (p.get("sources") or []):
                raise HTTPException(400, f"A sonda {p['name']} não mede dessa origem")
            comps.append({"type": "probe", "probe_id": p["id"], "source": src})
        elif c.type in ("device", "interface"):
            if c.device_id not in my_devs:
                raise HTTPException(400, "Equipamento não encontrado")
            if c.type == "interface":
                m = await db.if_monitors.find_one({"device_id": c.device_id, "if_index": c.if_index}, {"_id": 0, "if_index": 1})
                if not m:
                    raise HTTPException(400, "Interface não está monitorada (marque em Mapas → monitorar interface)")
                comps.append({"type": "interface", "device_id": c.device_id, "if_index": int(c.if_index)})
            else:
                comps.append({"type": "device", "device_id": c.device_id})
    if not comps:
        raise HTTPException(400, "Escolha pelo menos um componente")
    return {"name": body.name.strip()[:100], "kind": body.kind if body.kind in slamod.KINDS else "other",
            "target": min(100.0, max(0.0, float(body.target))), "customer": body.customer.strip()[:100],
            "notes": body.notes.strip()[:500], "components": comps}


@api.get("/sla/items")
async def sla_items(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return await db.sla_items.find(_sla_scope(user), {"_id": 0}).sort([("kind", 1), ("name", 1)]).to_list(2000)


@api.post("/sla/items")
async def sla_create(body: SlaItemIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    it = await _sla_clean(body, user)
    it.update(id=uuid.uuid4().hex[:16], owner_id=user["id"], created_at=datetime.now(timezone.utc).isoformat())
    await db.sla_items.insert_one(dict(it))
    return it


@api.put("/sla/items/{iid}")
async def sla_update(iid: str, body: SlaItemIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await db.sla_items.find_one({"id": iid, **_sla_scope(user)}, {"_id": 0})
    if not cur:
        raise HTTPException(404, "Item não encontrado")
    it = await _sla_clean(body, user)
    await db.sla_items.update_one({"id": iid}, {"$set": it})
    return {**cur, **it}


@api.delete("/sla/items/{iid}")
async def sla_delete(iid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await db.sla_items.delete_one({"id": iid, **_sla_scope(user)})
    return {"ok": True}


@api.get("/sla/components")
async def sla_components(user: dict = Depends(get_current_user)):
    """O que pode entrar num item: sondas (por origem), equipamentos e interfaces monitoradas."""
    _no_viewer(user)
    names = await _probe_source_names()
    probes = [{"probe_id": p["id"], "source": s, "label": f"{p['name']} — de {names.get(s, s)}", "group": p.get("group") or ""}
              for p in await db.probes.find({}, {"_id": 0}).sort("name", 1).to_list(2000) for s in p.get("sources") or []]
    devs = await db.devices.find(_scope(user), {"_id": 0, "id": 1, "name": 1, "host": 1, "tags": 1}).sort("name", 1).to_list(5000)
    ids = [d["id"] for d in devs]
    dn = {d["id"]: d["name"] for d in devs}
    ifs = [{"device_id": m["device_id"], "if_index": m["if_index"],
            "label": f"{dn.get(m['device_id'], '?')} · {m.get('if_name') or m['if_index']}" + (f" ({m['alias']})" if m.get("alias") else "")}
           for m in await db.if_monitors.find({"device_id": {"$in": ids}}, {"_id": 0, "device_id": 1, "if_index": 1, "if_name": 1, "alias": 1}).to_list(10000)]
    ifs.sort(key=lambda x: x["label"])
    return {"probes": probes, "devices": devs, "interfaces": ifs}


async def _maint_for(device_id: Optional[str], probe_id: Optional[str], start: float, end: float) -> list:
    """Janelas de manutenção que cobrem o componente no período (equipamento por id/tag, sonda por id, ou 'tudo')."""
    out = []
    tags = []
    if device_id:
        d = await db.devices.find_one({"id": device_id}, {"_id": 0, "tags": 1}) or {}
        tags = d.get("tags") or []
    async for w in db.maint_windows.find({"status": {"$ne": "cancelled"}}, {"_id": 0, "start": 1, "end": 1, "all": 1,
                                                                          "device_ids": 1, "tags": 1, "probe_ids": 1}):
        a, b = slamod.ts(w.get("start")), slamod.ts(w.get("end"))
        if a is None or b is None or b <= start or a >= end:
            continue
        hit = w.get("all") or (device_id and device_id in (w.get("device_ids") or [])) or \
            (tags and set(tags) & set(w.get("tags") or [])) or (probe_id and probe_id in (w.get("probe_ids") or []))
        if hit:
            out.append((a, b))
    return out


async def _sla_compute(it: dict, month: str) -> dict:
    start_dt, end_dt = slamod.month_range(month)
    start, end, now = start_dt.timestamp(), end_dt.timestamp(), time.time()
    names = await _probe_source_names()
    comps, extra = [], []
    for c in it.get("components") or []:
        if c["type"] == "device":
            d = await db.devices.find_one({"id": c["device_id"]}, {"_id": 0, "name": 1}) or {}
            incs = await db.incidents.find({"key": f"bastion:device:{c['device_id']}", "created_at": {"$lt": end_dt.isoformat()},
                                            "$or": [{"resolved_at": None}, {"resolved_at": {"$gte": start_dt.isoformat()}}]},
                                           {"_id": 0, "created_at": 1, "resolved_at": 1}).to_list(5000)
            comps.append({"label": f"Equipamento {d.get('name', '?')}", "type": "device", "down": slamod.device_outages(incs, now),
                          "maint": await _maint_for(c["device_id"], None, start, end), "measured": None})
        elif c["type"] == "interface":
            m = await db.if_monitors.find_one({"device_id": c["device_id"], "if_index": c["if_index"]}, {"_id": 0}) or {}
            d = await db.devices.find_one({"id": c["device_id"]}, {"_id": 0, "name": 1}) or {}
            q = {"device_id": c["device_id"], "if_index": c["if_index"]}
            before = await db.if_events.find({**q, "at": {"$lt": start_dt.isoformat()}}, {"_id": 0, "at": 1, "status": 1}).sort("at", -1).to_list(1)
            evs = before + await db.if_events.find({**q, "at": {"$gte": start_dt.isoformat(), "$lt": end_dt.isoformat()}},
                                                   {"_id": 0, "at": 1, "status": 1}).sort("at", 1).to_list(20000)
            lbl = f"Interface {d.get('name', '?')} · {m.get('if_name') or c['if_index']}" + (f" ({m['alias']})" if m.get("alias") else "")
            comps.append({"label": lbl, "type": "interface", "down": slamod.iface_outages(evs, start, end, now),
                          "maint": await _maint_for(c["device_id"], None, start, end), "measured": None})
        else:
            p = await db.probes.find_one({"id": c["probe_id"]}, {"_id": 0, "name": 1, "type": 1, "target": 1}) or {"name": "?", "type": ""}
            hours = await db.probe_hourly.find({"probe_id": c["probe_id"], "source": c["source"], "hour": {"$gte": start_dt, "$lt": end_dt}},
                                               {"_id": 0}).to_list(2000)
            down, meas = slamod.probe_outages(hours)
            comps.append({"label": f"Sonda {p['name']} — de {names.get(c['source'], c['source'])}", "type": "probe", "down": down,
                          "maint": await _maint_for(None, c["probe_id"], start, end), "measured": meas})
            ms_sum = sum(h.get("ms_sum") or 0 for h in hours)
            ms_n = sum(h.get("ms_n") or 0 for h in hours)
            sent = sum(h.get("sent") or 0 for h in hours)
            recv = sum(h.get("recv") or 0 for h in hours)
            ok_n, n = sum(h.get("ok_n") or 0 for h in hours), sum(h.get("n") or 0 for h in hours)
            extra.append({"label": comps[-1]["label"], "ms_avg": round(ms_sum / ms_n, 2) if ms_n else None,
                          "loss": round(100.0 * (sent - recv) / sent, 2) if sent else None,
                          "within_limits": round(100.0 * ok_n / n, 2) if n else None, "max": max((h.get("max") or 0 for h in hours), default=None)})
    r = slamod.summarize(comps, start, end, now, float(it.get("target") or 0))
    r["quality"] = extra
    r["month"] = month
    return r


@api.get("/sla/report")
async def sla_report(month: str = "", ids: str = "", user: dict = Depends(get_current_user)):
    _no_viewer(user)
    if not month:
        month = datetime.now(slamod.BR).strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month) or not 1 <= int(month[5:]) <= 12:
        raise HTTPException(400, "Mês inválido (AAAA-MM)")
    f = _sla_scope(user)
    if ids:
        f["id"] = {"$in": [x for x in ids.split(",") if x][:500]}
    items = await db.sla_items.find(f, {"_id": 0}).sort([("kind", 1), ("name", 1)]).to_list(2000)
    out = []
    for it in items:
        out.append({"item": it, "result": await _sla_compute(it, month)})
    return {"month": month, "generated_at": datetime.now(timezone.utc).isoformat(), "items": out,
            "kinds": slamod.KINDS}


@api.get("/sla/report.csv")
async def sla_report_csv(month: str = "", ids: str = "", user: dict = Depends(get_current_user)):
    rep = await sla_report(month, ids, user)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["item", "tipo", "cliente", "meta_%", "disponibilidade_%", "cumpriu", "indisponivel_min", "manutencao_min",
                "quedas", "mttr_min", "maior_queda_min", "cobertura_%"])
    for x in rep["items"]:
        it, r = x["item"], x["result"]
        w.writerow([it["name"], slamod.KINDS.get(it["kind"], it["kind"]), it.get("customer", ""), it.get("target"),
                    "" if r["availability"] is None else f"{r['availability']:.3f}".replace(".", ","),
                    "sim" if r["met"] else "não", round(r["downtime_s"] / 60, 1), round(r["maint_s"] / 60, 1), r["outages"],
                    round(r["mttr_s"] / 60, 1), round(r["longest_s"] / 60, 1), r["coverage"]])
    return PlainTextResponse("﻿" + buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="sla-{rep["month"]}.csv"'})


class SlaSettingsIn(BaseModel):
    monthly_alert: bool = False


@api.get("/sla/settings")
async def sla_settings_get(_: dict = Depends(require_admin)):
    doc = await db.config.find_one({"key": "sla"}, {"_id": 0}) or {}
    return {"monthly_alert": bool(doc.get("monthly_alert")), "last_sent": doc.get("last_sent")}


@api.put("/sla/settings")
async def sla_settings_put(body: SlaSettingsIn, _: dict = Depends(require_admin)):
    await db.config.update_one({"key": "sla"}, {"$set": {"monthly_alert": body.monthly_alert}}, upsert=True)
    return await sla_settings_get(_)


async def _sla_monthly_loop():
    """Dia 1, a partir das 08h (Brasília): resumo do mês anterior no Telegram/push."""
    await asyncio.sleep(120)
    while True:
        try:
            now = datetime.now(slamod.BR)
            cfg = await db.config.find_one({"key": "sla"}, {"_id": 0}) or {}
            prev = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
            if cfg.get("monthly_alert") and now.day == 1 and now.hour >= 8 and cfg.get("last_sent") != prev:
                await db.config.update_one({"key": "sla"}, {"$set": {"last_sent": prev}}, upsert=True)
                lines = []
                for it in await db.sla_items.find({}, {"_id": 0}).sort("name", 1).to_list(500):
                    r = await _sla_compute(it, prev)
                    av = "—" if r["availability"] is None else f"{r['availability']:.3f}%"
                    lines.append(f"{'✅' if r['met'] else '❌'} {it['name']}: {av} (meta {it['target']}%) · "
                                 f"{r['outages']} queda(s), {slamod.fmt_dur(r['downtime_s'])} fora")
                if lines:
                    await automation.send_alert(db, f"📊 SLA de {prev[5:]}/{prev[:4]}", "\n".join(lines)[:3800],
                                                push_url=f"/sla?month={prev}", push_tag=f"sla-{prev}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"SLA mensal: {e}")
        await asyncio.sleep(1800)


# ---------- Janelas de manutenção e pré/pós-check ----------
class MaintIn(BaseModel):
    name: str
    description: str = ""
    start: str
    end: str
    all: bool = False
    device_ids: List[str] = []
    tags: List[str] = []
    probe_ids: List[str] = []
    silence: bool = True
    auto_checks: bool = True
    check_device_ids: List[str] = []
    commands: Dict[str, List[List[str]]] = {}
    notify: bool = True


def _maint_scope(user: dict) -> dict:
    return {} if user.get("role") == "admin" else {"owner_id": user["id"]}


def _maint_public(w: dict, full: bool = False) -> dict:
    out = {k: v for k, v in w.items() if k not in ("_id",)}
    out["state"] = maintmod.window_state(w)
    ch = w.get("checks") or {}
    if not full:
        out["checks"] = {ph: {k: v for k, v in (ch.get(ph) or {}).items() if k != "devices"} |
                         {"n": len((ch.get(ph) or {}).get("devices") or {})} for ph in ("pre", "post") if ch.get(ph)}
    return out


async def _maint_clean(body: MaintIn, user: dict) -> dict:
    a, b = maintmod._ts(body.start), maintmod._ts(body.end)
    if a is None or b is None:
        raise HTTPException(400, "Informe início e fim")
    if b <= a:
        raise HTTPException(400, "O fim precisa ser depois do início")
    if b - a > 7 * 86400:
        raise HTTPException(400, "Janela de no máximo 7 dias")
    if body.all and user.get("role") != "admin":
        raise HTTPException(403, "Só o administrador cria janela para a rede toda")
    mine = {d["id"] async for d in db.devices.find(_scope(user), {"_id": 0, "id": 1})}
    devs = [d for d in dict.fromkeys(body.device_ids) if d in mine]
    chk = [d for d in dict.fromkeys(body.check_device_ids) if d in mine][:200]
    if not (body.all or devs or body.tags or body.probe_ids):
        raise HTTPException(400, "Escolha o que entra na janela (equipamentos, grupos ou sondas)")
    cmds = {}
    for vt, lst in (body.commands or {}).items():
        if vt in maintmod.TEMPLATES:
            cmds[vt] = [[k if k in maintmod.KINDS else "custom", c.strip()[:300]] for k, c in lst if c.strip()][:30]
    return {"name": body.name.strip()[:120] or "Manutenção", "description": body.description.strip()[:2000],
            "start": datetime.fromtimestamp(a, timezone.utc).isoformat(), "end": datetime.fromtimestamp(b, timezone.utc).isoformat(),
            "all": body.all, "device_ids": devs, "tags": [t.strip() for t in body.tags if t.strip()][:50],
            "probe_ids": body.probe_ids[:100], "silence": body.silence, "auto_checks": body.auto_checks,
            "check_device_ids": chk, "commands": cmds, "notify": body.notify}


async def _maint_get(wid: str, user: dict) -> dict:
    w = await db.maint_windows.find_one({"id": wid, **_maint_scope(user)}, {"_id": 0})
    if not w:
        raise HTTPException(404, "Janela não encontrada")
    return w


@api.get("/maint")
async def maint_list(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    rows = await db.maint_windows.find(_maint_scope(user), {"_id": 0}).sort("start", -1).to_list(500)
    return [_maint_public(w) for w in rows]


@api.get("/maint/templates")
async def maint_templates(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return {"kinds": maintmod.KINDS, "templates": {k: [list(x) for x in v] for k, v in maintmod.TEMPLATES.items()}}


@api.post("/maint")
async def maint_create(body: MaintIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    w = await _maint_clean(body, user)
    w.update(id=uuid.uuid4().hex[:16], owner_id=user["id"], created_by=user.get("name") or user["email"],
             created_at=datetime.now(timezone.utc).isoformat(), status="ok", checks={})
    await db.maint_windows.insert_one(dict(w))
    maintmod.invalidate()
    return _maint_public(w)


@api.put("/maint/{wid}")
async def maint_update(wid: str, body: MaintIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await _maint_get(wid, user)
    w = await _maint_clean(body, user)
    await db.maint_windows.update_one({"id": wid}, {"$set": w})
    maintmod.invalidate()
    return _maint_public({**cur, **w})


@api.post("/maint/{wid}/{action}")
async def maint_action(wid: str, action: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    w = await _maint_get(wid, user)
    now = datetime.now(timezone.utc).isoformat()
    if action == "cancel":
        await db.maint_windows.update_one({"id": wid}, {"$set": {"status": "cancelled"}})
    elif action == "end-now":
        if maintmod.window_state(w) != "active":
            raise HTTPException(400, "A janela não está em andamento")
        await db.maint_windows.update_one({"id": wid}, {"$set": {"end": now}})
    elif action == "start-now":
        if maintmod.window_state(w) != "scheduled":
            raise HTTPException(400, "A janela já começou")
        await db.maint_windows.update_one({"id": wid}, {"$set": {"start": now}})
    elif action in ("pre", "post"):
        st = ((w.get("checks") or {}).get(action) or {}).get("status")
        if st == "running":
            raise HTTPException(409, "Já está rodando")
        if not await _maint_check_devices(w, user):
            raise HTTPException(400, "Nenhum equipamento para o check (escolha em 'Rodar o check em')")
        _bg(_maint_run_check(wid, action, user.get("name") or user["email"], user))
    else:
        raise HTTPException(404, "Ação desconhecida")
    maintmod.invalidate()
    return _maint_public(await _maint_get(wid, user))


@api.delete("/maint/{wid}")
async def maint_delete(wid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _maint_get(wid, user)
    await db.maint_windows.delete_one({"id": wid})
    maintmod.invalidate()
    return {"ok": True}


@api.get("/maint/{wid}")
async def maint_detail(wid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    w = await _maint_get(wid, user)
    out = _maint_public(w, full=True)
    ch = w.get("checks") or {}
    pre, post = (ch.get("pre") or {}).get("devices") or {}, (ch.get("post") or {}).get("devices") or {}
    cmp_ = []
    for did in dict.fromkeys(list(pre) + list(post)):
        a, b = pre.get(did) or {}, post.get(did) or {}
        rows = maintmod.compare(a.get("outputs") or {}, b.get("outputs") or {}) if b else []
        cmp_.append({"device_id": did, "device_name": a.get("name") or b.get("name") or "?", "pre_ok": a.get("ok"),
                     "post_ok": b.get("ok") if b else None, "pre_error": a.get("error", ""), "post_error": b.get("error", ""),
                     "rows": rows, "worse": any(r["worse"] for r in rows), "changed": any(r["changed"] for r in rows),
                     "pre_metrics": [{"cmd": c, "kind": v.get("kind"), "m": maintmod.metrics(v.get("kind", ""), v.get("out", ""))}
                                     for c, v in (a.get("outputs") or {}).items()]})
    out["compare"] = cmp_
    out["incidents"] = await db.incidents.find({"maintenance.id": wid}, {"_id": 0, "id": 1, "number": 1, "title": 1, "status": 1,
                                                                         "severity": 1, "created_at": 1}).sort("created_at", 1).to_list(500)
    return out


@api.get("/maint/{wid}/output")
async def maint_output(wid: str, phase: str, device_id: str, cmd: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    w = await _maint_get(wid, user)
    o = ((((w.get("checks") or {}).get(phase) or {}).get("devices") or {}).get(device_id) or {}).get("outputs") or {}
    if cmd not in o:
        raise HTTPException(404, "Saída não encontrada")
    return {"out": o[cmd].get("out", ""), "error": o[cmd].get("error", "")}


async def _maint_check_devices(w: dict, user: Optional[dict] = None) -> List[dict]:
    ids = w.get("check_device_ids") or w.get("device_ids") or []
    q = {"id": {"$in": ids}} if ids else ({"tags": {"$in": w["tags"]}} if w.get("tags") else None)
    if q is None:
        return []
    q.update({"owner_id": w["owner_id"]} if w.get("owner_id") else {})
    return await db.devices.find(q, {"_id": 0}).to_list(200)


async def _maint_run_check(wid: str, phase: str, who: str, user: Optional[dict] = None):
    w = await db.maint_windows.find_one({"id": wid}, {"_id": 0})
    if not w:
        return
    devs = await _maint_check_devices(w)
    now = datetime.now(timezone.utc).isoformat()
    await db.maint_windows.update_one({"id": wid}, {"$set": {f"checks.{phase}": {"status": "running", "started_at": now, "by": who,
                                                                               "total": len(devs), "done": 0, "devices": {}}}})
    sem = asyncio.Semaphore(8)

    async def one(dev):
        async with sem:
            dtype = dev.get("device_type") or "other"
            cmds = (w.get("commands") or {}).get(dtype) or [list(x) for x in maintmod.TEMPLATES.get(dtype, maintmod.TEMPLATES["other"])]
            res = {"name": dev["name"], "ok": True, "error": "", "outputs": {}}
            cli = None
            try:
                cli = await asyncio.wait_for(_connect_device(dev), 40)
                sh = await cli.shell()
                for kind, cmd in cmds:
                    try:
                        out = await asyncio.wait_for(sh.run(cmd, timeout=60, idle=2.0), 90)
                        res["outputs"][cmd] = {"kind": kind, "out": (out or "")[:200000]}
                    except Exception as e:
                        res["outputs"][cmd] = {"kind": kind, "out": "", "error": str(e)[:200]}
                await sh.close()
                await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": (user or {}).get("id"), "user_email": who,
                                              "device_id": dev["id"], "device_name": dev["name"],
                                              "started_at": datetime.now(timezone.utc).isoformat(),
                                              "ended_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": 0,
                                              "kind": "batch", "commands": [c for _, c in cmds], "commands_count": len(cmds),
                                              "note": f"{'pré' if phase == 'pre' else 'pós'}-check da manutenção {w.get('name', '')}"})
            except Exception as e:
                res.update(ok=False, error=str(e)[:300] or e.__class__.__name__)
            finally:
                if cli:
                    try:
                        await cli.close()
                    except Exception:
                        pass
            await db.maint_windows.update_one({"id": wid}, {"$set": {f"checks.{phase}.devices.{dev['id']}": res},
                                                            "$inc": {f"checks.{phase}.done": 1}})

    await asyncio.gather(*[one(d) for d in devs], return_exceptions=True)
    await db.maint_windows.update_one({"id": wid}, {"$set": {f"checks.{phase}.status": "done",
                                                            f"checks.{phase}.finished_at": datetime.now(timezone.utc).isoformat()}})
    if phase == "post" and w.get("notify", True):
        try:
            full = await db.maint_windows.find_one({"id": wid}, {"_id": 0})
            ch = full.get("checks") or {}
            pre, post = (ch.get("pre") or {}).get("devices") or {}, (ch.get("post") or {}).get("devices") or {}
            lines = []
            for did, b in post.items():
                rows = maintmod.compare((pre.get(did) or {}).get("outputs") or {}, b.get("outputs") or {})
                bad = [f"{maintmod.KINDS.get(r['kind'], r['kind'])}: " + ", ".join(f"{k} {x}→{y}" for k, (x, y) in r["delta"].items())
                       for r in rows if r["worse"]]
                if not b.get("ok"):
                    lines.append(f"❌ {b['name']}: {b.get('error')}")
                elif bad:
                    lines.append(f"⚠️ {b['name']}: " + "; ".join(bad)[:300])
            title = f"🛠️ Pós-check — {w.get('name')}: " + ("tudo igual ao pré-check" if not lines else f"{len(lines)} equipamento(s) com diferença")
            await automation.send_alert(db, title, "\n".join(lines)[:3500] or "Vizinhos, sessões e rotas como antes da manutenção.",
                                        push_url=f"/maint?id={wid}", push_tag=f"maint-{wid}")
        except Exception as e:
            logger.warning(f"aviso do pós-check: {e}")


async def _maint_loop():
    """Avisa início/fim das janelas e roda o pré-check no início e o pós-check no fim (se ligado)."""
    await asyncio.sleep(40)
    while True:
        try:
            now = time.time()
            async for w in db.maint_windows.find({"status": {"$ne": "cancelled"}, "start": {"$gte": datetime.fromtimestamp(now - 8 * 86400, timezone.utc).isoformat()}}, {"_id": 0}):
                st = maintmod.window_state(w, now)
                ev = w.get("events") or {}
                ch = w.get("checks") or {}
                if st in ("active", "done") and not ev.get("started"):
                    await db.maint_windows.update_one({"id": w["id"]}, {"$set": {"events.started": datetime.now(timezone.utc).isoformat()}})
                    if w.get("auto_checks") and not ch.get("pre") and st == "active":
                        _bg(_maint_run_check(w["id"], "pre", "BastiON (automático)"))
                    if w.get("notify", True) and st == "active":
                        await automation.send_alert(db, f"🛠️ Manutenção iniciada — {w['name']}",
                                                    f"Até {datetime.fromisoformat(w['end']).astimezone(slamod.BR).strftime('%d/%m %H:%M')}. "
                                                    + ("Alarmes do escopo silenciados." if w.get("silence", True) else ""),
                                                    push_url=f"/maint?id={w['id']}", push_tag=f"maint-{w['id']}")
                if st == "done" and not ev.get("ended"):
                    await db.maint_windows.update_one({"id": w["id"]}, {"$set": {"events.ended": datetime.now(timezone.utc).isoformat()}})
                    maintmod.invalidate()
                    if w.get("auto_checks") and (ch.get("pre") or {}).get("status") == "done" and not ch.get("post") \
                            and now - (maintmod._ts(w["end"]) or now) < 6 * 3600:
                        _bg(_maint_run_check(w["id"], "post", "BastiON (automático)"))
                    elif w.get("notify", True):
                        await automation.send_alert(db, f"✅ Manutenção encerrada — {w['name']}", "Alarmes voltaram ao normal.",
                                                    push_url=f"/maint?id={w['id']}", push_tag=f"maint-{w['id']}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"janelas de manutenção: {e}")
        await asyncio.sleep(30)


# ---------- Monitor BGP (sessões + prefixos próprios no RIPE RIS) ----------
_bgp_state = {"sess_at": 0.0, "cli_at": {}, "pfx_at": 0.0, "running": False, "pfx_running": False}


async def _bgp_cfg() -> dict:
    doc = await db.config.find_one({"key": "bgpmon"}, {"_id": 0}) or {}
    return {**bgpmod.DEFAULTS, **{k: v for k, v in doc.items() if k in bgpmod.DEFAULTS}}


async def _bgp_poll_router(r: dict, cfg: dict, force_cli: bool = False) -> dict:
    dev = await db.devices.find_one({"id": r["device_id"]}, {"_id": 0})
    if not dev:
        return {"error": "equipamento removido"}
    peers: Dict[str, dict] = {}
    err = ""
    mode = r.get("mode") or "snmp"
    if mode == "snmp":
        try:
            cli = await _snmp_client(dev)
            walk = await cli.walk(bgpmod.BGP_PEER, limit=50000)
            peers = bgpmod.peers_from_walk(walk)
            if (dev.get("device_type") or "") == "cisco":
                try:
                    for ip, n in bgpmod.cisco_prefixes(await cli.walk(bgpmod.CISCO_ACCEPTED, limit=20000)).items():
                        if ip in peers:
                            peers[ip]["prefixes"] = n
                except Exception:
                    pass
        except Exception as e:
            err = f"SNMP: {str(e)[:150]}"
    now = time.time()
    due_cli = mode == "cli" or (r.get("cli_prefixes") and now - _bgp_state["cli_at"].get(dev["id"], 0) >= cfg["cli_interval_min"] * 60)
    if due_cli or force_cli:
        _bgp_state["cli_at"][dev["id"]] = now
        c = None
        try:
            c = await asyncio.wait_for(_connect_device(dev), 40)
            sh = await c.shell()
            text = ""
            for cmd in bgpmod.CLI_SUMMARY.get(dev.get("device_type") or "other", bgpmod.CLI_SUMMARY["other"]):
                text += "\n" + await asyncio.wait_for(sh.run(cmd, timeout=60, idle=2.0), 90)
            await sh.close()
            parsed = bgpmod.parse_cli_summary(text)
            for ip, p in parsed.items():
                cur = peers.setdefault(ip, {"peer": ip})
                if mode == "cli" or "state" not in cur:
                    cur.update({k: v for k, v in p.items() if v is not None})
                elif p.get("prefixes") is not None:
                    cur["prefixes"] = p["prefixes"]
            if mode == "cli" and not parsed:
                err = "CLI: não reconheci o resumo do BGP na saída"
        except Exception as e:
            err = (err + " · " if err else "") + f"CLI: {str(e)[:150]}"
        finally:
            if c:
                try:
                    await c.close()
                except Exception:
                    pass
    if err and not peers:
        await db.config.update_one({"key": "bgpmon_status"}, {"$set": {f"routers.{dev['id']}": {"error": err, "at": datetime.now(timezone.utc).isoformat()}}}, upsert=True)
        return {"error": err}
    await _bgp_store(dev, peers, cfg)
    await db.config.update_one({"key": "bgpmon_status"}, {"$set": {f"routers.{dev['id']}": {"error": err, "at": datetime.now(timezone.utc).isoformat(), "peers": len(peers)}}}, upsert=True)
    return {"peers": len(peers), "error": err}


async def _bgp_store(dev: dict, peers: Dict[str, dict], cfg: dict):
    now = datetime.now(timezone.utc)
    nowi = now.isoformat()
    names = cfg.get("peer_names") or {}
    seen = set()
    for ip, p in peers.items():
        key = f"{dev['id']}|{ip}"
        seen.add(key)
        cur = await db.bgp_peers.find_one({"key": key}, {"_id": 0}) or {}
        st = p.get("state") or cur.get("state") or "unknown"
        upd = {"key": key, "device_id": dev["id"], "device_name": dev["name"], "peer": ip, "state": st, "seen_at": nowi, "missing": 0}
        for k in ("asn", "admin", "established_s", "last_error"):
            if p.get(k) is not None:
                upd[k] = p[k]
        flaps = [t for t in cur.get("flaps") or [] if (now - datetime.fromisoformat(t)).total_seconds() < 86400]
        changed = cur and cur.get("state") != st
        if changed or not cur:
            upd["since"] = nowi
        if changed:
            flaps.append(nowi)
            await db.bgp_events.insert_one({"at": nowi, "key": key, "device_id": dev["id"], "peer": ip, "from": cur.get("state"), "to": st})
        upd["flaps"] = flaps[-50:]
        label = names.get(key) or names.get(ip) or (f"AS{upd.get('asn') or cur.get('asn')}" if (upd.get("asn") or cur.get("asn")) else "")
        # prefixos: base = média móvel enquanto está normal
        pf = p.get("prefixes")
        drop = False
        if pf is not None:
            upd["prefixes"] = pf
            base = cur.get("prefixes_base")
            if st == "established" and base and pf < base * (1 - cfg["prefix_drop_pct"] / 100.0):
                drop = True
            elif st == "established":
                upd["prefixes_base"] = round(pf if not base else base * 0.8 + pf * 0.2, 1)
        await db.bgp_peers.update_one({"key": key}, {"$set": upd}, upsert=True)
        if not cfg.get("alert_sessions", True) or not cur:
            continue
        sev_down = "critical" if (upd.get("prefixes_base") or cur.get("prefixes_base") or 0) > 100000 else "high"
        if changed and st != "established" and cur.get("state") == "established" and upd.get("admin", cur.get("admin")) != "stop":
            # o syslog já abriu incidente desse vizinho? não duplica
            dup = await db.incidents.find_one({"status": {"$in": ["open", "ack"]}, "device_id": dev["id"], "key": {"$regex": "^log:"},
                                               "title": {"$regex": re.escape(ip)}}, {"_id": 0, "id": 1})
            if not dup:
                await inc_center.ingest("bgp", {"key": f"bgp:sess:{key}", "status": "problem", "severity": sev_down, "device_id": dev["id"],
                                                "host": dev.get("host", ""), "host_name": dev["name"],
                                                "title": f"Sessão BGP {ip}{f' ({label})' if label else ''} caiu — {dev['name']}",
                                                "description": f"Estado: {st}" + (f" · último erro {p.get('last_error')}" if p.get("last_error") else "")})
        elif changed and st == "established":
            await inc_center.ingest("bgp", {"key": f"bgp:sess:{key}", "status": "ok", "severity": "high", "title": ""})
        pkey = f"bgp:pfx:{key}"
        if drop:
            await inc_center.ingest("bgp", {"key": pkey, "status": "problem", "severity": "high", "device_id": dev["id"], "host": dev.get("host", ""),
                                            "host_name": dev["name"], "title": f"Prefixos de {ip}{f' ({label})' if label else ''} caíram para {pf} — {dev['name']}",
                                            "description": f"Normal ~{int(cur.get('prefixes_base') or 0)}; limite de queda {cfg['prefix_drop_pct']}%."})
        elif pf is not None and st == "established" and cur.get("prefixes_base"):
            await inc_center.ingest("bgp", {"key": pkey, "status": "ok", "severity": "high", "title": ""})
    # vizinhos que sumiram da tabela
    async for old in db.bgp_peers.find({"device_id": dev["id"]}, {"_id": 0, "key": 1, "missing": 1}):
        if old["key"] not in seen:
            await db.bgp_peers.update_one({"key": old["key"]}, {"$set": {"missing": (old.get("missing") or 0) + 1}})


async def _ripestat(path: str, params: dict) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "BastiON-NOC bgp-monitor"}) as c:
        r = await c.get(f"{bgpmod.RIPESTAT}/{path}/data.json", params={**params, "sourceapp": "bastion-noc"})
        r.raise_for_status()
        return (r.json() or {}).get("data")


async def _bgp_check_prefixes(cfg: dict) -> int:
    n = 0
    for pc in cfg.get("prefixes") or []:
        doc = {"prefix": pc["prefix"], "checked_at": datetime.now(timezone.utc).isoformat(), "expected": pc["origins"],
               "upstreams": pc.get("upstreams") or [], "note": pc.get("note", ""), "error": ""}
        try:
            rs = await _ripestat("routing-status", {"resource": pc["prefix"]})
            rpki_st = {}
            for asn in pc["origins"][:5]:
                try:
                    rv = await _ripestat("rpki-validation", {"resource": f"AS{asn}", "prefix": pc["prefix"]})
                    rpki_st[str(asn)] = (rv or {}).get("status") or ""
                except Exception:
                    rpki_st[str(asn)] = ""
            lg = None
            if pc.get("upstreams"):
                try:
                    lg = await _ripestat("looking-glass", {"resource": pc["prefix"]})
                except Exception:
                    lg = None
            doc.update(bgpmod.evaluate_prefix(pc, rs, lg, rpki_st, float(cfg.get("visibility_min_pct") or 50)))
        except Exception as e:
            doc["error"] = f"RIPEstat: {str(e)[:150]}"
        old = await db.bgp_prefixes.find_one({"prefix": pc["prefix"]}, {"_id": 0}) or {}
        await db.bgp_prefixes.update_one({"prefix": pc["prefix"]}, {"$set": doc}, upsert=True)
        n += 1
        if doc["error"] or not cfg.get("alert_prefixes", True):
            continue
        now_codes = {f["code"]: f for f in doc.get("findings") or []}
        for code, f in now_codes.items():
            await inc_center.ingest("bgp", {"key": f"bgp:{code}:{pc['prefix']}", "status": "problem", "severity": f["level"],
                                            "host": pc["prefix"].split("/")[0], "host_name": pc["prefix"],
                                            "title": f"{pc['prefix']}: {f['text']}"[:250],
                                            "description": "Dados públicos do RIPE RIS (RIPEstat). Origem esperada: AS" + ", AS".join(map(str, pc["origins"]))})
        for f in old.get("findings") or []:
            if f["code"] not in now_codes:
                await inc_center.ingest("bgp", {"key": f"bgp:{f['code']}:{pc['prefix']}", "status": "ok", "severity": "high", "title": ""})
    return n


async def _bgp_poll_all(cfg: dict):
    if _bgp_state["running"]:
        return
    _bgp_state["running"] = True
    try:
        sem = asyncio.Semaphore(6)

        async def one(r):
            async with sem:
                try:
                    await _bgp_poll_router(r, cfg)
                except Exception as e:
                    logger.warning(f"bgp {r.get('device_id')}: {e}")
        await asyncio.gather(*[one(r) for r in cfg.get("routers") or []])
    finally:
        _bgp_state["running"] = False
        _bgp_state["sess_at"] = time.time()


async def _bgp_loop():
    await asyncio.sleep(70)
    while True:
        try:
            cfg = await _bgp_cfg()
            now = time.time()
            if cfg.get("routers") and now - _bgp_state["sess_at"] >= cfg["interval_min"] * 60:
                await _bgp_poll_all(cfg)
                if int(now) % 86400 < 60 * cfg["interval_min"]:      # uma vez por dia: histórico de 90 dias
                    await db.bgp_events.delete_many({"at": {"$lt": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()}})
            if cfg.get("prefixes") and now - _bgp_state["pfx_at"] >= cfg["ripe_interval_min"] * 60 and not _bgp_state["pfx_running"]:
                _bgp_state["pfx_running"] = True
                try:
                    await _bgp_check_prefixes(cfg)
                finally:
                    _bgp_state["pfx_running"] = False
                    _bgp_state["pfx_at"] = time.time()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"monitor BGP: {e}")
        await asyncio.sleep(30)


@api.get("/bgpmon")
async def bgpmon_view(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cfg = await _bgp_cfg()
    mine = {d["id"] for d in await db.devices.find({} if user.get("role") == "admin" else _scope(user), {"_id": 0, "id": 1}).to_list(10000)}
    peers = [p for p in await db.bgp_peers.find({}, {"_id": 0}).sort([("device_name", 1), ("peer", 1)]).to_list(10000) if p["device_id"] in mine]
    names = cfg.get("peer_names") or {}
    for p in peers:
        p["name"] = names.get(p["key"]) or names.get(p["peer"]) or ""
        p["flaps_24h"] = len(p.get("flaps") or [])
    st = await db.config.find_one({"key": "bgpmon_status"}, {"_id": 0}) or {}
    out = {"peers": peers, "prefixes": await db.bgp_prefixes.find({}, {"_id": 0}).sort("prefix", 1).to_list(500),
           "routers_status": st.get("routers") or {}}
    if user.get("role") == "admin":
        out["settings"] = cfg
    return out


class BgpRouter(BaseModel):
    device_id: str
    mode: str = "snmp"
    cli_prefixes: bool = False


class BgpPrefix(BaseModel):
    prefix: str
    origins: List[str] = []
    upstreams: List[str] = []
    note: str = ""


class BgpSettingsIn(BaseModel):
    routers: List[BgpRouter] = []
    interval_min: int = 5
    cli_interval_min: int = 30
    prefix_drop_pct: int = 50
    alert_sessions: bool = True
    prefixes: List[BgpPrefix] = []
    ripe_interval_min: int = 15
    visibility_min_pct: int = 50
    alert_prefixes: bool = True


@api.put("/bgpmon/settings")
async def bgpmon_settings(body: BgpSettingsIn, _: dict = Depends(require_admin)):
    ids = {d["id"] async for d in db.devices.find({}, {"_id": 0, "id": 1})}
    routers = [{"device_id": r.device_id, "mode": r.mode if r.mode in ("snmp", "cli") else "snmp", "cli_prefixes": r.cli_prefixes}
               for r in body.routers if r.device_id in ids][:200]
    try:
        pfx = bgpmod.clean_prefixes([x.model_dump() for x in body.prefixes])
    except ValueError as e:
        raise HTTPException(400, str(e))
    d = {"routers": routers, "interval_min": min(60, max(1, body.interval_min)), "cli_interval_min": min(1440, max(10, body.cli_interval_min)),
         "prefix_drop_pct": min(99, max(5, body.prefix_drop_pct)), "alert_sessions": body.alert_sessions, "prefixes": pfx,
         "ripe_interval_min": min(1440, max(5, body.ripe_interval_min)), "visibility_min_pct": min(100, max(1, body.visibility_min_pct)),
         "alert_prefixes": body.alert_prefixes}
    await db.config.update_one({"key": "bgpmon"}, {"$set": d}, upsert=True)
    keep = [p["prefix"] for p in pfx]
    await db.bgp_prefixes.delete_many({"prefix": {"$nin": keep}})
    keep_dev = [r["device_id"] for r in routers]
    await db.bgp_peers.delete_many({"device_id": {"$nin": keep_dev}})
    _bgp_state["sess_at"] = 0
    _bgp_state["pfx_at"] = 0
    return await _bgp_cfg()


class BgpPeerName(BaseModel):
    key: str
    name: str = ""


@api.put("/bgpmon/peer-name")
async def bgpmon_peer_name(body: BgpPeerName, _: dict = Depends(require_admin)):
    cfg = await _bgp_cfg()
    names = dict(cfg.get("peer_names") or {})
    if body.name.strip():
        names[body.key] = body.name.strip()[:60]
    else:
        names.pop(body.key, None)
    await db.config.update_one({"key": "bgpmon"}, {"$set": {"peer_names": names}}, upsert=True)
    return {"ok": True}


@api.post("/bgpmon/refresh")
async def bgpmon_refresh(what: str = "all", _: dict = Depends(require_admin)):
    cfg = await _bgp_cfg()
    if what in ("all", "sessions"):
        for r in cfg.get("routers") or []:
            r["cli_prefixes"] = r.get("cli_prefixes")
        _bg(_bgp_poll_all(cfg))
    if what in ("all", "prefixes") and not _bgp_state["pfx_running"]:
        _bgp_state["pfx_at"] = 0
    return {"ok": True}


@api.get("/bgpmon/events")
async def bgpmon_events(key: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return await db.bgp_events.find({"key": key}, {"_id": 0}).sort("at", -1).to_list(200)


# ---------- GPON: sinal, estado e ocupação de todas as ONUs ----------
_gpon_rt = {"state_at": {}, "power_at": {}, "plat": {}, "running": set(), "inv": {}, "fhdir": {}}


async def _gpon_cfg() -> dict:
    doc = await db.config.find_one({"key": "gpon"}, {"_id": 0}) or {}
    return {**gponmod.DEFAULTS, **{k: v for k, v in doc.items() if k in gponmod.DEFAULTS}}


def _gpon_hw_inventory(content: str) -> dict:
    out = {}
    for (f, sl, pt, o), d in olthw.parse_inventory(content).items():
        sn = d.get("sn") or ""
        out[(sl, pt, o)] = {"sn": olthw.sn_vendor(sn) if re.fullmatch(r"[0-9A-F]{16}", sn) else sn, "name": d.get("description") or "",
                            "description": d.get("description") or "", "pppoe": d.get("pppoe") or [], "frame": f}
    return out


async def _gpon_inventory(olt_id: str, hw: bool = False) -> dict:
    b = await db.backups.find_one({"device_id": olt_id, "ok": {"$ne": False}}, {"_id": 0, "id": 1, "content": 1, "created_at": 1}, sort=[("created_at", -1)])
    if not b:
        return {}
    c = _gpon_rt["inv"].get(olt_id)
    if c and c[0] == b["id"]:
        return c[1]
    inv = _gpon_hw_inventory(b.get("content") or "") if hw else gponmod.parse_inventory(b.get("content") or "")
    _gpon_rt["inv"][olt_id] = (b["id"], inv, b.get("created_at"))
    return inv


async def _gpon_read_hw(dev: dict, j: dict, power: bool, frame_of: dict):
    """Huawei: `display ont info 0 all` (estado de todas as ONTs); `display ont info summary F/S/P` nas PONs com ONT off
    (causa da queda) e, no ciclo de sinal, em todas (Rx de cada ONT)."""
    async with _HwSess(dev, j) as s:
        info = olthw.parse_ont_info(await s.run("display ont info 0 all", timeout=300))
        if not info:
            brd = olthw.parse_board(await s.run("display board 0", timeout=60))
            if not brd:
                raise RuntimeError("não achei ONTs nem placas PON (display ont info 0 all / display board 0)")
        allp = sorted({(r["frame"], r["slot"], r["port"]) for r in info})
        want = allp if power else sorted({(r["frame"], r["slot"], r["port"]) for r in info if r["off"]})
        summ = {}
        for f, sl, pt in want:
            for r in olthw.parse_summary(await s.run(f"display ont info summary {f}/{sl}/{pt}", timeout=120)):
                summ[(sl, pt, r["ont"])] = r
    rows, rx = [], {}
    for r in info:
        k = (r["slot"], r["port"], r["ont"])
        frame_of[(r["slot"], r["port"])] = r["frame"]
        d = summ.get(k) or {}
        phase = "working" if not r["off"] else (d.get("cause") or "offline")
        rows.append({"slot": k[0], "pon": k[1], "onu": k[2], "frame": r["frame"], "admin": r["control"], "phase": phase, "off": r["off"],
                     "sn": olthw.sn_vendor(r["sn"]), "description": r.get("description") or d.get("description") or "",
                     "name": r.get("description") or "", "type": d.get("type") if d.get("type") not in (None, "-") else ""})
        if d.get("rx") is not None and not r["off"]:
            rx[k] = d["rx"]
    return rows, sorted({(sl, pt) for _, sl, pt in allp}), rx, "huawei"


async def _gpon_cycle(olt_id: str, cfg: dict, power: bool):
    if olt_id in _gpon_rt["running"]:
        return
    _gpon_rt["running"].add(olt_id)
    now = datetime.now(timezone.utc)
    nowi = now.isoformat()
    st_key = f"olts.{olt_id}"
    try:
        dev = await db.devices.find_one({"id": olt_id}, {"_id": 0})
        if not dev:
            return
        hw = (dev.get("device_type") or "") == "huawei"
        inv = await _gpon_inventory(olt_id, hw)
        j: dict = {}
        frame_of: Dict[tuple, int] = {}
        fh = (dev.get("device_type") or "") == "fiberhome"
        if hw:
            rows, pons, rx, plat = await _gpon_read_hw(dev, j, power, frame_of)
        elif fh:
            rows, pons, rx, plat = await _gpon_read_fh(dev, j, power)
        else:
            cli = await asyncio.wait_for(_connect_device(dev), 45)
            try:
                raw = await cli.shell()
                sess = _OltSess(raw, j)
                try:
                    out = await sess.run("show gpon onu state", timeout=180)
                    rows = [] if _olt_err(out, "show gpon onu state") else oltgen.parse_onu_state(out)
                    pons = sorted({(r["slot"], r["pon"]) for r in rows})
                    if not rows:
                        cards = oltgen.parse_show_card(await sess.run("show card", timeout=60))
                        pons = [(c["slot"], p) for c in cards["pon"] for p in range(1, c["ports"] + 1)]
                    if not pons:
                        raise RuntimeError("não achei PONs (show gpon onu state / show card)")
                    plat = _gpon_rt["plat"].get(olt_id) or await _olt_detect_plat(sess, *pons[0])
                    _gpon_rt["plat"][olt_id] = plat
                    if not rows:
                        for sl, pt in pons:
                            rows += oltgen.parse_onu_state(await sess.run(f"show gpon onu state {oltgen.pon_if(plat, sl, pt)}", timeout=90))
                    rx: Dict[tuple, float] = {}
                    if power:
                        for sl, pt in pons:
                            rx.update(gponmod.parse_power(await sess.run(f"show pon power onu-rx {oltgen.pon_if(plat, sl, pt)}", timeout=90)))
                finally:
                    try:
                        await raw.close()
                    except Exception:
                        pass
            finally:
                await cli.close()
        prev = {(o["slot"], o["pon"], o["onu"]): o async for o in db.gpon_onus.find({"olt_id": olt_id}, {"_id": 0})}
        from pymongo import UpdateOne
        ops, by_pon, newly = [], {}, {}
        seen = set()
        for r in rows:
            k = (r["slot"], r["pon"], r["onu"])
            seen.add(k)
            old = prev.get(k) or {}
            info = inv.get(k) or {}
            doc = {"key": f"{olt_id}|{k[0]}/{k[1]}:{k[2]}", "olt_id": olt_id, "olt_name": dev["name"], "slot": k[0], "pon": k[1], "onu": k[2],
                   "admin": r["admin"], "phase": r["phase"], "off": r["off"], "state_at": nowi,
                   "sn": info.get("sn") or old.get("sn"), "type": info.get("type") or old.get("type"),
                   "name": info.get("name") or old.get("name"), "description": info.get("description") or old.get("description"),
                   "pppoe": info.get("pppoe") or old.get("pppoe") or [], "platform": plat}
            if hw or fh:
                if hw:
                    doc["frame"] = r.get("frame", 0)
                for fk in ("sn", "type", "name", "description"):
                    if r.get(fk) and not doc.get(fk):
                        doc[fk] = r[fk]
            if k in rx:
                doc["rx"], doc["rx_at"] = rx[k], nowi
            elif power and r["off"]:
                doc["rx"] = None
            if old and old.get("off") != r["off"]:
                doc["last_change"] = nowi
                if r["off"]:
                    doc["down_since"] = nowi
                    newly[(k[0], k[1])] = newly.get((k[0], k[1]), 0) + 1
                else:
                    doc["down_since"] = None
            elif not old:
                doc["last_change"] = nowi
                doc["down_since"] = nowi if r["off"] else None
            ops.append(UpdateOne({"key": doc["key"]}, {"$set": doc}, upsert=True))
            merged = {**old, **doc}
            by_pon.setdefault((k[0], k[1]), []).append(merged)
        if ops:
            await db.gpon_onus.bulk_write(ops, ordered=False)
        gone = [prev[k]["key"] for k in prev if k not in seen]
        if gone:
            await db.gpon_onus.delete_many({"key": {"$in": gone}})
        for (sl, pt) in pons:
            onus = by_pon.get((sl, pt), [])
            sm = gponmod.pon_summary(onus, float(cfg["rx_bad"]), int(cfg["pon_max"]))
            pkey = f"{olt_id}|{sl}/{pt}"
            oldp = await db.gpon_pons.find_one({"key": pkey}, {"_id": 0}) or {}
            pname = f"{frame_of.get((sl, pt), 0)}/{sl}/{pt}" if hw else f"{sl}/{pt}" if fh else f"1/{sl}/{pt}"
            upd = {"key": pkey, "olt_id": olt_id, "olt_name": dev["name"], "slot": sl, "pon": pt, "at": nowi, **sm, "pon_name": pname,
                   "if_name": f"gpon {pname}" if hw else f"slot {sl} pon {pt}" if fh else oltgen.pon_if(plat, sl, pt)}
            if sm["rx_median"] is None and not power:
                upd["rx_median"], upd["rx_min"], upd["rx_bad"], upd["rx_n"] = oldp.get("rx_median"), oldp.get("rx_min"), oldp.get("rx_bad", 0), oldp.get("rx_n", 0)
            base = oldp.get("rx_base")
            degraded = bool(power and base is not None and sm["rx_median"] is not None and sm["rx_n"] >= 5
                            and sm["rx_median"] < base - float(cfg["rx_drop_db"]))
            if power and sm["rx_median"] is not None and sm["rx_n"] >= 3 and not degraded:
                upd["rx_base"] = round(sm["rx_median"] if base is None else base * 0.8 + sm["rx_median"] * 0.2, 2)
            mass = gponmod.mass_event(oldp.get("offline", sm["offline"]), sm["offline"], sm["total"], newly.get((sl, pt), 0), cfg) if oldp else False
            ev_open = oldp.get("mass_open")
            if mass and not ev_open:
                upd["mass_open"] = {"at": nowi, "newly": newly.get((sl, pt), 0), "offline": sm["offline"]}
            elif ev_open and gponmod.recovered(sm["offline"], sm["total"], ev_open.get("newly", 0), cfg):
                upd["mass_open"] = None
            upd["degraded"] = degraded
            await db.gpon_pons.update_one({"key": pkey}, {"$set": upd}, upsert=True)
            await db.gpon_samples.insert_one({"key": pkey, "at": now, "online": sm["online"], "offline": sm["offline"], "los": sm["los"],
                                              "rx_median": upd.get("rx_median")})
            if not cfg.get("alert", True):
                continue
            where = f"PON {pname} — {dev['name']}"
            if mass and not ev_open:
                dup = await db.incidents.find_one({"status": {"$in": ["open", "ack"]}, "device_id": olt_id, "key": {"$regex": "^log:"},
                                                   "title": {"$regex": re.escape(f"{sl}/{pt}")}}, {"_id": 0, "id": 1})
                if not dup:
                    await inc_center.ingest("gpon", {"key": f"gpon:mass:{pkey}", "status": "problem", "severity": "critical", "device_id": olt_id,
                                                     "host": dev.get("host", ""), "host_name": dev["name"],
                                                     "title": f"{newly.get((sl, pt), 0) or sm['offline']} ONUs caíram na {where} ({sm['los']} em LOS) — possível fibra rompida",
                                                     "description": f"{sm['offline']} de {sm['total']} ONUs fora; {sm['dying_gasp']} por falta de energia (dying gasp)."})
            elif ev_open and upd.get("mass_open", ev_open) is None:
                await inc_center.ingest("gpon", {"key": f"gpon:mass:{pkey}", "status": "ok", "severity": "critical", "title": ""})
            if degraded and not oldp.get("degraded"):
                await inc_center.ingest("gpon", {"key": f"gpon:rx:{pkey}", "status": "problem", "severity": "high", "device_id": olt_id,
                                                 "host": dev.get("host", ""), "host_name": dev["name"],
                                                 "title": f"Sinal degradado na {where}: mediana {sm['rx_median']} dBm (normal {base} dBm)",
                                                 "description": f"{sm['rx_bad']} ONU(s) abaixo de {cfg['rx_bad']} dBm. Fusão, conector ou splitter com problema?"})
            elif power and not degraded and oldp.get("degraded"):
                await inc_center.ingest("gpon", {"key": f"gpon:rx:{pkey}", "status": "ok", "severity": "high", "title": ""})
        await db.gpon_pons.delete_many({"olt_id": olt_id, "key": {"$nin": [f"{olt_id}|{a}/{b}" for a, b in pons]}})
        await db.config.update_one({"key": "gpon_status"}, {"$set": {st_key: {"at": nowi, "error": "", "onus": len(rows), "pons": len(pons),
                                                                             "platform": plat, "power_at": nowi if power else None,
                                                                             "inventory": len(inv)}}}, upsert=True)
    except Exception as e:
        await db.config.update_one({"key": "gpon_status"}, {"$set": {f"{st_key}.error": str(e)[:200] or e.__class__.__name__, f"{st_key}.error_at": nowi}}, upsert=True)
    finally:
        _gpon_rt["running"].discard(olt_id)


async def _gpon_loop():
    await asyncio.sleep(90)
    tick = 0
    while True:
        try:
            cfg = await _gpon_cfg()
            now = time.time()
            for oid in cfg.get("olts") or []:
                pw = now - _gpon_rt["power_at"].get(oid, 0) >= cfg["power_interval_min"] * 60
                if pw or now - _gpon_rt["state_at"].get(oid, 0) >= cfg["state_interval_min"] * 60:
                    _gpon_rt["state_at"][oid] = now
                    if pw:
                        _gpon_rt["power_at"][oid] = now
                    _bg(_gpon_cycle(oid, cfg, pw))
            tick += 1
            if tick % 120 == 1:
                await db.gpon_samples.delete_many({"at": {"$lt": datetime.now(timezone.utc) - timedelta(days=30)}})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"GPON: {e}")
        await asyncio.sleep(30)


async def _gpon_olt_ids(user: dict) -> List[str]:
    cfg = await _gpon_cfg()
    ids = cfg.get("olts") or []
    if user.get("role") == "admin":
        return ids
    mine = {d["id"] async for d in db.devices.find({"id": {"$in": ids}, **_scope(user)}, {"_id": 0, "id": 1})}
    return [i for i in ids if i in mine]


@api.get("/gpon/overview")
async def gpon_overview(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    ids = await _gpon_olt_ids(user)
    st = (await db.config.find_one({"key": "gpon_status"}, {"_id": 0}) or {}).get("olts") or {}
    names = {d["id"]: d["name"] async for d in db.devices.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "name": 1})}
    pons = await db.gpon_pons.find({"olt_id": {"$in": ids}}, {"_id": 0}).sort([("slot", 1), ("pon", 1)]).to_list(5000)
    out = []
    for oid in ids:
        ps = [p for p in pons if p["olt_id"] == oid]
        out.append({"id": oid, "name": names.get(oid, "?"), "status": st.get(oid) or {}, "pons": ps,
                    "total": sum(p["total"] for p in ps), "online": sum(p["online"] for p in ps), "offline": sum(p["offline"] for p in ps),
                    "rx_bad": sum(p.get("rx_bad") or 0 for p in ps), "alarms": sum(1 for p in ps if p.get("mass_open") or p.get("degraded"))})
    cfg = await _gpon_cfg()
    return {"olts": out, "rx_bad_limit": cfg["rx_bad"], "pon_max": cfg["pon_max"], **({"settings": cfg} if user.get("role") == "admin" else {})}


@api.get("/gpon/pon")
async def gpon_pon(olt_id: str, slot: int, pon: int, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    if olt_id not in await _gpon_olt_ids(user):
        raise HTTPException(404, "OLT não encontrada")
    onus = await db.gpon_onus.find({"olt_id": olt_id, "slot": slot, "pon": pon}, {"_id": 0}).sort("onu", 1).to_list(1000)
    hist = await db.gpon_samples.find({"key": f"{olt_id}|{slot}/{pon}", "at": {"$gte": datetime.now(timezone.utc) - timedelta(days=7)}},
                                      {"_id": 0, "key": 0}).sort("at", 1).to_list(5000)
    for h in hist:
        h["at"] = (h["at"] if h["at"].tzinfo else h["at"].replace(tzinfo=timezone.utc)).isoformat()
    return {"onus": onus, "history": hist, "pon": await db.gpon_pons.find_one({"key": f"{olt_id}|{slot}/{pon}"}, {"_id": 0})}


@api.get("/gpon/search")
async def gpon_search(q: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    q = q.strip()
    if len(q) < 3:
        raise HTTPException(400, "Digite pelo menos 3 caracteres (serial, nome, descrição ou usuário PPPoE)")
    ids = await _gpon_olt_ids(user)
    rx = {"$regex": re.escape(q[:60]), "$options": "i"}
    ors = [{"sn": rx}, {"name": rx}, {"description": rx}, {"pppoe": rx}]
    sn = gponmod.search_terms(q)
    if len(sn) >= 6:
        ors.append({"sn": {"$regex": re.escape(sn)}})
    m = re.fullmatch(r"(\d+)/(\d+)(?::(\d+))?", q)
    if m:
        ors.append({"slot": int(m.group(1)), "pon": int(m.group(2)), **({"onu": int(m.group(3))} if m.group(3) else {})})
    return await db.gpon_onus.find({"olt_id": {"$in": ids}, "$or": ors}, {"_id": 0}).sort([("olt_name", 1), ("slot", 1), ("pon", 1), ("onu", 1)]).to_list(100)


class GponLiveIn(BaseModel):
    olt_id: str
    slot: int
    pon: int
    onu: int


@api.post("/gpon/onu/live")
async def gpon_onu_live(body: GponLiveIn, user: dict = Depends(get_current_user)):
    """Consulta a ONU agora: estado/última queda (detail-info) e atenuação (sinal nos dois sentidos)."""
    _no_viewer(user)
    if body.olt_id not in await _gpon_olt_ids(user):
        raise HTTPException(404, "OLT não encontrada")
    dev = await db.devices.find_one({"id": body.olt_id}, {"_id": 0})
    if (dev.get("device_type") or "") == "fiberhome":
        cmds = [f"show optic_module slot {body.slot} pon {body.pon} onu {body.onu}", f"show authorization slot {body.slot} pon {body.pon}"]
        async with _FhSess(dev) as fs:
            await fs.cd_onu(_gpon_rt["fhdir"].get(dev["id"], ""))
            att = await fs.run(cmds[0], timeout=60)
            det = await fs.run(cmds[1], timeout=60)
        a = next((x for x in oltfh.parse_auth(det, body.slot, body.pon) if x["onu"] == body.onu), {})
        await _olt_log(user, dev, commands=cmds, commands_count=len(cmds))
        return {"interface": f"slot {body.slot} pon {body.pon} onu {body.onu}",
                "detail": {"phase": a.get("state") or "?", "sn": a.get("sn") or "", "type": a.get("type") or "", "name": "",
                           "rx": oltfh.parse_optic(att)},
                "attenuation": att.strip()[-3000:], "detail_raw": det.strip()[-4000:]}
    if (dev.get("device_type") or "") == "huawei":
        o = await db.gpon_onus.find_one({"olt_id": body.olt_id, "slot": body.slot, "pon": body.pon, "onu": body.onu}, {"_id": 0, "frame": 1}) or {}
        f = o.get("frame", 0)
        cmds = [f"display ont info {f} {body.slot} {body.pon} {body.onu}", f"interface gpon {f}/{body.slot}",
                f"display ont optical-info {body.pon} {body.onu}", "quit"]
        async with _HwSess(dev) as hs:
            det = await hs.run(cmds[0], timeout=60)
            await hs.run(cmds[1], timeout=20)
            att = await hs.run(cmds[2], timeout=60)
            await hs.run(cmds[3], timeout=20)
        d = olthw.parse_detail(det)
        await _olt_log(user, dev, commands=cmds, commands_count=len(cmds))
        return {"interface": f"{f}/{body.slot}/{body.pon} ONT {body.onu}",
                "detail": {**d, "phase": d["run"], "name": d["description"], "sn": olthw.sn_vendor(d["sn"]) if d["sn"] else ""},
                "attenuation": att.strip()[-3000:], "detail_raw": det.strip()[-4000:]}
    plat = _gpon_rt["plat"].get(body.olt_id) or (await db.gpon_onus.find_one({"olt_id": body.olt_id}, {"_id": 0, "platform": 1}) or {}).get("platform") or "c300"
    ifn = oltgen.onu_if_name(plat, body.slot, body.pon, body.onu)
    cli = await _connect_device(dev)
    try:
        sh = await cli.shell()
        det = await sh.run(f"show gpon onu detail-info {ifn}", timeout=60)
        att = await sh.run(f"show pon power attenuation {ifn}", timeout=60)
        await sh.close()
    finally:
        await cli.close()
    d = oltgen.parse_onu_detail(det)
    await db.sessions.insert_one({"id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user["email"], "device_id": dev["id"],
                                  "device_name": dev["name"], "started_at": datetime.now(timezone.utc).isoformat(),
                                  "ended_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": 0, "kind": "batch",
                                  "commands": [f"show gpon onu detail-info {ifn}", f"show pon power attenuation {ifn}"], "commands_count": 2})
    return {"interface": ifn, "detail": d, "attenuation": att.strip()[-3000:], "detail_raw": det.strip()[-4000:]}


class GponSettingsIn(BaseModel):
    olts: List[str] = []
    state_interval_min: int = 15
    power_interval_min: int = 60
    mass_min: int = 8
    mass_pct: int = 50
    rx_bad: float = -27.0
    rx_drop_db: float = 3.0
    pon_max: int = 128
    alert: bool = True


@api.put("/gpon/settings")
async def gpon_settings(body: GponSettingsIn, _: dict = Depends(require_admin)):
    ids = {d["id"] async for d in db.devices.find({"id": {"$in": body.olts}}, {"_id": 0, "id": 1})}
    d = {"olts": [i for i in body.olts if i in ids][:50], "state_interval_min": min(240, max(5, body.state_interval_min)),
         "power_interval_min": min(1440, max(15, body.power_interval_min)), "mass_min": min(500, max(2, body.mass_min)),
         "mass_pct": min(100, max(5, body.mass_pct)), "rx_bad": max(-40.0, min(-10.0, body.rx_bad)),
         "rx_drop_db": max(0.5, min(20.0, body.rx_drop_db)), "pon_max": min(512, max(16, body.pon_max)), "alert": body.alert}
    await db.config.update_one({"key": "gpon"}, {"$set": d}, upsert=True)
    for col in (db.gpon_onus, db.gpon_pons):
        await col.delete_many({"olt_id": {"$nin": d["olts"]}})
    return await _gpon_cfg()


@api.post("/gpon/refresh")
async def gpon_refresh(_: dict = Depends(require_admin)):
    cfg = await _gpon_cfg()
    for oid in cfg.get("olts") or []:
        _gpon_rt["state_at"][oid] = _gpon_rt["power_at"][oid] = time.time()
        _bg(_gpon_cycle(oid, cfg, True))
    return {"ok": True, "olts": len(cfg.get("olts") or [])}


# ---------- Conformidade de configuração + firmware ----------
_comp_rt = {"running": False}


async def _comp_rules(enabled_only: bool = True) -> List[dict]:
    if not await db.config.find_one({"key": "compliance_seeded"}):
        if await db.compliance_rules.count_documents({}) == 0:
            for p in compmod.PRESETS:
                await db.compliance_rules.insert_one({**{k: v for k, v in p.items()}, "id": uuid.uuid4().hex[:16], "enabled": True,
                                                      "created_at": datetime.now(timezone.utc).isoformat()})
        await db.config.update_one({"key": "compliance_seeded"}, {"$set": {"key": "compliance_seeded", "ver": 1}}, upsert=True)
    q = {"enabled": True} if enabled_only else {}
    return await db.compliance_rules.find(q, {"_id": 0}).sort([("vendor", 1), ("name", 1)]).to_list(2000)


async def _comp_cfg() -> dict:
    doc = await db.config.find_one({"key": "compliance"}, {"_id": 0}) or {}
    return {"approved": doc.get("approved") or {}, "alert": doc.get("alert", True), "rules_ver": doc.get("rules_ver", 1)}


async def _comp_bump():
    await db.config.update_one({"key": "compliance"}, {"$inc": {"rules_ver": 1}}, upsert=True)


async def _comp_refresh(device_ids: Optional[List[str]] = None, force: bool = False) -> int:
    if _comp_rt["running"]:
        return 0
    _comp_rt["running"] = True
    n = 0
    try:
        cfg = await _comp_cfg()
        rules = await _comp_rules()
        by_id = {r["id"]: r for r in rules}
        q = {"id": {"$in": device_ids}} if device_ids else {}
        async for dev in db.devices.find(q, {"_id": 0, "id": 1, "name": 1, "device_type": 1, "host": 1, "owner_id": 1}):
            b = await db.backups.find_one({"device_id": dev["id"], "ok": {"$ne": False}, "content": {"$exists": True}},
                                          {"_id": 0, "id": 1, "created_at": 1}, sort=[("created_at", -1)])
            if not b:
                continue
            cur = await db.compliance_results.find_one({"device_id": dev["id"]}, {"_id": 0}) or {}
            if not force and cur.get("backup_id") == b["id"] and cur.get("rules_ver") == cfg["rules_ver"]:
                continue
            content = (await db.backups.find_one({"id": b["id"]}, {"_id": 0, "content": 1}) or {}).get("content") or ""
            vt = dev.get("device_type") or "other"
            mine = [r for r in rules if r["vendor"] in ("any", vt)]
            res = compmod.check(content, mine)
            fw = compmod.firmware(vt, content)
            doc = {"device_id": dev["id"], "device_name": dev["name"], "device_type": vt, "owner_id": dev.get("owner_id"),
                   "backup_id": b["id"], "backup_at": b.get("created_at"), "rules_ver": cfg["rules_ver"], "results": res,
                   "score": compmod.score(res, by_id), "failed": sum(1 for x in res if x["ok"] is False),
                   "failed_high": sum(1 for x in res if x["ok"] is False and by_id.get(x["rule_id"], {}).get("severity") == "high"),
                   "firmware": fw, "at": datetime.now(timezone.utc).isoformat()}
            await db.compliance_results.update_one({"device_id": dev["id"]}, {"$set": doc}, upsert=True)
            n += 1
            if not cfg.get("alert", True) or not cur.get("results"):
                continue
            old = {x["rule_id"]: x.get("ok") for x in cur["results"]}
            for x in res:
                r = by_id.get(x["rule_id"]) or {}
                if r.get("severity") != "high":
                    continue
                key = f"compliance:{dev['id']}:{x['rule_id']}"
                if x["ok"] is False and old.get(x["rule_id"]) is True:
                    await inc_center.ingest("compliance", {"key": key, "status": "problem", "severity": "medium", "device_id": dev["id"],
                                                           "host": dev.get("host", ""), "host_name": dev["name"],
                                                           "title": f"Fora do padrão: {r['name']} — {dev['name']}",
                                                           "description": f"Mudou no backup de {b.get('created_at', '')[:16]}." + (f"\nTrecho: {x['evidence']}" if x.get("evidence") else "")
                                                                          + (f"\nComo corrigir:\n{r['fix']}" if r.get("fix") else "")})
                elif x["ok"] is True and old.get(x["rule_id"]) is False:
                    await inc_center.ingest("compliance", {"key": key, "status": "ok", "severity": "medium", "title": ""})
    finally:
        _comp_rt["running"] = False
    return n


async def _comp_loop():
    await asyncio.sleep(150)
    while True:
        try:
            await _comp_refresh()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"conformidade: {e}")
        await asyncio.sleep(1800)


def _comp_fw_status(fw: dict, vt: str, approved: dict) -> str:
    std = (approved or {}).get(vt) or {}
    if not fw.get("version"):
        return "unknown"
    if std.get("min") and compmod.version_cmp(fw["version"], std["min"]) < 0:
        return "outdated"
    if std.get("min"):
        return "ok"
    return "nostd"


@api.get("/compliance/summary")
async def compliance_summary(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cfg = await _comp_cfg()
    rules = await _comp_rules(enabled_only=False)
    q = {} if user.get("role") == "admin" else {"owner_id": user["id"]}
    rows = await db.compliance_results.find(q, {"_id": 0}).sort("device_name", 1).to_list(10000)
    by_id = {r["id"]: r for r in rules}
    stats: Dict[str, list] = {}
    for d in rows:
        d["fw_status"] = _comp_fw_status(d.get("firmware") or {}, d["device_type"], cfg["approved"])
        d["fails"] = [{"rule_id": x["rule_id"], "name": by_id.get(x["rule_id"], {}).get("name", "?"), "severity": by_id.get(x["rule_id"], {}).get("severity")}
                      for x in d.get("results") or [] if x["ok"] is False]
        for x in d.get("results") or []:
            if x["ok"] is not None:
                stats.setdefault(x["rule_id"], [0, 0])
                stats[x["rule_id"]][0 if x["ok"] else 1] += 1
        d.pop("results", None)
    for r in rules:
        ok, bad = stats.get(r["id"], [0, 0])
        r["passed"], r["failed"] = ok, bad
    total_devs = await db.devices.count_documents({} if user.get("role") == "admin" else _scope(user))
    return {"devices": rows, "rules": rules, "approved": cfg["approved"], "alert": cfg["alert"], "vendors": compmod.VENDORS,
            "no_backup": max(0, total_devs - len(rows)), "running": _comp_rt["running"]}


@api.get("/compliance/device/{device_id}")
async def compliance_device(device_id: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    d = await db.compliance_results.find_one({"device_id": device_id, **({} if user.get("role") == "admin" else {"owner_id": user["id"]})}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Sem resultado para esse equipamento (precisa de um backup de configuração)")
    by_id = {r["id"]: r for r in await _comp_rules(enabled_only=False)}
    d["results"] = [{**x, "rule": {k: by_id.get(x["rule_id"], {}).get(k) for k in ("name", "kind", "severity", "fix", "why", "pattern")}} for x in d.get("results") or []]
    return d


class CompRuleIn(BaseModel):
    name: str
    vendor: str = "any"
    kind: str = "must"
    pattern: str
    severity: str = "medium"
    fix: str = ""
    why: str = ""
    enabled: bool = True


def _comp_clean(body: CompRuleIn) -> dict:
    try:
        return compmod.clean_rule(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@api.post("/compliance/rules")
async def compliance_rule_create(body: CompRuleIn, _: dict = Depends(require_admin)):
    r = _comp_clean(body)
    r.update(id=uuid.uuid4().hex[:16], created_at=datetime.now(timezone.utc).isoformat())
    await db.compliance_rules.insert_one(dict(r))
    await _comp_bump()
    _bg(_comp_refresh())
    return r


@api.put("/compliance/rules/{rid}")
async def compliance_rule_update(rid: str, body: CompRuleIn, _: dict = Depends(require_admin)):
    r = _comp_clean(body)
    res = await db.compliance_rules.update_one({"id": rid}, {"$set": r})
    if not res.matched_count:
        raise HTTPException(404, "Regra não encontrada")
    await _comp_bump()
    _bg(_comp_refresh())
    return {**r, "id": rid}


@api.delete("/compliance/rules/{rid}")
async def compliance_rule_delete(rid: str, _: dict = Depends(require_admin)):
    await db.compliance_rules.delete_one({"id": rid})
    await _comp_bump()
    _bg(_comp_refresh())
    return {"ok": True}


@api.post("/compliance/rules/presets")
async def compliance_rule_presets(_: dict = Depends(require_admin)):
    have = {r.get("preset") async for r in db.compliance_rules.find({}, {"_id": 0, "preset": 1})}
    n = 0
    for p in compmod.PRESETS:
        if p["preset"] not in have:
            await db.compliance_rules.insert_one({**p, "id": uuid.uuid4().hex[:16], "enabled": True, "created_at": datetime.now(timezone.utc).isoformat()})
            n += 1
    if n:
        await _comp_bump()
        _bg(_comp_refresh())
    return {"added": n}


class CompTestIn(BaseModel):
    pattern: str
    kind: str = "must"
    vendor: str = "any"


@api.post("/compliance/rules/test")
async def compliance_rule_test(body: CompTestIn, _: dict = Depends(require_admin)):
    """Roda a regra nos backups mais recentes (do fabricante escolhido) e diz quem passa e quem não."""
    try:
        r = compmod.clean_rule({"name": "teste", "pattern": body.pattern, "kind": body.kind, "vendor": body.vendor})
    except ValueError as e:
        raise HTTPException(400, str(e))
    q = {} if r["vendor"] == "any" else {"device_type": r["vendor"]}
    out = []
    for dev in await db.devices.find(q, {"_id": 0, "id": 1, "name": 1}).to_list(200):
        b = await db.backups.find_one({"device_id": dev["id"], "ok": {"$ne": False}, "content": {"$exists": True}}, {"_id": 0, "content": 1}, sort=[("created_at", -1)])
        if not b:
            continue
        try:
            x = (await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, compmod.check, b["content"], [r]), 5))[0]
        except asyncio.TimeoutError:
            raise HTTPException(400, "A regex demorou demais (backtracking); simplifique a expressão")
        out.append({"device": dev["name"], "ok": x["ok"], "evidence": x.get("evidence", "")})
    return {"total": len(out), "pass": sum(1 for x in out if x["ok"]), "results": out[:300]}


class CompStdIn(BaseModel):
    approved: Dict[str, Dict[str, str]] = {}
    alert: bool = True


@api.put("/compliance/settings")
async def compliance_settings(body: CompStdIn, _: dict = Depends(require_admin)):
    appr = {k: {"min": (v.get("min") or "").strip()[:60], "note": (v.get("note") or "").strip()[:120]}
            for k, v in body.approved.items() if k in compmod.VENDORS and (v.get("min") or "").strip()}
    await db.config.update_one({"key": "compliance"}, {"$set": {"approved": appr, "alert": body.alert}}, upsert=True)
    return {"approved": appr, "alert": body.alert}


@api.post("/compliance/refresh")
async def compliance_refresh(_: dict = Depends(require_admin)):
    _bg(_comp_refresh(force=True))
    return {"ok": True}


# ---------- Inventário / IPAM (+ NetBox) ----------
_ipam_rt = {"running": False}


async def _ipam_refresh(force: bool = False) -> int:
    if _ipam_rt["running"]:
        return 0
    _ipam_rt["running"] = True
    n = 0
    try:
        live = set()
        async for dev in db.devices.find({}, {"_id": 0, "id": 1, "name": 1, "device_type": 1, "owner_id": 1, "host": 1}):
            live.add(dev["id"])
            b = await db.backups.find_one({"device_id": dev["id"], "ok": {"$ne": False}, "content": {"$exists": True}},
                                          {"_id": 0, "id": 1, "created_at": 1}, sort=[("created_at", -1)])
            if not b:
                continue
            st = await db.ipam_dev_state.find_one({"device_id": dev["id"]}, {"_id": 0}) or {}
            if not force and st.get("backup_id") == b["id"]:
                continue
            content = (await db.backups.find_one({"id": b["id"]}, {"_id": 0, "content": 1}) or {}).get("content") or ""
            rows = ipammod.parse_addresses(dev.get("device_type") or "other", content)
            await db.ipam_addresses.delete_many({"device_id": dev["id"]})
            if rows:
                await db.ipam_addresses.insert_many([{**r, "device_id": dev["id"], "device_name": dev["name"], "owner_id": dev.get("owner_id"),
                                                      "source": "config", "backup_at": b.get("created_at")} for r in rows])
            await db.ipam_dev_state.update_one({"device_id": dev["id"]}, {"$set": {"device_id": dev["id"], "backup_id": b["id"], "count": len(rows),
                                                                                   "at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
            n += 1
        await db.ipam_addresses.delete_many({"device_id": {"$nin": list(live)}})
        await db.ipam_dev_state.delete_many({"device_id": {"$nin": list(live)}})
    finally:
        _ipam_rt["running"] = False
    return n


async def _ipam_loop():
    await asyncio.sleep(180)
    while True:
        try:
            await _ipam_refresh()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"IPAM: {e}")
        await asyncio.sleep(1800)


def _ipam_net(v):
    try:
        return ipaddress.ip_network(str(v).strip(), strict=False)
    except (ValueError, TypeError):
        return None


def _ipam_scope(user: dict) -> dict:
    return {} if user.get("role") == "admin" else {"owner_id": user["id"]}


def _ipam_ok_ip(v) -> bool:
    try:
        ipaddress.ip_address(str(v or ""))
        return True
    except ValueError:
        return False


async def _ipam_rows(user: dict) -> List[dict]:
    """IPs das configurações + reservados. Registro com IP/rede ilegível fica de fora (não derruba as telas)."""
    rows = []
    for r in await db.ipam_addresses.find(_ipam_scope(user), {"_id": 0}).to_list(200000):
        if _ipam_ok_ip(r.get("ip")) and _ipam_net(r.get("network")):
            r.setdefault("vrf", "")
            r.setdefault("device_name", "?")
            r.setdefault("device_id", "?")
            r.setdefault("source", "config")
            rows.append(r)
    for m in await db.ipam_manual.find(_ipam_scope(user), {"_id": 0}).to_list(50000):
        if not _ipam_ok_ip(m.get("ip")):
            continue
        rows.append({**m, "device_id": f"manual:{m.get('id')}", "device_name": "reservado", "interface": "", "source": "manual",
                     "network": m["ip"] + ("/32" if ":" not in m["ip"] else "/128"), "plen": 32 if ":" not in m["ip"] else 128})
    return rows


async def _ipam_pfx_docs(user: dict) -> List[dict]:
    out = []
    for p in await db.ipam_prefixes.find(_ipam_scope(user), {"_id": 0}).to_list(20000):
        n = _ipam_net(p.get("prefix"))
        if n:
            p["prefix"] = str(n)
            out.append(p)
    return out


class IpamPrefixIn(BaseModel):
    prefix: str
    description: str = ""
    vrf: str = ""
    vlan: Optional[int] = None
    status: str = "active"
    tags: List[str] = []


def _ipam_clean(body: IpamPrefixIn) -> dict:
    try:
        pfx = ipammod.clean_prefix(body.prefix)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"prefix": pfx, "description": body.description.strip()[:200], "vrf": body.vrf.strip()[:60],
            "vlan": body.vlan if body.vlan and 1 <= body.vlan <= 4094 else None,
            "status": body.status if body.status in ("active", "reserved", "container", "deprecated") else "active",
            "tags": [t.strip()[:30] for t in body.tags if t.strip()][:20]}


@api.get("/ipam/prefixes")
async def ipam_prefixes(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    raw = await db.ipam_prefixes.find(_ipam_scope(user), {"_id": 0}).to_list(20000)
    rows = await _ipam_rows(user)
    pfx, nets, bad, seen = [], {}, [], set()
    for p in raw:                                  # registro com prefixo estragado não derruba a tela
        n = _ipam_net(p.get("prefix"))
        if not n:
            bad.append(str(p.get("prefix"))[:60])
            continue
        p["prefix"] = str(n)
        if (p["prefix"], p.get("vrf") or "") in seen:
            continue
        seen.add((p["prefix"], p.get("vrf") or ""))
        nets[p["prefix"]] = n
        pfx.append(p)
    ips_v = []
    for r in rows:
        try:
            ips_v.append((ipaddress.ip_address(str(r.get("ip") or "").split("%")[0]), r.get("vrf") or ""))
        except ValueError:
            bad.append(str(r.get("ip"))[:60])
    if bad:
        logger.warning(f"IPAM: {len(bad)} registro(s) com IP/prefixo inválido ignorados: {bad[:10]}")
    allp = list(nets)                              # prefixo repetido em outra VRF entra uma vez só na hierarquia
    by_ver = {4: sorted((i, v) for i, v in ips_v if i.version == 4), 6: sorted((i, v) for i, v in ips_v if i.version == 6)}
    import bisect
    keys = {ver: [i for i, _ in lst] for ver, lst in by_ver.items()}
    for p in pfx:
        n = nets[p["prefix"]]
        lst, ks = by_ver[n.version], keys[n.version]
        lo = bisect.bisect_left(ks, n.network_address)
        hi = bisect.bisect_right(ks, n.broadcast_address)
        inside = {str(i) for i, v in lst[lo:hi] if not p.get("vrf") or v == p["vrf"]}
        try:
            p.update(ipammod.usage(p["prefix"], inside))
        except Exception as e:
            logger.warning(f"IPAM uso de {p['prefix']}: {e}")
            p.update({"size": n.num_addresses, "usable": n.num_addresses, "used": len(inside), "pct": 0.0})
        parents = [q for q in allp if q != p["prefix"] and nets[q].version == n.version and n.subnet_of(nets[q])]
        p["children"] = len(ipammod.children(p["prefix"], allp))
        p["parent"] = max(parents, key=lambda q: nets[q].prefixlen) if parents else None
        p["depth"] = len(parents)
    pfx.sort(key=lambda p: (nets[p["prefix"]].version, int(nets[p["prefix"]].network_address), nets[p["prefix"]].prefixlen))
    cfg_rows = [r for r in rows if r.get("source") == "config" and _ipam_net(r.get("network")) and r.get("ip")]
    try:
        dups = len(ipammod.duplicates(cfg_rows))
        unc = len(ipammod.uncovered_networks(cfg_rows, allp))
    except Exception as e:
        logger.warning(f"IPAM resumo: {e}")
        dups, unc = 0, 0
    return {"prefixes": pfx, "addresses": len(rows), "duplicates": dups, "uncovered": unc, "invalid": len(bad)}


@api.post("/ipam/prefixes")
async def ipam_prefix_create(body: IpamPrefixIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    p = _ipam_clean(body)
    if await db.ipam_prefixes.find_one({"prefix": p["prefix"], "vrf": p["vrf"], **_ipam_scope(user)}):
        raise HTTPException(409, "Esse prefixo já está cadastrado")
    p.update(id=uuid.uuid4().hex[:16], owner_id=user["id"], created_at=datetime.now(timezone.utc).isoformat())
    await db.ipam_prefixes.insert_one(dict(p))
    return p


@api.put("/ipam/prefixes/{pid}")
async def ipam_prefix_update(pid: str, body: IpamPrefixIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    p = _ipam_clean(body)
    r = await db.ipam_prefixes.update_one({"id": pid, **_ipam_scope(user)}, {"$set": p})
    if not r.matched_count:
        raise HTTPException(404, "Prefixo não encontrado")
    return {**p, "id": pid}


@api.delete("/ipam/prefixes/{pid}")
async def ipam_prefix_delete(pid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await db.ipam_prefixes.delete_one({"id": pid, **_ipam_scope(user)})
    return {"ok": True}


@api.get("/ipam/prefix")
async def ipam_prefix_detail(prefix: str, vrf: str = "", want: int = 0, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    try:
        pfx = ipammod.clean_prefix(prefix)
    except ValueError as e:
        raise HTTPException(400, str(e))
    n = ipaddress.ip_network(pfx)
    rows = [r for r in await _ipam_rows(user) if ipaddress.ip_address(r["ip"]).version == n.version and ipaddress.ip_address(r["ip"]) in n
            and (not vrf or (r.get("vrf") or "") == vrf)]
    rows.sort(key=lambda r: int(ipaddress.ip_address(r["ip"])))
    allp = list(dict.fromkeys(p["prefix"] for p in await _ipam_pfx_docs(user)))
    kids = ipammod.children(pfx, allp)
    used_nets = kids + [r["network"] for r in rows if r["source"] == "config"] + [r["ip"] + ("/32" if n.version == 4 else "/128") for r in rows]
    return {"prefix": pfx, "vrf": vrf, "addresses": rows, "children": kids, **ipammod.usage(pfx, [r["ip"] for r in rows]),
            "free": ipammod.free_blocks(pfx, used_nets, want or None, 30)}


@api.get("/ipam/search")
async def ipam_search(q: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(400, "Digite pelo menos 2 caracteres")
    rows = await _ipam_rows(user)
    out = []
    try:
        addr = ipaddress.ip_address(q)
        out = [r for r in rows if r["ip"] == str(addr) or (ipaddress.ip_address(r["ip"]).version == addr.version and addr in _ipam_net(r["network"]))]
    except ValueError:
        try:
            net = ipaddress.ip_network(q, strict=False)
            out = [r for r in rows if ipaddress.ip_address(r["ip"]).version == net.version and ipaddress.ip_address(r["ip"]) in net]
        except ValueError:
            low = q.lower()
            out = [r for r in rows if r["ip"].startswith(low) or low in (r.get("device_name") or "").lower() or low in (r.get("interface") or "").lower()
                   or low in (r.get("description") or "").lower() or low in (r.get("vrf") or "").lower()]
    pfx = [(ipaddress.ip_network(p["prefix"]), p["prefix"]) for p in await _ipam_pfx_docs(user)]
    for r in out[:300]:
        a = ipaddress.ip_address(r["ip"])
        inn = [(n, t) for n, t in pfx if n.version == a.version and a in n]
        r["prefix"] = max(inn, key=lambda x: x[0].prefixlen)[1] if inn else None
    return out[:300]


@api.get("/ipam/issues")
async def ipam_issues(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    rows = [r for r in await _ipam_rows(user) if r["source"] == "config"]
    allp = list(dict.fromkeys(p["prefix"] for p in await _ipam_pfx_docs(user)))
    return {"duplicates": [{"ip": k, "where": [{"device_name": x.get("device_name") or "?", "interface": x.get("interface") or ""} for x in v]}
                           for k, v in ipammod.duplicates(rows).items()],
            "uncovered": ipammod.uncovered_networks(rows, allp)[:1000]}


_IPAM_MGMT_IF = re.compile(r"^(meth|mgmt|management|fxp0|em0|me0|mgmt_eth|ma\d|oob|bmc|ilo)", re.I)


@api.get("/ipam/duplicates")
async def ipam_duplicates(tags: str = "", mode: str = "per_group", ignore_mgmt: bool = True, same_name: bool = True,
                          user: dict = Depends(get_current_user)):
    """IP duplicado por grupo de equipamentos (tags): redes separadas não se misturam.
    mode=per_group: só compara equipamentos do mesmo grupo; together: junta os grupos escolhidos num conjunto só."""
    _no_viewer(user)
    devs = {d["id"]: d async for d in db.devices.find(_ipam_scope(user), {"_id": 0, "id": 1, "name": 1, "tags": 1})}
    counts: Dict[str, int] = {}
    for d in devs.values():
        for t in (d.get("tags") or ["__none__"]):
            counts[t] = counts.get(t, 0) + 1
    want = [t for t in (x.strip() for x in tags.split(",")) if t]
    rows = [r for r in await _ipam_rows(user) if r["source"] == "config" and r.get("device_id") in devs]
    if ignore_mgmt:
        rows = [r for r in rows if not _IPAM_MGMT_IF.match(str(r.get("interface") or "").strip())]

    def groups_of(r) -> List[str]:
        tg = devs[r["device_id"]].get("tags") or ["__none__"]
        return [t for t in tg if not want or t in want]
    pools: Dict[str, list] = {}
    for r in rows:
        gs = groups_of(r)
        if not gs:
            continue
        if mode == "together":
            pools.setdefault("__all__", []).append(r)
        else:
            for g in gs:
                pools.setdefault(g, []).append(r)
    out = []
    for g, rs in pools.items():
        seen: Dict[tuple, dict] = {}
        for r in rs:
            k = (str(r["ip"]), r.get("vrf") or "")
            who = (devs[r["device_id"]].get("name") or "").strip().lower() if same_name else r["device_id"]
            e = seen.setdefault(k, {"devices": set(), "where": {}})
            e["devices"].add(who)
            e["where"][(r["device_id"], r.get("interface") or "")] = {"device_id": r["device_id"], "device_name": r.get("device_name") or "?",
                                                                        "interface": r.get("interface") or "", "tags": devs[r["device_id"]].get("tags") or []}
        for (ip, vrf), e in seen.items():
            if len(e["devices"]) > 1:
                out.append({"ip": ip, "vrf": vrf, "group": None if g == "__all__" else g, "where": list(e["where"].values()),
                            "devices": len(e["devices"])})
    out.sort(key=lambda x: (x["group"] or "", ipammod.net_key(x["ip"] + ("/32" if ":" not in x["ip"] else "/128")), x["vrf"]))
    by_group: Dict[str, int] = {}
    for x in out:
        by_group[x["group"] or "__all__"] = by_group.get(x["group"] or "__all__", 0) + 1
    return {"groups": [{"tag": t, "devices": n} for t, n in sorted(counts.items(), key=lambda x: (x[0] == "__none__", x[0].lower()))],
            "duplicates": out[:5000], "total": len(out), "by_group": by_group, "mode": mode}


# ---------- IPAM: documentação de IP por grupo + importação do phpIPAM ----------
async def _doc_group(gid: str, user: dict) -> dict:
    g = await db.ipam_docgroups.find_one({"id": gid, **_ipam_scope(user)}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Grupo de documentação não encontrado")
    return g


def _doc_err(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


@api.get("/ipam/doc/groups")
async def doc_groups(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    gs = await db.ipam_docgroups.find(_ipam_scope(user), {"_id": 0}).sort("name", 1).to_list(1000)
    for g in gs:
        g["subnets"] = await db.ipam_docsubnets.count_documents({"group_id": g["id"]})
        g["ips"] = await db.ipam_docips.count_documents({"group_id": g["id"]})
    return gs


@api.post("/ipam/doc/groups")
async def doc_group_add(body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    try:
        g = ipamdoc.clean_group(body or {})
    except ipamdoc.DocError as e:
        _doc_err(e)
    if await db.ipam_docgroups.find_one({"name": g["name"], **_ipam_scope(user)}):
        raise HTTPException(status_code=409, detail="Já existe um grupo com esse nome")
    g.update(id=os.urandom(8).hex(), owner_id=user["id"], created_at=datetime.now(timezone.utc).isoformat(), created_by=user.get("email", ""))
    await db.ipam_docgroups.insert_one(dict(g))
    return {**g, "subnets": 0, "ips": 0}


@api.put("/ipam/doc/groups/{gid}")
async def doc_group_put(gid: str, body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _doc_group(gid, user)
    try:
        g = ipamdoc.clean_group(body or {})
    except ipamdoc.DocError as e:
        _doc_err(e)
    await db.ipam_docgroups.update_one({"id": gid}, {"$set": g})
    return await _doc_group(gid, user)


@api.delete("/ipam/doc/groups/{gid}")
async def doc_group_del(gid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _doc_group(gid, user)
    await db.ipam_docips.delete_many({"group_id": gid})
    await db.ipam_docsubnets.delete_many({"group_id": gid})
    await db.ipam_docgroups.delete_one({"id": gid})
    return {"ok": True}


@api.get("/ipam/doc/groups/{gid}/subnets")
async def doc_subnets(gid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _doc_group(gid, user)
    subs = await db.ipam_docsubnets.find({"group_id": gid}, {"_id": 0}).to_list(20000)
    ips = await db.ipam_docips.find({"group_id": gid}, {"_id": 0, "ip": 1, "vrf": 1, "subnet_id": 1}).to_list(500000)
    by_sub: Dict[str, List[str]] = {}
    loose = 0
    for i in ips:
        if i.get("subnet_id"):
            by_sub.setdefault(i["subnet_id"], []).append(i["ip"])
        else:
            loose += 1
    for s in subs:
        s.update(ipamdoc.usage(s["prefix"], by_sub.get(s["id"], [])))
    subs.sort(key=lambda s: (ipaddress.ip_network(s["prefix"]).version, int(ipaddress.ip_network(s["prefix"]).network_address),
                             ipaddress.ip_network(s["prefix"]).prefixlen, s.get("vrf") or ""))
    return {"subnets": subs, "loose": loose}


async def _doc_relink(gid: str):
    """Liga cada IP do grupo ao prefixo mais específico (depois de criar/apagar prefixo)."""
    subs = await db.ipam_docsubnets.find({"group_id": gid}, {"_id": 0, "id": 1, "prefix": 1, "vrf": 1}).to_list(20000)
    async for i in db.ipam_docips.find({"group_id": gid}, {"_id": 0, "id": 1, "ip": 1, "vrf": 1, "subnet_id": 1}):
        cand = [s for s in subs if (s.get("vrf") or "") == (i.get("vrf") or "")] or subs
        s = ipamdoc.subnet_of(i["ip"], cand)
        sid = s["id"] if s else None
        if sid != i.get("subnet_id"):
            await db.ipam_docips.update_one({"id": i["id"]}, {"$set": {"subnet_id": sid}})


@api.post("/ipam/doc/groups/{gid}/subnets")
async def doc_subnet_add(gid: str, body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _doc_group(gid, user)
    try:
        s = ipamdoc.clean_subnet(body or {})
    except ipamdoc.DocError as e:
        _doc_err(e)
    if await db.ipam_docsubnets.find_one({"group_id": gid, "prefix": s["prefix"], "vrf": s["vrf"]}):
        raise HTTPException(status_code=409, detail="Esse prefixo já está neste grupo")
    s.update(id=os.urandom(8).hex(), group_id=gid, created_at=datetime.now(timezone.utc).isoformat())
    await db.ipam_docsubnets.insert_one(dict(s))
    await _doc_relink(gid)
    return s


@api.put("/ipam/doc/subnets/{sid}")
async def doc_subnet_put(sid: str, body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await db.ipam_docsubnets.find_one({"id": sid}, {"_id": 0})
    if not cur:
        raise HTTPException(status_code=404, detail="Prefixo não encontrado")
    await _doc_group(cur["group_id"], user)
    try:
        s = ipamdoc.clean_subnet(body or {})
    except ipamdoc.DocError as e:
        _doc_err(e)
    await db.ipam_docsubnets.update_one({"id": sid}, {"$set": s})
    await _doc_relink(cur["group_id"])
    return {**cur, **s}


@api.delete("/ipam/doc/subnets/{sid}")
async def doc_subnet_del(sid: str, ips: bool = False, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await db.ipam_docsubnets.find_one({"id": sid}, {"_id": 0})
    if not cur:
        raise HTTPException(status_code=404, detail="Prefixo não encontrado")
    await _doc_group(cur["group_id"], user)
    if ips:
        await db.ipam_docips.delete_many({"subnet_id": sid})
    await db.ipam_docsubnets.delete_one({"id": sid})
    await _doc_relink(cur["group_id"])
    return {"ok": True}


@api.get("/ipam/doc/groups/{gid}/ips")
async def doc_ips(gid: str, subnet_id: str = "", q: str = "", loose: bool = False, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _doc_group(gid, user)
    f: dict = {"group_id": gid}
    sub = None
    if subnet_id:
        sub = await db.ipam_docsubnets.find_one({"id": subnet_id, "group_id": gid}, {"_id": 0})
        if not sub:
            raise HTTPException(status_code=404, detail="Prefixo não encontrado")
        f["subnet_id"] = subnet_id
    elif loose:
        f["subnet_id"] = None
    rows = await db.ipam_docips.find(f, {"_id": 0}).to_list(100000)
    if q.strip():
        ql = q.strip().lower()
        rows = [r for r in rows if any(ql in str(r.get(k) or "").lower() for k in ("ip", "hostname", "description", "mac", "owner", "notes"))]
    rows.sort(key=lambda r: ipamdoc.ip_key(r["ip"]))
    # onde o IP aparece nas configurações dos equipamentos (backups)
    seen: Dict[str, list] = {}
    want = {r["ip"] for r in rows[:5000]}
    if want:
        async for a in db.ipam_addresses.find({"ip": {"$in": list(want)}, **_ipam_scope(user)}, {"_id": 0, "ip": 1, "device_name": 1, "interface": 1}):
            seen.setdefault(a["ip"], []).append(f"{a.get('device_name', '?')} ({a.get('interface', '')})")
    for r in rows:
        if r["ip"] in seen:
            r["seen"] = seen[r["ip"]][:5]
    # outros grupos que também documentam o mesmo IP
    others = {}
    if want:
        names = {g["id"]: g["name"] async for g in db.ipam_docgroups.find(_ipam_scope(user), {"_id": 0, "id": 1, "name": 1})}
        async for o in db.ipam_docips.find({"ip": {"$in": list(want)}, "group_id": {"$ne": gid}}, {"_id": 0, "ip": 1, "group_id": 1}):
            if o["group_id"] in names:
                others.setdefault(o["ip"], set()).add(names[o["group_id"]])
    for r in rows:
        if r["ip"] in others:
            r["also_in"] = sorted(others[r["ip"]])
    out = {"ips": rows}
    if sub:
        out["subnet"] = {**sub, **ipamdoc.usage(sub["prefix"], [r["ip"] for r in rows])}
        out["free"] = ipamdoc.free_ips(sub["prefix"], [r["ip"] for r in rows], 64) if ipaddress.ip_network(sub["prefix"]).num_addresses <= 65536 else []
    return out


async def _doc_ip_save(gid: str, data: dict, user: dict, iid: Optional[str] = None, overwrite: bool = True) -> tuple:
    subs = await db.ipam_docsubnets.find({"group_id": gid}, {"_id": 0, "id": 1, "prefix": 1, "vrf": 1}).to_list(20000)
    cand = [s for s in subs if (s.get("vrf") or "") == (data.get("vrf") or "")] or subs
    s = ipamdoc.subnet_of(data["ip"], cand)
    data["subnet_id"] = s["id"] if s else None
    now = datetime.now(timezone.utc).isoformat()
    data.update(updated_at=now, updated_by=user.get("email", ""))
    cur = await db.ipam_docips.find_one({"group_id": gid, "ip": data["ip"], "vrf": data.get("vrf") or ""}, {"_id": 0})
    if cur and iid and cur["id"] != iid:
        raise HTTPException(status_code=409, detail=f"{data['ip']} já está documentado neste grupo")
    if iid and not cur:
        cur = await db.ipam_docips.find_one({"id": iid, "group_id": gid}, {"_id": 0})
        if not cur:
            raise HTTPException(status_code=404, detail="IP não encontrado")
    if cur:
        if not overwrite:
            return cur, "kept"
        await db.ipam_docips.update_one({"id": cur["id"]}, {"$set": data})
        return {**cur, **data}, "updated"
    doc = {"id": os.urandom(8).hex(), "group_id": gid, "created_at": now, **data}
    await db.ipam_docips.insert_one(dict(doc))
    return doc, "created"


@api.post("/ipam/doc/groups/{gid}/ips")
async def doc_ip_add(gid: str, body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _doc_group(gid, user)
    try:
        d = ipamdoc.clean_ip(body or {})
    except ipamdoc.DocError as e:
        _doc_err(e)
    if await db.ipam_docips.find_one({"group_id": gid, "ip": d["ip"], "vrf": d["vrf"]}):
        raise HTTPException(status_code=409, detail=f"{d['ip']} já está documentado neste grupo (edite o existente)")
    doc, _ = await _doc_ip_save(gid, d, user)
    return doc


@api.put("/ipam/doc/ips/{iid}")
async def doc_ip_put(iid: str, body: dict, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await db.ipam_docips.find_one({"id": iid}, {"_id": 0})
    if not cur:
        raise HTTPException(status_code=404, detail="IP não encontrado")
    await _doc_group(cur["group_id"], user)
    try:
        d = ipamdoc.clean_ip(body or {})
    except ipamdoc.DocError as e:
        _doc_err(e)
    doc, _ = await _doc_ip_save(cur["group_id"], d, user, iid=iid)
    return doc


@api.delete("/ipam/doc/ips/{iid}")
async def doc_ip_del(iid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    cur = await db.ipam_docips.find_one({"id": iid}, {"_id": 0})
    if not cur:
        raise HTTPException(status_code=404, detail="IP não encontrado")
    await _doc_group(cur["group_id"], user)
    await db.ipam_docips.delete_one({"id": iid})
    return {"ok": True}


@api.get("/ipam/doc/groups/{gid}/export")
async def doc_export(gid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    g = await _doc_group(gid, user)
    subs = {s["id"]: s for s in await db.ipam_docsubnets.find({"group_id": gid}, {"_id": 0}).to_list(20000)}
    rows = await db.ipam_docips.find({"group_id": gid}, {"_id": 0}).to_list(500000)
    rows.sort(key=lambda r: ipamdoc.ip_key(r["ip"]))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["prefixo", "vlan"] + ipamdoc.CSV_COLS)
    for r in rows:
        s = subs.get(r.get("subnet_id")) or {}
        w.writerow([s.get("prefix", ""), s.get("vlan") or ""] + [r.get(k) or "" for k in ipamdoc.CSV_COLS])
    return PlainTextResponse("﻿" + buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="ipam-{_ftp_folder(g["name"]) or "grupo"}.csv"'})


class DocCsvIn(BaseModel):
    text: str
    overwrite: bool = False


@api.post("/ipam/doc/groups/{gid}/import-csv")
async def doc_import_csv(gid: str, body: DocCsvIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await _doc_group(gid, user)
    res = {"created": 0, "updated": 0, "kept": 0, "errors": []}
    for r in ipamdoc.parse_csv_rows(body.text[:5_000_000])[:50000]:
        try:
            d = ipamdoc.clean_ip(r)
        except ipamdoc.DocError as e:
            res["errors"].append(str(e))
            continue
        _, act = await _doc_ip_save(gid, d, user, overwrite=body.overwrite)
        res[act] += 1
    res["errors"] = res["errors"][:30]
    return res


# --- phpIPAM
_php_jobs: Dict[str, dict] = {}


async def _php_cfg(raw: bool = False) -> dict:
    d = await db.config.find_one({"key": "phpipam"}, {"_id": 0}) or {}
    if raw:
        return d
    return {"url": d.get("url", ""), "app_id": d.get("app_id", ""), "auth": d.get("auth", "token"), "user": d.get("user", ""),
            "verify_tls": d.get("verify_tls", True), "has_secret": bool(d.get("secret")), "last_import": d.get("last_import")}


async def _php_client() -> "ipamdoc.PhpIpam":
    d = await _php_cfg(raw=True)
    if not d.get("url") or not d.get("app_id"):
        raise HTTPException(status_code=400, detail="Configure a URL e o App ID do phpIPAM")
    secret = vault.decrypt(d.get("secret", ""))
    cl = httpx.AsyncClient(timeout=30, verify=d.get("verify_tls", True), follow_redirects=True)

    async def request(method, url, headers, auth):
        r = await cl.request(method, url, headers=headers, auth=auth)
        try:
            j = r.json()
        except ValueError:
            j = None
        return r.status_code, j
    p = ipamdoc.PhpIpam(d["url"], d["app_id"], request, token=secret if d.get("auth") == "token" else "",
                        user=d.get("user", ""), password=secret if d.get("auth") == "user" else "")
    p._http = cl
    return p


class PhpCfgIn(BaseModel):
    url: str
    app_id: str
    auth: str = "token"                 # token (App code / token estático) ou user (usuário e senha)
    user: str = ""
    secret: Optional[str] = None
    verify_tls: bool = True


@api.get("/ipam/phpipam")
async def php_get(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    return await _php_cfg()


@api.put("/ipam/phpipam")
async def php_put(body: PhpCfgIn, _: dict = Depends(require_admin)):
    url = body.url.strip().rstrip("/")
    if url.endswith("/api"):
        url = url[:-4]
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="URL do phpIPAM deve começar com http:// ou https://")
    if not re.fullmatch(r"[\w.-]{1,40}", body.app_id.strip()):
        raise HTTPException(status_code=400, detail="App ID inválido (o nome do app em Administração → API)")
    upd = {"key": "phpipam", "url": url, "app_id": body.app_id.strip(), "auth": "user" if body.auth == "user" else "token",
           "user": body.user.strip()[:80], "verify_tls": body.verify_tls}
    if body.secret is not None:
        upd["secret"] = vault.encrypt(body.secret.strip()) if body.secret.strip() else ""
    await db.config.update_one({"key": "phpipam"}, {"$set": upd}, upsert=True)
    return await _php_cfg()


async def _php_sections(p) -> list:
    secs = await p.get("sections")
    out = []
    for s in secs:
        subs = await p.get(f"sections/{s['id']}/subnets")
        nets = [x for x in subs if str(x.get("isFolder") or "0") != "1" and x.get("subnet")]
        out.append({"id": str(s["id"]), "name": s.get("name") or f"Seção {s['id']}", "description": s.get("description") or "",
                    "subnets": len(nets), "sample": [f"{x['subnet']}/{x['mask']}" for x in nets[:5]]})
    return out


@api.post("/ipam/phpipam/test")
async def php_test(_: dict = Depends(require_admin)):
    p = await _php_client()
    try:
        await p.login()
        return {"ok": True, "sections": await _php_sections(p)}
    except ipamdoc.DocError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        await p._http.aclose()


class PhpImportIn(BaseModel):
    mapping: List[dict]                  # [{section_id, target: "new"|"skip"|<group_id>, name}]
    addresses: bool = True
    overwrite: bool = False


async def _php_import_run(job: dict, body: PhpImportIn, user: dict):
    p = await _php_client()
    try:
        await p.login()
        tags = await p.tag_map()
        vlans = {str(v.get("vlanId") or v.get("id")): v for v in await p.get("vlan")}
        try:
            vrfs = {str(v.get("vrfId") or v.get("id")): v for v in await p.get("vrf")}
        except ipamdoc.DocError:
            vrfs = {}
        maps = [m for m in body.mapping if m.get("target") and m.get("target") != "skip"]
        for m in maps:
            job["section"] = m.get("name") or m.get("section_id")
            if m["target"] == "new":
                nm = ipamdoc._s(m.get("name") or f"phpIPAM {m['section_id']}", 80)
                g = await db.ipam_docgroups.find_one({"name": nm, **_ipam_scope(user)}, {"_id": 0})
                if not g:
                    g = {"id": os.urandom(8).hex(), "name": nm, "description": "importado do phpIPAM", "color": ipamdoc.COLORS[len(job["groups"]) % 8],
                         "owner_id": user["id"], "created_at": datetime.now(timezone.utc).isoformat(), "created_by": user.get("email", "")}
                    await db.ipam_docgroups.insert_one(dict(g))
            else:
                g = await _doc_group(m["target"], user)
            job["groups"].append(g["name"])
            subs = await p.get(f"sections/{m['section_id']}/subnets")
            job["subnets_total"] += len(subs)
            for s in subs:
                job["subnets_done"] += 1
                sd = ipamdoc.php_subnet(s, vlans, vrfs)
                if not sd:
                    continue
                php_id = sd.pop("php_id")
                cur = await db.ipam_docsubnets.find_one({"group_id": g["id"], "prefix": sd["prefix"], "vrf": sd["vrf"]}, {"_id": 0})
                if cur:
                    if body.overwrite:
                        await db.ipam_docsubnets.update_one({"id": cur["id"]}, {"$set": {k: v for k, v in sd.items() if v not in ("", None)}})
                    job["subnets_kept"] += 1
                else:
                    await db.ipam_docsubnets.insert_one({**sd, "id": os.urandom(8).hex(), "group_id": g["id"], "php_id": php_id,
                                                        "created_at": datetime.now(timezone.utc).isoformat()})
                    job["subnets_created"] += 1
                if not body.addresses:
                    continue
                for a in await p.get(f"subnets/{s['id']}/addresses"):
                    ad = ipamdoc.php_address(a, tags, sd["vrf"])
                    if not ad:
                        continue
                    ad.pop("php_id", None)
                    try:
                        d = ipamdoc.clean_ip(ad)
                    except ipamdoc.DocError:
                        ad["mac"] = ""
                        try:
                            d = ipamdoc.clean_ip(ad)
                        except ipamdoc.DocError as e:
                            job["errors"].append(str(e))
                            continue
                    _, act = await _doc_ip_save(g["id"], d, user, overwrite=body.overwrite)
                    job[f"ips_{act}"] += 1
            await _doc_relink(g["id"])
        job["state"] = "pronto"
        await db.config.update_one({"key": "phpipam"}, {"$set": {"last_import": {"at": datetime.now(timezone.utc).isoformat(), "by": user.get("email", ""),
                                   **{k: job[k] for k in ("subnets_created", "ips_created", "ips_updated")}}}})
    except Exception as e:
        job["state"], job["error"] = "erro", (str(e) if isinstance(e, ipamdoc.DocError) else f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        job["errors"] = job["errors"][:30]
        job["ended_at"] = datetime.now(timezone.utc).isoformat()
        await p._http.aclose()


@api.post("/ipam/phpipam/import")
async def php_import(body: PhpImportIn, user: dict = Depends(require_admin)):
    if any(j["state"] == "importando" for j in _php_jobs.values()):
        raise HTTPException(status_code=409, detail="Já há uma importação em andamento")
    jid = os.urandom(6).hex()
    job = {"id": jid, "state": "importando", "section": "", "groups": [], "subnets_total": 0, "subnets_done": 0, "subnets_created": 0,
           "subnets_kept": 0, "ips_created": 0, "ips_updated": 0, "ips_kept": 0, "errors": [], "error": "",
           "started_at": datetime.now(timezone.utc).isoformat()}
    _php_jobs.clear()
    _php_jobs[jid] = job
    _bg(_php_import_run(job, body, user))
    return job


@api.get("/ipam/phpipam/import/{jid}")
async def php_import_status(jid: str, _: dict = Depends(require_admin)):
    j = _php_jobs.get(jid)
    if not j:
        raise HTTPException(status_code=404, detail="Importação não encontrada")
    return j


class IpamImportIn(BaseModel):
    networks: List[str]
    description: str = ""


@api.post("/ipam/import")
async def ipam_import(body: IpamImportIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    n = 0
    for x in body.networks[:2000]:
        try:
            pfx = ipammod.clean_prefix(x)
        except ValueError:
            continue
        if await db.ipam_prefixes.find_one({"prefix": pfx, **_ipam_scope(user)}):
            continue
        await db.ipam_prefixes.insert_one({"id": uuid.uuid4().hex[:16], "prefix": pfx, "description": body.description.strip()[:200], "vrf": "",
                                           "vlan": None, "status": "active", "tags": [], "owner_id": user["id"],
                                           "created_at": datetime.now(timezone.utc).isoformat()})
        n += 1
    return {"added": n}


class IpamManualIn(BaseModel):
    ip: str
    description: str = ""
    vrf: str = ""


@api.post("/ipam/manual")
async def ipam_manual_add(body: IpamManualIn, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    try:
        ip = str(ipaddress.ip_address(body.ip.strip()))
    except ValueError:
        raise HTTPException(400, "IP inválido")
    doc = {"id": uuid.uuid4().hex[:16], "ip": ip, "description": body.description.strip()[:200], "vrf": body.vrf.strip()[:60],
           "owner_id": user["id"], "created_by": user.get("name") or user["email"], "created_at": datetime.now(timezone.utc).isoformat()}
    await db.ipam_manual.insert_one(dict(doc))
    return doc


@api.delete("/ipam/manual/{mid}")
async def ipam_manual_delete(mid: str, user: dict = Depends(get_current_user)):
    _no_viewer(user)
    await db.ipam_manual.delete_one({"id": mid, **_ipam_scope(user)})
    return {"ok": True}


@api.get("/ipam/inventory")
async def ipam_inventory(user: dict = Depends(get_current_user)):
    """Inventário dos equipamentos: fabricante, modelo, firmware, IP de gerência, agente, grupos, último backup e IPs."""
    _no_viewer(user)
    devs = [d for d in await db.devices.find(_scope(user), {"_id": 0, "password": 0, "snmp_community": 0}).sort("name", 1).to_list(20000) if d.get("id")]
    comp = {c.get("device_id"): c async for c in db.compliance_results.find({}, {"_id": 0, "device_id": 1, "firmware": 1})}
    cnt = {s.get("device_id"): s.get("count", 0) async for s in db.ipam_dev_state.find({}, {"_id": 0, "device_id": 1, "count": 1})}
    ags = {a.get("id"): a.get("name") async for a in db.agents.find({}, {"_id": 0, "id": 1, "name": 1})}

    def fw(d):
        f = (comp.get(d["id"]) or {}).get("firmware")
        return f if isinstance(f, dict) else {}
    last = {}
    async for b in db.backups.aggregate([{"$match": {"device_id": {"$in": [d["id"] for d in devs]}, "ok": {"$ne": False}}},
                                         {"$group": {"_id": "$device_id", "at": {"$max": "$created_at"}}}]):
        last[b["_id"]] = b["at"]
    return [{"id": d["id"], "name": d.get("name") or d.get("host") or "?", "host": d.get("host"), "device_type": d.get("device_type"), "tags": d.get("tags") or [],
             "agent": ags.get(d.get("agent_id")) if d.get("agent_id") else None, "status": d.get("status"),
             "model": fw(d).get("model"), "firmware": fw(d).get("version"),
             "addresses": cnt.get(d["id"], 0), "last_backup": last.get(d["id"])} for d in devs]


@api.post("/ipam/refresh")
async def ipam_refresh(user: dict = Depends(get_current_user)):
    _no_viewer(user)
    _bg(_ipam_refresh(force=True))
    return {"ok": True}


class NetboxIn(BaseModel):
    url: str = ""
    token: Optional[str] = None
    verify_tls: bool = True


async def _netbox_cfg() -> dict:
    return await db.config.find_one({"key": "netbox"}, {"_id": 0}) or {}


@api.get("/ipam/netbox")
async def netbox_get(_: dict = Depends(require_admin)):
    c = await _netbox_cfg()
    return {"url": c.get("url", ""), "has_token": bool(c.get("token")), "verify_tls": c.get("verify_tls", True), "last_sync": c.get("last_sync")}


@api.put("/ipam/netbox")
async def netbox_put(body: NetboxIn, _: dict = Depends(require_admin)):
    url = body.url.strip().rstrip("/")
    if url and not re.match(r"^https?://", url):
        raise HTTPException(400, "URL do NetBox deve começar com http:// ou https://")
    upd = {"key": "netbox", "url": url, "verify_tls": body.verify_tls}
    if body.token is not None:
        upd["token"] = vault.encrypt(body.token.strip()) if body.token.strip() else ""
    await db.config.update_one({"key": "netbox"}, {"$set": upd}, upsert=True)
    return await netbox_get(_)


def _netbox_client(c: dict) -> httpx.AsyncClient:
    tok = vault.decrypt(c.get("token", ""))
    if not c.get("url") or not tok:
        raise HTTPException(400, "Configure a URL e o token do NetBox")
    return httpx.AsyncClient(timeout=30, verify=c.get("verify_tls", True),
                             headers={"Authorization": f"Token {tok}", "Accept": "application/json", "Content-Type": "application/json"})


@api.post("/ipam/netbox/test")
async def netbox_test(_: dict = Depends(require_admin)):
    c = await _netbox_cfg()
    try:
        async with _netbox_client(c) as cl:
            r = await cl.get(f"{c['url']}/api/status/")
            if r.status_code in (401, 403):
                return {"ok": False, "error": "Token recusado pelo NetBox"}
            r.raise_for_status()
            j = r.json()
            return {"ok": True, "version": j.get("netbox-version") or j.get("netbox_version") or "?"}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


class NetboxSyncIn(BaseModel):
    push_prefixes: bool = True
    push_ips: bool = True
    pull_prefixes: bool = False
    dry_run: bool = True


@api.post("/ipam/netbox/sync")
async def netbox_sync(body: NetboxSyncIn, user: dict = Depends(require_admin)):
    """Compara o IPAM do BastiON com o NetBox; sem dry_run cria no NetBox o que falta (prefixos e IPs de interface)
    e, se pedido, traz para cá os prefixos que só existem lá. Nada é apagado em nenhum dos lados."""
    c = await _netbox_cfg()
    local_p = [p for p in await db.ipam_prefixes.find({}, {"_id": 0}).to_list(50000) if _ipam_net(p.get("prefix"))]
    for p in local_p:
        p["prefix"] = str(_ipam_net(p["prefix"]))
    local_i = [r for r in await _ipam_rows(user) if r["source"] == "config"]
    try:
        async with _netbox_client(c) as cl:
            nb_p = await ipammod.netbox_all(cl, c["url"], "ipam/prefixes")
            nb_i = await ipammod.netbox_all(cl, c["url"], "ipam/ip-addresses")
            vrfs = {v["name"]: v["id"] for v in await ipammod.netbox_all(cl, c["url"], "ipam/vrfs")}
            plan = ipammod.netbox_plan(local_p, local_i, nb_p, nb_i)
            res = {"to_create_prefixes": len(plan["new_prefixes"]), "to_create_ips": len(plan["new_ips"]),
                   "only_netbox": len(plan["only_netbox"]), "created_prefixes": 0, "created_ips": 0, "imported": 0, "errors": [],
                   "sample_prefixes": [p["prefix"] for p in plan["new_prefixes"][:10]], "sample_ips": [a["ip"] for a in plan["new_ips"][:10]],
                   "sample_only_netbox": [p["prefix"] for p in plan["only_netbox"][:10]], "dry_run": body.dry_run}
            if body.dry_run:
                return res

            async def bulk(path, items):
                done = 0
                for i in range(0, len(items), 100):
                    r = await cl.post(f"{c['url']}/api/{path}/", json=items[i:i + 100])
                    if r.status_code >= 400:
                        res["errors"].append(f"{path}: HTTP {r.status_code} {r.text[:200]}")
                        break
                    done += len(items[i:i + 100])
                return done
            if body.push_prefixes and plan["new_prefixes"]:
                items = [{"prefix": p["prefix"], "description": (p.get("description") or "")[:200], "status": p.get("status", "active"),
                          **({"vrf": vrfs[p["vrf"]]} if p.get("vrf") in vrfs else {})} for p in plan["new_prefixes"]]
                res["created_prefixes"] = await bulk("ipam/prefixes", items)
            if body.push_ips and plan["new_ips"]:
                items = [{"address": f"{a['ip']}/{a['plen']}", "status": "active",
                          "description": f"{a['device_name']} · {a['interface']}{(' · ' + a['description']) if a.get('description') else ''}"[:200],
                          **({"vrf": vrfs[a["vrf"]]} if a.get("vrf") in vrfs else {})} for a in plan["new_ips"]]
                res["created_ips"] = await bulk("ipam/ip-addresses", items)
            if body.pull_prefixes:
                for p in plan["only_netbox"]:
                    await db.ipam_prefixes.insert_one({"id": uuid.uuid4().hex[:16], "prefix": p["prefix"], "description": p["description"][:200],
                                                       "vrf": p["vrf"], "vlan": None, "status": "active", "tags": ["netbox"], "owner_id": user["id"],
                                                       "created_at": datetime.now(timezone.utc).isoformat()})
                    res["imported"] += 1
            await db.config.update_one({"key": "netbox"}, {"$set": {"last_sync": {"at": datetime.now(timezone.utc).isoformat(),
                                                                                  **{k: res[k] for k in ("created_prefixes", "created_ips", "imported")}}}})
            return res
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(400, f"NetBox respondeu {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(400, f"NetBox: {str(e)[:200]}")


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
    try:
        await ftp_mgr.stop_all()
    except Exception:
        pass
    scheduler.stop()
    for t in _bg_tasks:
        t.cancel()
    assistant.stop()
    net_monitor.stop()
    optics_collector.stop()
    if _flow_asn_task:
        _flow_asn_task.cancel()
    await agent_pool.close_all()
    await JUMP_POOL.close_all()
    await web_proxy.stop()
    client.close()
