"""SSH Bastion Central - FastAPI backend."""
from dotenv import load_dotenv
from pathlib import Path
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import csv
import io
import os
import re
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from auth import (
    hash_password, verify_password, create_access_token, decode_token,
    get_current_user, require_admin,
)
from models import (
    UserCreate, UserOut, LoginPayload, UserUpdate, ChangePasswordPayload,
    AgentCreate, Agent,
    DeviceCreate, DeviceCreateRequest, Device, DEVICE_TYPES,
    DeviceIdsPayload, DeviceExportPayload, DeviceBulkUpdate,
    MapCreate, MapUpdate, MonitorSettings, DeviceMonitorPayload,
    DashboardCreate, DashboardUpdate, OpticsSettings, OpticsTestPayload,
    ScriptCreate, Script,
    Session, BatchExecPayload, BatchResultItem, SshKeyConfig, BastionSettings,
    AutomationSettings, BackupRunPayload, AISettings, BackupIdsPayload, BackupCleanupPayload,
)
import ai_assistant
from ssh_service import SSHClientWrapper, Hop, tcp_ping, LEGACY_TYPES
from telnet_service import TelnetClientWrapper
import vault
import automation
import snmp_service
import monitor as monitoring
import optics as optics_mod

TUNNEL_BIND_HOST = os.environ.get("TUNNEL_BIND_HOST", "127.0.0.1")

# ---------- Mongo ----------
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="SSH Bastion Central")
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s - %(message)s')
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
    scheduler.start()
    assistant.start()
    net_monitor.start()
    optics_collector.start()


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
             "description": "Bastion Datacenter São Paulo", "status": "online",
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
@api.post("/auth/login")
async def login(payload: LoginPayload):
    user = await db.users.find_one({"email": payload.email.lower()})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    token = create_access_token(user["id"], user["email"], user.get("role", "operator"))
    return {
        "token": token,
        "user": {
            "id": user["id"], "email": user["email"],
            "name": user.get("name", ""), "role": user.get("role", "operator"),
        },
    }


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    u = await db.users.find_one({"id": user["id"]})
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return {"id": u["id"], "email": u["email"], "name": u.get("name", ""), "role": u.get("role", "operator")}


@api.post("/auth/logout")
async def logout(user: dict = Depends(get_current_user)):
    return {"ok": True}


# ---------- Users (admin) ----------
@api.get("/users")
async def list_users(_: dict = Depends(require_admin)):
    docs = await db.users.find({}, {"password_hash": 0, "_id": 0}).to_list(500)
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
        "role": payload.role,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
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
        if payload.role not in ("admin", "operator"):
            raise HTTPException(status_code=400, detail="Papel inválido")
        if user_id == current["id"] and payload.role != "admin":
            raise HTTPException(status_code=400, detail="Você não pode remover seu próprio papel de administrador")
        update["role"] = payload.role
    if payload.password:
        if len(payload.password) < 6:
            raise HTTPException(status_code=400, detail="A senha deve ter pelo menos 6 caracteres")
        update["password_hash"] = hash_password(payload.password)
    if update:
        await db.users.update_one({"id": user_id}, {"$set": update})
    return await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})


@api.post("/auth/change-password")
async def change_password(payload: ChangePasswordPayload, current: dict = Depends(get_current_user)):
    u = await db.users.find_one({"id": current["id"]})
    if not u or not verify_password(payload.current_password, u["password_hash"]):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="A nova senha deve ter pelo menos 6 caracteres")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="A nova senha deve ser diferente da atual")
    await db.users.update_one({"id": current["id"]}, {"$set": {"password_hash": hash_password(payload.new_password)}})
    return {"ok": True}


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


# ---------- Bastion settings ----------
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
    lines = []
    for a in agents:
        opts = f'restrict,port-forwarding,permitlisten="{TUNNEL_BIND_HOST}:{a.get("tunnel_port")}"'
        lines.append(f'{opts} {a["agent_public_key"].strip()} bastion-agent-{a["id"]}')
    return PlainTextResponse("\n".join(lines) + "\n")


@api.get("/bastion/setup-script")
async def bastion_setup_script(api_url: str = Query(...), _: dict = Depends(require_admin)):
    s = await _bastion_settings()
    user = s.get("ssh_user", "bastion")
    script = f"""#!/usr/bin/env bash
# SSH Bastion Central — preparação do servidor (rode como root no VPS onde o backend está rodando)
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
echo "Bastion pronto. Usuário $BASTION_USER aceita túneis reversos; chaves sincronizadas a cada minuto."
"""
    return {"script": script}


# ---------- Agents ----------
async def _agent_chain(agent_id: str) -> List[dict]:
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
    chain = await _agent_chain(ag["id"])
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
                "note": "Agente em modo direto: o Bastion conecta diretamente em "
                        f"{a.get('host')}:{a.get('port', 22)} (ou através do agente pai). Nenhum instalador necessário — "
                        "apenas garanta que a chave global (ou usuário/senha) esteja autorizada nesse host."}
    s = await _bastion_settings()
    host, sport, suser = s.get("public_host") or "SEU_VPS_IP", s.get("ssh_port", 22), s.get("ssh_user", "bastion")
    tport, lport = a.get("tunnel_port"), a.get("port", 22)
    priv = vault.decrypt(a.get("agent_private_key", ""))
    ssh_opts = (f'-N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 '
                f'-o StrictHostKeyChecking=no -R {TUNNEL_BIND_HOST}:{tport}:localhost:{lport} -p {sport} {suser}@{host}')
    bash = f"""#!/usr/bin/env bash
# SSH Bastion Central — Agente Gateway "{a['name']}" (Linux/macOS)
# Abre um túnel SSH reverso persistente até o Bastion. Requisito: servidor SSH local ativo na porta {lport}
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
Description=SSH Bastion Central Agent ({a['name']})
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
echo "Agente {a['name']} -> {suser}@{host}:{sport} (túnel {TUNNEL_BIND_HOST}:{tport} no Bastion)."
"""
    powershell = f"""# SSH Bastion Central — Agente Gateway "{a['name']}" (Windows, PowerShell como Administrador)
# Requisito: OpenSSH Server instalado e ativo (Configurações > Aplicativos > Recursos opcionais > "Servidor OpenSSH"):
#   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
#   Set-Service sshd -StartupType Automatic; Start-Service sshd
$Dir = "$env:USERPROFILE\\.bastion-agent"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
@"
{priv}"@ | Set-Content -Path "$Dir\\agent_key" -Encoding ascii -NoNewline
icacls "$Dir\\agent_key" /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null
@"
while (`$true) {{
  ssh {ssh_opts} -o UserKnownHostsFile=NUL -i "$Dir\\agent_key"
  Start-Sleep -Seconds 5
}}
"@ | Set-Content -Path "$Dir\\tunnel.ps1"
$Action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Dir\\tunnel.ps1`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "BastionAgent" -Action $Action -Trigger $Trigger -Force | Out-Null
Start-ScheduledTask -TaskName "BastionAgent"
Write-Host "Agente {a['name']} iniciado -> {suser}@{host}:{sport} (túnel {TUNNEL_BIND_HOST}:{tport}). Verifique o status no painel."
"""
    return {"mode": "reverse", "bash": bash, "powershell": powershell, "tunnel_port": tport,
            "public_key": a.get("agent_public_key", ""),
            "note": "" if s.get("public_host") else "Configure o host público do Bastion em 'Configurar Bastion' antes de instalar."}


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


async def _get_device_for(user: dict, device_id: str) -> dict:
    d = await db.devices.find_one({"id": device_id, **_scope(user)}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    return d


@api.get("/devices")
async def list_devices(user: dict = Depends(get_current_user)):
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
async def get_ssh_key(_: dict = Depends(get_current_user)):
    doc = await db.config.find_one({"key": "ssh_key"}, {"_id": 0}) or {}
    return {
        "private_key": doc.get("private_key", ""),
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


scheduler = automation.Scheduler(db, _ping_everything, _backup_many)


async def _telegram_token() -> str:
    s = await automation.get_settings(db)
    return vault.decrypt(s.get("telegram_bot_token", ""))

assistant = ai_assistant.TelegramAssistant(db, _connect_device, _telegram_token)


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
    m = await db.maps.find_one({"id": map_id, "owner_id": user["id"]}, {"_id": 0})
    if not m:
        raise HTTPException(status_code=404, detail="Mapa não encontrado")
    return m


@api.get("/maps")
async def list_maps(user: dict = Depends(get_current_user)):
    maps = await db.maps.find({"owner_id": user["id"]}, {"_id": 0}).sort("name", 1).to_list(500)
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
    await _get_device_for(user, device_id)
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


# ---------- Óptica (RX/TX por lane via CLI) ----------
optics_collector = optics_mod.OpticsCollector(db, _connect_device)


@api.get("/optics/series")
async def optics_series(device_id: str, if_index: int, minutes: int = 1440, points: int = 300,
                        user: dict = Depends(get_current_user)):
    await _get_device_for(user, device_id)
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
    d = await db.dashboards.find_one({"id": dash_id, "owner_id": user["id"]}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Dashboard não encontrado")
    return d


@api.get("/dashboards")
async def list_dashboards(user: dict = Depends(get_current_user)):
    rows = await db.dashboards.find({"owner_id": user["id"]}, {"_id": 0}).to_list(1000)
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
        if wd["device_id"] not in mine:
            raise HTTPException(status_code=400, detail="Equipamento inválido no dashboard")
        if wd["type"] not in ("traffic", "optics"):
            raise HTTPException(status_code=400, detail="Tipo de widget inválido")
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



# ---------- Assistente IA (Claude via Telegram) ----------
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
    clear = data.pop("clear_api_key", False)
    key = (data.pop("anthropic_api_key", None) or "").strip()
    data["anthropic_api_key"] = "" if clear else (vault.encrypt(key) if key else existing.get("anthropic_api_key", ""))
    valid_users = {u["id"] for u in await db.users.find({}, {"_id": 0, "id": 1}).to_list(1000)}
    seen, users = set(), []
    for u in data.get("ai_users") or []:
        tid = re.sub(r"\D", "", str(u.get("telegram_id", "")))
        if not tid or tid in seen:
            continue
        if u.get("user_id") not in valid_users:
            raise HTTPException(status_code=400, detail=f"Usuário do Bastion inválido para o Telegram ID {tid}")
        seen.add(tid)
        users.append({"telegram_id": tid, "user_id": u["user_id"], "label": (u.get("label") or "").strip()})
    data["ai_users"] = users
    if data["ai_model"] not in {m[0] for m in ai_assistant.MODELS}:
        raise HTTPException(status_code=400, detail="Modelo inválido")
    await db.config.update_one({"key": "ai"}, {"$set": data}, upsert=True)
    return await get_ai_settings(_)


@api.post("/ai/test")
async def test_ai(_: dict = Depends(require_admin)):
    s = await ai_assistant.get_ai_settings(db)
    key = vault.decrypt(s.get("anthropic_api_key", ""))
    if not key:
        return {"ok": False, "error": "Chave da API não configurada"}
    try:
        r = await ai_assistant.call_claude(key, s["ai_model"], [{"role": "user", "content": "Responda apenas: OK"}],
                                           [{"type": "text", "text": "Teste de conexão."}], max_tokens=10)
        text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()
        return {"ok": True, "model": r.get("model"), "reply": text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
    await db.config.update_one({"key": "automation"}, {"$set": data}, upsert=True)
    return automation.public_settings(await automation.get_settings(db))


@api.post("/automation/test-alert")
async def test_alert(_: dict = Depends(require_admin)):
    res = await automation.send_alert(db, "✅ SSH Bastion Central", "Alerta de teste — canal de notificações funcionando.")
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
    return {"results": results}


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


# ---------- Health ----------
@api.get("/")
async def root():
    return {"ok": True, "service": "SSH Bastion Central"}


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
    await agent_pool.close_all()
    client.close()
