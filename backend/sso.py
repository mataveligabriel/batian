"""Login único (SSO) por OpenID Connect: Google Workspace, Microsoft Entra ID, Keycloak, Authentik, Authelia…

Fluxo "authorization code" com PKCE (S256), state e nonce. O id_token é validado pela chave pública do provedor
(JWKS da discovery), conferindo emissor, audiência, validade e nonce. O e-mail vira o usuário do BastiON; grupos do
token podem definir o perfil (admin/operador/visualização) a cada login.
"""
import base64
import hashlib
import os
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urlencode

import jwt

PRESETS = {
    "google": {"name": "Google", "issuer": "https://accounts.google.com", "scopes": "openid email profile"},
    "microsoft": {"name": "Microsoft", "issuer": "https://login.microsoftonline.com/<TENANT_ID>/v2.0", "scopes": "openid email profile"},
    "keycloak": {"name": "Keycloak", "issuer": "https://sso.exemplo.net/realms/<REALM>", "scopes": "openid email profile"},
    "authentik": {"name": "Authentik", "issuer": "https://auth.exemplo.net/application/o/<SLUG>/", "scopes": "openid email profile"},
    "generic": {"name": "OpenID Connect", "issuer": "", "scopes": "openid email profile"},
}
ROLES = ("admin", "operator", "viewer")
# por usuário: default = segue a regra geral; both = senha e SSO; sso = só SSO; password = só senha
LOGIN_MODES = ("default", "both", "sso", "password")


def sso_allowed(user: dict, cfg: dict) -> bool:
    mode = user.get("login_mode") or "default"
    if mode == "password":
        return False
    if mode in ("both", "sso"):
        return True
    return (cfg.get("sso_users") or "all") == "all"


def password_allowed(user: dict, cfg: dict) -> bool:
    """O modo do usuário manda; sem modo próprio vale a regra geral ("senha só para administradores")."""
    mode = user.get("login_mode") or "default"
    if mode == "sso":
        return False
    if mode in ("both", "password"):
        return True
    return user.get("role") == "admin" or (cfg.get("password_login") or "all") != "admins"


class SsoError(Exception):
    pass


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def pkce() -> tuple:
    verifier = b64url(os.urandom(40))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def clean_provider(p: dict) -> dict:
    pid = re.sub(r"[^a-z0-9_-]", "", str(p.get("id") or "").lower())[:24]
    if not pid:
        raise SsoError("Identificador do provedor: letras minúsculas, números, - e _ (ex.: google)")
    issuer = str(p.get("issuer") or "").strip().rstrip("/")
    if not issuer.startswith("https://") or "<" in issuer:
        raise SsoError(f"{pid}: emissor (issuer) deve ser a URL https do provedor, sem os <…> do exemplo")
    cid = str(p.get("client_id") or "").strip()
    if not cid:
        raise SsoError(f"{pid}: informe o client ID")
    doms = [d.strip().lower().lstrip("@") for d in (p.get("allowed_domains") or []) if str(d).strip()]
    for d in doms:
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d):
            raise SsoError(f"{pid}: domínio inválido {d}")
    role = p.get("default_role") if p.get("default_role") in ROLES else "operator"
    return {"id": pid, "name": str(p.get("name") or pid).strip()[:40], "enabled": p.get("enabled", True) is not False,
            "issuer": issuer, "client_id": cid[:300], "scopes": str(p.get("scopes") or "openid email profile").strip()[:200],
            "allowed_domains": doms[:20], "auto_create": bool(p.get("auto_create")), "default_role": role,
            "groups_claim": str(p.get("groups_claim") or "groups").strip()[:60],
            "admin_groups": [str(g).strip() for g in (p.get("admin_groups") or []) if str(g).strip()][:50],
            "operator_groups": [str(g).strip() for g in (p.get("operator_groups") or []) if str(g).strip()][:50],
            "viewer_groups": [str(g).strip() for g in (p.get("viewer_groups") or []) if str(g).strip()][:50],
            "require_group": bool(p.get("require_group"))}


def auth_url(disc: dict, prov: dict, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
    q = {"response_type": "code", "client_id": prov["client_id"], "redirect_uri": redirect_uri, "scope": prov["scopes"],
         "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
    if "google" in prov["issuer"]:
        q["prompt"] = "select_account"
    return disc["authorization_endpoint"] + ("&" if "?" in disc["authorization_endpoint"] else "?") + urlencode(q)


def verify_id_token(id_token: str, jwks: dict, prov: dict, issuer: str, nonce: str, leeway: int = 60) -> dict:
    try:
        hdr = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as e:
        raise SsoError(f"id_token inválido: {e}")
    alg = hdr.get("alg") or "RS256"
    if alg not in ("RS256", "RS384", "RS512", "ES256", "ES384", "PS256"):
        raise SsoError(f"Algoritmo do id_token não aceito: {alg}")
    keys = jwks.get("keys") or []
    kid = hdr.get("kid")
    cand = [k for k in keys if not kid or k.get("kid") == kid] or keys
    last = None
    for k in cand:
        try:
            key = jwt.PyJWK(k, algorithm=alg).key
        except Exception as e:
            last = e
            continue
        try:
            claims = jwt.decode(id_token, key, algorithms=[alg], audience=prov["client_id"], leeway=leeway,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"], "verify_iss": False})
        except jwt.PyJWTError as e:
            last = e
            continue
        iss = claims.get("iss", "").rstrip("/")
        want = issuer.rstrip("/")
        # Microsoft "common/organizations": o emissor real tem o tenant no lugar de {tenantid}
        if iss != want and not (re.sub(r"\{tenantid\}", claims.get("tid", ""), want) == iss):
            raise SsoError(f"Emissor diferente do configurado: {iss}")
        if nonce and claims.get("nonce") != nonce:
            raise SsoError("nonce não confere (login repetido ou adulterado)")
        return claims
    raise SsoError(f"Assinatura do id_token não confere com as chaves do provedor ({last})")


def identity(claims: dict, userinfo: Optional[dict] = None) -> dict:
    c = {**(userinfo or {}), **claims}
    email = (c.get("email") or "").strip().lower()
    verified = c.get("email_verified")
    if not email:
        cand = (c.get("preferred_username") or c.get("upn") or "").strip().lower()       # Entra ID sem o claim email
        if "@" in cand:
            email, verified = cand, True
    if isinstance(verified, str):
        verified = verified.lower() == "true"
    return {"email": email, "verified": verified is not False, "name": (c.get("name") or c.get("given_name") or "").strip()[:80],
            "sub": c.get("sub", ""), "claims": c}


def groups_of(claims: dict, claim: str) -> List[str]:
    v = claims.get(claim)
    if v is None and claim == "groups":
        v = claims.get("roles")
    if isinstance(v, str):
        v = [v]
    return [str(x) for x in (v or [])]


def role_from_groups(prov: dict, groups: List[str]) -> Optional[str]:
    g = set(groups)
    if prov.get("admin_groups") and g & set(prov["admin_groups"]):
        return "admin"
    if prov.get("operator_groups") and g & set(prov["operator_groups"]):
        return "operator"
    if prov.get("viewer_groups") and g & set(prov["viewer_groups"]):
        return "viewer"
    return None


def domain_ok(prov: dict, email: str) -> bool:
    doms = prov.get("allowed_domains") or []
    return not doms or email.rsplit("@", 1)[-1] in doms


def now() -> int:
    return int(time.time())
