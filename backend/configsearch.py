"""Busca em todas as configurações salvas (último backup OK de cada equipamento).

Para cada linha encontrada devolve o "caminho" de blocos em que ela está (ex.: `bgp 65000` → `ipv4-family
vpn-instance X`), para responder perguntas como "onde está a VLAN 1302?" sem abrir config por config.
"""
import asyncio
import re
import time
from typing import Dict, List, Optional, Tuple

MAX_MATCHES_PER_DEVICE = 100
MAX_TOTAL_MATCHES = 3000
TIME_BUDGET = 12.0          # segundos por busca (regex patológica não trava o servidor por muito tempo)
_SEP = re.compile(r"^\s*[#!]+\s*$")

# cache: backup_id -> (lista de linhas, lista de caminhos por linha é calculada sob demanda)
_cache: Dict[str, List[str]] = {}


def _indent(line: str, dtype: str) -> int:
    if dtype == "mikrotik" and line.startswith("/"):
        return -1                      # "/ip address" abre seção; os "add ..." (coluna 0) ficam dentro dela
    return len(line) - len(line.lstrip(" \t"))


def build_matcher(q: str, mode: str, case: bool):
    q = (q or "").strip()
    if not q:
        raise ValueError("Digite o que procurar")
    if len(q) > 300:
        raise ValueError("Busca longa demais (máx. 300 caracteres)")
    flags = 0 if case else re.IGNORECASE
    if mode == "regex":
        try:
            rx = re.compile(q, flags)
        except re.error as e:
            raise ValueError(f"Regex inválida: {e}")
    elif mode == "word":
        rx = re.compile(r"(?<![\w.:/-])" + re.escape(q) + r"(?![\w:-]|\.\d)", flags)
    else:
        rx = re.compile(re.escape(q), flags)
    return rx


def search_text(content_lines: List[str], rx, dtype: str, context: int, limit: int) -> Tuple[int, List[dict]]:
    """Varre uma config: devolve (total de linhas que casam, detalhes das primeiras `limit`)."""
    stack: List[Tuple[int, str]] = []     # (indentação, linha) dos blocos abertos
    count, out = 0, []
    n = len(content_lines)
    for i, raw in enumerate(content_lines):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        ind = _indent(line, dtype)
        if _SEP.match(line):
            while stack and stack[-1][0] >= ind:
                stack.pop()
            continue
        while stack and stack[-1][0] >= ind:
            stack.pop()
        m = rx.search(line)
        if m:
            count += 1
            if len(out) < limit:
                spans = [[x.start(), x.end()] for x in rx.finditer(line)][:10]
                out.append({
                    "line_no": i + 1, "text": line[:500], "spans": spans,
                    "path": [h.strip()[:200] for _, h in stack][-4:],
                    "before": [[j + 1, content_lines[j].rstrip("\r")[:300]] for j in range(max(0, i - context), i)],
                    "after": [[j + 1, content_lines[j].rstrip("\r")[:300]] for j in range(i + 1, min(n, i + 1 + context))],
                })
        stack.append((ind, line))
    return count, out


async def latest_backups(db, device_ids: List[str]) -> List[dict]:
    rows = await db.backups.aggregate([
        {"$match": {"device_id": {"$in": device_ids}, "ok": True}},
        {"$sort": {"created_at": -1}},
        {"$group": {"_id": "$device_id", "id": {"$first": "$id"}, "created_at": {"$first": "$created_at"},
                    "device_name": {"$first": "$device_name"}}},
    ]).to_list(len(device_ids) + 1)
    return [{"device_id": r["_id"], "id": r["id"], "created_at": r["created_at"], "device_name": r["device_name"]} for r in rows]


async def _load(db, ids: List[str]) -> Dict[str, List[str]]:
    missing = [i for i in ids if i not in _cache]
    for k in range(0, len(missing), 50):
        async for b in db.backups.find({"id": {"$in": missing[k:k + 50]}}, {"_id": 0, "id": 1, "content": 1}):
            _cache[b["id"]] = (b.get("content") or "").split("\n")
    keep = set(ids)
    if len(_cache) > len(keep) + 200:            # descarta versões antigas que não são mais "a última"
        for k in [k for k in _cache if k not in keep]:
            _cache.pop(k, None)
    return {i: _cache[i] for i in ids if i in _cache}


async def search(db, devices: List[dict], q: str, mode: str = "word", case: bool = False, context: int = 1,
                 per_device: int = MAX_MATCHES_PER_DEVICE) -> dict:
    """devices: equipamentos que o usuário pode ver (dicts com id, name, device_type)."""
    rx = build_matcher(q, mode, case)
    context = max(0, min(int(context or 0), 5))
    by_id = {d["id"]: d for d in devices}
    latest = await latest_backups(db, list(by_id))
    contents = await _load(db, [b["id"] for b in latest])
    t0 = time.monotonic()

    def work():
        results, total, truncated = [], 0, False
        for b in sorted(latest, key=lambda x: (by_id[x["device_id"]].get("name") or "").lower()):
            if time.monotonic() - t0 > TIME_BUDGET or total >= MAX_TOTAL_MATCHES:
                truncated = True
                break
            lines = contents.get(b["id"])
            if lines is None:
                continue
            dev = by_id[b["device_id"]]
            lim = min(per_device, MAX_TOTAL_MATCHES - total)
            cnt, matches = search_text(lines, rx, dev.get("device_type") or "", context, lim)
            if cnt:
                total += len(matches)
                results.append({"device_id": dev["id"], "device_name": dev.get("name"), "device_type": dev.get("device_type"),
                                "host": dev.get("host"), "backup_id": b["id"], "backup_at": b["created_at"],
                                "count": cnt, "matches": matches})
        return results, truncated

    results, truncated = await asyncio.to_thread(work)
    with_backup = {b["device_id"] for b in latest}
    return {
        "query": q, "mode": mode, "case": case,
        "devices_searched": len(latest), "devices_matched": len(results),
        "lines_matched": sum(r["count"] for r in results), "truncated": truncated,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "without_backup": sorted(d.get("name") or "" for i, d in by_id.items() if i not in with_backup),
        "results": results,
    }


def as_text(res: dict, max_per_device: int = 8) -> str:
    """Resumo em texto (assistente de IA / Telegram)."""
    if not res["results"]:
        return (f"Nada encontrado para '{res['query']}' nas configs de {res['devices_searched']} equipamento(s)."
                + (f" Sem backup: {', '.join(res['without_backup'][:10])}." if res["without_backup"] else ""))
    out = [f"'{res['query']}' aparece em {res['devices_matched']} de {res['devices_searched']} equipamento(s) "
           f"({res['lines_matched']} linha(s)){' — resultado cortado' if res['truncated'] else ''}:"]
    for r in res["results"]:
        out.append(f"\n## {r['device_name']} ({r['device_type']}) — backup de {r['backup_at'][:16].replace('T', ' ')} UTC, {r['count']} linha(s)")
        for m in r["matches"][:max_per_device]:
            where = " > ".join(m["path"])
            out.append(f"  L{m['line_no']}: {m['text'].strip()}" + (f"   [em: {where}]" if where else ""))
        if r["count"] > max_per_device:
            out.append(f"  … mais {r['count'] - max_per_device} linha(s)")
    return "\n".join(out)
