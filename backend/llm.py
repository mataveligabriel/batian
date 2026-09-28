"""Camada de modelo do assistente: Claude (API da Anthropic) ou qualquer API compatível com OpenAI
(Groq, Google Gemini, Ollama local, OpenRouter…). O resto do código conversa sempre no formato da Anthropic
(blocos text / tool_use / tool_result); aqui convertemos na ida e na volta.
"""
import asyncio
import json
import os
from typing import List, Optional

import httpx

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

PROVIDERS = {
    "groq": {"label": "Groq (grátis para começar)", "base_url": "https://api.groq.com/openai/v1",
             "model": "openai/gpt-oss-120b", "key": True, "free": True,
             "hint": "Crie a chave em console.groq.com (sem cartão). Plano grátis com limite por minuto e por dia.",
             "suggest": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]},
    "gemini": {"label": "Google Gemini (grátis para começar)", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-3.5-flash", "key": True, "free": True,
               "hint": "Chave em aistudio.google.com. No plano grátis o Google pode usar as conversas para melhorar os modelos.",
               "suggest": ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite"]},
    "ollama": {"label": "Ollama no próprio servidor (grátis, local)", "base_url": "http://127.0.0.1:11434/v1",
               "model": "qwen2.5:7b", "key": False, "free": True,
               "hint": "Roda no seu VPS: nada sai da rede. Sem GPU é lento; modelos de 7-8B erram mais nas ferramentas.",
               "suggest": ["qwen2.5:7b", "qwen2.5:14b", "llama3.1:8b", "qwen3:8b"]},
    "openai": {"label": "Outra API compatível com OpenAI (OpenRouter, etc.)", "base_url": "https://openrouter.ai/api/v1",
               "model": "", "key": True, "free": False, "hint": "Informe a URL base e o modelo do serviço.", "suggest": []},
    "anthropic": {"label": "Claude (Anthropic, pago)", "base_url": "", "model": "claude-sonnet-5", "key": True, "free": False,
                  "hint": "Mais capaz com ferramentas. Chave em console.anthropic.com.",
                  "suggest": ["claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"]},
}


class LLMError(Exception):
    pass


def tool_output_limit(provider: str) -> int:
    """Planos grátis têm limite de tokens por minuto: saídas de ferramenta menores."""
    return 15000 if provider == "anthropic" else 6000


# ---------------------------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------------------------
def _strip_extra(messages: list) -> list:
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(b, dict) and "extra" in b for b in c):
            m = {**m, "content": [{k: v for k, v in b.items() if k != "extra"} if isinstance(b, dict) else b for b in c]}
        out.append(m)
    return out


async def _anthropic(http, key, model, messages, system, tools, max_tokens) -> dict:
    messages = _strip_extra(messages)
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if tools:
        body["tools"] = tools
    headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
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
            raise LLMError(f"API do Claude HTTP {r.status_code}: {msg[:300]}")
        return r.json()
    raise LLMError("API do Claude indisponível")


# ---------------------------------------------------------------------------------------------
# OpenAI compatível (Groq, Gemini, Ollama, OpenRouter)
# ---------------------------------------------------------------------------------------------
def to_openai(messages: list, system: list, tools: Optional[list]):
    sys_text = "\n\n".join(b["text"] for b in system if b.get("text")) if isinstance(system, list) else str(system or "")
    out = [{"role": "system", "content": sys_text}] if sys_text else []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
            continue
        if m["role"] == "assistant":
            text = "\n".join(b["text"] for b in c if b.get("type") == "text" and b.get("text"))
            calls = []
            for b in c:
                if b.get("type") != "tool_use":
                    continue
                call = {"id": b["id"], "type": "function",
                        "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                if isinstance(b.get("extra"), dict) and b["extra"]:
                    call["extra_content"] = b["extra"]      # Gemini 3: thought_signature precisa voltar igual
                calls.append(call)
            msg = {"role": "assistant", "content": text or (None if calls else "(ok)")}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        # usuário: resultados de ferramenta viram mensagens "tool" (logo depois do assistente), texto vira "user"
        for b in c:
            if b.get("type") == "tool_result":
                content = b.get("content")
                if isinstance(content, list):
                    content = "\n".join(x.get("text", "") for x in content if isinstance(x, dict))
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                            "content": ("ERRO: " if b.get("is_error") else "") + str(content or "")})
        text = "\n\n".join(b["text"] for b in c if b.get("type") == "text" and b.get("text"))
        if text:
            out.append({"role": "user", "content": text})
    oa_tools = None
    if tools:
        oa_tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                      "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
                    for t in tools]
    return out, oa_tools


def from_openai(data: dict) -> dict:
    """Resposta chat/completions -> formato Anthropic ({content, stop_reason, usage, model})."""
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = []
    text = msg.get("content")
    if isinstance(text, list):
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    if text and str(text).strip():
        content.append({"type": "text", "text": str(text).strip()})
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": args}
        block = {"type": "tool_use", "id": tc.get("id") or f"call_{os.urandom(4).hex()}_{i}",
                 "name": fn.get("name", ""), "input": args if isinstance(args, dict) else {}}
        if isinstance(tc.get("extra_content"), dict) and tc["extra_content"]:
            block["extra"] = tc["extra_content"]            # ex.: {"google": {"thought_signature": "..."}}
        content.append(block)
    u = data.get("usage") or {}
    return {"content": content, "model": data.get("model"),
            "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn",
            "usage": {"input_tokens": u.get("prompt_tokens") or 0, "output_tokens": u.get("completion_tokens") or 0}}


RETRY_STATUS = (429, 500, 502, 503, 504, 529)
RETRY_BUDGET = 35          # segundos esperando o mesmo modelo antes de desistir (ou trocar de modelo)


class LLMBusy(LLMError):
    """Provedor sobrecarregado ou limite por minuto — vale tentar outro modelo."""


def _err_msg(r) -> str:
    try:
        j = r.json()
        if isinstance(j, list) and j:
            j = j[0]
        err = j.get("error") if isinstance(j, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else err) or r.text
    except Exception:
        msg = r.text
    return str(msg)


def _busy_text(name: str, model: str, status: int, provider: str, wait: float = 0) -> str:
    if status == 429:
        return (f"{name} ({model}): limite do plano atingido (HTTP 429)"
                + (f". Aguarde {int(round(wait))} s" if wait else "")
                + (" — no plano grátis há limite por minuto e por dia" if PROVIDERS.get(provider, {}).get("free") else ""))
    return f"{name} ({model}) sobrecarregado (HTTP {status}) — costuma passar em alguns minutos"


GEMINI_SKIP_SIGNATURE = "skip_thought_signature_validator"


def _gemini_signatures(oa_msgs: list):
    """Gemini 3 exige thought_signature na 1ª chamada de ferramenta de cada passo. Chamadas vindas de outro
    provedor (ou de antes desta correção) não têm: usa o valor que o Google documenta para pular a validação."""
    for m in oa_msgs:
        calls = m.get("tool_calls") if m.get("role") == "assistant" else None
        if calls and not any(((c.get("extra_content") or {}).get("google") or {}).get("thought_signature") for c in calls):
            calls[0]["extra_content"] = {"google": {"thought_signature": GEMINI_SKIP_SIGNATURE}}


async def _openai(http, base_url, key, model, messages, system, tools, max_tokens, provider,
                  on_wait=None, budget: float = RETRY_BUDGET) -> dict:
    oa_msgs, oa_tools = to_openai(messages, system, tools)
    if provider == "gemini":
        _gemini_signatures(oa_msgs)
    else:                                  # outros provedores podem recusar campos desconhecidos
        for m in oa_msgs:
            for c in m.get("tool_calls") or []:
                c.pop("extra_content", None)
    body = {"model": model, "messages": oa_msgs, "max_tokens": max_tokens, "temperature": 0.2}
    if oa_tools:
        body["tools"] = oa_tools
        body["tool_choice"] = "auto"
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    url = base_url.rstrip("/") + "/chat/completions"
    name = PROVIDERS.get(provider, {}).get("label", provider).split(" (")[0]
    waited, bad_tool = 0.0, 0
    for attempt in range(8):
        try:
            r = await http.post(url, json=body, headers=headers, timeout=300 if provider == "ollama" else 120)
        except httpx.ConnectError as e:
            raise LLMError(f"Não consegui conectar em {url} ({e}). " +
                           ("O Ollama está rodando? (systemctl status ollama)" if provider == "ollama" else ""))
        except httpx.TimeoutException:
            raise LLMError(f"{name} ({model}) não respondeu a tempo")
        if r.status_code == 200:
            return from_openai(r.json())
        if r.status_code == 400 and "tool_use_failed" in r.text and bad_tool < 2:
            bad_tool += 1
            continue                       # Groq: o modelo gerou chamada de ferramenta malformada — tenta de novo
        if r.status_code in RETRY_STATUS:
            wait = None
            if r.status_code == 429:
                try:
                    wait = float(r.headers.get("retry-after"))
                except (TypeError, ValueError):
                    wait = None
            wait = max(1.0, wait if wait is not None else min(5.0 * (2 ** attempt), 30.0))
            if waited + wait > budget:
                raise LLMBusy(_busy_text(name, model, r.status_code, provider, wait if r.status_code == 429 else 0))
            if on_wait:
                what = "limite por minuto" if r.status_code == 429 else "provedor sobrecarregado"
                await on_wait(f"{name} ({model}): {what} — tentando de novo em {int(round(wait))} s")
            await asyncio.sleep(wait)
            waited += wait
            continue
        msg = _err_msg(r)
        if r.status_code == 404 and provider == "ollama":
            msg += f" — baixe o modelo no servidor: ollama pull {model}"
        raise LLMError(f"{name} HTTP {r.status_code}: {msg[:300]}")
    raise LLMBusy(f"{name} ({model}) indisponível no momento")


# ---------------------------------------------------------------------------------------------
async def call(cfg: dict, messages: list, system: list, tools: Optional[list] = None, max_tokens: int = 4096,
               http: Optional[httpx.AsyncClient] = None, on_wait=None) -> dict:
    """cfg = {provider, model, key, base_url, fallbacks?} -> resposta no formato Anthropic.
    Sobrecarga/limite: espera e tenta de novo; persistindo, tenta os modelos de cfg["fallbacks"]."""
    provider = cfg.get("provider") or "anthropic"
    own = http is None
    http = http or httpx.AsyncClient(timeout=300)
    try:
        if provider == "anthropic":
            if not cfg.get("key"):
                raise LLMError("Chave da API da Anthropic não configurada")
            return await _anthropic(http, cfg["key"], cfg["model"], messages, system, tools, max_tokens)
        base = cfg.get("base_url") or PROVIDERS.get(provider, {}).get("base_url")
        if not base:
            raise LLMError("URL da API não configurada")
        if PROVIDERS.get(provider, {}).get("key") and not cfg.get("key"):
            raise LLMError("Chave da API não configurada")
        if not cfg.get("model"):
            raise LLMError("Modelo não configurado")
        clean_tools = [{k: v for k, v in t.items() if k != "cache_control"} for t in tools] if tools else None
        models = [cfg["model"]] + [m for m in (cfg.get("fallbacks") or []) if m and m != cfg["model"]]
        first_err = None
        for i, model in enumerate(models):
            if i and on_wait:
                await on_wait(f"trocando para {model} enquanto {models[0]} está indisponível")
            try:
                return await _openai(http, base, cfg.get("key"), model, messages, system, clean_tools, max_tokens, provider,
                                     on_wait=on_wait, budget=RETRY_BUDGET if i == 0 else 20)
            except LLMBusy as e:
                first_err = first_err or e
                if i == len(models) - 1:
                    msg = str(first_err) + (". Os modelos reserva também estão indisponíveis" if i else "")
                    raise LLMBusy(msg + ("." if "Aguarde" in msg else ". Tente de novo em alguns minutos."))
        raise first_err or LLMError("sem modelo")
    finally:
        if own:
            await http.aclose()


async def list_models(cfg: dict) -> List[str]:
    provider = cfg.get("provider") or "anthropic"
    if provider == "anthropic":
        return PROVIDERS["anthropic"]["suggest"]
    base = (cfg.get("base_url") or PROVIDERS.get(provider, {}).get("base_url") or "").rstrip("/")
    headers = {"authorization": f"Bearer {cfg['key']}"} if cfg.get("key") else {}
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.get(base + "/models", headers=headers)
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json().get("data") or []
    ids = sorted({(m.get("id") or "").removeprefix("models/") for m in data if m.get("id")})
    return ids
