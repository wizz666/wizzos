"""Text till tal: AI-tjänstens röst (Gemini via LiteLLM) i första hand, Piper lokalt som reserv.

Rösten (ca 60 MB) hämtas från Piper-projektets röstbibliotek första gången den behövs och sparas
i /DATA/WizzOS/voices. Den hålls i minnet medan den används och släpps efter 10 minuter utan
användning, så att servern inte bär på ~300 MB i onödan.
"""
from __future__ import annotations

import io
import os
import re
import threading
import time
import urllib.request

from . import config

VOICES = {
    "sv": "sv/sv_SE/nst/medium/sv_SE-nst-medium",
    "en": "en/en_US/lessac/medium/en_US-lessac-medium",
}
VOICE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
MAX_CHARS = 900          # längre svar läses bara i början; hela texten finns ändå i chatten
IDLE_SECONDS = 600

_lock = threading.Lock()
_loaded: dict = {"lang": None, "voice": None, "used": 0.0}


def _voice_dir() -> str:
    d = config.HOME / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _download(lang: str) -> str:
    rel = VOICES[lang]
    path = os.path.join(_voice_dir(), rel.rsplit("/", 1)[-1] + ".onnx")
    for suffix in (".json", ""):  # modellen sist: finns .onnx så är allt klart
        target = path + suffix
        if os.path.exists(target):
            continue
        tmp = target + ".part"
        with urllib.request.urlopen(VOICE_BASE + rel + ".onnx" + suffix, timeout=120) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
        if os.path.getsize(tmp) < (1_000_000 if suffix == "" else 100):
            os.remove(tmp)
            raise RuntimeError("Rösten kunde inte hämtas.")
        os.replace(tmp, target)
    return path


def _voice(lang: str):
    from piper import PiperVoice
    if _loaded["lang"] != lang or _loaded["voice"] is None:
        _loaded.update(voice=None, lang=None)
        _loaded.update(voice=PiperVoice.load(_download(lang)), lang=lang)
    _loaded["used"] = time.time()
    return _loaded["voice"]


def _release_when_idle() -> None:
    while True:
        time.sleep(60)
        with _lock:
            if _loaded["voice"] is not None and time.time() - _loaded["used"] > IDLE_SECONDS:
                _loaded.update(voice=None, lang=None)


threading.Thread(target=_release_when_idle, daemon=True, name="speech-idle").start()


def speakable(text: str) -> str:
    """Gör ett chattsvar läsbart högt: inga länkar, kodblock, markdown-tecken eller emojis."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[\[[^\]]*\]\]", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[`*_#>|~]", " ", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", ". ", text.strip())
    text = re.sub(r"([.!?])\.+", r"\1", text)
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        text = cut[: max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), 200) + 1]
    return text.strip()


def _ogg(pcm, rate: int) -> bytes:
    """Mono-ljud (float32) -> Ogg/Opus i 48 kHz, som Telegram visar som röstmeddelande."""
    import numpy as np
    import soundfile as sf
    n = int(len(pcm) * 48000 / rate)
    audio = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, 48000, format="OGG", subtype="OPUS")
    return buf.getvalue()


def _piper(words: str, lang: str) -> bytes:
    import numpy as np
    with _lock:
        voice = _voice(lang)
        chunks = list(voice.synthesize(words))
        _loaded["used"] = time.time()
    if not chunks:
        raise RuntimeError("Rösten gav inget ljud.")
    pcm = np.frombuffer(b"".join(c.audio_int16_bytes for c in chunks), dtype=np.int16).astype(np.float32) / 32768
    return _ogg(pcm, chunks[0].sample_rate)


def _remote(words: str) -> bytes:
    """AI-tjänstens röst (Gemini via LiteLLM) – uttalar svenska mycket bättre än Piper."""
    import soundfile as sf

    from . import ai
    pcm, rate = sf.read(io.BytesIO(ai.synthesize(words)), dtype="float32")
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1)
    if not len(pcm):
        raise RuntimeError("Röstmodellen gav tomt ljud.")
    return _ogg(pcm, rate)


def speak(text: str, lang: str | None = None) -> bytes:
    """Returnerar ett röstmeddelande (Ogg/Opus). Provar AI-tjänstens röst först, Piper lokalt som reserv."""
    from . import i18n
    lang = lang if lang in VOICES else (i18n.server_lang() if i18n.server_lang() in VOICES else "sv")
    words = speakable(text)
    if not words:
        raise ValueError("Inget att läsa upp.")
    try:
        return _remote(words)
    except Exception as e:  # noqa: BLE001 – kvoten slut, nätet nere …: läs upp lokalt i stället
        print(f"speech: AI-rösten gick inte ({type(e).__name__}: {str(e)[:120]}) – använder Piper")
    return _piper(words, lang)
