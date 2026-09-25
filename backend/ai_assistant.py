"""Assistente de operações via Telegram usando o Claude (API da Anthropic).

Fluxo:
  Telegram (long polling) -> usuário autorizado -> loop de ferramentas do Claude ->
  - leitura (show/display/ping...) roda direto no equipamento
  - qualquer alteração vira uma PROPOSTA com botões Confirmar/Cancelar; só executa após o clique
Tudo que roda nos equipamentos fica em ai_audit (e em sessions, kind="ia").
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Awaitable, Callable, List, Optional

import httpx

import vault

logger = logging.getLogger("bastion.ai")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODELS = [
    ["claude-sonnet-5", "Claude Sonnet 5 (recomendado)"],
    ["claude-opus-5-5", "Claude Opus 5.5 (mais capaz, mais caro)"],
    ["claude-haiku-4-5-20251001", "Claude Haiku 4.5 (mais barato)"],
]
AI_DEFAULTS = {
    "ai_enabled": False,
    "anthropic_api_key": "",
    "ai_model": "claude-sonnet-5",
    "ai_allow_changes": True,
    "ai_users": [],  # [{telegram_id, user_id, label}]
}

MAX_STEPS = 16              # chamadas ao Claude por mensagem do usuário
MAX_TOOL_OUTPUT = 15000     # caracteres devolvidos ao Claude por ferramenta
PENDING_TTL_MIN = 15        # validade de uma proposta de alteração
CONV_TTL_HOURS = 3          # conversa "esquece" depois de 3h parada
CONV_MAX_MESSAGES = 40
TG_LIMIT = 3900

DEVICE_TYPE_NAMES = {
    "linux": "Linux", "mikrotik": "Mikrotik RouterOS", "cisco": "Cisco IOS/NX-OS", "huawei": "Huawei VRP",
    "juniper": "Juniper Junos", "ubiquiti": "Ubiquiti", "datacom": "Datacom DmOS", "zte": "ZTE (OLT/Switch)",
    "other": "Outro",
}


# ---------------------------------------------------------------------------
# Classificação leitura x alteração
# ---------------------------------------------------------------------------
_READ_RE = re.compile(
    r"^(sh|sho|show|dis|disp|displ|displa|display|ping|traceroute|tracert|trace)\b", re.I)
# pipes que gravam arquivo / redirecionam
_PIPE_WRITE_RE = re.compile(r"^(redirect|tee|append|save|file|out|copy)\b", re.I)
_MIKROTIK_READ_RE = re.compile(r"^/(?:[\w-]+ )*?(?:[\w-]+/)*[\w-]*\s*(print|export|monitor)\b|^/(ping|tool (traceroute|ping))\b", re.I)
_MIKROTIK_WRITE = {"set", "add", "remove", "disable", "enable", "reset", "reboot", "shutdown", "unset", "move",
                   "import", "run", "reset-configuration", "upgrade", "downgrade", "edit", "comment"}
_LINUX_META_RE = re.compile(r"[;&|`$<>\\\n]")
_LINUX_OK = {"uptime", "df", "free", "ss", "netstat", "ping", "traceroute", "mtr", "hostname", "uname", "ip",
             "lsblk", "w", "who", "date", "arp", "route"}
_IP_WRITE_WORDS = {"set", "add", "del", "delete", "flush", "change", "replace", "append", "save", "restore"}


def is_read_only(cmd: str, device_type: str) -> bool:
    c = (cmd or "").strip()
    if not c or "\n" in c or "\r" in c:
        return False
    dt = device_type or "linux"
    if dt == "mikrotik":
        tokens = {t.lower() for t in re.split(r"[\s/]+", c) if t}
        return bool(_MIKROTIK_READ_RE.match(c)) and "file=" not in c.lower() and not (tokens & _MIKROTIK_WRITE)
    if dt in ("linux", "ubiquiti"):
        if _LINUX_META_RE.search(c):
            return False
        words = c.split()
        if words[0] == "systemctl":
            return len(words) >= 2 and words[1] in ("status", "is-active", "list-units")
        if words[0] not in _LINUX_OK:
            return False
        if words[0] in ("ip", "route", "arp") and _IP_WRITE_WORDS & {w.lower() for w in words[1:]}:
            return False
        return True
    # equipamentos de rede (Huawei, Juniper, Cisco, Datacom, ZTE, outros)
    first, *pipes = c.split("|")
    if not _READ_RE.match(first.strip()):
        return False
    for p in pipes:
        if _PIPE_WRITE_RE.match(p.strip()):
            return False
    return True


def _filter_output(text: str, pattern: Optional[str]) -> str:
    if not pattern:
        return text
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    lines = [ln for ln in text.splitlines() if rx.search(ln)]
    return "\n".join(lines) if lines else f"(nenhuma linha casou com o filtro '{pattern}')"


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.8), int(limit * 0.2)
    cut = len(text) - head - tail
    return f"{text[:head]}\n\n[... {cut} caracteres omitidos — use 'filter' para ver a parte que interessa ...]\n\n{text[-tail:]}"


# ---------------------------------------------------------------------------
# Prompt e ferramentas
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """Você é o assistente de operações de rede do Bastion. Conversa pelo Telegram com engenheiros de rede de um provedor de internet e acessa os equipamentos cadastrados no Bastion (Huawei, Juniper, Cisco, Datacom DmOS, ZTE OLT/switch, Mikrotik, Linux) através das ferramentas.

Regras obrigatórias:
1. Nunca invente saídas, nomes de interface, IDs de ONU, estados ou valores. Consulte o equipamento com run_show_commands antes de afirmar algo ou propor qualquer alteração.
2. Para achar equipamentos use list_devices (busca em nome, IP, descrição e tags — cidade/POP costuma estar no nome ou nas tags). Se houver mais de um candidato plausível, pergunte ao usuário qual, listando as opções.
3. run_show_commands só aceita comandos de leitura (show/display/ping/traceroute; Mikrotik "/... print"). Use o parâmetro filter (regex) para reduzir saídas grandes, e rode vários equipamentos em paralelo quando fizer sentido.
4. Toda alteração (configuração, shutdown/undo shutdown, apagar ONU, BGP, clear/reset, save/commit, reboot) vai SOMENTE por propose_config_change, que mostra os comandos exatos ao usuário com botões Confirmar/Cancelar. Nunca diga que alterou algo antes de receber o resultado da execução. Confirmação digitada em texto ("sim", "pode") NÃO executa nada: o usuário precisa tocar no botão.
5. Em propose_config_change escreva a sequência COMPLETA exatamente como digitada no CLI, com entrada e saída do modo de configuração e commit quando o fabricante exigir. Referência: Huawei VRP "system-view" ... "quit"/"return" (e "commit" em equipamentos two-stage, ex.: NE com commit ativo); Juniper "configure" ... "commit and-quit"; Cisco IOS "configure terminal" ... "end"; Datacom DmOS "config" ... "commit" ... "end"; ZTE "configure terminal" ... "exit". Se o CLI pedir confirmação [y/n] ou [Y/N], inclua a resposta como a linha seguinte. Não inclua salvar configuração (save / write memory / copy running-config startup-config) a menos que o usuário peça — pergunte ao final se deseja salvar. Agrupe todas as mudanças de um mesmo pedido em uma única proposta.
6. No summary da proposta explique em uma ou duas linhas o que muda e o impacto (ex.: "derruba a sessão BGP com AS X"). Para mudanças de risco (BGP, uplinks, muitas ONUs) preencha rollback.
7. Depois que o usuário confirmar e você receber a saída da execução, confira se houve erro na saída e verifique o resultado com comandos de leitura.
8. Responda em português do Brasil, curto e direto, em texto simples (sem Markdown, sem tabelas largas): o usuário lê no celular. Mostre só o essencial das saídas.
9. Se um comando falhar por sintaxe (versões de firmware variam), tente a variação equivalente antes de desistir.

Dicas por fabricante (confirme a sintaxe da versão do equipamento):
- Huawei VRP: display interface brief | display interface X | display bgp peer | display bgp routing-table peer X advertised-routes | display transceiver interface X verbose (níveis ópticos) | display current-configuration configuration bgp. MAC no formato xxxx-xxxx-xxxx.
- Juniper: show interfaces terse | show bgp summary | show bgp neighbor X | show route advertising-protocol bgp X | show interfaces diagnostics optics X | show configuration protocols bgp | display set. Desativar interface: set interfaces X disable (reativar: delete interfaces X disable).
- Cisco IOS/XE: show ip interface brief | show ip bgp summary | show interfaces transceiver | show running-config | section router bgp.
- Datacom DmOS: show interface link | show running-config router bgp | show bgp summary (varia por versão).
- ZTE OLT (C300/C320/C600/C650): show gpon onu state gpon-olt_R/S/P | show gpon onu detail-info gpon-onu_R/S/P:ID | show pon power attenuation gpon-onu_R/S/P:ID | show pon power onu-rx gpon-olt_R/S/P | show mac ... (MAC no formato xxxx.xxxx.xxxx; nos C6xx a interface pode ser gpon_olt-R/S/P e gpon_onu-R/S/P:ID). Remover ONU: configure terminal / interface gpon-olt_R/S/P / no onu ID / exit.
- Mikrotik: /interface print | /routing bgp session print (v7) ou /routing bgp peer print (v6) | /interface ethernet monitor X once.
"""

TOOLS = [
    {
        "name": "list_devices",
        "description": "Lista/busca equipamentos cadastrados no Bastion do usuário. Retorna nome, host, tipo, tags, status e descrição.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Texto buscado em nome, IP, descrição e tags (vazio = todos). Várias palavras = todas devem aparecer."},
                "device_type": {"type": "string", "enum": list(DEVICE_TYPE_NAMES.keys()), "description": "Filtra por tipo."},
                "limit": {"type": "integer", "description": "Máximo de resultados (padrão 50, máx. 300)."},
            },
        },
    },
    {
        "name": "run_show_commands",
        "description": "Executa comandos SOMENTE DE LEITURA em um equipamento e devolve a saída. Comandos de alteração são recusados — use propose_config_change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Nome exato (ou id) do equipamento, como retornado por list_devices."},
                "commands": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10,
                             "description": "Comandos, um por item, executados em sequência na mesma sessão."},
                "filter": {"type": "string", "description": "Regex opcional (case-insensitive): devolve só as linhas que casam. Útil para saídas grandes."},
                "timeout": {"type": "integer", "description": "Timeout em segundos por comando (padrão 60, máx. 240)."},
            },
            "required": ["device", "commands"],
        },
    },
    {
        "name": "get_config_backup",
        "description": "Lê o último backup de configuração salvo pelo Bastion (sem acessar o equipamento). Bom para analisar configuração de BGP, interfaces etc. Pode estar desatualizado — a data vem no resultado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Nome exato (ou id) do equipamento."},
                "filter": {"type": "string", "description": "Regex opcional para devolver só as linhas que casam."},
            },
            "required": ["device"],
        },
    },
    {
        "name": "propose_config_change",
        "description": "Propõe uma alteração em um ou mais equipamentos. NÃO executa: envia os comandos ao usuário com botões Confirmar/Cancelar. A execução só acontece após o clique, e o resultado volta para você na conversa.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "O que muda e o impacto, em 1-2 linhas."},
                "risk": {"type": "string", "enum": ["baixo", "medio", "alto"]},
                "changes": {
                    "type": "array", "minItems": 1, "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "device": {"type": "string"},
                            "commands": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200},
                        },
                        "required": ["device", "commands"],
                    },
                },
                "rollback": {"type": "string", "description": "Comandos/procedimento para desfazer (opcional, recomendado em risco médio/alto)."},
            },
            "required": ["summary", "risk", "changes"],
        },
        "cache_control": {"type": "ephemeral"},
    },
]

HELP_TEXT = (
    "Sou o assistente do Bastion. Exemplos:\n"
    "• verifica o nível de sinal da porta 100GE0/0/1 da BORDA Huawei Cachoeiro\n"
    "• mostra as sessões BGP da borda X e o que estamos anunciando para o peer Y\n"
    "• na OLT ZTE de Guaçuí, em quais PONs está a ONU com MAC aa:bb:cc:dd:ee:ff? remove de todas\n"
    "• desativa a interface GE0/0/5 do switch S5732-Caxixe\n\n"
    "Leitura roda direto. Qualquer alteração eu mostro os comandos e só executo depois do botão ✅.\n\n"
    "/novo — começa uma conversa nova\n/id — mostra seu ID do Telegram\n/ajuda — esta mensagem"
)


class AIError(Exception):
    pass


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
async def get_ai_settings(db) -> dict:
    doc = await db.config.find_one({"key": "ai"}, {"_id": 0}) or {}
    return {**AI_DEFAULTS, **{k: v for k, v in doc.items() if k != "key"}}


def public_ai_settings(s: dict) -> dict:
    out = {k: v for k, v in s.items() if k != "anthropic_api_key"}
    out["has_api_key"] = bool(s.get("anthropic_api_key"))
    out["models"] = MODELS
    return out


async def call_claude(api_key: str, model: str, messages: list, system: list, tools: Optional[list] = None,
                      max_tokens: int = 4096, http: Optional[httpx.AsyncClient] = None) -> dict:
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if tools:
        body["tools"] = tools
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
    own = http is None
    http = http or httpx.AsyncClient(timeout=180)
    try:
        for attempt in range(3):
            r = await http.post(ANTHROPIC_URL, json=body, headers=headers, timeout=180)
            if r.status_code in (429, 500, 502, 503, 529) and attempt < 2:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            if r.status_code != 200:
                try:
                    msg = r.json().get("error", {}).get("message") or r.text
                except Exception:
                    msg = r.text
                raise AIError(f"API do Claude HTTP {r.status_code}: {msg[:300]}")
            return r.json()
    finally:
        if own:
            await http.aclose()
    raise AIError("API do Claude indisponível")


def _clean_assistant_content(content: list) -> list:
    out = []
    for b in content or []:
        if b.get("type") == "text" and b.get("text"):
            out.append({"type": "text", "text": b["text"]})
        elif b.get("type") == "tool_use":
            out.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b.get("input") or {}})
    return out


def _has_tool_result(msg: dict) -> bool:
    c = msg.get("content")
    return isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)


def _append_user(messages: list, text: str):
    """Acrescenta texto do usuário, mesclando com a última mensagem se ela também for do usuário."""
    if messages and messages[-1]["role"] == "user":
        last = messages[-1]
        content = last["content"] if isinstance(last["content"], list) else [{"type": "text", "text": last["content"]}]
        last["content"] = content + [{"type": "text", "text": text}]
    else:
        messages.append({"role": "user", "content": [{"type": "text", "text": text}]})


def compact_history(messages: list) -> list:
    msgs = list(messages)
    # encurta resultados de ferramentas antigos
    for m in msgs[:-8]:
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if b.get("type") == "tool_result" and isinstance(b.get("content"), str) and len(b["content"]) > 1500:
                    b["content"] = b["content"][:1500] + "\n[... saída antiga resumida ...]"
    while len(msgs) > CONV_MAX_MESSAGES:
        msgs.pop(0)
    # a conversa precisa começar com mensagem do usuário que não seja tool_result
    while msgs and (msgs[0]["role"] != "user" or _has_tool_result(msgs[0])):
        msgs.pop(0)
    return msgs


# ---------------------------------------------------------------------------
# Assistente
# ---------------------------------------------------------------------------
class TelegramAssistant:
    def __init__(self, db, connect_device: Callable[[dict], Awaitable], get_telegram_token: Callable[[], Awaitable[str]]):
        self.db = db
        self.connect_device = connect_device
        self.get_telegram_token = get_telegram_token
        self._task: Optional[asyncio.Task] = None
        self._token = ""
        self.http: Optional[httpx.AsyncClient] = None
        self._locks: dict = {}
        self._sem = asyncio.Semaphore(6)
        self.status = "parado"

    # ---------- ciclo de vida ----------
    def start(self):
        if not self._task:
            self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        await asyncio.sleep(5)
        offset = None
        async with httpx.AsyncClient(timeout=70) as http:
            self.http = http
            while True:
                try:
                    s = await get_ai_settings(self.db)
                    token = await self.get_telegram_token()
                    if not (s["ai_enabled"] and token and s.get("anthropic_api_key")):
                        self.status = "desativado" if not s["ai_enabled"] else "falta configurar token do Telegram ou chave da API"
                        await asyncio.sleep(15)
                        continue
                    if token != self._token:
                        self._token = token
                        offset = None
                        # long polling não funciona se o bot tiver webhook configurado
                        await self._tg("deleteWebhook", drop_pending_updates=False)
                    params = {"timeout": 50, "allowed_updates": json.dumps(["message", "callback_query"])}
                    if offset is not None:
                        params["offset"] = offset
                    r = await http.get(f"https://api.telegram.org/bot{self._token}/getUpdates", params=params, timeout=65)
                    data = r.json()
                    if not data.get("ok"):
                        self.status = f"erro Telegram: {data.get('description', r.status_code)}"
                        logger.warning(self.status)
                        await asyncio.sleep(10)
                        continue
                    self.status = "ativo"
                    for upd in data.get("result", []):
                        offset = upd["update_id"] + 1
                        asyncio.create_task(self._safe_handle(upd))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.status = f"erro: {e.__class__.__name__}"
                    logger.warning(f"telegram polling: {e}")
                    await asyncio.sleep(5)

    # ---------- Telegram helpers ----------
    async def _tg(self, method: str, **payload) -> dict:
        if not self._token or not self.http:
            return {}
        try:
            r = await self.http.post(f"https://api.telegram.org/bot{self._token}/{method}", json=payload, timeout=30)
            return r.json()
        except Exception as e:
            logger.warning(f"telegram {method}: {e}")
            return {}

    async def send(self, chat_id, text: str, **extra) -> dict:
        text = text.strip() or "(vazio)"
        chunks = [text[i:i + TG_LIMIT] for i in range(0, len(text), TG_LIMIT)]
        res = {}
        for i, ch in enumerate(chunks):
            kw = extra if i == len(chunks) - 1 else {}
            res = await self._tg("sendMessage", chat_id=chat_id, text=ch, disable_web_page_preview=True, **kw)
        return res

    async def _typing(self, chat_id, stop: asyncio.Event):
        while not stop.is_set():
            await self._tg("sendChatAction", chat_id=chat_id, action="typing")
            try:
                await asyncio.wait_for(stop.wait(), 4.5)
            except asyncio.TimeoutError:
                pass

    # ---------- roteamento ----------
    async def _safe_handle(self, upd: dict):
        try:
            if "callback_query" in upd:
                await self._on_callback(upd["callback_query"])
            elif "message" in upd:
                await self._on_message(upd["message"])
        except Exception as e:
            logger.exception(f"falha ao tratar update: {e}")

    def _auth(self, s: dict, telegram_id) -> Optional[dict]:
        for u in s.get("ai_users") or []:
            if str(u.get("telegram_id", "")).strip() == str(telegram_id) and u.get("user_id"):
                return u
        return None

    async def _on_message(self, msg: dict):
        text = (msg.get("text") or "").strip()
        chat_id = msg["chat"]["id"]
        from_id = (msg.get("from") or {}).get("id")
        if not text or from_id is None:
            return
        s = await get_ai_settings(self.db)
        cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if cmd == "/id":
            return await self.send(chat_id, f"Seu ID do Telegram: {from_id}")
        link = self._auth(s, from_id)
        if not link:
            return await self.send(chat_id, f"Acesso não autorizado.\nSeu ID do Telegram é {from_id} — peça ao administrador do Bastion para liberar em Automação → Assistente IA.")
        user = await self.db.users.find_one({"id": link["user_id"]}, {"_id": 0, "password_hash": 0})
        if not user:
            return await self.send(chat_id, "Seu ID do Telegram está vinculado a um usuário do Bastion que não existe mais. Fale com o administrador.")
        if cmd in ("/start", "/ajuda", "/help"):
            return await self.send(chat_id, HELP_TEXT)
        if cmd in ("/novo", "/reset", "/new"):
            await self.db.ai_conversations.delete_one({"chat_id": str(chat_id)})
            return await self.send(chat_id, "Conversa reiniciada.")
        lock = self._locks.setdefault(str(chat_id), asyncio.Lock())
        if lock.locked():
            await self.send(chat_id, "Ainda estou trabalhando no pedido anterior — sua mensagem entra na sequência.")
        async with lock:
            messages = await self._load_conv(chat_id)
            _append_user(messages, text)
            await self._run_agent(chat_id, from_id, user, s, messages)

    # ---------- conversa ----------
    async def _load_conv(self, chat_id) -> list:
        doc = await self.db.ai_conversations.find_one({"chat_id": str(chat_id)}, {"_id": 0})
        if not doc:
            return []
        try:
            upd = datetime.fromisoformat(doc.get("updated_at"))
            if datetime.now(timezone.utc) - upd > timedelta(hours=CONV_TTL_HOURS):
                return []
        except Exception:
            return []
        return doc.get("messages") or []

    async def _save_conv(self, chat_id, messages: list):
        await self.db.ai_conversations.update_one(
            {"chat_id": str(chat_id)},
            {"$set": {"messages": compact_history(messages), "updated_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True)

    def _system(self, user: dict, s: dict) -> list:
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        ctx = (f"Data/hora do servidor: {now}. Usuário do Bastion: {user.get('name') or user.get('email')}. "
               + ("Alterações permitidas (sempre via propose_config_change)." if s.get("ai_allow_changes")
                  else "ALTERAÇÕES DESATIVADAS pelo administrador: apenas consultas. Se pedirem mudança, explique e mostre os comandos como sugestão em texto, sem propor."))
        return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": ctx}]

    async def _run_agent(self, chat_id, telegram_id, user: dict, s: dict, messages: list):
        snapshot = json.loads(json.dumps(messages))
        api_key = vault.decrypt(s.get("anthropic_api_key", ""))
        stop = asyncio.Event()
        typing = asyncio.create_task(self._typing(chat_id, stop))
        ctx = {"chat_id": chat_id, "telegram_id": telegram_id, "user": user, "settings": s}
        try:
            for _ in range(MAX_STEPS):
                resp = await call_claude(api_key, s.get("ai_model") or AI_DEFAULTS["ai_model"], messages,
                                         self._system(user, s), TOOLS, http=self.http)
                await self._track_usage(resp.get("usage") or {})
                content = _clean_assistant_content(resp.get("content"))
                if not content:
                    content = [{"type": "text", "text": "(sem resposta)"}]
                messages.append({"role": "assistant", "content": content})
                text = "\n".join(b["text"] for b in content if b["type"] == "text").strip()
                tool_uses = [b for b in content if b["type"] == "tool_use"]
                if resp.get("stop_reason") != "tool_use" or not tool_uses:
                    await self.send(chat_id, text or "Pronto.")
                    break
                if text:
                    await self.send(chat_id, text)  # "vou verificar..." — feedback enquanto trabalha
                results = await asyncio.gather(*[self._exec_tool(tu, ctx) for tu in tool_uses])
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tu["id"], "content": out, **({"is_error": True} if err else {})}
                    for tu, (out, err) in zip(tool_uses, results)]})
            else:
                await self.send(chat_id, "Parei aqui: atingi o limite de passos para um único pedido. Me diga como seguir.")
                _append_user(messages, "[Sistema] Limite de passos atingido; aguarde nova instrução do usuário.")
                messages.append({"role": "assistant", "content": [{"type": "text", "text": "Ok, aguardando."}]})
            await self._save_conv(chat_id, messages)
        except AIError as e:
            await self.send(chat_id, f"⚠️ {e}")
            await self._save_conv(chat_id, snapshot)  # termina com a msg do usuário: a próxima é mesclada nela
        except Exception as e:
            logger.exception("falha no agente")
            await self.send(chat_id, f"⚠️ Erro interno: {e}")
            await self._save_conv(chat_id, snapshot)
        finally:
            stop.set()
            typing.cancel()

    async def _track_usage(self, usage: dict):
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        inc = {
            "requests": 1,
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
            "cache_write_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        }
        await self.db.ai_usage.update_one({"month": month}, {"$inc": inc}, upsert=True)

    # ---------- ferramentas ----------
    async def _find_device(self, user: dict, ref: str):
        ref = (ref or "").strip()
        scope = {"owner_id": user["id"]}
        d = await self.db.devices.find_one({**scope, "id": ref}, {"_id": 0})
        if d:
            return d, None
        devs = await self.db.devices.find(scope, {"_id": 0}).to_list(10000)
        exact = [x for x in devs if x["name"].strip().lower() == ref.lower()]
        if len(exact) == 1:
            return exact[0], None
        part = [x for x in devs if ref.lower() in x["name"].lower() or ref == x.get("host")]
        if len(part) == 1:
            return part[0], None
        if not part:
            return None, f"Nenhum equipamento chamado '{ref}'. Use list_devices para buscar."
        names = ", ".join(x["name"] for x in part[:15])
        return None, f"'{ref}' é ambíguo ({len(part)} equipamentos): {names}. Use o nome exato."

    async def _exec_tool(self, tu: dict, ctx: dict):
        name, inp = tu["name"], tu.get("input") or {}
        try:
            if name == "list_devices":
                return await self._tool_list(ctx["user"], inp), False
            if name == "run_show_commands":
                return await self._tool_show(ctx, inp)
            if name == "get_config_backup":
                return await self._tool_backup(ctx["user"], inp)
            if name == "propose_config_change":
                return await self._tool_propose(ctx, inp)
            return f"Ferramenta desconhecida: {name}", True
        except Exception as e:
            logger.exception(f"tool {name}")
            return f"Erro ao executar {name}: {e}", True

    async def _tool_list(self, user: dict, inp: dict) -> str:
        q = {"owner_id": user["id"]}
        if inp.get("device_type"):
            q["device_type"] = inp["device_type"]
        devs = await self.db.devices.find(q, {"_id": 0, "password": 0}).sort("name", 1).to_list(10000)
        words = [w.lower() for w in (inp.get("query") or "").split() if w.strip()]
        if words:
            def hay(d):
                return " ".join([d.get("name", ""), d.get("host", ""), d.get("description", ""), " ".join(d.get("tags") or [])]).lower()
            devs = [d for d in devs if all(w in hay(d) for w in words)]
        limit = max(1, min(int(inp.get("limit") or 50), 300))
        agents = {a["id"]: a["name"] for a in await self.db.agents.find({"owner_id": user["id"]}, {"_id": 0, "id": 1, "name": 1}).to_list(1000)}
        lines = [f"{len(devs)} equipamento(s) encontrado(s)" + (f", mostrando {limit}" if len(devs) > limit else "") + ":"]
        for d in devs[:limit]:
            parts = [d["name"], f"{d['host']}:{d.get('port', 22)}/{d.get('protocol') or 'ssh'}",
                     DEVICE_TYPE_NAMES.get(d.get("device_type") or "linux", d.get("device_type")),
                     f"status={d.get('status') or '?'}"]
            if d.get("tags"):
                parts.append("tags=" + ",".join(d["tags"]))
            if d.get("agent_id"):
                parts.append("via " + agents.get(d["agent_id"], "agente"))
            if d.get("description"):
                parts.append(d["description"][:80])
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    async def _run_on_device(self, dev: dict, commands: List[str], timeout: int, idle: float) -> dict:
        async with self._sem:
            client = await self.connect_device(dev)
            try:
                res = await client.run_command("\n".join(commands), timeout=timeout, idle=idle)
            finally:
                await client.close()
        out = res.get("stdout") or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        err = res.get("stderr") or ""
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return {"ok": bool(res.get("ok")), "output": (out + ("\n[stderr]\n" + err if err.strip() else "")).strip()}

    async def _audit(self, ctx: dict, dev: dict, kind: str, commands: List[str], ok: bool, output: str,
                     proposal_id: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        user = ctx["user"]
        await self.db.ai_audit.insert_one({
            "id": os.urandom(8).hex(), "at": now, "user_id": user["id"], "user_email": user.get("email"),
            "telegram_id": str(ctx.get("telegram_id")), "device_id": dev["id"], "device_name": dev["name"],
            "kind": kind, "commands": commands, "ok": ok, "output": output[:4000], "proposal_id": proposal_id,
        })
        await self.db.sessions.insert_one({
            "id": os.urandom(8).hex(), "user_id": user["id"], "user_email": user.get("email"),
            "device_id": dev["id"], "device_name": dev["name"], "started_at": now, "ended_at": now,
            "duration_seconds": 0, "kind": "ia" if kind == "read" else "ia-alteracao",
        })

    async def _tool_show(self, ctx: dict, inp: dict):
        dev, err = await self._find_device(ctx["user"], inp.get("device", ""))
        if err:
            return err, True
        commands = [c.strip() for c in (inp.get("commands") or []) if str(c).strip()]
        if not commands:
            return "Nenhum comando informado.", True
        dtype = dev.get("device_type") or "linux"
        blocked = [c for c in commands if not is_read_only(c, dtype)]
        if blocked:
            return ("Recusado — não são comandos de leitura: " + " ; ".join(blocked) +
                    ". Para alterações use propose_config_change."), True
        timeout = max(10, min(int(inp.get("timeout") or 60), 240))
        try:
            r = await self._run_on_device(dev, commands, timeout, idle=4.0)
        except Exception as e:
            await self._audit(ctx, dev, "read", commands, False, str(e))
            return f"Falha ao conectar/executar em {dev['name']}: {e}", True
        await self._audit(ctx, dev, "read", commands, r["ok"], r["output"])
        out = _truncate(_filter_output(r["output"], inp.get("filter")))
        return f"== {dev['name']} ({dev['host']}) ==\n{out or '(saída vazia)'}", False

    async def _tool_backup(self, user: dict, inp: dict):
        dev, err = await self._find_device(user, inp.get("device", ""))
        if err:
            return err, True
        b = await self.db.backups.find_one({"device_id": dev["id"], "ok": True}, {"_id": 0}, sort=[("created_at", -1)])
        if not b:
            return f"Não há backup salvo de {dev['name']}. Use run_show_commands para ler a configuração.", True
        when = b.get("created_at", "")[:16].replace("T", " ")
        out = _truncate(_filter_output(b.get("content", ""), inp.get("filter")))
        return f"== Backup de {dev['name']} em {when} UTC ==\n{out}", False

    async def _tool_propose(self, ctx: dict, inp: dict):
        s = ctx["settings"]
        if not s.get("ai_allow_changes"):
            return "Alterações estão desativadas pelo administrador. Mostre os comandos ao usuário apenas como sugestão.", True
        changes = []
        for ch in inp.get("changes") or []:
            dev, err = await self._find_device(ctx["user"], ch.get("device", ""))
            if err:
                return err, True
            cmds = [str(c).rstrip() for c in ch.get("commands") or [] if str(c).strip()]
            if not cmds:
                return f"Sem comandos para {dev['name']}.", True
            changes.append({"device_id": dev["id"], "device_name": dev["name"], "host": dev["host"], "commands": cmds})
        if not changes:
            return "Nenhuma alteração informada.", True
        pid = os.urandom(4).hex()
        risk = inp.get("risk") or "medio"
        icon = {"baixo": "🟢", "medio": "🟡", "alto": "🔴"}.get(risk, "🟡")
        lines = [f"{icon} Proposta de alteração #{pid} (risco {risk})", "", (inp.get("summary") or "").strip(), ""]
        for c in changes:
            lines.append(f"▶ {c['device_name']} ({c['host']})")
            lines.extend(f"  {x}" for x in c["commands"])
            lines.append("")
        if inp.get("rollback"):
            lines += ["Rollback:", str(inp["rollback"]).strip(), ""]
        lines.append(f"Válida por {PENDING_TTL_MIN} min. Nada foi executado ainda.")
        text = "\n".join(lines)
        if len(text) > TG_LIMIT:
            text = text[:TG_LIMIT - 60] + "\n[... lista longa cortada na mensagem, mas será executada completa ...]"
        kb = {"inline_keyboard": [[{"text": "✅ Confirmar", "callback_data": f"ok:{pid}"},
                                   {"text": "❌ Cancelar", "callback_data": f"no:{pid}"}]]}
        sent = await self.send(ctx["chat_id"], text, reply_markup=kb)
        now = datetime.now(timezone.utc)
        await self.db.ai_pending.insert_one({
            "id": pid, "chat_id": str(ctx["chat_id"]), "telegram_id": str(ctx["telegram_id"]),
            "user_id": ctx["user"]["id"], "changes": changes, "summary": inp.get("summary"), "risk": risk,
            "status": "pending", "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=PENDING_TTL_MIN)).isoformat(),
            "message_id": (sent.get("result") or {}).get("message_id"), "message_text": text,
        })
        return (f"Proposta #{pid} enviada ao usuário com botões Confirmar/Cancelar. NADA foi executado. "
                "Encerre sua resposta avisando que aguarda a confirmação pelo botão."), False

    async def _on_callback(self, cq: dict):
        data = cq.get("data") or ""
        from_id = str((cq.get("from") or {}).get("id"))
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        action, _, pid = data.partition(":")
        p = await self.db.ai_pending.find_one({"id": pid}, {"_id": 0})
        if not p:
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Proposta não encontrada.")
        if from_id != p["telegram_id"]:
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Só quem pediu a alteração pode confirmar.", show_alert=True)
        s = await get_ai_settings(self.db)
        link = self._auth(s, from_id)
        if not link or link["user_id"] != p["user_id"]:
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Acesso não autorizado.", show_alert=True)
        now = datetime.now(timezone.utc)
        if action == "no":
            r = await self.db.ai_pending.update_one({"id": pid, "status": "pending"}, {"$set": {"status": "cancelled", "decided_at": now.isoformat()}})
            await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Cancelado" if r.modified_count else "Já decidida")
            if r.modified_count:
                await self._edit_status(p, "❌ Cancelada. Nada foi executado.")
                async with self._locks.setdefault(str(chat_id), asyncio.Lock()):
                    msgs = await self._load_conv(chat_id)
                    _append_user(msgs, f"[Sistema] O usuário CANCELOU a proposta #{pid}. Nada foi executado.")
                    msgs.append({"role": "assistant", "content": [{"type": "text", "text": "Ok, proposta cancelada."}]})
                    await self._save_conv(chat_id, msgs)
            return
        if action != "ok":
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"])
        # confirmação atômica: só uma execução por proposta, e só se ainda estiver válida
        r = await self.db.ai_pending.update_one(
            {"id": pid, "status": "pending", "expires_at": {"$gt": now.isoformat()}},
            {"$set": {"status": "running", "decided_at": now.isoformat()}})
        if not r.modified_count:
            cur = await self.db.ai_pending.find_one({"id": pid}, {"_id": 0, "status": 1})
            why = "expirou — peça de novo" if cur and cur.get("status") == "pending" else f"já está '{cur.get('status') if cur else '?'}'"
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text=f"Proposta {why}.", show_alert=True)
        await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Executando…")
        await self._edit_status(p, "⏳ Confirmada — executando…")
        user = await self.db.users.find_one({"id": p["user_id"]}, {"_id": 0, "password_hash": 0})
        ctx = {"chat_id": chat_id, "telegram_id": from_id, "user": user, "settings": s}

        async def _one(ch):
            dev = await self.db.devices.find_one({"id": ch["device_id"], "owner_id": p["user_id"]}, {"_id": 0})
            if not dev:
                return ch, False, "Equipamento não encontrado (removido?)"
            try:
                res = await self._run_on_device(dev, ch["commands"], timeout=120, idle=4.0)
                await self._audit(ctx, dev, "change", ch["commands"], res["ok"], res["output"], pid)
                return ch, res["ok"], res["output"]
            except Exception as e:
                await self._audit(ctx, dev, "change", ch["commands"], False, str(e), pid)
                return ch, False, f"Falha ao conectar/executar: {e}"

        results = await asyncio.gather(*[_one(c) for c in p["changes"]])
        all_ok = all(ok for _, ok, _ in results)
        await self.db.ai_pending.update_one({"id": pid}, {"$set": {"status": "done" if all_ok else "failed",
                                                                   "finished_at": datetime.now(timezone.utc).isoformat()}})
        await self._edit_status(p, "✅ Executada." if all_ok else "⚠️ Executada com falhas.")
        report = []
        for ch, ok, out in results:
            report.append(f"== {ch['device_name']} ({'ok' if ok else 'FALHA'}) ==\n{_truncate(out, 6000)}")
        feedback = (f"[Sistema] O usuário CONFIRMOU a proposta #{pid} e os comandos foram executados. "
                    f"Saída da execução:\n\n" + "\n\n".join(report) +
                    "\n\nAnalise a saída procurando erros, verifique o resultado com comandos de leitura se fizer sentido "
                    "e informe o usuário em poucas linhas.")
        async with self._locks.setdefault(str(chat_id), asyncio.Lock()):
            msgs = await self._load_conv(chat_id)
            _append_user(msgs, feedback)
            await self._run_agent(chat_id, from_id, user, s, msgs)

    async def _edit_status(self, p: dict, status: str):
        if not p.get("message_id"):
            return await self.send(p["chat_id"], status)
        text = (p.get("message_text") or "").replace(f"Válida por {PENDING_TTL_MIN} min. Nada foi executado ainda.", "").rstrip()
        await self._tg("editMessageText", chat_id=p["chat_id"], message_id=p["message_id"],
                       text=f"{text}\n\n{status}"[:4096], disable_web_page_preview=True)
