"""Área de Trabalho Remota (RDP) no navegador.

  navegador (guacamole-common-js) ⇄ WebSocket ⇄ BastiON ⇄ guacd (container, 127.0.0.1:4822) ⇄ RDP no destino

O guacd (Apache Guacamole) fala RDP; o BastiON faz a negociação inicial (onde entram host, usuário e senha — o
navegador nunca recebe a senha salva) e depois só repassa as instruções. Destino atrás de um agente: o BastiON abre
um encaminhamento local pela cadeia SSH e manda o guacd conectar nele.
"""
import asyncio
import codecs
import os
import re
import uuid
from typing import Dict, List, Optional, Tuple

GUACD_HOST = os.environ.get("GUACD_HOST", "127.0.0.1")
GUACD_PORT = int(os.environ.get("GUACD_PORT", "4822"))
MAX_BUFFER = 32 * 1024 * 1024
_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
SECURITY = ("any", "nla", "nla-ext", "tls", "rdp", "vmconnect")
LAYOUTS = ("pt-br-qwerty", "en-us-qwerty", "pt-pt-qwerty", "es-es-qwerty", "es-latam-qwerty", "failsafe")


class RdpError(Exception):
    pass


def enc(*elements) -> str:
    """Instrução do protocolo Guacamole: 'tamanho.valor,tamanho.valor;' (tamanho em caracteres, não bytes)."""
    return ",".join(f"{len(str(e))}.{e}" for e in elements) + ";"


def split_instructions(buf: str) -> Tuple[List[str], str]:
    """Separa as instruções completas do começo do buffer. Devolve (instruções, resto incompleto)."""
    out, start, i, n = [], 0, 0, len(buf)
    while i < n:
        dot = buf.find(".", i, i + 12)
        if dot < 0:
            if n - i > 11:
                raise RdpError("fluxo do guacd corrompido")
            break
        try:
            ln = int(buf[i:dot])
        except ValueError:
            raise RdpError("fluxo do guacd corrompido")
        end = dot + 1 + ln
        if end >= n:
            break
        t = buf[end]
        if t == ";":
            out.append(buf[start:end + 1])
            start = i = end + 1
        elif t == ",":
            i = end + 1
        else:
            raise RdpError("fluxo do guacd corrompido")
    return out, buf[start:]


def parse(instr: str) -> List[str]:
    out, i = [], 0
    while i < len(instr):
        dot = instr.index(".", i)
        ln = int(instr[i:dot])
        out.append(instr[dot + 1:dot + 1 + ln])
        i = dot + 2 + ln
    return out


def clean_target(host: str, port) -> Tuple[str, int]:
    host = (host or "").strip().strip("[]")
    if not host or not (_HOST_RE.match(host) or re.match(r"^[0-9A-Fa-f:]{2,45}$", host)):
        raise RdpError("Informe o IP ou o nome do computador (ex.: 10.0.0.15 ou servidor.empresa.local)")
    try:
        port = int(port or 3389)
    except (TypeError, ValueError):
        raise RdpError("Porta inválida")
    if not 1 <= port <= 65535:
        raise RdpError("Porta inválida")
    return host, port


def split_user(username: str, domain: str) -> Tuple[str, str]:
    """Aceita 'DOMINIO\\usuario' no campo usuário. 'usuario@dominio' segue inteiro (UPN)."""
    username, domain = (username or "").strip(), (domain or "").strip()
    if "\\" in username and not domain:
        domain, username = username.split("\\", 1)
    return username, domain


def clamp_size(w, h) -> Tuple[int, int]:
    try:
        w, h = int(w), int(h)
    except (TypeError, ValueError):
        w, h = 1280, 720
    w, h = max(640, min(w, 3840)), max(480, min(h, 2160))
    return w - w % 2, h - h % 2


def rdp_params(host: str, port: int, username: str, password: str, domain: str, width: int, height: int,
               security: str = "any", layout: str = "pt-br-qwerty", admin: bool = False) -> Dict[str, str]:
    return {
        "hostname": host, "port": str(port), "username": username, "password": password, "domain": domain,
        "security": security if security in SECURITY else "any", "ignore-cert": "true",
        "width": str(width), "height": str(height), "dpi": "96", "color-depth": "24",
        "resize-method": "display-update", "server-layout": layout if layout in LAYOUTS else "pt-br-qwerty",
        "disable-audio": "true", "enable-font-smoothing": "true", "enable-wallpaper": "false",
        "enable-theming": "true", "console": "true" if admin else "",
        "client-name": "BastiON",
    }


_ERR_TEXT = {
    "519": "o computador não respondeu (RDP desligado, firewall ou IP errado)",
    "769": "usuário ou senha recusados (ou a conta não pode entrar por Área de Trabalho Remota)",
    "771": "acesso negado para este usuário",
    "776": "o computador demorou demais para responder",
    "514": "o computador demorou demais para responder",
    "797": "muitas conexões abertas para este usuário",
    "521": "sessão encerrada: outro usuário entrou no computador",
    "522": "sessão encerrada por inatividade",
    "523": "sessão encerrada pelo computador remoto",
    "512": "erro no computador remoto",
    "520": "o computador remoto recusou a conexão (confira a segurança: NLA/TLS/RDP)",
}


def friendly_error(elements: List[str]) -> str:
    """Instrução `error` do guacd → texto para o operador."""
    msg = elements[1] if len(elements) > 1 else ""
    code = elements[2] if len(elements) > 2 else ""
    base = _ERR_TEXT.get(code)
    return f"{base} [{msg}]" if base and msg else base or msg or f"erro {code}"


class Guacd:
    """Conexão com o guacd já negociada (até o `ready`). Depois é só ler instruções e escrever o que vier do navegador."""

    def __init__(self):
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")
        self._buf = ""
        self._pending: List[str] = []
        self.id = ""

    async def _next(self, timeout: float = 30) -> List[str]:
        while not self._pending:
            data = await asyncio.wait_for(self.reader.read(65536), timeout)
            if not data:
                raise RdpError("o guacd fechou a conexão")
            self._buf += self._dec.decode(data)
            self._pending, self._buf = split_instructions(self._buf)
        return parse(self._pending.pop(0))

    async def open(self, params: Dict[str, str], width: int, height: int, protocol: str = "rdp"):
        try:
            self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(GUACD_HOST, GUACD_PORT), 6)
        except Exception:
            raise RdpError("O serviço de Área de Trabalho Remota (guacd) não está no ar neste servidor — rode o update.sh "
                           "e confira com: docker compose logs guacd")
        self.writer.write(enc("select", protocol).encode())
        args = await self._next(15)
        if not args or args[0] != "args":
            raise RdpError(friendly_error(args) if args and args[0] == "error" else "o guacd não aceitou o protocolo RDP")
        names = args[1:]
        self.writer.write((enc("size", width, height, 96) + enc("audio") + enc("video") +
                           enc("image", "image/png", "image/jpeg", "image/webp") + enc("timezone", "America/Sao_Paulo")).encode())
        values = [n if n.startswith("VERSION_") else params.get(n, "") for n in names]
        self.writer.write(enc("connect", *values).encode())
        await self.writer.drain()
        first = await self._next(40)
        if first and first[0] == "error":
            raise RdpError(friendly_error(first))
        if first and first[0] == "ready":
            self.id = first[1] if len(first) > 1 else ""
        elif first:                                        # guacd antigo sem `ready`: a instrução já é da sessão
            self._pending.insert(0, enc(*first))

    async def read(self) -> Optional[str]:
        """Próximo bloco de instruções completas (texto pronto para um quadro WebSocket). None = guacd encerrou."""
        if self._pending:
            out, self._pending = "".join(self._pending), []
            return out
        while True:
            data = await self.reader.read(65536)
            if not data:
                return None
            self._buf += self._dec.decode(data)
            if len(self._buf) > MAX_BUFFER:
                raise RdpError("fluxo do guacd grande demais")
            done, self._buf = split_instructions(self._buf)
            if done:
                return "".join(done)

    def write(self, text: str):
        self.writer.write(text.encode())

    async def close(self):
        try:
            if self.writer:
                self.writer.write(enc("disconnect").encode())
                self.writer.close()
        except Exception:
            pass


def tunnel_hello() -> str:
    """Primeira mensagem que o cliente JS espera: identificador do túnel (instrução interna, opcode vazio)."""
    return enc("", str(uuid.uuid4()))


def is_internal(msg: str) -> bool:
    return msg.startswith("0.,")
