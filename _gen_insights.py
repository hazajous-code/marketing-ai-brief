"""Generate LLM insights for the latest date and save to cache.

After this, run quick_build.py to generate HTML using the cached insights.
"""
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

PROJECT = Path(__file__).parent
ARCHIVE = PROJECT / "data" / "article_archive.json"
REPORT = PROJECT / "data" / "generated_reports.json"


def main():
    store = json.loads(ARCHIVE.read_text(encoding="utf-8")) if ARCHIVE.exists() else {}
    dates = sorted({
        v["published_at"][:10]
        for v in store.values()
        if isinstance(v.get("published_at"), str) and len(v["published_at"]) >= 10
    }, reverse=True)

    if not dates:
        print("No dates in archive")
        return

    latest = dates[0]
    articles = [
        v for v in store.values()
        if isinstance(v.get("published_at"), str) and v["published_at"][:10] == latest
        and v.get("category") != "ai-tool"
    ]
    logger.info("Latest date: %s (%d articles)", latest, len(articles))

    if not articles:
        print("No articles for", latest)
        return

    from ollama_client import warmup
    logger.info("Warming up model...")
    warmup()

    from newsletter_builder import _generate_three_marketing_insights
    logger.info("Generating 3 insights via LLM...")
    insights = _generate_three_marketing_insights(articles)

    for i, ins in enumerate(insights, 1):
        print(f"\n--- Insight {i} ---")
        print(f"Title: {ins.get('title', '?')}")
        kp = ins.get('key_point', '')
        if kp:
            print(f"Key Point: {kp}")
        print(f"Body: {ins.get('body', '?')[:200]}")
        print(f"Evidence: {ins.get('evidence', [])}")

    reports = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    reports[f"insights-{latest}"] = insights
    REPORT.write_text(json.dumps(reports, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Saved insights for %s", latest)
    print("\nDone! Now run: python quick_build.py")


if __name__ == "__main__":
    main()
