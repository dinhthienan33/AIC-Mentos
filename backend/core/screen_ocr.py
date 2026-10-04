"""OCR a screenshot of the contest prompt. Local Tesseract, Vietnamese + English."""
from __future__ import annotations

import io
import subprocess
import time
from typing import Any, Dict

from PIL import Image, ImageOps

_MAX_EDGE = 2400


def recognize_screen(data: bytes) -> Dict[str, Any]:
    if not data:
        raise ValueError("Ảnh trống")
    started = time.perf_counter()
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
    except Exception as exc:
        raise ValueError("Không đọc được file ảnh") from exc

    width, height = image.size
    long_edge = max(width, height)
    if long_edge > _MAX_EDGE:
        scale = _MAX_EDGE / long_edge
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.LANCZOS)
    elif long_edge < 900:
        image = image.resize((width * 2, height * 2), Image.Resampling.LANCZOS)

    payload = io.BytesIO()
    image.save(payload, format="PNG")
    try:
        proc = subprocess.run(
            [
                "tesseract",
                "stdin",
                "stdout",
                "-l",
                "vie+eng",
                "--psm",
                "6",
                "-c",
                "preserve_interword_spaces=1",
            ],
            input=payload.getvalue(),
            capture_output=True,
            timeout=20,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Tesseract chưa được cài") from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("OCR quá 20 giây") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "Tesseract thất bại")

    text = _clean(proc.stdout.decode("utf-8", errors="replace"))
    return {
        "text": text,
        "seconds": round(time.perf_counter() - started, 3),
        "width": width,
        "height": height,
    }


def _clean(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    cleaned = []
    blank = 0
    for line in lines:
        if line.strip():
            blank = 0
            cleaned.append(line)
        else:
            blank += 1
            if blank == 1:
                cleaned.append("")
    return "\n".join(cleaned)
