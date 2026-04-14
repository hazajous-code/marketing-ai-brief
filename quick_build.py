"""Quick HTML rebuild: no Ollama calls, uses fallback insights + untranslated articles.

Run: python quick_build.py
"""
from __future__ import annotations
import json, logging, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent))

# Monkey-patch Ollama calls to return immediately
import newsletter_builder as nb
import translate as _tr

# Disable ALL Ollama/translation calls
def _no_llm(*a, **kw): raise RuntimeError("LLM disabled")
def _passthrough_batch(texts, *a, **kw): return list(texts)
def _passthrough_single(text, *a, **kw): return text

_tr.translate_batch = _passthrough_batch
_tr.translate_text = _passthrough_single
nb._generate_three_marketing_insights = lambda arts: nb._fallback_three_insights(arts)
# Also patch the inner report generation
nb._generate_period_report = lambda *a, **kw: None

logger.info("Rebuilding HTML (no-LLM mode)...")

# Rebuild today's page (latest archived date)
with open(nb._ARCHIVE_FILE, encoding="utf-8") as f:
    archive = json.load(f)

dates = sorted(archive.keys(), reverse=True)
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
