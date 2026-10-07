"""Serviço de VPN do BastiON — roda no container "vpn" (python vpnd.py), com NET_ADMIN, /dev/ppp e /dev/net/tun.

Sempre como CLIENTE. Tipos:
- FortiGate SSL-VPN (openfortivpn): usuário + senha (do cadastro, criptografada) + token digitado na hora.
- OpenVPN (openvpn): arquivo .ovpn já limpo no cadastro (vpnconf) + usuário/senha opcionais.
- PPTP (pppd + pptp): usuário/senha, MPPE.
- L2TP/IPsec (strongSwan + xl2tpd + pppd): chave pré-compartilhada + usuário/senha. Sem chave = L2TP puro.
O BastiON pede pela coleção vpn_cmds (connect/disconnect) e acompanha em vpn_status. Só as redes dos jumps que
usam a VPN (e as que você cadastrar) entram no túnel: a rota padrão do servidor não muda.
"""
import asyncio
import base64
import ipaddress
import logging
import os
import re
import shutil
import socket
import stat
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
OVPN = os.environ.get("OPENVPN_BIN", "openvpn")
PPPD = os.environ.get("PPPD_BIN", "pppd")
PPTP = os.environ.get("PPTP_BIN", "pptp")
XL2TPD = os.environ.get("XL2TPD_BIN", "xl2tpd")
IPSEC = os.environ.get("IPSEC_BIN", "ipsec")
IPSEC_CONF = Path(os.environ.get("IPSEC_CONF", "/etc/ipsec.conf"))
IPSEC_SECRETS = Path(os.environ.get("IPSEC_SECRETS", "/etc/ipsec.secrets"))
IPSEC_DIR = Path(os.environ.get("IPSEC_DIR", "/etc/ipsec.d/bastion"))
TUN_DEV = Path(os.environ.get("VPN_TUN_DEV", "/dev/net/tun"))
KIND_BIN = {"fortinet": "openfortivpn", "openvpn": "openvpn", "pptp": "pppd/pptp", "l2tp": "xl2tpd"}
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


class VpnFail(Exception):
    pass


def capabilities() -> dict:
    """O que este container consegue discar (pacotes instalados na imagem)."""
    w = shutil.which
    return {"fortinet": bool(w(OFV)), "openvpn": bool(w(OVPN)), "pptp": bool(w(PPPD) and w(PPTP)),
            "l2tp": bool(w(XL2TPD) and w(PPPD)), "ipsec": bool(w(IPSEC))}


def _q(v: str) -> str:
    """Valor entre aspas para arquivo de opções do pppd."""
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _ensure_tun():
    if TUN_DEV.exists():
        return
    try:
        TUN_DEV.parent.mkdir(parents=True, exist_ok=True)
        os.mknod(TUN_DEV, 0o600 | stat.S_IFCHR, os.makedev(10, 200))
    except Exception as e:
        log.warning(f"/dev/net/tun: {e}")


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
        self.kind = prof.get("type") or "fortinet"
        self.unit = daemon.next_unit() if self.kind != "fortinet" else None
        self.files: list = []
        self.ipsec_conn = None
        self.aux: list = []

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

    def _stamp(self, text: str):
        self.lines.append(f"{datetime.now().strftime('%H:%M:%S')} {_redact(text, self.secrets)}")

    async def _up(self):
        if self.state == "up":
            return
        self.state, self.error, self.was_up, self.since = "up", None, True, _now()
        await self.apply_routes()

    # ---------- OpenVPN / PPTP / L2TP ----------
    async def _prepare(self) -> list:
        """Escreve os arquivos de configuração e devolve o comando a rodar."""
        p, pid = self.prof, self.pid
        user, pw = p.get("username") or "", vault.decrypt(p.get("password", ""))
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(RUN_DIR, 0o700)
        if self.kind == "openvpn":
            cfg = vault.decrypt(p.get("ovpn_config", ""))
            if not cfg.strip():
                raise VpnFail("esta VPN está sem o arquivo .ovpn — edite e cole o conteúdo")
            _ensure_tun()
            conf = RUN_DIR / f"{pid}.ovpn"
            _write(conf, cfg)
            self.files.append(conf)
            tap = bool(re.search(r"^\s*dev\s+tap", cfg, re.M))
            self.iface = f"bvpn{self.unit}"
            args = [OVPN, "--config", str(conf), "--dev", self.iface, "--dev-type", "tap" if tap else "tun", "--route-nopull",
                    "--pull-filter", "ignore", "redirect-gateway", "--pull-filter", "ignore", "dhcp-option", "--auth-nocache",
                    "--auth-retry", "none", "--script-security", "1", "--verb", "3", "--connect-retry-max", "3",
                    "--connect-timeout", "20"]
            if user:
                cred = RUN_DIR / f"{pid}.cred"
                _write(cred, f"{user}\n{pw}\n")
                self.files.append(cred)
                args += ["--auth-user-pass", str(cred)]
            return args
        host = p["host"]
        self.iface = f"ppp{self.unit}"
        common = ["noauth", "nodefaultroute", "refuse-eap", "nobsdcomp", "nodeflate", "lcp-echo-interval 20", "lcp-echo-failure 4",
                  f"name {_q(user)}", f"password {_q(pw)}", f"unit {self.unit}", "ipparam bastion"]
        if self.kind == "pptp":
            opts = RUN_DIR / f"{pid}.ppp"
            mppe = p.get("mppe", True)
            _write(opts, "\n".join([f'pty "{PPTP} {host} --nolaunchpppd"', "remotename PPTP", "nodetach", "mtu 1400", "mru 1400"] + common +
                                   (["refuse-pap", "refuse-chap", "refuse-mschap", "require-mppe-128"] if mppe else [])) + "\n")
            self.files.append(opts)
            return [PPPD, "file", str(opts)]
        # L2TP (com IPsec quando há chave)
        lport = 1701 if not self.d.l2tp_port_busy(self) else 17000 + self.unit
        psk = vault.decrypt(p.get("psk", ""))
        if psk:
            self.secrets.append(psk)
            await self._ipsec_up(host, psk, lport)
        ppplog = RUN_DIR / f"{pid}.ppplog"
        _write(ppplog, "")
        opts = RUN_DIR / f"{pid}.ppp"
        _write(opts, "\n".join(["ipcp-accept-local", "ipcp-accept-remote", "noccp", "mtu 1280", "mru 1280", "connect-delay 5000",
                                f"logfile {ppplog}"] + common) + "\n")
        conf = RUN_DIR / f"{pid}.l2tp"
        _write(conf, f"[global]\nport = {lport}\naccess control = no\n\n[lac bvpn]\nlns = {host}\npppoptfile = {opts}\n"
                     f"length bit = yes\nredial = no\nrequire chap = yes\nrefuse pap = yes\n")
        self.ctl = RUN_DIR / f"{pid}.ctl"
        self.files += [ppplog, opts, conf, self.ctl, RUN_DIR / f"{pid}.pid"]
        self.ppplog = ppplog
        return [XL2TPD, "-D", "-c", str(conf), "-C", str(self.ctl), "-p", str(RUN_DIR / f"{pid}.pid")]

    async def _ipsec_up(self, host: str, psk: str, lport: int):
        if not shutil.which(IPSEC):
            raise VpnFail("o strongSwan (ipsec) não está instalado neste container — L2TP com chave não funciona; rode o update.sh")
        try:
            gw = (await asyncio.get_running_loop().getaddrinfo(host, None, family=socket.AF_INET))[0][4][0]
        except Exception:
            raise VpnFail(f"não consegui resolver o endereço {host}")
        name = f"bvpn{self.unit}"
        IPSEC_DIR.mkdir(parents=True, exist_ok=True)
        for f, inc in ((IPSEC_CONF, f"include {IPSEC_DIR}/*.conf"), (IPSEC_SECRETS, f"include {IPSEC_DIR}/*.secrets")):
            cur = f.read_text() if f.exists() else ""
            if inc not in cur:
                _write(f, cur.rstrip("\n") + ("\n" if cur.strip() else "") + inc + "\n")
        conf, sec = IPSEC_DIR / f"{name}.conf", IPSEC_DIR / f"{name}.secrets"
        _write(conf, f"conn {name}\n  keyexchange=ikev1\n  authby=secret\n  type=transport\n  left=%defaultroute\n"
                     f"  leftprotoport=17/{lport}\n  right={gw}\n  rightid=%any\n  rightprotoport=17/1701\n"
                     f"  ike=aes256-sha1-modp2048,aes256-sha1-modp1024,aes128-sha1-modp1024,3des-sha1-modp1024,aes256-sha256-modp2048\n"
                     f"  esp=aes256-sha1,aes128-sha1,3des-sha1,aes256-sha256\n  dpdaction=clear\n  dpddelay=30s\n"
                     f"  keyingtries=1\n  auto=add\n")
        _write(sec, f"%any {gw} : PSK 0s{base64.b64encode(psk.encode()).decode()}\n")
        self.files += [conf, sec]
        self.ipsec_conn = name
        rc, _ = await self.d.sh(IPSEC, "status", timeout=8)
        if rc != 0:
            await self.d.sh(IPSEC, "start", timeout=15)
            for _ in range(20):
                await asyncio.sleep(0.5)
                rc, _ = await self.d.sh(IPSEC, "status", timeout=8)
                if rc == 0:
                    break
            else:
                raise VpnFail("o strongSwan não subiu neste container (outra instância de IPsec usando as portas 500/4500 no servidor?)")
        await self.d.sh(IPSEC, "reload", timeout=10)
        await self.d.sh(IPSEC, "rereadsecrets", timeout=10)
        self._stamp(f"IPsec: negociando com {gw}…")
        await self.publish()
        rc, out = await self.d.sh(IPSEC, "up", name, timeout=40)
        for l in out.strip().splitlines()[-8:]:
            self._stamp("ipsec: " + l.strip())
        if rc != 0 or "established" not in out:
            low = out.lower()
            if "no_proposal_chosen" in low or "no proposal" in low:
                why = "o servidor não aceitou a criptografia proposta (IKE/ESP) — veja o log"
            elif "invalid_hash" in low or "authentication_failed" in low or "malformed" in low or "invalid_id" in low or "decrypt" in low:
                why = "a chave pré-compartilhada (PSK) não confere com a do servidor"
            elif "not responding" in low or "giving up" in low or "timed out" in low or rc == -9:
                why = "o servidor não respondeu ao IPsec (UDP 500/4500 bloqueado ou endereço errado)"
            else:
                why = "o IPsec não fechou — veja o log"
            raise VpnFail(why)

    async def _ipsec_down(self):
        if self.ipsec_conn:
            await self.d.sh(IPSEC, "down", self.ipsec_conn, timeout=10)
            for f in list(IPSEC_DIR.glob(f"{self.ipsec_conn}.*")):
                f.unlink(missing_ok=True)
            await self.d.sh(IPSEC, "reload", timeout=10)
            await self.d.sh(IPSEC, "rereadsecrets", timeout=10)
            self.ipsec_conn = None

    async def _l2tp_dial(self):
        for _ in range(30):                                # espera o xl2tpd criar o arquivo de controle
            if self.ctl.exists():
                break
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.5)
        try:
            fd = os.open(self.ctl, os.O_WRONLY | os.O_NONBLOCK)
            os.write(fd, b"c bvpn\n")
            os.close(fd)
        except OSError as e:
            self._stamp(f"ERRO ao pedir a discagem ao xl2tpd: {e}")

    async def _tail(self, path: Path):
        pos, buf = 0, ""
        while True:
            await asyncio.sleep(0.3)
            try:
                with open(path, "r", errors="replace") as f:
                    f.seek(pos)
                    buf += f.read()
                    pos = f.tell()
            except OSError:
                continue
            *lines, buf = buf.split("\n")
            for raw in lines:
                if raw.strip():
                    self._stamp("pppd: " + raw.strip())
                    await self._parse_generic(raw)

    async def _parse_generic(self, line: str):
        low = line.lower()
        if self.kind == "openvpn":
            m = re.search(r"net_addr_v4_add: ([\d.]+)|ifconfig \S+ ([\d.]+)|addr add dev \S+ (?:local )?([\d.]+)", low)
            if m:
                self.ip = next(g for g in m.groups() if g)
            if "auth_failed" in low:
                self.state, self.error = "error", "usuário ou senha recusados pelo servidor OpenVPN"
            elif "tls key negotiation failed" in low or "tls handshake failed" in low:
                self.state, self.error = "error", "o servidor não completou o TLS (endereço/porta/protocolo errados, ou certificado e chave não conferem)"
            elif "cannot resolve host" in low or "connection refused" in low:
                self.state, self.error = "error", "não consegui chegar no servidor (endereço/porta)"
            elif "cannot open tun/tap" in low or "/dev/net/tun" in low:
                self.state, self.error = "error", "o container não tem acesso ao /dev/net/tun — rode o update.sh no servidor"
            elif "private key password" in low:
                self.state, self.error = "error", "a chave privada do .ovpn tem senha — exporte o perfil com a chave sem senha"
            elif "options error" in low:
                self.state, self.error = "error", "o OpenVPN recusou o arquivo: " + line.split("rror:", 1)[-1].strip()[:160]
            elif "initialization sequence completed" in low:
                await self._up()
        else:
            m = re.search(r"using interface (ppp\d+)", low)
            if m:
                self.iface = m.group(1)
            m = re.search(r"local\s+ip address ([\d.]+)", low)
            if m:
                self.ip = m.group(1)
                await self._up()
            elif "authentication failed" in low or "auth failed" in low or "peer refused to authenticate" in low or "e=691" in low:
                self.state, self.error = "error", "usuário ou senha recusados pelo servidor"
            elif "mppe required" in low:
                self.state, self.error = "error", "o servidor não aceitou a criptografia MPPE — desmarque \"Exigir criptografia\" na VPN ou habilite MPPE no servidor"
            elif "timeout sending config-requests" in low:
                self.state, self.error = "error", ("o servidor não respondeu ao PPP — o GRE (protocolo 47) costuma estar bloqueado no caminho" if self.kind == "pptp"
                                                   else "o servidor não respondeu ao PPP dentro do L2TP")
            elif "maximum retries exceeded" in low or "connection refused" in low or "no route to host" in low or "could not resolve" in low:
                self.state, self.error = "error", ("o servidor L2TP não respondeu (UDP 1701" + (" dentro do IPsec" if self.ipsec_conn else "") + ")"
                                                   if self.kind == "l2tp" else "não consegui chegar no servidor PPTP (TCP 1723)")
            elif "/dev/ppp" in low or "operation not permitted" in low:
                self.state, self.error = "error", "o container não tem acesso ao /dev/ppp ou ao NET_ADMIN (veja o README)"
            elif self.state == "up" and ("connection terminated" in low or "modem hangup" in low or "lcp terminated" in low) and self.kind == "l2tp":
                await self.stop(keep_state=True)           # o xl2tpd continuaria de pé sem túnel
        await self.publish()
        if self.state == "error" and self.proc and self.proc.returncode is None:
            await self.stop(keep_state=True)               # erro definitivo: não deixa o cliente tentando sozinho

    async def run_generic(self):
        binname = KIND_BIN.get(self.kind, self.kind)
        self.secrets = [vault.decrypt(self.prof.get("password", ""))]
        try:
            args = await self._prepare()
            self.proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        except (VpnFail, FileNotFoundError) as e:
            self.state = "error"
            self.error = str(e) if isinstance(e, VpnFail) else f"{binname} não está instalado neste container — rode o update.sh no servidor"
            await self._cleanup()
            await self.publish()
            return
        self.deadline = time.monotonic() + CONNECT_TIMEOUT
        await self.publish()
        watchdog = asyncio.create_task(self._watchdog())
        if self.kind == "l2tp":
            self.aux = [asyncio.create_task(self._l2tp_dial()), asyncio.create_task(self._tail(self.ppplog))]
        buf = ""
        try:
            while True:
                chunk = await self.proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                *lines, buf = buf.split("\n")
                for raw in lines:
                    if raw.strip():
                        self._stamp(raw.rstrip("\r"))
                        await self._parse_generic(raw)
        finally:
            watchdog.cancel()
            for a in self.aux:
                a.cancel()
            rc = await self.proc.wait()
            await self._cleanup()
        await self._finish(rc, binname)

    async def _cleanup(self):
        try:
            await self._ipsec_down()
        except Exception as e:
            log.warning(f"ipsec down: {e}")
        for f in self.files:
            try:
                Path(f).unlink(missing_ok=True)
            except OSError:
                pass
        self.files = []

    async def run(self):
        if self.kind != "fortinet":
            return await self.run_generic()
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
        await self._finish(rc, "openfortivpn")

    async def _finish(self, rc: int, binname: str):
        if self.state == "up" or self.was_up:
            self.state = "down"
            why = next((l.split("ERROR:", 1)[-1].strip() for l in reversed(self.lines) if "ERROR" in l or "terminated" in l.lower()), "")
            up_for = ""
            try:
                up_for = f" depois de {int((datetime.now(timezone.utc) - datetime.fromisoformat(self.since)).total_seconds())} s"
            except Exception:
                pass
            self.error = None if self.stopping else f"túnel encerrado pelo {'gateway' if self.kind == 'fortinet' else 'servidor'}{up_for} (código {rc})" + (f": {why}" if why else "")
        elif self.state != "error":
            self.state = "error"
            self.error = self.error or f"{binname} saiu com código {rc} — veja o log"
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
                self.state, self.error = "error", "o gateway não respondeu em 60 s" if self.kind == "fortinet" else "o servidor não completou a conexão em 60 s — veja o log"
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

    async def sh(self, *args, timeout: float = 30):
        try:
            p = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.STDOUT)
        except FileNotFoundError:
            return 127, f"{args[0]}: não instalado"
        try:
            out, _ = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            try:
                p.kill()
            except ProcessLookupError:
                pass
            return -9, "tempo esgotado"
        return p.returncode, out.decode("utf-8", "replace")

    def _alive(self):
        return [t for t in self.tunnels.values() if t.proc and t.proc.returncode is None]

    def next_unit(self) -> int:
        """Número fixo da interface (ppp50…, bvpn50…) para não confundir com o ppp do FortiGate."""
        used = {t.unit for t in self._alive() if t.unit is not None}
        return next(n for n in range(50, 200) if n not in used)

    def l2tp_port_busy(self, me) -> bool:
        return any(t is not me and t.kind == "l2tp" for t in self._alive())

    async def find_ppp(self):
        """Interface ppp do FortiGate: a mais nova que não pertence a um túnel PPTP/L2TP."""
        rc, out = await self.sh(IP, "-o", "link", "show")
        mine = {t.iface for t in self.tunnels.values() if t.kind != "fortinet" and t.iface}
        names = [n for n in re.findall(r"\d+: (ppp\d+)", out) if n not in mine]
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
        kind = (t.kind if t else None) or (await self.db.vpn_profiles.find_one({"id": pid}, {"_id": 0, "type": 1}) or {}).get("type") or "fortinet"
        if kind == "fortinet":
            rc, v = await self.sh(OFV, "--version")
            out.append(f"openfortivpn {v.strip() or '?'}")
        else:
            out.append(f"tipo: {kind}")
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
                             "Os jumps que dependem dela ficaram sem acesso. Abra o BastiON e " + ("informe o token para reconectar." if (prof.get("type") or "fortinet") == "fortinet" else "clique em Conectar de novo."),
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
                await self.db.vpn_status.replace_one({"_id": "_daemon"}, {"_id": "_daemon", "at": _now(), "bin": OFV, "caps": capabilities()}, upsert=True)
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
