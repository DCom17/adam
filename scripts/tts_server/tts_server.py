"""
Jarvis Kokoro TTS service.

Local-only FastAPI service that turns text into Jarvis's voice using the Kokoro
ONNX model (no PyTorch, no GPU required — real-time on CPU). The main voice
server proxies to this on 127.0.0.1:8001; it is never exposed to the network.

    POST /tts  {text, voice?, speed?}  ->  audio/wav  (24 kHz mono PCM16)
    GET  /ping                         ->  health check

Set up by INSTALL-VOICE.cmd, which creates this folder's .venv and downloads
kokoro-v1.0.onnx + voices-v1.0.bin alongside this file.
"""

import asyncio
import io
from contextlib import asynccontextmanager
from pathlib import Path

import onnxruntime
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from kokoro_onnx import Kokoro

HERE = Path(__file__).resolve().parent
MODEL = HERE / "kokoro-v1.0.onnx"
VOICES = HERE / "voices-v1.0.bin"

# Jarvis register: British male. bm_daniel is the Jarvis voice.
DEFAULT_VOICE = "bm_daniel"
DEFAULT_LANG = "en-gb"
DEFAULT_SPEED = 1.0

# CPU only — real-time on a normal laptop, and avoids GPU-provider quirks
# (DirectML loads but throws on Kokoro's ConvTranspose op at inference).
PROVIDERS = ["CPUExecutionProvider"]

_kokoro: "Kokoro | None" = None
_active_provider = "unknown"


def get_kokoro() -> "Kokoro":
    """Lazy singleton — the ONNX model loads once and stays resident."""
    global _kokoro, _active_provider
    if _kokoro is None:
        session = onnxruntime.InferenceSession(str(MODEL), providers=PROVIDERS)
        _active_provider = session.get_providers()[0]
        _kokoro = Kokoro.from_session(session, str(VOICES))
    return _kokoro


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the model at boot so the first real request isn't slow.
    get_kokoro()
    yield


app = FastAPI(title="Jarvis Kokoro TTS", lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str
    voice: "str | None" = None
    speed: "float | None" = None


@app.get("/ping")
async def ping():
    return {"status": "ok", "voice": DEFAULT_VOICE, "provider": _active_provider}


@app.post("/tts")
async def tts(body: TTSRequest):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")
    voice = body.voice or DEFAULT_VOICE
    speed = body.speed or DEFAULT_SPEED

    def synth():
        # CPU-bound; run off the event loop so the server stays responsive.
        samples, sample_rate = get_kokoro().create(
            text, voice=voice, speed=speed, lang=DEFAULT_LANG
        )
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    try:
        wav = await asyncio.to_thread(synth)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS failed: {e}")

    return Response(content=wav, media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
