"""Auth utilities: bcrypt hashing + JWT + role checks."""
import os
import re
import jwt
import bcrypt
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, Request, Depends, status
from typing import Optional

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXP_HOURS = 12


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _secret() -> str:
    return os.environ["JWT_SECRET"]


def create_access_token(user_id: str, email: str, role: str, token_version: int = 0) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "type": "access",
        "tv": int(token_version or 0),   # sobe ao trocar senha / 2FA / "encerrar sessões": tokens antigos deixam de valer
        "exp": datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXP_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")


def extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    # allow token as query param (for WebSocket)
    return request.query_params.get("token")


ROLES = ("admin", "operator", "viewer")

# Carrega o usuário do banco a cada requisição (definido pelo server.py): papel alterado ou usuário
# excluído vale na hora, sem esperar o token expirar.
USER_LOADER = None

# Perfil "viewer" (View): só Painel NOC, Dashboards e Mapas — e só leitura. Tudo fora desta lista é negado.
VIEWER_ALLOW = [
    ("GET", r"/api/auth/me"), ("POST", r"/api/auth/logout"), ("POST", r"/api/auth/change-password"),
    ("GET", r"/api/auth/2fa"), ("POST", r"/api/auth/2fa/(setup|enable|disable|recovery-codes)"),
    ("POST", r"/api/auth/logout-all"), ("GET", r"/api/auth/logins"),
    ("GET", r"/api/stats"),
    ("GET", r"/api/devices"),
    ("GET", r"/api/maps"), ("GET", r"/api/maps/[\w-]+"), ("GET", r"/api/maps/[\w-]+/live"),
    ("GET", r"/api/dashboards"), ("GET", r"/api/dashboards/[\w-]+"),
    ("GET", r"/api/monitor/series"), ("POST", r"/api/monitor/series-multi"), ("GET", r"/api/optics/series"),
    ("GET", r"/api/noc/layout"), ("PUT", r"/api/noc/layout"),
    ("GET", r"/api/push/public-key"), ("POST", r"/api/push/subscribe"), ("POST", r"/api/push/unsubscribe"),
    ("POST", r"/api/push/test"), ("GET", r"/api/push/devices"),
]
_VIEWER_RE = [(m, re.compile(p + r"/?$")) for m, p in VIEWER_ALLOW]


def viewer_allowed(method: str, path: str) -> bool:
    return any(m == method.upper() and rx.match(path) for m, rx in _VIEWER_RE)


async def get_current_user(request: Request) -> dict:
    token = extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Não autenticado")
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Tipo de token inválido")
    user = {
        "id": payload["sub"],
        "email": payload["email"],
        "role": payload.get("role", "operator"),
    }
    if USER_LOADER is not None:
        u = await USER_LOADER(payload["sub"])
        if not u:
            raise HTTPException(status_code=401, detail="Usuário não existe mais")
        if int(payload.get("tv", 0)) != int(u.get("token_version", 0)):
            raise HTTPException(status_code=401, detail="Sessão encerrada — entre novamente")
        user = {"id": u["id"], "email": u["email"], "name": u.get("name", ""), "role": u.get("role") or "operator",
                "view_maps": u.get("view_maps") or [], "view_dashboards": u.get("view_dashboards") or []}
    if user["role"] == "viewer" and not viewer_allowed(request.method, request.url.path):
        raise HTTPException(status_code=403, detail="Perfil de visualização: acesso só ao Painel NOC, Dashboards e Mapas")
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    return user
