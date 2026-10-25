"""Servidor TACACS+ do BastiON — roda no container "tacacs" (python tacacsd.py), TCP 49.

Fala o protocolo (tacacs.py) e repassa as decisões ao backend pelos endpoints internos /api/tacacs/internal/*:
usuário e senha, perfil por fabricante, comando a comando pela Política de comandos e contabilidade. Assim tudo
fica num lugar só: bloqueio por tentativas, histórico de logins, SIEM, aprovações no Telegram.

Equipamento cujo IP não está em nenhum "cliente" cadastrado tem a conexão fechada na hora. Se o backend não
responder, a resposta é ERROR: o equipamento passa para o método seguinte (login local), sem trancar ninguém para fora.
"""
import asyncio
import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import tacacs as T  # noqa: E402
import vault  # noqa: E402

log = logging.getLogger("tacacsd")
BACKEND = os.environ.get("BASTION_BACKEND_URL", "http://127.0.0.1:8001").rstrip("/")


def internal_key() -> str:
    """Mesmo cálculo no backend: só quem tem o JWT_SECRET (os containers do BastiON) fala com /tacacs/internal."""
    return hmac.new(os.environ["JWT_SECRET"].encode(), b"bastion-tacacs-internal", hashlib.sha256).hexdigest()


class Daemon(T.Handler):
    def __init__(self, db):
        self.db = db
        self.cfg = dict(T.DEFAULTS)
        self.clients: List[dict] = []
        self.stats = {"connections": 0, "unknown": 0, "bad": 0, "authen": 0, "author": 0, "acct": 0, "backend_errors": 0}
        self.recent: List[dict] = []
        self.started = datetime.now(timezone.utc).isoformat()
        self.port = None
        self.server = None
        self.listen_error = ""
        self.http = None

    # ---------- configuração
    async def load(self):
        doc = await self.db.config.find_one({"key": "tacacs"}, {"_id": 0}) or {}
        self.cfg = T.clean_settings({}, doc) if doc else dict(T.DEFAULTS)
        cl = []
        for c in doc.get("clients") or []:
            if not c.get("enabled", True):
                continue
            key = vault.decrypt(c.get("secret", ""))
            if not key:
                continue                      # sem chave não atende (TACACS+ sem cifra não é aceito)
            cl.append({**{k: v for k, v in c.items() if k != "secret"}, "key": key.encode()})
        self.clients = cl

    def client(self, ip: str) -> Optional[dict]:
        self.stats["connections"] += 1
        if ip in ("127.0.0.1", "::1"):         # botão "Testar" do BastiON (chave interna, só nesta máquina)
            return {"id": "__local__", "name": "Teste do BastiON", "vendor": "generic", "key": internal_key().encode()}
        if not self.cfg.get("enabled", True):
            return None
        return T.client_for_ip(self.clients, ip)

    def note(self, kind: str, ip: str, text: str = ""):
        self.stats[kind] = self.stats.get(kind, 0) + 1
        self.recent = ([{"at": datetime.now(timezone.utc).isoformat(), "kind": kind, "ip": ip, "text": text[:200]}] + self.recent)[:30]

    # ---------- backend
    async def _call(self, path: str, body: dict) -> Optional[dict]:
        import httpx
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=6, headers={"X-Bastion-Internal": internal_key()})
        try:
            r = await self.http.post(f"{BACKEND}/api/tacacs/internal/{path}", json=body)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            self.stats["backend_errors"] += 1
            self.note("backend", body.get("ctx", {}).get("nas_ip", ""), f"{path}: {type(e).__name__} {str(e)[:120]}")
            return None

    @staticmethod
    def _cl(c: dict) -> dict:
        return {k: c.get(k) for k in ("id", "name", "vendor")}

    async def authenticate(self, client: dict, ctx: dict, user: str, password: str) -> dict:
        self.stats["authen"] += 1
        r = await self._call("authen", {"client": self._cl(client), "ctx": ctx, "user": user, "password": password})
        if r is None:
            return {"error": True, "msg": "BastiON indisponível"}
        return r

    async def authorize(self, client: dict, ctx: dict, args: List[str]) -> dict:
        self.stats["author"] += 1
        r = await self._call("author", {"client": self._cl(client), "ctx": ctx, "args": args})
        if r is None:
            return {"status": T.AU_ERROR, "msg": "BastiON indisponível"}
        return r

    async def account(self, client: dict, ctx: dict, args: List[str]) -> dict:
        self.stats["acct"] += 1
        r = await self._call("acct", {"client": self._cl(client), "ctx": ctx, "args": args})
        return r if r is not None else {"ok": False, "msg": "BastiON indisponível"}

    # ---------- laço
    async def listen(self):
        port = int(self.cfg.get("port") or 49)
        if self.server and self.port == port:
            return
        if self.server:
            self.server.close()
            self.server = None
        srv = T.Server(self)
        try:
            self.server = await asyncio.start_server(srv.serve, host=None, port=port, reuse_address=True, limit=T.MAX_BODY + 64)
            self.port, self.listen_error = port, ""
            log.info(f"TACACS+ ouvindo na porta TCP {port}")
        except OSError as e:
            self.listen_error = f"porta {port}: {e}"
            log.warning(f"não consegui abrir a porta {port}: {e}")

    async def heartbeat(self):
        await self.db.config.update_one({"key": "tacacs_status"}, {"$set": {
            "key": "tacacs_status", "at": datetime.now(timezone.utc).isoformat(), "started": self.started, "port": self.port,
            "listening": bool(self.server), "error": self.listen_error, "clients": len(self.clients), "stats": self.stats,
            "recent": self.recent}}, upsert=True)

    async def run(self):
        n = 0
        while True:
            try:
                if n % 4 == 0:
                    await self.load()
                    await self.listen()
                await self.heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"tacacsd: {e}")
            n += 1
            await asyncio.sleep(5)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s tacacsd - %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    for attempt in range(60):
        try:
            await client.admin.command("ping")
            break
        except Exception as e:
            log.warning(f"MongoDB indisponível ({e.__class__.__name__}), tentativa {attempt + 1}/60…")
            await asyncio.sleep(2)
    await Daemon(db).run()


if __name__ == "__main__":
    asyncio.run(main())
