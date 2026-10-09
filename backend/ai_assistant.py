"""Assistente de operações do BastiON: chat dentro do sistema e bot do Telegram.

Modelo: Claude (Anthropic) ou qualquer API compatível com OpenAI — Groq e Gemini (grátis para começar), Ollama local…
Fluxo: pergunta -> loop de ferramentas do modelo ->
  - leitura (SNMP, show/display, óptica, flow) roda direto
  - qualquer alteração vira uma PROPOSTA com botões Confirmar/Cancelar; só executa após o clique
Tudo que roda nos equipamentos fica em ai_audit (e em sessions, kind="ia").
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
import time
from typing import Awaitable, Callable, List, Optional, Tuple

import httpx

import configsearch
import llm
import snmp_service
import vault

logger = logging.getLogger("bastion.ai")

MODELS = [[m, m] for m in llm.PROVIDERS["anthropic"]["suggest"]]
AI_DEFAULTS = {
    "ai_enabled": False,          # bot do Telegram
    "ai_web_enabled": True,       # chat dentro do BastiON
    "anthropic_api_key": "",      # legado (hoje fica em ai_keys["anthropic"])
    "ai_model": "claude-sonnet-5",
    "ai_allow_changes": True,
    "ai_users": [],  # [{telegram_id, user_id, label}]
    "ai_keys": {}, "ai_models": {}, "ai_base_urls": {},
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
SYSTEM_PROMPT = """Você é o assistente de operações de rede do BastiON. Conversa com engenheiros de rede de um provedor de internet e acessa os equipamentos cadastrados no BastiON (Huawei, Juniper, Cisco, Datacom DmOS, ZTE OLT/switch, Mikrotik, Linux) através das ferramentas.

Regras obrigatórias:
1. Nunca invente saídas, nomes de interface, IDs de ONU, estados ou valores. Consulte antes de afirmar algo ou propor qualquer alteração.
2. Para achar equipamentos use list_devices (busca em nome, IP, descrição e tags — cidade/POP costuma estar no nome ou nas tags). Se houver mais de um candidato plausível, pergunte ao usuário qual, listando as opções.
3. Prefira as ferramentas prontas, que são rápidas e já vêm organizadas: get_interfaces (lista de interfaces com status, velocidade e tráfego atual, via SNMP), get_bgp_sessions (sessões BGP via SNMP), get_optical_signal (RX/TX por lane da porta) e get_flow_top (quem está consumindo nas interfaces com Flow). Se falharem (sem SNMP, dado ausente) ou precisar de detalhe, use run_show_commands.
4. run_show_commands só aceita comandos de leitura (show/display/ping/traceroute; Mikrotik "/... print"). Use o parâmetro filter (regex) para reduzir saídas grandes.
5. Toda alteração (configuração, shutdown/undo shutdown, desativar/ativar porta, apagar ONU, BGP, clear/reset, save/commit, reboot) vai SOMENTE por propose_config_change, que mostra os comandos exatos ao usuário com botões Confirmar/Cancelar. Nunca diga que alterou algo antes de receber o resultado da execução. Confirmação digitada em texto ("sim", "pode") NÃO executa nada: o usuário precisa clicar no botão.
6. Em propose_config_change escreva a sequência COMPLETA exatamente como digitada no CLI, com entrada e saída do modo de configuração e commit quando o fabricante exigir. Referência: Huawei VRP "system-view" / "interface X" / "shutdown" (reativar: "undo shutdown") / "quit" / "return" (e "commit" em equipamentos two-stage, ex.: NE com commit ativo); Juniper "configure" / "set interfaces X disable" (reativar: "delete interfaces X disable") / "commit and-quit"; Cisco IOS "configure terminal" / "interface X" / "shutdown" (reativar: "no shutdown") / "end"; Datacom DmOS "config" / "interface X" / "shutdown" / "commit" / "end"; ZTE "configure terminal" / "interface X" / "shutdown" / "exit" / "end". Se o CLI pedir confirmação [y/n] ou [Y/N], inclua a resposta como a linha seguinte. Não inclua salvar configuração (save / write memory) a menos que o usuário peça — pergunte ao final se deseja salvar. Agrupe as mudanças de um pedido em uma única proposta.
7. Antes de propor desativar uma porta, confira o nome exato e o estado atual dela (get_interfaces). No summary diga o impacto (ex.: "derruba o trânsito X — o tráfego vai para Y", "cliente Z fica sem link"). Para mudanças de risco (BGP, uplinks, muitas ONUs) preencha rollback.
8. Depois que o usuário confirmar e você receber a saída da execução, confira se houve erro e verifique o resultado com uma consulta.
9. Responda em português do Brasil, curto e direto. Mostre só o essencial das saídas.
10. Se um comando falhar por sintaxe (versões de firmware variam), tente a variação equivalente antes de desistir.

Dicas por fabricante (confirme a sintaxe da versão do equipamento):
- Huawei VRP: display interface brief | display interface X | display bgp peer | display bgp routing-table peer X advertised-routes | display transceiver interface X verbose | display current-configuration configuration bgp. MAC no formato xxxx-xxxx-xxxx.
- Juniper: show interfaces terse | show bgp summary | show bgp neighbor X | show route advertising-protocol bgp X | show interfaces diagnostics optics X | show configuration protocols bgp | display set.
- Cisco IOS/XE: show ip interface brief | show ip bgp summary | show interfaces transceiver | show running-config | section router bgp.
- Datacom DmOS: show interface link | show running-config router bgp | show bgp summary (varia por versão).
- ZTE OLT (C300/C320/C600/C650): show gpon onu state gpon-olt_R/S/P | show gpon onu detail-info gpon-onu_R/S/P:ID | show pon power attenuation gpon-onu_R/S/P:ID | show pon power onu-rx gpon-olt_R/S/P | show mac ... (MAC no formato xxxx.xxxx.xxxx; nos C6xx a interface pode ser gpon_olt-R/S/P e gpon_onu-R/S/P:ID). Remover ONU: configure terminal / interface gpon-olt_R/S/P / no onu ID / exit.
- Mikrotik: /interface print | /routing bgp session print (v7) ou /routing bgp peer print (v6) | /interface ethernet monitor X once. Desativar: /interface disable X.
"""
CHANNEL_PROMPT = {
    "telegram": "Canal: Telegram. Texto simples, sem Markdown e sem tabelas largas: o usuário lê no celular.",
    "web": ("Canal: chat dentro do BastiON (navegador). Pode usar listas curtas e blocos ``` para trechos de CLI; "
            "evite tabelas largas. As propostas aparecem como um cartão com os botões Confirmar/Cancelar."),
}

# conversa por voz: a resposta é lida em voz alta pelo navegador
VOICE_PROMPT = ("MODO VOZ: o usuário está FALANDO com você e a sua resposta será lida em voz alta. Responda como numa conversa: "
                "frases curtas e naturais em português do Brasil, no máximo 3 ou 4 frases, indo direto ao resultado. "
                "Não use Markdown, listas, tabelas, blocos de código, emojis nem símbolos; não dite saídas de comando. "
                "Fale números e unidades por extenso do jeito que se fala (\"dois vírgula um giga\", \"menos dezoito dBm\"), "
                "cite só os 2 ou 3 itens mais importantes e ofereça detalhar se ele quiser. "
                "O texto vem de reconhecimento de fala e chega com erros: interprete pelo SOM e pelo contexto de rede antes de agir. "
                "Enganos comuns: 'ponta/ponte/pom 3' = PON 3; 'ônus/onius/ônibus' = ONUs; 'o l t/daltz/oeltê' = OLT (ZTE, Huawei…); "
                "'porta gê' = GE; 'bras/brás' = BRAS; 'vê lan' = VLAN; 'ráuei' = Huawei; 'placa 2 ponta 3' = slot 2, PON 3. "
                "Nomes de equipamento e de cidade vêm escritos 'de ouvido' (uma cidade ou sigla pode sair como palavras soltas sem sentido): "
                "chame list_devices e escolha o cadastrado que soa mais parecido; se houver mais de um candidato, pergunte qual em uma frase. "
                "Ao responder, diga o nome real do equipamento que usou. "
                "Para alterações, proponha normalmente e diga que o cartão de confirmação está na tela.")

_DEV = {"type": "string", "description": "Nome exato (ou id) do equipamento, como retornado por list_devices."}
TOOLS = [
    {
        "name": "list_devices",
        "description": "Lista/busca equipamentos cadastrados no BastiON do usuário. Retorna nome, host, tipo, tags, status e descrição.",
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
        "name": "get_interfaces",
        "description": ("Interfaces de um equipamento via SNMP: nome, descrição, estado admin/oper, velocidade e o tráfego ATUAL "
                        "(entrada/saída medido agora em ~5 s). Use query para filtrar (ex.: '100GE', 'TRANSITO', '0/0/1')."),
        "input_schema": {
            "type": "object",
            "properties": {
                "device": _DEV,
                "query": {"type": "string", "description": "Palavras que devem aparecer no nome/descrição/índice (vazio = todas)."},
                "only_up": {"type": "boolean", "description": "Só interfaces operacionalmente UP."},
                "only_down": {"type": "boolean", "description": "Só interfaces DOWN (admin up e oper down)."},
                "with_traffic": {"type": "boolean", "description": "Medir tráfego atual (padrão true; até 40 interfaces)."},
            },
            "required": ["device"],
        },
    },
    {
        "name": "get_bgp_sessions",
        "description": ("Sessões BGP do equipamento via SNMP (BGP4-MIB, IPv4 da instância principal): peer, AS remoto, estado, há quanto "
                        "tempo está estabelecida, nº de quedas e último erro. Para IPv6/VPN, rotas recebidas/anunciadas use run_show_commands."),
        "input_schema": {
            "type": "object",
            "properties": {"device": _DEV,
                           "only_problems": {"type": "boolean", "description": "Só sessões que não estão established ou caíram há pouco."}},
            "required": ["device"],
        },
    },
    {
        "name": "get_optical_signal",
        "description": "Potência óptica RX/TX (dBm) de uma porta, por lane, lida na CLI do equipamento.",
        "input_schema": {
            "type": "object",
            "properties": {"device": _DEV, "interface": {"type": "string", "description": "Nome da interface (ex.: 100GE0/0/1, xe-0/0/1)."}},
            "required": ["device", "interface"],
        },
    },
    {
        "name": "get_flow_top",
        "description": ("Análise de Flow (NetFlow/sFlow) das interfaces monitoradas: quem está consumindo. Agrupa por interface, AS de "
                        "origem/destino, prefixo de destino, porta ou protocolo, na janela pedida. Também lista os ataques DDoS ativos."),
        "input_schema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string", "description": "Texto que casa com o nome/rótulo das interfaces monitoradas (vazio = todas)."},
                "group_by": {"type": "string", "enum": ["interface", "sas", "das", "dpfx", "spfx", "dport", "sport", "proto", "group"]},
                "direction": {"type": "string", "enum": ["in", "out"], "description": "in = entrando pela interface (padrão)."},
                "minutes": {"type": "integer", "description": "Janela em minutos (padrão 60)."},
            },
        },
    },
    {
        "name": "run_show_commands",
        "description": "Executa comandos SOMENTE DE LEITURA em um equipamento e devolve a saída. Comandos de alteração são recusados — use propose_config_change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device": _DEV,
                "commands": {"type": "array", "items": {"type": "string"}, "description": "Comandos (1 a 10), um por item, executados em sequência na mesma sessão."},
                "filter": {"type": "string", "description": "Regex opcional (case-insensitive): devolve só as linhas que casam. Útil para saídas grandes."},
                "timeout": {"type": "integer", "description": "Timeout em segundos por comando (padrão 60, máx. 240)."},
            },
            "required": ["device", "commands"],
        },
    },
    {
        "name": "get_config_backup",
        "description": "Lê o último backup de configuração salvo pelo BastiON (sem acessar o equipamento). Bom para analisar configuração de BGP, interfaces etc. Pode estar desatualizado — a data vem no resultado.",
        "input_schema": {
            "type": "object",
            "properties": {"device": _DEV, "filter": {"type": "string", "description": "Regex opcional para devolver só as linhas que casam."}},
            "required": ["device"],
        },
    },
    {
        "name": "search_configs",
        "description": ("Procura um texto em TODAS as configurações salvas (último backup de cada equipamento) e diz em qual "
                        "equipamento, linha e bloco aparece. Use para 'onde está a VLAN X', 'quem tem o peer Y', 'qual VSI/VRF "
                        "leva o cliente Z', 'onde o IP W está configurado'. Muito mais rápido que abrir equipamento por equipamento."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "O que procurar (ex.: 1302, 187.16.216.95, CLIENTE-ACME)."},
                "mode": {"type": "string", "enum": ["word", "text", "regex"],
                         "description": "word = palavra/número inteiro (padrão; 1302 não casa 13020); text = qualquer trecho; regex."},
                "device_type": {"type": "string", "description": "Opcional: só um fabricante (huawei, juniper, datacom, zte, cisco, mikrotik)."},
                "device": {"type": "string", "description": "Opcional: parte do nome do equipamento para limitar a busca."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "propose_config_change",
        "description": "Propõe uma alteração em um ou mais equipamentos (inclusive desativar/ativar porta). NÃO executa: mostra os comandos ao usuário com botões Confirmar/Cancelar. A execução só acontece após o clique, e o resultado volta para você na conversa.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "O que muda e o impacto, em 1-2 linhas."},
                "risk": {"type": "string", "enum": ["baixo", "medio", "alto"]},
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "device": {"type": "string"},
                            "commands": {"type": "array", "items": {"type": "string"}},
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
    "Sou o assistente do BastiON. Exemplos:\n"
    "• verifica o nível de sinal da porta 100GE0/0/1 da BORDA Huawei Cachoeiro\n"
    "• mostra as sessões BGP da borda X e o que estamos anunciando para o peer Y\n"
    "• quais interfaces estão down no switch S5732-Caxixe?\n"
    "• o que está consumindo o trânsito A agora?\n"
    "• desativa a interface GE0/0/5 do switch S5732-Caxixe\n\n"
    "Leitura roda direto. Qualquer alteração eu mostro os comandos e só executo depois do botão ✅.\n\n"
    "/roteiros — roteiros automatizados (ex.: ATIVAR ROTA LIMOEIRO); também dá para mandar só o nome do roteiro\n"
    "/novo — começa uma conversa nova\n/id — mostra seu ID do Telegram\n/ajuda — esta mensagem"
)


def _norm_name(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


class AIError(Exception):
    pass


async def get_ai_settings(db) -> dict:
    doc = await db.config.find_one({"key": "ai"}, {"_id": 0}) or {}
    s = {**AI_DEFAULTS, **{k: v for k, v in doc.items() if k != "key"}}
    if not doc.get("ai_provider"):          # instalação antiga com chave do Claude continua no Claude
        s["ai_provider"] = "anthropic" if doc.get("anthropic_api_key") else "groq"
    s["ai_keys"] = dict(s.get("ai_keys") or {})
    if s.get("anthropic_api_key") and not s["ai_keys"].get("anthropic"):
        s["ai_keys"]["anthropic"] = s["anthropic_api_key"]
    s["ai_models"] = dict(s.get("ai_models") or {})
    if doc.get("ai_model") and not s["ai_models"].get("anthropic"):
        s["ai_models"]["anthropic"] = doc["ai_model"]
    s["ai_base_urls"] = dict(s.get("ai_base_urls") or {})
    return s


def llm_cfg(s: dict, provider: Optional[str] = None) -> dict:
    p = provider or s.get("ai_provider") or "groq"
    info = llm.PROVIDERS.get(p, {})
    return {"provider": p, "model": (s.get("ai_models") or {}).get(p) or info.get("model", ""),
            "key": vault.decrypt((s.get("ai_keys") or {}).get(p, "")),
            "base_url": (s.get("ai_base_urls") or {}).get(p) or info.get("base_url", ""),
            # planos grátis: se o modelo escolhido estiver sobrecarregado/no limite, tenta os outros sugeridos
            "fallbacks": list(info.get("suggest") or [])[:3] if p in ("gemini", "groq") else []}


def ai_ready(s: dict) -> Optional[str]:
    """None = pronto; senão, o motivo."""
    c = llm_cfg(s)
    if llm.PROVIDERS.get(c["provider"], {}).get("key") and not c["key"]:
        return "falta a chave da API do provedor escolhido"
    if not c["model"]:
        return "falta escolher o modelo"
    return None


def public_ai_settings(s: dict) -> dict:
    out = {k: v for k, v in s.items() if k not in ("anthropic_api_key", "ai_keys")}
    out["has_keys"] = {p: bool(v) for p, v in (s.get("ai_keys") or {}).items()}
    out["has_api_key"] = bool((s.get("ai_keys") or {}).get(s.get("ai_provider")))
    out["providers"] = llm.PROVIDERS
    out["models"] = [[m, m] for m in llm.PROVIDERS["anthropic"]["suggest"]]
    out["current"] = {k: v for k, v in llm_cfg(s).items() if k != "key"}
    out["ready_error"] = ai_ready(s)
    return out


async def call_claude(api_key: str, model: str, messages: list, system: list, tools: Optional[list] = None,
                      max_tokens: int = 4096, http: Optional[httpx.AsyncClient] = None) -> dict:
    """Compatibilidade: chamada direta ao Claude."""
    try:
        return await llm.call({"provider": "anthropic", "model": model, "key": api_key}, messages, system, tools, max_tokens, http)
    except llm.LLMError as e:
        raise AIError(str(e))


def _clean_assistant_content(content: list) -> list:
    out = []
    for b in content or []:
        if b.get("type") == "text" and b.get("text"):
            out.append({"type": "text", "text": b["text"]})
        elif b.get("type") == "tool_use":
            tu = {"type": "tool_use", "id": b["id"], "name": b["name"], "input": b.get("input") or {}}
            if isinstance(b.get("extra"), dict) and b["extra"]:
                tu["extra"] = b["extra"]          # assinatura do Gemini 3 (volta junto na próxima chamada)
            out.append(tu)
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


def _fmt_bps(v) -> str:
    if v is None:
        return "?"
    for u, d in (("Gbps", 1e9), ("Mbps", 1e6), ("Kbps", 1e3)):
        if v >= d:
            return f"{v / d:.2f} {u}" if u == "Gbps" else f"{v / d:.1f} {u}"
    return f"{v:.0f} bps"


def _fmt_age(sec) -> str:
    if not isinstance(sec, int):
        return "?"
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    return f"{d}d{h}h" if d else f"{h}h{r // 60}min"


def describe_tool(name: str, inp: dict) -> str:
    """Linha curta mostrada no chat enquanto a ferramenta roda."""
    dev = inp.get("device") or ""
    if name == "list_devices":
        return f"buscando equipamentos {('“' + inp['query'] + '”') if inp.get('query') else ''}".strip()
    if name == "get_interfaces":
        return f"interfaces de {dev}" + (f" ({inp['query']})" if inp.get("query") else "") + " via SNMP"
    if name == "get_bgp_sessions":
        return f"sessões BGP de {dev} via SNMP"
    if name == "get_optical_signal":
        return f"sinal óptico de {inp.get('interface', '')} em {dev}"
    if name == "get_flow_top":
        return f"flow: {inp.get('group_by') or 'interface'} {('em ' + inp['interface']) if inp.get('interface') else ''}".strip()
    if name == "run_show_commands":
        cmds = inp.get("commands") or []
        return f"{dev}: " + " ; ".join(str(c) for c in cmds[:3]) + (" …" if len(cmds) > 3 else "")
    if name == "get_config_backup":
        return f"backup de configuração de {dev}"
    if name == "search_configs":
        return f"procurando “{inp.get('query', '')}” em todas as configs"
    if name == "propose_config_change":
        return "preparando proposta de alteração"
    return name


# ---------------------------------------------------------------------------
# Núcleo: ferramentas + loop (independente do canal)
# ---------------------------------------------------------------------------
class AgentCore:
    channel = "?"

    def __init__(self, db, connect_device: Callable[[dict], Awaitable], snmp_client: Optional[Callable] = None,
                 flow_query: Optional[Callable] = None):
        self.db = db
        self.connect_device = connect_device
        self.snmp_client = snmp_client
        self.flow_query = flow_query
        self.http: Optional[httpx.AsyncClient] = None
        self._locks: dict = {}
        self._sem = asyncio.Semaphore(6)

    # ----- ganchos do canal -----
    async def out_text(self, ctx: dict, text: str, final: bool):
        raise NotImplementedError

    async def out_activity(self, ctx: dict, name: str, inp: dict):
        pass

    async def out_wait(self, ctx: dict, text: str):
        pass

    async def out_proposal(self, ctx: dict, pid: str, text: str, data: dict) -> dict:
        raise NotImplementedError

    async def save_conv(self, ctx: dict, messages: list):
        raise NotImplementedError

    def _system(self, user: dict, s: dict, voice: bool = False) -> list:
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        ctx = (f"Data/hora do servidor: {now}. Usuário do BastiON: {user.get('name') or user.get('email')}. "
               + ("Alterações permitidas (sempre via propose_config_change)." if s.get("ai_allow_changes")
                  else "ALTERAÇÕES DESATIVADAS pelo administrador: apenas consultas. Se pedirem mudança, explique e mostre os comandos como sugestão em texto, sem propor."))
        return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": CHANNEL_PROMPT.get(self.channel, "") + " " + ctx + (" " + VOICE_PROMPT if voice else "")}]

    async def run_agent(self, ctx: dict, messages: list):
        s = ctx["settings"]
        snapshot = json.loads(json.dumps(messages))
        cfg = llm_cfg(s)
        ctx["out_limit"] = llm.tool_output_limit(cfg["provider"])
        try:
            for _ in range(MAX_STEPS):
                resp = await llm.call(cfg, messages, self._system(ctx["user"], s, bool(ctx.get("voice"))), TOOLS, http=self.http,
                                      on_wait=lambda txt: self.out_wait(ctx, txt))
                await self._track_usage(resp.get("usage") or {}, cfg["provider"])
                content = _clean_assistant_content(resp.get("content"))
                if not content:
                    content = [{"type": "text", "text": "(sem resposta)"}]
                messages.append({"role": "assistant", "content": content})
                text = "\n".join(b["text"] for b in content if b["type"] == "text").strip()
                tool_uses = [b for b in content if b["type"] == "tool_use"]
                if resp.get("stop_reason") != "tool_use" or not tool_uses:
                    await self.out_text(ctx, text or "Pronto.", True)
                    break
                if text:
                    await self.out_text(ctx, text, False)  # "vou verificar..." — retorno enquanto trabalha
                for tu in tool_uses:
                    await self.out_activity(ctx, tu["name"], tu.get("input") or {})
                results = await asyncio.gather(*[self._exec_tool(tu, ctx) for tu in tool_uses])
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tu["id"], "content": out, **({"is_error": True} if err else {})}
                    for tu, (out, err) in zip(tool_uses, results)]})
            else:
                await self.out_text(ctx, "Parei aqui: atingi o limite de passos para um único pedido. Me diga como seguir.", True)
                _append_user(messages, "[Sistema] Limite de passos atingido; aguarde nova instrução do usuário.")
                messages.append({"role": "assistant", "content": [{"type": "text", "text": "Ok, aguardando."}]})
            await self.save_conv(ctx, messages)
        except (AIError, llm.LLMError) as e:
            await self.out_text(ctx, f"⚠️ {e}", True)
            await self.save_conv(ctx, snapshot)  # termina com a msg do usuário: a próxima é mesclada nela
        except Exception as e:
            logger.exception("falha no agente")
            await self.out_text(ctx, f"⚠️ Erro interno: {e}", True)
            await self.save_conv(ctx, snapshot)

    async def _track_usage(self, usage: dict, provider: str = ""):
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        inc = {
            "requests": 1,
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
            "cache_write_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        }
        await self.db.ai_usage.update_one({"month": month}, {"$inc": inc}, upsert=True)

    # ----- ferramentas -----
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
            if name == "get_interfaces":
                return await self._tool_interfaces(ctx, inp)
            if name == "get_bgp_sessions":
                return await self._tool_bgp(ctx, inp)
            if name == "get_optical_signal":
                return await self._tool_optics(ctx, inp)
            if name == "get_flow_top":
                return await self._tool_flow(ctx, inp)
            if name == "run_show_commands":
                return await self._tool_show(ctx, inp)
            if name == "get_config_backup":
                return await self._tool_backup(ctx, inp)
            if name == "search_configs":
                return await self._tool_search_configs(ctx, inp)
            if name == "propose_config_change":
                return await self._tool_propose(ctx, inp)
            return f"Ferramenta desconhecida: {name}", True
        except Exception as e:
            logger.exception(f"tool {name}")
            return f"Erro ao executar {name}: {e}", True

    def _cut(self, ctx: dict, text: str) -> str:
        return _truncate(text, ctx.get("out_limit") or MAX_TOOL_OUTPUT)

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
        try:
            limit = max(1, min(int(inp.get("limit") or 50), 300))
        except (TypeError, ValueError):
            limit = 50
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

    async def _snmp(self, dev: dict):
        if not self.snmp_client:
            raise RuntimeError("SNMP indisponível neste canal")
        return await self.snmp_client(dev)

    async def _iface_list(self, dev: dict, client, refresh: bool = False) -> List[dict]:
        cached = await self.db.device_ifaces.find_one({"device_id": dev["id"]}, {"_id": 0})
        fresh = False
        if cached and not refresh:
            try:
                fresh = datetime.now(timezone.utc) - datetime.fromisoformat(cached["at"]) < timedelta(hours=12)
            except Exception:
                fresh = False
        if fresh:
            return cached.get("interfaces") or []
        info = await asyncio.wait_for(snmp_service.discover_interfaces(client), timeout=90)
        doc = {"device_id": dev["id"], "at": datetime.now(timezone.utc).isoformat(), **info}
        await self.db.device_ifaces.update_one({"device_id": dev["id"]}, {"$set": doc}, upsert=True)
        return info["interfaces"]

    async def _tool_interfaces(self, ctx: dict, inp: dict):
        dev, err = await self._find_device(ctx["user"], inp.get("device", ""))
        if err:
            return err, True
        try:
            client = await self._snmp(dev)
            ifaces = await self._iface_list(dev, client)
        except Exception as e:
            return (f"SNMP falhou em {dev['name']}: {e}. Use run_show_commands (ex.: display interface brief / "
                    "show interfaces terse)."), True
        words = [w.lower() for w in (inp.get("query") or "").split() if w.strip()]
        rows = [i for i in ifaces if all(w in f"{i.get('name')} {i.get('alias')} {i.get('descr')} #{i.get('index')}".lower() for w in words)]
        total = len(rows)
        with_traffic = inp.get("with_traffic", True) is not False
        live = {}
        if rows and len(rows) <= 40:
            idx = [i["index"] for i in rows]
            try:
                a = await asyncio.wait_for(snmp_service.poll_counters(client, idx), timeout=40)
                if with_traffic:
                    t0 = time.monotonic()
                    await asyncio.sleep(5)
                    b = await asyncio.wait_for(snmp_service.poll_counters(client, idx), timeout=40)
                    dt = time.monotonic() - t0
                else:
                    b, dt = a, 0
                for i in idx:
                    x, y = a.get(i, {}), b.get(i, {})
                    rate = lambda k: ((y[k] - x[k]) % 2 ** 64) * 8 / dt if dt and isinstance(x.get(k), int) and isinstance(y.get(k), int) else None  # noqa: E731
                    live[i] = {"oper": y.get("oper"), "admin": y.get("admin"), "in": rate("in_octets"), "out": rate("out_octets")}
            except Exception as e:
                live = {"_err": str(e)}
        if inp.get("only_up"):
            rows = [r for r in rows if (live.get(r["index"], {}).get("oper") or r.get("oper")) == "up"]
        if inp.get("only_down"):
            rows = [r for r in rows if (live.get(r["index"], {}).get("oper") or r.get("oper")) != "up"
                    and (live.get(r["index"], {}).get("admin") or r.get("admin")) == "up"]
        lines = [f"== {dev['name']}: {len(rows)} interface(s)" + (f" (de {total} que casam)" if len(rows) != total else "")
                 + (" · tráfego medido agora em ~5 s" if live and "_err" not in live and with_traffic else "") + " =="]
        if "_err" in live:
            lines.append(f"(não consegui ler os contadores agora: {live['_err']})")
        if total > 40:
            lines.append("(muitas interfaces: sem medição de tráfego — refine com query)")
        for r in rows[:150]:
            lv = live.get(r["index"], {}) if isinstance(live, dict) else {}
            spd = r.get("speed_mbps") or 0
            util = ""
            if lv.get("in") is not None and spd:
                util = f" ({max(lv['in'], lv.get('out') or 0) / (spd * 1e6) * 100:.0f}% da porta)"
            traffic = f" | ↓{_fmt_bps(lv.get('in'))} ↑{_fmt_bps(lv.get('out'))}{util}" if lv.get("in") is not None else ""
            lines.append(f"{r.get('name')} (#{r['index']}) | admin {lv.get('admin') or r.get('admin')} / oper {lv.get('oper') or r.get('oper')}"
                         f" | {('%gG' % (spd / 1000)) if spd >= 1000 else (str(spd) + 'M') if spd else '?'}"
                         f"{(' | ' + r['alias']) if r.get('alias') else ''}{traffic}")
        if len(rows) > 150:
            lines.append(f"... mais {len(rows) - 150}")
        return self._cut(ctx, "\n".join(lines)), False

    async def _tool_bgp(self, ctx: dict, inp: dict):
        import netanalysis
        dev, err = await self._find_device(ctx["user"], inp.get("device", ""))
        if err:
            return err, True
        try:
            client = await self._snmp(dev)
            bgp = await asyncio.wait_for(netanalysis.collect_bgp(client), timeout=60)
        except Exception as e:
            return f"SNMP falhou em {dev['name']}: {e}. Use run_show_commands (display bgp peer / show bgp summary).", True
        peers = bgp.get("peers") or []
        if not peers:
            return (f"{dev['name']}: nenhuma sessão na BGP4-MIB (AS local {bgp.get('local_as') or '?'}). Pode não ter BGP IPv4 na "
                    "instância principal ou a MIB não está exposta — confira com run_show_commands."), False
        if inp.get("only_problems"):
            peers = [p for p in peers if p["state"] != "established" or (isinstance(p.get("established_sec"), int) and p["established_sec"] < 3600)]
        up = sum(1 for p in bgp["peers"] if p["state"] == "established")
        lines = [f"== {dev['name']} · AS {bgp.get('local_as') or '?'} · {up}/{len(bgp['peers'])} sessões established =="]
        for p in sorted(peers, key=lambda p: (p["state"] == "established", p["ip"])):
            extra = []
            if p["state"] == "established":
                extra.append(f"há {_fmt_age(p.get('established_sec'))}")
            elif not p.get("admin_up"):
                extra.append("desativada (admin)")
            if p.get("transitions"):
                extra.append(f"{p['transitions']} transições")
            if p.get("last_error"):
                extra.append(f"último erro: {p['last_error']}")
            lines.append(f"{p['ip']} | AS {p.get('remote_as')} | {p['state']} | " + " | ".join(extra))
        return self._cut(ctx, "\n".join(lines)), False

    async def _tool_optics(self, ctx: dict, inp: dict):
        import optics as optics_mod
        dev, err = await self._find_device(ctx["user"], inp.get("device", ""))
        if err:
            return err, True
        ifname = str(inp.get("interface") or "").strip()
        if not ifname:
            return "Informe a interface.", True
        cached = await self.db.device_ifaces.find_one({"device_id": dev["id"]}, {"_id": 0, "interfaces": 1}) or {}
        norm = lambda x: re.sub(r"\s+", "", str(x or "")).lower()  # noqa: E731
        names = [i.get("name") for i in cached.get("interfaces") or []]
        exact = [n for n in names if norm(n) == norm(ifname)]
        if not exact:
            ends = [n for n in names if norm(n).endswith(norm(ifname))]
            if len(ends) == 1:
                ifname = ends[0]
            elif len(ends) > 1:
                return f"'{ifname}' casa com várias interfaces: {', '.join(ends[:10])}. Use o nome completo.", True
        s = await optics_mod.get_settings(self.db)
        try:
            async with self._sem:
                client = await self.connect_device(dev)
                try:
                    session = await client.shell()
                    try:
                        parsed, cmd, raw = await optics_mod.read_optics(session, dev.get("device_type") or "other", ifname, s)
                    finally:
                        await session.close()
                finally:
                    await client.close()
        except Exception as e:
            await self._audit(ctx, dev, "read", [f"(óptica) {ifname}"], False, str(e))
            return f"Falha ao ler a óptica em {dev['name']}: {e}", True
        await self._audit(ctx, dev, "read", [cmd or f"(óptica) {ifname}"], parsed["ok"], raw)
        if not parsed["ok"]:
            return self._cut(ctx, f"Não consegui interpretar a óptica de {ifname}. Saída bruta:\n{raw}"), False
        lines = [f"== {dev['name']} · {ifname} ({cmd}) =="]
        for ln in parsed["lanes"]:
            rx = "sem luz" if ln["rx"] is not None and ln["rx"] <= optics_mod.NO_LIGHT else (f"{ln['rx']} dBm" if ln["rx"] is not None else "?")
            lines.append(f"lane {ln.get('lane', 0)}: RX {rx} | TX {ln['tx'] if ln['tx'] is not None else '?'} dBm")
        lines.append(f"RX mínimo: {parsed['rx_min']} dBm")
        return "\n".join(lines), False

    async def _tool_flow(self, ctx: dict, inp: dict):
        if not self.flow_query:
            return "Análise de Flow indisponível.", True
        try:
            minutes = int(inp.get("minutes") or 60)
        except (TypeError, ValueError):
            minutes = 60
        text = await self.flow_query(ctx["user"], str(inp.get("interface") or ""), inp.get("group_by") or "interface",
                                     inp.get("direction") or "in", max(5, min(minutes, 43200)))
        return self._cut(ctx, text), False

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
            "channel": self.channel, "telegram_id": str(ctx["telegram_id"]) if ctx.get("telegram_id") else None,
            "device_id": dev["id"], "device_name": dev["name"],
            "kind": kind, "commands": commands, "ok": ok, "output": (output or "")[:4000], "proposal_id": proposal_id,
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
        commands = [str(c).strip() for c in (inp.get("commands") or []) if str(c).strip()][:10]
        if not commands:
            return "Nenhum comando informado.", True
        dtype = dev.get("device_type") or "linux"
        blocked = [c for c in commands if not is_read_only(c, dtype)]
        if blocked:
            return ("Recusado — não são comandos de leitura: " + " ; ".join(blocked) +
                    ". Para alterações use propose_config_change."), True
        try:
            timeout = max(10, min(int(inp.get("timeout") or 60), 240))
        except (TypeError, ValueError):
            timeout = 60
        try:
            r = await self._run_on_device(dev, commands, timeout, idle=4.0)
        except Exception as e:
            await self._audit(ctx, dev, "read", commands, False, str(e))
            return f"Falha ao conectar/executar em {dev['name']}: {e}", True
        await self._audit(ctx, dev, "read", commands, r["ok"], r["output"])
        out = self._cut(ctx, _filter_output(r["output"], inp.get("filter")))
        return f"== {dev['name']} ({dev['host']}) ==\n{out or '(saída vazia)'}", False

    async def _tool_backup(self, ctx: dict, inp: dict):
        dev, err = await self._find_device(ctx["user"], inp.get("device", ""))
        if err:
            return err, True
        b = await self.db.backups.find_one({"device_id": dev["id"], "ok": True}, {"_id": 0}, sort=[("created_at", -1)])
        if not b:
            return f"Não há backup salvo de {dev['name']}. Use run_show_commands para ler a configuração.", True
        when = b.get("created_at", "")[:16].replace("T", " ")
        out = self._cut(ctx, _filter_output(b.get("content", ""), inp.get("filter")))
        return f"== Backup de {dev['name']} em {when} UTC ==\n{out}", False

    async def _tool_search_configs(self, ctx: dict, inp: dict):
        q = {"owner_id": ctx["user"]["id"]}
        if inp.get("device_type"):
            q["device_type"] = str(inp["device_type"]).lower()
        devs = await self.db.devices.find(q, {"_id": 0, "id": 1, "name": 1, "device_type": 1, "host": 1}).to_list(10000)
        w = str(inp.get("device") or "").strip().lower()
        if w:
            devs = [d for d in devs if w in (d.get("name") or "").lower()]
        try:
            res = await configsearch.search(self.db, devs, str(inp.get("query") or ""), inp.get("mode") or "word", False, 0, per_device=20)
        except ValueError as e:
            return str(e), True
        return self._cut(ctx, configsearch.as_text(res)), False

    async def _tool_propose(self, ctx: dict, inp: dict):
        s = ctx["settings"]
        if not s.get("ai_allow_changes"):
            return "Alterações estão desativadas pelo administrador. Mostre os comandos ao usuário apenas como sugestão.", True
        changes = []
        for ch in (inp.get("changes") or [])[:20]:
            dev, err = await self._find_device(ctx["user"], ch.get("device", ""))
            if err:
                return err, True
            cmds = [str(c).rstrip() for c in ch.get("commands") or [] if str(c).strip()][:200]
            if not cmds:
                return f"Sem comandos para {dev['name']}.", True
            changes.append({"device_id": dev["id"], "device_name": dev["name"], "host": dev["host"], "commands": cmds})
        if not changes:
            return "Nenhuma alteração informada.", True
        pid = os.urandom(4).hex()
        risk = inp.get("risk") if inp.get("risk") in ("baixo", "medio", "alto") else "medio"
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
        now = datetime.now(timezone.utc)
        doc = {"id": pid, "channel": self.channel, "user_id": ctx["user"]["id"], "changes": changes,
               "summary": inp.get("summary"), "risk": risk, "rollback": inp.get("rollback") or "",
               "status": "pending", "created_at": now.isoformat(),
               "expires_at": (now + timedelta(minutes=PENDING_TTL_MIN)).isoformat()}
        extra = await self.out_proposal(ctx, pid, text, doc)
        await self.db.ai_pending.insert_one({**doc, **(extra or {}), "message_text": text})
        return (f"Proposta #{pid} enviada ao usuário com botões Confirmar/Cancelar. NADA foi executado. "
                "Encerre sua resposta avisando que aguarda a confirmação pelo botão."), False

    async def claim_proposal(self, pid: str, user_id: str) -> Tuple[Optional[dict], str]:
        """Confirmação atômica: só uma execução por proposta, e só se ainda estiver válida."""
        now = datetime.now(timezone.utc)
        r = await self.db.ai_pending.update_one(
            {"id": pid, "user_id": user_id, "status": "pending", "expires_at": {"$gt": now.isoformat()}},
            {"$set": {"status": "running", "decided_at": now.isoformat()}})
        if not r.modified_count:
            cur = await self.db.ai_pending.find_one({"id": pid}, {"_id": 0, "status": 1})
            why = "expirou — peça de novo" if cur and cur.get("status") == "pending" else f"já está '{cur.get('status') if cur else '?'}'"
            return None, why
        return await self.db.ai_pending.find_one({"id": pid}, {"_id": 0}), ""

    async def execute_proposal(self, p: dict, ctx: dict) -> Tuple[bool, str]:
        async def _one(ch):
            dev = await self.db.devices.find_one({"id": ch["device_id"], "owner_id": p["user_id"]}, {"_id": 0})
            if not dev:
                return ch, False, "Equipamento não encontrado (removido?)"
            try:
                res = await self._run_on_device(dev, ch["commands"], timeout=120, idle=4.0)
                await self._audit(ctx, dev, "change", ch["commands"], res["ok"], res["output"], p["id"])
                return ch, res["ok"], res["output"]
            except Exception as e:
                await self._audit(ctx, dev, "change", ch["commands"], False, str(e), p["id"])
                return ch, False, f"Falha ao conectar/executar: {e}"

        results = await asyncio.gather(*[_one(c) for c in p["changes"]])
        all_ok = all(ok for _, ok, _ in results)
        await self.db.ai_pending.update_one({"id": p["id"]}, {"$set": {"status": "done" if all_ok else "failed",
                                                                       "finished_at": datetime.now(timezone.utc).isoformat()}})
        report = [f"== {ch['device_name']} ({'ok' if ok else 'FALHA'}) ==\n{_truncate(out, 6000)}" for ch, ok, out in results]
        feedback = (f"[Sistema] O usuário CONFIRMOU a proposta #{p['id']} e os comandos foram executados. "
                    f"Saída da execução:\n\n" + "\n\n".join(report) +
                    "\n\nAnalise a saída procurando erros, verifique o resultado com uma consulta se fizer sentido "
                    "e informe o usuário em poucas linhas.")
        return all_ok, feedback


# ---------------------------------------------------------------------------
# Canal Telegram
# ---------------------------------------------------------------------------
class TelegramAssistant(AgentCore):
    channel = "telegram"

    def __init__(self, db, connect_device: Callable[[dict], Awaitable], get_telegram_token: Callable[[], Awaitable[str]],
                 snmp_client: Optional[Callable] = None, flow_query: Optional[Callable] = None):
        super().__init__(db, connect_device, snmp_client, flow_query)
        self.get_telegram_token = get_telegram_token
        self._task: Optional[asyncio.Task] = None
        self._token = ""
        self.status = "parado"
        self.runbook_list = None        # async (user) -> [roteiros liberados para o Telegram]
        self.runbook_run = None         # async (user, id) -> texto com o resultado

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
                    not_ready = ai_ready(s)
                    if not (s["ai_enabled"] and token and not not_ready):
                        self.status = ("desativado" if not s["ai_enabled"] else
                                       "falta configurar o token do Telegram" if not token else not_ready)
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

    # ---------- ganchos ----------
    async def out_text(self, ctx: dict, text: str, final: bool):
        await self.send(ctx["chat_id"], text)

    async def out_proposal(self, ctx: dict, pid: str, text: str, data: dict) -> dict:
        if len(text) > TG_LIMIT:
            text = text[:TG_LIMIT - 60] + "\n[... lista longa cortada na mensagem, mas será executada completa ...]"
        kb = {"inline_keyboard": [[{"text": "✅ Confirmar", "callback_data": f"ok:{pid}"},
                                   {"text": "❌ Cancelar", "callback_data": f"no:{pid}"}]]}
        sent = await self.send(ctx["chat_id"], text, reply_markup=kb)
        return {"chat_id": str(ctx["chat_id"]), "telegram_id": str(ctx["telegram_id"]),
                "message_id": (sent.get("result") or {}).get("message_id")}

    async def save_conv(self, ctx: dict, messages: list):
        await self._save_conv(ctx["chat_id"], messages)

    async def run_agent(self, ctx: dict, messages: list):
        stop = asyncio.Event()
        typing = asyncio.create_task(self._typing(ctx["chat_id"], stop))
        try:
            await super().run_agent(ctx, messages)
        finally:
            stop.set()
            typing.cancel()

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
            return await self.send(chat_id, f"Acesso não autorizado.\nSeu ID do Telegram é {from_id} — peça ao administrador do BastiON para liberar em Automação → Assistente IA.")
        user = await self.db.users.find_one({"id": link["user_id"]}, {"_id": 0, "password_hash": 0})
        if not user:
            return await self.send(chat_id, "Seu ID do Telegram está vinculado a um usuário do BastiON que não existe mais. Fale com o administrador.")
        if cmd in ("/start", "/ajuda", "/help"):
            return await self.send(chat_id, HELP_TEXT)
        if self.runbook_list and (cmd in ("/roteiros", "/roteiro", "/scripts") or not cmd):
            rbs = await self.runbook_list(user)
            if cmd in ("/roteiros", "/scripts") or (cmd == "/roteiro" and len(text.split()) == 1):
                if not rbs:
                    return await self.send(chat_id, "Nenhum roteiro liberado para o Telegram. Crie em Execução em Lote → Roteiros.")
                kb = {"inline_keyboard": [[{"text": f"▶ {r['name']}"[:60], "callback_data": f"rb:{r['id']}"}] for r in rbs[:30]]}
                return await self.send(chat_id, "Roteiros automatizados — escolha um (vou pedir confirmação antes de executar):", reply_markup=kb)
            wanted = text.split(None, 1)[1] if cmd == "/roteiro" else text
            key = _norm_name(wanted)
            hit = next((r for r in rbs if _norm_name(r["name"]) == key), None)
            if hit:
                return await self._runbook_confirm(chat_id, hit)
            if cmd == "/roteiro":
                return await self.send(chat_id, f"Não achei o roteiro “{wanted}”. Mande /roteiros para ver a lista.")
        if cmd in ("/novo", "/reset", "/new"):
            await self.db.ai_conversations.delete_one({"chat_id": str(chat_id)})
            return await self.send(chat_id, "Conversa reiniciada.")
        lock = self._locks.setdefault(str(chat_id), asyncio.Lock())
        if lock.locked():
            await self.send(chat_id, "Ainda estou trabalhando no pedido anterior — sua mensagem entra na sequência.")
        async with lock:
            messages = await self._load_conv(chat_id)
            _append_user(messages, text)
            await self.run_agent({"chat_id": chat_id, "telegram_id": from_id, "user": user, "settings": s}, messages)

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

    async def _on_mitigation_cb(self, cq: dict, data: str):
        """Botão do alerta de ataque: mit:<ataque>:<min> pede confirmação; mitok:<ataque>:<min> aplica."""
        import mitigation
        from_id = str((cq.get("from") or {}).get("id"))
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        parts = data.split(":")
        if len(parts) != 3 or not parts[2].isdigit():
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"])
        action, aid, mins = parts[0], parts[1], int(parts[2])
        if action == "mitno":
            await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Cancelado — nada foi anunciado.")
            return await self._tg("editMessageReplyMarkup", chat_id=chat_id, message_id=msg.get("message_id"), reply_markup={"inline_keyboard": []})
        s = await get_ai_settings(self.db)
        link = self._auth(s, from_id)
        user = await self.db.users.find_one({"id": link["user_id"]}, {"_id": 0, "password_hash": 0}) if link else None
        if not user or user.get("role") == "viewer":
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"], show_alert=True,
                                  text=f"Seu Telegram ({from_id}) não está liberado para mitigar. Libere em Automação → Assistente IA.")
        att = await self.db.flow_attacks.find_one({"id": aid}, {"_id": 0, "victim": 1, "type": 1, "peak_bps": 1, "status": 1})
        if not att:
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Ataque não encontrado.", show_alert=True)
        if action == "mit":
            await self._tg("answerCallbackQuery", callback_query_id=cq["id"])
            kb = {"inline_keyboard": [[{"text": "✅ Confirmar blackhole", "callback_data": f"mitok:{aid}:{mins}"},
                                       {"text": "❌ Cancelar", "callback_data": f"mitno:{aid}:{mins}"}]]}
            return await self.send(chat_id, f"Confirma o blackhole de {att['victim']} por {mitigation.fmt_minutes(mins)}?\n"
                                            "O IP fica SEM tráfego nenhum (inclusive o legítimo) até o prazo acabar ou alguém remover.",
                                   reply_markup=kb)
        if action != "mitok":
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"])
        try:
            m = await mitigation.create(self.db, user, attack_id=aid, minutes=mins, channel="telegram")
        except mitigation.MitigationError as e:
            await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Não foi possível mitigar", show_alert=False)
            return await self.send(chat_id, f"⚠️ {e}")
        await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Blackhole anunciado")
        await self._tg("editMessageReplyMarkup", chat_id=chat_id, message_id=msg.get("message_id"), reply_markup={"inline_keyboard": []})
        exp = datetime.fromisoformat(m["expires_at"]).astimezone().strftime("%H:%M")
        await self.send(chat_id, f"🛡️ {m['prefix']} em blackhole nas bordas até {exp} ({'prazo estendido' if m.get('extended') else 'por ' + (user.get('email') or '')}).\n"
                                 "Remover antes: Flow → Mitigação no BastiON.")

    async def _runbook_confirm(self, chat_id, rb: dict):
        names = {d["id"]: d["name"] async for d in self.db.devices.find(
            {"id": {"$in": [st["device_id"] for st in rb.get("steps") or []]}}, {"_id": 0, "id": 1, "name": 1})}
        for st in rb.get("steps") or []:
            st["device_name"] = names.get(st["device_id"], "?")
        steps = "\n".join(f"{i}. {st.get('device_name') or 'equipamento'}: " + " | ".join(
            [l.strip() for l in (st.get("commands") or "").splitlines() if l.strip() and l.strip() not in ("#", "!")][:6])
            for i, st in enumerate(rb.get("steps") or [], 1))
        kb = {"inline_keyboard": [[{"text": "✅ Executar", "callback_data": f"rbok:{rb['id']}"},
                                   {"text": "❌ Cancelar", "callback_data": f"rbno:{rb['id']}"}]]}
        return await self.send(chat_id, f"Roteiro {rb['name']}\n{steps}\n\nExecuto agora? (para no primeiro erro)", reply_markup=kb)

    async def _on_runbook_cb(self, cq: dict, data: str):
        from_id = str((cq.get("from") or {}).get("id"))
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        action, _, rid = data.partition(":")
        s = await get_ai_settings(self.db)
        link = self._auth(s, from_id)
        user = await self.db.users.find_one({"id": link["user_id"]}, {"_id": 0, "password_hash": 0}) if link else None
        if not user or not self.runbook_list:
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Acesso não autorizado.", show_alert=True)
        if action == "rbno":
            await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Cancelado — nada foi executado.")
            return await self._tg("editMessageReplyMarkup", chat_id=chat_id, message_id=msg.get("message_id"), reply_markup={"inline_keyboard": []})
        rb = next((r for r in await self.runbook_list(user) if r["id"] == rid), None)
        if not rb:
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Roteiro não encontrado.", show_alert=True)
        if action == "rb":
            await self._tg("answerCallbackQuery", callback_query_id=cq["id"])
            return await self._runbook_confirm(chat_id, rb)
        if action != "rbok":
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"])
        await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Executando…")
        await self._tg("editMessageReplyMarkup", chat_id=chat_id, message_id=msg.get("message_id"), reply_markup={"inline_keyboard": []})
        await self.send(chat_id, f"⏳ Executando {rb['name']}…")
        stop = asyncio.Event()
        typing = asyncio.create_task(self._typing(chat_id, stop))
        try:
            text = await self.runbook_run(user, rid)
        finally:
            stop.set()
            typing.cancel()
        return await self.send(chat_id, text)

    async def _on_callback(self, cq: dict):
        data = cq.get("data") or ""
        if data.startswith(("rb:", "rbok:", "rbno:")):
            return await self._on_runbook_cb(cq, data)
        if data.startswith(("mit:", "mitok:", "mitno:")):
            return await self._on_mitigation_cb(cq, data)
        from_id = str((cq.get("from") or {}).get("id"))
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        action, _, pid = data.partition(":")
        p = await self.db.ai_pending.find_one({"id": pid}, {"_id": 0})
        if not p or p.get("channel", "telegram") != "telegram":
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Proposta não encontrada.")
        if from_id != p.get("telegram_id"):
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
        p2, why = await self.claim_proposal(pid, p["user_id"])
        if not p2:
            return await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text=f"Proposta {why}.", show_alert=True)
        await self._tg("answerCallbackQuery", callback_query_id=cq["id"], text="Executando…")
        await self._edit_status(p, "⏳ Confirmada — executando…")
        user = await self.db.users.find_one({"id": p["user_id"]}, {"_id": 0, "password_hash": 0})
        ctx = {"chat_id": chat_id, "telegram_id": from_id, "user": user, "settings": s}
        all_ok, feedback = await self.execute_proposal(p2, ctx)
        await self._edit_status(p, "✅ Executada." if all_ok else "⚠️ Executada com falhas.")
        async with self._locks.setdefault(str(chat_id), asyncio.Lock()):
            msgs = await self._load_conv(chat_id)
            _append_user(msgs, feedback)
            await self.run_agent(ctx, msgs)

    async def _edit_status(self, p: dict, status: str):
        if not p.get("message_id"):
            return await self.send(p["chat_id"], status)
        text = (p.get("message_text") or "").replace(f"Válida por {PENDING_TTL_MIN} min. Nada foi executado ainda.", "").rstrip()
        await self._tg("editMessageText", chat_id=p["chat_id"], message_id=p["message_id"],
                       text=f"{text}\n\n{status}"[:4096], disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Canal web (chat dentro do BastiON)
# ---------------------------------------------------------------------------
WEB_DISPLAY_MAX = 200


class WebAssistant(AgentCore):
    """Uma conversa por usuário, guardada em ai_web: mensagens da API + o que aparece na tela (display)."""
    channel = "web"

    def __init__(self, db, connect_device, snmp_client=None, flow_query=None):
        super().__init__(db, connect_device, snmp_client, flow_query)
        self._docs: dict = {}
        self._tasks: set = set()

    # ----- estado -----
    async def _doc(self, uid: str) -> dict:
        d = self._docs.get(uid)
        if d is None:
            d = await self.db.ai_web.find_one({"user_id": uid}, {"_id": 0}) or {"user_id": uid, "messages": [], "display": []}
            d["busy"] = False               # tarefa que morreu com o processo não fica "trabalhando" para sempre
            self._docs[uid] = d
        return d

    async def _persist(self, d: dict):
        d["display"] = d["display"][-WEB_DISPLAY_MAX:]
        d["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self.db.ai_web.update_one({"user_id": d["user_id"]}, {"$set": {k: v for k, v in d.items() if k != "_id"}}, upsert=True)

    async def _push(self, uid: str, item: dict):
        d = await self._doc(uid)
        item = {"id": os.urandom(6).hex(), "at": datetime.now(timezone.utc).isoformat(), **item}
        d["display"].append(item)
        d["rev"] = d.get("rev", 0) + 1
        await self._persist(d)
        return item

    async def view(self, uid: str) -> dict:
        d = await self._doc(uid)
        now = datetime.now(timezone.utc).isoformat()
        for it in d["display"]:        # propostas vencidas aparecem como expiradas
            if it.get("kind") == "proposal" and it.get("status") == "pending" and it.get("expires_at", "") < now:
                it["status"] = "expired"
        return {"busy": d.get("busy", False), "rev": d.get("rev", 0), "items": [dict(i) for i in d["display"]]}

    async def reset(self, uid: str):
        d = await self._doc(uid)
        if d.get("busy"):
            raise AIError("Aguarde terminar o pedido atual")
        d.update({"messages": [], "display": [], "rev": d.get("rev", 0) + 1})
        await self._persist(d)

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    # ----- entrada -----
    async def post(self, user: dict, text: str, voice: bool = False) -> dict:
        s = await get_ai_settings(self.db)
        why = ai_ready(s)
        if not s.get("ai_web_enabled", True):
            raise AIError("O chat está desativado (Automação → Assistente IA)")
        if why:
            raise AIError(f"Assistente não configurado: {why} (Automação → Assistente IA)")
        text = (text or "").strip()[:4000]
        if not text:
            raise AIError("Mensagem vazia")
        d = await self._doc(user["id"])
        if d.get("busy"):
            raise AIError("Ainda estou trabalhando no pedido anterior")
        d["busy"] = True
        await self._push(user["id"], {"kind": "user", "text": text})
        self._spawn(self._run(user, s, text, voice=voice))
        return await self.view(user["id"])

    async def _run(self, user: dict, s: dict, text: str, feedback: bool = False, voice: bool = False):
        d = await self._doc(user["id"])
        try:
            msgs = d.get("messages") or []
            try:
                if msgs and datetime.now(timezone.utc) - datetime.fromisoformat(d.get("updated_at")) > timedelta(hours=CONV_TTL_HOURS * 4):
                    msgs = []
            except Exception:
                pass
            _append_user(msgs, text)
            await self.run_agent({"user": user, "settings": s, "voice": voice}, msgs)
        finally:
            d["busy"] = False
            d["rev"] = d.get("rev", 0) + 1
            await self._persist(d)

    # ----- ganchos -----
    async def out_text(self, ctx: dict, text: str, final: bool):
        await self._push(ctx["user"]["id"], {"kind": "error" if text.startswith("⚠️") else "assistant", "text": text})

    async def out_wait(self, ctx: dict, text: str):
        await self._push(ctx["user"]["id"], {"kind": "wait", "text": text})

    async def out_activity(self, ctx: dict, name: str, inp: dict):
        if name != "propose_config_change":
            await self._push(ctx["user"]["id"], {"kind": "tool", "tool": name, "text": describe_tool(name, inp)})

    async def out_proposal(self, ctx: dict, pid: str, text: str, data: dict) -> dict:
        await self._push(ctx["user"]["id"], {"kind": "proposal", "pid": pid, "summary": data["summary"], "risk": data["risk"],
                                             "rollback": data.get("rollback") or "", "expires_at": data["expires_at"],
                                             "status": "pending",
                                             "changes": [{k: c[k] for k in ("device_name", "host", "commands")} for c in data["changes"]]})
        return {}

    async def save_conv(self, ctx: dict, messages: list):
        d = await self._doc(ctx["user"]["id"])
        d["messages"] = compact_history(messages)
        await self._persist(d)

    async def _set_prop_status(self, uid: str, pid: str, status: str, note: str = ""):
        d = await self._doc(uid)
        for it in d["display"]:
            if it.get("kind") == "proposal" and it.get("pid") == pid:
                it["status"] = status
                if note:
                    it["note"] = note
        d["rev"] = d.get("rev", 0) + 1
        await self._persist(d)

    # ----- confirmar / cancelar -----
    async def decide(self, user: dict, pid: str, action: str) -> dict:
        p = await self.db.ai_pending.find_one({"id": pid, "user_id": user["id"], "channel": "web"}, {"_id": 0})
        if not p:
            raise AIError("Proposta não encontrada")
        d = await self._doc(user["id"])
        if action == "cancel":
            r = await self.db.ai_pending.update_one({"id": pid, "status": "pending"},
                                                    {"$set": {"status": "cancelled", "decided_at": datetime.now(timezone.utc).isoformat()}})
            if not r.modified_count:
                raise AIError("Essa proposta já foi decidida")
            await self._set_prop_status(user["id"], pid, "cancelled")
            msgs = d.get("messages") or []
            _append_user(msgs, f"[Sistema] O usuário CANCELOU a proposta #{pid}. Nada foi executado.")
            msgs.append({"role": "assistant", "content": [{"type": "text", "text": "Ok, proposta cancelada."}]})
            d["messages"] = msgs
            await self._persist(d)
            return await self.view(user["id"])
        if action != "confirm":
            raise AIError("Ação inválida")
        if d.get("busy"):
            raise AIError("Aguarde terminar o pedido atual")
        s = await get_ai_settings(self.db)
        if not s.get("ai_allow_changes"):
            raise AIError("Alterações estão desativadas pelo administrador")
        p2, why = await self.claim_proposal(pid, user["id"])
        if not p2:
            if "expirou" in why:
                await self._set_prop_status(user["id"], pid, "expired")
            raise AIError(f"Proposta {why}")
        d["busy"] = True
        await self._set_prop_status(user["id"], pid, "running")
        self._spawn(self._execute(user, s, p2))
        return await self.view(user["id"])

    async def _execute(self, user: dict, s: dict, p: dict):
        ctx = {"user": user, "settings": s}
        try:
            all_ok, feedback = await self.execute_proposal(p, ctx)
        except Exception as e:
            logger.exception("execução da proposta")
            all_ok, feedback = False, f"[Sistema] Falha ao executar a proposta #{p['id']}: {e}"
        await self._set_prop_status(user["id"], p["id"], "done" if all_ok else "failed")
        await self._run(user, s, feedback)      # o modelo confere a saída e responde (busy segue até o fim)
