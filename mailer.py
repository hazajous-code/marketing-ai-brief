"""Email delivery for Marketing AI Brief daily newsletter.

Reads SMTP credentials from .env, builds an HTML email from the daily digest,
and sends it to all active subscribers. Degrades gracefully when .env is missing.
"""
from __future__ import annotations

import json
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

_ENV = dotenv_values(Path(__file__).parent / ".env")
SMTP_HOST = _ENV.get("SMTP_HOST", "smtp.office365.com")
SMTP_PORT = int(_ENV.get("SMTP_PORT", "587"))
SMTP_USER = _ENV.get("SMTP_USER", "")
SMTP_PASS = _ENV.get("SMTP_PASS", "")
SMTP_FROM = _ENV.get("SMTP_FROM", "") or SMTP_USER

KST = ZoneInfo("Asia/Seoul")

_DATA_DIR = Path(__file__).parent / "data"
_SUBSCRIBERS_FILE = _DATA_DIR / "subscribers.json"


def is_configured() -> bool:
    return bool(SMTP_USER and SMTP_PASS)


# ── subscriber helpers ───────────────────────────────────────────────

def load_subscribers() -> Dict[str, dict]:
    try:
        if _SUBSCRIBERS_FILE.exists():
            data = json.loads(_SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_subscribers(store: Dict[str, dict]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _SUBSCRIBERS_FILE.write_text(
        json.dumps(store, ensure_ascii=False, default=str), encoding="utf-8",
    )


def add_subscriber(email: str) -> bool:
    """Add a subscriber. Returns True if newly added, False if duplicate."""
    email = email.strip().lower()
    store = load_subscribers()
    if email in store and store[email].get("active"):
        return False
    store[email] = {
        "subscribed_at": datetime.now(KST).isoformat(),
        "active": True,
    }
    save_subscribers(store)
    return True


def get_active_emails() -> List[str]:
    return [e for e, v in load_subscribers().items() if v.get("active")]


# ── HTML email builder ───────────────────────────────────────────────

def _build_html(
    digest: List[dict],
    articles: List[dict],
    date_str: str,
    ai_tools: List[dict] | None = None,
) -> str:
    """Build a full HTML email from digest insights, AI tools, and top articles."""

    # AI Tools section
    ai_tools_html = ""
    if ai_tools:
        tool_rows = ""
        for t in ai_tools[:5]:
            t_title = escape(t.get("title", ""))
            t_link = escape(t.get("link", "#"))
            t_source = escape(t.get("source", ""))
            t_desc = escape(t.get("content", "")[:100])
            tool_rows += f"""
            <tr><td style="padding:6px 24px;">
                <p style="font-size:13px;margin:0 0 2px;line-height:1.5;">
                    <span style="display:inline-block;font-size:8px;font-weight:700;letter-spacing:0.5px;
                                 text-transform:uppercase;padding:1px 5px;border-radius:3px;
                                 color:#7C3AED;background:rgba(124,58,237,0.08);margin-right:6px;
                                 vertical-align:middle;">AI TOOL</span>
                    <a href="{t_link}" style="color:#C46644;text-decoration:none;font-weight:600;">{t_title}</a>
                    <span style="color:#A09890;font-size:11px;"> — {t_source}</span>
                </p>
                <p style="font-size:11.5px;color:#716D66;margin:0;line-height:1.5;padding-left:62px;">{t_desc}</p>
            </td></tr>"""

        ai_tools_html = f"""
        <tr><td style="padding:18px 24px 8px;">
            <p style="font-size:10px;font-weight:700;letter-spacing:1.8px;text-transform:uppercase;
                      color:#7C3AED;margin:0;">New AI Tools</p>
        </td></tr>
        {tool_rows}
        <tr><td style="padding:0 24px;"><hr style="border:none;border-top:1px solid #E8E3DC;margin:14px 0 0;"></td></tr>
        """

    sections_html = ""
    for idx, ins in enumerate(digest[:3], 1):
        bullets = "".join(
            f'<li style="font-size:14px;line-height:1.7;color:#3D3A35;margin-bottom:4px;padding-left:4px;">'
            f'{escape(k)}</li>'
            for k in ins.get("key_points", [])[:3]
        )
        insight = escape(ins.get("marketing_insight", ""))
        implication = escape(ins.get("strategic_implication", ""))

        sections_html += f"""
        <tr><td style="padding:20px 24px;">
            <p style="font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
                      color:#DA7756;margin:0 0 8px;">0{idx}</p>
            <h2 style="font-size:16px;font-weight:700;color:#1A1916;margin:0 0 10px;line-height:1.4;">
                {escape(ins.get("title", ""))}</h2>
            <p style="font-size:13px;line-height:1.8;color:#3D3A35;margin:0 0 12px;white-space:pre-wrap;">
                {escape(ins.get("summary", ""))}</p>
            <ul style="margin:0 0 14px;padding-left:18px;">{bullets}</ul>
            <p style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
                      color:#DA7756;margin:14px 0 4px;padding-top:12px;border-top:1px solid #E8E3DC;">
                Marketing Insight</p>
            <p style="font-size:13px;line-height:1.7;color:#3D3A35;margin:0 0 8px;">{insight}</p>
            <p style="font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
                      color:#DA7756;margin:10px 0 4px;">Strategic Implication</p>
            <p style="font-size:13px;line-height:1.7;color:#3D3A35;margin:0;">{implication}</p>
        </td></tr>
        <tr><td style="padding:0 24px;"><hr style="border:none;border-top:1px solid #E8E3DC;margin:0;"></td></tr>
        """

    articles_html = ""
    for a in articles[:5]:
        title = escape(a.get("title", ""))
        link = escape(a.get("link", "#"))
        source = escape(a.get("source", ""))
        articles_html += f"""
        <tr><td style="padding:6px 24px;">
            <p style="font-size:13px;margin:0;line-height:1.6;">
                <a href="{link}" style="color:#C46644;text-decoration:none;font-weight:600;">{title}</a>
                <span style="color:#A09890;font-size:11px;"> — {source}</span>
            </p>
        </td></tr>
        """

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#FAF9F7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#FAF9F7;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0"
       style="background:#FFFFFF;border:1px solid #E8E3DC;border-radius:10px;overflow:hidden;">

    <!-- masthead -->
    <tr><td style="padding:28px 24px 20px;border-bottom:1px solid #E8E3DC;">
        <p style="font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;
                  color:#DA7756;margin:0 0 6px;">Marketing AI Brief</p>
        <h1 style="font-size:20px;font-weight:700;color:#1A1916;margin:0 0 4px;line-height:1.3;">
            Today's Marketing AI Insight</h1>
        <p style="font-size:12px;color:#A09890;margin:0;">{escape(date_str)}</p>
    </td></tr>

    <!-- AI tools -->
    {ai_tools_html}

    <!-- digest sections -->
    {sections_html}

    <!-- top articles -->
    <tr><td style="padding:18px 24px 8px;">
        <p style="font-size:10px;font-weight:700;letter-spacing:1.8px;text-transform:uppercase;
                  color:#A09890;margin:0;">Top Articles</p>
    </td></tr>
    {articles_html}

    <!-- footer -->
    <tr><td style="padding:24px;border-top:1px solid #E8E3DC;text-align:center;">
        <p style="font-size:11px;color:#A09890;margin:0;">
            Marketing AI Brief · Powered by Ollama + Streamlit<br>
            이 뉴스레터는 자동 생성되었습니다.
        </p>
    </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# ── send ─────────────────────────────────────────────────────────────

def send_daily_brief(
    digest: List[dict],
    articles: List[dict],
    recipients: List[str] | None = None,
    ai_tools: List[dict] | None = None,
) -> Dict[str, Any]:
    """Send the daily digest email. Returns {"sent": N, "failed": N, "errors": [...]}."""
    if not is_configured():
        return {"sent": 0, "failed": 0, "errors": ["SMTP not configured — check .env"]}

    if recipients is None:
        recipients = get_active_emails()
    if not recipients:
        return {"sent": 0, "failed": 0, "errors": ["No subscribers"]}

    today = datetime.now(KST)
    date_str = today.strftime("%Y년 %m월 %d일 (KST)")
    subject = f"[Marketing AI Brief] {today.strftime('%Y-%m-%d')} Daily Digest"
    html_body = _build_html(digest, articles, date_str, ai_tools=ai_tools)

    sent, failed, errors = 0, 0, []

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)

            for addr in recipients:
                try:
                    msg = MIMEMultipart("alternative")
                    msg["From"] = SMTP_FROM
                    msg["To"] = addr
                    msg["Subject"] = subject
                    msg.attach(MIMEText(html_body, "html", "utf-8"))
                    server.sendmail(SMTP_FROM, addr, msg.as_string())
                    sent += 1
                except Exception as e:
                    failed += 1
                    errors.append(f"{addr}: {e}")
                    logger.warning("Failed to send to %s: %s", addr, e)
    except Exception as e:
        errors.append(f"SMTP connection error: {e}")
        logger.error("SMTP connection failed: %s", e)

    logger.info("Email sent=%d failed=%d", sent, failed)
    return {"sent": sent, "failed": failed, "errors": errors}
