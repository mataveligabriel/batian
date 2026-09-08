"""Pydantic models for MongoDB documents. Uses uuid strings as ids."""
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import List, Optional
from datetime import datetime, timezone
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Users ----------
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: str = "operator"  # 'admin' | 'operator'


class UserOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    email: str
    name: str
    role: str
    created_at: Optional[str] = None


class LoginPayload(BaseModel):
    email: EmailStr
    password: str


# ---------- Agents (Bastion proxies) ----------
class AgentCreate(BaseModel):
    name: str
    location: str = ""
    host: str  # host that agent is reachable from (bastion side)
    port: int = 22
    username: str = "root"
    description: str = ""


class Agent(AgentCreate):
    id: str = Field(default_factory=_uuid)
    status: str = "unknown"  # online | offline | unknown
    last_seen: Optional[str] = None
    latency_ms: Optional[float] = None
    created_at: str = Field(default_factory=_now_iso)


# ---------- Devices ----------
class DeviceCreate(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str = "root"
    tags: List[str] = []
    agent_id: Optional[str] = None  # reference to Agent for ProxyJump
    description: str = ""


class Device(DeviceCreate):
    id: str = Field(default_factory=_uuid)
    status: str = "unknown"
    last_seen: Optional[str] = None
    latency_ms: Optional[float] = None
    created_at: str = Field(default_factory=_now_iso)


# ---------- Scripts ----------
class ScriptCreate(BaseModel):
    name: str
    description: str = ""
    content: str  # shell script


class Script(ScriptCreate):
    id: str = Field(default_factory=_uuid)
    created_at: str = Field(default_factory=_now_iso)


# ---------- Sessions (audit) ----------
class Session(BaseModel):
    id: str = Field(default_factory=_uuid)
    user_id: str
    user_email: str
    device_id: str
    device_name: str
    started_at: str = Field(default_factory=_now_iso)
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    kind: str = "terminal"  # terminal | batch


# ---------- Batch Execution ----------
class BatchExecPayload(BaseModel):
    device_ids: List[str]
    script_id: Optional[str] = None
    inline_command: Optional[str] = None
    timeout: int = 60


class BatchResultItem(BaseModel):
    device_id: str
    device_name: str
    host: str
    ok: bool
    exit_status: int
    stdout: str
    stderr: str
    error: Optional[str] = None


# ---------- SSH Key config ----------
class SshKeyConfig(BaseModel):
    private_key: str = ""
    public_key: str = ""
    default_username: str = "root"
