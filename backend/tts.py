"""Voz do servidor para os alertas do mapa (igual em qualquer aparelho): Piper, offline e grátis.

O modelo de voz (~60 MB) é baixado na primeira vez para /data/tts e fica guardado. Cada frase gerada vira um .wav
em cache (frases repetidas não são geradas de novo). Se o Piper não estiver instalado, quem chama recebe TTSError
e o navegador volta para a voz do próprio aparelho.
"""
import asyncio
import hashlib
import io
import logging
import os
import wave
from pathlib import Path

import httpx

log = logging.getLogger("tts")
DATA = Path(os.environ.get("TTS_DIR", "/data/tts"))
BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR"

# vozes masculinas em português do Brasil do projeto Piper
VOICES = {
    "faber": {"label": "Faber (masculina)", "path": "faber/medium/pt_BR-faber-medium"},
    "cadu": {"label": "Cadu (masculina)", "path": "cadu/medium/pt_BR-cadu-medium"},
    "jeff": {"label": "Jeff (masculina)", "path": "jeff/medium/pt_BR-jeff-medium"},
}
DEFAULT = "faber"
MAX_CACHE = 400

_loaded: dict = {}
_lock = asyncio.Lock()


class TTSError(Exception):
    pass


def available() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except Exception:
        return False


async def _download(name: str) -> Path:
    v = VOICES[name]
    d = DATA / "models"
    d.mkdir(parents=True, exist_ok=True)
    model = d / f"{name}.onnx"
    conf = d / f"{name}.onnx.json"
    if model.exists() and conf.exists() and model.stat().st_size > 1_000_000:
        return model
    log.info(f"baixando a voz {name} do Piper…")
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as http:
        for url, dest in ((f"{BASE}/{v['path']}.onnx.json", conf), (f"{BASE}/{v['path']}.onnx", model)):
            r = await http.get(url)
            if r.status_code != 200:
                raise TTSError(f"não consegui baixar a voz {name} (HTTP {r.status_code})")
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(r.content)
            tmp.replace(dest)
    return model


def _synth(voice, text: str, length_scale: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        try:                                    # piper-tts 1.3+
            from piper import SynthesisConfig
            voice.synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=length_scale))
        except ImportError:                     # piper-tts 1.2
            voice.synthesize(text, wf, length_scale=length_scale)
    return buf.getvalue()


async def speak(text: str, name: str = DEFAULT, rate: float = 1.0) -> bytes:
    """Devolve o WAV da frase (gera e guarda em cache)."""
    text = " ".join(str(text or "").split())[:240]
    if not text:
        raise TTSError("texto vazio")
    name = name if name in VOICES else DEFAULT
    rate = max(0.5, min(1.6, float(rate or 1)))
    if not available():
        raise TTSError("Piper não está instalado no servidor (rode o update.sh)")
    key = hashlib.sha256(f"{name}|{rate:.2f}|{text}".encode()).hexdigest()[:32]
    cache = DATA / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{key}.wav"
    if f.exists():
        return f.read_bytes()
    async with _lock:                           # uma geração por vez (CPU) e um download por vez
        if f.exists():
            return f.read_bytes()
        if name not in _loaded:
            model = await _download(name)
            from piper import PiperVoice
            _loaded[name] = await asyncio.to_thread(PiperVoice.load, str(model))
        data = await asyncio.to_thread(_synth, _loaded[name], text, 1.0 / rate)
    f.write_bytes(data)
    try:                                        # cache não cresce sem fim
        files = sorted(cache.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        for old in files[:-MAX_CACHE]:
            old.unlink(missing_ok=True)
    except Exception:
        pass
    return data
