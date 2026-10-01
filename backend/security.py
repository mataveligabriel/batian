"""Segurança do login: TOTP (2FA), códigos de recuperação, limite de tentativas e registro de acessos."""
import base64
import hashlib
import hmac
import os
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from urllib.parse import quote

ISSUER = "BastiON"
STEP = 30
WINDOW = timedelta(minutes=15)
MAX_FAILS_EMAIL = 5         # por e-mail em 15 min
MAX_FAILS_IP = 20           # por IP em 15 min
_RC_ALPHA = "abcdefghjkmnpqrstuvwxyz23456789"   # sem 0/o, 1/l/i


# ---------------- TOTP (RFC 6238) ----------------
def new_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp(secret: str, t: Optional[float] = None) -> str:
    return _code(secret, int((t or time.time()) // STEP))


def verify_totp(secret: str, code: str, last_step: int = -1, t: Optional[float] = None) -> Optional[int]:
    """Aceita o código atual, o anterior e o próximo (relógio do celular adiantado/atrasado).
    Devolve o passo usado (para impedir reuso) ou None."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6 or not secret:
        return None
    now = int((t or time.time()) // STEP)
    for step in (now, now - 1, now + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str) -> str:
    return (f"otpauth://totp/{quote(ISSUER)}:{quote(account)}?secret={secret}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits=6&period={STEP}")


# ---------------- códigos de recuperação ----------------
def _hash_rc(code: str) -> str:
    c = "".join(ch for ch in code.lower() if ch.isalnum())
    return hashlib.sha256(("bastion-rc:" + c).encode()).hexdigest()


def new_recovery_codes(n: int = 10) -> Tuple[List[str], List[str]]:
    codes = []
    for _ in range(n):
        raw = "".join(_RC_ALPHA[b % len(_RC_ALPHA)] for b in os.urandom(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes, [_hash_rc(c) for c in codes]


def match_recovery(code: str, hashes: List[str]) -> Optional[str]:
    """Devolve o hash que casou (para removê-lo) ou None."""
    if not code or len("".join(ch for ch in code if ch.isalnum())) != 8:
        return None
    h = _hash_rc(code)
    for x in hashes or []:
        if hmac.compare_digest(x, h):
            return x
    return None


# ---------------- limite de tentativas ----------------
async def locked_for(db, email: str, ip: str) -> int:
    """Segundos até liberar (0 = pode tentar)."""
    since = datetime.now(timezone.utc) - WINDOW
    wait = 0
    for key, limit in ((f"e:{email}", MAX_FAILS_EMAIL), (f"i:{ip}", MAX_FAILS_IP)):
        fails = await db.login_fails.find({"key": key, "at": {"$gte": since}}, {"_id": 0, "at": 1}) \
            .sort("at", -1).to_list(limit)
        if len(fails) >= limit:
            at = fails[limit - 1]["at"]
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            wait = max(wait, int((at + WINDOW - datetime.now(timezone.utc)).total_seconds()) + 1)
    return max(0, wait)


async def record_fail(db, email: str, ip: str) -> int:
    """Registra a falha; devolve quantas falhas o e-mail tem na janela."""
    now = datetime.now(timezone.utc)
    await db.login_fails.insert_many([{"key": f"e:{email}", "at": now}, {"key": f"i:{ip}", "at": now}])
    return await db.login_fails.count_documents({"key": f"e:{email}", "at": {"$gte": now - WINDOW}})


async def clear_fails(db, email: str):
    await db.login_fails.delete_many({"key": f"e:{email}"})


async def log_event(db, email: str, ip: str, ua: str, ok: bool, reason: str = "", user_id: Optional[str] = None):
    now = datetime.now(timezone.utc)
    await db.login_events.insert_one({
        "id": os.urandom(8).hex(), "ts": now, "at": now.isoformat(), "email": email, "user_id": user_id,
        "ip": ip, "ua": (ua or "")[:200], "ok": ok, "reason": reason,
    })


async def ensure_indexes(db):
    await db.login_fails.create_index("at", expireAfterSeconds=3600)
    await db.login_fails.create_index([("key", 1), ("at", -1)])
    await db.login_events.create_index("ts", expireAfterSeconds=90 * 86400)
    await db.login_events.create_index([("user_id", 1), ("ts", -1)])


def client_ip(request) -> str:
    """IP real do cliente. O Caddy (mesma máquina) repassa em X-Forwarded-For; só confiamos nele vindo de 127.0.0.1."""
    peer = (request.client.host if getattr(request, "client", None) else "") or ""
    if peer in ("127.0.0.1", "::1", ""):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()[:64]
        xr = request.headers.get("x-real-ip", "")
        if xr:
            return xr.strip()[:64]
    return peer[:64] or "?"
