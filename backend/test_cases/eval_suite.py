"""Score retrieval predictions against the AIC test suite.

Expected prediction item:
  {"video_id": "L21_V001", "frame_idx": 123, "answer": "optional for QA"}
TRAKE predictions may use "frames": [e1, e2, ...].
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_SUITE = Path(__file__).resolve().parent / "test_suite.json"


def load_suite(path: Path = DEFAULT_SUITE) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_queries(
    suite: dict[str, Any],
    *,
    source: Optional[str] = None,
    qtype: Optional[str] = None,
    require_gt: bool = True,
) -> list[dict[str, Any]]:
    out = []
    for q in suite.get("queries", []):
        if source and q.get("source") != source:
            continue
        if qtype and q.get("type") != qtype:
            continue
        if require_gt and not q.get("has_gt"):
            continue
        out.append(q)
    return out


def _norm_answer(value: Optional[str]) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().replace(",", ".").split())


def score_kis(pred: Iterable[dict[str, Any]], case: dict[str, Any], frame_window: int = 25) -> dict[str, Any]:
    """Hit if any predicted (video, frame) is within frame_window of a GT pair."""
    gt_pairs = {(g["video_id"], g["frame_idx"]) for g in case["gt"] if g.get("video_id") and g.get("frame_idx") is not None}
    gt_videos = set(case.get("video_ids") or [])
    rank_video = None
    rank_frame = None
    for i, item in enumerate(pred, 1):
        vid = item.get("video_id")
        frame = item.get("frame_idx")
        if rank_video is None and vid in gt_videos:
            rank_video = i
        if vid is None or frame is None:
            continue
        for gvid, gframe in gt_pairs:
            if vid == gvid and abs(int(frame) - int(gframe)) <= frame_window:
                rank_frame = i
                break
        if rank_frame is not None:
            break
    return {
        "id": case["id"],
        "type": "KIS",
        "video_hit": rank_video is not None,
        "frame_hit": rank_frame is not None,
        "rank_video": rank_video,
        "rank_frame": rank_frame,
    }


def score_trake(pred: Iterable[dict[str, Any]], case: dict[str, Any], frame_window: int = 25) -> dict[str, Any]:
    """Hit if predicted video matches and each event frame is near a GT sequence."""
    gt_seqs = [
        (g["video_id"], g["frames"])
        for g in case["gt"]
        if g.get("video_id") and g.get("frames")
    ]
    best = None
    for i, item in enumerate(pred, 1):
        vid = item.get("video_id")
        frames = item.get("frames") or ([item["frame_idx"]] if item.get("frame_idx") is not None else [])
        for gvid, gframes in gt_seqs:
            if vid != gvid:
                continue
            if not frames:
                hit = False
            elif len(frames) == len(gframes):
                hit = all(abs(int(a) - int(b)) <= frame_window for a, b in zip(frames, gframes))
            else:
                hit = any(abs(int(frames[0]) - int(b)) <= frame_window for b in gframes)
            if hit:
                best = i
                break
        if best is not None:
            break
    return {
        "id": case["id"],
        "type": "TRAKE",
        "video_hit": any(item.get("video_id") in set(case.get("video_ids") or []) for item in pred),
        "sequence_hit": best is not None,
        "rank": best,
    }


def score_qa(pred: Iterable[dict[str, Any]], case: dict[str, Any], frame_window: int = 25) -> dict[str, Any]:
    answers = {_norm_answer(a) for a in case.get("answers") or []}
    gt_pairs = {(g["video_id"], g["frame_idx"]) for g in case["gt"] if g.get("video_id") and g.get("frame_idx") is not None}
    rank_answer = rank_video = rank_frame = None
    for i, item in enumerate(pred, 1):
        if rank_answer is None and _norm_answer(item.get("answer")) in answers and answers:
            rank_answer = i
        vid, frame = item.get("video_id"), item.get("frame_idx")
        if rank_video is None and vid in set(case.get("video_ids") or []):
            rank_video = i
        if vid is None or frame is None:
            continue
        if any(vid == gvid and abs(int(frame) - int(gframe)) <= frame_window for gvid, gframe in gt_pairs):
            rank_frame = i if rank_frame is None else rank_frame
    return {
        "id": case["id"],
        "type": "QA",
        "answer_hit": rank_answer is not None,
        "video_hit": rank_video is not None,
        "frame_hit": rank_frame is not None,
        "rank_answer": rank_answer,
        "rank_video": rank_video,
        "rank_frame": rank_frame,
    }


def score_case(pred: Iterable[dict[str, Any]], case: dict[str, Any], frame_window: int = 25) -> dict[str, Any]:
    preds = list(pred)
    qtype = case.get("type")
    if qtype == "TRAKE":
        return score_trake(preds, case, frame_window=frame_window)
    if qtype == "QA":
        return score_qa(preds, case, frame_window=frame_window)
    return score_kis(preds, case, frame_window=frame_window)
