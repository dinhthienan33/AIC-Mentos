"""Transcribe a short clip with VinAI PhoWhisper (Vietnamese ASR)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

_LOCK = threading.Lock()
_MODEL = None
_PROCESSOR = None
_MODEL_NAME = None
_DEVICE = "cpu"
_DTYPE = None

_PHO = {
    "tiny": "vinai/PhoWhisper-tiny",
    "base": "vinai/PhoWhisper-base",
    "small": "vinai/PhoWhisper-small",
    "medium": "vinai/PhoWhisper-medium",
    "large": "vinai/PhoWhisper-large",
}
_DEFAULT = "vinai/PhoWhisper-medium"
_AUDIO_SUFFIXES = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4", ".mpeg", ".mpga", ".flac", ".aac"}


def _model_id() -> str:
    raw = (os.getenv("ASR_WHISPER_MODEL") or _DEFAULT).strip() or _DEFAULT
    if raw in _PHO:
        return _PHO[raw]
    if "/" not in raw and not raw.startswith("vinai/"):
        return _DEFAULT
    return raw


def _get_model():
    global _MODEL, _PROCESSOR, _MODEL_NAME, _DEVICE, _DTYPE
    name = _model_id()
    if _MODEL is not None and _MODEL_NAME == name:
        return _MODEL, _PROCESSOR
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

    _DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    _DTYPE = torch.float16 if _DEVICE == "cuda" else torch.float32
    processor = AutoProcessor.from_pretrained(name)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(name, dtype=_DTYPE, low_cpu_mem_usage=True)
    model.to(_DEVICE)
    model.eval()
    _MODEL = model
    _PROCESSOR = processor
    _MODEL_NAME = name
    return model, processor


def _to_16k_mono(src: str) -> str:
    dest = src + ".16k.wav"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", "16000", "-f", "wav", dest],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not os.path.isfile(dest):
        detail = (proc.stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise ValueError(detail or "Không đọc được file âm thanh")
    return dest


def _read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as handle:
        pcm = handle.readframes(handle.getnframes())
        width = handle.getsampwidth()
    if width != 2:
        raise ValueError("ffmpeg không xuất được wav 16-bit")
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _transcribe_chunks(audio: np.ndarray) -> str:
    import torch

    model, processor = _get_model()
    prompt = processor.get_decoder_prompt_ids(language="vi", task="transcribe")
    step = 16000 * 30
    parts = []
    for start in range(0, max(len(audio), 1), step):
        chunk = audio[start : start + step]
        if chunk.size < 1600:
            continue
        features = processor(chunk, sampling_rate=16000, return_tensors="pt").input_features
        features = features.to(device=_DEVICE, dtype=_DTYPE)
        with torch.inference_mode():
            ids = model.generate(features, forced_decoder_ids=prompt, max_new_tokens=444)
        text = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def transcribe_audio(data: bytes, filename: str = "audio.webm", language: Optional[str] = None) -> Dict[str, Any]:
    if not data:
        raise ValueError("File âm thanh trống")
    suffix = Path(filename or "audio.webm").suffix.lower()
    if suffix not in _AUDIO_SUFFIXES:
        suffix = ".webm"

    started = time.perf_counter()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        src = handle.name
    wav = None
    try:
        wav = _to_16k_mono(src)
        audio = _read_wav(wav)
        with _LOCK:
            text = _transcribe_chunks(audio)
    finally:
        for path in (src, wav):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    return {
        "text": text,
        "language": "vi",
        "model": _MODEL_NAME or _model_id(),
        "seconds": round(time.perf_counter() - started, 3),
        "duration": round(len(audio) / 16000.0, 3) if "audio" in locals() else 0.0,
    }
