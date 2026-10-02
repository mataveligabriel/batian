"""SSH connection manager using asyncssh - multi-hop ProxyJump chains, legacy algorithms and shell-mode exec."""
import asyncio
import re
import time
from typing import Optional, List

import asyncssh

# device types that usually only speak old kex/ciphers
LEGACY_TYPES = {"mikrotik", "cisco", "huawei", "juniper", "ubiquiti", "datacom", "zte", "other"}
# device types without a proper "exec" channel -> run commands inside an interactive shell
SHELL_EXEC_TYPES = {"cisco", "huawei", "juniper", "datacom", "zte", "mikrotik"}
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
PAGER_RE = re.compile(rb"(?:-+\s*\(?more(?:\s+\d+%)?\)?\s*-+|<-+\s*more\s*-+>|\(more\)|press any key to continue|"
                      rb"--more--\s*or\s*\(q\)uit)[^\r\n]{0,40}$", re.I)
_E = r"\x1b\[[0-9;]*[A-Za-z]"
# marca de paginação + o "apaga" que vem depois dela (backspaces/espaços/backspaces, \r espaços \r ou ESC[nD espaços ESC[nD).
# Não come os espaços seguintes: são a indentação da próxima linha da configuração.
_PAGER_TXT = re.compile(r"[ \t]*(?:-+\s*\(?more(?:\s+\d+%)?\)?\s*-+|<-+\s*more\s*-+>|\(more\)|press any key to continue)"
                        r"[^\n\x08\r\x1b]{0,40}(?:\x08+ *\x08+|\x08+|\r[ \t]*\r|\r(?!\n)|(?:" + _E + r" *)+" + _E + r"|" + _E + r")?", re.I)
MAX_PAGES = 20000


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
        except asyncio.TimeoutError:
            if buf:
                break
    return buf



class Hop:
    def __init__(self, host: str, port: int, username: str, private_key: Optional[str] = None,
                 password: Optional[str] = None, legacy: bool = False, label: str = ""):
        self.host = host
        self.port = port
        self.username = username
        self.private_key = private_key
        self.password = password
        self.legacy = legacy
        self.label = label or host

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


class SSHClientWrapper:
    """Interactive SSH session to the last hop of a chain (hops[:-1] are jump hosts)."""

    def __init__(self, hops: List[Hop], device_type: str = "linux"):
        assert hops, "at least one hop required"
        self.hops = hops
        self.device_type = device_type
        self.conns: List[asyncssh.SSHClientConnection] = []
        self.conn: Optional[asyncssh.SSHClientConnection] = None
        self.process: Optional[asyncssh.SSHClientProcess] = None

    async def connect(self):
        tunnel = None
        for hop in self.hops:
            try:
                if tunnel is None:
                    conn = await asyncssh.connect(hop.host, port=hop.port, **hop.connect_kwargs())
                else:
                    conn = await tunnel.connect_ssh(hop.host, port=hop.port, **hop.connect_kwargs())
            except Exception as e:
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
        self.conns = []
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
