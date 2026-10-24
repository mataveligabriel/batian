"""Gravação das sessões de terminal (SSH/Telnet) para auditoria e replay.

Formato asciicast v2 (o mesmo do asciinema): 1ª linha = cabeçalho JSON; depois [segundos, "o"|"i"|"r", dados].
Grava a saída do equipamento ("o"), os redimensionamentos ("r") e, se ligado, o que foi digitado ("i" — inclui
senhas digitadas sem eco, por isso vem desligado). Durante a sessão o arquivo é texto puro (sobrevive a uma queda
do BastiON); ao terminar vira .cast.gz.

Arquivos em <dados>/recordings/AAAA/MM/<id da sessão>.cast(.gz), no volume bastion_data.
"""
import gzip
import json
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Optional

DATA_DIR = os.environ.get("BASTION_DATA_DIR", "/data")
REC_DIR = os.path.join(DATA_DIR, "recordings")
DEFAULTS = {"enabled": True, "input": False, "retention_days": 180, "max_mb": 50}


class Recorder:
    def __init__(self, session_id: str, cols: int, rows: int, title: str, record_input: bool = False, max_mb: int = 50):
        now = datetime.now(timezone.utc)
        self.rel = os.path.join(now.strftime("%Y"), now.strftime("%m"), f"{session_id}.cast")
        self.path = os.path.join(REC_DIR, self.rel)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.t0 = time.monotonic()
        self.input = record_input
        self.limit = max(1, int(max_mb)) * 1024 * 1024
        self.size = 0
        self.events = 0
        self.truncated = False
        self._last_flush = self.t0
        self.f = open(self.path, "w", encoding="utf-8")
        os.chmod(self.path, 0o600)
        self._line({"version": 2, "width": int(cols), "height": int(rows), "timestamp": int(now.timestamp()),
                    "title": title[:120], "env": {"TERM": "xterm-256color"}})

    def _line(self, obj):
        if self.truncated or self.f is None:
            return
        s = json.dumps(obj, ensure_ascii=False) + "\n"
        if self.size + len(s) > self.limit:
            self.truncated = True
            s = json.dumps([round(time.monotonic() - self.t0, 3), "o",
                            "\r\n\x1b[33m[gravação interrompida: limite de tamanho atingido]\x1b[0m\r\n"]) + "\n"
        self.f.write(s)
        self.size += len(s)
        self.events += 1
        if time.monotonic() - self._last_flush > 2:
            self.f.flush()
            self._last_flush = time.monotonic()

    def _ev(self, kind: str, data: str):
        self._line([round(time.monotonic() - self.t0, 3), kind, data])

    def output(self, data: str):
        if data:
            self._ev("o", data)

    def typed(self, data: str):
        if self.input and data:
            self._ev("i", data)

    def resize(self, cols: int, rows: int):
        self._ev("r", f"{int(cols)}x{int(rows)}")

    def close(self) -> Optional[dict]:
        """Fecha e comprime. Devolve os dados para guardar na sessão."""
        if self.f is None:
            return None
        self.f.close()
        self.f = None
        gz = self.path + ".gz"
        try:
            with open(self.path, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst)
            os.chmod(gz, 0o600)
            os.remove(self.path)
            rel = self.rel + ".gz"
        except OSError:
            gz, rel = self.path, self.rel
        return {"file": rel, "bytes": os.path.getsize(gz), "raw_bytes": self.size, "events": self.events,
                "duration": round(time.monotonic() - self.t0, 1), "input": self.input, "truncated": self.truncated}


def path_of(rel: str) -> Optional[str]:
    """Caminho seguro de uma gravação (sem sair da pasta)."""
    p = os.path.realpath(os.path.join(REC_DIR, rel or ""))
    if not p.startswith(os.path.realpath(REC_DIR) + os.sep) or not os.path.isfile(p):
        # sessão interrompida: o .gz pode não existir ainda, só o .cast
        alt = p[:-3] if p.endswith(".gz") else None
        if alt and alt.startswith(os.path.realpath(REC_DIR) + os.sep) and os.path.isfile(alt):
            return alt
        return None
    return p


def read_text(rel: str) -> Optional[str]:
    p = path_of(rel)
    if not p:
        return None
    if p.endswith(".gz"):
        try:
            with gzip.open(p, "rt", encoding="utf-8", errors="replace") as f:
                return f.read()
        except (OSError, EOFError):
            return None
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def finalize_orphans():
    """Gravações que ficaram abertas (BastiON reiniciou no meio da sessão): comprime."""
    if not os.path.isdir(REC_DIR):
        return 0
    n = 0
    for root, _, files in os.walk(REC_DIR):
        for name in files:
            if name.endswith(".cast"):
                p = os.path.join(root, name)
                try:
                    with open(p, "rb") as src, gzip.open(p + ".gz", "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.remove(p)
                    n += 1
                except OSError:
                    pass
    return n


def purge(days: int) -> int:
    """Apaga gravações mais antigas que N dias (pastas AAAA/MM e data do arquivo)."""
    if not os.path.isdir(REC_DIR) or days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for root, _, files in os.walk(REC_DIR):
        for name in files:
            p = os.path.join(root, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    n += 1
            except OSError:
                pass
    for root, dirs, _ in sorted(os.walk(REC_DIR), reverse=True):
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass
    return n


def disk_usage() -> dict:
    total = files = 0
    for root, _, names in os.walk(REC_DIR) if os.path.isdir(REC_DIR) else []:
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
                files += 1
            except OSError:
                pass
    return {"bytes": total, "files": files}
