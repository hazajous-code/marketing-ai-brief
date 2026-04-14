"""Background scheduler for automated daily newsletter delivery.

Uses APScheduler to fire once per day at 09:00 KST.  The scheduler is
designed to be started once via Streamlit's @st.cache_resource so it
survives across reruns but doesn't duplicate.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
_ARCHIVE_FILE = Path(__file__).parent / "data" / "article_archive.json"

_scheduler: BackgroundScheduler | None = None


def _load_recent_articles(days: int = 1) -> List[dict]:
    try:
        if not _ARCHIVE_FILE.exists():
            return []
        store = json.loads(_ARCHIVE_FILE.read_text(encoding="utf-8"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        items = []
        for v in store.values():
            pub = v.get("published_at")
            if isinstance(pub, str):
                try:
                    pub = datetime.fromisoformat(pub)
                except Exception:
                    continue
            if pub and pub >= cutoff:
                items.append(v)
        items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        return items
    except Exception:
        return []


def _generate_digest(articles: List[dict]) -> List[dict]:
    """Generate daily digest insights (reuses app logic without Streamlit dep)."""
    CATEGORIES = [
        "Generative Engine Optimization",
        "AI Automation in Marketing Execution",
        "Marketing AI Trend",
    ]

    payload = json.dumps([
        {"title": a.get("title", ""), "summary": (a.get("content") or "")[:260],
         "link": a.get("link", ""), "category": a.get("category", "")}
        for a in articles[:20]
    ], ensure_ascii=False)

    prompt = (
        "You are a global marketing strategist writing a premium daily brief.\n"
        "You MUST write ALL text content in Korean (한국어). Do NOT use Chinese or any other language.\n"
        "Classify the news into exactly 3 fixed categories and write insight-focused analysis.\n"
        "Do NOT summarize headlines. Extract strategic patterns and interpret meaning.\n"
        "Tone: professional newsletter, concise consulting report.\n\n"
        "Categories:\n- Generative Engine Optimization\n- AI Automation in Marketing Execution\n- Marketing AI Trend\n\n"
        "Return ONLY valid JSON array with exactly 3 objects:\n"
        '[{"title":"category","summary":"한국어 2-3 lines","key_points":["한국어...","한국어..."],'
        '"sources":[{"title":"...","link":"..."}],'
        '"marketing_insight":"한국어...","strategic_implication":"한국어..."}]\n'
        "Rules: 3 sections only, 2-3 sources each from provided news, no markdown.\n\n"
        f"News:\n{payload}"
    )

    try:
        from ollama_client import ollama_generate
        raw = ollama_generate(prompt, timeout=180, retries=1)
        parsed = json.loads(raw)
        if isinstance(parsed, list) and len(parsed) == 3:
            return parsed
    except Exception:
        pass

    # Fallback: return a minimal digest so the email still goes out
    return [
        {
            "title": cat,
            "summary": "이번 기간의 주요 동향을 분석한 인사이트입니다.",
            "key_points": ["AI 기반 마케팅 전략의 변화 가속", "데이터 기반 의사결정 체계 강화 필요"],
            "marketing_insight": "변화 속도에 맞춘 전략 대응 주기 단축이 핵심 과제입니다.",
            "strategic_implication": "조직 내 AI 활용 역량과 거버넌스 프레임워크를 동시에 점검해야 합니다.",
        }
        for cat in CATEGORIES
    ]


def _daily_job() -> None:
    """The job that runs every morning: send emails + publish newsletter HTML."""
    from collect_news import fetch_ai_tools_news
    from mailer import send_daily_brief

    logger.info("Daily job triggered at %s", datetime.now(KST).isoformat())

    articles = _load_recent_articles(days=1)
    if not articles:
        logger.info("No recent articles — skipping.")
        return

    digest = _generate_digest(articles)

    try:
        ai_tools = fetch_ai_tools_news(limit=5)
    except Exception:
        ai_tools = []

    # 1) Email
    result = send_daily_brief(digest, articles, ai_tools=ai_tools)
    logger.info("Email result: %s", result)

    # 2) Static HTML newsletter → GitHub Pages
    try:
        from newsletter_builder import git_push, publish_daily
        publish_daily()
        git_push()
        logger.info("Newsletter HTML published and pushed.")
    except Exception as e:
        logger.warning("Newsletter publish failed (non-fatal): %s", e)


def start_scheduler(hour: int = 9, minute: int = 0) -> BackgroundScheduler:
    """Start the background scheduler (idempotent — safe to call multiple times)."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone=KST)
    _scheduler.add_job(
        _daily_job,
        trigger="cron",
        hour=hour,
        minute=minute,
        id="daily_brief_email",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started — daily email at %02d:%02d KST", hour, minute)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_next_fire_time() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("daily_brief_email")
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M KST")
    return None


def trigger_now() -> None:
    """Manually trigger the daily email job immediately."""
    _daily_job()
