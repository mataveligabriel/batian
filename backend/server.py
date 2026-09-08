"""SSH Bastion Central - FastAPI backend."""
from dotenv import load_dotenv
from pathlib import Path
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import re
import asyncio
import logging
from datetime import datetime, timezone
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
    UserCreate, UserOut, LoginPayload,
    AgentCreate, Agent,
    DeviceCreate, Device, DEVICE_TYPES,
    ScriptCreate, Script,
    Session, BatchExecPayload, BatchResultItem, SshKeyConfig, BastionSettings,
    AutomationSettings, BackupRunPayload,
)
from ssh_service import SSHClientWrapper, Hop, tcp_ping, LEGACY_TYPES
import vault
import automation

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
    await db.users.create_index("email", unique=True)
    await db.devices.create_index("name")
    await db.agents.create_index("name")
    await seed_admin()
    await seed_sample_data()
    await db.backups.create_index([("device_id", 1), ("created_at", -1)])
    scheduler.start()


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


@api.get("/agents")
async def list_agents(_: dict = Depends(get_current_user)):
    docs = await db.agents.find({}, {"_id": 0}).to_list(500)
    return [_public(d) for d in docs]


@api.post("/agents")
async def create_agent(payload: AgentCreate, _: dict = Depends(require_admin)):
    data = _apply_secret(payload.model_dump(), None)
    if data.get("parent_agent_id") == "":
        data["parent_agent_id"] = None
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
async def update_agent(agent_id: str, payload: AgentCreate, _: dict = Depends(require_admin)):
    existing = await db.agents.find_one({"id": agent_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Agente não encontrado")
    if payload.parent_agent_id == agent_id:
        raise HTTPException(status_code=400, detail="Agente não pode ser pai de si mesmo")
    data = _apply_secret(payload.model_dump(), existing)
    if data.get("parent_agent_id") == "":
        data["parent_agent_id"] = None
    if data["mode"] == "reverse":
        data["tunnel_port"] = int(data.get("tunnel_port") or existing.get("tunnel_port") or await _next_tunnel_port())
        if not existing.get("agent_public_key"):
            priv, pub = _gen_agent_keypair()
            data["agent_private_key"] = vault.encrypt(priv)
            data["agent_public_key"] = pub
    await db.agents.update_one({"id": agent_id}, {"$set": data})
    return _public(await db.agents.find_one({"id": agent_id}, {"_id": 0}))


@api.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, _: dict = Depends(require_admin)):
    r = await db.agents.delete_one({"id": agent_id})
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
async def ping_agent(agent_id: str, _: dict = Depends(get_current_user)):
    a = await db.agents.find_one({"id": agent_id}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Agente não encontrado")
    lat = await _ping_agent(a)
    status = "online" if lat is not None else "offline"
    now = datetime.now(timezone.utc).isoformat()
    await db.agents.update_one({"id": agent_id}, {"$set": {"status": status, "latency_ms": lat, "last_seen": now if lat else a.get("last_seen")}})
    return {"agent_id": agent_id, "status": status, "latency_ms": lat}


@api.post("/agents/{agent_id}/test")
async def test_agent(agent_id: str, _: dict = Depends(get_current_user)):
    """Full SSH login through the chain up to this agent."""
    a = await db.agents.find_one({"id": agent_id}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Agente não encontrado")
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
async def install_script(agent_id: str, _: dict = Depends(get_current_user)):
    a = await db.agents.find_one({"id": agent_id}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Agente não encontrado")
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
def _normalize_device(data: dict) -> dict:
    if data.get("agent_id") == "":
        data["agent_id"] = None
    if data.get("device_type") not in DEVICE_TYPES:
        data["device_type"] = "other"
    return data


@api.get("/devices")
async def list_devices(_: dict = Depends(get_current_user)):
    docs = await db.devices.find({}, {"_id": 0}).to_list(5000)
    return [_public(d) for d in docs]


@api.post("/devices")
async def create_device(payload: DeviceCreate, _: dict = Depends(get_current_user)):
    data = _normalize_device(_apply_secret(payload.model_dump(), None))
    doc = Device(**data).model_dump()
    await db.devices.insert_one(doc)
    return _public(doc)


@api.post("/devices/import")
async def import_devices(payload: dict, _: dict = Depends(get_current_user)):
    """Bulk import. payload = {"rows": [{name, host, port, username, password, device_type, tags, agent, description}]}"""
    rows = payload.get("rows") or []
    agents = await db.agents.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(1000)
    agent_by_name = {a["name"].strip().lower(): a["id"] for a in agents}
    agent_ids = {a["id"] for a in agents}
    existing = await db.devices.find({}, {"_id": 0, "name": 1, "host": 1, "port": 1}).to_list(10000)
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
        doc = Device(**_normalize_device({
            "name": name, "host": host, "port": port,
            "username": str(r.get("username") or r.get("user") or "").strip(),
            "password": vault.encrypt(pw) if pw else "",
            "device_type": dtype, "tags": tags, "agent_id": agent_id,
            "description": str(r.get("description") or "").strip(),
        })).model_dump()
        to_insert.append(doc)
        seen.add(key)
        created += 1
    if to_insert:
        await db.devices.insert_many(to_insert)
    return {"created": created, "skipped": skipped, "errors": errors}


@api.put("/devices/{device_id}")
async def update_device(device_id: str, payload: DeviceCreate, _: dict = Depends(get_current_user)):
    existing = await db.devices.find_one({"id": device_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    data = _normalize_device(_apply_secret(payload.model_dump(), existing))
    await db.devices.update_one({"id": device_id}, {"$set": data})
    return _public(await db.devices.find_one({"id": device_id}, {"_id": 0}))


@api.delete("/devices/{device_id}")
async def delete_device(device_id: str, _: dict = Depends(get_current_user)):
    r = await db.devices.delete_one({"id": device_id})
    return {"deleted": r.deleted_count}


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
async def ping_device(device_id: str, _: dict = Depends(get_current_user)):
    d = await db.devices.find_one({"id": device_id}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    lat = await _ping_device(d)
    status = "online" if lat is not None else "offline"
    now = datetime.now(timezone.utc).isoformat()
    await db.devices.update_one({"id": device_id}, {"$set": {"status": status, "latency_ms": lat, "last_seen": now if lat else d.get("last_seen")}})
    return {"device_id": device_id, "status": status, "latency_ms": lat}


@api.post("/devices/ping-all")
async def ping_all_devices(_: dict = Depends(get_current_user)):
    devs = await db.devices.find({}, {"_id": 0}).to_list(5000)
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
async def list_sessions(_: dict = Depends(get_current_user), limit: int = 100):
    return await db.sessions.find({}, {"_id": 0}).sort("started_at", -1).to_list(limit)


# ---------- Dashboard stats ----------
@api.get("/stats")
async def stats(_: dict = Depends(get_current_user)):
    total_devices = await db.devices.count_documents({})
    online_devices = await db.devices.count_documents({"status": "online"})
    offline_devices = await db.devices.count_documents({"status": "offline"})
    total_agents = await db.agents.count_documents({})
    online_agents = await db.agents.count_documents({"status": "online"})
    sessions_today = await db.sessions.count_documents({
        "started_at": {"$gte": datetime.now(timezone.utc).date().isoformat()}
    })
    recent = await db.sessions.find({}, {"_id": 0}).sort("started_at", -1).to_list(8)
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


async def _connect_device(dev: dict) -> SSHClientWrapper:
    hops, dtype = await _device_hops(dev)
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
        dev = await db.devices.find_one({"id": dev_id}, {"_id": 0})
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


@api.get("/automation/settings")
async def get_automation_settings(_: dict = Depends(get_current_user)):
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
async def ping_now(_: dict = Depends(get_current_user)):
    if scheduler.busy:
        return {"started": False, "reason": "Ping já em andamento"}
    asyncio.create_task(scheduler.tick(force_ping=True))
    return {"started": True}


@api.get("/alerts")
async def list_alerts(_: dict = Depends(get_current_user), limit: int = 50):
    return await db.alerts.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)


@api.post("/backups/run")
async def run_backups(payload: BackupRunPayload, user: dict = Depends(get_current_user)):
    results = await _backup_many(payload.device_ids)
    return {"results": results}


@api.get("/backups")
async def list_backups(_: dict = Depends(get_current_user), device_id: Optional[str] = None, limit: int = 200):
    q = {"device_id": device_id} if device_id else {}
    return await db.backups.find(q, {"_id": 0, "content": 0}).sort("created_at", -1).to_list(limit)


@api.get("/backups/summary")
async def backups_summary(_: dict = Depends(get_current_user)):
    pipeline = [
        {"$sort": {"created_at": -1}},
        {"$group": {"_id": "$device_id", "device_name": {"$first": "$device_name"}, "device_type": {"$first": "$device_type"},
                    "last_at": {"$first": "$created_at"}, "last_ok": {"$first": "$ok"}, "last_error": {"$first": "$error"},
                    "count": {"$sum": 1}, "ok_count": {"$sum": {"$cond": ["$ok", 1, 0]}}}},
        {"$sort": {"device_name": 1}},
    ]
    rows = await db.backups.aggregate(pipeline).to_list(5000)
    return [{"device_id": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in rows]


@api.get("/backups/{backup_id}")
async def get_backup(backup_id: str, _: dict = Depends(get_current_user)):
    b = await db.backups.find_one({"id": backup_id}, {"_id": 0})
    if not b:
        raise HTTPException(status_code=404, detail="Backup não encontrado")
    return b


@api.get("/backups/{backup_id}/diff/{other_id}")
async def diff_backups(backup_id: str, other_id: str, _: dict = Depends(get_current_user)):
    a = await db.backups.find_one({"id": other_id}, {"_id": 0})
    b = await db.backups.find_one({"id": backup_id}, {"_id": 0})
    if not a or not b:
        raise HTTPException(status_code=404, detail="Backup não encontrado")
    diff = automation.unified_diff(a.get("content", ""), b.get("content", ""),
                                   f"{a['device_name']} @ {a['created_at']}", f"{b['device_name']} @ {b['created_at']}")
    return {"diff": diff, "identical": a.get("sha256") == b.get("sha256")}


@api.delete("/backups/{backup_id}")
async def delete_backup(backup_id: str, _: dict = Depends(require_admin)):
    r = await db.backups.delete_one({"id": backup_id})
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

    dev = await db.devices.find_one({"id": device_id}, {"_id": 0})
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
        await ws.send_json({"type": "status", "message": f"Conectando em {dev['name']} ({dev['host']}:{dev.get('port',22)})..."})
        wrapper = await _connect_device(dev)
        await ws.send_json({"type": "status", "message": "Conectado. Sessão SSH ativa."})
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
    client.close()
