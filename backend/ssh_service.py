"""SSH connection manager using asyncssh - multi-hop ProxyJump chains, legacy algorithms and shell-mode exec."""
import asyncio
import hashlib
import os
import re
import time
from typing import Dict, Optional, List

import asyncssh

# device types that usually only speak old kex/ciphers
LEGACY_TYPES = {"mikrotik", "cisco", "huawei", "juniper", "ubiquiti", "datacom", "zte", "fiberhome", "other"}
# device types without a proper "exec" channel -> run commands inside an interactive shell
SHELL_EXEC_TYPES = {"cisco", "huawei", "juniper", "datacom", "zte", "mikrotik", "fiberhome"}
# commands sent before batch commands in shell mode to disable pagination
PAGINATION_OFF = {
    "cisco": "terminal length 0",
    "huawei": "screen-length 0 temporary",
    "datacom": "paginate false\nterminal length 0",   # DmOS usa "paginate false"; os Datacom antigos, "terminal length 0"
    "zte": "terminal length 0",
    "juniper": "set cli screen-length 0",
}

LEGACY_ALGS = dict(
    kex_algs="+diffie-hellman-group1-sha1,diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1",
    encryption_algs="+aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc",
    mac_algs="+hmac-sha1,hmac-sha1-96,hmac-md5",
    server_host_key_algs="+ssh-rsa,ssh-dss",
)

# Paginação que sobrou ligada ("--More--", "---- More ----", "---(more 42%)---", "Press any key…"):
# o leitor manda espaço sozinho até a saída acabar, em qualquer fabricante.
PAGER_RE = re.compile(rb"(?:-+\s*\(?more(?:\s+\d+%)?\)?\s*-+|-+\s*more\s*\([^)\r\n]{0,40}\)\s*-+|<-+\s*more\s*-+>|\(more\)|press any key to continue|"
                      rb"--more--\s*or\s*\(q\)uit)[^\r\n]{0,40}$", re.I)
_E = r"\x1b\[[0-9;]*[A-Za-z]"
# marca de paginação + o "apaga" que vem depois dela (backspaces/espaços/backspaces, \r espaços \r ou ESC[nD espaços ESC[nD).
# Não come os espaços seguintes: são a indentação da próxima linha da configuração.
_PAGER_TXT = re.compile(r"[ \t]*(?:-+\s*\(?more(?:\s+\d+%)?\)?\s*-+|-+\s*more\s*\([^)\r\n]{0,40}\)\s*-+|<-+\s*more\s*-+>|\(more\)|press any key to continue)"
                        r"[^\n\x08\r\x1b]{0,40}(?:\x08+ *\x08+|\x08+|\r[ \t]*\r|\r(?!\n)|(?:" + _E + r" *)+" + _E + r"|" + _E + r")?", re.I)
MAX_PAGES = 20000


# OLT Huawei (SmartAX) pergunta os parâmetros opcionais: "{ <cr>|ontid<U><0,127> }:" — Enter aceita o padrão.
HW_OPT_RE = re.compile(rb"\{\s*<cr>[^{}\r\n]{0,200}\}\s*:\s*$", re.I)


def at_hw_opt(buf: bytes) -> bool:
    return bool(HW_OPT_RE.search(re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", buf[-260:]).rstrip(b" \t\x00")))


def at_pager(buf: bytes) -> bool:
    tail = re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", buf[-160:]).rstrip(b" \t\x00\x08")
    return bool(PAGER_RE.search(tail))


PROMPT_RE = re.compile(rb"[\r\n][^\r\n]{0,120}[#>$%\]] ?\s*$")

PATIENCE = 12.0      # sem o prompt de volta: quanto silêncio aceitar no meio de uma saída (equipamento "pensando")
PAGE_WAIT = 25.0     # depois de pedir a próxima página, quanto esperar ela chegar
_QUESTION = re.compile(r"(\[y/n\]|\(y/n\)|yes/no|\by\|n\b|password|senha|username|login|continue\?|confirm|[:?])\s*$", re.I)
_ANSI_B = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")


def learn_prompt(buf: bytes) -> Optional[str]:
    """Do que o equipamento mostrou ao entrar, tira o 'miolo' do prompt (o nome do host):
    'OLT-PENHA#' → 'OLT-PENHA', '<SW-CGS>' → 'SW-CGS', '[admin@MikroTik] >' → 'admin@MikroTik'."""
    text = _ANSI_B.sub(b"", buf).decode("utf-8", "replace").replace("\r", "\n")
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return None
    last = lines[-1]
    if len(last) > 80 or last[-1] not in "#>]$%":
        return None
    base = re.sub(r"[#>\]$%\s]+$", "", last).lstrip("<[").strip()
    base = re.sub(r"\(.*$", "", base).strip()            # 'host(config)' → 'host'
    return base if len(base) >= 2 else None


def at_prompt(buf: bytes, base: str) -> bool:
    """A última linha é o prompt deste equipamento (em qualquer modo: 'host#', '[host-if]', 'host(config)#')."""
    tail = _ANSI_B.sub(b"", buf[-400:]).decode("utf-8", "replace").replace("\r", "\n")
    if tail.endswith("\n"):
        return False
    last = tail.split("\n")[-1].strip()
    return bool(last) and len(last) <= 160 and last[-1] in "#>]$%" and base in last


async def read_reply(read, send, idle: float, hard: float, prompt: Optional[str] = None) -> bytes:
    """Lê a resposta de um comando.
    - Com o prompt conhecido: termina quando ele volta; silêncio no meio da saída é tolerado (PATIENCE).
    - Paginação ('--More--'): manda espaço e espera a próxima página com folga (PAGE_WAIT).
    - Sem prompt conhecido: regra antiga (para depois de `idle` s de silêncio).
    read(timeout) devolve um pedaço (b'' = fim) ou levanta asyncio.TimeoutError; send(bytes) escreve."""
    buf = b""
    deadline = time.monotonic() + hard
    pages, paging, mark = 0, False, 0
    while time.monotonic() < deadline:
        if paging:
            wait = PAGE_WAIT
        elif prompt and buf:
            if at_prompt(buf, prompt):
                wait = 0.35
            else:
                tail = _ANSI_B.sub(b"", buf[-200:]).decode("utf-8", "replace")
                mid_line = not tail.endswith(("\n", "\r"))
                wait = idle if (mid_line and _QUESTION.search(tail)) else (max(idle, 4.0) if mid_line else PATIENCE)
        else:
            wait = min(idle, 1.0) if buf and PROMPT_RE.search(buf[-200:]) else idle
        try:
            chunk = await read(min(wait, max(0.05, deadline - time.monotonic())))
            if not chunk:
                break
            buf += chunk
            if paging and _ANSI_B.sub(b"", chunk).strip(b" \t\r\n\x00\x08"):
                paging = False                               # chegou conteúdo de verdade (não só o "apaga" do --More--)
            if pages < MAX_PAGES and at_pager(buf[mark:]):   # saída paginada: pede a próxima página (uma vez por marca)
                pages += 1
                paging = True
                mark = len(buf)
                send(b" ")
                deadline = max(deadline, time.monotonic() + PAGE_WAIT)
            elif pages < MAX_PAGES and at_hw_opt(buf[mark:]):
                pages += 1
                paging = True
                mark = len(buf)
                send(b"\r")
                deadline = max(deadline, time.monotonic() + PAGE_WAIT)
        except asyncio.TimeoutError:
            if buf:
                break
    return buf



class Hop:
    def __init__(self, host: str, port: int, username: str, private_key: Optional[str] = None,
                 password: Optional[str] = None, legacy: bool = False, label: str = "", shared: bool = False):
        self.host = host
        self.port = port
        self.username = username
        self.private_key = private_key
        self.password = password
        self.legacy = legacy
        self.label = label or host
        # shared=True: salto que é agente/jump server — a conexão é reaproveitada (JumpPool) em vez de um login novo
        # no jump a cada acesso, ping ou coleta.
        self.shared = shared

    def pool_key(self) -> tuple:
        """Identifica o login no salto; muda se trocar usuário, senha ou chave (aí abre uma conexão nova)."""
        cred = hashlib.sha256(f"{self.password or ''}\0{self.private_key or ''}".encode()).hexdigest()[:16]
        return (self.host, int(self.port), self.username, cred)

    def connect_kwargs(self) -> dict:
        key = _load_key(self.private_key)
        kwargs = dict(
            username=self.username,
            known_hosts=None,
            client_keys=[key] if key else None,
            password=self.password or None,
            connect_timeout=20,
            login_timeout=30,
        )
        if self.legacy:
            kwargs.update(LEGACY_ALGS)
        return kwargs


def _load_key(pem: Optional[str]):
    if not pem:
        return None
    try:
        return asyncssh.import_private_key(pem)
    except Exception:
        return None


# Conexões aos jump servers (agentes) ficam abertas e são compartilhadas: cada acesso a um equipamento vira só um
# canal (direct-tcpip) dentro da conexão que já existe, sem login novo no jump. Antes, ping/coleta/acesso de N
# equipamentos atrás do mesmo agente geravam N logins "Accepted password" no auth.log do jump.
JUMP_IDLE_SECONDS = int(os.environ.get("JUMP_IDLE_SECONDS", "600") or 600)   # fecha depois de 10 min sem uso
JUMP_KEEPALIVE = 30                                                          # detecta túnel caído em ~90 s


class _PooledJump:
    __slots__ = ("conn", "refs", "timer", "dead")

    def __init__(self, conn):
        self.conn = conn
        self.refs = 0
        self.timer: Optional[asyncio.TimerHandle] = None
        self.dead = False


class JumpPool:
    def __init__(self):
        self.items: Dict[tuple, _PooledJump] = {}
        self.locks: Dict[tuple, asyncio.Lock] = {}

    @staticmethod
    def chain_key(hops: List[Hop]) -> tuple:
        return tuple(h.pool_key() for h in hops)

    def _alive(self, it: Optional[_PooledJump]) -> bool:
        if not it or it.dead:
            return False
        tr = getattr(it.conn, "_transport", True)
        return tr is not None

    async def acquire(self, hops: List[Hop], parent=None):
        """Conexão compartilhada até hops[-1] (hops = cadeia de jumps desde o BastiON). parent = conexão ao salto anterior."""
        key = self.chain_key(hops)
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            it = self.items.get(key)
            if not self._alive(it):
                if it:
                    self._drop(key, it)
                hop = hops[-1]
                kw = dict(hop.connect_kwargs(), keepalive_interval=JUMP_KEEPALIVE, keepalive_count_max=3)
                conn = await (parent.connect_ssh(hop.host, port=hop.port, **kw) if parent is not None
                              else asyncssh.connect(hop.host, port=hop.port, **kw))
                it = _PooledJump(conn)
                self.items[key] = it
                asyncio.ensure_future(self._watch(key, it))
            if it.timer:
                it.timer.cancel()
                it.timer = None
            it.refs += 1
            return it.conn

    async def _watch(self, key, it: _PooledJump):
        try:
            await it.conn.wait_closed()
        except Exception:
            pass
        it.dead = True
        if self.items.get(key) is it:
            self.items.pop(key, None)

    def release(self, key: tuple, conn):
        it = self.items.get(key)
        if not it or it.conn is not conn:
            return
        it.refs = max(0, it.refs - 1)
        if it.refs == 0 and not it.timer:
            try:
                it.timer = asyncio.get_running_loop().call_later(JUMP_IDLE_SECONDS, self._expire, key, it)
            except RuntimeError:
                self._drop(key, it)

    def invalidate(self, key: tuple, conn):
        it = self.items.get(key)
        if it and it.conn is conn:
            self._drop(key, it)

    def _expire(self, key, it: _PooledJump):
        if it.refs == 0 and self.items.get(key) is it:
            self._drop(key, it)

    def _drop(self, key, it: _PooledJump):
        it.dead = True
        if it.timer:
            it.timer.cancel()
            it.timer = None
        if self.items.get(key) is it:
            self.items.pop(key, None)
        try:
            it.conn.close()
        except Exception:
            pass

    def stats(self) -> List[dict]:
        return [{"chain": [f"{k[0]}:{k[1]}" for k in key], "in_use": it.refs} for key, it in self.items.items()]

    async def close_all(self):
        for key, it in list(self.items.items()):
            self._drop(key, it)


JUMP_POOL = JumpPool()


class _StaleJump(Exception):
    pass


class SSHClientWrapper:
    """Interactive SSH session to the last hop of a chain (hops[:-1] are jump hosts).

    Os saltos iniciais marcados shared=True (agentes) usam JUMP_POOL; os demais (o equipamento) são conexões próprias
    deste wrapper. pool=False força login novo em tudo (ex.: botão "Testar" do agente)."""

    def __init__(self, hops: List[Hop], device_type: str = "linux", pool: bool = True):
        assert hops, "at least one hop required"
        self.hops = hops
        self.device_type = device_type
        self.pool = pool
        self.conns: List[asyncssh.SSHClientConnection] = []
        self.pooled: List[tuple] = []          # (chave, conexão) emprestadas do JUMP_POOL
        self.conn: Optional[asyncssh.SSHClientConnection] = None
        self.process: Optional[asyncssh.SSHClientProcess] = None

    async def connect(self):
        try:
            await self._connect()
        except _StaleJump:
            await self.close()            # conexão compartilhada tinha caído: refaz uma vez, já sem ela no pool
            await self._connect()

    async def _connect(self):
        tunnel = None
        shared = self.pool
        for i, hop in enumerate(self.hops):
            shared = shared and hop.shared
            try:
                if shared:
                    tunnel = await JUMP_POOL.acquire(self.hops[:i + 1], tunnel)
                    self.pooled.append((JUMP_POOL.chain_key(self.hops[:i + 1]), tunnel))
                    continue
                if tunnel is None:
                    conn = await asyncssh.connect(hop.host, port=hop.port, **hop.connect_kwargs())
                else:
                    conn = await tunnel.connect_ssh(hop.host, port=hop.port, **hop.connect_kwargs())
            except Exception as e:
                last = self.pooled[-1][1] if self.pooled else None
                stale = last is not None and not getattr(self, "_retried", False) and (
                    getattr(last, "_transport", True) is None
                    or isinstance(e, (asyncssh.ConnectionLost, ConnectionError, BrokenPipeError, EOFError)))
                if stale:
                    key, c = self.pooled[-1]
                    JUMP_POOL.invalidate(key, c)
                    self._retried = True
                    raise _StaleJump() from e
                await self.close()
                raise RuntimeError(f"Falha ao conectar em {hop.label} ({hop.host}:{hop.port}): {e}") from e
            self.conns.append(conn)
            tunnel = conn
        self.conn = tunnel

    async def open_shell(self, cols: int = 120, rows: int = 32):
        assert self.conn is not None
        self.process = await self.conn.create_process(
            term_type="xterm-256color", term_size=(cols, rows), encoding=None,
        )
        return self.process

    async def resize(self, cols: int, rows: int):
        if self.process:
            try:
                self.process.change_terminal_size(cols, rows)
            except Exception:
                pass

    async def run_command(self, command: str, timeout: int = 60, idle: float = 1.5) -> dict:
        if self.device_type in SHELL_EXEC_TYPES:
            return await self._run_in_shell(command, timeout, idle)
        try:
            result = await asyncio.wait_for(self.conn.run(command, check=False), timeout=timeout)
            return {"stdout": result.stdout or "", "stderr": result.stderr or "",
                    "exit_status": result.exit_status if result.exit_status is not None else 0,
                    "ok": (result.exit_status or 0) == 0}
        except asyncio.TimeoutError:
            return {"stdout": "", "stderr": "Timeout", "exit_status": -1, "ok": False}
        except asyncssh.ChannelOpenError:
            return await self._run_in_shell(command, timeout, idle)

    async def _read_until_idle(self, proc, idle: float = 1.2, hard: float = 60, prompt: Optional[str] = None) -> bytes:
        """Lê até o prompt voltar (ou, sem prompt conhecido, até o equipamento ficar quieto)."""
        return await read_reply(lambda t: asyncio.wait_for(proc.stdout.read(65536), timeout=t), proc.stdin.write,
                                idle, hard, prompt)

    async def _run_in_shell(self, command: str, timeout: int, idle: float = 1.5) -> dict:
        proc = await self.conn.create_process(term_type="vt100", term_size=(200, 100), encoding=None)
        try:
            banner = await self._read_until_idle(proc, idle=1.5, hard=10)
            for pre in (PAGINATION_OFF.get(self.device_type) or "").splitlines():
                proc.stdin.write((pre + "\n").encode())
                banner = await self._read_until_idle(proc, idle=1.0, hard=8) or banner
            prompt = learn_prompt(banner)
            out = b""
            for line in command.splitlines():
                if not line.strip():
                    continue
                proc.stdin.write((line + "\n").encode())
                out += await self._read_until_idle(proc, idle=idle, hard=timeout, prompt=prompt)
            text = _clean_ansi(out.decode("utf-8", "replace"))
            return {"stdout": text, "stderr": "", "exit_status": 0, "ok": True}
        finally:
            try:
                proc.close()
            except Exception:
                pass

    async def shell(self) -> "ShellSession":
        """Uma única sessão de shell para vários comandos (equipamentos como Huawei VRP aceitam só
        um canal por conexão e derrubam a conexão quando o primeiro canal fecha)."""
        if self.device_type not in SHELL_EXEC_TYPES:
            return _ExecSession(self)
        s = ShellSession(self)
        await s.open()
        return s

    async def close(self):
        try:
            if self.process:
                self.process.close()
        except Exception:
            pass
        for c in reversed(self.conns):
            try:
                c.close()
                await c.wait_closed()
            except Exception:
                pass
        for key, c in reversed(self.pooled):
            JUMP_POOL.release(key, c)
        self.conns = []
        self.pooled = []
        self.conn = None

    async def tcp_check(self, host: str, port: int, timeout: float = 5.0) -> Optional[float]:
        """TCP reachability of host:port measured from the last connected hop."""
        assert self.conn is not None
        start = time.perf_counter()
        try:
            reader, writer = await asyncio.wait_for(self.conn.open_connection(host, port), timeout=timeout)
            writer.close()
            return round((time.perf_counter() - start) * 1000, 1)
        except Exception:
            return None


class ShellSession:
    """Shell interativa reaproveitada: pagina desligada uma vez, comandos em sequência no mesmo canal."""

    def __init__(self, wrapper: "SSHClientWrapper"):
        self.w = wrapper
        self.proc = None

    async def open(self):
        self.proc = await self.w.conn.create_process(term_type="vt100", term_size=(200, 100), encoding=None)
        banner = await self.w._read_until_idle(self.proc, idle=1.5, hard=10)
        for pre in (PAGINATION_OFF.get(self.w.device_type) or "").splitlines():
            self.proc.stdin.write((pre + "\n").encode())
            banner = await self.w._read_until_idle(self.proc, idle=1.0, hard=8) or banner
        self.prompt = learn_prompt(banner)

    async def run(self, command: str, timeout: int = 60, idle: float = 1.5) -> str:
        out = b""
        for line in command.splitlines():
            if line.strip():
                self.proc.stdin.write((line + "\n").encode())
                out += await self.w._read_until_idle(self.proc, idle=idle, hard=timeout, prompt=getattr(self, "prompt", None))
        return _clean_ansi(out.decode("utf-8", "replace"))

    async def close(self):
        try:
            if self.proc:
                self.proc.stdin.write(b"quit\n")
                self.proc.close()
        except Exception:
            pass


class _ExecSession:
    """Linux e afins: cada comando num canal exec próprio (suportado normalmente)."""

    def __init__(self, wrapper):
        self.w = wrapper

    async def run(self, command: str, timeout: int = 60, idle: float = 1.5) -> str:
        r = await self.w.run_command(command, timeout=timeout, idle=idle)
        out = r.get("stdout") or ""
        return out.decode("utf-8", "replace") if isinstance(out, bytes) else out

    async def close(self):
        pass


def _clean_ansi(s: str) -> str:
    s = _PAGER_TXT.sub("", s)                          # marcas de paginação e o "apaga" que vem depois
    s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)
    s = re.sub(r"[^\n]\x08", "", s) if "\x08" in s else s
    return s.replace("\x08", "").replace("\r", "")


async def tcp_ping(host: str, port: int, timeout: float = 3.0) -> Optional[float]:
    """Return latency in ms if reachable via TCP from this server, else None."""
    start = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return round((time.perf_counter() - start) * 1000, 1)
    except Exception:
        return None
