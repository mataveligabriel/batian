"""Potência óptica (RX/TX por lane) lida pela CLI via SSH.

Cada fabricante mostra DOM de um jeito e as MIBs proprietárias variam por modelo, então usamos a CLI
(mesmo caminho do terminal, inclusive atrás de agente). Os comandos por tipo de equipamento são
editáveis na tela; o parser entende os formatos mais comuns:
  - Huawei:  "Current RX Power(dBm) : -2.35|-2.41|-2.10|-2.62"  (lanes na mesma linha)
  - Juniper: blocos "Lane N" com "Laser receiver power : 0.9 mW / -0.46 dBm"
  - Cisco IOS/XE: tabela com colunas "Tx Power (dBm)  Rx Power (dBm)"; IOS-XR: tabela de lanes "(x dBm)"
  - Mikrotik e genérico: "sfp-rx-power: -5.1dBm", "Rx Power: -3.4 dBm"
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("bastion.optics")

DEFAULT_COMMANDS: Dict[str, List[str]] = {
    "huawei": ["display interface {ifname} transceiver verbose", "display transceiver interface {ifname} verbose"],
    "juniper": ["show interfaces diagnostics optics {ifname}"],
    "cisco": ["show interfaces {ifname} transceiver detail", "show interfaces {ifname} transceiver", "show controllers {ifname} phy"],
    "datacom": ["show interface transceiver {ifname} detail", "show interface {ifname} transceiver"],
    "zte": ["show transceiver information {ifname}", "show optical-module-info {ifname}"],
    "mikrotik": ["/interface ethernet monitor {ifname} once"],
    "linux": [], "ubiquiti": [], "other": [],
}
DEFAULTS = {"optics_enabled": True, "optics_interval_sec": 300, "optics_commands": {}}

NUM = r"[-+]?\d+(?:\.\d+)?"
NO_LIGHT = -40.0
_SKIP = re.compile(r"(?i)thresh|alarm|warn|high|low|range|default|limit|min\b|max\b")
_DBM_VAL = re.compile(rf"({NUM}|-\s*inf)\s*dBm", re.I)


def _to_dbm(s: str) -> Optional[float]:
    s = s.strip().lower().replace(" ", "")
    if s in ("-inf", "inf", "-infinity"):
        return NO_LIGHT
    try:
        v = float(s)
    except ValueError:
        return None
    if v <= -40:
        return NO_LIGHT
    return v if -60 <= v <= 15 else None


def _clean(t: str) -> str:
    t = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", t)
    return t.replace("\r", "")


def _values_after_colon(line: str) -> List[float]:
    """'Current RX Power(dBm) :-2.35|-2.41|-2.10|-2.62'  ou  'Rx Power: -3.42dBm, Warning range: [...]'"""
    if ":" not in line:
        return []
    rest = line.split(":", 1)[1]
    rest = re.split(r"(?i)\[|warning|range|,\s*[a-z]", rest)[0]
    out = []
    for tok in re.split(r"[|,;/]|\s{2,}", rest):
        tok = tok.strip().lower().replace("dbm", "").strip()
        if not tok:
            continue
        v = _to_dbm(tok)
        if v is not None:
            out.append(v)
    return out


def _is_rx(line: str) -> bool:
    l = line.lower()
    return bool(re.search(r"\brx\b|rx[\s_-]*power|rx[\s_-]*optical|receiv", l)) and "power" in l


def _is_tx(line: str) -> bool:
    l = line.lower()
    return bool(re.search(r"\btx\b|tx[\s_-]*power|tx[\s_-]*optical|output power|transmit", l)) and "power" in l and not _is_rx(line)


def _parse_lane_blocks(text: str) -> List[dict]:
    """Juniper e afins: blocos 'Lane N' com linhas de rx/tx em dBm."""
    lanes: Dict[int, dict] = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"\s*(?:lane|channel)\s*(\d+)\s*$", line, re.I)
        if m:
            cur = int(m.group(1))
            lanes.setdefault(cur, {"lane": cur, "rx": None, "tx": None})
            continue
        if cur is None or _SKIP.search(line):
            continue
        vals = _DBM_VAL.findall(line)
        if not vals:
            continue
        v = _to_dbm(vals[-1])
        if _is_rx(line) and lanes[cur]["rx"] is None:
            lanes[cur]["rx"] = v
        elif _is_tx(line) and lanes[cur]["tx"] is None:
            lanes[cur]["tx"] = v
    return [lanes[k] for k in sorted(lanes) if lanes[k]["rx"] is not None or lanes[k]["tx"] is not None]


def _parse_table(text: str) -> List[dict]:
    """Cisco: cabeçalho com 'Tx Power' e 'Rx Power'; cada linha de dados = uma lane."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        l = line.lower()
        # cabeçalho pode estar quebrado em 2-3 linhas; procura a linha com 'rx power' ou 'rx' + 'power' próximas
        window = " ".join(x.lower() for x in lines[max(0, i - 2):i + 1])
        if not ("tx" in window and "rx" in window and "power" in window):
            continue
        if not re.search(r"-{3,}", lines[i + 1] if i + 1 < len(lines) else ""):
            continue
        rx_first = window.find("rx") < window.find("tx")
        rows = []
        for data in lines[i + 2:]:
            if not data.strip() or re.match(r"\s*[-=]{3,}", data):
                if rows:
                    break
                continue
            dbms = _DBM_VAL.findall(data)
            if len(dbms) >= 2:  # IOS-XR: "1.26 mW (1.00 dBm)  0.98 mW (-0.09 dBm)"
                a, b = _to_dbm(dbms[0]), _to_dbm(dbms[1])
            else:
                nums = re.findall(rf"(?<![\w/.:]){NUM}(?![\w/.:])", data)
                if len(nums) < 2:
                    if rows:
                        break
                    continue
                a, b = _to_dbm(nums[-2]), _to_dbm(nums[-1])
            tx, rx = (b, a) if rx_first else (a, b)
            rows.append({"lane": len(rows), "rx": rx, "tx": tx})
        if rows:
            return rows
    return []


def _parse_lines(text: str) -> List[dict]:
    """Huawei (lanes separadas por |), Mikrotik e genérico: uma linha de RX e uma de TX."""
    rx: List[float] = []
    tx: List[float] = []
    for line in text.splitlines():
        if _SKIP.search(line.split(":", 1)[0] if ":" in line else line):
            continue
        if not re.search(r"dbm", line, re.I):
            continue
        vals = _values_after_colon(line)
        if not vals:
            m = _DBM_VAL.findall(line)
            vals = [v for v in (_to_dbm(x) for x in m) if v is not None]
        if not vals:
            continue
        if _is_rx(line) and not rx:
            rx = vals
        elif _is_tx(line) and not tx:
            tx = vals
    n = max(len(rx), len(tx))
    return [{"lane": i, "rx": rx[i] if i < len(rx) else None, "tx": tx[i] if i < len(tx) else None} for i in range(n)]


def parse_optics(text: str) -> dict:
    t = _clean(text or "")
    lanes = _parse_lane_blocks(t) or _parse_table(t) or _parse_lines(t)
    for ln in lanes:
        ln["rx"] = None if ln["rx"] is None else round(ln["rx"], 2)
        ln["tx"] = None if ln["tx"] is None else round(ln["tx"], 2)
    rxs = [l["rx"] for l in lanes if l["rx"] is not None]
    return {"lanes": lanes, "rx_min": min(rxs) if rxs else None, "ok": bool(lanes)}


def commands_for(device_type: str, settings: dict) -> List[str]:
    custom = (settings.get("optics_commands") or {}).get(device_type)
    if custom:
        return [c for c in custom if c.strip()]
    return DEFAULT_COMMANDS.get(device_type or "other", [])


async def read_optics(session, device_type: str, ifname: str, settings: dict) -> Tuple[dict, str, str]:
    """Tenta os comandos do tipo em ordem, na MESMA sessão de shell, até um trazer lanes.
    -> (resultado, comando usado, saída bruta de todos os comandos tentados)"""
    outs, last_cmd = [], ""
    cmds = commands_for(device_type, settings)
    if not cmds:
        return {"lanes": [], "rx_min": None, "ok": False}, "", "Sem comando de óptica para este tipo de equipamento."
    for tpl in cmds:
        cmd = tpl.replace("{ifname}", ifname)
        try:
            out = await session.run(cmd, timeout=45, idle=2.5)
        except Exception as e:
            raise RuntimeError(f"{e} (ao rodar '{cmd}')") from e
        outs.append(f"### {cmd}\n{out.strip()}")
        parsed = parse_optics(out)
        last_cmd = cmd
        if parsed["ok"]:
            return parsed, cmd, out
    return {"lanes": [], "rx_min": None, "ok": False}, last_cmd, "\n\n".join(outs)


async def get_settings(db) -> dict:
    doc = await db.config.find_one({"key": "optics"}, {"_id": 0}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if k != "key"}}


class OpticsCollector:
    def __init__(self, db, connect_device: Callable[[dict], Awaitable]):
        self.db = db
        self.connect_device = connect_device
        self.live: Dict[Tuple[str, int], dict] = {}
        self.errors: Dict[Tuple[str, int], str] = {}
        self.last_tick: Optional[datetime] = None
        self.busy = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not self._task:
            self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        await asyncio.sleep(20)
        while True:
            s = await get_settings(self.db)
            started = time.monotonic()
            if s["optics_enabled"]:
                try:
                    await self.tick(s)
                except Exception as e:
                    logger.warning(f"optics tick falhou: {e}")
            await asyncio.sleep(max(30.0, max(60, int(s["optics_interval_sec"])) - (time.monotonic() - started)))

    async def targets(self) -> Dict[str, Dict[int, str]]:
        """device_id -> {if_index: if_name} das interfaces dos links dos mapas e dos widgets ópticos."""
        want: Dict[str, Dict[int, str]] = {}
        async for m in self.db.maps.find({}, {"_id": 0, "nodes": 1, "links": 1}):
            node_dev = {n["id"]: n.get("device_id") for n in m.get("nodes", [])}
            for ln in m.get("links", []):
                for side, node_key in (("from_if", "from"), ("to_if", "to")):
                    iface, dev = ln.get(side), node_dev.get(ln.get(node_key))
                    if iface and dev and iface.get("name"):
                        want.setdefault(dev, {})[int(iface["index"])] = iface["name"]
        async for d in self.db.dashboards.find({}, {"_id": 0, "widgets": 1}):
            for w in d.get("widgets", []):
                if w.get("type") == "optics" and w.get("device_id") and w.get("if_name"):
                    want.setdefault(w["device_id"], {})[int(w["if_index"])] = w["if_name"]
        return want

    async def read_one(self, dev: dict, if_index: int, if_name: str, s: dict, store: bool = True) -> dict:
        client = await self.connect_device(dev)
        try:
            sess = await client.shell()
            try:
                parsed, cmd, raw = await read_optics(sess, dev.get("device_type") or "other", if_name, s)
            finally:
                await sess.close()
        finally:
            await client.close()
        if store:
            self._store(dev["id"], if_index, parsed, cmd, raw)
        return {"parsed": parsed, "command": cmd, "raw": raw}

    def _store(self, dev_id, idx, parsed, cmd, raw):
        key = (dev_id, idx)
        if parsed["ok"]:
            self.errors.pop(key, None)
            self.live[key] = {"lanes": parsed["lanes"], "rx_min": parsed["rx_min"], "ts": time.time(), "command": cmd}
        else:
            self.errors[key] = f"não entendi a saída de '{cmd}'" if cmd else raw

    async def tick(self, s: Optional[dict] = None):
        if self.busy:
            return
        self.busy = True
        try:
            s = s or await get_settings(self.db)
            want = await self.targets()
            if not want:
                return
            devs = {d["id"]: d async for d in self.db.devices.find({"id": {"$in": list(want)}}, {"_id": 0})}
            sem = asyncio.Semaphore(4)
            samples = []

            async def one(dev_id, ifaces):
                dev = devs.get(dev_id)
                if not dev or not commands_for(dev.get("device_type"), s):
                    return
                async with sem:
                    try:
                        client = await self.connect_device(dev)
                        sess = await client.shell()
                    except Exception as e:
                        for idx in ifaces:
                            self.errors[(dev_id, idx)] = f"SSH: {e}"
                        return
                    try:
                        for idx, name in ifaces.items():
                            try:
                                parsed, cmd, raw = await read_optics(sess, dev.get("device_type") or "other", name, s)
                            except Exception as e:
                                self.errors[(dev_id, idx)] = str(e)
                                break  # sessão caiu: não adianta tentar as outras interfaces agora
                            self._store(dev_id, idx, parsed, cmd, raw)
                            if parsed["ok"]:
                                samples.append({"device_id": dev_id, "if_index": idx, "ts": datetime.now(timezone.utc),
                                                "lanes": [{"rx": l["rx"], "tx": l["tx"]} for l in parsed["lanes"]]})
                    finally:
                        try:
                            await sess.close()
                            await client.close()
                        except Exception:
                            pass

            await asyncio.gather(*[one(d, i) for d, i in want.items()])
            if samples:
                await self.db.optics_samples.insert_many(samples)
            self.last_tick = datetime.now(timezone.utc)
        finally:
            self.busy = False

    def get_live(self, dev_id: str, idx: Optional[int]) -> dict:
        if idx is None:
            return {}
        key = (dev_id, int(idx))
        out = dict(self.live.get(key) or {})
        if key in self.errors:
            out["error"] = self.errors[key]
        return out
