"""Translation layer for Marketing AI Brief.

Primary backend: Ollama (local LLM, no network dependency).
Supports both single-text and batch translation to reduce LLM round-trips.
Backend is swappable through set_translator_backend().
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import List, Protocol, Tuple

from ollama_client import ollama_generate

LANGUAGE_MAP = {
    "Original": "en",
    "Korean": "ko",
}

_LANG_NAMES = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}
WHITESPACE_REGEX = re.compile(r"\s+")

_MAX_BATCH = 8


class TranslatorBackend(Protocol):
    def translate(self, text: str, target_lang: str) -> str:
        ...


class OllamaTranslatorBackend:
    """Use local Ollama LLM for translation — no SSL/network issues."""

    def translate(self, text: str, target_lang: str) -> str:
        lang_name = _LANG_NAMES.get(target_lang, target_lang)
        prompt = (
            f"Translate the following text to {lang_name}. "
            "Return ONLY the translated text, nothing else.\n\n"
            f"{text}"
        )
        return ollama_generate(prompt, timeout=60, retries=1)


TRANSLATOR_BACKEND: TranslatorBackend = OllamaTranslatorBackend()


def set_translator_backend(backend: TranslatorBackend) -> None:
    """Swap translation provider (for future API migration)."""
    global TRANSLATOR_BACKEND
    TRANSLATOR_BACKEND = backend
    _translate_cached.cache_clear()


def _normalize_for_cache(text: str) -> str:
    return WHITESPACE_REGEX.sub(" ", (text or "")).strip()


@lru_cache(maxsize=2048)
def _translate_cached(normalized_text: str, target_lang: str) -> str:
    return TRANSLATOR_BACKEND.translate(text=normalized_text, target_lang=target_lang)


def translate_text(text: str, target_lang: str) -> str:
    original_text = text or ""
    normalized_text = _normalize_for_cache(original_text)
    if not normalized_text:
        return ""

    if target_lang == "en":
        return normalized_text

    try:
        return _translate_cached(normalized_text=normalized_text, target_lang=target_lang)
    except Exception:
        return original_text.strip()


# ── batch translation (one LLM call for multiple texts) ──────────────

_batch_cache: dict[tuple, List[str]] = {}


def _translate_chunk(texts: List[str], target_lang: str) -> List[str]:
    """Translate a single chunk (up to _MAX_BATCH) via one Ollama call."""
    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts) if t)
    prompt = (
        f"Translate each numbered line to {lang_name}. "
        "Return ONLY the translations, one per line, keeping the same numbering.\n\n"
        f"{numbered}"
    )
    raw = ollama_generate(prompt, timeout=60, retries=1)
    lines = [re.sub(r"^\d+\.\s*", "", ln).strip()
             for ln in raw.splitlines() if ln.strip()]

    results: List[str] = []
    line_idx = 0
    for t in texts:
        if not t:
            results.append("")
        elif line_idx < len(lines):
            results.append(lines[line_idx])
            line_idx += 1
        else:
            results.append(t)
    return results


def translate_batch(texts: List[str], target_lang: str) -> List[str]:
    """Translate multiple texts via Ollama, chunked to _MAX_BATCH per call.

    Results are cached so repeated calls return instantly.
    """
    if not texts or target_lang == "en":
        return list(texts)

    normed = [_normalize_for_cache(t) for t in texts]
    cache_key = (tuple(normed), target_lang)
    if cache_key in _batch_cache:
        return _batch_cache[cache_key]

    all_results: List[str] = []
    for start in range(0, len(normed), _MAX_BATCH):
        chunk = normed[start:start + _MAX_BATCH]
        try:
            all_results.extend(_translate_chunk(chunk, target_lang))
        except Exception:
            all_results.extend(chunk)

    _batch_cache[cache_key] = all_results
    return all_results
