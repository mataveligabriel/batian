"""Servidor FTP do BastiON: os equipamentos baixam e sobem arquivos (firmware, backup de configuração) direto no
servidor ou por um jump server.

Pelo jump, o BastiON usa a conexão SSH que já mantém com o agente para pedir ao sshd do jump uma porta de escuta
(tcpip-forward, o mesmo que "ssh -R"): o equipamento conecta em IP_DO_JUMP:2121 e o tráfego chega aqui pelo túnel.
Conexão de dados:
  - modo ativo (PORT/EPRT): o BastiON abre a conexão até o equipamento através do jump (canal direct-tcpip);
  - modo passivo (PASV/EPSV): uma faixa pequena de portas também fica aberta no jump.
Os arquivos ficam numa pasta do servidor; cada acesso só enxerga a pasta dele.
"""
import asyncio
import ipaddress
import logging
import os
import posixpath
import stat
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("bastion.ftp")
CHUNK = 64 * 1024
IDLE = 600                    # sem comando por 10 min: desconecta
DATA_WAIT = 30                # espera pela conexão de dados


class Account:
    def __init__(self, access_id: str, name: str, root: str, write: bool, max_bytes: int = 0):
        self.access_id, self.name, self.root, self.write, self.max_bytes = access_id, name, root, write, max_bytes


def _close(w):
    try:
        if hasattr(w, "can_write_eof") and w.can_write_eof():
            w.write_eof()
    except Exception:
        pass
    try:
        w.close()
    except Exception:
        pass


class DataNet:
    """Como abrir conexões de dados: direto do servidor (LocalNet) ou pelo jump (JumpNet)."""

    async def passive(self, peer: str) -> Tuple[str, int, Callable[[], Awaitable[tuple]], Callable[[], None]]:
        raise NotImplementedError

    async def active(self, host: str, port: int) -> tuple:
        raise NotImplementedError


class LocalNet(DataNet):
    def __init__(self, pasv_ip: str, ports: List[int]):
        self.pasv_ip, self.ports = pasv_ip, list(ports)
        self.busy: set = set()

    async def passive(self, peer: str):
        fut: asyncio.Future = asyncio.get_running_loop().create_future()

        async def on_conn(r, w):
            ip = str((w.get_extra_info("peername") or ("",))[0]).replace("::ffff:", "")
            if fut.done() or ip != peer:
                _close(w)
                return
            fut.set_result((r, w))

        for p in self.ports:
            if p in self.busy:
                continue
            try:
                srv = await asyncio.start_server(on_conn, host=None, port=p, reuse_address=True)
            except OSError:
                continue
            self.busy.add(p)

            def done(p=p, srv=srv):
                srv.close()
                self.busy.discard(p)

            async def wait():
                return await asyncio.wait_for(fut, DATA_WAIT)
            return self.pasv_ip, p, wait, done
        raise OSError("sem porta passiva livre")

    async def active(self, host: str, port: int):
        return await asyncio.wait_for(asyncio.open_connection(host, port), 15)


class JumpNet(DataNet):
    """Portas passivas já abertas no jump (listeners remotos); conexão ativa sai do jump pelo canal SSH."""

    def __init__(self, conn, pasv_ip: str, ports: List[int]):
        self.conn, self.pasv_ip, self.ports = conn, pasv_ip, list(ports)
        self.waiting: Dict[int, Tuple[str, asyncio.Future]] = {}

    def incoming(self, port: int, orig_host: str):
        """handler_factory do listener remoto de cada porta passiva."""
        w = self.waiting.get(port)
        if not w or w[1].done() or w[0] != orig_host:
            import asyncssh
            raise asyncssh.ChannelOpenError(asyncssh.OPEN_CONNECT_FAILED, "nenhuma transferência esperando")

        async def handler(r, wr):          # o asyncssh não fecha o canal quando o handler termina
            if not w[1].done():
                w[1].set_result((r, wr))
            else:
                _close(wr)
        return handler

    async def passive(self, peer: str):
        for p in self.ports:
            if p in self.waiting:
                continue
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self.waiting[p] = (peer, fut)

            def done(p=p):
                self.waiting.pop(p, None)

            async def wait(fut=fut):
                return await asyncio.wait_for(fut, DATA_WAIT)
            return self.pasv_ip, p, wait, done
        raise OSError("sem porta passiva livre no jump")

    async def active(self, host: str, port: int):
        return await asyncio.wait_for(self.conn.open_connection(host, port), 15)


def _ls_line(name: str, st: os.stat_result) -> str:
    kind = "d" if stat.S_ISDIR(st.st_mode) else "-"
    perm = "rwxr-xr-x" if kind == "d" else "rw-r--r--"
    t = time.localtime(st.st_mtime)
    when = time.strftime("%b %d %H:%M" if time.time() - st.st_mtime < 180 * 86400 else "%b %d  %Y", t)
    return f"{kind}{perm} 1 ftp ftp {st.st_size:>12} {when} {name}"


class Session:
    def __init__(self, reader, writer, peer: str, net: DataNet,
                 auth: Callable[[str, str, str], Awaitable[Optional[Account]]], on_event: Callable[[dict], Awaitable[None]],
                 label: str = "", banner: str = "BastiON FTP"):
        self.r, self.w, self.peer, self.net, self.auth, self.on_event = reader, writer, peer, net, auth, on_event
        self.label, self.banner = label, banner
        self.user = ""
        self.acc: Optional[Account] = None
        self.cwd = "/"
        self.pending = None           # (wait, done) da conexão de dados preparada
        self.active_addr: Optional[Tuple[str, int]] = None
        self.rest = 0
        self.rnfr: Optional[str] = None
        self.fails = 0

    # ---------- E/S
    async def send(self, line: str):
        self.w.write((line + "\r\n").encode("utf-8", "replace"))
        await self.w.drain()

    async def readline(self) -> Optional[str]:
        line = await asyncio.wait_for(self.r.readline(), IDLE)
        if not line:
            return None
        return line[:4096].decode("utf-8", "replace").rstrip("\r\n")

    # ---------- caminhos (sempre dentro da raiz do acesso)
    def vpath(self, arg: str) -> str:
        p = posixpath.normpath(posixpath.join(self.cwd, arg or "."))
        return "/" if p in (".", "") else ("/" + p.lstrip("/"))

    def real(self, vp: str) -> str:
        root = os.path.realpath(self.acc.root)
        rp = os.path.realpath(os.path.join(root, vp.lstrip("/")))
        if rp != root and not rp.startswith(root + os.sep):
            raise PermissionError("fora da pasta")
        return rp

    async def event(self, action: str, path: str = "", ok: bool = True, size: int = 0, secs: float = 0, error: str = ""):
        try:
            await self.on_event({"action": action, "path": path, "ok": ok, "bytes": size, "secs": round(secs, 2), "error": error,
                                 "user": self.user, "access_id": self.acc.access_id if self.acc else None, "peer": self.peer, "via": self.label})
        except Exception as e:
            log.debug(f"evento FTP: {e}")

    # ---------- dados
    async def open_data(self):
        if self.pending:
            wait, done = self.pending
            self.pending = None
            try:
                return await wait(), done
            except Exception:
                done()
                raise
        if self.active_addr:
            h, p = self.active_addr
            self.active_addr = None
            r, w = await self.net.active(h, p)
            return (r, w), (lambda: None)
        raise RuntimeError("use PASV ou PORT antes")

    async def run(self):
        await self.send(f"220 {self.banner}")
        try:
            while True:
                line = await self.readline()
                if line is None:
                    break
                cmd, _, arg = line.partition(" ")
                cmd = cmd.upper().strip()
                if not await self.handle(cmd, arg.strip()):
                    break
        except (asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            log.warning(f"FTP sessão {self.peer}: {type(e).__name__}: {e}")
        finally:
            if self.pending:
                self.pending[1]()
            _close(self.w)

    async def handle(self, cmd: str, arg: str) -> bool:
        pre = {"USER", "PASS", "QUIT", "SYST", "FEAT", "OPTS", "NOOP", "AUTH", "HELP"}
        if not self.acc and cmd not in pre:
            await self.send("530 Faça login primeiro")
            return True
        h = getattr(self, f"c_{cmd.lower()}", None)
        if not h:
            await self.send(f"502 Comando {cmd[:20]} não implementado")
            return True
        r = await h(arg)
        return r is not False

    # ---------- comandos
    async def c_user(self, a):
        self.user, self.acc = a[:64], None
        await self.send("331 Senha, por favor")

    async def c_pass(self, a):
        acc = await self.auth(self.user, a, self.peer) if self.user else None
        if not acc:
            self.fails += 1
            await self.event("login", ok=False, error="usuário ou senha")
            await asyncio.sleep(1.5)
            await self.send("530 Login incorreto")
            return self.fails < 3
        self.acc = acc
        os.makedirs(acc.root, exist_ok=True)
        await self.event("login")
        await self.send(f"230 Bem-vindo ({'leitura e escrita' if acc.write else 'só leitura'})")

    async def c_auth(self, a):
        await self.send("534 Sem TLS: use FTP comum")

    async def c_quit(self, a):
        await self.send("221 Até logo")
        return False

    async def c_syst(self, a):
        await self.send("215 UNIX Type: L8")

    async def c_feat(self, a):
        self.w.write(b"211-Recursos:\r\n EPSV\r\n PASV\r\n EPRT\r\n SIZE\r\n MDTM\r\n REST STREAM\r\n UTF8\r\n211 Fim\r\n")
        await self.w.drain()

    async def c_opts(self, a):
        await self.send("200 OK")

    async def c_noop(self, a):
        await self.send("200 OK")

    async def c_help(self, a):
        await self.send("214 USER PASS PWD CWD LIST NLST RETR STOR APPE DELE MKD RMD RNFR RNTO SIZE MDTM PASV EPSV PORT EPRT TYPE QUIT")

    async def c_allo(self, a):
        await self.send("202 Não precisa")

    async def c_type(self, a):
        await self.send(f"200 Tipo {a[:1].upper() or 'I'}")

    async def c_mode(self, a):
        await self.send("200 OK" if a.upper() == "S" else "504 Só modo S")

    async def c_stru(self, a):
        await self.send("200 OK" if a.upper() == "F" else "504 Só estrutura F")

    async def c_pwd(self, a):
        await self.send(f'257 "{self.cwd}"')

    c_xpwd = c_pwd

    async def c_cwd(self, a):
        vp = self.vpath(a)
        try:
            ok = os.path.isdir(self.real(vp))
        except PermissionError:
            ok = False
        if ok:
            self.cwd = vp
            await self.send(f'250 "{vp}"')
        else:
            await self.send("550 Pasta não existe")

    c_xcwd = c_cwd

    async def c_cdup(self, a):
        await self.c_cwd("..")

    c_xcup = c_cdup

    async def c_pasv(self, a, ext=False):
        if self.pending:
            self.pending[1]()
            self.pending = None
        try:
            ip, port, wait, done = await self.net.passive(self.peer)
        except OSError as e:
            await self.send(f"425 {e}")
            return
        self.pending = (wait, done)
        self.active_addr = None
        if ext:
            await self.send(f"229 Modo passivo estendido (|||{port}|)")
        else:
            try:
                h = ipaddress.ip_address(ip)
                if h.version != 4:
                    raise ValueError
            except ValueError:
                await self.send("425 Sem IPv4 para o modo passivo: use EPSV ou modo ativo")
                done()
                self.pending = None
                return
            await self.send(f"227 Modo passivo ({str(h).replace('.', ',')},{port >> 8},{port & 255})")

    async def c_epsv(self, a):
        if a.upper() == "ALL":
            await self.send("200 OK")
            return
        await self.c_pasv(a, ext=True)

    async def _set_active(self, host: str, port: int):
        if host != self.peer:
            await self.send("504 O endereço do PORT precisa ser o do próprio equipamento")
            return
        if not 1024 <= port <= 65535:
            await self.send("504 Porta inválida")
            return
        if self.pending:
            self.pending[1]()
            self.pending = None
        self.active_addr = (host, port)
        await self.send("200 PORT OK")

    async def c_port(self, a):
        try:
            p = [int(x) for x in a.split(",")]
            assert len(p) == 6 and all(0 <= x <= 255 for x in p)
        except (ValueError, AssertionError):
            await self.send("501 PORT inválido")
            return
        await self._set_active(".".join(map(str, p[:4])), p[4] * 256 + p[5])

    async def c_eprt(self, a):
        try:
            d = a[0]
            _, proto, host, port, _ = a.split(d)
            await self._set_active(str(ipaddress.ip_address(host)), int(port))
        except (ValueError, IndexError):
            await self.send("501 EPRT inválido")

    async def c_rest(self, a):
        try:
            self.rest = max(0, int(a))
            await self.send(f"350 Continua de {self.rest}")
        except ValueError:
            await self.send("501 REST inválido")

    async def _list(self, a, names_only):
        arg = " ".join(x for x in a.split() if not x.startswith("-"))
        try:
            rp = self.real(self.vpath(arg))
        except PermissionError:
            await self.send("550 Sem acesso")
            return
        if not os.path.exists(rp):
            await self.send("550 Não existe")
            return
        try:
            (r, w), done = await self.open_data()
        except Exception as e:
            await self.send(f"425 Sem conexão de dados: {e}")
            return
        try:
            await self.send("150 Lista")
            items = [(os.path.basename(rp), os.stat(rp))] if os.path.isfile(rp) else \
                [(n, os.stat(os.path.join(rp, n))) for n in sorted(os.listdir(rp)) if not n.startswith(".")]
            body = "".join((n if names_only else _ls_line(n, st)) + "\r\n" for n, st in items)
            w.write(body.encode("utf-8", "replace"))
            await w.drain()
            _close(w)
            await self.send("226 Pronto")
        finally:
            done()

    async def c_list(self, a):
        await self._list(a, False)

    async def c_nlst(self, a):
        await self._list(a, True)

    async def c_size(self, a):
        try:
            rp = self.real(self.vpath(a))
            await self.send(f"213 {os.path.getsize(rp)}" if os.path.isfile(rp) else "550 Não é arquivo")
        except (PermissionError, OSError):
            await self.send("550 Não existe")

    async def c_mdtm(self, a):
        try:
            rp = self.real(self.vpath(a))
            await self.send("213 " + time.strftime("%Y%m%d%H%M%S", time.gmtime(os.path.getmtime(rp))))
        except (PermissionError, OSError):
            await self.send("550 Não existe")

    async def c_retr(self, a):
        vp = self.vpath(a)
        try:
            rp = self.real(vp)
        except PermissionError:
            await self.send("550 Sem acesso")
            return
        if not os.path.isfile(rp):
            await self.send("550 Arquivo não existe")
            return
        try:
            (r, w), done = await self.open_data()
        except Exception as e:
            await self.send(f"425 Sem conexão de dados: {e}")
            return
        t0, sent, off = time.monotonic(), 0, self.rest
        self.rest = 0
        try:
            await self.send(f"150 Enviando {posixpath.basename(vp)} ({os.path.getsize(rp) - off} bytes)")
            f = await asyncio.to_thread(open, rp, "rb")
            try:
                if off:
                    f.seek(off)
                while True:
                    chunk = await asyncio.to_thread(f.read, CHUNK)
                    if not chunk:
                        break
                    w.write(chunk)
                    await w.drain()
                    sent += len(chunk)
            finally:
                f.close()
            _close(w)
            await self.send("226 Enviado")
            await self.event("download", vp, True, sent, time.monotonic() - t0)
        except Exception as e:
            _close(w)
            await self.send("426 Transferência interrompida")
            await self.event("download", vp, False, sent, time.monotonic() - t0, str(e)[:120])
        finally:
            done()

    async def _store(self, a, append=False):
        if not self.acc.write:
            await self.send("550 Acesso só de leitura")
            return
        vp = self.vpath(a)
        try:
            rp = self.real(vp)
        except PermissionError:
            await self.send("550 Sem acesso")
            return
        if os.path.isdir(rp) or not os.path.isdir(os.path.dirname(rp)):
            await self.send("553 Caminho inválido")
            return
        try:
            (r, w), done = await self.open_data()
        except Exception as e:
            await self.send(f"425 Sem conexão de dados: {e}")
            return
        t0, got, off = time.monotonic(), 0, self.rest
        self.rest = 0
        tmp = rp + ".parcial"
        try:
            await self.send(f"150 Recebendo {posixpath.basename(vp)}")
            mode = "ab" if append or off else "wb"
            if mode == "ab" and os.path.exists(rp):
                await asyncio.to_thread(os.replace, rp, tmp)
            f = await asyncio.to_thread(open, tmp, mode)
            try:
                if off:
                    f.truncate(off)
                    f.seek(off)
                while True:
                    chunk = await r.read(CHUNK)
                    if not chunk:
                        break
                    got += len(chunk)
                    if self.acc.max_bytes and got + off > self.acc.max_bytes:
                        raise OSError("arquivo maior que o limite")
                    await asyncio.to_thread(f.write, chunk)
            finally:
                f.close()
            await asyncio.to_thread(os.replace, tmp, rp)
            _close(w)
            await self.send("226 Recebido")
            await self.event("upload", vp, True, got, time.monotonic() - t0)
        except Exception as e:
            _close(w)
            try:
                os.remove(tmp)
            except OSError:
                pass
            await self.send(f"451 Falhou: {str(e)[:80]}")
            await self.event("upload", vp, False, got, time.monotonic() - t0, str(e)[:120])
        finally:
            done()

    async def c_stor(self, a):
        await self._store(a)

    async def c_appe(self, a):
        await self._store(a, append=True)

    async def _w(self) -> bool:
        if not self.acc.write:
            await self.send("550 Acesso só de leitura")
            return False
        return True

    async def c_dele(self, a):
        if not await self._w():
            return
        vp = self.vpath(a)
        try:
            os.remove(self.real(vp))
            await self.send("250 Apagado")
            await self.event("delete", vp)
        except (PermissionError, OSError):
            await self.send("550 Não consegui apagar")

    async def c_mkd(self, a):
        if not await self._w():
            return
        vp = self.vpath(a)
        try:
            os.makedirs(self.real(vp), exist_ok=False)
            await self.send(f'257 "{vp}" criada')
        except (PermissionError, OSError):
            await self.send("550 Não consegui criar")

    c_xmkd = c_mkd

    async def c_rmd(self, a):
        if not await self._w():
            return
        vp = self.vpath(a)
        try:
            if vp == "/":
                raise PermissionError
            os.rmdir(self.real(vp))
            await self.send("250 Pasta apagada")
        except (PermissionError, OSError):
            await self.send("550 Não consegui apagar (precisa estar vazia)")

    c_xrmd = c_rmd

    async def c_rnfr(self, a):
        if not await self._w():
            return
        try:
            rp = self.real(self.vpath(a))
        except PermissionError:
            await self.send("550 Sem acesso")
            return
        if not os.path.exists(rp):
            await self.send("550 Não existe")
            return
        self.rnfr = rp
        await self.send("350 Pronto para o novo nome")

    async def c_rnto(self, a):
        if not self.rnfr:
            await self.send("503 Use RNFR antes")
            return
        try:
            os.replace(self.rnfr, self.real(self.vpath(a)))
            await self.send("250 Renomeado")
        except (PermissionError, OSError):
            await self.send("553 Não consegui renomear")
        self.rnfr = None

    async def c_abor(self, a):
        if self.pending:
            self.pending[1]()
            self.pending = None
        await self.send("226 Abortado")

    async def c_stat(self, a):
        await self.send(f"211 BastiON FTP — {self.user} em {self.cwd}")

    async def c_site(self, a):
        await self.send("200 OK" if a.upper().startswith(("CHMOD", "UMASK")) else "502 SITE não suportado")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- portas no BastiON e nos jumps
DEFAULTS = {"enabled": True, "local_enabled": True, "local_port": 2121, "local_pasv": "30000-30009", "local_ip": "",
            "jump_port": 2121, "jump_pasv_start": 2122, "jump_pasv_count": 8, "max_mb": 2048, "agents": {}}


def parse_range(spec: str, limit: int = 50) -> List[int]:
    out = []
    for part in str(spec or "").replace(" ", "").split(","):
        if "-" in part:
            a, _, b = part.partition("-")
            if a.isdigit() and b.isdigit():
                out += list(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.append(int(part))
    return [p for p in out if 1024 <= p <= 65535][:limit]


def clean_settings(body: dict, cur: Optional[dict] = None) -> dict:
    cur = cur or {}
    out = {**DEFAULTS, **{k: cur[k] for k in DEFAULTS if k in cur}, **{k: body[k] for k in DEFAULTS if k in body}}
    for k in ("enabled", "local_enabled"):
        out[k] = bool(out[k])
    for k, lo in (("local_port", 1), ("jump_port", 1024), ("jump_pasv_start", 1024)):
        v = int(out[k] or 0)
        if not lo <= v <= 65535:
            raise ValueError(f"Porta inválida: {k}={v}" + (" (no jump, porta abaixo de 1024 só com root: use 2121 e redirecione)" if k.startswith("jump") else ""))
        out[k] = v
    out["jump_pasv_count"] = max(0, min(32, int(out["jump_pasv_count"] or 0)))
    if not parse_range(out["local_pasv"]):
        raise ValueError("Faixa passiva do BastiON inválida (ex.: 30000-30009)")
    ip = str(out["local_ip"] or "").strip()
    if ip:
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            raise ValueError("IP do BastiON para o modo passivo inválido")
    out["local_ip"] = ip
    out["max_mb"] = max(1, min(102400, int(out["max_mb"] or 2048)))
    ag = {}
    for k, v in (out.get("agents") or {}).items():
        v = v or {}
        aip = str(v.get("ip") or "").strip()
        if aip:
            try:
                aip = str(ipaddress.ip_address(aip))
            except ValueError:
                raise ValueError(f"IP do jump inválido: {aip}")
        ag[str(k)[:40]] = {"ip": aip}
    out["agents"] = ag
    return out


class JumpBinding:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.conn = None
        self.closer = None
        self.listeners: list = []
        self.net: Optional[JumpNet] = None
        self.state = "parado"
        self.error = ""
        self.bound = ""             # endereço em que o sshd do jump abriu a porta (0.0.0.0 ou só 127.0.0.1)
        self.ip = ""                # IP anunciado no modo passivo
        self.ips: List[str] = []    # IPs do jump (para escolher)
        self.since = ""
        self.sessions = 0

    def alive(self) -> bool:
        return self.conn is not None and getattr(self.conn, "_transport", None) is not None and self.state == "no ar"

    async def stop(self):
        for l in self.listeners:
            try:
                l.close()
            except Exception:
                pass
        self.listeners = []
        if self.closer:
            try:
                await self.closer()
            except Exception:
                pass
        self.conn = self.closer = self.net = None
        if self.state == "no ar":
            self.state = "parado"


class Manager:
    """Mantém o FTP no ar onde há acesso ativo: no próprio BastiON e em cada jump com acesso."""

    def __init__(self, auth, on_event, connect_jump, root: str):
        self.auth, self.on_event, self.connect_jump, self.root = auth, on_event, connect_jump, root
        self.cfg = dict(DEFAULTS)
        self.local_srv = None
        self.local_net: Optional[LocalNet] = None
        self.local_state, self.local_error = "parado", ""
        self.jumps: Dict[str, JumpBinding] = {}
        self.lock = asyncio.Lock()

    def _session(self, r, w, peer, net, scope, label):
        async def auth(u, p, ip):
            return await self.auth(scope, u, p, ip)

        async def ev(e):
            await self.on_event({**e, "scope": scope})
        return Session(r, w, peer, net, auth, ev, label=label)

    async def reconcile(self, cfg: dict, local_needed: bool, agents_needed: List[str]):
        async with self.lock:
            self.cfg = cfg
            want_local = cfg["enabled"] and cfg["local_enabled"] and local_needed
            await self._local(want_local)
            want = set(agents_needed) if cfg["enabled"] else set()
            for aid in list(self.jumps):
                if aid not in want:
                    await self.jumps.pop(aid).stop()
            for aid in want:
                b = self.jumps.setdefault(aid, JumpBinding(aid))
                if not b.alive():
                    await self._start_jump(b)

    async def _local(self, want: bool):
        port = self.cfg["local_port"]
        if self.local_srv and (not want or getattr(self, "_lport", None) != port):
            self.local_srv.close()
            self.local_srv, self.local_state = None, "parado"
        if want and not self.local_srv:
            ip = self.cfg.get("local_ip") or ""
            self.local_net = LocalNet(ip, parse_range(self.cfg["local_pasv"]))

            async def on_conn(r, w):
                peer = str((w.get_extra_info("peername") or ("?",))[0]).replace("::ffff:", "")
                net = self.local_net
                if not net.pasv_ip:          # sem IP configurado: o mesmo IP em que o equipamento conectou
                    sock = w.get_extra_info("sockname") or ("",)
                    net = LocalNet(str(sock[0]).replace("::ffff:", ""), net.ports)
                    net.busy = self.local_net.busy
                await self._session(r, w, peer, net, "local", "BastiON").run()
            try:
                self.local_srv = await asyncio.start_server(on_conn, host=None, port=port, reuse_address=True)
                self._lport = port
                self.local_state, self.local_error = "no ar", ""
            except OSError as e:
                self.local_state, self.local_error = "erro", f"porta {port}: {e}"

    async def _start_jump(self, b: JumpBinding):
        await b.stop()
        b.state, b.error = "conectando", ""
        try:
            conn, closer, label = await self.connect_jump(b.agent_id)
        except Exception as e:
            b.state, b.error = "erro", f"não conectou no jump: {str(e)[:200]}"
            return
        b.conn, b.closer = conn, closer
        port, pstart, pcount = self.cfg["jump_port"], self.cfg["jump_pasv_start"], self.cfg["jump_pasv_count"]
        try:
            b.ips = await self._jump_ips(conn)
        except Exception:
            b.ips = []
        b.ip = ((self.cfg.get("agents") or {}).get(b.agent_id) or {}).get("ip") or (b.ips[0] if b.ips else "")
        net = JumpNet(conn, b.ip, [])
        b.net = net

        def control_factory(orig_host, orig_port):
            async def handler(r, w):
                b.sessions += 1
                try:
                    await self._session(r, w, orig_host, net, b.agent_id, label).run()
                finally:
                    b.sessions -= 1
            return handler
        try:
            b.listeners.append(await conn.start_server(control_factory, "", port))
        except Exception as e:
            log.debug("listener", exc_info=True)
            b.state, b.error = "erro", f"o jump recusou abrir a porta {port}: {str(e)[:160]} (AllowTcpForwarding no sshd do jump?)"
            await b.stop()
            b.state = "erro"
            return
        for p in range(pstart, pstart + pcount):
            try:
                b.listeners.append(await conn.start_server(lambda oh, op, p=p: net.incoming(p, oh), "", p))
                net.ports.append(p)
            except Exception:
                pass                                  # porta ocupada no jump: o passivo usa as outras
        try:
            b.bound = await self._bound(conn, port)
        except Exception:
            b.bound = ""
        b.state, b.since = "no ar", now_iso()

    @staticmethod
    async def _run(conn, cmd: str) -> str:
        r = await asyncio.wait_for(conn.run(cmd, check=False), 10)
        out = r.stdout or ""
        return out.decode("utf-8", "replace") if isinstance(out, bytes) else out

    async def _jump_ips(self, conn) -> List[str]:
        out = await self._run(conn, "hostname -I 2>/dev/null || ip -4 -o addr show 2>/dev/null")
        ips = []
        for tok in out.replace("/", " ").split():
            try:
                a = ipaddress.ip_address(tok)
            except ValueError:
                continue
            if a.version == 4 and not a.is_loopback and str(a) not in ips:
                ips.append(str(a))
        return ips

    async def _bound(self, conn, port: int) -> str:
        out = await self._run(conn, f"ss -ltnH 2>/dev/null | grep ':{port} ' || netstat -ltn 2>/dev/null | grep ':{port} '")
        addrs = set()
        for ln in out.splitlines():
            for tok in ln.split():
                if tok.endswith(f":{port}"):
                    addrs.add(tok.rsplit(":", 1)[0].strip("[]"))
        if not addrs:
            return ""
        if addrs & {"0.0.0.0", "*", "::", ""}:
            return "0.0.0.0"
        return ", ".join(sorted(addrs))

    def status(self) -> dict:
        return {"local": {"state": self.local_state, "error": self.local_error, "port": self.cfg.get("local_port")},
                "jumps": {aid: {"state": b.state, "error": b.error, "bound": b.bound, "ip": b.ip, "ips": b.ips, "since": b.since,
                                "sessions": b.sessions, "pasv_ports": b.net.ports if b.net else []} for aid, b in self.jumps.items()}}

    async def stop_all(self):
        if self.local_srv:
            self.local_srv.close()
        for b in self.jumps.values():
            await b.stop()
