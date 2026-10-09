"""Roteiros automatizados: uma sequência de passos, cada um com um equipamento e os comandos dele.

Ex.: "ATIVAR ROTA LIMOEIRO" = no S6730-CASTELO dá undo shutdown na subinterface e, em seguida, no S6730-VENDA-NOVA
dá shutdown na porta. Roda pelo BastiON (tela Execução em Lote → Roteiros) ou pelo bot do Telegram, sempre com
confirmação. Os passos são em ordem e param no primeiro erro — o passo seguinte não roda se o anterior falhou.
"""
import re
import unicodedata
from typing import Awaitable, Callable, Dict, List, Optional

MAX_STEPS = 20
MAX_LINES = 200

# gravar a configuração no fim de cada equipamento (opcional, por roteiro)
SAVE_COMMANDS: Dict[str, List[str]] = {
    "huawei": ["return", "save"],                 # pergunta [Y/N]: respondido sozinho
    "zte": ["end", "write"],
    "cisco": ["end", "write memory"],
    "datacom": ["end", "copy running-config startup-config"],
    "juniper": ["commit and-quit"],
    "mikrotik": [],                                  # RouterOS grava sozinho
}

ERR_RE = re.compile(
    r"^\s*Error\s*[:\d]|%\s*(error|invalid|unrecognized|incomplete|ambiguous|unknown|code)|"
    r"unrecognized command|wrong parameter|incomplete command|too many parameters|"
    r"\^\s*$|syntax error|unknown command|invalid input|not\s+exist|failure:", re.I | re.M)
CONFIRM_RE = re.compile(r"(\[y/n\]|\(y/n\)|\[yes/no\]|yes/no|y/n|continue\?)\s*\]?\s*:?\s*$", re.I)


class RunbookError(ValueError):
    pass


def norm_name(s: str) -> str:
    """Comparação de nome sem acento, caixa ou espaço extra ('ativar rota limoeiro' == 'ATIVAR ROTA LIMOEIRO')."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def command_lines(text: str) -> List[str]:
    """Linhas que vão para o equipamento: tira as vazias e os separadores '#' / '---' (comentário)."""
    out = []
    for raw in str(text or "").replace("\r", "").split("\n"):
        line = raw.rstrip()
        s = line.strip()
        if not s or re.fullmatch(r"[#!]+|-{2,}|={2,}", s):
            continue
        out.append(line)
    return out


def clean(data: dict) -> dict:
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()
    if not name or len(name) > 60 or re.search(r"[<>\r\n]", name):
        raise RunbookError("Dê um nome ao roteiro (até 60 caracteres)")
    desc = re.sub(r"[<>]", "", str(data.get("description") or "")).strip()[:300]
    steps = []
    for i, st in enumerate(data.get("steps") or [], 1):
        st = st or {}
        dev = str(st.get("device_id") or "").strip()
        if not dev:
            raise RunbookError(f"Passo {i}: escolha o equipamento")
        cmds = str(st.get("commands") or "").replace("\r", "")
        lines = command_lines(cmds)
        if not lines:
            raise RunbookError(f"Passo {i}: informe os comandos")
        if len(lines) > MAX_LINES:
            raise RunbookError(f"Passo {i}: no máximo {MAX_LINES} linhas")
        steps.append({"device_id": dev, "commands": cmds.strip()[:20000]})
    if not steps:
        raise RunbookError("Adicione ao menos um passo")
    if len(steps) > MAX_STEPS:
        raise RunbookError(f"No máximo {MAX_STEPS} passos")
    return {"name": name, "description": desc, "steps": steps, "save": bool(data.get("save")),
            "telegram": bool(data.get("telegram", True)), "shared": bool(data.get("shared"))}


async def run(rb: dict, get_device: Callable[[str], Awaitable[Optional[dict]]], connect: Callable[[dict], Awaitable],
              on_step: Optional[Callable[[int, dict], Awaitable]] = None) -> dict:
    """Executa os passos em ordem; para no primeiro passo com erro. Devolve {ok, steps:[...]}."""
    results: List[dict] = []
    stopped = False
    for i, st in enumerate(rb.get("steps") or []):
        dev = await get_device(st["device_id"])
        r = {"index": i + 1, "device_id": st["device_id"], "device": (dev or {}).get("name") or "?",
             "host": (dev or {}).get("host") or "", "ok": False, "skipped": stopped, "failed_command": None,
             "error": None, "output": ""}
        if stopped:
            results.append(r)
            continue
        if on_step:
            await on_step(i + 1, r)
        if not dev:
            r["error"] = "Equipamento do passo não existe mais (apagado ou de outro usuário)"
            results.append(r)
            stopped = True
            continue
        lines = command_lines(st["commands"])
        if rb.get("save"):
            lines += SAVE_COMMANDS.get(dev.get("device_type") or "", [])
        out_all = []
        try:
            cli = await connect(dev)
            try:
                sess = await cli.shell()
                try:
                    for cmd in lines:
                        out = await sess.run(cmd, timeout=90)
                        if CONFIRM_RE.search(out.strip()[-60:]):
                            out += await sess.run("Y" if (dev.get("device_type") == "huawei") else "yes", timeout=120)
                        out_all.append(out)
                        body = out.replace(cmd.strip(), "", 1)
                        if ERR_RE.search(body):
                            r["failed_command"] = cmd.strip()
                            break
                finally:
                    try:
                        await sess.close()
                    except Exception:
                        pass
            finally:
                await cli.close()
        except Exception as e:
            r["error"] = (str(e) or type(e).__name__)[:300]
        r["output"] = "".join(out_all)[-8000:]
        r["ok"] = not r["error"] and not r["failed_command"]
        if not r["ok"]:
            stopped = True
        results.append(r)
    return {"ok": all(x["ok"] for x in results), "steps": results}


def summary(rb: dict, res: dict) -> str:
    """Texto curto do resultado (Telegram)."""
    lines = [("✅" if res["ok"] else "❌") + f" {rb['name']}"]
    for s in res["steps"]:
        if s["skipped"]:
            lines.append(f"⏭ {s['index']}. {s['device']} — não executado (passo anterior falhou)")
        elif s["ok"]:
            lines.append(f"✔️ {s['index']}. {s['device']} — ok")
        else:
            why = s["error"] or f"recusou: {s['failed_command']}"
            tail = (s["output"] or "").strip().splitlines()[-3:]
            lines.append(f"✖️ {s['index']}. {s['device']} — {why}" + ("\n   " + "\n   ".join(tail) if tail and not s["error"] else ""))
    return "\n".join(lines)
