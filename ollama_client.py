"""Shared Ollama client with retry, health-check, and CPU-optimised defaults.

All Ollama calls across the project should go through `ollama_generate()`.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"
_GENERATE_URL = f"{OLLAMA_BASE}/api/generate"

OLLAMA_OPTIONS: Dict[str, Any] = {
    "num_ctx": 4096,
    "temperature": 0.3,
    "top_p": 0.85,
}

_session = requests.Session()
_session.verify = False


def _health_check(timeout: float = 3) -> bool:
    try:
        r = _session.get(OLLAMA_BASE, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def ollama_generate(
    prompt: str,
    *,
    timeout: int = 60,
    retries: int = 2,
    model: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> str:
    """Call Ollama /api/generate with automatic retry and keep-alive.

    Raises on final failure so callers can fall back gracefully.
    """
    payload = {
        "model": model or OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "24h",
        "options": {**OLLAMA_OPTIONS, **(options or {})},
    }

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            if attempt > 0 or not _health_check():
                if not _health_check(timeout=5):
                    raise ConnectionError("Ollama not reachable")

            resp = _session.post(_GENERATE_URL, json=payload, timeout=timeout)
            resp.raise_for_status()
            result = (resp.json().get("response") or "").strip()
            if not result:
                raise ValueError("Empty Ollama response")
            return result
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                wait = 2 ** attempt
                logger.debug("Ollama attempt %d failed (%s), retrying in %ds", attempt + 1, exc, wait)
                time.sleep(wait)

    raise last_exc  # type: ignore[misc]


def warmup() -> bool:
    """Pre-load the model into Ollama memory. Returns True on success."""
    try:
        ollama_generate("hi", timeout=30, retries=0)
        logger.info("Ollama model %s warmed up", OLLAMA_MODEL)
        return True
    except Exception as exc:
        logger.warning("Ollama warmup failed: %s", exc)
        return False
