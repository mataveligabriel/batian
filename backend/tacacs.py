"""Servidor TACACS+ (RFC 8907) do BastiON: protocolo, ofuscação, fluxo de login e modelos de configuração por fabricante.

O serviço "tacacs" (tacacsd.py) escuta TCP 49 e usa este módulo para falar o protocolo; quem decide (usuário,
senha, perfil, comando permitido) é o backend, pelos endpoints internos /api/tacacs/internal/*.

Autenticação: ASCII (login interativo: pede usuário e senha) e PAP. CHAP/MS-CHAP não: a senha só existe como hash.
Autorização: início de sessão (perfil: priv-lvl, local-user-name do Junos, roles do NX-OS…) e comando a comando
(service=shell cmd=… cmd-arg=…), que passa pela Política de comandos. Contabilidade: início/fim de sessão e comandos.
"""
import asyncio
import hashlib
import ipaddress
import os
import re
import struct
from typing import Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- constantes do protocolo
MAJOR = 0xC
AUTHEN, AUTHOR, ACCT = 1, 2, 3
UNENCRYPTED, SINGLE_CONNECT = 0x01, 0x04
HDR = struct.Struct("!BBBBII")          # version, type, seq_no, flags, session_id, length
MAX_BODY = 65535

# autenticação
ACT_LOGIN, ACT_CHPASS, ACT_SENDAUTH = 1, 2, 4
T_ASCII, T_PAP, T_CHAP, T_MSCHAP, T_MSCHAPV2 = 1, 2, 3, 5, 6
SVC_LOGIN, SVC_ENABLE = 1, 2
ST_PASS, ST_FAIL, ST_GETDATA, ST_GETUSER, ST_GETPASS, ST_RESTART, ST_ERROR = 1, 2, 3, 4, 5, 6, 7
REPLY_NOECHO = 0x01
CONT_ABORT = 0x01
# autorização
AU_PASS_ADD, AU_PASS_REPL, AU_FAIL, AU_ERROR = 0x01, 0x02, 0x10, 0x11
# contabilidade
ACCT_START, ACCT_STOP, ACCT_WATCHDOG = 0x02, 0x04, 0x08
ACCT_SUCCESS, ACCT_ERROR = 0x01, 0x02

TYPE_NAME = {AUTHEN: "authen", AUTHOR: "author", ACCT: "acct"}
AUTHEN_TYPE_NAME = {T_ASCII: "ascii", T_PAP: "pap", T_CHAP: "chap", T_MSCHAP: "mschap", T_MSCHAPV2: "mschapv2"}


class TacacsError(Exception):
    pass


def crypt(body: bytes, session_id: int, key: bytes, version: int, seq_no: int) -> bytes:
    """Ofuscação do corpo (XOR com MD5 encadeado). A mesma função cifra e decifra."""
    if not key:
        return body
    base = struct.pack("!I", session_id) + key + bytes([version, seq_no])
    pad, prev = bytearray(), b""
    while len(pad) < len(body):
        prev = hashlib.md5(base + prev).digest()
        pad += prev
    return bytes(a ^ b for a, b in zip(body, pad))


def pack(ptype: int, seq_no: int, session_id: int, body: bytes, key: bytes, version: int = MAJOR << 4, flags: int = 0) -> bytes:
    f = flags & ~UNENCRYPTED if key else flags | UNENCRYPTED
    return HDR.pack(version, ptype, seq_no, f, session_id, len(body)) + crypt(body, session_id, key, version, seq_no)


async def read_packet(reader: asyncio.StreamReader, timeout: float = 60) -> Optional[Tuple[tuple, bytes]]:
    try:
        h = await asyncio.wait_for(reader.readexactly(HDR.size), timeout)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
        return None
    hdr = HDR.unpack(h)
    if hdr[0] >> 4 != MAJOR:
        raise TacacsError("versão TACACS+ desconhecida")
    if hdr[5] > MAX_BODY:
        raise TacacsError("pacote grande demais")
    try:
        body = await asyncio.wait_for(reader.readexactly(hdr[5]), timeout) if hdr[5] else b""
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
        return None
    return hdr, body


def _take(b: bytes, pos: int, n: int) -> Tuple[bytes, int]:
    if pos + n > len(b):
        raise TacacsError("corpo truncado (chave errada?)")
    return b[pos:pos + n], pos + n


def _s(b: bytes) -> str:
    return b.decode("utf-8", "replace")


# ---------------------------------------------------------------- corpos
def parse_authen_start(b: bytes) -> dict:
    if len(b) < 8:
        raise TacacsError("START curto (chave errada?)")
    action, priv, atype, svc, ul, pl, rl, dl = b[:8]
    pos = 8
    user, pos = _take(b, pos, ul)
    port, pos = _take(b, pos, pl)
    rem, pos = _take(b, pos, rl)
    data, pos = _take(b, pos, dl)
    if pos != len(b):
        raise TacacsError("tamanho do START não confere (chave errada?)")
    return {"action": action, "priv_lvl": priv, "authen_type": atype, "service": svc, "user": _s(user), "port": _s(port),
            "rem_addr": _s(rem), "data": data}


def parse_authen_continue(b: bytes) -> dict:
    if len(b) < 5:
        raise TacacsError("CONTINUE curto")
    ul, dl, flags = struct.unpack("!HHB", b[:5])
    msg, pos = _take(b, 5, ul)
    data, pos = _take(b, pos, dl)
    if pos != len(b):
        raise TacacsError("tamanho do CONTINUE não confere")
    return {"user_msg": msg, "data": data, "flags": flags}


def build_authen_reply(status: int, msg: str = "", data: bytes = b"", flags: int = 0) -> bytes:
    m = msg.encode()[:2000]
    return struct.pack("!BBHH", status, flags, len(m), len(data)) + m + data


def _parse_args(b: bytes, pos: int, n: int) -> Tuple[List[int], int]:
    lens, pos = _take(b, pos, n)
    return list(lens), pos


def parse_author_request(b: bytes) -> dict:
    if len(b) < 8:
        raise TacacsError("REQUEST curto (chave errada?)")
    method, priv, atype, svc, ul, pl, rl, ac = b[:8]
    lens, pos = _parse_args(b, 8, ac)
    user, pos = _take(b, pos, ul)
    port, pos = _take(b, pos, pl)
    rem, pos = _take(b, pos, rl)
    args = []
    for n in lens:
        a, pos = _take(b, pos, n)
        args.append(_s(a))
    if pos != len(b):
        raise TacacsError("tamanho do REQUEST não confere (chave errada?)")
    return {"authen_method": method, "priv_lvl": priv, "authen_type": atype, "service": svc, "user": _s(user), "port": _s(port),
            "rem_addr": _s(rem), "args": args}


def build_author_reply(status: int, args: List[str] = (), msg: str = "", data: str = "") -> bytes:
    ab = [a.encode()[:255] for a in args][:255]
    m, d = msg.encode()[:2000], data.encode()[:2000]
    return (struct.pack("!BBHH", status, len(ab), len(m), len(d)) + bytes(len(a) for a in ab) + m + d + b"".join(ab))


def parse_acct_request(b: bytes) -> dict:
    if len(b) < 9:
        raise TacacsError("REQUEST de contabilidade curto (chave errada?)")
    flags, method, priv, atype, svc, ul, pl, rl, ac = b[:9]
    lens, pos = _parse_args(b, 9, ac)
    user, pos = _take(b, pos, ul)
    port, pos = _take(b, pos, pl)
    rem, pos = _take(b, pos, rl)
    args = []
    for n in lens:
        a, pos = _take(b, pos, n)
        args.append(_s(a))
    if pos != len(b):
        raise TacacsError("tamanho do REQUEST de contabilidade não confere")
    return {"flags": flags, "authen_method": method, "priv_lvl": priv, "authen_type": atype, "service": svc, "user": _s(user),
            "port": _s(port), "rem_addr": _s(rem), "args": args}


def build_acct_reply(status: int, msg: str = "") -> bytes:
    m = msg.encode()[:2000]
    return struct.pack("!HHB", len(m), 0, status) + m


# ---------------------------------------------------------------- atributos
def split_arg(a: str) -> Tuple[str, str, bool]:
    """'priv-lvl=15' -> ('priv-lvl', '15', True); 'x*y' -> opcional."""
    m = re.match(r"^([^=*]+)([=*])(.*)$", a, re.S)
    if not m:
        return a, "", True
    return m.group(1).strip(), m.group(3), m.group(2) == "="


def args_dict(args: List[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for a in args:
        k, v, _ = split_arg(a)
        out.setdefault(k, []).append(v)
    return out


def command_of(args: List[str]) -> str:
    """Reconstrói o comando de 'cmd=show' 'cmd-arg=ip' 'cmd-arg=route' 'cmd-arg=<cr>'."""
    d = args_dict(args)
    parts = [x for x in (d.get("cmd") or [""])[:1] + (d.get("cmd-arg") or []) if x and x != "<cr>"]
    return re.sub(r"\s+", " ", re.sub(r"(^|\s)<cr>(?=\s|$)", " ", " ".join(parts))).strip()


READ_ONLY = re.compile(r"^(show|display|ping|traceroute|tracert|trace|exit|quit|end|logout|terminal|screen-length|enable|who|"
                       r"whoami|help|history|dir|more|monitor\s+interface|top|uptime)\b", re.I)


def role_attrs(vendor: str, prof: dict) -> List[str]:
    """Atributos devolvidos no início da sessão, conforme o fabricante do equipamento."""
    priv = str(int(prof.get("priv", 15)))
    if vendor == "juniper":
        return [f"local-user-name={prof.get('junos') or 'remote'}"]
    if vendor == "nxos":
        return [f'shell:roles*"{prof.get("nxos") or "network-operator"}"', f"priv-lvl*{priv}"]
    if vendor == "iosxr":
        return [f"priv-lvl={priv}", f"task*{prof.get('iosxr') or '#read-only-tg'}"]
    return [f"priv-lvl={priv}"]


# ---------------------------------------------------------------- servidor
class Handler:
    """Interface que o tacacsd implementa (chamando o backend)."""

    def client(self, ip: str) -> Optional[dict]:                     # {id, name, vendor, key(bytes)}
        raise NotImplementedError

    async def authenticate(self, client: dict, ctx: dict, user: str, password: str) -> dict:   # {ok, msg, error?}
        raise NotImplementedError

    async def authorize(self, client: dict, ctx: dict, args: List[str]) -> dict:              # {status, args, msg}
        raise NotImplementedError

    async def account(self, client: dict, ctx: dict, args: List[str]) -> dict:                # {ok}
        raise NotImplementedError

    def note(self, kind: str, ip: str, text: str = ""):               # contadores/diagnóstico
        pass


class Server:
    def __init__(self, handler: Handler, idle: float = 120):
        self.h = handler
        self.idle = idle
        self.sessions: Dict[Tuple[int, int], dict] = {}

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername") or ("?", 0)
        ip = peer[0][7:] if str(peer[0]).startswith("::ffff:") else str(peer[0])
        cl = self.h.client(ip)
        if not cl:
            self.h.note("unknown", ip)
            writer.close()
            return
        conn = id(writer)
        try:
            while True:
                try:
                    pkt = await read_packet(reader, self.idle)
                except TacacsError as e:
                    self.h.note("bad", ip, str(e))
                    break
                if not pkt:
                    break
                (ver, ptype, seq, flags, sid, _), raw = pkt
                if flags & UNENCRYPTED and cl.get("key"):
                    self.h.note("bad", ip, "pacote sem cifra com chave configurada")
                    break
                body = crypt(raw, sid, cl.get("key") or b"", ver, seq)
                try:
                    reply = await self._dispatch(cl, ip, ptype, seq, sid, body, conn)
                except TacacsError as e:
                    self.h.note("bad", ip, str(e))
                    reply = self._error(ptype, str(e))
                if reply is None:
                    continue
                writer.write(pack(ptype, seq + 1, sid, reply, cl.get("key") or b"", ver, flags & SINGLE_CONNECT))
                await writer.drain()
                if not flags & SINGLE_CONNECT and (conn, sid) not in self.sessions:
                    break                                       # sessão acabou; sem single-connect fecha o TCP
        except Exception as e:                                  # um equipamento com bug não derruba o serviço
            self.h.note("bad", ip, f"{type(e).__name__}: {e}")
        finally:
            for k in [k for k in self.sessions if k[0] == conn]:
                self.sessions.pop(k, None)
            try:
                writer.close()
            except Exception:
                pass

    @staticmethod
    def _error(ptype: int, msg: str) -> bytes:
        if ptype == AUTHEN:
            return build_authen_reply(ST_ERROR, msg[:200])
        if ptype == AUTHOR:
            return build_author_reply(AU_ERROR, msg=msg[:200])
        return build_acct_reply(ACCT_ERROR, msg[:200])

    async def _dispatch(self, cl: dict, ip: str, ptype: int, seq: int, sid: int, body: bytes, conn: int) -> Optional[bytes]:
        if ptype == AUTHEN:
            return await self._authen(cl, ip, seq, sid, body, conn)
        if ptype == AUTHOR:
            req = parse_author_request(body)
            ctx = {k: req[k] for k in ("user", "port", "rem_addr", "priv_lvl", "authen_method", "authen_type", "service")} | {"nas_ip": ip}
            r = await self.h.authorize(cl, ctx, req["args"])
            return build_author_reply(r.get("status", AU_FAIL), r.get("args") or [], r.get("msg", ""))
        if ptype == ACCT:
            req = parse_acct_request(body)
            ctx = {k: req[k] for k in ("user", "port", "rem_addr", "priv_lvl", "flags", "service")} | {"nas_ip": ip}
            r = await self.h.account(cl, ctx, req["args"])
            return build_acct_reply(ACCT_SUCCESS if r.get("ok", True) else ACCT_ERROR, r.get("msg", ""))
        raise TacacsError(f"tipo de pacote desconhecido {ptype}")

    async def _authen(self, cl: dict, ip: str, seq: int, sid: int, body: bytes, conn: int) -> bytes:
        key = (conn, sid)
        st = self.sessions.get(key)
        if seq == 1:
            s = parse_authen_start(body)
            if s["action"] != ACT_LOGIN:
                return build_authen_reply(ST_FAIL, "Só login (troca de senha pelo BastiON)")
            ctx = {"user": s["user"], "port": s["port"], "rem_addr": s["rem_addr"], "priv_lvl": s["priv_lvl"], "nas_ip": ip,
                   "authen_type": AUTHEN_TYPE_NAME.get(s["authen_type"], str(s["authen_type"])),
                   "service": "enable" if s["service"] == SVC_ENABLE else "login"}
            if s["authen_type"] == T_PAP:
                return await self._finish(cl, ctx, s["user"], _s(s["data"]))
            if s["authen_type"] != T_ASCII:
                return build_authen_reply(ST_FAIL, "Use PAP ou ASCII (CHAP não é aceito: a senha não fica guardada em texto)")
            self.sessions[key] = st = {"ctx": ctx, "user": s["user"]}
            if not st["user"] and s["service"] != SVC_ENABLE:
                st["want"] = "user"
                return build_authen_reply(ST_GETUSER, "Usuário: ")
            st["want"] = "pass"
            return build_authen_reply(ST_GETPASS, "Senha: ", flags=REPLY_NOECHO)
        if not st:
            raise TacacsError("CONTINUE sem START")
        c = parse_authen_continue(body)
        if c["flags"] & CONT_ABORT:
            self.sessions.pop(key, None)
            return build_authen_reply(ST_FAIL, "Cancelado")
        val = _s(c["user_msg"])
        if st["want"] == "user":
            st["user"] = val.strip()
            if not st["user"]:
                return build_authen_reply(ST_GETUSER, "Usuário: ")
            st["want"] = "pass"
            return build_authen_reply(ST_GETPASS, "Senha: ", flags=REPLY_NOECHO)
        self.sessions.pop(key, None)
        st["ctx"]["user"] = st["user"]
        return await self._finish(cl, st["ctx"], st["user"], val)

    async def _finish(self, cl: dict, ctx: dict, user: str, password: str) -> bytes:
        r = await self.h.authenticate(cl, ctx, user, password)
        if r.get("error"):
            return build_authen_reply(ST_ERROR, r.get("msg", "BastiON indisponível"))
        return build_authen_reply(ST_PASS if r.get("ok") else ST_FAIL, r.get("msg", ""))


# ---------------------------------------------------------------- cliente (teste e diagnóstico)
class Client:
    """Faz o papel do equipamento: usado nos testes e no botão "Testar" (login de verdade no serviço)."""

    def __init__(self, host: str, port: int, key: str, timeout: float = 5, bind: Optional[str] = None):
        self.host, self.port, self.key, self.timeout = host, port, key.encode(), timeout
        self.bind = (bind, 0) if bind else None

    async def _xchg(self, ptype: int, bodies: List[bytes], version: int = MAJOR << 4) -> List[bytes]:
        r, w = await asyncio.wait_for(asyncio.open_connection(self.host, self.port, local_addr=self.bind), self.timeout)
        sid = struct.unpack("!I", os.urandom(4))[0]
        out = []
        try:
            seq = 1
            for b in bodies:
                w.write(pack(ptype, seq, sid, b, self.key, version))
                await w.drain()
                pkt = await read_packet(r, self.timeout)
                if not pkt:
                    raise TacacsError("servidor fechou a conexão (IP não cadastrado ou chave errada)")
                (ver, _, rseq, _, rsid, _), raw = pkt
                out.append(crypt(raw, rsid, self.key, ver, rseq))
                seq = rseq + 1
            return out
        finally:
            w.close()

    async def pap(self, user: str, password: str, rem: str = "198.51.100.10") -> dict:
        u, p, port, ra = user.encode(), password.encode(), b"tty0", rem.encode()
        body = bytes([ACT_LOGIN, 1, T_PAP, SVC_LOGIN, len(u), len(port), len(ra), len(p)]) + u + port + ra + p
        rep = (await self._xchg(AUTHEN, [body], (MAJOR << 4) | 1))[0]
        st, _, ml, _ = struct.unpack("!BBHH", rep[:6])
        return {"status": st, "ok": st == ST_PASS, "msg": _s(rep[6:6 + ml])}

    async def ascii(self, user: str, password: str, rem: str = "198.51.100.10") -> dict:
        """Login interativo: START sem usuário -> GETUSER -> GETPASS -> PASS/FAIL."""
        r, w = await asyncio.wait_for(asyncio.open_connection(self.host, self.port, local_addr=self.bind), self.timeout)
        sid = struct.unpack("!I", os.urandom(4))[0]
        try:
            port, ra = b"tty1", rem.encode()
            msgs = [bytes([ACT_LOGIN, 1, T_ASCII, SVC_LOGIN, 0, len(port), len(ra), 0]) + port + ra]
            seq, prompts = 1, []
            answers = [user, password]
            while True:
                w.write(pack(AUTHEN, seq, sid, msgs[-1], self.key))
                await w.drain()
                pkt = await read_packet(r, self.timeout)
                if not pkt:
                    raise TacacsError("servidor fechou a conexão")
                (ver, _, rseq, _, rsid, _), raw = pkt
                rep = crypt(raw, rsid, self.key, ver, rseq)
                st, fl, ml, _ = struct.unpack("!BBHH", rep[:6])
                prompts.append((st, fl))
                if st in (ST_GETUSER, ST_GETPASS, ST_GETDATA):
                    a = (answers.pop(0) if answers else "").encode()
                    msgs.append(struct.pack("!HHB", len(a), 0, 0) + a)
                    seq = rseq + 1
                    continue
                return {"status": st, "ok": st == ST_PASS, "msg": _s(rep[6:6 + ml]), "prompts": prompts}
        finally:
            w.close()

    async def author(self, user: str, args: List[str], rem: str = "198.51.100.10") -> dict:
        u, port, ra = user.encode(), b"tty1", rem.encode()
        ab = [a.encode() for a in args]
        body = bytes([6, 15, T_ASCII, SVC_LOGIN, len(u), len(port), len(ra), len(ab)]) + bytes(len(a) for a in ab) + u + port + ra + b"".join(ab)
        rep = (await self._xchg(AUTHOR, [body]))[0]
        st, ac, ml, dl = struct.unpack("!BBHH", rep[:6])
        lens = list(rep[6:6 + ac])
        pos = 6 + ac
        msg = _s(rep[pos:pos + ml])
        pos += ml + dl
        out = []
        for n in lens:
            out.append(_s(rep[pos:pos + n]))
            pos += n
        return {"status": st, "args": out, "msg": msg}

    async def acct(self, user: str, flags: int, args: List[str], rem: str = "198.51.100.10") -> dict:
        u, port, ra = user.encode(), b"tty1", rem.encode()
        ab = [a.encode() for a in args]
        body = bytes([flags, 6, 15, T_ASCII, SVC_LOGIN, len(u), len(port), len(ra), len(ab)]) + bytes(len(a) for a in ab) + u + port + ra + b"".join(ab)
        rep = (await self._xchg(ACCT, [body]))[0]
        ml, dl, st = struct.unpack("!HHB", rep[:5])
        return {"status": st, "ok": st == ACCT_SUCCESS}


# ---------------------------------------------------------------- configuração
ROLES = ("admin", "operator", "viewer")
VENDORS = {
    "cisco": "Cisco IOS / IOS-XE", "iosxr": "Cisco IOS XR", "nxos": "Cisco NX-OS", "huawei": "Huawei VRP (NE/CE/S)",
    "juniper": "Juniper Junos", "zte": "ZTE (OLT C300/C320/C600 e ZXR10)", "datacom": "Datacom DmOS", "generic": "Outro (genérico)",
}
DEVICE_TYPE_VENDOR = {"cisco": "cisco", "huawei": "huawei", "juniper": "juniper", "zte": "zte", "datacom": "datacom"}
DEFAULT_PROFILES = {
    "admin": {"priv": 15, "junos": "bastion-admin", "nxos": "network-admin", "iosxr": "#root-lr"},
    "operator": {"priv": 15, "junos": "bastion-oper", "nxos": "network-admin", "iosxr": "#root-lr"},
    "viewer": {"priv": 1, "junos": "bastion-ro", "nxos": "network-operator", "iosxr": "#read-only-tg"},
}
DEFAULTS = {"enabled": True, "port": 49, "server_ip": "", "password_source": "tacacs", "otp": "never", "cmd_author": True,
            "readonly_viewer": True, "fail_open_cmd": True, "retention_days": 90, "profiles": DEFAULT_PROFILES,
            "clients": [], "templates": {}}
# estado de confiança do modelo (o que foi conferido em documentação do fabricante)
TEMPLATE_NOTE = {
    "zte": "Modelo da linha ZXAN (C300/C320/C600). Confira no manual da sua versão: alguns firmwares usam 'tacacs-server key' "
           "separado e o ZXR10 tem sintaxe própria de templates AAA.",
    "datacom": "Modelo DmOS montado sem a documentação oficial à mão: revise a sintaxe no Guia de Configuração do seu "
               "firmware antes de aplicar (edite e salve o modelo aqui).",
    "nxos": "Rode o 'test aaa group BASTION <usuário> <senha>' antes de sair da sessão.",
}

# Linha que começa com "?flag " só entra se a opção estiver ligada: source, cmd (autorização de comando), acct.
TEMPLATES = {
    "cisco": """! BastiON TACACS+ — Cisco IOS / IOS-XE
! Antes de aplicar: mantenha um usuário local de emergência (username <nome> privilege 15 secret <senha>)
! e não feche a sessão atual até testar o login numa segunda janela.
aaa new-model
tacacs server BASTION
 address ipv4 {{server}}
 key 0 {{secret}}
 timeout 5
aaa group server tacacs+ BASTION
 server name BASTION
?source  ip tacacs source-interface {{source}}
aaa authentication login default group BASTION local
aaa authorization exec default group BASTION local if-authenticated
?cmd aaa authorization commands 1 default group BASTION local if-authenticated
?cmd aaa authorization commands 15 default group BASTION local if-authenticated
?acct aaa accounting exec default start-stop group BASTION
?acct aaa accounting commands 1 default start-stop group BASTION
?acct aaa accounting commands 15 default start-stop group BASTION
line vty 0 15
 login authentication default
 authorization exec default
""",
    "iosxr": """! BastiON TACACS+ — Cisco IOS XR (aplique em 'configure' e 'commit')
tacacs-server host {{server}} port 49
 key 0 {{secret}}
!
aaa group server tacacs+ BASTION
 server {{server}}
!
?source tacacs source-interface {{source}} vrf default
aaa authentication login default group BASTION local
aaa authorization exec default group BASTION local
?cmd aaa authorization commands default group BASTION none
?acct aaa accounting exec default start-stop group BASTION
?acct aaa accounting commands default start-stop group BASTION
line default
 login authentication default
 authorization exec default
?cmd  authorization commands default
?acct  accounting exec default
?acct  accounting commands default
!
commit
""",
    "nxos": """! BastiON TACACS+ — Cisco NX-OS
feature tacacs+
tacacs-server host {{server}} key 0 {{secret}}
aaa group server tacacs+ BASTION
 server {{server}}
 use-vrf management
?source  source-interface {{source}}
aaa authentication login default group BASTION local
?cmd aaa authorization commands default group BASTION local
?cmd aaa authorization config-commands default group BASTION local
?acct aaa accounting default group BASTION
""",
    "huawei": """# BastiON HWTACACS — Huawei VRP (NE/CE/S). Em switches S mais antigos, rode "hwtacacs enable" antes.
# Mantenha um local-user de emergência e teste numa segunda sessão antes de sair desta.
system-view
hwtacacs-server template bastion
 hwtacacs-server authentication {{server}} 49
 hwtacacs-server authorization {{server}} 49
 hwtacacs-server accounting {{server}} 49
?source  hwtacacs-server source-ip {{source}}
 hwtacacs-server shared-key cipher {{secret}}
 undo hwtacacs-server user-name domain-included
 quit
aaa
 authentication-scheme bastion
  authentication-mode hwtacacs local
  quit
 authorization-scheme bastion
  authorization-mode hwtacacs local
?cmd   authorization-cmd 1 hwtacacs local
?cmd   authorization-cmd 2 hwtacacs local
?cmd   authorization-cmd 3 hwtacacs local
  quit
?acct  accounting-scheme bastion
?acct   accounting-mode hwtacacs
?acct   accounting start-fail online
?acct   quit
?acct  recording-scheme bastion
?acct   recording-mode hwtacacs bastion
?acct   quit
?acct  cmd recording-scheme bastion
 domain default_admin
  authentication-scheme bastion
  authorization-scheme bastion
?acct   accounting-scheme bastion
  hwtacacs-server bastion
  quit
 quit
user-interface vty 0 4
 authentication-mode aaa
 quit
# VRP V8 (NE/CE): commit
""",
    "juniper": """# BastiON TACACS+ — Juniper Junos (configure; depois "commit confirmed 5" e, testado, "commit")
set system tacplus-server {{server}} secret "{{secret}}"
set system tacplus-server {{server}} timeout 5
?source set system tacplus-server {{server}} source-address {{source}}
set system authentication-order [ tacplus password ]
?acct set system accounting events [ login change-log interactive-commands ]
?acct set system accounting destination tacplus server {{server}} secret "{{secret}}"
?acct+source set system accounting destination tacplus server {{server}} source-address {{source}}
# usuários-modelo: o BastiON diz qual usar (local-user-name) conforme o perfil de cada pessoa
set system login user {{junos_admin}} class super-user
set system login user {{junos_oper}} class super-user
set system login user {{junos_ro}} class read-only
set system login user remote class read-only
""",
    "zte": """! BastiON TACACS+ — ZTE ZXAN (OLT C300/C320/C600)
configure terminal
tacacs enable
tacacs-server host {{server}} key {{secret}}
tacacs-server timeout 10
?source tacacs-server source-ip {{source}}
aaa group-server tacacs+ bastion
 server {{server}}
 exit
aaa authentication login default group bastion local
aaa authorization exec default group bastion local
?cmd aaa authorization commands 15 default group bastion local
?acct aaa accounting exec default start-stop group bastion
?acct aaa accounting commands 15 default stop-only group bastion
user-authentication-type aaa
user-authorization-type aaa
ssh server authentication mode aaa
""",
    "datacom": """! BastiON TACACS+ — Datacom DmOS (REVISE: modelo a conferir no guia do seu firmware)
config
aaa server tacacs bastion
 host {{server}}
 shared-secret {{secret}}
?source  source-address {{source}}
 authentication
 authorization
?acct  accounting
 !
aaa authentication-order [ tacacs local ]
commit
""",
    "generic": """BastiON TACACS+ — dados para configurar qualquer equipamento
Servidor: {{server}}  (TCP 49)
Chave (shared secret): {{secret}}
Autenticação: PAP ou ASCII (CHAP não)
Autorização: priv-lvl {{priv_admin}} (admin/operador), {{priv_ro}} (visualização); comando a comando: service=shell cmd=…
Contabilidade: start/stop de sessão e comandos
Mantenha sempre o login local como segundo método (fallback).
""",
}


def render(vendor: str, params: dict, template: Optional[str] = None) -> str:
    """Troca {{chave}} e resolve as linhas condicionais "?flag " / "?flag+outra "."""
    t = template if template is not None else TEMPLATES.get(vendor, TEMPLATES["generic"])
    flags = {"source": bool(params.get("source")), "cmd": bool(params.get("cmd")), "acct": bool(params.get("acct"))}
    out = []
    for ln in t.splitlines():
        m = re.match(r"^\?([a-z+]+) ?(.*)$", ln)
        if m:
            if not all(flags.get(f) for f in m.group(1).split("+")):
                continue
            ln = m.group(2)
        out.append(re.sub(r"\{\{(\w+)\}\}", lambda x: str(params.get(x.group(1), "")), ln))
    return "\n".join(out).rstrip() + "\n"


def config_params(cfg: dict, client: dict, secret: str, source: str = "", cmd: bool = True, acct: bool = True) -> dict:
    p = cfg.get("profiles") or DEFAULT_PROFILES
    return {"server": cfg.get("server_ip") or "<IP_DO_BASTION>", "secret": secret, "source": source.strip(), "cmd": cmd, "acct": acct,
            "junos_admin": p["admin"].get("junos") or "bastion-admin", "junos_oper": p["operator"].get("junos") or "bastion-oper",
            "junos_ro": p["viewer"].get("junos") or "bastion-ro", "priv_admin": p["admin"].get("priv", 15),
            "priv_ro": p["viewer"].get("priv", 1), "name": client.get("name", "")}


def gen_secret(n: int = 24) -> str:
    alpha = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(alpha[b % len(alpha)] for b in os.urandom(n))


def clean_client(c: dict) -> dict:
    name = str(c.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("Dê um nome ao cliente (ex.: Bordas, OLTs)")
    nets = []
    for x in c.get("networks") or []:
        x = str(x).strip()
        if not x:
            continue
        try:
            nets.append(str(ipaddress.ip_network(x, strict=False)))
        except ValueError:
            raise ValueError(f"{name}: rede/IP inválido {x}")
    if not nets:
        raise ValueError(f"{name}: informe pelo menos um IP ou rede dos equipamentos")
    vendor = c.get("vendor") if c.get("vendor") in VENDORS else "generic"
    cid = re.sub(r"[^a-z0-9]", "", str(c.get("id") or "").lower())[:16] or os.urandom(6).hex()
    return {"id": cid, "name": name, "networks": nets[:200], "vendor": vendor, "enabled": c.get("enabled", True) is not False,
            "description": str(c.get("description") or "")[:200]}


def clean_settings(body: dict, cur: Optional[dict] = None) -> dict:
    cur = cur or {}
    out = {**DEFAULTS, **{k: cur[k] for k in DEFAULTS if k in cur}, **{k: body[k] for k in DEFAULTS if k in body}}
    out["enabled"] = bool(out["enabled"])
    out["port"] = int(out["port"] or 49)
    if not 1 <= out["port"] <= 65535:
        raise ValueError("Porta inválida")
    sip = str(out["server_ip"] or "").strip()
    if sip:
        try:
            sip = str(ipaddress.ip_address(sip))
        except ValueError:
            raise ValueError("IP do BastiON (que os equipamentos alcançam) inválido")
    out["server_ip"] = sip
    out["password_source"] = out["password_source"] if out["password_source"] in ("tacacs", "bastion", "both") else "tacacs"
    out["otp"] = out["otp"] if out["otp"] in ("never", "if_enabled") else "never"
    for k in ("cmd_author", "readonly_viewer", "fail_open_cmd"):
        out[k] = bool(out[k])
    out["retention_days"] = max(7, min(730, int(out["retention_days"] or 90)))
    prof = {}
    for r in ROLES:
        p = {**DEFAULT_PROFILES[r], **((out.get("profiles") or {}).get(r) or {})}
        try:
            p["priv"] = max(0, min(15, int(p.get("priv", 15))))
        except (TypeError, ValueError):
            p["priv"] = DEFAULT_PROFILES[r]["priv"]
        for k in ("junos", "nxos", "iosxr"):
            p[k] = re.sub(r"[^\w#:.\- ]", "", str(p.get(k) or DEFAULT_PROFILES[r][k]))[:64]
        prof[r] = p
    out["profiles"] = prof
    # clientes: os segredos ficam fora (cada cliente guarda o seu em 'secret', cifrado, e só o backend mexe)
    old = {c["id"]: c for c in cur.get("clients") or []}
    cl, seen = [], set()
    for c in out.get("clients") or []:
        x = clean_client(c)
        if x["id"] in seen:
            x["id"] = os.urandom(6).hex()
        seen.add(x["id"])
        x["secret"] = (old.get(x["id"]) or {}).get("secret", "")
        cl.append(x)
    out["clients"] = cl[:100]
    tpl = {}
    for k, v in (out.get("templates") or {}).items():
        if k in TEMPLATES and isinstance(v, str) and v.strip() and v.strip() != TEMPLATES[k].strip():
            tpl[k] = v[:20000]
    out["templates"] = tpl
    return out


def client_for_ip(clients: List[dict], ip: str) -> Optional[dict]:
    """O cliente mais específico que contém o IP (o /32 ganha da rede inteira)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return None
    best, blen = None, -1
    for c in clients:
        if not c.get("enabled", True):
            continue
        for n in c.get("networks") or []:
            try:
                net = ipaddress.ip_network(n, strict=False)
            except ValueError:
                continue
            if a.version == net.version and a in net and net.prefixlen > blen:
                best, blen = c, net.prefixlen
    return best


def clean_username(v: str) -> str:
    u = str(v or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{2,64}", u):
        raise ValueError("Usuário TACACS: 2 a 64 caracteres (letras, números, . _ - @)")
    return u


def default_username(email: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", (email or "").split("@")[0])[:32] or "usuario"


def split_otp(password: str) -> Tuple[str, str]:
    """'senha123456' -> ('senha', '123456') quando a pessoa usa 2FA (código do app no fim da senha)."""
    if len(password) > 6 and password[-6:].isdigit():
        return password[:-6], password[-6:]
    return password, ""
