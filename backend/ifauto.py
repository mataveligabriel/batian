"""Monitoramento automático de interfaces.

Uma regra por equipamento diz quais interfaces entram no alarme de queda (ex.: "portas físicas que estão up").
Ao aplicar e depois a cada varredura (de tempos em tempos), as interfaces novas que casam com a regra entram sozinhas.
O que o usuário tira do monitoramento vai para a lista de ignoradas e nunca volta pela regra.
"""
import re
from typing import Dict, List, Optional

# o que normalmente não interessa alarmar: loopbacks, gerência, VLANs lógicas, túneis e subinterfaces (nome terminando em .N)
DEFAULT_EXCLUDE = (r"^(null|inloopback|loopback|lo\d*$|console|meth|mgmt|management|aux|vlanif|vlan|vlan-interface|tunnel|virtual|"
                   r"dialer|nve|stack|cpu|sit|gre|ip-|register|bdif|vbdif|ve-|irb|lsi|dsc|pime|pimd|mtun|tap|gr-|lt-|pp0|"
                   r"vme|jsrv|em\d|fxp|bme|ppd|ppe|mpls|cbp|pip|vtep|vt-|<|pppoe|l2tp|ovpn|sstp|pptp|wg|wireguard|eoip|ipip|bridge|docker|veth|br-|"
                   r"virbr|vnet|tun|vxlan|gpon-onu|epon-onu|xgpon-onu|onu)|\.\d+$|:\d+$")
PRESETS = {
    "physical_up": {"label": "Portas físicas que estão up", "include": "", "exclude": DEFAULT_EXCLUDE, "only_up": True, "only_alias": False},
    "physical_desc": {"label": "Portas físicas com descrição", "include": "", "exclude": DEFAULT_EXCLUDE, "only_up": False, "only_alias": True},
    "physical_all": {"label": "Todas as portas físicas", "include": "", "exclude": DEFAULT_EXCLUDE, "only_up": False, "only_alias": False},
    "custom": {"label": "Personalizado", "include": "", "exclude": "", "only_up": False, "only_alias": False},
}
MAX_RE = 300


class RuleError(ValueError):
    pass


def _re(pattern: str, what: str) -> Optional[re.Pattern]:
    p = (pattern or "").strip()
    if not p:
        return None
    if len(p) > MAX_RE * 3:
        raise RuleError(f"Filtro de {what} muito longo")
    try:
        return re.compile(p, re.IGNORECASE)
    except re.error as e:
        raise RuleError(f"Filtro de {what} inválido: {e}")


def clean_rule(b: dict) -> dict:
    """Valida o que veio da tela. `include`/`exclude` são expressões regulares sobre nome, descrição e alias."""
    out = {"enabled": bool(b.get("enabled", True)), "include": str(b.get("include") or "").strip()[:MAX_RE * 3],
           "exclude": str(b.get("exclude") or "").strip()[:MAX_RE * 3], "only_up": bool(b.get("only_up")),
           "only_alias": bool(b.get("only_alias")), "preset": b.get("preset") if b.get("preset") in PRESETS else "custom",
           "notify_new": bool(b.get("notify_new", True))}
    _re(out["include"], "inclusão")
    _re(out["exclude"], "exclusão")
    return out


def matches(iface: dict, rule: dict, _cache: Dict[str, Optional[re.Pattern]] = None) -> bool:
    cache = _cache if _cache is not None else {}
    inc = cache.setdefault("i:" + rule.get("include", ""), _re(rule.get("include", ""), "inclusão"))
    exc = cache.setdefault("e:" + rule.get("exclude", ""), _re(rule.get("exclude", ""), "exclusão"))
    name = str(iface.get("name") or "")
    texts = [name, str(iface.get("descr") or ""), str(iface.get("alias") or "")]
    if exc and exc.search(name):
        return False                                  # exclusão olha só o nome (evita tirar porta só porque a descrição diz "vlan")
    if inc and not any(inc.search(t) for t in texts):
        return False
    if rule.get("only_up") and iface.get("oper") != "up":
        return False
    if rule.get("only_alias") and not str(iface.get("alias") or "").strip():
        return False
    if iface.get("admin") == "down" and rule.get("only_up"):
        return False
    return True


def plan(ifaces: List[dict], rule: dict, monitored: set, ignored: set) -> List[dict]:
    """Interfaces que entram agora: casam com a regra, ainda não monitoradas e não ignoradas pelo usuário."""
    cache: Dict[str, Optional[re.Pattern]] = {}
    out = []
    for i in ifaces:
        idx = i.get("index")
        if idx is None or idx in monitored or idx in ignored:
            continue
        if matches(i, rule, cache):
            out.append({"index": int(idx), "name": str(i.get("name") or f"if{idx}"), "alias": str(i.get("alias") or "")})
    return out


def preview(ifaces: List[dict], rule: dict) -> Dict[str, int]:
    cache: Dict[str, Optional[re.Pattern]] = {}
    n = sum(1 for i in ifaces if matches(i, rule, cache))
    return {"match": n, "total": len(ifaces)}
