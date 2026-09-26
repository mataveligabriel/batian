"""Web Push (notificação no celular/PC pelo PWA) sem dependência extra.

Implementa o que o navegador exige:
  - VAPID (RFC 8292): JWT ES256 assinado com a chave do servidor, gerada uma vez e guardada no Mongo.
  - Criptografia da mensagem aes128gcm (RFC 8291 / RFC 8188) com a chave pública de cada aparelho.
Assinaturas ficam em db.push_subs; as que o serviço de push devolve 404/410 (app removido) são apagadas.
"""
import base64
import json
import logging
import os
import struct
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hmac import HMAC

logger = logging.getLogger("bastion.push")
RECORD_SIZE = 4096


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pub_bytes(pub: ec.EllipticCurvePublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hmac(key: bytes, data: bytes) -> bytes:
    h = HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = _hmac(salt, ikm)                       # extract
    return _hmac(prk, info + b"\x01")[:length]   # expand (1 bloco basta: length <= 32)


# ---------- chaves VAPID ----------
async def get_vapid(db) -> dict:
    doc = await db.config.find_one({"key": "vapid"}, {"_id": 0})
    if doc and doc.get("private_pem"):
        return doc
    priv = ec.generate_private_key(ec.SECP256R1())
    doc = {
        "key": "vapid",
        "private_pem": priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()).decode(),
        "public_key": b64u(_pub_bytes(priv.public_key())),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # upsert atômico: se dois processos gerarem ao mesmo tempo, fica a primeira
    await db.config.update_one({"key": "vapid"}, {"$setOnInsert": doc}, upsert=True)
    return await db.config.find_one({"key": "vapid"}, {"_id": 0})


def vapid_auth(endpoint: str, vapid: dict, subject: str) -> str:
    u = urlparse(endpoint)
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": subject},
                             separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    priv = serialization.load_pem_private_key(vapid["private_pem"].encode(), password=None)
    r, s = decode_dss_signature(priv.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{sig}, k={vapid['public_key']}"


# ---------- criptografia aes128gcm ----------
def encrypt(payload: bytes, p256dh: str, auth: str, salt: Optional[bytes] = None,
            server_key: Optional[ec.EllipticCurvePrivateKey] = None) -> bytes:
    ua_pub_raw = b64u_dec(p256dh)
    auth_secret = b64u_dec(auth)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_raw)
    as_priv = server_key or ec.generate_private_key(ec.SECP256R1())
    as_pub_raw = _pub_bytes(as_priv.public_key())
    shared = as_priv.exchange(ec.ECDH(), ua_pub)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_pub_raw + as_pub_raw, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(payload) > RECORD_SIZE - 17 - 86:
        raise ValueError("mensagem grande demais para push")
    cipher = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)   # 0x02 = último registro
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_pub_raw)) + as_pub_raw + cipher


def decrypt(body: bytes, ua_priv: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """Lado do navegador — usado só nos testes para provar que a mensagem abre."""
    salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
    as_pub_raw = body[21:21 + idlen]
    cipher = body[21 + idlen:]
    ua_pub_raw = _pub_bytes(ua_priv.public_key())
    shared = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub_raw))
    ikm = _hkdf(b64u_dec(auth), shared, b"WebPush: info\x00" + ua_pub_raw + as_pub_raw, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, cipher, None)
    return plain.rstrip(b"\x00")[:-1]


# ---------- envio ----------
def _subject() -> str:
    mail = (os.environ.get("ADMIN_EMAIL") or "").strip()
    if mail:
        return f"mailto:{mail}"
    return os.environ.get("PUBLIC_URL") or "mailto:admin@localhost"


async def send(db, title: str, body: str, url: str = "/", user_id: Optional[str] = None,
               tag: Optional[str] = None, client: Optional[httpx.AsyncClient] = None) -> dict:
    """Envia para todos os aparelhos inscritos (ou só os de `user_id`). Retorna contagem ok/falha."""
    q = {"user_id": user_id} if user_id else {}
    subs = [s async for s in db.push_subs.find(q, {"_id": 0})]
    if not subs:
        return {"sent": 0, "failed": 0, "removed": 0}
    vapid = await get_vapid(db)
    payload = json.dumps({"title": title, "body": body[:900], "url": url, "tag": tag,
                          "ts": int(time.time() * 1000)}, ensure_ascii=False).encode()
    sent = failed = removed = 0
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        for s in subs:
            try:
                data = encrypt(payload, s["keys"]["p256dh"], s["keys"]["auth"])
                r = await client.post(s["endpoint"], content=data, headers={
                    "Authorization": vapid_auth(s["endpoint"], vapid, _subject()),
                    "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                    "TTL": "86400", "Urgency": "high",
                })
                if r.status_code in (404, 410):          # aparelho desinstalou/revogou
                    await db.push_subs.delete_one({"endpoint": s["endpoint"]})
                    removed += 1
                elif r.status_code >= 300:
                    failed += 1
                    await db.push_subs.update_one({"endpoint": s["endpoint"]},
                                                  {"$set": {"last_error": f"HTTP {r.status_code}: {r.text[:160]}"}})
                else:
                    sent += 1
                    await db.push_subs.update_one({"endpoint": s["endpoint"]},
                                                  {"$set": {"last_ok": datetime.now(timezone.utc).isoformat(), "last_error": None}})
            except Exception as e:
                failed += 1
                logger.warning(f"push falhou ({urlparse(s['endpoint']).netloc}): {e}")
    finally:
        if own:
            await client.aclose()
    return {"sent": sent, "failed": failed, "removed": removed}
