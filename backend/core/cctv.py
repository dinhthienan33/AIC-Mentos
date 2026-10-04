"""Group-N CCTV clock index.

Reads ``cams.json`` built by ``aic_tools/cctv``. Clock math matches ``t2.clock2frame``:
the displayed second starts at ``start_iso`` (the clock value at t=0) and the
returned frame is the middle of that second.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import re
import subprocess
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_CAMS = Path(os.getenv("CCTV_CAMS_PATH") or Path(__file__).resolve().parents[1] / "data" / "cctv" / "cams.json")
_CACHE_DIR = Path(os.getenv("CCTV_HIRES_CACHE") or Path(__file__).resolve().parents[1] / "data" / "cctv_hires")
_BUNNY_REF = "Referer: https://iframe.mediadelivery.net/\r\n"
_DAY = {"day", "ngay", "ngày"}
_NIGHT = {"night", "dem", "đêm"}


def cams_path() -> Path:
    raw = (os.getenv("CCTV_CAMS_PATH") or "").strip()
    return Path(raw) if raw else _DEFAULT_CAMS


@lru_cache(maxsize=1)
def load_cams() -> Dict[str, Dict[str, Any]]:
    path = cams_path()
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"CCTV index is not an object: {path}")
    return data


def reload_cams() -> Dict[str, Dict[str, Any]]:
    load_cams.cache_clear()
    return load_cams()


def _norm(text: str) -> str:
    folded = (text or "").replace("đ", "d").replace("Đ", "D")
    ascii_text = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", ascii_text.lower())


def _jmatch(query: str, record: Dict[str, Any]) -> float:
    name = _norm(f"{record.get('junction') or ''} {record.get('junction_raw') or ''}")
    tokens = _norm(query).split()
    if not tokens:
        return 1.0
    if all(token in name for token in tokens):
        return 1.0
    compact = name.replace(" ", "")
    needle = "".join(tokens)
    if not needle or not compact:
        return 0.0
    span = max(1, len(compact) - len(needle) + 1)
    return max(
        difflib.SequenceMatcher(None, needle, compact[i : i + len(needle)]).ratio()
        for i in range(span)
    )


def _video_key(video_id: str) -> str:
    key = (video_id or "").strip()
    cams = load_cams()
    if key in cams:
        return key
    swapped = key.replace("_", "-") if "_" in key else key.replace("-", "_")
    if swapped in cams:
        return swapped
    raise KeyError(key)


def get_cam(video_id: str) -> Dict[str, Any]:
    return load_cams()[_video_key(video_id)]


def _start(record: Dict[str, Any]) -> dt.datetime:
    return dt.datetime.fromisoformat(record["start_iso"])


def _fps(record: Dict[str, Any]) -> float:
    rate = float(record.get("fps") or 0)
    if rate <= 0:
        raise ValueError("video has no fps in the CCTV index")
    return rate


def parse_clock(text: str, record: Optional[Dict[str, Any]] = None) -> dt.datetime:
    """Parse ``HH:MM:SS``, ``DD/MM HH:MM:SS``, or ``DD/MM/YYYY HH:MM:SS``."""
    raw = (text or "").strip()
    match = re.match(
        r"(?:(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{4}))?\s+)?"
        r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.(\d+))?$",
        raw,
    )
    if not match:
        raise ValueError(f"bad clock {text}")
    day, month, year, hour, minute, second, _frac = match.groups()
    clock = dt.time(int(hour), int(minute), int(second or 0))
    if day:
        return dt.datetime(int(year or 2026), int(month), int(day), clock.hour, clock.minute, clock.second)
    if record is None or not record.get("start_iso"):
        raise ValueError("clock needs a date, or a video whose start date is known")
    start = _start(record)
    combined = dt.datetime.combine(start.date(), clock)
    if combined < start - dt.timedelta(hours=12):
        combined += dt.timedelta(days=1)
    return combined


def _video_url(video_id: str) -> str:
    try:
        from core.utils import get_video_url

        return get_video_url(video_id) or ""
    except Exception:
        return ""


def _ms(frame: int, fps: float) -> int:
    return int(round(frame / fps * 1000))


def public_cam(video_id: str, record: Dict[str, Any], match: Optional[float] = None) -> Dict[str, Any]:
    out = {
        "video_id": video_id,
        "junction": record.get("junction") or record.get("junction_raw"),
        "date": record.get("date"),
        "dates": record.get("dates") or ([record["date"]] if record.get("date") else []),
        "start": record.get("start"),
        "end": record.get("end"),
        "daynight": record.get("daynight"),
        "length": record.get("length"),
        "fps": record.get("fps"),
        "conf": record.get("conf"),
        "off_err": record.get("off_err"),
        "video_url": _video_url(video_id),
    }
    if match is not None:
        out["match"] = round(float(match), 3)
    return out


def clock_to_frame(video_id: str, clock_text: str) -> Dict[str, Any]:
    key = _video_key(video_id)
    record = load_cams()[key]
    if not record.get("start_iso"):
        raise ValueError(f"{key} has no clock anchor")
    fps = _fps(record)
    moment = parse_clock(clock_text, record)
    elapsed = (moment - _start(record)).total_seconds() + 0.5
    frame = int(round(elapsed * fps))
    return {
        **public_cam(key, record),
        "clock": moment.strftime("%d/%m/%Y %H:%M:%S"),
        "frame": frame,
        "ms": _ms(frame, fps),
        "t": round(elapsed, 3),
        "lo_frame": int(round((elapsed - 0.5) * fps)),
        "hi_frame": int(round((elapsed + 0.5) * fps)) - 1,
        "inside": 0 <= elapsed <= float(record.get("length") or 0),
    }


def frame_to_clock(video_id: str, frame: int) -> Dict[str, Any]:
    key = _video_key(video_id)
    record = load_cams()[key]
    if not record.get("start_iso"):
        raise ValueError(f"{key} has no clock anchor")
    fps = _fps(record)
    moment = _start(record) + dt.timedelta(seconds=frame / fps)
    return {
        **public_cam(key, record),
        "frame": int(frame),
        "ms": _ms(int(frame), fps),
        "clock": moment.strftime("%d/%m/%Y %H:%M:%S"),
        "inside": 0 <= frame / fps <= float(record.get("length") or 0),
    }


def _date_ok(record: Dict[str, Any], date: str) -> bool:
    parts = date.split("/")
    if len(parts) < 2:
        return False
    wanted = f"{int(parts[0]):02d}/{int(parts[1]):02d}"
    if len(parts) > 2 and parts[2]:
        wanted = f"{wanted}/{parts[2]}"
    dates = record.get("dates") or ([record["date"]] if record.get("date") else [])
    sliced = [item[: len(wanted)] if len(parts) > 2 else item[:5] for item in dates]
    return wanted in sliced


def _daynight(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    token = value.strip().lower()
    if token in _DAY:
        return "day"
    if token in _NIGHT:
        return "night"
    raise ValueError("daynight must be day or night")


def find_cameras(
    text: str = "",
    date: Optional[str] = None,
    daynight: Optional[str] = None,
    threshold: float = 0.75,
) -> List[Dict[str, Any]]:
    period = _daynight(daynight)
    found: List[Dict[str, Any]] = []
    for video_id, record in sorted(load_cams().items()):
        if not record.get("start"):
            if text and _jmatch(text, record) >= threshold and not date and not period:
                found.append(public_cam(video_id, record, 1.0))
            continue
        score = _jmatch(text, record)
        if score < threshold:
            continue
        if date and not _date_ok(record, date):
            continue
        if period and record.get("daynight") != period:
            continue
        found.append(public_cam(video_id, record, score))
    found.sort(key=lambda item: (-float(item.get("match") or 0), item["video_id"]))
    return found


def cameras_at(when: str, text: Optional[str] = None) -> List[Dict[str, Any]]:
    raw = (when or "").strip()
    dated = re.match(r"\d{1,2}[/.-]\d{1,2}", raw)
    moment = parse_clock(raw if dated else f"01/01/1900 {raw}")
    found: List[Dict[str, Any]] = []
    for video_id, record in sorted(load_cams().items()):
        if not record.get("start"):
            continue
        if text and _jmatch(text, record) < 0.75:
            continue
        start = _start(record)
        stamped = moment if moment.year > 1900 else dt.datetime.combine(start.date(), moment.time())
        if moment.year == 1900 and stamped < start - dt.timedelta(hours=12):
            stamped += dt.timedelta(days=1)
        if start <= stamped <= start + dt.timedelta(seconds=float(record.get("length") or 0) + 1):
            shot = clock_to_frame(video_id, stamped.strftime("%d/%m/%Y %H:%M:%S"))
            found.append(shot)
    return found


def _bunny_mp4(video_id: str, res: str) -> Tuple[str, str]:
    from bunny_client import cdn_host, lookup_video

    hit = lookup_video(video_id)
    if not hit or not hit.get("guid"):
        raise FileNotFoundError(f"no Bunny video for {video_id}")
    chosen = res if res else "1080p"
    url = f"https://{cdn_host()}/{hit['guid']}/play_{chosen}.mp4"
    return url, chosen


def hires_path(video_id: str, frame: int, res: str = "1080p") -> Path:
    key = _video_key(video_id)
    record = load_cams()[key]
    fps = _fps(record)
    frame_no = int(frame)
    if frame_no < 0:
        raise ValueError("frame must be >= 0")
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = _CACHE_DIR / f"{key}_{frame_no}_{res}.jpg"
    if target.is_file() and target.stat().st_size > 1000:
        return target

    seconds = frame_no / fps
    resolutions = [res] if res else ["1080p"]
    if "720p" not in resolutions:
        resolutions.append("720p")
    last_error = "could not grab frame"
    for chosen in resolutions:
        url, used_res = _bunny_mp4(key, chosen)
        target = _CACHE_DIR / f"{key}_{frame_no}_{used_res}.jpg"
        if target.is_file() and target.stat().st_size > 1000:
            return target
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-headers",
            _BUNNY_REF,
            "-ss",
            f"{seconds:.3f}",
            "-i",
            url,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(target),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=25, check=False)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"ffmpeg timed out for {key} frame {frame_no}") from exc
        if target.is_file() and target.stat().st_size > 1000:
            return target
        target.unlink(missing_ok=True)
        last_error = f"could not grab {used_res} frame for {key}"
    raise RuntimeError(last_error)
