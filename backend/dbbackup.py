"""Backup e importação do banco do BastiON pela própria interface (Configurações → Banco de dados).

Arquivo .bastionbk: o conteúdo do Mongo (JSON estendido, uma linha por documento, comprimido) criptografado com
AES-256-GCM numa chave derivada da senha escolhida (scrypt). A senha é obrigatória porque o arquivo leva junto a
chave que abre as senhas dos equipamentos/agentes: sem ela, um BastiON instalado em outro servidor (outro
JWT_SECRET) não conseguiria ler essas senhas. Na importação, cada segredo é aberto com a chave do backup e
regravado com a chave deste servidor.

Formato (versão 1):
  b"BASTIONBK1\\n" + sal(16)
  blocos: tamanho(4, big-endian) + nonce(12) + texto cifrado com tag (AESGCM, AAD = número do bloco)
  bloco de tamanho 0 = fim (protege contra arquivo cortado)
Texto claro (gzip): 1ª linha = manifesto JSON; depois {"c": coleção, "d": documento} por linha.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import socket
import struct
import zlib
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional

from bson import json_util
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

log = logging.getLogger("bastion.dbbackup")

MAGIC = b"BASTIONBK1\n"
CHUNK = 1 << 20                      # 1 MiB de texto claro por bloco
BACKUP_DIR = os.environ.get("DB_BACKUP_DIR", "/data/db-backups")
JSON_OPTS = json_util.CANONICAL_JSON_OPTIONS
NAME_RE = re.compile(r"^\w[\w.-]*\.bastionbk$")   # não lista os envios temporários (.upload-*)

# grupos de coleções (o resto entra sempre: equipamentos, agentes, usuários, mapas, dashboards, configurações…)
HISTORY = {"sessions", "login_events", "alerts", "if_events", "ai_audit", "ai_usage", "ai_conversations",
           "lg_public_log", "rpki_log", "runbook_runs", "vpn_cmds", "net_reports_raw", "flow_mitigations", "flow_attacks"}
METRICS = {"if_samples", "optics_samples"}
METRICS_PREFIX = ("flow_",)          # flow_* (tráfego), menos o que é configuração do Flow
FLOW_CONFIG = {"flow_ifaces", "flow_groups", "flow_shares", "flow_exporters", "flow_botnet", "flow_templates"}
CONFIG_BACKUPS = {"backups"}         # backups de configuração dos equipamentos (texto das configs)
NEVER = {"login_fails", "peeringdb_cache", "asn_db", "import_raw", "flow_live", "vpn_status", "bgp_status",
         "system.profile"}


class BackupError(Exception):
    pass


def classify(col: str) -> str:
    if col in NEVER or col.startswith("system."):
        return "never"
    if col in CONFIG_BACKUPS:
        return "config_backups"
    if col in HISTORY:
        return "history"
    if col in METRICS or (col.startswith(METRICS_PREFIX) and col not in FLOW_CONFIG):
        return "metrics"
    return "core"


def pick(cols: Iterable[str], history: bool, metrics: bool, config_backups: bool) -> List[str]:
    want = {"core": True, "history": history, "metrics": metrics, "config_backups": config_backups, "never": False}
    return sorted(c for c in cols if want[classify(c)])


def _key(password: str, salt: bytes) -> bytes:
    if len(password or "") < 8:
        raise BackupError("A senha do backup precisa ter pelo menos 8 caracteres")
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(password.encode())


def vault_key() -> bytes:
    """Mesma chave do vault.py (derivada do JWT_SECRET deste servidor)."""
    return base64.urlsafe_b64encode(hashlib.sha256(os.environ["JWT_SECRET"].encode()).digest())


# ---------- escrita ----------
class _Writer:
    def __init__(self, f, password: str):
        salt = os.urandom(16)
        self.f, self.aes, self.n = f, AESGCM(_key(password, salt)), 0
        self.z = zlib.compressobj(6, zlib.DEFLATED, 31)
        self.buf = bytearray()
        f.write(MAGIC + salt)

    def _emit(self, data: bytes):
        nonce = os.urandom(12)
        ct = self.aes.encrypt(nonce, data, struct.pack(">Q", self.n))
        self.n += 1
        self.f.write(struct.pack(">I", len(ct)) + nonce + ct)

    def write(self, data: bytes):
        self.buf += self.z.compress(data)
        while len(self.buf) >= CHUNK:
            self._emit(bytes(self.buf[:CHUNK]))
            del self.buf[:CHUNK]

    def close(self):
        self.buf += self.z.flush()
        while self.buf:
            self._emit(bytes(self.buf[:CHUNK]))
            del self.buf[:CHUNK]
        # bloco final vazio (autenticado): sem ele o arquivo está incompleto
        nonce = os.urandom(12)
        ct = self.aes.encrypt(nonce, b"", struct.pack(">Q", self.n) + b"END")
        self.f.write(struct.pack(">I", 0) + nonce + ct)


# ---------- leitura ----------
def _read_lines(path: str, password: str):
    """Gera as linhas do texto claro. Senha errada ou arquivo alterado → BackupError."""
    with open(path, "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise BackupError("Este arquivo não é um backup do BastiON (.bastionbk)")
        salt = f.read(16)
        aes, n = AESGCM(_key(password, salt)), 0
        z = zlib.decompressobj(31)
        pend = b""
        while True:
            hdr = f.read(4)
            if len(hdr) < 4:
                raise BackupError("Arquivo incompleto (cortado no envio ou na cópia)")
            ln = struct.unpack(">I", hdr)[0]
            nonce = f.read(12)
            if ln == 0:
                try:
                    aes.decrypt(nonce, f.read(16), struct.pack(">Q", n) + b"END")
                except Exception:
                    raise BackupError("Final do arquivo inválido (arquivo alterado)")
                break
            ct = f.read(ln)
            if len(ct) < ln:
                raise BackupError("Arquivo incompleto (cortado no envio ou na cópia)")
            try:
                data = aes.decrypt(nonce, ct, struct.pack(">Q", n))
            except Exception:
                raise BackupError("Senha errada ou arquivo alterado" if n == 0 else "Arquivo alterado ou corrompido")
            n += 1
            pend += z.decompress(data)
            *lines, pend = pend.split(b"\n")
            for line in lines:
                if line:
                    yield line
        pend += z.flush()
        for line in pend.split(b"\n"):
            if line:
                yield line


def read_manifest(path: str, password: str) -> dict:
    for line in _read_lines(path, password):
        m = json.loads(line)
        if m.get("format") != "bastion-db" or int(m.get("v", 0)) != 1:
            raise BackupError("Versão de backup desconhecida")
        return m
    raise BackupError("Backup vazio")


# ---------- segredos ----------
def _reencrypt(value, old: Fernet, new: Fernet, stats: dict):
    """Abre com a chave do backup e regrava com a deste servidor qualquer texto que seja um token do vault."""
    if isinstance(value, str):
        if value.startswith("gAAAAA") and len(value) > 60:
            try:
                plain = old.decrypt(value.encode())
            except (InvalidToken, ValueError):
                return value
            stats["secrets"] = stats.get("secrets", 0) + 1
            return new.encrypt(plain).decode()
        return value
    if isinstance(value, dict):
        return {k: _reencrypt(v, old, new, stats) for k, v in value.items()}
    if isinstance(value, list):
        return [_reencrypt(v, old, new, stats) for v in value]
    return value


# ---------- operações ----------
def ensure_dir() -> str:
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    return BACKUP_DIR


def list_files() -> List[dict]:
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for name in os.listdir(BACKUP_DIR):
        if not NAME_RE.match(name):
            continue
        st = os.stat(os.path.join(BACKUP_DIR, name))
        meta = {}
        try:
            with open(os.path.join(BACKUP_DIR, name + ".json")) as f:
                meta = json.load(f)
        except Exception:
            pass
        out.append({"name": name, "size": st.st_size, "at": meta.get("created_at") or
                    datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(), **{k: meta.get(k) for k in
                    ("kind", "docs", "collections", "groups", "by")}})
    return sorted(out, key=lambda x: x["at"] or "", reverse=True)


def path_of(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise BackupError("Nome de arquivo inválido")
    p = os.path.join(BACKUP_DIR, name)
    if not os.path.isfile(p):
        raise BackupError("Backup não encontrado")
    return p


def remove(name: str):
    p = path_of(name)
    os.remove(p)
    try:
        os.remove(p + ".json")
    except FileNotFoundError:
        pass


def prune(keep: int, kind: str):
    files = [f for f in list_files() if f.get("kind") == kind]
    for f in files[keep:]:
        try:
            remove(f["name"])
        except Exception:
            pass


async def export(db, password: str, *, history=False, metrics=False, config_backups=True, kind="manual",
                 by: str = "", progress: Optional[Callable[[str, int], None]] = None) -> dict:
    """Grava BACKUP_DIR/bastion-db-AAAAMMDD-HHMMSS.bastionbk. Devolve os metadados."""
    _key(password, b"0" * 16)                    # valida a senha antes de começar
    ensure_dir()
    now = datetime.now(timezone.utc)
    cols = pick(await db.list_collection_names(), history, metrics, config_backups)
    counts = {c: await db[c].estimated_document_count() for c in cols}
    prefix = {"auto": "bastion-db-auto", "pre-import": "bastion-db-antes-importacao"}.get(kind, "bastion-db")
    name = f"{prefix}-{now.strftime('%Y%m%d-%H%M%S')}.bastionbk"
    final = os.path.join(BACKUP_DIR, name)
    tmp = final + ".part"
    manifest = {"format": "bastion-db", "v": 1, "created_at": now.isoformat(), "host": socket.gethostname(),
                "db": db.name, "collections": counts,
                "groups": {"history": history, "metrics": metrics, "config_backups": config_backups},
                "vault_key": vault_key().decode()}
    total = 0
    loop = asyncio.get_running_loop()
    try:
        with open(tmp, "wb") as f:
            os.chmod(tmp, 0o600)
            w = _Writer(f, password)
            w.write(json.dumps(manifest).encode() + b"\n")
            for c in cols:
                if progress:
                    progress(c, total)
                batch = []
                async for d in db[c].find({}):
                    batch.append(json_util.dumps({"c": c, "d": d}, json_options=JSON_OPTS).encode())
                    if len(batch) >= 500:
                        data = b"\n".join(batch) + b"\n"
                        await loop.run_in_executor(None, w.write, data)
                        total += len(batch)
                        batch = []
                if batch:
                    await loop.run_in_executor(None, w.write, b"\n".join(batch) + b"\n")
                    total += len(batch)
            await loop.run_in_executor(None, w.close)
        os.replace(tmp, final)
    except BaseException:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise
    meta = {"name": name, "created_at": now.isoformat(), "kind": kind, "docs": total, "collections": len(cols),
            "groups": manifest["groups"], "by": by, "size": os.path.getsize(final)}
    with open(final + ".json", "w") as f:
        json.dump(meta, f)
    log.info(f"backup do banco: {name} ({total} documentos, {meta['size']} bytes)")
    return meta


async def restore(db, path: str, password: str, progress: Optional[Callable[[str, int], None]] = None) -> dict:
    """Substitui cada coleção que está no backup pelo conteúdo dele (as que não estão ficam como estão).
    Os segredos são regravados com a chave deste servidor."""
    loop = asyncio.get_running_loop()
    manifest = await loop.run_in_executor(None, read_manifest, path, password)
    old = Fernet(manifest["vault_key"].encode())
    new = Fernet(vault_key())
    same_key = manifest["vault_key"].encode() == vault_key()
    stats: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    cleared = set()
    gen = _read_lines(path, password)
    next(gen)                                    # manifesto

    def take(n=500):
        out = []
        for line in gen:
            out.append(line)
            if len(out) >= n:
                break
        return out

    async def flush(col, docs):
        if col not in cleared:
            await db[col].delete_many({})        # delete_many mantém os índices (e o TTL) da coleção
            cleared.add(col)
        if docs:
            await db[col].insert_many(docs, ordered=False)
            counts[col] = counts.get(col, 0) + len(docs)

    total = 0
    while True:
        lines = await loop.run_in_executor(None, take)
        if not lines:
            break
        groups: Dict[str, list] = {}
        for line in lines:
            r = json_util.loads(line, json_options=JSON_OPTS)
            d = r["d"] if same_key else _reencrypt(r["d"], old, new, stats)
            groups.setdefault(r["c"], []).append(d)
        for col, docs in groups.items():
            if classify(col) == "never" or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", col):
                continue
            if progress:
                progress(col, total)
            await flush(col, docs)
            total += len(docs)
    for col in manifest.get("collections", {}):   # coleção vazia no backup: fica vazia aqui também
        if col not in cleared and classify(col) != "never" and re.fullmatch(r"[A-Za-z0-9_]{1,64}", col):
            await flush(col, [])
    return {"docs": total, "collections": counts, "secrets_reencrypted": stats.get("secrets", 0),
            "same_key": same_key, "from_host": manifest.get("host"), "from_date": manifest.get("created_at")}


# ---------- automático ----------
async def auto_loop(db, decrypt: Callable[[str], str], upload: Optional[Callable] = None, alert: Optional[Callable] = None):
    """Uma vez por dia, na hora escolhida, faz o backup automático e guarda os últimos N."""
    await asyncio.sleep(60)
    while True:
        try:
            s = await db.config.find_one({"key": "dbbackup"}, {"_id": 0}) or {}
            now = datetime.now().astimezone()
            today = now.strftime("%Y-%m-%d")
            if s.get("auto") and s.get("password") and now.hour == int(s.get("hour", 3)) and s.get("last_auto_day") != today:
                await db.config.update_one({"key": "dbbackup"}, {"$set": {"last_auto_day": today}})
                try:
                    meta = await export(db, decrypt(s["password"]), history=bool(s.get("history")), metrics=False,
                                        config_backups=bool(s.get("config_backups", True)), kind="auto", by="automático")
                    prune(int(s.get("keep", 7) or 7), "auto")
                    state = {"last_auto_at": meta["created_at"], "last_auto_ok": True, "last_auto_error": ""}
                    if s.get("cloud") and upload:
                        ok, err = await upload(os.path.join(BACKUP_DIR, meta["name"]))
                        state.update({"last_cloud_ok": ok, "last_cloud_error": err or ""})
                        if not ok and alert:
                            await alert("⚠️ Backup do banco não foi para a nuvem", err or "erro desconhecido")
                except Exception as e:
                    state = {"last_auto_at": datetime.now(timezone.utc).isoformat(), "last_auto_ok": False,
                             "last_auto_error": f"{type(e).__name__}: {e}"[:300]}
                    log.warning(f"backup automático do banco falhou: {e}")
                    if alert:
                        await alert("⚠️ Backup automático do banco falhou", state["last_auto_error"])
                await db.config.update_one({"key": "dbbackup"}, {"$set": state})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"agenda do backup do banco: {e}")
        await asyncio.sleep(300)
