"""SSH connection manager using asyncssh - supports ProxyJump (bastion) and custom ports."""
import asyncio
import asyncssh
import os
import io
from typing import Optional


class SSHClientWrapper:
    """Handles a single interactive SSH session with optional ProxyJump."""

    def __init__(self, host: str, port: int, username: str, private_key: Optional[str],
                 password: Optional[str] = None,
                 jump_host: Optional[str] = None, jump_port: int = 22,
                 jump_user: Optional[str] = None, jump_key: Optional[str] = None):
        self.host = host
        self.port = port
        self.username = username
        self.private_key = private_key
        self.password = password
        self.jump_host = jump_host
        self.jump_port = jump_port
        self.jump_user = jump_user
        self.jump_key = jump_key
        self.conn: Optional[asyncssh.SSHClientConnection] = None
        self.process: Optional[asyncssh.SSHClientProcess] = None
        self.tunnel: Optional[asyncssh.SSHClientConnection] = None

    @staticmethod
    def _load_key(pem: Optional[str]):
        if not pem:
            return None
        try:
            return asyncssh.import_private_key(pem)
        except Exception:
            return None

    async def connect(self):
        client_keys = []
        k = self._load_key(self.private_key)
        if k:
            client_keys.append(k)

        connect_kwargs = dict(
            username=self.username,
            known_hosts=None,
            client_keys=client_keys or None,
            password=self.password,
            connect_timeout=15,
        )

        if self.jump_host:
            # First open connection to jump host
            jump_keys = []
            jk = self._load_key(self.jump_key or self.private_key)
            if jk:
                jump_keys.append(jk)
            self.tunnel = await asyncssh.connect(
                self.jump_host, port=self.jump_port,
                username=self.jump_user or self.username,
                client_keys=jump_keys or None,
                known_hosts=None,
                connect_timeout=15,
            )
            self.conn = await self.tunnel.connect_ssh(
                self.host, port=self.port, **connect_kwargs
            )
        else:
            self.conn = await asyncssh.connect(self.host, port=self.port, **connect_kwargs)

    async def open_shell(self, cols: int = 120, rows: int = 32):
        assert self.conn is not None
        self.process = await self.conn.create_process(
            term_type="xterm-256color",
            term_size=(cols, rows),
            encoding=None,  # binary passthrough
        )
        return self.process

    async def resize(self, cols: int, rows: int):
        if self.process:
            try:
                self.process.change_terminal_size(cols, rows)
            except Exception:
                pass

    async def run_command(self, command: str, timeout: int = 60) -> dict:
        assert self.conn is not None
        try:
            result = await asyncio.wait_for(
                self.conn.run(command, check=False), timeout=timeout
            )
            return {
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
                "exit_status": result.exit_status,
                "ok": result.exit_status == 0,
            }
        except asyncio.TimeoutError:
            return {"stdout": "", "stderr": "Timeout", "exit_status": -1, "ok": False}

    async def close(self):
        try:
            if self.process:
                self.process.close()
        except Exception:
            pass
        try:
            if self.conn:
                self.conn.close()
                await self.conn.wait_closed()
        except Exception:
            pass
        try:
            if self.tunnel:
                self.tunnel.close()
                await self.tunnel.wait_closed()
        except Exception:
            pass


async def tcp_ping(host: str, port: int, timeout: float = 3.0) -> Optional[float]:
    """Return latency in ms if reachable via TCP, else None."""
    import time
    start = time.perf_counter()
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return round((time.perf_counter() - start) * 1000, 1)
    except Exception:
        return None
