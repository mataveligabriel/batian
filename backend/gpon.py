"""GPON de verdade (OLTs ZTE C300/C320 e TITAN C600/C650/C620).

- Inventário das ONUs (serial, modelo, nome, descrição, usuário PPPoE) lido do último backup de configuração da OLT
  — sem comandos a mais. Senha do PPPoE nunca é guardada.
- Estado de todas as ONUs (`show gpon onu state`, um comando) a cada ciclo curto e sinal recebido pela ONU
  (`show pon power onu-rx <PON>`, um por PON) num ciclo mais longo.
- Por PON: ocupação, quantas online/offline, mediana do sinal; alarme de queda em massa (fibra rompida) e de
  sinal degradado (mediana caiu X dB do normal).
"""
import re
import statistics
from typing import Dict, List, Optional, Tuple

DEFAULTS = {"olts": [], "state_interval_min": 15, "power_interval_min": 60, "mass_min": 8, "mass_pct": 50,
            "rx_bad": -27.0, "rx_drop_db": 3.0, "pon_max": 128, "alert": True}

_IF_OLT = re.compile(r"^\s*interface\s+gpon[-_]olt[-_]1/(\d+)/(\d+)\s*$", re.I)
_IF_ONU = re.compile(r"^\s*interface\s+gpon[-_]onu[-_]1/(\d+)/(\d+):(\d+)\s*$", re.I)
_MNG = re.compile(r"^\s*pon-onu-mng\s+gpon[-_]onu[-_]1/(\d+)/(\d+):(\d+)\s*$", re.I)
_ONU_LINE = re.compile(r"^\s*onu\s+(\d+)\s+type\s+(\S+)\s+(?:sn|loid|pw)\s+(\S+)", re.I)
_PPPOE = re.compile(r"pppoe\s+(?:username|user)\s+(\S+)", re.I)
_NAME = re.compile(r"^\s*name\s+(.+?)\s*$", re.I)
_DESC = re.compile(r"^\s*description\s+(.+?)\s*$", re.I)
_POWER = re.compile(r"gpon[-_]onu[-_]1/(\d+)/(\d+):(\d+)\s+(-?\d+(?:\.\d+)?)\s*\(?\s*dbm", re.I)


def parse_inventory(cfg: str) -> Dict[Tuple[int, int, int], dict]:
    """Running-config da OLT -> {(slot, pon, onu): {sn, type, name, description, pppoe:[...]}}."""
    inv: Dict[Tuple[int, int, int], dict] = {}
    ctx: Optional[tuple] = None
    for ln in (cfg or "").splitlines():
        m = _IF_OLT.match(ln)
        if m:
            ctx = ("olt", int(m.group(1)), int(m.group(2)))
            continue
        m = _IF_ONU.match(ln) or _MNG.match(ln)
        if m:
            ctx = ("onu", int(m.group(1)), int(m.group(2)), int(m.group(3)))
            continue
        if ln.strip() == "!" or (ln[:1].strip() and not ln.strip().startswith(("onu ", "name ", "description "))):
            ctx = None              # fim do bloco ("!") ou outra seção começando na coluna 0
            continue
        if not ctx:
            continue
        if ctx[0] == "olt":
            m = _ONU_LINE.match(ln)
            if m:
                k = (ctx[1], ctx[2], int(m.group(1)))
                d = inv.setdefault(k, {"pppoe": []})
                d["type"], d["sn"] = m.group(2), m.group(3).upper() if re.fullmatch(r"[A-Za-z]{4}[0-9A-Fa-f]{8}", m.group(3)) else m.group(3)
        else:
            k = (ctx[1], ctx[2], ctx[3])
            d = inv.setdefault(k, {"pppoe": []})
            m = _NAME.match(ln)
            if m and "name" not in d:
                d["name"] = m.group(1)[:80]
            m = _DESC.match(ln)
            if m and "description" not in d:
                d["description"] = m.group(1)[:120]
            for u in _PPPOE.findall(ln):
                if u not in d["pppoe"]:
                    d["pppoe"].append(u[:80])
    return inv


def parse_power(text: str) -> Dict[Tuple[int, int, int], float]:
    out = {}
    for m in _POWER.finditer(text or ""):
        v = float(m.group(4))
        if -50 < v < 10:
            out[(int(m.group(1)), int(m.group(2)), int(m.group(3)))] = v
    return out


def pon_summary(onus: List[dict], rx_bad: float, pon_max: int) -> dict:
    total = len(onus)
    off = [o for o in onus if o.get("off")]
    los = [o for o in off if (o.get("phase") or "").lower() in ("los", "losi", "lof", "lofi")]
    rx = [o["rx"] for o in onus if o.get("rx") is not None and not o.get("off")]
    return {"total": total, "online": total - len(off), "offline": len(off), "los": len(los),
            "dying_gasp": sum(1 for o in off if "dying" in (o.get("phase") or "").lower()),
            "rx_median": round(statistics.median(rx), 2) if rx else None, "rx_min": min(rx) if rx else None,
            "rx_bad": sum(1 for v in rx if v < rx_bad), "rx_n": len(rx),
            "occupancy_pct": round(100.0 * total / pon_max, 1) if pon_max else None}


def mass_event(prev_off: int, now_off: int, total: int, newly_off: int, cfg: dict) -> bool:
    """Queda em massa: N ONUs caíram de uma vez ou mais de X% da PON ficou fora."""
    if total <= 0:
        return False
    return newly_off >= cfg["mass_min"] or (now_off - prev_off >= 3 and 100.0 * now_off / total >= cfg["mass_pct"])


def recovered(now_off: int, total: int, newly_off_at_event: int, cfg: dict) -> bool:
    return total > 0 and (now_off < max(3, newly_off_at_event * 0.2)) and 100.0 * now_off / total < cfg["mass_pct"] / 2


def search_terms(q: str) -> str:
    return re.sub(r"[\s:-]", "", q or "").upper()
