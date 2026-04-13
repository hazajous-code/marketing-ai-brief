"""Static HTML newsletter generator for GitHub Pages.

Reads from the article archive and generates self-contained HTML pages
that can be served statically via GitHub Pages or any web server.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent
_DOCS_DIR = _PROJECT_ROOT / "docs"
_ISSUES_DIR = _DOCS_DIR / "issues"
_ARCHIVE_FILE = _PROJECT_ROOT / "data" / "article_archive.json"
_REPORT_FILE = _PROJECT_ROOT / "data" / "generated_reports.json"

# Relative path from issue pages back to style.css
_CSS_REL_PATH = "../style.css"
_CSS_INDEX_PATH = "style.css"


def _load_archive() -> Dict[str, dict]:
    try:
        if _ARCHIVE_FILE.exists():
            data = json.loads(_ARCHIVE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _articles_for_date(date_str: str) -> List[dict]:
    """Return articles published on a given date (YYYY-MM-DD)."""
    store = _load_archive()
    items = []
    for v in store.values():
        pub = v.get("published_at", "")
        if isinstance(pub, str) and pub[:10] == date_str:
            items.append(v)
    items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return items


def _all_dates() -> List[str]:
    """Return sorted list of unique dates (YYYY-MM-DD) in the archive."""
    store = _load_archive()
    dates = set()
    for v in store.values():
        pub = v.get("published_at", "")
        if isinstance(pub, str) and len(pub) >= 10:
            dates.add(pub[:10])
    return sorted(dates, reverse=True)


def _load_digest_for_date(date_str: str) -> List[dict]:
    """Load pre-generated digest from reports file if available."""
    try:
        if _REPORT_FILE.exists():
            reports = json.loads(_REPORT_FILE.read_text(encoding="utf-8"))
            key = f"daily-{date_str}"
            if key in reports and isinstance(reports[key], list):
                return reports[key]
    except Exception:
        pass
    return []


def _generate_digest(articles: List[dict]) -> List[dict]:
    """Generate digest via Ollama (same logic as scheduler.py)."""
    import requests

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
        "Classify the news into exactly 3 fixed categories and write insight-focused analysis.\n"
        "Do NOT summarize headlines. Extract strategic patterns and interpret meaning.\n"
        "Tone: professional newsletter, concise consulting report.\n\n"
        "Categories:\n- Generative Engine Optimization\n- AI Automation in Marketing Execution\n- Marketing AI Trend\n\n"
        "Return ONLY valid JSON array with exactly 3 objects:\n"
        '[{"title":"category","summary":"2-3 lines","key_points":["...","..."],'
        '"sources":[{"title":"...","link":"..."}],'
        '"marketing_insight":"...","strategic_implication":"..."}]\n'
        "Rules: 3 sections only, 2-3 sources each from provided news, no markdown.\n\n"
        f"News:\n{payload}"
    )

    try:
        res = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3.1:8b", "prompt": prompt, "stream": False},
            timeout=60,
        )
        res.raise_for_status()
        raw = res.json().get("response", "").strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            if isinstance(parsed, list) and len(parsed) == 3:
                return parsed
    except Exception as e:
        logger.warning("Ollama digest generation failed: %s", e)

    return [
        {
            "title": cat,
            "summary": "AI 기반 마케팅 전략의 주요 동향을 분석한 인사이트입니다.",
            "key_points": ["AI 기반 마케팅 전략의 변화 가속", "데이터 기반 의사결정 체계 강화 필요"],
            "marketing_insight": "변화 속도에 맞춘 전략 대응 주기 단축이 핵심 과제입니다.",
            "strategic_implication": "조직 내 AI 활용 역량과 거버넌스 프레임워크를 동시에 점검해야 합니다.",
        }
        for cat in CATEGORIES
    ]


def _get_ai_tools_for_date(date_str: str) -> List[dict]:
    """Return AI-tool-flagged articles for a date, if any."""
    store = _load_archive()
    tools = []
    for v in store.values():
        pub = v.get("published_at", "")
        if isinstance(pub, str) and pub[:10] == date_str and v.get("is_ai_tool"):
            tools.append(v)
    return tools[:6]


# ── HTML builders ────────────────────────────────────────────────────

def build_daily_page(
    date_str: str,
    digest: List[dict],
    articles: List[dict],
    ai_tools: List[dict] | None = None,
    prev_date: str | None = None,
    next_date: str | None = None,
) -> str:
    """Build a complete HTML page for one day's newsletter."""
    ai_tools = ai_tools or []

    # Navigation
    nav_left = f'<a href="{prev_date}.html">&larr; {prev_date}</a>' if prev_date else '<span></span>'
    nav_right = f'<a href="{next_date}.html">{next_date} &rarr;</a>' if next_date else '<span></span>'
    nav_html = f"""
    <nav class="nav">
        {nav_left}
        <span class="nav-center"><a href="../index.html">All Issues</a></span>
        {nav_right}
    </nav>"""

    # AI Tools section
    ai_html = ""
    if ai_tools:
        cards = ""
        for t in ai_tools:
            title = escape(t.get("title", ""))
            link = escape(t.get("link", "#"))
            desc = escape(t.get("content", "")[:120])
            source = escape(t.get("source", ""))
            new_badge = '<span class="badge badge-new">NEW</span>' if t.get("is_new") else ""
            cards += f"""
            <div class="ai-tool-card">
                <p class="ai-tool-title"><a href="{link}" target="_blank">{title}</a></p>
                <p class="ai-tool-desc">{desc}</p>
                <div class="ai-tool-meta">
                    <span class="badge badge-tool">AI TOOL</span>
                    {new_badge}
                    <span>{source}</span>
                </div>
            </div>"""
        ai_html = f"""
        <div class="ai-tools-header">
            <span class="ai-tools-label">New AI Tools</span>
            <div class="ai-tools-line"></div>
        </div>
        <div class="ai-tools-grid">{cards}</div>"""

    # Digest section
    digest_html = ""
    if digest:
        digest_cards = ""
        for idx, d in enumerate(digest[:3], 1):
            bullets = "".join(
                f'<li>{escape(kp)}</li>' for kp in d.get("key_points", [])[:3]
            )
            sources_links = "".join(
                f'<a href="{escape(s.get("link", "#"))}" target="_blank">{escape(s.get("title", ""))}</a> '
                for s in d.get("sources", [])[:3]
            )
            digest_cards += f"""
            <div class="digest-card">
                <p class="digest-num">0{idx}</p>
                <h3 class="digest-title">{escape(d.get("title", ""))}</h3>
                <p class="digest-summary">{escape(d.get("summary", ""))}</p>
                <ul class="digest-kp">{bullets}</ul>
                <p class="digest-insight-label">Marketing Insight</p>
                <p class="digest-insight">{escape(d.get("marketing_insight", ""))}</p>
                <p class="digest-insight-label">Strategic Implication</p>
                <p class="digest-insight">{escape(d.get("strategic_implication", ""))}</p>
                <div class="digest-sources">{sources_links}</div>
            </div>"""
        digest_html = f"""
        <p class="section-label">Today's Marketing Trend Insights</p>
        <div class="digest-grid">{digest_cards}</div>"""

    # Article list
    article_cards = ""
    for a in articles[:18]:
        title = escape(a.get("title", ""))
        link = escape(a.get("link", "#"))
        source = escape(a.get("source", ""))
        content = escape(a.get("content", "")[:100])
        badges = ""
        if a.get("is_new"):
            badges += '<span class="badge badge-new">NEW</span>'
        if a.get("lang") == "ko":
            badges += '<span class="badge badge-ko">KR</span>'
        if a.get("is_research"):
            badges += '<span class="badge badge-research">RESEARCH</span>'
        article_cards += f"""
        <div class="article-card">
            <div class="article-badges">{badges}</div>
            <p class="article-title"><a href="{link}" target="_blank">{title}</a></p>
            <p class="ai-tool-desc">{content}</p>
            <div class="article-meta">
                <span>{source}</span>
            </div>
        </div>"""

    articles_html = ""
    if article_cards:
        articles_html = f"""
        <p class="section-label">Articles</p>
        <div class="article-grid">{article_cards}</div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Marketing AI Brief — {escape(date_str)}</title>
    <link rel="stylesheet" href="{_CSS_REL_PATH}">
</head>
<body>
<div class="container">
    <header class="masthead">
        <div>
            <p class="masthead-wordmark">Marketing AI Brief</p>
            <h1 class="masthead-title">Today's Marketing AI Insight</h1>
        </div>
        <div class="masthead-right">
            <p class="masthead-date">{escape(date_str)} (KST)</p>
            <p class="masthead-count">{len(articles)} articles{f" · {len(ai_tools)} tools" if ai_tools else ""}</p>
        </div>
    </header>

    {nav_html}
    {ai_html}
    {digest_html}
    {articles_html}

    <footer class="footer">
        <p>Marketing AI Brief &middot; Powered by Ollama + Streamlit</p>
        <p><a href="../index.html">All Issues</a></p>
    </footer>
</div>
</body>
</html>"""


def build_index_page(issues: List[dict]) -> str:
    """Build the index.html listing all newsletter issues."""
    items_html = ""
    for issue in issues:
        date = escape(issue["date"])
        count = issue.get("article_count", 0)
        tool_count = issue.get("tool_count", 0)
        meta_parts = [f"{count} articles"]
        if tool_count:
            meta_parts.append(f"{tool_count} AI tools")
        meta = " &middot; ".join(meta_parts)
        items_html += f"""
        <li class="issue-item">
            <a class="issue-date" href="issues/{date}.html">{date}</a>
            <span class="issue-meta">{meta}</span>
            <span class="issue-arrow">&rarr;</span>
        </li>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Marketing AI Brief — Archive</title>
    <link rel="stylesheet" href="{_CSS_INDEX_PATH}">
</head>
<body>
<div class="container">
    <header class="masthead">
        <div>
            <p class="masthead-wordmark">Marketing AI Brief</p>
            <h1 class="masthead-title">Newsletter Archive</h1>
        </div>
        <div class="masthead-right">
            <p class="masthead-count">{len(issues)} issues</p>
        </div>
    </header>

    <ul class="issue-list">
        {items_html}
    </ul>

    <footer class="footer">
        <p>Marketing AI Brief &middot; Powered by Ollama + Streamlit</p>
    </footer>
</div>
</body>
</html>"""


# ── publish helpers ──────────────────────────────────────────────────

def publish_single_date(date_str: str, dates_list: List[str] | None = None) -> Path | None:
    """Generate and save HTML for one date. Returns the output path or None."""
    articles = _articles_for_date(date_str)
    if not articles:
        logger.info("No articles for %s — skipping.", date_str)
        return None

    digest = _load_digest_for_date(date_str)
    if not digest:
        digest = _generate_digest(articles)
        _save_digest(date_str, digest)

    ai_tools = _get_ai_tools_for_date(date_str)

    if dates_list is None:
        dates_list = _all_dates()
    idx = dates_list.index(date_str) if date_str in dates_list else -1
    prev_date = dates_list[idx + 1] if idx >= 0 and idx + 1 < len(dates_list) else None
    next_date = dates_list[idx - 1] if idx > 0 else None

    html = build_daily_page(date_str, digest, articles, ai_tools, prev_date, next_date)
    _ISSUES_DIR.mkdir(parents=True, exist_ok=True)
    out = _ISSUES_DIR / f"{date_str}.html"
    out.write_text(html, encoding="utf-8")
    logger.info("Generated %s (%d articles)", out, len(articles))
    return out


def _save_digest(date_str: str, digest: List[dict]) -> None:
    """Persist the generated digest so we don't regenerate it next time."""
    try:
        reports: dict = {}
        if _REPORT_FILE.exists():
            reports = json.loads(_REPORT_FILE.read_text(encoding="utf-8"))
        reports[f"daily-{date_str}"] = digest
        _REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REPORT_FILE.write_text(json.dumps(reports, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save digest: %s", e)


def publish_index() -> Path:
    """Regenerate the index.html listing all available issues."""
    dates = _all_dates()
    issues = []
    for d in dates:
        arts = _articles_for_date(d)
        tools = [a for a in arts if a.get("is_ai_tool")]
        issues.append({
            "date": d,
            "article_count": len(arts),
            "tool_count": len(tools),
        })
    html = build_index_page(issues)
    _DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out = _DOCS_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    logger.info("Generated index.html (%d issues)", len(issues))
    return out


def publish_daily(date_str: str | None = None) -> None:
    """Generate today's newsletter and update the index."""
    if date_str is None:
        from zoneinfo import ZoneInfo
        date_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    dates = _all_dates()
    if date_str not in dates:
        dates = sorted(set(dates) | {date_str}, reverse=True)

    publish_single_date(date_str, dates)
    publish_index()


def publish_all() -> None:
    """Generate HTML for every date in the archive + index."""
    dates = _all_dates()
    if not dates:
        logger.info("No archived dates found.")
        return
    for d in dates:
        publish_single_date(d, dates)
    publish_index()
    logger.info("Published %d issues.", len(dates))


def git_push(message: str | None = None) -> bool:
    """Stage docs/, commit, and push. Returns True on success."""
    if message is None:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
        message = f"Newsletter update {today}"

    try:
        subprocess.run(["git", "add", "docs/"], cwd=str(_PROJECT_ROOT), check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(_PROJECT_ROOT), capture_output=True,
        )
        if result.returncode == 0:
            logger.info("No changes to commit.")
            return True
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(_PROJECT_ROOT), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=str(_PROJECT_ROOT), check=True, capture_output=True,
        )
        logger.info("Pushed to remote: %s", message)
        return True
    except subprocess.CalledProcessError as e:
        logger.error("Git operation failed: %s — %s", e, e.stderr)
        return False
