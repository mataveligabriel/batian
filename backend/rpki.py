"""RPKI: o BastiON comanda um Krill (NLnet Labs) que roda num container ao lado.

  BastiON (tela, permissões, histórico, alertas) ──API──► Krill (chaves, certificados, ROAs, publicação)

Cada rede/ASN gerenciado é uma CA no Krill. O vínculo com o Registro.br (RPKI delegado) é feito trocando dois XML
(RFC 8183): o pedido sai daqui, a resposta volta do portal. Toda a criptografia fica no Krill.
"""
import ipaddress
import os
import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

import httpx

KRILL_URL = os.environ.get("KRILL_URL", "https://127.0.0.1:3000").rstrip("/")
KRILL_TOKEN = os.environ.get("KRILL_ADMIN_TOKEN", "")
_HANDLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
MAX_XML = 100_000


class RpkiError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def configured() -> bool:
    return bool(KRILL_TOKEN)


def check_handle(h: str) -> str:
    h = (h or "").strip()
    if not _HANDLE.match(h):
        raise RpkiError("Identificador inválido: use letras, números, - e _ (até 32), sem espaço. Ex.: LINK10")
    return h


def _err(r: httpx.Response) -> str:
    try:
        j = r.json()
        return j.get("msg") or j.get("label") or r.text[:300]
    except Exception:
        return (r.text or f"HTTP {r.status_code}")[:300]


async def _call(method: str, path: str, *, json=None, content: Optional[str] = None, ctype: Optional[str] = None,
                timeout: float = 30, raw: bool = False):
    if not configured():
        raise RpkiError("O Krill ainda não foi ativado neste servidor (rode o deploy/rpki-setup.sh)", 503)
    headers = {"Authorization": f"Bearer {KRILL_TOKEN}"}
    if ctype:
        headers["Content-Type"] = ctype
    try:
        # o Krill escuta só em 127.0.0.1 com certificado próprio gerado por ele: não há o que validar
        async with httpx.AsyncClient(verify=False, timeout=timeout) as c:
            r = await c.request(method, KRILL_URL + path, json=json, content=content, headers=headers)
    except httpx.HTTPError as e:
        raise RpkiError(f"Krill fora do ar ({type(e).__name__}) — confira: docker compose logs krill", 503)
    if r.status_code in (401, 403):
        raise RpkiError("O Krill recusou o token (KRILL_ADMIN_TOKEN do .env difere do que o Krill usa)", 503)
    if r.status_code >= 400:
        raise RpkiError(_err(r), 404 if r.status_code == 404 else 400)
    if raw:
        return r.text
    if not r.content:
        return {}
    try:
        return r.json()
    except Exception:
        return {"text": r.text}


# ---------- servidor / CAs ----------
async def info() -> dict:
    try:
        i = await _call("GET", "/stats/info", timeout=6)
        return {"online": True, "version": i.get("version"), "started": i.get("started")}
    except RpkiError as e:
        return {"online": False, "error": str(e)}


async def list_cas() -> List[str]:
    return sorted((c.get("handle") for c in (await _call("GET", "/api/v1/cas")).get("cas", []) if c.get("handle")), key=str.lower)


async def create_ca(handle: str):
    await _call("POST", "/api/v1/cas", json={"handle": check_handle(handle)})


async def delete_ca(handle: str):
    await _call("DELETE", f"/api/v1/cas/{check_handle(handle)}")


def _split(s) -> List[str]:
    return [x.strip() for x in str(s or "").split(",") if x.strip()]


def _result(ex: Optional[dict]) -> Tuple[Optional[bool], str]:
    """Resultado da última conversa com o pai/repositório: (ok, mensagem)."""
    if not ex:
        return None, ""
    res = ex.get("result")
    if res == "Success" or (isinstance(res, dict) and "Success" in res):
        return True, ""
    if isinstance(res, dict):
        v = next(iter(res.values()), "")
        return False, str(v.get("msg") if isinstance(v, dict) else v)[:300]
    return False, str(res or "")[:300]


def _ts(v) -> Optional[int]:
    return int(v) if isinstance(v, (int, float)) else None


async def ca_detail(handle: str) -> dict:
    h = check_handle(handle)
    d = await _call("GET", f"/api/v1/cas/{h}")
    res = d.get("resources") or {}
    out = {"handle": h, "asns": _split(res.get("asn")), "ipv4": _split(res.get("ipv4") or res.get("v4")),
           "ipv6": _split(res.get("ipv6") or res.get("v6")), "repo": bool(d.get("repo_info")),
           "repo_uri": (d.get("repo_info") or {}).get("rrdp_notification_uri") or (d.get("repo_info") or {}).get("sia_base"),
           "parents": [], "repo_ok": None, "repo_error": "", "repo_last": None}
    statuses = {}
    try:
        statuses = await _call("GET", f"/api/v1/cas/{h}/parents")
    except RpkiError:
        pass
    for p in d.get("parents") or []:
        name = p.get("handle") if isinstance(p, dict) else str(p)
        st = statuses.get(name) if isinstance(statuses, dict) else None
        ok, msg = _result((st or {}).get("last_exchange"))
        out["parents"].append({"name": name, "ok": ok, "error": msg, "last_success": _ts((st or {}).get("last_success"))})
    if out["repo"]:
        try:
            st = await _call("GET", f"/api/v1/cas/{h}/repo/status")
            out["repo_ok"], out["repo_error"] = _result(st.get("last_exchange"))
            out["repo_last"] = _ts(st.get("last_success"))
        except RpkiError:
            pass
    return out


async def child_request(handle: str) -> str:
    return await _call("GET", f"/api/v1/cas/{check_handle(handle)}/id/child_request.xml", raw=True)


async def publisher_request(handle: str) -> str:
    return await _call("GET", f"/api/v1/cas/{check_handle(handle)}/id/publisher_request.xml", raw=True)


def parse_setup_xml(xml: str, expect: str) -> dict:
    """Lê a resposta RFC 8183 colada pelo administrador (`parent_response` ou `repository_response`)."""
    xml = (xml or "").strip()
    if not xml:
        raise RpkiError("Cole o XML de resposta")
    if len(xml) > MAX_XML or "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise RpkiError("XML inválido")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise RpkiError("Isto não é um XML válido — cole o arquivo inteiro, do <" + expect + " até o fim")
    tag = root.tag.split("}")[-1]                          # ignora o namespace do XML
    if tag != expect:
        other = {"child_request": "este é o pedido (o que sai daqui); cole a RESPOSTA que o Registro.br devolve",
                 "publisher_request": "este é o pedido (o que sai daqui); cole a RESPOSTA do servidor de publicação",
                 "parent_response": "esta é a resposta do pai (Registro.br) — ela vai no passo 2",
                 "repository_response": "esta é a resposta do servidor de publicação — ela vai no passo 1"}.get(tag)
        raise RpkiError(f"XML errado para este passo: {other or 'esperava <' + expect + '>'}")
    want = "parent_bpki_ta" if expect == "parent_response" else "repository_bpki_ta"
    ta = next((c for c in root if c.tag.split("}")[-1] == want), None)
    cert = re.sub(r"\s+", "", (ta.text if ta is not None else "") or "")
    if not cert:
        raise RpkiError("XML incompleto (falta o certificado)")
    a = root.attrib
    if expect == "parent_response":
        return {"tag": a.get("tag"), "id_cert": cert, "parent_handle": a.get("parent_handle"),
                "child_handle": a.get("child_handle"), "service_uri": a.get("service_uri")}
    return {"tag": a.get("tag"), "publisher_handle": a.get("publisher_handle"), "id_cert": cert,
            "service_uri": a.get("service_uri"),
            "repo_info": {"sia_base": a.get("sia_base"), "rrdp_notification_uri": a.get("rrdp_notification_uri")}}


async def set_repo(handle: str, xml: str):
    h = check_handle(handle)
    parsed = parse_setup_xml(xml, "repository_response")
    try:
        await _call("POST", f"/api/v1/cas/{h}/repo", content=xml.strip(), ctype="application/xml")
    except RpkiError as e:
        if e.status == 503:
            raise
        await _call("POST", f"/api/v1/cas/{h}/repo", json={"repository_response": parsed})


async def add_parent(handle: str, name: str, xml: str):
    h, name = check_handle(handle), check_handle(name)
    parsed = parse_setup_xml(xml, "parent_response")
    try:
        await _call("POST", f"/api/v1/cas/{h}/parents/{name}", content=xml.strip(), ctype="application/xml")
    except RpkiError as e:
        if e.status == 503:
            raise
        await _call("POST", f"/api/v1/cas/{h}/parents/{name}", json=parsed)


async def remove_parent(handle: str, name: str):
    await _call("DELETE", f"/api/v1/cas/{check_handle(handle)}/parents/{check_handle(name)}")


# ---------- ROAs ----------
def clean_roa(r: dict, need_max: bool = True) -> dict:
    try:
        asn = int(str(r.get("asn", "")).upper().replace("AS", "").strip())
    except ValueError:
        raise RpkiError("ASN inválido")
    if not 0 <= asn <= 4294967295:
        raise RpkiError("ASN inválido")
    try:
        net = ipaddress.ip_network(str(r.get("prefix", "")).strip(), strict=True)
    except ValueError:
        raise RpkiError(f"Prefixo inválido: {r.get('prefix')!r} (use o endereço de rede, ex.: 200.160.0.0/20)")
    top = 32 if net.version == 4 else 128
    ml = r.get("max_length")
    if ml in (None, ""):
        ml = net.prefixlen
    try:
        ml = int(ml)
    except (TypeError, ValueError):
        raise RpkiError("Tamanho máximo inválido")
    if not net.prefixlen <= ml <= top:
        raise RpkiError(f"Tamanho máximo de {net} tem de ficar entre /{net.prefixlen} e /{top}")
    out = {"asn": asn, "prefix": str(net), "max_length": ml}
    c = str(r.get("comment") or "").strip()[:120]
    if c:
        out["comment"] = c
    return out


def roa_warnings(roa: dict, resources: Optional[dict] = None) -> List[str]:
    """Avisos que não impedem salvar, mas merecem um segundo olhar."""
    net = ipaddress.ip_network(roa["prefix"])
    w = []
    edge = 24 if net.version == 4 else 48
    if roa["max_length"] > max(edge, net.prefixlen):
        w.append(f"tamanho máximo /{roa['max_length']} é mais específico que /{edge}: a internet não aceita anúncios assim, "
                 f"e deixar aberto facilita sequestro de sub-prefixo")
    elif roa["max_length"] > net.prefixlen:
        w.append(f"libera qualquer anúncio de /{net.prefixlen} até /{roa['max_length']} — só deixe assim se você realmente "
                 f"anuncia os mais específicos")
    if roa["asn"] == 0:
        w.append("AS0 = ninguém pode anunciar este prefixo")
    if resources is not None:
        pool = [ipaddress.ip_network(x, strict=False) for x in (resources.get("ipv4", []) + resources.get("ipv6", [])) if "/" in x]
        if pool and not any(p.version == net.version and net.subnet_of(p) for p in pool):
            w.append("este prefixo não está entre os recursos certificados desta CA — o Krill vai recusar")
    return w


def _roa_of(e: dict) -> Optional[dict]:
    d = e.get("configured") or e.get("roa") or e.get("definition")
    if isinstance(d, dict) and "prefix" in d:
        return {"asn": d.get("asn"), "prefix": d.get("prefix"), "max_length": d.get("max_length"), "comment": d.get("comment")}
    return None


async def roas(handle: str) -> List[dict]:
    data = await _call("GET", f"/api/v1/cas/{check_handle(handle)}/routes")
    out = []
    for r in data if isinstance(data, list) else []:
        if isinstance(r, dict) and "prefix" in r:
            out.append({"asn": r.get("asn"), "prefix": r.get("prefix"), "max_length": r.get("max_length"),
                        "comment": r.get("comment") or ""})
    out.sort(key=lambda r: (ipaddress.ip_network(r["prefix"], strict=False).version,
                            int(ipaddress.ip_network(r["prefix"], strict=False).network_address), r["max_length"] or 0))
    return out


async def update_roas(handle: str, added: List[dict], removed: List[dict]):
    await _call("POST", f"/api/v1/cas/{check_handle(handle)}/routes",
                json={"added": added, "removed": [{k: r[k] for k in ("asn", "prefix", "max_length")} for r in removed]})


STATE_TEXT = {
    "roa_seen": ("ok", "anúncio visto e válido"),
    "roa_unseen": ("info", "nenhum anúncio visto com este ROA"),
    "roa_too_permissive": ("warn", "ROA mais aberto do que o que é anunciado"),
    "roa_redundant": ("info", "coberto por outro ROA"),
    "roa_not_held": ("bad", "prefixo fora dos recursos da CA"),
    "roa_disallowing": ("warn", "só invalida anúncios (não autoriza nenhum)"),
    "roa_as0": ("info", "AS0 — prefixo não deve ser anunciado"),
    "roa_as0_redundant": ("info", "AS0 redundante"),
    "announcement_validated": ("ok", "válido"),
    "announcement_invalid_asn": ("bad", "INVÁLIDO — ASN de origem diferente do ROA"),
    "announcement_invalid_length": ("bad", "INVÁLIDO — mais específico que o tamanho máximo do ROA"),
    "announcement_disallowed": ("bad", "INVÁLIDO — barrado por ROA"),
    "announcement_not_found": ("warn", "sem ROA"),
}


async def analysis(handle: str) -> dict:
    """O que o Krill enxerga na tabela global (RIPE RIS) contra os ROAs: estado de cada ROA e de cada anúncio."""
    try:
        data = await _call("GET", f"/api/v1/cas/{check_handle(handle)}/routes/analysis/full", timeout=60)
    except RpkiError as e:
        return {"available": False, "error": str(e), "roa_state": {}, "announcements": []}
    roa_state, anns = {}, []
    for e in data if isinstance(data, list) else []:
        if not isinstance(e, dict):
            continue
        st = str(e.get("state") or "")
        level, text = STATE_TEXT.get(st, ("info", st.replace("_", " ")))
        roa = _roa_of(e)
        if roa:
            roa_state[f"{roa['asn']}|{roa['prefix']}|{roa['max_length']}"] = {"state": st, "level": level, "text": text}
        a = e.get("announcement")
        if isinstance(a, dict) and "prefix" in a:
            anns.append({"asn": a.get("asn"), "prefix": a.get("prefix"), "state": st, "level": level, "text": text})
    order = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
    anns.sort(key=lambda a: (order.get(a["level"], 9), str(a["prefix"])))
    return {"available": True, "roa_state": roa_state, "announcements": anns}
