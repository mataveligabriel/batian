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
               "model": "gemini-2.5-flash", "key": True, "free": True,
               "hint": "Chave em aistudio.google.com. No plano grátis o Google pode usar as conversas para melhorar os modelos.",
               "suggest": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"]},
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
async def _anthropic(http, key, model, messages, system, tools, max_tokens) -> dict:
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
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                     for b in c if b.get("type") == "tool_use"]
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
        content.append({"type": "tool_use", "id": tc.get("id") or f"call_{os.urandom(4).hex()}_{i}",
                        "name": fn.get("name", ""), "input": args if isinstance(args, dict) else {}})
    u = data.get("usage") or {}
    return {"content": content, "model": data.get("model"),
            "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn",
            "usage": {"input_tokens": u.get("prompt_tokens") or 0, "output_tokens": u.get("completion_tokens") or 0}}


async def _openai(http, base_url, key, model, messages, system, tools, max_tokens, provider) -> dict:
    oa_msgs, oa_tools = to_openai(messages, system, tools)
    body = {"model": model, "messages": oa_msgs, "max_tokens": max_tokens, "temperature": 0.2}
    if oa_tools:
        body["tools"] = oa_tools
        body["tool_choice"] = "auto"
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    url = base_url.rstrip("/") + "/chat/completions"
    name = PROVIDERS.get(provider, {}).get("label", provider).split(" (")[0]
    for attempt in range(3):
        try:
            r = await http.post(url, json=body, headers=headers, timeout=300 if provider == "ollama" else 120)
        except httpx.ConnectError as e:
            raise LLMError(f"Não consegui conectar em {url} ({e}). " +
                           ("O Ollama está rodando? (docker compose --profile ollama up -d)" if provider == "ollama" else ""))
        if r.status_code == 429:
            wait = r.headers.get("retry-after")
            try:
                wait = float(wait)
            except (TypeError, ValueError):
                wait = 4.0 * (attempt + 1)
            if attempt < 2 and wait <= 20:
                await asyncio.sleep(wait)
                continue
            raise LLMError(f"{name}: limite do plano atingido (HTTP 429). Aguarde {int(wait)} s e tente de novo"
                           + (" — no plano grátis há limite por minuto e por dia." if PROVIDERS.get(provider, {}).get("free") else "."))
        if r.status_code == 400 and "tool_use_failed" in r.text and attempt < 2:
            continue                       # Groq: o modelo gerou chamada de ferramenta malformada — tenta de novo
        if r.status_code in (500, 502, 503) and attempt < 2:
            await asyncio.sleep(3 * (attempt + 1))
            continue
        if r.status_code != 200:
            try:
                j = r.json()
                err = j.get("error")
                msg = (err.get("message") if isinstance(err, dict) else err) or r.text
            except Exception:
                msg = r.text
            if r.status_code == 404 and provider == "ollama":
                msg += f" — baixe o modelo no servidor: docker compose exec ollama ollama pull {model}"
            raise LLMError(f"{name} HTTP {r.status_code}: {str(msg)[:300]}")
        return from_openai(r.json())
    raise LLMError(f"{name} indisponível")


# ---------------------------------------------------------------------------------------------
async def call(cfg: dict, messages: list, system: list, tools: Optional[list] = None, max_tokens: int = 4096,
               http: Optional[httpx.AsyncClient] = None) -> dict:
    """cfg = {provider, model, key, base_url} -> resposta no formato Anthropic."""
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
        return await _openai(http, base, cfg.get("key"), cfg["model"], messages, system, clean_tools, max_tokens, provider)
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
