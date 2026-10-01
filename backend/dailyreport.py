"""Resumo diário do BastiON (Telegram / push): o que aconteceu nas últimas 24 h e o que pede atenção agora."""
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

OPTIC_DROP_DB = 2.0        # queda média do RX (24 h vs. 7 dias anteriores) que vale avisar
OPTIC_LOW_DBM = -25.0      # RX abaixo disso aparece mesmo sem queda
NO_LIGHT = -39.0


def _gbps(bps: Optional[float]) -> str:
    if bps is None:
        return "—"
    for div, unit in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if bps >= div:
            return f"{bps / div:.1f}{unit}"
    return f"{bps:.0f}"


def _ts(d) -> datetime:
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def build(db, optics_names: Optional[Callable[[], Awaitable[Dict[str, Dict[int, str]]]]] = None,
                now: Optional[datetime] = None) -> Tuple[str, str, dict]:
    """Devolve (título, texto, dados)."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    since_iso = since.isoformat()
    data: dict = {}
    lines: List[str] = []
    attention = 0

    devs = {d["id"]: d async for d in db.devices.find({}, {"_id": 0, "id": 1, "name": 1, "status": 1})}
    dname = lambda i: (devs.get(i) or {}).get("name") or i  # noqa: E731

    # ---- equipamentos e agentes ----
    off = sorted(d["name"] for d in devs.values() if d.get("status") == "offline")
    agents = await db.agents.find({}, {"_id": 0, "name": 1, "status": 1}).to_list(1000)
    ag_off = sorted(a["name"] for a in agents if a.get("status") == "offline")
    data.update(devices=len(devs), devices_offline=off, agents=len(agents), agents_offline=ag_off)
    s = f"🖥️ Equipamentos: {len(devs)}" + (f" — {len(off)} OFFLINE: {', '.join(off[:8])}{'…' if len(off) > 8 else ''}" if off else " (todos online)")
    if agents:
        s += f"\n🔌 Agentes: {len(agents)}" + (f" — {len(ag_off)} offline: {', '.join(ag_off[:5])}" if ag_off else " (todos online)")
    lines.append(s)
    attention += len(off) + len(ag_off)

    # ---- interfaces monitoradas ----
    down_now = await db.if_monitors.find({"state": "down"}, {"_id": 0}).to_list(500)
    events = await db.if_events.find({"at": {"$gte": since_iso}}, {"_id": 0}).to_list(5000)
    downs = [e for e in events if e.get("status") == "down"]
    flap: Dict[Tuple[str, str], int] = {}
    for e in downs:
        k = (e.get("device_name") or dname(e.get("device_id")), e.get("if_name") or "?")
        flap[k] = flap.get(k, 0) + 1
    data.update(if_down_now=len(down_now), if_drops_24h=len(downs))
    if down_now or downs:
        s = "🔗 Interfaces monitoradas:"
        if down_now:
            names = [f"{dname(m['device_id'])} · {m.get('if_name') or m.get('if_index')}"
                     + (f" ({m['alias']})" if m.get("alias") else "") for m in down_now[:6]]
            s += f"\n  {len(down_now)} DOWN agora: " + "; ".join(names) + ("…" if len(down_now) > 6 else "")
        if downs:
            top = sorted(flap.items(), key=lambda x: -x[1])[:4]
            s += f"\n  {len(downs)} queda(s) em 24 h" + (": " + ", ".join(f"{d} · {i} ({n}x)" for (d, i), n in top) if top else "")
        lines.append(s)
        attention += len(down_now)

    # ---- sinal óptico: queda vs. semana anterior ----
    names = {}
    if optics_names:
        try:
            names = await optics_names()
        except Exception:
            names = {}
    week = now - timedelta(days=8)
    acc: Dict[Tuple[str, int, int], List[float]] = {}     # (dev, if, lane) -> [soma24, n24, somaAnt, nAnt, último]
    async for smp in db.optics_samples.find({"ts": {"$gte": week}}, {"_id": 0, "device_id": 1, "if_index": 1, "ts": 1, "lanes": 1}):
        recent = _ts(smp["ts"]) >= since
        for li, ln in enumerate(smp.get("lanes") or []):
            rx = ln.get("rx")
            if rx is None or rx <= NO_LIGHT:
                continue
            a = acc.setdefault((smp["device_id"], int(smp["if_index"]), li), [0.0, 0, 0.0, 0, None])
            if recent:
                a[0] += rx
                a[1] += 1
                a[4] = rx
            else:
                a[2] += rx
                a[3] += 1
    worse = []
    for (dv, ix, li), (s24, n24, sant, nant, last) in acc.items():
        if not n24:
            continue
        avg24 = s24 / n24
        drop = (sant / nant - avg24) if nant else 0.0
        if drop >= OPTIC_DROP_DB or avg24 <= OPTIC_LOW_DBM:
            ifn = (names.get(dv) or {}).get(ix) or f"if{ix}"
            worse.append((drop, dname(dv), ifn, li, avg24, sant / nant if nant else None))
    worse.sort(key=lambda x: (-x[0], x[4]))
    data["optics_worse"] = len(worse)
    if worse:
        s = f"💡 Sinal óptico — {len(worse)} porta(s)/lane(s) pedindo atenção:"
        for drop, dn, ifn, li, avg, before in worse[:6]:
            lane = f" lane {li}" if li else ""
            s += f"\n  {dn} · {ifn}{lane}: RX {avg:.1f} dBm" + (f" (era {before:.1f}, −{drop:.1f} dB)" if before is not None and drop >= OPTIC_DROP_DB else " (baixo)")
        lines.append(s)
        attention += len(worse)

    # ---- tráfego: picos das interfaces monitoradas ----
    peaks: Dict[Tuple[str, int], List[float]] = {}
    async for smp in db.if_samples.find({"ts": {"$gte": since}}, {"_id": 0, "device_id": 1, "if_index": 1, "in_bps": 1, "out_bps": 1}):
        p = peaks.setdefault((smp["device_id"], int(smp["if_index"])), [0.0, 0.0])
        p[0] = max(p[0], smp.get("in_bps") or 0)
        p[1] = max(p[1], smp.get("out_bps") or 0)
    if peaks:
        mons = {(m["device_id"], int(m["if_index"])): m async for m in db.if_monitors.find({}, {"_id": 0})}
        top = sorted(peaks.items(), key=lambda x: -max(x[1]))[:5]
        s = "📈 Maiores picos de tráfego em 24 h (entrada / saída):"
        for (dv, ix), (pin, pout) in top:
            m = mons.get((dv, ix)) or {}
            label = m.get("alias") or m.get("if_name") or f"if{ix}"
            s += f"\n  {dname(dv)} · {label}: ↓{_gbps(pin)} ↑{_gbps(pout)}"
        lines.append(s)

    # ---- ataques DDoS ----
    attacks = await db.flow_attacks.find({"$or": [{"status": "active"}, {"start": {"$gte": since_iso}}]}, {"_id": 0, "series": 0}).to_list(500)
    data["attacks"] = len(attacks)
    if attacks:
        active = [a for a in attacks if a.get("status") == "active"]
        big = max(attacks, key=lambda a: a.get("peak_bps") or 0)
        s = f"🛡️ Ataques DDoS em 24 h: {len(attacks)}" + (f" ({len(active)} ATIVO(S) agora)" if active else "")
        s += f"\n  maior: {big.get('victim')} — {_gbps(big.get('peak_bps'))}bps, {big.get('type', '')}"
        lines.append(s)
        attention += len(active)

    # ---- configurações ----
    changed = await db.backups.find({"created_at": {"$gte": since_iso}, "ok": True, "changed": True, "first": {"$ne": True}},
                                    {"_id": 0, "device_name": 1, "diff_stats": 1}).to_list(500)
    failed = await db.backups.find({"created_at": {"$gte": since_iso}, "ok": False}, {"_id": 0, "device_name": 1, "error": 1}).to_list(500)
    data.update(config_changed=len(changed), backup_failed=len(failed))
    if changed or failed:
        s = "📝 Configurações:"
        if changed:
            items = [c["device_name"] + (f" (+{c['diff_stats']['added']}/−{c['diff_stats']['removed']})" if c.get("diff_stats") else "")
                     for c in changed[:8]]
            s += f"\n  alteradas em {len(changed)} equipamento(s): " + ", ".join(items) + ("…" if len(changed) > 8 else "")
        if failed:
            fn = sorted({f["device_name"] for f in failed})
            s += f"\n  backup falhou em {len(fn)}: " + ", ".join(fn[:8])
        lines.append(s)
        attention += len(failed)

    # ---- acessos ----
    fails = await db.login_events.count_documents({"ok": False, "at": {"$gte": since_iso}})
    blocked = await db.login_events.count_documents({"ok": False, "reason": "bloqueado", "at": {"$gte": since_iso}})
    oks = await db.login_events.count_documents({"ok": True, "at": {"$gte": since_iso}})
    users = await db.users.find({}, {"_id": 0, "email": 1, "totp_enabled": 1}).to_list(1000)
    no2fa = [u["email"] for u in users if not u.get("totp_enabled")]
    ai_changes = await db.ai_audit.count_documents({"at": {"$gte": since_iso}, "kind": {"$ne": "read"}})
    data.update(login_ok=oks, login_failed=fails, login_blocked=blocked, users_without_2fa=len(no2fa), ai_changes=ai_changes)
    s = f"🔐 Acessos em 24 h: {oks} login(s)"
    if fails:
        s += f", {fails} tentativa(s) errada(s)" + (f", {blocked} bloqueada(s)" if blocked else "")
    if no2fa:
        s += f"\n  {len(no2fa)} usuário(s) sem 2FA: " + ", ".join(no2fa[:5]) + ("…" if len(no2fa) > 5 else "")
    if ai_changes:
        s += f"\n  assistente executou {ai_changes} alteração(ões) confirmada(s)"
    lines.append(s)

    local = now.astimezone()
    title = f"📋 Resumo do BastiON — {local.strftime('%d/%m %H:%M')}"
    head = "✅ Nada pedindo atenção agora." if attention == 0 else f"⚠️ {attention} item(ns) pedindo atenção."
    data["attention"] = attention
    return title, head + "\n\n" + "\n\n".join(lines), data
