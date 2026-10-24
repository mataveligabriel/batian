"""Plantão do NOC: escala em rodízio, trocas pontuais e escalonamento dos incidentes.

Escala: lista de pessoas em ordem; cada uma fica de plantão um dia ou uma semana, trocando no dia/hora definidos
(hora de Brasília por padrão). Trocas pontuais ("fulano cobre de sexta 18h a segunda 8h") têm prioridade.

Escalonamento: para incidentes abertos (não reconhecidos) a partir de uma severidade, cada nível avisa alguém
depois de X minutos — nível 1 normalmente é quem está de plantão (na hora), depois o próximo da escala, um usuário
específico ou todos. Reconhecer o incidente para a escalada. Quem é avisado passa a ver o incidente.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

PERIODS = {"day": 1, "week": 7}
TARGETS = ("oncall", "next", "all")          # ou "user:<id>"
WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

DEFAULTS = {
    "enabled": False,
    "members": [],                 # ids dos usuários, na ordem do rodízio
    "period": "week",              # day | week
    "handoff_weekday": 0,          # 0 = segunda (só no semanal)
    "handoff_hour": 8,             # hora local da troca
    "anchor": "2026-01-05",        # uma data de troca em que o 1º da lista começou (segunda)
    "utc_offset": -3,              # fuso da escala (Brasil sem horário de verão)
    "overrides": [],               # [{id, user_id, start, end, note}] em ISO local (AAAA-MM-DDTHH:MM)
    "min_severity": "high",
    "levels": [{"after_min": 0, "target": "oncall"}, {"after_min": 15, "target": "next"}, {"after_min": 30, "target": "all"}],
}


class OncallError(ValueError):
    pass


def tz(cfg: dict) -> timezone:
    return timezone(timedelta(hours=int(cfg.get("utc_offset", -3))))


def _local(dt: datetime, cfg: dict) -> datetime:
    return dt.astimezone(tz(cfg))


def _parse_local(s: str, cfg: dict) -> datetime:
    try:
        d = datetime.fromisoformat(str(s).strip()[:16])
    except ValueError:
        raise OncallError(f"Data inválida: {s}")
    return d.replace(tzinfo=tz(cfg)) if d.tzinfo is None else d


def _first_handoff(cfg: dict) -> datetime:
    """Primeira troca da escala (o 1º da lista começa aqui)."""
    try:
        a = datetime.fromisoformat(str(cfg.get("anchor") or DEFAULTS["anchor"])[:10])
    except ValueError:
        a = datetime.fromisoformat(DEFAULTS["anchor"])
    a = a.replace(hour=int(cfg.get("handoff_hour", 8)), minute=0, second=0, microsecond=0, tzinfo=tz(cfg))
    if cfg.get("period") == "week":
        a -= timedelta(days=(a.weekday() - int(cfg.get("handoff_weekday", 0))) % 7)
    return a


def _slot(cfg: dict, t: datetime):
    """(índice do turno, início, fim) do rodízio no instante t."""
    days = PERIODS.get(cfg.get("period"), 7)
    first = _first_handoff(cfg)
    step = timedelta(days=days)
    n = (t - first) // step
    start = first + n * step
    return int(n), start, start + step


def rotation_user(cfg: dict, t: datetime, offset: int = 0) -> Optional[str]:
    m = cfg.get("members") or []
    if not m:
        return None
    n, _, _ = _slot(cfg, t)
    return m[(n + offset) % len(m)]


def override_at(cfg: dict, t: datetime) -> Optional[dict]:
    for o in cfg.get("overrides") or []:
        try:
            if _parse_local(o["start"], cfg) <= t < _parse_local(o["end"], cfg):
                return o
        except (OncallError, KeyError):
            continue
    return None


def who(cfg: dict, t: Optional[datetime] = None) -> Optional[str]:
    t = t or datetime.now(timezone.utc)
    o = override_at(cfg, t)
    return o["user_id"] if o else rotation_user(cfg, t)


def next_after(cfg: dict, t: Optional[datetime] = None) -> Optional[str]:
    """Próximo da escala depois de quem está agora (para o nível "próximo")."""
    m = cfg.get("members") or []
    cur = who(cfg, t)
    if not m:
        return None
    if cur in m:
        return m[(m.index(cur) + 1) % len(m)] if len(m) > 1 else None
    return rotation_user(cfg, t or datetime.now(timezone.utc))


def shifts(cfg: dict, start: Optional[datetime] = None, count: int = 8) -> List[dict]:
    """Próximos turnos do rodízio (com as trocas pontuais que caem neles)."""
    t = start or datetime.now(timezone.utc)
    out = []
    for _ in range(max(1, min(60, count))):
        n, s, e = _slot(cfg, t)
        m = cfg.get("members") or []
        ov = [o for o in cfg.get("overrides") or [] if _safe_overlap(o, s, e, cfg)]
        out.append({"start": _local(s, cfg).isoformat(), "end": _local(e, cfg).isoformat(),
                    "user_id": m[n % len(m)] if m else None, "overrides": ov})
        t = e
    return out


def _safe_overlap(o: dict, s: datetime, e: datetime, cfg: dict) -> bool:
    try:
        return _parse_local(o["start"], cfg) < e and _parse_local(o["end"], cfg) > s
    except (OncallError, KeyError):
        return False


def clean(data: dict, valid_users: set, severities: List[str]) -> dict:
    c = {**DEFAULTS}
    c["enabled"] = bool(data.get("enabled"))
    members = [m for m in (data.get("members") or []) if m in valid_users]
    c["members"] = list(dict.fromkeys(members))[:50]
    if c["enabled"] and not c["members"]:
        raise OncallError("Coloque ao menos uma pessoa na escala")
    c["period"] = data.get("period") if data.get("period") in PERIODS else "week"
    c["handoff_weekday"] = min(6, max(0, int(data.get("handoff_weekday", 0) or 0)))
    c["handoff_hour"] = min(23, max(0, int(data.get("handoff_hour", 8) or 0)))
    try:
        c["anchor"] = datetime.fromisoformat(str(data.get("anchor") or DEFAULTS["anchor"])[:10]).date().isoformat()
    except ValueError:
        raise OncallError("Data de início da escala inválida")
    c["utc_offset"] = min(14, max(-12, int(data.get("utc_offset", -3))))
    c["min_severity"] = data.get("min_severity") if data.get("min_severity") in severities else "high"
    levels = []
    for i, lv in enumerate(data.get("levels") or [], 1):
        tgt = str(lv.get("target") or "")
        if tgt not in TARGETS and not (tgt.startswith("user:") and tgt[5:] in valid_users):
            raise OncallError(f"Nível {i}: escolha quem é avisado")
        levels.append({"after_min": min(1440, max(0, int(lv.get("after_min", 0) or 0))), "target": tgt})
    c["levels"] = sorted(levels, key=lambda x: x["after_min"])[:6]
    ovs = []
    for o in data.get("overrides") or []:
        if o.get("user_id") not in valid_users:
            raise OncallError("Troca de plantão: escolha quem cobre")
        s, e = _parse_local(o.get("start"), c), _parse_local(o.get("end"), c)
        if e <= s:
            raise OncallError("Troca de plantão: o fim precisa ser depois do início")
        if e < datetime.now(timezone.utc) - timedelta(days=30):
            continue                                       # trocas muito antigas saem sozinhas
        ovs.append({"id": str(o.get("id") or ""), "user_id": o["user_id"], "start": _local(s, c).strftime("%Y-%m-%dT%H:%M"),
                    "end": _local(e, c).strftime("%Y-%m-%dT%H:%M"), "note": str(o.get("note") or "")[:120]})
    c["overrides"] = sorted(ovs, key=lambda o: o["start"])[:100]
    return c


def targets_for(cfg: dict, level: dict, t: Optional[datetime] = None) -> List[str]:
    tgt = level["target"]
    if tgt == "oncall":
        u = who(cfg, t)
        return [u] if u else []
    if tgt == "next":
        u = next_after(cfg, t)
        return [u] if u else []
    if tgt == "all":
        return list(cfg.get("members") or [])
    if tgt.startswith("user:"):
        return [tgt[5:]]
    return []


def due_levels(cfg: dict, inc: dict, now: Optional[datetime] = None) -> List[int]:
    """Níveis de escalonamento que já venceram e ainda não foram avisados para este incidente."""
    now = now or datetime.now(timezone.utc)
    try:
        age = (now - datetime.fromisoformat(inc["created_at"])).total_seconds() / 60
    except Exception:
        return []
    done = set(inc.get("escalated") or [])
    return [i for i, lv in enumerate(cfg.get("levels") or []) if i not in done and age >= lv["after_min"]]
