"""SLA e disponibilidade por enlace, POP ou cliente.

Um item de SLA junta componentes — sondas (de uma origem), equipamentos e interfaces monitoradas — e o item está
indisponível sempre que QUALQUER componente estiver fora (série). As quedas vêm de:

- equipamento: incidentes "Equipamento X sem resposta" do BastiON (abertura → resolução);
- interface: eventos de queda/volta do monitor de interfaces (if_events);
- sonda: resumo por hora (ciclos que falharam / ciclos medidos da hora).

Janelas de manutenção que cobrem o componente não contam como indisponibilidade (aparecem à parte). Horas sem
medição (BastiON parado, sonda desligada) ficam fora do tempo medido e aparecem como cobertura.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

KINDS = {"link": "Enlace", "pop": "POP", "client": "Cliente", "service": "Serviço", "other": "Outro"}
BR = timezone(timedelta(hours=-3))

Interval = Tuple[float, float]


def month_range(month: str) -> Tuple[datetime, datetime]:
    """'2026-10' -> início e fim do mês no horário de Brasília (em UTC)."""
    y, m = (int(x) for x in month.split("-"))
    start = datetime(y, m, 1, tzinfo=BR)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=BR)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def ts(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).timestamp()
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def union(iv: List[Interval]) -> List[Interval]:
    out: List[list] = []
    for a, b in sorted(x for x in iv if x[1] > x[0]):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def clip(iv: List[Interval], a: float, b: float) -> List[Interval]:
    return [(max(x, a), min(y, b)) for x, y in iv if y > a and x < b]


def subtract(iv: List[Interval], cut: List[Interval]) -> List[Interval]:
    cut = union(cut)
    out = []
    for a, b in iv:
        cur = a
        for c, d in cut:
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append((cur, c))
            cur = max(cur, d)
            if cur >= b:
                break
        if cur < b:
            out.append((cur, b))
    return out


def total(iv: List[Interval]) -> float:
    return sum(b - a for a, b in iv)


def device_outages(incidents: list, now: float) -> List[Interval]:
    return union([(ts(i["created_at"]), ts(i.get("resolved_at")) or now) for i in incidents if ts(i.get("created_at"))])


def iface_outages(events: list, start: float, end: float, now: float) -> List[Interval]:
    """events ordenados por data (inclui o último antes do período, para saber o estado inicial)."""
    out, down_at = [], None
    for e in events:
        t = ts(e["at"])
        if t is None:
            continue
        if e["status"] == "down" and down_at is None:
            down_at = t
        elif e["status"] == "up" and down_at is not None:
            out.append((down_at, t))
            down_at = None
    if down_at is not None:
        out.append((down_at, min(end, now)))
    return clip(union(out), start, end)


def probe_outages(hours: list) -> Tuple[List[Interval], List[Interval]]:
    """-> (indisponível, medido). Cada hora vira [hora, hora + fração que falhou]."""
    down, measured = [], []
    for h in hours:
        t = ts(h["hour"])
        n = h.get("n") or 0
        if t is None or not n:
            continue
        measured.append((t, t + 3600))
        f = (h.get("fail_n") or 0) / n
        if f > 0:
            down.append((t, t + 3600 * f))
    return union(down), union(measured)


def outage_list(iv: List[Interval], min_s: float = 60) -> list:
    return [{"start": datetime.fromtimestamp(a, timezone.utc).isoformat(), "end": datetime.fromtimestamp(b, timezone.utc).isoformat(),
             "seconds": int(b - a)} for a, b in iv if b - a >= min_s]


def summarize(comps: list, start: float, end: float, now: float, target: float) -> dict:
    """comps: [{label, down:[iv], maint:[iv], measured:[iv] | None}] -> números do item."""
    period_end = min(end, now)
    span = max(1.0, period_end - start)
    downs, maints, measured_all = [], [], []
    rows = []
    for c in comps:
        d = clip(c["down"], start, period_end)
        m = clip(union(c.get("maint") or []), start, period_end)
        meas = clip(c["measured"], start, period_end) if c.get("measured") is not None else [(start, period_end)]
        d_eff = subtract(d, m)
        m_eff = clip_to(d, m)
        mt = max(1.0, total(meas))
        rows.append({"label": c["label"], "type": c["type"], "downtime_s": int(total(d_eff)), "maint_s": int(total(m_eff)),
                     "availability": round(100.0 * (1 - total(d_eff) / mt), 4), "coverage": round(100.0 * total(meas) / span, 1),
                     "outages": len([x for x in d_eff if x[1] - x[0] >= 60])})
        downs += d_eff
        maints += m_eff
        measured_all.append(meas)
    item_down = union(downs)
    # tempo medido do item = interseção das coberturas (todo componente medido)
    meas = [(start, period_end)]
    for mm in measured_all:
        meas = intersect(meas, mm)
    mt = max(1.0, total(meas))
    dt = total(item_down)
    av = round(100.0 * (1 - dt / mt), 4) if comps else None
    real = [x for x in item_down if x[1] - x[0] >= 60]
    allowed = (1 - target / 100.0) * (end - start) if target else None
    return {"availability": av, "downtime_s": int(dt), "maint_s": int(total(union(maints))), "outages": len(real),
            "mttr_s": int(sum(b - a for a, b in real) / len(real)) if real else 0,
            "longest_s": int(max((b - a for a, b in real), default=0)), "coverage": round(100.0 * total(meas) / span, 1),
            "target": target, "met": (av is None or av >= target) if target else None,
            "allowed_s": int(allowed) if allowed is not None else None,
            "partial": now < end, "components": rows, "outage_list": outage_list(item_down)[:200]}


def clip_to(down: List[Interval], maint: List[Interval]) -> List[Interval]:
    """Parte das quedas que caiu dentro de manutenção."""
    return intersect(union(down), union(maint))


def intersect(a: List[Interval], b: List[Interval]) -> List[Interval]:
    out, i, j = [], 0, 0
    a, b = union(a), union(b)
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if lo < hi:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def fmt_dur(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    return ((f"{d}d " if d else "") + (f"{h}h " if h or d else "") + (f"{m}min" if m or not (h or d) else "")).strip()
