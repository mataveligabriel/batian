"""Política de comandos: regras que bloqueiam, pedem aprovação ou alertam antes de um comando chegar ao equipamento.

Vale para o terminal web (tecla Enter), Execução em Lote, roteiros, script de OLT, alterações do assistente de IA e
comandos digitados direto no equipamento quando ele autoriza comando a comando pelo TACACS+ do BastiON.
Regras em ordem; a mais restritiva entre as que casam vence (bloquear > aprovar > alertar > permitir). Uma regra
"permitir" vale como exceção para as regras ABAIXO dela (ex.: admins podem reiniciar sem aprovação); as de cima continuam.
"""
import hashlib
import re
from typing import Dict, List, Optional

ACTIONS = ("block", "approve", "alert", "allow")
ACTION_LABEL = {"block": "Bloquear", "approve": "Pedir aprovação", "alert": "Alertar", "allow": "Permitir"}
RANK = {"allow": 0, "alert": 1, "approve": 2, "block": 3}
ROLES = ("admin", "operator", "viewer")
CHANNELS = ("terminal", "batch", "runbook", "olt", "ai", "tacacs")
DEFAULTS = {"enabled": True, "wait_min": 10, "grant_min": 30, "self_approve": False, "notify": True}
RULE_FIELDS = ("name", "pattern", "action", "device_types", "tags", "device_ids", "roles", "user_ids", "channels",
               "severity", "enabled", "description", "preset")

PRESETS = [
    {"preset": "wipe", "name": "Apagar configuração / formatar", "action": "block", "severity": "critical",
     "pattern": r"^(reset\s+saved-configuration|write\s+erase|wr\s+er|erase\s+(startup|nvram|flash)|format\s|"
                r"request\s+system\s+zeroize|/system\s+reset-configuration|delete\s+(flash|startup)|erase\s+startup-config|"
                r"rm\s+-rf\s+/(\s|$)|mkfs\b|dd\s+if=.*of=/dev/[sh]d)",
     "description": "Comandos que apagam a configuração inteira ou o armazenamento do equipamento."},
    {"preset": "reboot", "name": "Reiniciar equipamento", "action": "approve", "severity": "high",
     "pattern": r"^(reload|reboot|/system\s+reboot|request\s+system\s+(reboot|halt|power-off)|shutdown\s+-[rh]|"
                r"system\s+reboot|reset\s+slot|reboot\s+slot|init\s+[06])\b",
     "description": "Reboot do equipamento ou de placa precisa de outra pessoa aprovando."},
    {"preset": "bgp", "name": "Derrubar/alterar sessão BGP", "action": "approve", "severity": "high",
     "pattern": r"^(undo\s+peer\s|no\s+neighbor\s|neighbor\s+\S+\s+shutdown|peer\s+\S+\s+ignore|"
                r"delete\s+protocols\s+bgp|set\s+protocols\s+bgp\s+.*\bdisable\b|reset\s+bgp\s|clear\s+(ip\s+)?bgp\s+\*|"
                r"undo\s+bgp\s|no\s+router\s+bgp|/routing\s+bgp\s+peer\s+(disable|remove))",
     "description": "Remover vizinho, desligar ou resetar BGP derruba trânsito/clientes."},
    {"preset": "shut", "name": "Desligar interface", "action": "alert", "severity": "medium",
     "pattern": r"^(shutdown|disable|set\s+interfaces\s+\S+\s+disable|/interface\s+(ethernet\s+)?disable)\s*$|^/interface\s+\S*\s*disable\s",
     "description": "Avisa no SOC quando alguém desliga uma porta."},
    {"preset": "users", "name": "Mexer em usuários e AAA do equipamento", "action": "alert", "severity": "high",
     "pattern": r"^(local-user\s|undo\s+local-user|username\s|no\s+username|set\s+system\s+login\s+user|"
                r"delete\s+system\s+login|/user\s+(add|remove|set)|aaa\s|undo\s+aaa|tacacs|radius-server|useradd|passwd\b)",
     "description": "Criar/alterar usuário local, senha ou AAA."},
]


class PolicyError(ValueError):
    pass


def norm(cmd: str) -> str:
    return re.sub(r"\s+", " ", (cmd or "").strip())


def command_lines(text: str) -> List[str]:
    out = []
    for ln in (text or "").splitlines():
        s = norm(ln)
        if not s or s in ("!", "#") or s.startswith(("! ", "# ")):
            continue
        out.append(s)
    return out


def clean_rule(body: dict) -> dict:
    r = {k: body.get(k) for k in RULE_FIELDS if k in body}
    r["name"] = str(r.get("name") or "").strip()[:80] or "Regra"
    pat = str(r.get("pattern") or "").strip()
    if not pat:
        raise PolicyError("Informe o padrão (expressão regular) do comando")
    if len(pat) > 800:
        raise PolicyError("Padrão grande demais")
    try:
        re.compile(pat, re.I)
    except re.error as e:
        raise PolicyError(f"Expressão regular inválida: {e}")
    r["pattern"] = pat
    if r.get("action") not in ACTIONS:
        raise PolicyError("Ação inválida")
    for k in ("device_types", "tags", "device_ids", "user_ids"):
        r[k] = [str(x).strip()[:64] for x in (r.get(k) or []) if str(x).strip()][:200]
    r["roles"] = [x for x in (r.get("roles") or []) if x in ROLES]
    r["channels"] = [x for x in (r.get("channels") or []) if x in CHANNELS]
    r["severity"] = r.get("severity") if r.get("severity") in ("info", "low", "medium", "high", "critical") else "medium"
    r["enabled"] = r.get("enabled", True) is not False
    r["description"] = str(r.get("description") or "")[:300]
    r["preset"] = str(r.get("preset") or "")[:32]
    return r


_RX: Dict[str, re.Pattern] = {}


def _rx(p: str) -> Optional[re.Pattern]:
    if p not in _RX:
        try:
            _RX[p] = re.compile(p, re.I)
        except re.error:
            _RX[p] = None
        if len(_RX) > 2000:
            _RX.clear()
    return _RX.get(p)


def applies(rule: dict, user: dict, dev: dict, channel: str) -> bool:
    if not rule.get("enabled", True):
        return False
    if rule.get("roles") and (user.get("role") or "operator") not in rule["roles"]:
        return False
    if rule.get("user_ids") and user.get("id") not in rule["user_ids"]:
        return False
    if rule.get("channels") and channel not in rule["channels"]:
        return False
    if rule.get("device_ids") and (dev or {}).get("id") not in rule["device_ids"]:
        return False
    if rule.get("device_types") and ((dev or {}).get("device_type") or "") not in rule["device_types"]:
        return False
    if rule.get("tags") and not set(rule["tags"]) & set((dev or {}).get("tags") or []):
        return False
    return True


def match(rules: List[dict], user: dict, dev: dict, command: str, channel: str) -> Optional[dict]:
    """A regra que vale para um comando: a primeira 'permitir' que casar encerra; senão, a mais restritiva."""
    c = norm(command)
    if not c:
        return None
    best = None
    for r in rules:
        if not applies(r, user, dev, channel):
            continue
        rx = _rx(r["pattern"])
        if not rx or not rx.search(c):
            continue
        if r["action"] == "allow":
            return best or r            # exceção: vale para as regras de baixo; as de cima continuam valendo
        if not best or RANK[r["action"]] > RANK[best["action"]]:
            best = r
    return best


def evaluate(rules: List[dict], user: dict, dev: dict, commands: List[str], channel: str) -> dict:
    """Vários comandos (lote/roteiro): a decisão mais restritiva e quais comandos a causaram."""
    worst, hits = "allow", []
    for c in commands:
        r = match(rules, user, dev, c, channel)
        if not r:
            continue
        hits.append({"command": c, "rule_id": r.get("id"), "rule": r["name"], "action": r["action"], "severity": r.get("severity", "medium")})
        if RANK[r["action"]] > RANK[worst]:
            worst = r["action"]
    return {"action": worst, "hits": hits}


def grant_hash(user_id: str, device_id: str, commands: List[str]) -> str:
    return hashlib.sha256((f"{user_id}\0{device_id}\0" + "\n".join(norm(c) for c in commands)).encode()).hexdigest()


# ---------------------------------------------------------------- linha do terminal
_CSI = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z@])")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


class LineTracker:
    """Reconstrói a linha atual da tela a partir da saída (eco das teclas, backspace, \\r, ESC[K, ESC[nD/C, ESC[P),
    para saber o comando de verdade quando a pessoa aperta Enter (com histórico, Tab e edição no meio)."""

    def __init__(self):
        self.buf: List[str] = []
        self.cur = 0
        self.prompt = ""

    def feed(self, s: str):
        s = _OSC.sub("", s or "")
        i, n = 0, len(s)
        while i < n:
            ch = s[i]
            if ch == "\x1b":
                m = _CSI.match(s, i)
                if m:
                    arg, fn = m.group(1), m.group(2)
                    k = int(arg) if arg.isdigit() else 1
                    if fn == "K":
                        if arg in ("", "0"):
                            del self.buf[self.cur:]
                        elif arg == "2":
                            self.buf, self.cur = [], 0
                    elif fn == "D":
                        self.cur = max(0, self.cur - k)
                    elif fn == "C":
                        self.cur = min(len(self.buf), self.cur + k)
                    elif fn == "P":
                        del self.buf[self.cur:self.cur + k]
                    elif fn == "@":
                        self.buf[self.cur:self.cur] = [" "] * k
                    elif fn == "G":
                        self.cur = max(0, min(len(self.buf), k - 1))
                    i = m.end()
                    continue
                i += 2
                continue
            if ch == "\n":
                self.buf, self.cur = [], 0
            elif ch == "\r":
                self.cur = 0
            elif ch == "\b":
                self.cur = max(0, self.cur - 1)
            elif ch == "\x07" or ord(ch) < 32:
                pass
            else:
                if self.cur < len(self.buf):
                    self.buf[self.cur] = ch
                else:
                    self.buf.append(ch)
                self.cur += 1
            i += 1
        if len(self.buf) > 4000:
            self.buf, self.cur = self.buf[-2000:], min(self.cur, 2000)

    def line(self) -> str:
        return "".join(self.buf)

    def mark_prompt(self):
        """Chamado na primeira tecla depois de um Enter: o que está na linha agora é o prompt."""
        self.prompt = self.line()

    def command(self) -> str:
        ln = self.line()
        if self.prompt and ln.startswith(self.prompt):
            return ln[len(self.prompt):].strip()
        return strip_prompt(ln)


_PROMPT = re.compile(r"^\s*(?:\[[^\]]{1,80}\]\s*>?|<[^>]{1,80}>|[\w.@:~/()\\-]{1,120}[#>$%])\s?")


def strip_prompt(line: str) -> str:
    m = _PROMPT.match(line or "")
    return (line[m.end():] if m else line or "").strip()


def at_password(line: str) -> bool:
    return bool(re.search(r"(?i)(pass(word|phrase)?|senha|secret)\s*:?\s*$", line or ""))
