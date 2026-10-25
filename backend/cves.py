"""CVEs dos equipamentos: versão de firmware (backups/Conformidade) x base da NVD (NIST) + catálogo KEV da CISA.

Para cada produto (RouterOS, Junos, IOS, IOS XE, IOS XR, NX-OS, firmware Huawei por modelo) o servidor baixa da NVD
todas as CVEs do produto (API 2.0, virtualMatchString) e guarda só o que precisa para casar a versão: as faixas
versionStart/End e as versões exatas dos cpeMatch vulneráveis. A comparação é local.

Limitação: a NVD às vezes liga a CVE ao sistema "rodando em" certos modelos; aqui olhamos só o sistema, então pode
aparecer CVE de outro modelo da mesma linha. Por isso cada CVE mostra o texto para conferir.
"""
import re
from typing import Dict, List, Optional, Tuple

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
DEFAULTS = {"enabled": True, "alert_kev": True, "alert_min_score": 9.0, "refresh_days": 3}
PRODUCT_LABEL = {"mikrotik:routeros": "MikroTik RouterOS", "juniper:junos": "Juniper Junos", "cisco:ios": "Cisco IOS",
                 "cisco:ios_xe": "Cisco IOS XE", "cisco:ios_xr": "Cisco IOS XR", "cisco:nx-os": "Cisco NX-OS"}


def _num_or_str(t: str):
    return (0, int(t)) if t.isdigit() else (1, t)


def vtuple(s: str) -> Tuple:
    s = (s or "").lower().replace("\\", "")
    return tuple(_num_or_str(t) for t in re.findall(r"\d+|[a-z]+", s))


def products_for(device_type: str, version: str, model: str = "", head: str = "") -> List[Tuple[str, str]]:
    """(produto NVD 'fornecedor:produto', versão normalizada) para o equipamento."""
    v = (version or "").strip()
    if not v:
        return []
    vt = (device_type or "").lower()
    if vt == "mikrotik":
        return [("mikrotik:routeros", re.split(r"\s", v)[0])]
    if vt == "juniper":
        return [("juniper:junos", v)]
    if vt == "cisco":
        h = head or ""
        if re.search(r"IOS\s*XR", h, re.I):
            return [("cisco:ios_xr", v)]
        if re.search(r"NX-?OS", h, re.I):
            return [("cisco:nx-os", v)]
        m = re.match(r"(\d+)\.", v)
        if m and int(m.group(1)) >= 16:
            return [("cisco:ios_xe", v)]
        return [("cisco:ios", v)]
    if vt == "huawei":
        m = re.search(r"(V\d{3}R\d{3}C\d{2}\w*)", v, re.I)
        ver = (m.group(1) if m else v).lower()
        out = []
        if model:
            mm = re.sub(r"[^a-z0-9]", "", model.lower())
            if mm:
                out.append((f"huawei:{mm}_firmware", ver))
        return out
    return []


def _crit_parts(criteria: str) -> Tuple[str, str, str]:
    """cpe:2.3:o:juniper:junos:21.4:r3-s4:*:... -> ('juniper:junos', '21.4', 'r3-s4')"""
    p = re.split(r"(?<!\\):", criteria)
    if len(p) < 7:
        return "", "*", "*"
    return f"{p[3]}:{p[4]}", p[5], p[6]


def compact_nvd(js: dict, product: str) -> List[dict]:
    """Resposta da NVD -> [{id, score, sev, pub, desc, m:[[ver, upd, vsi, vse, vei, vee]]}] só com os cpeMatch do produto."""
    out = []
    for v in js.get("vulnerabilities") or []:
        c = v.get("cve") or {}
        ms = []
        for conf in c.get("configurations") or []:
            for node in conf.get("nodes") or []:
                for cm in node.get("cpeMatch") or []:
                    if not cm.get("vulnerable"):
                        continue
                    prod, ver, upd = _crit_parts(cm.get("criteria", ""))
                    if prod != product:
                        continue
                    ms.append([ver, upd, cm.get("versionStartIncluding"), cm.get("versionStartExcluding"),
                               cm.get("versionEndIncluding"), cm.get("versionEndExcluding")])
        if not ms:
            continue
        score, sev = None, ""
        mt = c.get("metrics") or {}
        for k in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
            if mt.get(k):
                d = mt[k][0].get("cvssData") or {}
                score = d.get("baseScore")
                sev = (d.get("baseSeverity") or mt[k][0].get("baseSeverity") or "").lower()
                break
        desc = next((d.get("value", "") for d in c.get("descriptions") or [] if d.get("lang") == "en"), "")
        out.append({"id": c.get("id"), "score": score, "sev": sev, "pub": (c.get("published") or "")[:10], "desc": desc[:400], "m": ms})
    return out


def _cmp(a: Tuple, b: Tuple) -> int:
    return (a > b) - (a < b)


def version_hit(m: list, version: str, product: str) -> bool:
    ver, upd, vsi, vse, vei, vee = m
    dv = vtuple(version)
    if ver not in ("*", "-", ""):
        cv = vtuple(ver + ("" if upd in ("*", "-", "") else upd))
        if dv == cv:
            return True
        # Junos: "21.4R3-S5.4" (build no fim) casa com "21.4:r3-s5"
        if product == "juniper:junos" and len(dv) == len(cv) + 1 and dv[:len(cv)] == cv and dv[-1][0] == 0:
            return True
        return False
    if not any((vsi, vse, vei, vee)):
        return True                       # todas as versões
    if vsi and _cmp(dv, vtuple(vsi)) < 0:
        return False
    if vse and _cmp(dv, vtuple(vse)) <= 0:
        return False
    if vei and _cmp(dv, vtuple(vei)) > 0:
        return False
    if vee and _cmp(dv, vtuple(vee)) >= 0:
        return False
    return True


def matches(items: List[dict], version: str, product: str, kev: set) -> List[dict]:
    out = []
    for it in items:
        hit = [m for m in it["m"] if version_hit(m, version, product)]
        if not hit:
            continue
        broad = all(m[0] in ("*", "-", "") and not any(m[2:]) for m in hit)
        out.append({k: it[k] for k in ("id", "score", "sev", "pub", "desc")} | {"kev": it["id"] in kev, "all_versions": broad})
    out.sort(key=lambda x: (not x["kev"], -(x["score"] or 0), x["id"]))
    return out


def summary(found: List[dict]) -> dict:
    return {"total": len(found), "kev": sum(1 for c in found if c["kev"]),
            "critical": sum(1 for c in found if (c["score"] or 0) >= 9), "high": sum(1 for c in found if 7 <= (c["score"] or 0) < 9),
            "max_score": max((c["score"] or 0 for c in found), default=0)}
