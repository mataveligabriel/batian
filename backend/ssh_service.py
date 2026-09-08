"""SSH connection manager using asyncssh - multi-hop ProxyJump chains, legacy algorithms and shell-mode exec."""
import asyncio
import re
import time
from typing import Optional, List

import asyncssh

# device types that usually only speak old kex/ciphers
LEGACY_TYPES = {"mikrotik", "cisco", "huawei", "ubiquiti", "datacom", "zte", "other"}
# device types without a proper "exec" channel -> run commands inside an interactive shell
SHELL_EXEC_TYPES = {"cisco", "huawei", "datacom", "zte", "mikrotik"}
# commands sent before batch commands in shell mode to disable pagination
PAGINATION_OFF = {
    "cisco": "terminal length 0",
    "huawei": "screen-length 0 temporary",
    "datacom": "terminal length 0",
    "zte": "terminal length 0",
}

LEGACY_ALGS = dict(
    kex_algs="+diffie-hellman-group1-sha1,diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1",
    encryption_algs="+aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc",
    mac_algs="+hmac-sha1,hmac-sha1-96,hmac-md5",
    server_host_key_algs="+ssh-rsa,ssh-dss",
)

PROMPT_RE = re.compile(rb"[\r\n][^\r\n]{0,120}[#>$%\]] ?\s*$")


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

    async def run_command(self, command: str, timeout: int = 60) -> dict:
        if self.device_type in SHELL_EXEC_TYPES:
            return await self._run_in_shell(command, timeout)
        try:
            result = await asyncio.wait_for(self.conn.run(command, check=False), timeout=timeout)
            return {"stdout": result.stdout or "", "stderr": result.stderr or "",
                    "exit_status": result.exit_status if result.exit_status is not None else 0,
                    "ok": (result.exit_status or 0) == 0}
        except asyncio.TimeoutError:
            return {"stdout": "", "stderr": "Timeout", "exit_status": -1, "ok": False}
        except asyncssh.ChannelOpenError:
            return await self._run_in_shell(command, timeout)

    async def _read_until_idle(self, proc, idle: float = 1.2, hard: float = 60) -> bytes:
        buf = b""
        deadline = time.monotonic() + hard
        while time.monotonic() < deadline:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(65536), timeout=idle)
                if not chunk:
                    break
                buf += chunk
                if PROMPT_RE.search(buf[-200:]):
                    await asyncio.sleep(0.15)
                    continue
            except asyncio.TimeoutError:
                if buf:
                    break
        return buf

    async def _run_in_shell(self, command: str, timeout: int) -> dict:
        proc = await self.conn.create_process(term_type="vt100", term_size=(200, 100), encoding=None)
        try:
            await self._read_until_idle(proc, idle=1.5, hard=10)
            pre = PAGINATION_OFF.get(self.device_type)
            if pre:
                proc.stdin.write((pre + "\n").encode())
                await self._read_until_idle(proc, idle=1.0, hard=8)
            out = b""
            for line in command.splitlines():
                if not line.strip():
                    continue
                proc.stdin.write((line + "\n").encode())
                out += await self._read_until_idle(proc, idle=1.5, hard=timeout)
            text = _clean_ansi(out.decode("utf-8", "replace"))
            return {"stdout": text, "stderr": "", "exit_status": 0, "ok": True}
        finally:
            try:
                proc.close()
            except Exception:
                pass

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


def _clean_ansi(s: str) -> str:
    s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)
    return s.replace("\r", "")


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
