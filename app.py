from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from collect_news import DEFAULT_FEEDS, fetch_ai_tools_news, fetch_rss_news
from newsletter_builder import _render_tool_directory_table, _render_youtube_section
from mailer import add_subscriber, get_active_emails, is_configured, load_subscribers, send_daily_brief
from scheduler import get_next_fire_time, start_scheduler, trigger_now
from summarize import summarize_text
from translate import translate_batch, translate_text

# ── persistent archive ──────────────────────────────────────────────
_ARCHIVE_DIR = Path(__file__).parent / "data"
_ARCHIVE_FILE = _ARCHIVE_DIR / "article_archive.json"
_REPORT_FILE = _ARCHIVE_DIR / "generated_reports.json"


def _load_archive() -> Dict[str, dict]:
    try:
        if _ARCHIVE_FILE.exists():
            data = json.loads(_ARCHIVE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_archive(store: Dict[str, dict]) -> None:
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    _ARCHIVE_FILE.write_text(json.dumps(store, ensure_ascii=False, default=str), encoding="utf-8")


def _accumulate_articles(items: List[dict]) -> Dict[str, dict]:
    """Add today's articles to the persistent archive and return the full store."""
    store = _load_archive()
    changed = False
    for item in items:
        aid = item.get("id")
        if aid and aid not in store:
            serializable = dict(item)
            if isinstance(serializable.get("published_at"), datetime):
                serializable["published_at"] = serializable["published_at"].isoformat()
            store[aid] = serializable
            changed = True
    if changed:
        _save_archive(store)
    return store


def _get_archived_items(days: int) -> List[dict]:
    store = _load_archive()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for v in store.values():
        pub = v.get("published_at")
        if isinstance(pub, str):
            try:
                pub = datetime.fromisoformat(pub)
            except Exception:
                continue
        if pub and pub >= cutoff:
            item = dict(v)
            item["published_at"] = pub
            result.append(item)
    result.sort(key=lambda x: x.get("published_at", datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return result


def _load_reports() -> Dict[str, dict]:
    try:
        if _REPORT_FILE.exists():
            return json.loads(_REPORT_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_report(key: str, report: dict) -> None:
    reports = _load_reports()
    reports[key] = report
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    _REPORT_FILE.write_text(json.dumps(reports, ensure_ascii=False, default=str), encoding="utf-8")

KST = ZoneInfo("Asia/Seoul")

st.set_page_config(page_title="Marketing AI Brief", layout="wide")


@st.cache_resource
def _warmup_ollama():
    """Pre-load the LLM model into Ollama memory to eliminate cold start."""
    try:
        from ollama_client import warmup
        warmup()
    except Exception:
        pass


_warmup_ollama()

DAILY_DIGEST_CATEGORIES = [
    "Generative Engine Optimization",
    "AI Automation in Marketing Execution",
    "Marketing AI Trend",
]

NEWSLETTER_CSS = """
<style>
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Claude-style design system
   Light: warm cream  ·  Dark: charcoal-warm
   Accent: #DA7756 (Claude orange)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
:root {
    --bg-base:        #FAF9F7;
    --bg-card:        #FFFFFF;
    --bg-elevated:    #F4F2EE;
    --bg-input:       #F0EDE8;
    --bg-sidebar:     #F4F2EE;
    --text-primary:   #1A1916;
    --text-secondary: #3D3A35;
    --text-muted:     #716D66;
    --text-faint:     #A09890;
    --border:         #E8E3DC;
    --border-strong:  #D4CCC0;
    --accent:         #DA7756;
    --accent-hover:   #C46644;
    --accent-soft:    rgba(218,119,86,0.09);
    --accent-text:    #B35A38;
    --link:           #C46644;
    --shadow-sm:      0 1px 2px rgba(60,40,20,0.05);
    --shadow-md:      0 2px 8px rgba(60,40,20,0.07), 0 1px 2px rgba(60,40,20,0.04);
    --radius:         10px;
    --radius-sm:      6px;
    --radius-tag:     4px;
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg-base:        #1C1917;
        --bg-card:        #262320;
        --bg-elevated:    #2E2B28;
        --bg-input:       #332F2B;
        --bg-sidebar:     #232019;
        --text-primary:   #F0EBE3;
        --text-secondary: #C8C0B5;
        --text-muted:     #8C8278;
        --text-faint:     #5C5550;
        --border:         #38332E;
        --border-strong:  #4A443D;
        --accent:         #E8906E;
        --accent-hover:   #F0A080;
        --accent-soft:    rgba(232,144,110,0.12);
        --accent-text:    #E8906E;
        --link:           #E8906E;
        --shadow-sm:      0 1px 3px rgba(0,0,0,0.25);
        --shadow-md:      0 3px 10px rgba(0,0,0,0.35);
    }
}

/* ── base ──────────────────────────────────── */
.stApp {
    background: var(--bg-base) !important;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    color: var(--text-primary);
}
.block-container {
    max-width: 100%;
    padding: 1.5rem 2rem 4rem;
}
/* prevent Streamlit header bar from overlapping content */
.stApp > header { background: transparent !important; }
.stMainBlockContainer { padding-top: 0.5rem !important; }

/* ── masthead ──────────────────────────────── */
.masthead {
    padding: 12px 0 20px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
    overflow: visible;
}
.masthead-left {}
.masthead-wordmark {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    color: var(--accent);
    margin: 0 0 6px;
}
.masthead-title {
    font-size: 22px;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0;
    line-height: 1.3;
    letter-spacing: -0.3px;
}
.masthead-right {
    text-align: right;
    flex-shrink: 0;
}
.masthead-date {
    font-size: 12px;
    color: var(--text-faint);
    margin: 0;
    line-height: 1.6;
}
.masthead-count {
    font-size: 12px;
    font-weight: 600;
    color: var(--accent);
    margin: 0;
}

/* ── digest (Daily Brief cards) ────────────── */
.digest-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 22px 20px 18px;
    height: 100%;
    display: flex;
    flex-direction: column;
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.15s ease;
}
.digest-card:hover { box-shadow: var(--shadow-md); }
.digest-num {
    font-size: 10px;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin: 0 0 10px;
}
.digest-title {
    font-size: 15px;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 10px;
    line-height: 1.45;
    letter-spacing: -0.1px;
}
.digest-body {
    font-size: 13px;
    line-height: 1.8;
    color: var(--text-secondary);
    margin: 0 0 12px;
    white-space: pre-wrap;
}
.digest-bullets {
    margin: 0 0 12px;
    padding-left: 16px;
}
.digest-bullets li {
    font-size: 13px;
    line-height: 1.75;
    color: var(--text-secondary);
    margin-bottom: 5px;
}
.digest-lbl {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--accent);
    margin: 14px 0 4px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
}
.digest-val {
    font-size: 12.5px;
    line-height: 1.7;
    color: var(--text-secondary);
    margin: 0 0 2px;
}
.digest-sources {
    margin: 12px 0 0;
    padding: 0;
    list-style: none;
}
.digest-sources li {
    font-size: 11px;
    line-height: 1.7;
    color: var(--text-faint);
}
.digest-sources a {
    color: var(--link) !important;
    text-decoration: none;
}
.digest-sources a:hover { text-decoration: underline; }

/* ── insight cards (오늘의 마케팅 인사이트) ── */
.insight-cards {
    display: flex;
    flex-direction: column;
    gap: 14px;
    margin-bottom: 28px;
}
.insight-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-top: 3px solid var(--accent);
    border-radius: var(--radius);
    padding: 22px 26px 20px;
    box-shadow: var(--shadow-sm);
    transition: box-shadow .18s, transform .18s;
}
.insight-card:hover {
    box-shadow: var(--shadow-md);
    transform: translateY(-1px);
}
.ins-card-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
}
.ins-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    background: var(--accent);
    color: #fff;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1px;
    flex-shrink: 0;
}
.ins-tag {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--accent-text);
    background: var(--accent-soft);
    padding: 2px 10px;
    border-radius: 20px;
}
.ins-title {
    font-size: 16px;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 10px;
    line-height: 1.45;
    letter-spacing: -0.2px;
}
.ins-text {
    font-size: 13.5px;
    line-height: 1.85;
    color: var(--text-secondary);
    margin: 0 0 14px;
}
.ins-sources {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    padding-top: 12px;
    border-top: 1px dashed var(--border);
}
.ins-src-label {
    font-size: 10px;
    color: var(--text-faint);
    font-weight: 600;
    letter-spacing: .5px;
    text-transform: uppercase;
    margin-right: 2px;
}
.ins-src {
    font-size: 11px;
    background: var(--bg-elevated);
    color: var(--text-muted);
    padding: 3px 10px;
    border-radius: 20px;
    border: 1px solid var(--border);
}

/* ── insight key point ─────────────────────── */
.ins-keypoint {
    font-size: 13px;
    font-weight: 600;
    color: var(--accent-text);
    background: var(--accent-soft);
    border-left: 3px solid var(--accent);
    padding: 8px 12px;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    margin: 0 0 12px;
    line-height: 1.6;
}
.ins-kp-label {
    display: inline-block;
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: var(--accent);
    margin-right: 8px;
}

/* ── YouTube cards ─────────────────────────── */
.yt-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    box-shadow: var(--shadow-sm);
    transition: box-shadow .15s, transform .15s;
    margin-bottom: 10px;
}
.yt-card:hover { box-shadow: var(--shadow-md); transform: translateY(-1px); }
.yt-thumb { width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; background: var(--bg-elevated); }
.yt-body { padding: 12px 14px 10px; display: flex; flex-direction: column; gap: 5px; }
.yt-channel-row { display: flex; align-items: center; gap: 7px; }
.yt-badge {
    background: #FF0000; color: #fff; font-size: 9px; font-weight: 700;
    letter-spacing: .5px; padding: 2px 6px; border-radius: 3px; text-transform: uppercase;
}
.yt-channel-name { font-size: 11px; font-weight: 600; color: var(--text-muted); }
.yt-new-badge {
    font-size: 9px; font-weight: 700; background: var(--accent);
    color: #fff; padding: 1px 6px; border-radius: 10px; margin-left: auto;
}
.yt-title { font-size: 13.5px; font-weight: 700; line-height: 1.45; color: var(--text-primary); margin: 0; }
.yt-title a { color: inherit; text-decoration: none; }
.yt-title a:hover { color: var(--accent); }
.yt-desc {
    font-size: 12px; line-height: 1.7; color: var(--text-secondary);
    display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3;
    overflow: hidden; margin: 0;
}
.yt-meta { font-size: 10.5px; color: var(--text-faint); padding-top: 6px; border-top: 1px solid var(--border); }

/* YouTube region badges */
.yt-region-badge { font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 3px; letter-spacing: .5px; }
.yt-region-gl { background: #1a73e8; color: #fff; }
.yt-region-kr { background: #c62828; color: #fff; }

/* YouTube section wrapper */
.yt-section {
    background: var(--bg-elevated); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px 22px 22px; margin-bottom: 28px;
}
.yt-sub-section { margin-top: 4px; }
.yt-sub-label {
    display: flex; align-items: center; gap: 6px;
    font-size: 12px; font-weight: 700; color: var(--text-primary);
    padding-bottom: 9px; margin-bottom: 11px;
    border-bottom: 2px solid var(--border); letter-spacing: .2px;
}

/* YouTube summary card — 2-column */
.yt-summary-card {
    background: var(--bg-card); border: 1px solid var(--border);
    border-top: 3px solid #FF0000; border-radius: var(--radius);
    padding: 18px 20px; margin-bottom: 20px;
    display: grid; grid-template-columns: 1fr 1.6fr; gap: 22px; align-items: start;
}
.yt-summary-left {} .yt-summary-right {}
.yt-summary-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.yt-summary-icon { font-size: 24px; flex-shrink: 0; }
.yt-summary-title { font-size: 14px; font-weight: 700; color: var(--text-primary); margin: 0 0 3px; }
.yt-summary-topics { font-size: 11px; color: var(--accent-text); margin: 0; font-weight: 600; }
.yt-summary-desc { font-size: 12px; color: var(--text-muted); margin: 8px 0 0; line-height: 1.6; }
.yt-summary-list { list-style: none; padding: 0; margin: 0; }
.yt-summary-list li {
    font-size: 12px; line-height: 1.7; color: var(--text-secondary);
    padding: 5px 0; border-bottom: 1px solid var(--border);
    display: flex; align-items: baseline; gap: 5px;
}
.yt-summary-list li:last-child { border-bottom: none; }
.yt-summary-list li strong { color: var(--text-primary); font-weight: 600; white-space: nowrap; }

/* ── divider ───────────────────────────────── */
.section-divider {
    border: none;
    border-top: 1px solid var(--border);
    margin: 28px 0;
}

/* ── section label ─────────────────────────── */
.section-lbl {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.8px;
    text-transform: uppercase;
    color: var(--text-faint);
    margin: 0 0 16px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
}
p.section-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.8px;
    text-transform: uppercase;
    color: var(--text-faint);
    margin: 0 0 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
}
.section-label.section-label-hero {
    color: var(--accent-text, #7C3AED);
    letter-spacing: 2px;
}
.tool-dir-hero {
    margin-bottom: 22px;
    padding: 10px 12px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--bg-elevated);
}
.tool-dir-wrap { overflow-x: auto; }
.tool-dir-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
}
.tool-dir-table thead th {
    text-align: left;
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted);
    padding: 6px 8px;
    border-bottom: 2px solid var(--border);
}
.dir-cat-row td {
    font-size: 12px;
    font-weight: 700;
    color: var(--accent-text, #7C3AED);
    padding: 10px 8px 4px;
}
.tool-dir-table tbody tr:not(.dir-cat-row) { border-bottom: 1px solid var(--border); }
.dir-icon { width: 28px; text-align: center; font-size: 15px; padding: 5px 4px; }
.dir-name { padding: 5px 8px; font-weight: 600; }
.dir-name a { color: var(--text-primary); }
.dir-maker { display: block; font-size: 9px; color: var(--text-faint); font-weight: 400; }
.dir-desc { padding: 5px 8px; color: var(--text-muted); font-size: 11.5px; }

.yt-section-compact {
    padding: 12px 14px 14px !important;
    margin-bottom: 20px !important;
}
.yt-micro-line { font-size: 12px; color: var(--text-secondary); margin: 0 0 6px; }
.yt-micro-line strong { color: var(--text-primary); }
.yt-micro-meta { font-weight: 600; color: var(--text-muted); margin-left: 6px; }
.yt-compact-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin-top: 8px;
}
.yt-compact-list { display: flex; flex-direction: column; gap: 5px; }
.yt-compact-item {
    display: flex; align-items: center; gap: 8px;
    text-decoration: none; padding: 5px 6px; border-radius: var(--radius-sm);
}
.yt-compact-item:hover { background: var(--bg-card); }
.yt-compact-thumb { width: 56px; height: 32px; object-fit: cover; border-radius: 4px; flex-shrink: 0; }
.yt-compact-title { font-size: 11.5px; font-weight: 600; color: var(--text-primary); display: block; line-height: 1.35; }
.yt-compact-channel { font-size: 10px; color: var(--text-faint); }
.ai-tools-news-grid .ai-tool-desc { -webkit-line-clamp: 3; }

/* ── article cards ─────────────────────────── */
.a-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 18px 18px 14px;
    margin-bottom: 14px;
    display: flex;
    flex-direction: column;
    box-shadow: var(--shadow-sm);
    min-height: 180px;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.a-card:hover {
    border-color: var(--border-strong);
    box-shadow: var(--shadow-md);
}
.a-card-tags {
    display: flex;
    align-items: center;
    gap: 5px;
    margin-bottom: 10px;
    flex-wrap: wrap;
}
/* shared tag base */
.a-src, .lang-ko, .badge-research, .badge-new {
    display: inline-block;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    padding: 2px 7px;
    border-radius: var(--radius-tag);
    line-height: 1.5;
}
.a-src {
    color: var(--accent-text);
    background: var(--accent-soft);
}
.lang-ko {
    color: #5B6E00;
    background: rgba(120,140,0,0.1);
}
.badge-research {
    color: #7A5500;
    background: rgba(200,140,0,0.1);
}
.badge-new {
    color: var(--accent-text);
    background: var(--accent-soft);
    border: 1px solid var(--accent);
}
@media (prefers-color-scheme: dark) {
    .lang-ko    { color: #B8D000; background: rgba(180,200,0,0.1); }
    .badge-research { color: #F0C060; background: rgba(200,150,0,0.13); }
    .badge-new  { color: var(--accent); background: var(--accent-soft); }
}
.a-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
    line-height: 1.5;
    margin: 0 0 10px;
    letter-spacing: -0.1px;
}
.a-title a {
    color: var(--text-primary) !important;
    text-decoration: none;
}
.a-title a:hover { color: var(--accent) !important; }
.a-kp {
    margin: 0;
    padding-left: 0;
    flex: 1;
    list-style: none;
}
.a-kp li {
    font-size: 13px;
    line-height: 1.75;
    color: var(--text-secondary);
    margin-bottom: 6px;
    padding-left: 14px;
    position: relative;
}
.a-kp li::before {
    content: "–";
    position: absolute;
    left: 0;
    color: var(--accent);
    font-weight: 600;
}
.a-meta {
    font-size: 10.5px;
    color: var(--text-faint);
    margin-top: auto;
    padding-top: 10px;
    border-top: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 6px;
}
.a-meta-dot {
    width: 3px; height: 3px;
    background: var(--border-strong);
    border-radius: 50%;
    display: inline-block;
}

/* ── search ────────────────────────────────── */
[data-testid="stTextInput"] input {
    background: var(--bg-input) !important;
    border: 1px solid var(--border) !important;
    color: var(--text-primary) !important;
    border-radius: 20px !important;
    font-size: 13px !important;
    padding: 8px 16px !important;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
[data-testid="stTextInput"] input:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px var(--accent-soft) !important;
}

/* ── coming soon ───────────────────────────── */
.coming-soon {
    text-align: center;
    padding: 64px 20px;
    background: var(--bg-elevated);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    margin-top: 12px;
}
.coming-soon-icon { font-size: 32px; margin-bottom: 14px; }
.coming-soon-title {
    font-size: 17px;
    font-weight: 600;
    color: var(--text-primary);
    margin: 0 0 8px;
    letter-spacing: -0.2px;
}
.coming-soon-desc {
    font-size: 13px;
    color: var(--text-muted);
    line-height: 1.7;
    max-width: 440px;
    margin: 0 auto;
}

/* ── footer ────────────────────────────────── */
.nl-footer {
    text-align: center;
    padding: 24px 0 0;
    border-top: 1px solid var(--border);
    margin-top: 40px;
}
.nl-footer p {
    font-size: 11px;
    color: var(--text-faint);
    margin: 0;
}

/* ── streamlit overrides ───────────────────── */
.stApp [data-testid="stSidebar"] { background: var(--bg-sidebar) !important; }
.stTabs [data-baseweb="tab-list"] { background: transparent; gap: 4px; }
.stTabs [data-baseweb="tab"] {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-muted);
    padding: 6px 14px;
    border-radius: 6px;
}
.stTabs [aria-selected="true"] {
    color: var(--accent) !important;
    background: var(--accent-soft) !important;
    font-weight: 600;
}
.stTabs [data-baseweb="tab-highlight"] { background: var(--accent) !important; }

/* ── subscribe banner ─────────────────────── */
.subscribe-banner {
    background: linear-gradient(135deg, var(--accent) 0%, #C46644 100%);
    border-radius: var(--radius);
    padding: 20px 24px;
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
}
.subscribe-banner .sub-icon {
    font-size: 28px;
    line-height: 1;
    flex-shrink: 0;
}
.subscribe-banner .sub-copy {
    flex: 1;
    min-width: 180px;
}
.subscribe-banner .sub-title {
    font-size: 15px;
    font-weight: 700;
    color: #FFFFFF;
    margin: 0 0 2px;
    letter-spacing: -0.2px;
}
.subscribe-banner .sub-desc {
    font-size: 12px;
    color: rgba(255,255,255,0.82);
    margin: 0;
    line-height: 1.5;
}
.subscribe-banner .sub-count {
    font-size: 11px;
    color: rgba(255,255,255,0.65);
    margin: 0;
}

/* ── AI tools section ─────────────────────── */
.ai-tools-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 16px;
}
.ai-tools-label {
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: #7C3AED;
    margin: 0;
    flex-shrink: 0;
}
@media (prefers-color-scheme: dark) {
    .ai-tools-label { color: #A78BFA; }
}
.ai-tools-line {
    flex: 1;
    height: 1px;
    background: linear-gradient(90deg, #7C3AED33 0%, var(--border) 100%);
}
.ai-tool-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-left: 3px solid #7C3AED;
    border-radius: var(--radius-sm);
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-height: 130px;
    transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
}
.ai-tool-card:hover {
    border-color: #7C3AED;
    box-shadow: 0 4px 16px rgba(124,58,237,0.12);
    transform: translateY(-1px);
}
@media (prefers-color-scheme: dark) {
    .ai-tool-card { border-left-color: #A78BFA; }
    .ai-tool-card:hover { border-color: #A78BFA; box-shadow: 0 4px 16px rgba(167,139,250,0.15); }
}
.ai-tool-title {
    font-size: 13.5px;
    font-weight: 600;
    color: var(--text-primary);
    line-height: 1.5;
    margin: 0;
}
.ai-tool-title a {
    color: var(--text-primary) !important;
    text-decoration: none;
}
.ai-tool-title a:hover { color: #7C3AED !important; }
@media (prefers-color-scheme: dark) {
    .ai-tool-title a:hover { color: #A78BFA !important; }
}
.ai-tool-desc {
    font-size: 12px;
    line-height: 1.6;
    color: var(--text-muted);
    margin: 0;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
}
.ai-tool-meta {
    font-size: 10px;
    color: var(--text-faint);
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: auto;
    flex-wrap: wrap;
}
.badge-tool {
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: var(--radius-tag);
    color: #FFFFFF;
    background: #7C3AED;
}
@media (prefers-color-scheme: dark) {
    .badge-tool { background: #7C3AED; color: #FFFFFF; }
}
</style>
"""


# ── data helpers ─────────────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def load_news(feed_urls: tuple[str, ...], limit: int) -> List[dict]:
    return fetch_rss_news(feed_urls, limit=limit)


@st.cache_data(ttl=1800, show_spinner=False)
def load_ai_tools(limit: int = 10) -> List[dict]:
    return fetch_ai_tools_news(limit=limit)


def _get_summary(aid: str, mode: str) -> str:
    return st.session_state.setdefault("summaries", {}).get(aid, {}).get(mode, "")


def _set_summary(aid: str, mode: str, text: str) -> None:
    st.session_state.setdefault("summaries", {}).setdefault(aid, {})[mode] = text


def _auto_summarize(items: List[dict], mode: str) -> None:
    for idx, item in enumerate(items):
        aid = item.get("id") or item.get("link") or f"a-{idx}"
        if _get_summary(aid, mode):
            continue
        raw = item.get("content") or item.get("title") or ""
        result = summarize_text(raw, length=mode)
        _set_summary(aid, mode, (result or "").strip() or "- 분석 결과를 생성하지 못했습니다")


def add_metadata(items: List[dict]) -> List[dict]:
    enriched = []
    for item in items:
        c = dict(item)
        text = f"{c.get('title','')} {c.get('content','')}".lower()
        if any(k in text for k in ("ai", "tech", "software", "chip", "startup", "llm", "generative")):
            c["category"] = "Technology"
        elif any(k in text for k in ("market", "stock", "economy", "trade", "business", "commerce")):
            c["category"] = "Business"
        else:
            c["category"] = "Marketing"
        enriched.append(c)
    return enriched


def search_filter(items: List[dict], query: str) -> List[dict]:
    q = (query or "").strip().lower()
    if not q:
        return items
    return [i for i in items if q in f"{i.get('title','')} {i.get('content','')} {i.get('source','')}".lower()]


# ── daily digest (LLM) ──────────────────────────────────────────────

def _fallback_daily_insights(items: List[dict]) -> List[dict]:
    fb = {
        "Generative Engine Optimization": {
            "summary": (
                "검색 인터페이스가 생성형 응답 중심으로 전환되며 기존 SEO 프레임워크의 유효성이 급격히 낮아지고 있습니다.\n"
                "콘텐츠는 클릭 유도가 아니라 AI가 인용·요약할 수 있는 구조로 재설계되어야 합니다."
            ),
            "key_points": [
                "검색 결과의 생성형 응답 비중 확대 → 노출 경쟁 축 변경",
                "브랜드 자산의 구조화 데이터 품질이 도달 효율을 직접 좌우",
                "콘텐츠 설계 기준이 CTR에서 인용 가능성(citability)으로 이동",
            ],
            "marketing_insight": "GEO는 SEO의 대체가 아니라 확장. 검색 전략 팀의 역할이 '순위 관리'에서 'AI 인용 설계'로 재정의되고 있습니다.",
            "strategic_implication": "구조화 데이터, FAQ 스키마, 권위 출처 연결 점검 후 콘텐츠 파이프라인에 GEO 체크리스트 통합이 필요합니다.",
        },
        "AI Automation in Marketing Execution": {
            "summary": (
                "마케팅 실행 단계에서 AI 자동화가 캠페인 셋업, 크리에이티브 생성, 입찰 최적화까지 확대되고 있습니다.\n"
                "운영 효율 차이보다 학습 루프의 설계 품질이 성과 격차를 만드는 구간입니다."
            ),
            "key_points": [
                "캠페인 자동화가 실험 주기를 단축 → 학습 속도 확대",
                "광고 운영 핵심이 수작업 최적화에서 모델 피드백 루프 설계로 전환",
                "성과 차이는 도구 사용 여부보다 데이터 파이프라인 성숙도에서 발생",
            ],
            "marketing_insight": "자동화 도입 자체는 경쟁 우위가 아닙니다. 차별화는 '무엇을 자동화하지 않을 것인가'에 대한 판단에서 결정됩니다.",
            "strategic_implication": "테스트-학습 루프를 조직 KPI와 직결시키고, 자동화 범위와 수동 개입 지점을 명확히 구분해야 합니다.",
        },
        "Marketing AI Trend": {
            "summary": (
                "AI가 제작-집행-측정 전 단계를 연결하며 마케팅 조직의 운영 모델 자체를 재편하고 있습니다.\n"
                "플랫폼 정책 변화가 AI 활용 범위와 측정 기준에 직접 영향을 미치는 구간입니다."
            ),
            "key_points": [
                "AI가 제작-집행-측정을 연결 → 조직 운영 모델 재편 압력",
                "플랫폼 정책 변화가 AI 활용 범위와 측정 기준에 직접 영향",
                "브랜드는 거버넌스와 퍼포먼스 혁신을 동시에 요구받는 국면",
            ],
            "marketing_insight": "AI 도입의 병목은 기술이 아니라 조직 구조. 마케팅-데이터-프로덕트 간 협업 모델이 성과를 결정합니다.",
            "strategic_implication": "AI 거버넌스 프레임워크를 수립하고, 마케팅 조직 역할을 '실행'에서 '설계+감독'으로 전환해야 합니다.",
        },
    }
    pool = [{"title": i.get("title", ""), "link": i.get("link", "#")} for i in items[:9]]
    if not pool:
        pool = [{"title": "—", "link": "#"}]
    result = []
    for idx, cat in enumerate(DAILY_DIGEST_CATEGORIES):
        e = fb[cat]
        result.append({
            "title": cat, "summary": e["summary"], "key_points": e["key_points"],
            "sources": pool[idx * 3: idx * 3 + 3] or pool[:2],
            "marketing_insight": e["marketing_insight"],
            "strategic_implication": e["strategic_implication"],
        })
    return result


@st.cache_data(ttl=21600)
def generate_daily_insights(payload: str) -> List[dict]:
    items = json.loads(payload)
    if not items:
        return []
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
            ordered = []
            for cat in DAILY_DIGEST_CATEGORIES:
                m = next((x for x in parsed if x.get("title") == cat), None)
                if m:
                    ordered.append(m)
            if len(ordered) == 3:
                return ordered
    except Exception:
        pass
    return _fallback_daily_insights(items)


# ── render helpers ───────────────────────────────────────────────────

def render_subscribe_bar() -> None:
    """Render a prominent email subscribe banner below the masthead."""
    sub_count = len(get_active_emails())
    next_fire = get_next_fire_time()
    count_text = f"{sub_count}명이 구독 중" if sub_count else ""
    fire_text = f" · 다음 발송: {next_fire}" if next_fire else ""

    st.markdown(
        f"""<div class="subscribe-banner">
            <div class="sub-icon">✉️</div>
            <div class="sub-copy">
                <p class="sub-title">매일 오전 9시, 마케팅 AI 인사이트를 받아보세요</p>
                <p class="sub-desc">Daily Brief를 이메일로 보내드립니다 — 무료 구독</p>
                <p class="sub-count">{count_text}{fire_text}</p>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

    col_input, col_btn = st.columns([4, 1], gap="small")
    with col_input:
        email = st.text_input(
            "email", placeholder="your-email@company.com",
            label_visibility="collapsed", key="subscribe_email",
        )
    with col_btn:
        clicked = st.button("🔔 구독하기", key="subscribe_btn", type="primary", use_container_width=True)

    if clicked and email:
        email_clean = email.strip().lower()
        if "@" not in email_clean or "." not in email_clean:
            st.error("올바른 이메일 주소를 입력해주세요.")
        elif add_subscriber(email_clean):
            st.success(f"🎉 {email_clean} 구독 완료! 매일 오전 9시에 Daily Brief를 보내드립니다.")
        else:
            st.info("이미 구독 중인 이메일입니다.")


def render_masthead(article_count: int = 0) -> None:
    today = datetime.now(KST)
    count_str = f"{article_count}개 기사" if article_count else "수집 중..."
    st.markdown(
        f"""
        <div class="masthead">
            <div class="masthead-left">
                <p class="masthead-wordmark">Marketing AI Brief</p>
                <h1 class="masthead-title">Today's Marketing AI Insight</h1>
            </div>
            <div class="masthead-right">
                <p class="masthead-date">{today.strftime("%Y년 %m월 %d일")} (KST)</p>
                <p class="masthead-count">KR + EN · {count_str}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _get_ai_tools_translations(tools: List[dict]) -> dict:
    """Batch-translate English AI tool titles & descriptions to Korean.

    Uses st.session_state to cache results across reruns so the LLM is
    only called once (until Refresh is pressed).
    """
    cache = st.session_state.setdefault("ai_tools_ko", {})
    en_tools = [(i, t) for i, t in enumerate(tools) if t.get("lang") != "ko"]
    missing = [(i, t) for i, t in en_tools if t.get("id", str(i)) not in cache]

    if missing:
        all_texts: List[str] = []
        for _, t in missing:
            all_texts.append(t.get("title", ""))
            all_texts.append(t.get("content", "")[:120])
        translated = translate_batch(all_texts, target_lang="ko")
        for j, (_, t) in enumerate(missing):
            aid = t.get("id", str(j))
            cache[aid] = {
                "title": translated[j * 2] if j * 2 < len(translated) else t.get("title", ""),
                "desc": translated[j * 2 + 1] if j * 2 + 1 < len(translated) else t.get("content", "")[:120],
            }
    return cache


def render_ai_tools_section(tools: List[dict]) -> None:
    """Render AI tool news — always translated to Korean."""
    if not tools:
        return
    st.markdown('<p class="section-lbl">AI 툴 뉴스 모음</p>', unsafe_allow_html=True)
    st.markdown(
        """<div class="ai-tools-header">
            <p class="ai-tools-label">RSS 수집 <span style="background:#E8590C;color:#fff;font-size:9px;font-weight:800;padding:2px 7px;border-radius:10px;margin-left:6px;letter-spacing:.5px;vertical-align:middle">NEW</span></p>
            <div class="ai-tools-line"></div>
        </div>""",
        unsafe_allow_html=True,
    )
    display = tools[:6]

    en_exists = any(t.get("lang") != "ko" for t in display)
    if en_exists:
        with st.spinner("AI 툴 소식 번역 중..."):
            ko_cache = _get_ai_tools_translations(display)
    else:
        ko_cache = {}

    for row_start in range(0, len(display), 3):
        row_items = display[row_start:row_start + 3]
        cols = st.columns(3)
        for col_idx, t in enumerate(row_items):
            aid = t.get("id", "")
            if t.get("lang") != "ko" and aid in ko_cache:
                title_raw = ko_cache[aid]["title"]
                desc_raw = ko_cache[aid]["desc"]
            else:
                title_raw = t.get("title", "")
                desc_raw = t.get("content", "")[:120]
            title = escape(title_raw)
            link = escape(t.get("link", "#"))
            desc = escape(desc_raw)
            source = escape(t.get("source", ""))
            lang_badge = '<span class="a-badge lang-ko">KR</span> ' if t.get("lang") == "ko" else ""
            new_badge = '<span class="a-badge badge-new">NEW</span> ' if t.get("is_new") else ""
            with cols[col_idx]:
                st.markdown(
                    f'<div class="ai-tool-card">'
                    f'<p class="ai-tool-title"><a href="{link}" target="_blank">{title}</a></p>'
                    f'<p class="ai-tool-desc">{desc}</p>'
                    f'<div class="ai-tool-meta">'
                    f'<span class="badge-tool">AI TOOL</span> '
                    f'{new_badge}{lang_badge}'
                    f'<span>{source}</span>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
    st.markdown('<div style="margin-bottom:20px;"></div>', unsafe_allow_html=True)


def render_daily_digest(items: List[dict]) -> None:
    today_items = [
        i for i in items
        if (datetime.now(timezone.utc) - i.get("published_at", datetime.now(timezone.utc))) <= timedelta(days=1)
    ] or items[:12]

    payload = json.dumps([
        {"title": i.get("title", ""), "summary": (i.get("content") or "")[:260],
         "link": i.get("link", ""), "category": i.get("category", "")}
        for i in today_items[:20]
    ], ensure_ascii=False)

    with st.spinner("Daily Digest 생성 중..."):
        insights = generate_daily_insights(payload)
    if not insights:
        return

    cards_html = ""
    for idx, ins in enumerate(insights[:3], 1):
        title = escape(ins.get("title") or "")
        tag = escape(ins.get("tag") or "")
        key_point = escape(ins.get("key_point") or "")
        body_main = ins.get("marketing_insight") or ins.get("body") or ins.get("summary") or ""
        body_supp = ins.get("strategic_implication") or ""
        full_body = escape(body_main)
        if body_supp and body_supp != body_main:
            full_body += f'<br><br><span style="color:var(--text-muted);font-size:12.5px;font-style:italic;">{escape(body_supp)}</span>'

        tag_html = f'<span class="ins-tag">{tag}</span>' if tag else ""
        kp_html = (
            f'<p class="ins-keypoint"><span class="ins-kp-label">핵심 포인트</span>{key_point}</p>'
            if key_point else ""
        )
        sources = ins.get("sources") or []
        src_tags = "".join(
            f'<span class="ins-src"><a href="{escape(s.get("link","#"))}" target="_blank" '
            f'style="color:inherit;text-decoration:none;">{escape(s.get("title","—")[:50])}</a></span>'
            for s in sources[:3]
        )
        src_html = (
            f'<div class="ins-sources"><span class="ins-src-label">관련 기사</span>{src_tags}</div>'
            if src_tags else ""
        )
        cards_html += f"""
        <div class="insight-card">
            <div class="ins-card-header">
                <span class="ins-badge">0{idx}</span>
                {tag_html}
            </div>
            <h3 class="ins-title">{title}</h3>
            {kp_html}
            <p class="ins-text">{full_body}</p>
            {src_html}
        </div>"""

    st.markdown(
        f'<p class="section-lbl">오늘의 마케팅 인사이트</p>'
        f'<div class="insight-cards">{cards_html}</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<hr class="section-divider">', unsafe_allow_html=True)


@st.cache_data(ttl=3600)
def _get_youtube_videos() -> List[dict]:
    try:
        from collect_news import fetch_youtube_ai_news
        videos = fetch_youtube_ai_news(limit=12, days=14)
        for v in videos:
            if hasattr(v.get("published_at"), "isoformat"):
                v["published_at"] = v["published_at"].isoformat()
        return videos
    except Exception:
        return []


def render_youtube_section() -> None:
    """Compact YouTube block (same HTML as GitHub Pages)."""
    videos = _get_youtube_videos()
    if not videos:
        return
    st.markdown(_render_youtube_section(videos), unsafe_allow_html=True)
    st.markdown('<hr class="section-divider">', unsafe_allow_html=True)


def _summary_to_bullets_html(summary: str) -> str:
    lines = [ln.strip().lstrip("- ").strip() for ln in summary.strip().splitlines() if ln.strip().startswith("-")]
    if not lines:
        return f"<li>{escape(summary[:120])}</li>"
    return "".join(f"<li>{escape(ln)}</li>" for ln in lines[:3])


def render_article_list(items: List[dict], total: int, summary_mode: str, language: str) -> None:
    shown = len(items)
    label = f"Articles — {shown} / {total}개" if shown < total else f"Articles — {total}개"
    st.markdown(f'<p class="section-lbl">{escape(label)}</p>', unsafe_allow_html=True)

    for row_start in range(0, len(items), 3):
        row_items = items[row_start: row_start + 3]
        cols = st.columns(3, gap="medium")
        for col_idx, item in enumerate(row_items):
            idx = row_start + col_idx
            aid = item.get("id") or item.get("link") or f"a-{idx}"
            title = item.get("title", "")
            link = escape(item.get("link") or "#")
            source = escape(item.get("source", ""))
            date_str = escape(item.get("published_str", "")[:10])
            is_new = item.get("is_new", False)
            summary = _get_summary(aid, summary_mode)

            item_lang = item.get("lang", "en")
            if language == "Korean" and item_lang != "ko":
                title_view = escape(translate_text(title, "ko") or title)
                summary_view = translate_text(summary, "ko") or summary if summary else summary
            else:
                title_view = escape(title)
                summary_view = summary

            lang_badge = '<span class="lang-ko">KR</span>' if item_lang == "ko" else ""
            research_badge = '<span class="badge-research">Research</span>' if item.get("is_research") else ""
            bullets_html = _summary_to_bullets_html(summary_view) if summary_view else ""

            new_badge = '<span class="badge-new">NEW</span>' if is_new else ""
            with cols[col_idx]:
                st.markdown(
                    f"""
                    <div class="a-card">
                        <div class="a-card-tags">{research_badge}{lang_badge}{new_badge}<span class="a-src">{source}</span></div>
                        <h3 class="a-title"><a href="{link}" target="_blank">{title_view}</a></h3>
                        <ul class="a-kp">{bullets_html}</ul>
                        <p class="a-meta">{date_str}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# ── main ─────────────────────────────────────────────────────────────

def render_coming_soon(period: str, description: str) -> None:
    icon = "📊" if period == "Weekly" else "📈"
    st.markdown(
        f"""
        <div class="coming-soon">
            <div class="coming-soon-icon">{icon}</div>
            <h3 class="coming-soon-title">{period} Report</h3>
            <p class="coming-soon-desc">{description}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_daily_tab(items: List[dict], summary_mode: str, language: str) -> None:
    render_daily_digest(items)
    render_youtube_section()

    query = st.text_input(
        "search", placeholder="키워드로 검색...",
        label_visibility="collapsed", key="search_daily",
    )
    filtered = search_filter(items, query)

    if "visible" not in st.session_state:
        st.session_state["visible"] = 9
    sig = f"{query}|{len(filtered)}"
    if st.session_state.get("sig") != sig:
        st.session_state["sig"] = sig
        st.session_state["visible"] = 9

    show = min(st.session_state["visible"], len(filtered))

    # Render articles immediately — users see titles/metadata without waiting for AI
    render_article_list(filtered[:show], total=len(filtered), summary_mode=summary_mode, language=language)

    # Generate missing summaries AFTER rendering so the page isn't blank while waiting
    missing = [
        item for item in filtered[:show]
        if not _get_summary(item.get("id") or item.get("link") or "", summary_mode)
    ]
    if missing:
        with st.spinner(f"AI 인사이트 분석 중... ({len(missing)}건)"):
            _auto_summarize(filtered[:show], summary_mode)
        st.rerun()

    if show < len(filtered):
        if st.button(f"더보기 ({show}/{len(filtered)})"):
            st.session_state["visible"] = min(show + 9, len(filtered))
            st.rerun()


# ── period report generation (weekly / monthly) ─────────────────────

_WEEKLY_PROMPT = """You are a global marketing strategist writing a premium WEEKLY trend report.
Analyze {count} articles from the past 7 days and produce a structured weekly brief.

Return ONLY valid JSON with this structure:
{{
  "period": "weekly",
  "headline": "one-line headline summarizing the week",
  "executive_summary": "3-4 sentences: what happened this week and why it matters",
  "trend_sections": [
    {{
      "category": "Generative Engine Optimization",
      "summary": "2-3 sentences on this category's weekly developments",
      "key_points": ["point1", "point2", "point3"],
      "notable_sources": ["source name 1", "source name 2"]
    }},
    {{
      "category": "AI Automation in Marketing Execution",
      "summary": "...", "key_points": ["..."], "notable_sources": ["..."]
    }},
    {{
      "category": "Marketing AI Trend",
      "summary": "...", "key_points": ["..."], "notable_sources": ["..."]
    }}
  ],
  "top_sources": ["most cited source 1", "source 2", "source 3"],
  "strategic_outlook": "2-3 sentences: what to watch next week"
}}

Rules: insight-focused, NOT headline summaries. Consulting report tone. Korean output.
No markdown, no explanation outside JSON.

Articles:
{payload}"""

_MONTHLY_PROMPT = """You are a global marketing strategist writing a premium MONTHLY strategic brief.
Analyze {count} articles from the past 30 days and produce a comprehensive monthly report.

Return ONLY valid JSON with this structure:
{{
  "period": "monthly",
  "headline": "one-line headline for the month",
  "executive_summary": "5-6 sentences: month in review, major shifts, strategic implications",
  "trend_sections": [
    {{
      "category": "Generative Engine Optimization",
      "summary": "3-4 sentences on monthly evolution",
      "key_points": ["point1", "point2", "point3"],
      "trend_direction": "accelerating / stable / emerging / declining",
      "notable_sources": ["source 1", "source 2"]
    }},
    {{
      "category": "AI Automation in Marketing Execution",
      "summary": "...", "key_points": ["..."], "trend_direction": "...", "notable_sources": ["..."]
    }},
    {{
      "category": "Marketing AI Trend",
      "summary": "...", "key_points": ["..."], "trend_direction": "...", "notable_sources": ["..."]
    }}
  ],
  "source_analysis": {{
    "total_articles": {count},
    "top_sources": ["source1", "source2", "source3", "source4", "source5"],
    "language_split": "KR X% / EN Y%"
  }},
  "strategic_recommendations": [
    "recommendation 1",
    "recommendation 2",
    "recommendation 3"
  ],
  "next_month_outlook": "2-3 sentences: what to watch"
}}

Rules: deep strategic insight. NOT summaries. Consulting report. Korean output.
No markdown, no explanation outside JSON.

Articles:
{payload}"""


def _build_payload(items: List[dict], max_items: int = 40) -> str:
    return json.dumps([
        {
            "title": i.get("title", ""),
            "source": i.get("source", ""),
            "date": i.get("published_str", ""),
            "summary": (i.get("content") or "")[:200],
            "lang": i.get("lang", "en"),
        }
        for i in items[:max_items]
    ], ensure_ascii=False)


def _generate_period_report(items: List[dict], period: str) -> dict:
    """Generate a weekly or monthly report via LLM, with fallback."""
    payload = _build_payload(items, max_items=50 if period == "monthly" else 30)
    prompt_tpl = _MONTHLY_PROMPT if period == "monthly" else _WEEKLY_PROMPT
    prompt = prompt_tpl.format(count=len(items), payload=payload)

    try:
        from ollama_client import ollama_generate
        raw = ollama_generate(prompt, timeout=180, retries=1)
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "trend_sections" in parsed:
            parsed["generated_at"] = datetime.now(KST).isoformat()
            parsed["article_count"] = len(items)
            return parsed
    except Exception:
        pass

    return _fallback_period_report(items, period)


def _fallback_period_report(items: List[dict], period: str) -> dict:
    src_counter = Counter(i.get("source", "Unknown") for i in items)
    top_sources = [s for s, _ in src_counter.most_common(5)]
    ko_count = sum(1 for i in items if i.get("lang") == "ko")
    en_count = len(items) - ko_count
    total = len(items) or 1

    if period == "monthly":
        return {
            "period": "monthly",
            "headline": f"이번 달 마케팅 AI 동향: {len(items)}건의 기사 분석",
            "executive_summary": (
                f"지난 30일간 {len(items)}건의 마케팅 AI 관련 기사가 수집되었습니다. "
                "생성형 AI의 검색 최적화 적용, 마케팅 자동화 확산, 그리고 AI 거버넌스 이슈가 주요 흐름입니다. "
                "컨설팅 기관과 테크 미디어 모두 AI의 전략적 활용 역량을 핵심 경쟁력으로 지목하고 있으며, "
                "퍼스트파티 데이터 기반 의사결정 체계 구축이 공통 과제로 부상했습니다."
            ),
            "trend_sections": [
                {
                    "category": "Generative Engine Optimization",
                    "summary": "AI 기반 검색 응답이 확대되며 콘텐츠의 인용 가능성(citability)이 핵심 지표로 자리잡고 있습니다.",
                    "key_points": [
                        "검색 결과 내 생성형 응답 비중이 월간 기준 지속 확대",
                        "콘텐츠 구조화 데이터 품질이 AI 인용 확률을 직접 좌우",
                        "GEO 전략이 기존 SEO 파이프라인에 통합되는 추세",
                    ],
                    "trend_direction": "accelerating",
                    "notable_sources": top_sources[:2],
                },
                {
                    "category": "AI Automation in Marketing Execution",
                    "summary": "캠페인 자동화가 크리에이티브 생성과 입찰 최적화를 넘어 전략 수립 영역까지 확장 중입니다.",
                    "key_points": [
                        "자동화의 차별화 포인트가 도구 도입에서 학습 루프 설계로 이동",
                        "AI 에이전트 기반 마케팅 운영이 초기 도입 단계 진입",
                        "데이터 파이프라인 성숙도가 자동화 성과 격차의 핵심 변수",
                    ],
                    "trend_direction": "accelerating",
                    "notable_sources": top_sources[:2],
                },
                {
                    "category": "Marketing AI Trend",
                    "summary": "AI가 마케팅 조직의 역할 자체를 재정의하며, 거버넌스와 혁신의 균형이 핵심 과제입니다.",
                    "key_points": [
                        "마케팅 팀 역할이 '실행'에서 '설계+감독'으로 전환 압력",
                        "플랫폼 정책 변화가 AI 활용 범위에 직접 영향",
                        "브랜드 데이터 기반 의사결정 체계 강화가 공통 어젠다",
                    ],
                    "trend_direction": "stable",
                    "notable_sources": top_sources[:2],
                },
            ],
            "source_analysis": {
                "total_articles": len(items),
                "top_sources": top_sources,
                "language_split": f"KR {ko_count*100//total}% / EN {en_count*100//total}%",
            },
            "strategic_recommendations": [
                "AI 활용 역량과 퍼스트파티 데이터 기반 의사결정 체계를 동시에 강화",
                "GEO 체크리스트를 콘텐츠 파이프라인에 통합하고 인용 가능성 지표 도입",
                "마케팅 조직 내 AI 거버넌스 프레임워크 수립 및 역할 재정의",
            ],
            "next_month_outlook": "생성형 AI 기반 광고 플랫폼의 신규 기능 출시와 주요 컨설팅 리포트 발행이 예상됩니다.",
            "generated_at": datetime.now(KST).isoformat(),
            "article_count": len(items),
        }
    else:
        return {
            "period": "weekly",
            "headline": f"이번 주 마케팅 AI 핵심 동향: {len(items)}건 분석",
            "executive_summary": (
                f"지난 7일간 {len(items)}건의 기사를 분석했습니다. "
                "검색 AI 최적화, 마케팅 자동화 확대, AI 트렌드 변화가 주요 흐름입니다. "
                "실행 자동화가 확산되며 차별화 포인트가 운영 효율에서 전략 설계 역량으로 이동하고 있습니다."
            ),
            "trend_sections": [
                {
                    "category": "Generative Engine Optimization",
                    "summary": "검색 인터페이스의 생성형 응답 전환이 가속화되며 기존 SEO 전략의 재편이 요구됩니다.",
                    "key_points": [
                        "검색 노출 경쟁 축이 키워드 순위에서 AI 인용 가능성으로 이동",
                        "구조화 데이터 품질이 도달 효율의 핵심 변수로 부상",
                        "콘텐츠 설계 기준의 CTR→citability 전환 필요",
                    ],
                    "notable_sources": top_sources[:2],
                },
                {
                    "category": "AI Automation in Marketing Execution",
                    "summary": "자동화 범위가 확대되며 학습 루프 설계와 데이터 파이프라인 성숙도가 성과를 결정짓고 있습니다.",
                    "key_points": [
                        "캠페인 자동화가 셋업-크리에이티브-입찰 전 영역으로 확대",
                        "자동화 도입 자체보다 피드백 루프 설계 품질이 핵심",
                        "AI 에이전트 기반 운영 모델의 초기 실험 확산",
                    ],
                    "notable_sources": top_sources[:2],
                },
                {
                    "category": "Marketing AI Trend",
                    "summary": "AI가 마케팅 전 영역을 연결하며 조직 운영 모델 자체의 전환이 가속화되고 있습니다.",
                    "key_points": [
                        "AI 도입의 병목이 기술에서 조직 구조로 이동",
                        "플랫폼 정책 변화가 AI 활용 전략에 직접 영향",
                        "마케팅-데이터-프로덕트 간 협업 모델이 성과 결정",
                    ],
                    "notable_sources": top_sources[:2],
                },
            ],
            "top_sources": top_sources,
            "strategic_outlook": "다음 주에는 주요 테크 플랫폼의 AI 기능 업데이트와 컨설팅 기관의 분기 리포트에 주목할 필요가 있습니다.",
            "generated_at": datetime.now(KST).isoformat(),
            "article_count": len(items),
        }


def _get_or_generate_report(period: str, days: int, min_articles: int) -> tuple[dict | None, int]:
    """Return (report, article_count). Generates if needed, caches to disk."""
    archived = _get_archived_items(days=days)
    count = len(archived)

    if count < min_articles:
        return None, count

    now = datetime.now(KST)
    if period == "weekly":
        report_key = f"weekly-{now.strftime('%Y-W%W')}"
    else:
        report_key = f"monthly-{now.strftime('%Y-%m')}"

    reports = _load_reports()
    existing = reports.get(report_key)
    if existing:
        return existing, count

    report = _generate_period_report(archived, period)
    _save_report(report_key, report)
    return report, count


def _render_trend_card(section: dict, num: str) -> str:
    bullets = "".join(f"<li>{escape(k)}</li>" for k in section.get("key_points", [])[:4])
    direction = section.get("trend_direction", "")
    dir_badge = ""
    if direction:
        dir_colors = {
            "accelerating": ("var(--accent)", "var(--accent-soft)"),
            "emerging": ("#5B6E00", "rgba(120,140,0,0.1)"),
            "stable": ("var(--text-muted)", "var(--bg-elevated)"),
            "declining": ("#7A5500", "rgba(200,140,0,0.1)"),
        }
        col, bg = dir_colors.get(direction, ("var(--text-muted)", "var(--bg-elevated)"))
        dir_badge = f'<span style="font-size:9px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;padding:2px 7px;border-radius:4px;color:{col};background:{bg};">{escape(direction)}</span>'

    sources_html = ""
    notable = section.get("notable_sources", [])
    if notable:
        sources_html = '<p class="digest-lbl">Notable Sources</p><p class="digest-val">' + ", ".join(escape(s) for s in notable[:3]) + "</p>"

    return f"""
    <div class="digest-card">
        <p class="digest-num">{num} {dir_badge}</p>
        <h2 class="digest-title">{escape(section.get("category", ""))}</h2>
        <p class="digest-body">{escape(section.get("summary", ""))}</p>
        <ul class="digest-bullets">{bullets}</ul>
        {sources_html}
    </div>
    """


def _render_period_report(report: dict, period_label: str) -> None:
    st.markdown(f'<p class="section-lbl">{escape(period_label)}</p>', unsafe_allow_html=True)

    headline = report.get("headline", "")
    exec_summary = report.get("executive_summary", "")
    generated = report.get("generated_at", "")[:16]
    article_count = report.get("article_count", 0)

    st.markdown(
        f"""
        <div style="background:var(--bg-elevated);border:1px solid var(--border);border-radius:var(--radius);
                    padding:22px 24px;margin-bottom:24px;">
            <h2 style="font-size:18px;font-weight:700;color:var(--text-primary);margin:0 0 12px;letter-spacing:-0.2px;">
                {escape(headline)}
            </h2>
            <p style="font-size:13px;line-height:1.8;color:var(--text-secondary);margin:0 0 10px;">
                {escape(exec_summary)}
            </p>
            <p style="font-size:10px;color:var(--text-faint);margin:0;">
                {article_count}건 기사 분석 · 생성: {escape(generated)}
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    sections = report.get("trend_sections", [])
    if sections:
        cols = st.columns(min(3, len(sections)), gap="medium")
        for idx, sec in enumerate(sections[:3]):
            with cols[idx]:
                st.markdown(_render_trend_card(sec, f"0{idx+1}"), unsafe_allow_html=True)

    st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

    source_analysis = report.get("source_analysis")
    if source_analysis:
        top_src = source_analysis.get("top_sources", [])
        lang_split = source_analysis.get("language_split", "")
        src_pills = " ".join(
            f'<span style="display:inline-block;font-size:11px;padding:3px 10px;border-radius:12px;'
            f'background:var(--accent-soft);color:var(--accent-text);margin:2px 3px;">{escape(s)}</span>'
            for s in top_src[:6]
        )
        st.markdown(
            f"""
            <div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:20px;">
                <div>
                    <p style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
                              color:var(--text-faint);margin:0 0 6px;">Top Sources</p>
                    <div>{src_pills}</div>
                </div>
                <div>
                    <p style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
                              color:var(--text-faint);margin:0 0 6px;">Language</p>
                    <p style="font-size:13px;color:var(--text-secondary);margin:0;">{escape(lang_split)}</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    recs = report.get("strategic_recommendations", [])
    if recs:
        rec_html = "".join(f"<li>{escape(r)}</li>" for r in recs[:5])
        st.markdown(
            f"""
            <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);
                        padding:18px 20px;margin-bottom:20px;">
                <p style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
                          color:var(--accent);margin:0 0 10px;">Strategic Recommendations</p>
                <ul style="margin:0;padding-left:16px;">{rec_html}</ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

    outlook = report.get("strategic_outlook") or report.get("next_month_outlook", "")
    if outlook:
        st.markdown(
            f"""
            <div style="background:var(--accent-soft);border:1px solid var(--border);border-radius:var(--radius);
                        padding:16px 20px;">
                <p style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
                          color:var(--accent);margin:0 0 6px;">Outlook</p>
                <p style="font-size:13px;line-height:1.7;color:var(--text-secondary);margin:0;">
                    {escape(outlook)}
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_weekly_tab() -> None:
    report, count = _get_or_generate_report("weekly", days=7, min_articles=15)
    if report is None:
        render_coming_soon(
            "Weekly",
            f"주간 리포트는 최소 15건의 기사가 축적되면 자동 생성됩니다. "
            f"현재 {count}건이 수집되었습니다. 매일 방문하면 데이터가 누적됩니다.",
        )
    else:
        with st.spinner("Weekly Report 생성 중..."):
            _render_period_report(report, f"Weekly Report — {count}건 분석")


def _render_monthly_tab() -> None:
    report, count = _get_or_generate_report("monthly", days=30, min_articles=30)
    if report is None:
        render_coming_soon(
            "Monthly",
            f"월간 리포트는 최소 30건의 기사가 축적되면 자동 생성됩니다. "
            f"현재 {count}건이 수집되었습니다.",
        )
    else:
        with st.spinner("Monthly Report 생성 중..."):
            _render_period_report(report, f"Monthly Report — {count}건 분석")


@st.cache_resource
def _init_scheduler():
    """Start the background email scheduler once per app lifetime."""
    if is_configured():
        return start_scheduler(hour=9, minute=0)
    return None


def main() -> None:
    st.markdown(NEWSLETTER_CSS, unsafe_allow_html=True)

    _init_scheduler()

    with st.sidebar:
        st.header("Settings")
        feed_input = st.text_area("RSS feeds", value="\n".join(DEFAULT_FEEDS), height=120)
        language = st.selectbox("Language", ["Original", "Korean"], index=0)
        summary_mode = st.selectbox("Analysis depth", ["short", "medium", "long"], index=1)
        limit = st.slider("Max articles", 12, 90, 30, 3)
        if st.button("Refresh"):
            st.cache_data.clear()
            for key in ["summaries", "article_archive", "visible", "sig", "ai_tools_ko"]:
                st.session_state.pop(key, None)
            st.rerun()

        # ── newsletter admin ──
        st.divider()
        st.subheader("Newsletter")
        subs = load_subscribers()
        active = [e for e, v in subs.items() if v.get("active")]
        st.caption(f"구독자: {len(active)}명")
        next_fire = get_next_fire_time()
        if next_fire:
            st.caption(f"다음 자동 발송: {next_fire}")
        elif not is_configured():
            st.caption("SMTP 미설정 — .env 파일을 확인하세요")

        if is_configured() and active:
            if st.button("지금 발송", key="manual_send"):
                with st.spinner("이메일 발송 중..."):
                    trigger_now()
                st.success("발송 완료!")
        if active:
            with st.expander(f"구독자 목록 ({len(active)}명)"):
                for email in sorted(active):
                    st.text(email)

    feed_urls = tuple(u.strip() for u in feed_input.splitlines() if u.strip())
    if not feed_urls:
        st.warning("RSS feed URL을 하나 이상 입력해주세요.")
        return

    with st.spinner("뉴스를 수집하고 있습니다..."):
        raw = load_news(feed_urls, limit)
    if not raw:
        st.info("수집된 뉴스가 없습니다.")
        return

    items = add_metadata(raw)
    _accumulate_articles(items)

    render_masthead(article_count=len(items))
    st.markdown(_render_tool_directory_table(), unsafe_allow_html=True)

    with st.spinner("AI 툴 소식 수집 중..."):
        ai_tools = load_ai_tools(limit=6)
    render_ai_tools_section(ai_tools)

    tab_daily, tab_weekly, tab_monthly = st.tabs(["Daily Brief", "Weekly Report", "Monthly Report"])

    with tab_daily:
        _render_daily_tab(items, summary_mode, language)

    with tab_weekly:
        _render_weekly_tab()

    with tab_monthly:
        _render_monthly_tab()

    render_subscribe_bar()
    st.markdown(
        '<div class="nl-footer"><p>Marketing AI Brief · Powered by Ollama + Streamlit</p></div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()

# streamlit run app.py --server.address 0.0.0.0 --server.port 8501
