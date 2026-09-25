"""Telnet client with the same interface as SSHClientWrapper (connect / open_shell / run_command / close).
Reaches the target directly or through the last SSH hop of an agent chain (open_connection on that hop)."""
import asyncio
import re
import time
from typing import Optional, List

import asyncssh

from ssh_service import Hop, SSHClientWrapper, _clean_ansi, PROMPT_RE

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
OPT_ECHO, OPT_SGA, OPT_TTYPE, OPT_NAWS = 1, 3, 24, 31
LOGIN_RE = re.compile(rb"(?i)(user ?name|login)\s*:\s*$")
PASS_RE = re.compile(rb"(?i)pass(word|phrase)?\s*:\s*$")


class _Reader:
    def __init__(self, q: asyncio.Queue):
        self.q = q

    async def read(self, n: int = 4096) -> bytes:
        return await self.q.get()


class _NullReader:
    async def read(self, n: int = 4096) -> bytes:
        await asyncio.sleep(3600)
        return b""


class _Writer:
    def __init__(self, session):
        self.session = session

    def write(self, data: bytes):
        self.session._send(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n"))


class TelnetClientWrapper:
    def __init__(self, hops: List[Hop], host: str, port: int = 23, username: str = "",
                 password: Optional[str] = None, device_type: str = "other", label: str = ""):
        self.hops = hops
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.device_type = device_type
        self.label = label or host
        self.ssh: Optional[SSHClientWrapper] = None
        self.reader = None
        self.writer = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.process = None
        self._pump: Optional[asyncio.Task] = None
        self._login_done = False
        self._tail = b""
        self.cols, self.rows = 120, 32

    async def connect(self):
        try:
            if self.hops:
                self.ssh = SSHClientWrapper(self.hops)
                await self.ssh.connect()
                self.reader, self.writer = await asyncio.wait_for(self.ssh.conn.open_connection(self.host, self.port), timeout=20)
            else:
                self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout=20)
        except Exception as e:
            await self.close()
            raise RuntimeError(f"Falha ao conectar via Telnet em {self.label} ({self.host}:{self.port}): {e}") from e

    def _send(self, data: bytes):
        if self.writer:
            try:
                self.writer.write(data)
            except Exception:
                pass

    def _negotiate(self, data: bytes) -> bytes:
        """Strip IAC sequences, answering negotiations; returns clean payload."""
        out = bytearray()
        buf = self._tail + data
        self._tail = b""
        i = 0
        while i < len(buf):
            b = buf[i]
            if b != IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= len(buf):
                self._tail = buf[i:]
                break
            cmd = buf[i + 1]
            if cmd == IAC:
                out.append(IAC)
                i += 2
                continue
            if cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(buf):
                    self._tail = buf[i:]
                    break
                opt = buf[i + 2]
                if cmd == DO:
                    if opt == OPT_TTYPE:
                        self._send(bytes([IAC, WILL, OPT_TTYPE]))
                    elif opt == OPT_NAWS:
                        self._send(bytes([IAC, WILL, OPT_NAWS]))
                        self._send_naws()
                    else:
                        self._send(bytes([IAC, WONT, opt]))
                elif cmd == WILL:
                    self._send(bytes([IAC, DO if opt in (OPT_ECHO, OPT_SGA) else DONT, opt]))
                i += 3
                continue
            if cmd == SB:
                end = buf.find(bytes([IAC, SE]), i)
                if end == -1:
                    self._tail = buf[i:]
                    break
                sub = buf[i + 2:end]
                if sub[:2] == bytes([OPT_TTYPE, 1]):
                    self._send(bytes([IAC, SB, OPT_TTYPE, 0]) + b"XTERM" + bytes([IAC, SE]))
                i = end + 2
                continue
            i += 2
        return bytes(out)

    def _send_naws(self):
        c, r = self.cols, self.rows
        self._send(bytes([IAC, SB, OPT_NAWS, c >> 8, c & 255, r >> 8, r & 255, IAC, SE]))

    def _auto_login(self, chunk: bytes):
        if self._login_done or not self.password:
            return
        tail = (self._tail_text + chunk)[-160:]
        self._tail_text = tail
        if self.username and LOGIN_RE.search(tail.strip()[-40:] or b"x"):
            self._send(self.username.encode() + b"\r\n")
            self._tail_text = b""
        elif PASS_RE.search(tail.strip()[-40:] or b"x"):
            self._send(self.password.encode() + b"\r\n")
            self._login_done = True

    _tail_text = b""

    async def _pump_loop(self):
        try:
            while True:
                data = await self.reader.read(4096)
                if not data:
                    break
                clean = self._negotiate(data)
                if clean:
                    self._auto_login(clean)
                    await self.queue.put(clean)
        except Exception:
            pass
        finally:
            await self.queue.put(b"")

    async def open_shell(self, cols: int = 120, rows: int = 32):
        self.cols, self.rows = cols, rows
        self._pump = asyncio.create_task(self._pump_loop())
        self.process = type("TelnetProcess", (), {})()
        self.process.stdout = _Reader(self.queue)
        self.process.stderr = _NullReader()
        self.process.stdin = _Writer(self)
        self.process.close = lambda: None
        self.process.change_terminal_size = lambda c, r: self.resize_sync(c, r)
        return self.process

    def resize_sync(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self._send_naws()

    async def resize(self, cols: int, rows: int):
        self.resize_sync(cols, rows)

    async def _read_until_idle(self, idle: float = 1.5, hard: float = 60) -> bytes:
        buf = b""
        deadline = time.monotonic() + hard
        while time.monotonic() < deadline:
            wait = min(idle, 1.0) if buf and PROMPT_RE.search(buf[-200:]) else idle
            try:
                chunk = await asyncio.wait_for(self.queue.get(), timeout=wait)
                if not chunk:
                    break
                buf += chunk
            except asyncio.TimeoutError:
                if buf:
                    break
        return buf

    async def run_command(self, command: str, timeout: int = 60, idle: float = 1.5) -> dict:
        if not self._pump:
            await self.open_shell()
        await self._read_until_idle(idle=2.0, hard=15)
        from ssh_service import PAGINATION_OFF
        pre = PAGINATION_OFF.get(self.device_type)
        if pre:
            self._send(pre.encode() + b"\r\n")
            await self._read_until_idle(idle=1.0, hard=8)
        out = b""
        for line in command.splitlines():
            if line.strip():
                self._send(line.encode() + b"\r\n")
                out += await self._read_until_idle(idle=idle, hard=timeout)
        return {"stdout": _clean_ansi(out.decode("utf-8", "replace")), "stderr": "", "exit_status": 0, "ok": True}

    async def tcp_check(self, host: str, port: int, timeout: float = 5.0):
        return None

    async def close(self):
        if self._pump:
            self._pump.cancel()
        try:
            if self.writer:
                self.writer.close()
        except Exception:
            pass
        if self.ssh:
            await self.ssh.close()
        self.ssh = None
        self.writer = None
