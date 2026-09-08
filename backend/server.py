"""SSH Bastion Central - FastAPI backend."""
from dotenv import load_dotenv
from pathlib import Path
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from auth import (
    hash_password, verify_password, create_access_token, decode_token,
    get_current_user, require_admin,
)
from models import (
    UserCreate, UserOut, LoginPayload,
    AgentCreate, Agent,
    DeviceCreate, Device,
    ScriptCreate, Script,
    Session, BatchExecPayload, BatchResultItem, SshKeyConfig,
)
from ssh_service import SSHClientWrapper, tcp_ping

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


# ---------- Agents ----------
@api.get("/agents")
async def list_agents(_: dict = Depends(get_current_user)):
    return await db.agents.find({}, {"_id": 0}).to_list(500)


@api.post("/agents")
async def create_agent(payload: AgentCreate, _: dict = Depends(require_admin)):
    a = Agent(**payload.model_dump())
    doc = a.model_dump()
    await db.agents.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.put("/agents/{agent_id}")
async def update_agent(agent_id: str, payload: AgentCreate, _: dict = Depends(require_admin)):
    res = await db.agents.update_one({"id": agent_id}, {"$set": payload.model_dump()})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Agente não encontrado")
    return await db.agents.find_one({"id": agent_id}, {"_id": 0})


@api.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, _: dict = Depends(require_admin)):
    r = await db.agents.delete_one({"id": agent_id})
    return {"deleted": r.deleted_count}


@api.post("/agents/{agent_id}/ping")
async def ping_agent(agent_id: str, _: dict = Depends(get_current_user)):
    a = await db.agents.find_one({"id": agent_id}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Agente não encontrado")
    lat = await tcp_ping(a["host"], a.get("port", 22))
    status = "online" if lat is not None else "offline"
    now = datetime.now(timezone.utc).isoformat()
    await db.agents.update_one({"id": agent_id}, {"$set": {"status": status, "latency_ms": lat, "last_seen": now if lat else a.get("last_seen")}})
    return {"agent_id": agent_id, "status": status, "latency_ms": lat}


@api.get("/agents/{agent_id}/install-script")
async def install_script(agent_id: str, _: dict = Depends(get_current_user)):
    a = await db.agents.find_one({"id": agent_id}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Agente não encontrado")
    key = await db.config.find_one({"key": "ssh_key"}) or {}
    script = f"""#!/usr/bin/env bash
# SSH Bastion Central - Instalador do Agente Remoto ({a['name']})
# Cria túnel reverso persistente até o Bastion Central
set -e
BASTION_HOST="{os.environ.get('BASTION_HOST','bastion.example.com')}"
BASTION_PORT={os.environ.get('BASTION_PORT','22')}
BASTION_USER="{os.environ.get('BASTION_USER','bastion')}"
AGENT_ID="{a['id']}"
mkdir -p /etc/bastion-agent
cat > /etc/bastion-agent/tunnel.sh <<'EOF'
#!/usr/bin/env bash
autossh -M 0 -N -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" \\
  -R 0.0.0.0:0:localhost:22 \\
  $BASTION_USER@$BASTION_HOST -p $BASTION_PORT
EOF
chmod +x /etc/bastion-agent/tunnel.sh
echo "Agente {a['name']} configurado. Rode /etc/bastion-agent/tunnel.sh como serviço."
"""
    return {"script": script}


# ---------- Devices ----------
@api.get("/devices")
async def list_devices(_: dict = Depends(get_current_user)):
    return await db.devices.find({}, {"_id": 0}).to_list(1000)


@api.post("/devices")
async def create_device(payload: DeviceCreate, _: dict = Depends(get_current_user)):
    d = Device(**payload.model_dump())
    doc = d.model_dump()
    await db.devices.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.put("/devices/{device_id}")
async def update_device(device_id: str, payload: DeviceCreate, _: dict = Depends(get_current_user)):
    res = await db.devices.update_one({"id": device_id}, {"$set": payload.model_dump()})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    return await db.devices.find_one({"id": device_id}, {"_id": 0})


@api.delete("/devices/{device_id}")
async def delete_device(device_id: str, _: dict = Depends(get_current_user)):
    r = await db.devices.delete_one({"id": device_id})
    return {"deleted": r.deleted_count}


@api.post("/devices/{device_id}/ping")
async def ping_device(device_id: str, _: dict = Depends(get_current_user)):
    d = await db.devices.find_one({"id": device_id}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    lat = await tcp_ping(d["host"], d.get("port", 22))
    status = "online" if lat is not None else "offline"
    now = datetime.now(timezone.utc).isoformat()
    await db.devices.update_one({"id": device_id}, {"$set": {"status": status, "latency_ms": lat, "last_seen": now if lat else d.get("last_seen")}})
    return {"device_id": device_id, "status": status, "latency_ms": lat}


@api.post("/devices/ping-all")
async def ping_all_devices(_: dict = Depends(get_current_user)):
    devs = await db.devices.find({}, {"_id": 0}).to_list(1000)
    results = []
    async def _one(dv):
        lat = await tcp_ping(dv["host"], dv.get("port", 22))
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
    }


@api.put("/ssh-key")
async def set_ssh_key(payload: SshKeyConfig, _: dict = Depends(require_admin)):
    await db.config.update_one(
        {"key": "ssh_key"},
        {"$set": {
            "private_key": payload.private_key,
            "public_key": payload.public_key,
            "default_username": payload.default_username,
        }},
        upsert=True,
    )
    return {"ok": True, "has_key": bool(payload.private_key)}


@api.post("/ssh-key/generate")
async def generate_ssh_key(_: dict = Depends(require_admin)):
    import asyncssh
    key = asyncssh.generate_private_key("ssh-rsa", key_size=2048)
    priv = key.export_private_key().decode()
    pub = key.export_public_key().decode()
    await db.config.update_one(
        {"key": "ssh_key"},
        {"$set": {"private_key": priv, "public_key": pub}},
        upsert=True,
    )
    return {"private_key": priv, "public_key": pub}


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
    return doc.get("private_key", ""), doc.get("default_username", "root")


async def _connect_device(dev: dict) -> SSHClientWrapper:
    priv, default_user = await _get_ssh_key()
    jump_host = jump_port = jump_user = None
    if dev.get("agent_id"):
        ag = await db.agents.find_one({"id": dev["agent_id"]}, {"_id": 0})
        if ag:
            jump_host = ag.get("host")
            jump_port = ag.get("port", 22)
            jump_user = ag.get("username", "bastion")
    wrapper = SSHClientWrapper(
        host=dev["host"], port=dev.get("port", 22),
        username=dev.get("username") or default_user,
        private_key=priv or None,
        jump_host=jump_host, jump_port=jump_port or 22, jump_user=jump_user,
    )
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
    client.close()
