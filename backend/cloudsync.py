"""Backups na nuvem: copia os backups de configuração das tags escolhidas para um drive (Google Drive, OneDrive,
Dropbox, S3…) usando o rclone.

  banco do BastiON ──► /data/cloud/<TAG>/<equipamento>/<data>.cfg ──rclone copy──► drive:<pasta>/<TAG>/…

- Só copia (nunca apaga nada no drive). Cada backup vai uma vez por tag; o que já foi fica marcado no banco.
- Por padrão vão só as versões em que a configuração mudou (e a primeira de cada equipamento).
- A autorização do drive é feita uma vez no servidor (deploy/cloud-auth.sh) e fica em /data/rclone/rclone.conf.
"""
import asyncio
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional, Tuple

log = logging.getLogger("bastion.cloud")

DATA_DIR = os.environ.get("BASTION_DATA_DIR", "/data")
RCLONE_CONF = os.environ.get("RCLONE_CONFIG", os.path.join(DATA_DIR, "rclone", "rclone.conf"))
STAGE_DIR = os.path.join(DATA_DIR, "cloud")
DEFAULTS = {"enabled": False, "remote": "", "folder": "BastiON", "tags": [], "only_changed": True}
MAX_PER_RUN = 5000
_lock = asyncio.Lock()
_SAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_name(s: str) -> str:
    """Nome de pasta/arquivo aceito por qualquer drive."""
    return _SAFE.sub("_", (s or "").strip()).strip(" .")[:120] or "sem-nome"


def rclone_bin() -> Optional[str]:
    return shutil.which("rclone")


async def _rclone(*args: str, timeout: float = 600) -> Tuple[int, str]:
    """Roda o rclone; devolve (código, saída). Código -1 = não instalado / tempo esgotado."""
    exe = rclone_bin()
    if not exe:
        return -1, "rclone não está instalado no servidor (rode o update.sh)"
    os.makedirs(os.path.dirname(RCLONE_CONF), exist_ok=True)
    proc = await asyncio.create_subprocess_exec(exe, "--config", RCLONE_CONF, *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, f"tempo esgotado ({int(timeout)} s)"
    return proc.returncode, out.decode("utf-8", "replace").strip()


def _short(err: str) -> str:
    """Última linha útil do rclone, sem data/hora."""
    lines = [re.sub(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\s+", "", l).strip() for l in err.splitlines() if l.strip()]
    msg = (lines[-1] if lines else "erro desconhecido")[:300]
    if "didn't find section in config file" in err or "not found in config" in err:
        return "este drive ainda não foi autorizado no servidor (rode o deploy/cloud-auth.sh)"
    if "invalid_grant" in err or "token expired" in err.lower():
        return "a autorização do drive expirou — rode o deploy/cloud-auth.sh de novo"
    return msg


async def remotes() -> List[str]:
    code, out = await _rclone("listremotes", timeout=20)
    return [l.strip().rstrip(":") for l in out.splitlines() if l.strip().endswith(":")] if code == 0 else []


async def get_settings(db) -> dict:
    doc = await db.config.find_one({"key": "cloud"}, {"_id": 0}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if k != "key"}}


async def status(db) -> dict:
    s = await get_settings(db)
    st = await db.config.find_one({"key": "cloud_state"}, {"_id": 0}) or {}
    tags = sorted({t for d in await db.devices.find({}, {"_id": 0, "tags": 1}).to_list(5000) for t in (d.get("tags") or [])},
                  key=str.lower)
    return {"settings": s, "rclone": bool(rclone_bin()), "remotes": await remotes(), "all_tags": tags,
            "running": _lock.locked(), "last": {k: v for k, v in st.items() if k != "key"}}


async def test(remote: str, folder: str) -> dict:
    """Cria a pasta de destino (se não existir) e lista: confirma que a autorização vale."""
    remote, folder = remote.strip().rstrip(":"), folder.strip().strip("/")
    if not remote:
        return {"ok": False, "error": "Escolha o drive"}
    code, out = await _rclone("mkdir", f"{remote}:{folder}", timeout=60)
    if code != 0:
        return {"ok": False, "error": _short(out)}
    code, out = await _rclone("lsf", f"{remote}:{folder}", "--max-depth", "1", timeout=60)
    if code != 0:
        return {"ok": False, "error": _short(out)}
    return {"ok": True, "items": [l for l in out.splitlines() if l.strip()][:30]}


def _stamp(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    except Exception:
        return re.sub(r"[^0-9T-]", "-", iso)[:19]


async def run(db, alert: Optional[Callable[..., Awaitable]] = None, trigger: str = "manual", force: bool = False) -> dict:
    """Envia o que ainda não foi. Devolve {ok, sent, tags:{tag: n}, error?}."""
    s = await get_settings(db)
    if not force and not s.get("enabled"):
        return {"ok": True, "skipped": "desligado", "sent": 0}
    remote, folder = (s.get("remote") or "").strip().rstrip(":"), (s.get("folder") or "").strip().strip("/")
    tags = [t for t in (s.get("tags") or []) if t]
    if not remote or not tags:
        return {"ok": False, "error": "Escolha o drive e pelo menos uma tag", "sent": 0}
    if _lock.locked():
        return {"ok": False, "error": "Já existe um envio em andamento", "sent": 0}
    async with _lock:
        started = datetime.now(timezone.utc)
        sent, per_tag, errors = 0, {}, []
        for tag in tags:
            devs = await db.devices.find({"tags": tag}, {"_id": 0, "id": 1, "name": 1}).to_list(5000)
            if not devs:
                per_tag[tag] = 0
                continue
            names = {d["id"]: d["name"] for d in devs}
            q = {"device_id": {"$in": list(names)}, "ok": True, "cloud": {"$ne": tag}}
            if s.get("only_changed", True):
                q["$or"] = [{"changed": True}, {"first": True}]
            docs = await db.backups.find(q, {"_id": 0, "id": 1, "device_id": 1, "created_at": 1, "content": 1}) \
                .sort("created_at", 1).to_list(MAX_PER_RUN)
            per_tag[tag] = 0
            if not docs:
                continue
            base = os.path.join(STAGE_DIR, safe_name(tag))
            shutil.rmtree(base, ignore_errors=True)
            try:
                for b in docs:
                    folder_dev = os.path.join(base, safe_name(names.get(b["device_id"], b["device_id"])))
                    os.makedirs(folder_dev, exist_ok=True)
                    with open(os.path.join(folder_dev, _stamp(b["created_at"]) + ".cfg"), "w", encoding="utf-8") as f:
                        f.write(b.get("content") or "")
                dest = f"{remote}:{folder + '/' if folder else ''}{safe_name(tag)}"
                code, out = await _rclone("copy", base, dest, "--transfers", "4", "--retries", "2", timeout=1800)
                if code != 0:
                    errors.append(f"{tag}: {_short(out)}")
                    continue
                ids = [b["id"] for b in docs]
                for i in range(0, len(ids), 500):
                    await db.backups.update_many({"id": {"$in": ids[i:i + 500]}}, {"$addToSet": {"cloud": tag}})
                per_tag[tag] = len(docs)
                sent += len(docs)
            except Exception as e:
                errors.append(f"{tag}: {type(e).__name__}: {e}")
            finally:
                shutil.rmtree(base, ignore_errors=True)
        state = {"key": "cloud_state", "at": started.isoformat(), "ok": not errors, "sent": sent, "tags": per_tag,
                 "error": "; ".join(errors)[:600], "trigger": trigger, "dest": f"{remote}:{folder}"}
        if not errors and sent == 0:                     # nada novo: mantém a data do último envio de verdade
            prev = await db.config.find_one({"key": "cloud_state"}, {"_id": 0}) or {}
            state["last_sent_at"] = prev.get("last_sent_at")
        elif sent:
            state["last_sent_at"] = started.isoformat()
        await db.config.update_one({"key": "cloud_state"}, {"$set": state}, upsert=True)
        if errors:
            log.warning(f"backup na nuvem falhou: {state['error']}")
            if alert:
                try:
                    await alert(db, "⚠️ Backup na nuvem falhou", "\n".join(f"- {e}" for e in errors) +
                                "\nOs backups continuam guardados no BastiON; o envio é tentado de novo no próximo backup.",
                                push_url="/automation", push_tag="cloud-backup")
                except Exception:
                    pass
        elif sent:
            log.info(f"backup na nuvem: {sent} arquivo(s) para {remote}:{folder} {per_tag}")
        return {k: v for k, v in state.items() if k != "key"}
