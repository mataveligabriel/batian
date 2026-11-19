"""Saúde do servidor do BastiON: CPU, memória, disco, rede, processo e atraso do loop, lidos do /proc (sem dependências).

Uma tarefa amostra a cada STEP segundos e guarda a última hora em memória (some ao reiniciar o backend).
As rotinas de fundo registram quanto tempo levou cada rodada com `job(nome)` para mostrar o que está pesando.
"""
import asyncio
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Dict, Optional

STEP = 10
KEEP = 360                     # 1 hora com STEP=10s
HIST: deque = deque(maxlen=KEEP)
JOBS: Dict[str, dict] = {}
_prev: dict = {}


def _read(path: str) -> str:
    try:
        with open(path, "r") as f:
            return f.read()
    except OSError:
        return ""


def cpu_times() -> Optional[tuple]:
    line = _read("/proc/stat").split("\n", 1)[0].split()
    if not line or line[0] != "cpu":
        return None
    v = [int(x) for x in line[1:]]
    idle = v[3] + (v[4] if len(v) > 4 else 0)              # idle + iowait
    iowait = v[4] if len(v) > 4 else 0
    steal = v[7] if len(v) > 7 else 0
    return sum(v[:8]), idle, iowait, steal


def cpu_pct(prev, cur) -> Optional[dict]:
    if not prev or not cur:
        return None
    dt = cur[0] - prev[0]
    if dt <= 0:
        return None
    return {"used": round(100 * (1 - (cur[1] - prev[1]) / dt), 1), "iowait": round(100 * (cur[2] - prev[2]) / dt, 1),
            "steal": round(100 * (cur[3] - prev[3]) / dt, 1)}


def meminfo() -> dict:
    m = {}
    for ln in _read("/proc/meminfo").splitlines():
        k, _, rest = ln.partition(":")
        try:
            m[k] = int(rest.split()[0]) * 1024
        except (ValueError, IndexError):
            pass
    total, avail = m.get("MemTotal", 0), m.get("MemAvailable", m.get("MemFree", 0))
    st, sf = m.get("SwapTotal", 0), m.get("SwapFree", 0)
    out = {"total": total, "used": max(0, total - avail), "available": avail, "swap_total": st, "swap_used": max(0, st - sf)}
    # limite do contêiner (cgroup v2 / v1), quando houver
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        v = _read(p).strip()
        if v.isdigit() and 0 < int(v) < total:
            out["limit"] = int(v)
            cur = _read("/sys/fs/cgroup/memory.current" if p.endswith("memory.max") else "/sys/fs/cgroup/memory/memory.usage_in_bytes").strip()
            if cur.isdigit():
                out["limit_used"] = int(cur)
            break
    return out


def vmstat_swap() -> tuple:
    """Páginas trocadas com o swap desde o boot (entrando, saindo)."""
    v = {}
    for ln in _read("/proc/vmstat").splitlines():
        k, _, n = ln.partition(" ")
        if k in ("pswpin", "pswpout"):
            v[k] = int(n)
    return v.get("pswpin", 0), v.get("pswpout", 0)


def loadavg() -> list:
    p = _read("/proc/loadavg").split()
    try:
        return [float(x) for x in p[:3]]
    except ValueError:
        return []


def disks(paths=("/", "/data", "/backups")) -> list:
    out, seen = [], set()
    for p in paths:
        try:
            st = os.statvfs(p)
        except OSError:
            continue
        key = (st.f_blocks, st.f_bfree)
        if key in seen:
            continue
        seen.add(key)
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        out.append({"path": p, "total": total, "used": total - free, "free": free})
    return out


def net_bytes() -> tuple:
    rx = tx = 0
    for ln in _read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = ln.partition(":")
        if name.strip() == "lo":
            continue
        f = rest.split()
        if len(f) >= 9:
            rx += int(f[0])
            tx += int(f[8])
    return rx, tx


def proc_self() -> dict:
    out = {}
    for ln in _read("/proc/self/status").splitlines():
        k, _, v = ln.partition(":")
        if k in ("VmRSS", "Threads"):
            out[k] = int(v.split()[0]) * (1024 if k == "VmRSS" else 1)
    try:
        out["fds"] = len(os.listdir("/proc/self/fd"))
    except OSError:
        pass
    st = _read("/proc/self/stat").rsplit(")", 1)[-1].split()
    try:
        out["cpu_ticks"] = int(st[11]) + int(st[12])
    except (IndexError, ValueError):
        pass
    return {"rss": out.get("VmRSS"), "threads": out.get("Threads"), "fds": out.get("fds"), "cpu_ticks": out.get("cpu_ticks")}


def uptime_s() -> Optional[float]:
    try:
        return float(_read("/proc/uptime").split()[0])
    except (ValueError, IndexError):
        return None


def sample(lag_ms: Optional[float] = None) -> dict:
    """Uma leitura; taxas (CPU, rede, CPU do processo) contra a leitura anterior."""
    now = time.time()
    ct, (rx, tx), me, (si, so) = cpu_times(), net_bytes(), proc_self(), vmstat_swap()
    s = {"t": round(now), "cpu": None, "load": loadavg(), "mem": meminfo(), "lag_ms": lag_ms, "rx_bps": None, "tx_bps": None,
         "proc": {k: me.get(k) for k in ("rss", "threads", "fds")}, "proc_cpu": None, "swap_io": None}
    if _prev:
        dt = now - _prev["t"]
        s["cpu"] = cpu_pct(_prev.get("ct"), ct)
        if dt > 0:
            s["rx_bps"] = round(max(0, rx - _prev["rx"]) * 8 / dt)
            s["tx_bps"] = round(max(0, tx - _prev["tx"]) * 8 / dt)
            if "si" in _prev:                     # páginas/s indo e voltando do swap: isso é o que deixa lento
                s["swap_io"] = round((max(0, si - _prev["si"]) + max(0, so - _prev["so"])) / dt, 1)
            if me.get("cpu_ticks") is not None and _prev.get("pt") is not None:
                hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
                s["proc_cpu"] = round(100 * (me["cpu_ticks"] - _prev["pt"]) / hz / dt, 1)    # % de 1 núcleo
    _prev.update(t=now, ct=ct, rx=rx, tx=tx, pt=me.get("cpu_ticks"), si=si, so=so)
    return s


async def sampler():
    """Roda para sempre: mede o atraso do loop (quanto o sleep de STEP passou do tempo) e guarda a amostra."""
    sample()
    while True:
        t0 = time.monotonic()
        await asyncio.sleep(STEP)
        lag = max(0.0, (time.monotonic() - t0 - STEP) * 1000)
        try:
            HIST.append(sample(round(lag, 1)))
        except Exception:
            pass


@asynccontextmanager
async def job(name: str, items: Optional[int] = None):
    """Mede uma rodada de rotina de fundo: `async with syshealth.job("Ping dos equipamentos", len(devs)):`."""
    t0 = time.monotonic()
    ok = True
    try:
        yield
    except Exception:
        ok = False
        raise
    finally:
        dur = time.monotonic() - t0
        j = JOBS.setdefault(name, {"name": name, "runs": 0, "max_s": 0.0})
        j.update(last_s=round(dur, 2), at=round(time.time()), ok=ok, runs=j["runs"] + 1, max_s=round(max(j["max_s"], dur), 2))
        if items is not None:
            j["items"] = items
