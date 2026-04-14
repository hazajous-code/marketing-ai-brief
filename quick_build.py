"""Quick HTML rebuild: uses cached insights (or fallback).

Tries live Ollama translation if available; falls back to untranslated if not.

Run: python quick_build.py
"""
from __future__ import annotations
import json, logging, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent))

import newsletter_builder as nb

nb._generate_three_marketing_insights = lambda arts: nb._fallback_three_insights(arts)
nb._generate_period_report = lambda *a, **kw: None

ollama_ok = False
try:
    from ollama_client import _health_check
    ollama_ok = _health_check(timeout=5)
except Exception:
    pass

if ollama_ok:
    logger.info("Ollama is alive — translation enabled")
    from ollama_client import warmup
    warmup()
else:
    logger.warning("Ollama not reachable — skipping translation, using original text")
    import translate as _tr
    _tr.translate_batch = lambda texts, *a, **kw: list(texts)
    _tr.translate_text = lambda text, *a, **kw: text

logger.info("Rebuilding HTML...")

dates = nb._all_dates()
if not dates:
    logger.error("No archived dates found.")
    sys.exit(1)

latest = dates[0]
logger.info("Latest archived date: %s", latest)

nb.publish_single_date(latest, is_latest=True)
logger.info("Built issue for %s", latest)

nb.publish_index()
logger.info("Built index.html")

print("Done (no-push). Run git push separately.")
