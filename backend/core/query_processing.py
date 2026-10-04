"""LLM helpers for query translation and temporal event extraction (OpenAI)."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI

from .prompts import QUERY_EXPANSION, QUERY_EXTRACTION, QUERY_EXTRACTION_KEEP_LANG, TRANSLATE_PROMPT

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class QueryProcessingConfig:
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    # Keep low for Luna temporal_prepare (<1s); long outputs dominate latency.
    max_tokens: int = 48
    top_p: float = 1.0
    timeout: int = 20
    # gpt-5 / o-series: "none" fully disables thinking when supported.
    reasoning_effort: str = "none"


def _resolve_openai_api_key() -> str:
    return (
        os.getenv("OPEN_AI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip().strip('"').strip("'")


def _resolve_openai_model(default: str = "gpt-4o-mini") -> str:
    return (os.getenv("OPENAI_MODEL") or default).strip().strip('"').strip("'") or default


def _resolve_reasoning_effort(default: str = "minimal") -> str:
    value = (os.getenv("OPENAI_REASONING_EFFORT") or default).strip().strip('"').strip("'").lower()
    # "none" = fully disable thinking on models that support it (gpt-5.4 / luna, etc.)
    if value not in {"none", "minimal", "low", "medium", "high"}:
        return default
    return value


def _uses_completion_tokens_api(model: str) -> bool:
    """Newer OpenAI models reject ``max_tokens`` / often ignore sampling knobs."""
    name = (model or "").strip().lower()
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


def extract_json_object(text: str) -> dict:
    """Parse a JSON object from model output (tolerates markdown fences / trailing commas)."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty LLM response")
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        raw = fence.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    return json.loads(raw)


class QueryProcessor:
    def __init__(
        self,
        config: Optional[QueryProcessingConfig] = None,
        api_key: Optional[str] = None,
    ):
        self.config = config or QueryProcessingConfig(
            model=_resolve_openai_model(),
            reasoning_effort=_resolve_reasoning_effort("minimal"),
        )
        if not self.config.reasoning_effort:
            self.config.reasoning_effort = "minimal"
        self.api_key = (api_key if api_key is not None else _resolve_openai_api_key()).strip()
        self.client = OpenAI(api_key=self.api_key, timeout=self.config.timeout) if self.api_key else None

    @property
    def is_ready(self) -> bool:
        return self.client is not None

    def _create_chat_completion(self, messages: List[Dict[str, str]], **kwargs) -> str:
        if self.client is None:
            raise RuntimeError("OPEN_AI_API_KEY is not configured")
        try:
            model = kwargs.get("model", self.config.model)
            max_tokens = int(kwargs.get("max_tokens", self.config.max_tokens))
            create_kwargs = {
                "messages": messages,
                "model": model,
            }
            if _uses_completion_tokens_api(model):
                create_kwargs["max_completion_tokens"] = max_tokens
                # Always prefer no-reasoning for latency unless caller overrides.
                create_kwargs["reasoning_effort"] = kwargs.get(
                    "reasoning_effort", self.config.reasoning_effort or "minimal"
                )
            else:
                create_kwargs["max_tokens"] = max_tokens
                create_kwargs["temperature"] = kwargs.get("temperature", self.config.temperature)
                create_kwargs["top_p"] = kwargs.get("top_p", self.config.top_p)
            if kwargs.get("json_object"):
                create_kwargs["response_format"] = {"type": "json_object"}
            response = self.client.chat.completions.create(**create_kwargs)
            choice = response.choices[0]
            content = (choice.message.content or "").strip()
            if not content:
                details = getattr(response.usage, "completion_tokens_details", None)
                reasoning = getattr(details, "reasoning_tokens", None) if details else None
                raise RuntimeError(
                    "Empty LLM response "
                    f"(finish_reason={choice.finish_reason}, reasoning_tokens={reasoning}, "
                    f"max_completion_tokens={max_tokens}). "
                    "Set OPENAI_REASONING_EFFORT=minimal."
                )
            return content
        except Exception as exc:
            logger.error("OpenAI API error: %s", exc)
            raise RuntimeError(f"Failed to process query with OpenAI: {exc}") from exc

    def translate(self, text: str, lang: str = "en") -> str:
        return self._create_chat_completion(
            [{"role": "user", "content": TRANSLATE_PROMPT.format(text=text, lang=lang)}],
            max_tokens=min(max(self.config.max_tokens, 128), 256),
            reasoning_effort=self.config.reasoning_effort or "none",
        )

    def query_expansion(self, query: str) -> str:
        return self._create_chat_completion(
            [{"role": "user", "content": QUERY_EXPANSION.format(query=query)}],
            max_tokens=min(max(self.config.max_tokens, 256), 512),
            reasoning_effort=self.config.reasoning_effort or "none",
        )

    def query_extraction(self, query: str, *, translate: bool = True) -> str:
        # Enough room for 2–4 visual phrases; 48 tok collapsed multi-scene queries.
        max_tokens = min(max(int(self.config.max_tokens), 192), 256)
        prompt = QUERY_EXTRACTION if translate else QUERY_EXTRACTION_KEEP_LANG
        return self._create_chat_completion(
            [{"role": "user", "content": prompt.format(query=query)}],
            json_object=True,
            max_tokens=max_tokens,
            reasoning_effort=self.config.reasoning_effort or "none",
        )

    def query_extraction_json(self, query: str, *, translate: bool = True) -> dict:
        return extract_json_object(self.query_extraction(query, translate=translate))

    def temporal_prepare(self, query: str, *, translate: bool = True) -> dict:
        """Split temporal events. Optionally translate phrases into English.

        Returns ``translated_query`` (search text / first event) and ``event_descriptions``.
        Accepts compact ``{"events":[...]}`` or legacy ``structured_events``.
        """
        data = self.query_extraction_json(query, translate=translate)
        descriptions: List[str] = []

        # Fast path: {"events": ["...", "..."]}
        raw_events = data.get("events")
        if isinstance(raw_events, list):
            descriptions = [str(x).strip() for x in raw_events if str(x).strip()]

        # Legacy: {"structured_events":[{"event_description":...}, ...]}
        if not descriptions:
            for event in data.get("structured_events") or []:
                if not isinstance(event, dict):
                    continue
                desc = str(event.get("event_description") or "").strip()
                if desc:
                    descriptions.append(desc)

        # Fallback keys used by experimental short prompts
        if not descriptions and isinstance(data.get("e"), list):
            descriptions = [str(x).strip() for x in data["e"] if str(x).strip()]

        translated = (data.get("translated_query") or data.get("t") or "").strip()
        if not translate:
            translated = descriptions[0] if descriptions else query
        elif not translated:
            translated = descriptions[0] if descriptions else query
        return {
            "translated_query": translated,
            "event_descriptions": descriptions or [translated],
            "raw": data,
        }


@lru_cache(maxsize=1)
def get_query_processor(
    model: str = "",
    api_key: str = "",
    reasoning_effort: str = "none",
    max_tokens: int = 48,
) -> QueryProcessor:
    """Process-wide cached OpenAI client (connection reuse)."""
    return QueryProcessor(
        api_key=api_key or None,
        config=QueryProcessingConfig(
            model=model or _resolve_openai_model(),
            reasoning_effort=reasoning_effort or "none",
            max_tokens=max_tokens,
            timeout=20,
        ),
    )


if __name__ == "__main__":
    sample = (
        "Đoạn giới thiệu về một lễ hội các món ăn. Tuy nhiên, trong đoạn giới thiệu "
        "lại có một phân cảnh có các bạn sử dụng các thiết bị thực tại ảo."
    )
    processor = QueryProcessor()
    prepared = processor.temporal_prepare(sample)
    print("Translated:", prepared["translated_query"])
    for i, event in enumerate(prepared["event_descriptions"]):
        print(f"Query {i + 1}: {event}")
