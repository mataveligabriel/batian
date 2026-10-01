"""Serviço de VPN do BastiON — roda no container "vpn" (python vpnd.py), com NET_ADMIN e /dev/ppp.

FortiGate SSL-VPN pelo openfortivpn: usuário + senha (do cadastro, criptografada) + token digitado na hora.
O BastiON pede pela coleção vpn_cmds (connect/disconnect) e acompanha em vpn_status. Só as redes dos jumps que
usam a VPN (e as que você cadastrar) entram no túnel: a rota padrão do servidor não muda.
"""
import asyncio
import ipaddress
import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import vault  # noqa: E402

log = logging.getLogger("vpnd")
OFV = os.environ.get("OPENFORTIVPN_BIN", "openfortivpn")
IP = os.environ.get("VPN_IP_BIN", "ip")
RUN_DIR = Path(os.environ.get("VPN_RUN_DIR", "/run/bastion-vpn"))
CONNECT_TIMEOUT = 60
OTP_WAIT = 300                      # e-mail/SMS podem demorar
_OTP_PROMPT = re.compile(r"(two-factor|token|otp|one[- ]time|c[oó]digo|code)[^\n]*[:?]\s*$", re.I)
_SECRET_RE = re.compile(r"(password|passwd|otp|token)(\s*[=:]\s*)\S+", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(line: str, secrets) -> str:
    for s in secrets:
        if s and len(s) >= 3:
            line = line.replace(s, "•••")
    return _SECRET_RE.sub(r"\1\2•••", line)


def _routes_for(prof: dict, hosts) -> list:
    out = []
    for r in list(prof.get("routes") or []) + [f"{h}/32" for h in hosts]:
        try:
            n = ipaddress.ip_network(str(r).strip(), strict=False)
        except ValueError:
            continue
        if n.version == 4 and n.prefixlen > 0 and str(n) not in out:     # nunca a rota padrão
            out.append(str(n))
    return out


class Tunnel:
    def __init__(self, daemon, prof: dict, otp: str):
        self.d = daemon
        self.prof = prof
        self.otp = otp
        self.proc = None
        self.state = "connecting"
        self.iface = None
        self.ip = None
        self.error = None
        self.cert = None
        self.routes: list = []
        self.lines = deque(maxlen=80)
        self.since = _now()
        self.was_up = False
        self.stopping = False
        self.prompt = None
        self.secrets: list = []
        self.deadline = time.monotonic() + CONNECT_TIMEOUT
        self.otp_deadline = 0.0

    @property
    def pid(self):
        return self.prof["id"]

    async def publish(self):
        await self.d.db.vpn_status.update_one({"_id": self.pid}, {"$set": {
            "state": self.state, "since": self.since, "iface": self.iface, "ip": self.ip,
            "routes": self.routes, "error": self.error, "pending_cert": self.cert, "prompt": self.prompt, "log": list(self.lines)[-40:],
            "updated": _now()}}, upsert=True)

    def _conf(self) -> Path:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(RUN_DIR, 0o700)
        p = self.prof
        lines = [f"host = {p['host']}", f"port = {int(p.get('port') or 443)}", f"username = {p['username']}",
                 f"password = {vault.decrypt(p.get('password', ''))}", "set-routes = 0", "set-dns = 0",
                 "pppd-use-peerdns = 0"]
        if p.get("realm"):
            lines.append(f"realm = {p['realm']}")
        for c in p.get("trusted_certs") or []:
            lines.append(f"trusted-cert = {c}")
        path = RUN_DIR / f"{self.pid}.conf"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    async def run(self):
        conf = self._conf()
        self.secrets = [vault.decrypt(self.prof.get("password", "")), self.otp]
        args = [OFV, "-c", str(conf)] + ([f"--otp={self.otp}"] if self.otp else [])
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        except FileNotFoundError:
            self.state, self.error = "error", "openfortivpn não está instalado neste container"
            await self.publish()
            return
        finally:
            self.otp = ""
        await self.publish()
        asyncio.get_running_loop().call_later(3, lambda: conf.unlink(missing_ok=True))   # senha não fica em disco
        watchdog = asyncio.create_task(self._watchdog())
        buf = ""
        try:
            while True:
                chunk = await self.proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                *lines, buf = buf.split("\n")
                for raw in lines:
                    line = _redact(raw.rstrip("\r"), self.secrets)
                    if line.strip():
                        self.lines.append(f"{datetime.now().strftime('%H:%M:%S')} {line}")
                        await self._parse(line)
                if buf.strip() and _OTP_PROMPT.search(buf) and self.state != "up":
                    # o gateway pediu o token (FortiToken, e-mail ou SMS): espera o usuário digitar no BastiON
                    self.lines.append(f"{datetime.now().strftime('%H:%M:%S')} {_redact(buf.strip(), self.secrets)}")
                    self.prompt = buf.strip().rstrip(":").strip()
                    buf = ""
                    self.state, self.error = "need_otp", None
                    self.otp_deadline = time.monotonic() + OTP_WAIT
                    await self.publish()
        finally:
            watchdog.cancel()
            rc = await self.proc.wait()
            conf.unlink(missing_ok=True)
        if self.state == "up" or self.was_up:
            self.state = "down"
            why = next((l.split("ERROR:", 1)[-1].strip() for l in reversed(self.lines) if "ERROR" in l or "terminated" in l.lower()), "")
            up_for = ""
            try:
                up_for = f" depois de {int((datetime.now(timezone.utc) - datetime.fromisoformat(self.since)).total_seconds())} s"
            except Exception:
                pass
            self.error = None if self.stopping else f"túnel encerrado pelo gateway{up_for} (código {rc})" + (f": {why}" if why else "")
        elif self.state != "error":
            self.state = "error"
            self.error = self.error or f"openfortivpn saiu com código {rc} — veja o log"
        if self.stopping:
            self.state, self.error = "disconnected", None
        self.since = _now()
        self.iface, self.routes = None, []
        await self.publish()
        if self.was_up and not self.stopping:
            await self.d.alert_down(self.prof)

    async def _parse(self, line: str):
        low = line.lower()
        m = re.search(r"interface (ppp\d+) is up|using interface (ppp\d+)|connect: (ppp\d+)", low)
        if m:
            self.iface = next(g for g in m.groups() if g)
        m = re.search(r"got addresses: \[([\d.]+)\]", low)
        if m:
            self.ip = m.group(1)
        m = re.search(r"trusted-cert\s*=?\s*([0-9a-f]{64})", low)
        if m:
            self.cert = m.group(1)
            self.state, self.error = "error", "certificado do gateway não confiável: confira a impressão digital e clique em Confiar"
        elif "could not authenticate" in low or "authentication failed" in low or "permission denied" in low:
            self.state, self.error = "error", "usuário, senha ou token recusados pelo gateway"
        elif "could not resolve" in low or "connection refused" in low or "could not connect" in low:
            self.state, self.error = "error", "não consegui chegar no gateway (endereço/porta)"
        elif "/dev/ppp" in low or "operation not permitted" in low:
            self.state, self.error = "error", "o container não tem acesso ao /dev/ppp ou ao NET_ADMIN (veja o README)"
        if "tunnel is up and running" in low:
            self.state, self.error, self.was_up, self.since = "up", None, True, _now()
            for _ in range(8):                       # o ppp pode aparecer um instante depois do aviso
                if self.iface:
                    break
                self.iface = await self.d.find_ppp()
                if not self.iface:
                    await asyncio.sleep(0.5)
            if not self.iface:
                self.lines.append(f"{datetime.now().strftime('%H:%M:%S')} AVISO: não achei a interface ppp — rotas não aplicadas")
            await self.apply_routes()
        await self.publish()

    async def apply_routes(self):
        if self.state != "up" or not self.iface:
            return
        want = _routes_for(self.prof, await self.d.hosts_for(self.pid))
        for r in want:
            if r not in self.routes:
                rc, out = await self.d.sh(IP, "route", "replace", r, "dev", self.iface)
                if rc == 0:
                    self.routes.append(r)
                else:
                    self.lines.append(f"{datetime.now().strftime('%H:%M:%S')} ERRO rota {r}: {out.strip()[:120]}")
        for r in [x for x in self.routes if x not in want]:
            await self.d.sh(IP, "route", "del", r, "dev", self.iface)
            self.routes.remove(r)

    async def send_otp(self, otp: str):
        if self.state != "need_otp" or not self.proc or self.proc.returncode is not None:
            return
        self.secrets.append(otp)
        self.proc.stdin.write((otp + "\n").encode())
        await self.proc.stdin.drain()
        self.state, self.prompt = "connecting", None
        self.deadline = time.monotonic() + CONNECT_TIMEOUT
        await self.publish()

    async def _watchdog(self):
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            if self.state == "connecting" and now > self.deadline:
                self.state, self.error = "error", "o gateway não respondeu em 60 s"
            elif self.state == "need_otp" and now > self.otp_deadline:
                self.state, self.error = "error", "o token não foi informado a tempo (5 min) — conecte de novo"
            else:
                continue
            await self.stop(keep_state=True)
            return

    async def stop(self, keep_state: bool = False):
        self.stopping = not keep_state
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), 10)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass


class VpnDaemon:
    def __init__(self, db, alert=None):
        self.db = db
        self.alert = alert
        self.tunnels: dict = {}
        self.tasks: set = set()

    async def sh(self, *args):
        p = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
        out, _ = await p.communicate()
        return p.returncode, out.decode("utf-8", "replace")

    async def find_ppp(self):
        rc, out = await self.sh(IP, "-o", "link", "show")
        names = re.findall(r"\d+: (ppp\d+)", out)
        return names[-1] if names else None

    async def hosts_for(self, pid: str) -> list:
        """IPs dos agentes (modo direto) e equipamentos que dependem desta VPN."""
        hosts = []
        async for a in self.db.agents.find({"vpn_id": pid}, {"_id": 0, "host": 1, "mode": 1}):
            if a.get("mode") != "reverse" and a.get("host"):
                hosts.append(a["host"])
        async for d in self.db.devices.find({"vpn_id": pid}, {"_id": 0, "host": 1}):
            if d.get("host"):
                hosts.append(d["host"])
        out = []
        for h in hosts:
            try:
                out.append(str(ipaddress.ip_address(h.strip())))
            except ValueError:
                pass      # nome DNS: cadastre a rede na VPN
        return out

    async def diagnose(self, pid: str):
        """Mostra, do ponto de vista do servidor: túnel, rotas, rota escolhida para cada jump e se a porta SSH abre."""
        t = self.tunnels.get(pid)
        out = [f"== diagnóstico {_now()[:19].replace('T', ' ')} UTC =="]
        rc, v = await self.sh(OFV, "--version")
        out.append(f"openfortivpn {v.strip() or '?'}")
        if t and t.iface:
            out.append(f"estado: {t.state} · interface {t.iface} · IP {t.ip}")
            for args in (("-o", "addr", "show", "dev", t.iface), ("route", "show", "dev", t.iface)):
                rc, o = await self.sh(IP, *args)
                out.append(f"$ ip {' '.join(args)}\n{o.strip() or '(vazio)'}")
            t.prof = await self.db.vpn_profiles.find_one({"id": pid}, {"_id": 0}) or t.prof
            await t.apply_routes()
        else:
            out.append(f"estado: {t.state if t else 'sem túnel'} — conecte a VPN antes do diagnóstico")
        targets = []
        async for a in self.db.agents.find({"vpn_id": pid}, {"_id": 0, "name": 1, "host": 1, "port": 1, "mode": 1}):
            if a.get("mode") != "reverse" and a.get("host"):
                targets.append((a["name"], a["host"].strip(), int(a.get("port") or 22)))
        if not targets:
            out.append("nenhum agente marcado com esta VPN")
        for name, host, port in targets:
            rc, o = await self.sh(IP, "route", "get", host)
            via = o.strip().splitlines()[0] if o.strip() else "?"
            ok = "?"
            t0 = time.monotonic()
            try:
                r, w = await asyncio.wait_for(asyncio.open_connection(host, port), 6)
                ok = f"ABRE ({(time.monotonic() - t0) * 1000:.0f} ms)"
                try:
                    banner = await asyncio.wait_for(r.readline(), 3)
                    ok += f" · {banner.decode(errors='replace').strip()[:60]}"
                except asyncio.TimeoutError:
                    pass
                w.close()
            except asyncio.TimeoutError:
                ok = "SEM RESPOSTA em 6 s (pacote sai mas não volta: política/rota no FortiGate ou jump fora)"
            except OSError as e:
                ok = f"FALHOU: {e.strerror or e}"
            warn = "" if (t and t.iface and t.iface in via) else "   <-- NÃO está saindo pela VPN"
            out.append(f"{name} {host}:{port}\n   rota: {via}{warn}\n   SSH: {ok}")
        await self.db.vpn_status.update_one({"_id": pid}, {"$set": {"diag": "\n".join(out), "diag_at": _now()}}, upsert=True)

    async def alert_down(self, prof: dict):
        if not self.alert:
            return
        try:
            await self.alert(self.db, f"🔌 VPN {prof['name']} caiu",
                             "Os jumps que dependem dela ficaram sem acesso. Abra o BastiON e informe o token para reconectar.",
                             push_url=f"/agents?vpn={prof['id']}", push_tag=f"vpn-{prof['id']}")
        except Exception as e:
            log.warning(f"alerta: {e}")

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def handle(self, cmd: dict):
        pid = cmd.get("profile_id")
        t = self.tunnels.get(pid)
        if cmd.get("action") == "disconnect":
            if t:
                await t.stop()
            else:
                await self.db.vpn_status.update_one({"_id": pid}, {"$set": {"state": "disconnected", "error": None, "updated": _now()}}, upsert=True)
            return
        if cmd.get("action") == "diag":
            await self.diagnose(pid)
            return
        if cmd.get("action") == "otp":
            if t:
                await t.send_otp(cmd.get("otp") or "")
            return
        if cmd.get("action") == "connect":
            prof = await self.db.vpn_profiles.find_one({"id": pid}, {"_id": 0})
            if not prof:
                return
            if t and t.proc and t.proc.returncode is None:
                await t.stop()
            t = Tunnel(self, prof, cmd.get("otp") or "")
            self.tunnels[pid] = t
            self._spawn(t.run())

    async def tick(self):
        cmds = await self.db.vpn_cmds.find({}, {"_id": 0}).sort("at", 1).to_list(50)
        for c in cmds:
            await self.db.vpn_cmds.delete_one({"id": c["id"]})          # o token não fica guardado
            if time.time() - c.get("ts", 0) > 120:                       # pedido velho: token já venceu
                continue
            try:
                await self.handle(c)
            except Exception as e:
                log.exception(f"comando {c.get('action')}: {e}")
        # perfis apagados
        ids = {p["id"] async for p in self.db.vpn_profiles.find({}, {"_id": 0, "id": 1})}
        for pid, t in list(self.tunnels.items()):
            if pid not in ids:
                await t.stop()
                self.tunnels.pop(pid, None)
                await self.db.vpn_status.delete_one({"_id": pid})

    async def refresh_routes(self):
        for t in self.tunnels.values():
            if t.state == "up" and not t.iface:
                t.iface = await self.find_ppp()
            if t.state == "up":
                prof = await self.db.vpn_profiles.find_one({"id": t.pid}, {"_id": 0})
                if prof:
                    t.prof = prof
                await t.apply_routes()
                await t.publish()

    async def run(self):
        # nada fica "conectado" de uma execução anterior
        await self.db.vpn_status.update_many({"state": {"$in": ["up", "connecting", "disconnecting", "need_otp"]}},
                                             {"$set": {"state": "disconnected", "error": "serviço de VPN reiniciado", "iface": None}})
        n = 0
        while True:
            try:
                await self.db.vpn_status.replace_one({"_id": "_daemon"}, {"_id": "_daemon", "at": _now(), "bin": OFV}, upsert=True)
                await self.tick()
                n += 1
                if n % 15 == 0:
                    await self.refresh_routes()
            except Exception as e:
                log.warning(f"laço: {e}")
            await asyncio.sleep(2)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s vpnd - %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # não grava o token do Telegram no log
    from motor.motor_asyncio import AsyncIOMotorClient
    import automation
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    for attempt in range(60):
        try:
            await client.admin.command("ping")
            break
        except Exception:
            await asyncio.sleep(2)
    await VpnDaemon(db, automation.send_alert).run()


if __name__ == "__main__":
    asyncio.run(main())
