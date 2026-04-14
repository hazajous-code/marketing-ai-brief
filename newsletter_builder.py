"""Static HTML newsletter generator for GitHub Pages.

Reads from the article archive and generates self-contained HTML pages
that can be served statically via GitHub Pages or any web server.

index.html = dashboard (today's full content + archive below)
issues/YYYY-MM-DD.html = individual daily pages
reports/weekly-YYYY-WNN.html = weekly reports
reports/monthly-YYYY-MM.html = monthly reports
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent
_DOCS_DIR = _PROJECT_ROOT / "docs"
_ISSUES_DIR = _DOCS_DIR / "issues"
_REPORTS_DIR = _DOCS_DIR / "reports"
_ARCHIVE_FILE = _PROJECT_ROOT / "data" / "article_archive.json"
_REPORT_FILE = _PROJECT_ROOT / "data" / "generated_reports.json"

_CSS_REL_PATH = "../style.css"
_CSS_INDEX_PATH = "style.css"
_CSS_REPORTS_REL = "../style.css"


def _load_dotenv_for_build() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception:
        pass


def _active_subscriber_count() -> int | None:
    try:
        from mailer import get_active_emails
        n = len(get_active_emails())
        return n if n > 0 else None
    except Exception:
        return None


def _subscribe_banner_html() -> str:
    """Subscribe banner + email field. Formspree / Streamlit URL / mailto from env at build time."""
    _load_dotenv_for_build()
    formspree = os.environ.get("MARKETING_BRIEF_FORMSPREE_ACTION", "").strip()
    streamlit = os.environ.get("MARKETING_BRIEF_STREAMLIT_URL", "").strip()
    mailto = os.environ.get("MARKETING_BRIEF_SUBSCRIBE_MAILTO", "").strip()
    pages_next = os.environ.get("MARKETING_BRIEF_PAGES_URL", "").strip()

    count = _active_subscriber_count()
    count_html = f'<p class="sub-count">{count}명 구독 중</p>' if count else ""

    if formspree:
        next_hidden = (
            f'<input type="hidden" name="_next" value="{escape(pages_next)}" />'
            if pages_next else ""
        )
        form_block = f"""
        <form class="subscribe-form subscribe-form--post" action="{escape(formspree)}" method="POST">
            <input type="hidden" name="_subject" value="Marketing AI Brief 구독 신청" />
            {next_hidden}
            <input type="email" name="email" class="sub-email-input" placeholder="your-email@company.com"
                   required autocomplete="email" aria-label="구독 이메일" />
            <button type="submit" class="sub-submit-btn">🔔 구독하기</button>
        </form>"""
    else:
        cfg = json.dumps({"streamlit": streamlit, "mailto": mailto}, ensure_ascii=False)
        form_block = f"""
        <div class="subscribe-form subscribe-form--js">
            <input type="email" class="sub-email-input" placeholder="your-email@company.com"
                   autocomplete="email" aria-label="구독 이메일" />
            <button type="button" class="sub-submit-btn">🔔 구독하기</button>
        </div>
        <script type="application/json" id="subscribe-cfg-json">{cfg}</script>
        <script>
        (function() {{
          var j = document.getElementById('subscribe-cfg-json');
          if (!j) return;
          var cfg = {{}};
          try {{ cfg = JSON.parse(j.textContent); }} catch (e) {{ return; }}
          var root = j.previousElementSibling;
          if (!root || !root.classList.contains('subscribe-form--js')) return;
          var inp = root.querySelector('.sub-email-input');
          var btn = root.querySelector('.sub-submit-btn');
          if (!inp || !btn) return;
          btn.addEventListener('click', function() {{
            var e = (inp.value || '').trim();
            if (!e) {{ alert('이메일을 입력해 주세요.'); return; }}
            if (cfg.streamlit) {{
              var u = cfg.streamlit + (cfg.streamlit.indexOf('?') > -1 ? '&' : '?') +
                'subscribe_email=' + encodeURIComponent(e);
              window.open(u, '_blank');
              return;
            }}
            if (cfg.mailto) {{
              window.location.href = 'mailto:' + cfg.mailto +
                '?subject=' + encodeURIComponent('[Marketing AI Brief] 구독 신청') +
                '&body=' + encodeURIComponent('구독 이메일: ' + e);
              return;
            }}
            alert('구독 처리 URL이 빌드 시 설정되지 않았습니다. 담당자에게 문의하거나, ' +
              'MARKETING_BRIEF_FORMSPREE_ACTION / MARKETING_BRIEF_STREAMLIT_URL / MARKETING_BRIEF_SUBSCRIBE_MAILTO를 설정한 뒤 publish.py를 다시 실행하세요.');
          }});
        }})();
        </script>"""

    return f"""
    <div class="subscribe-banner js-subscribe-root">
        <div class="sub-icon">📮</div>
        <div class="sub-copy">
            <p class="sub-title">매일 오전 9시, 마케팅 AI 인사이트를 받아보세요</p>
            <p class="sub-desc">데일리 브리프를 이메일로 보내드립니다 — 무료 구독</p>
            {count_html}
        </div>
        {form_block}
    </div>"""


# ── data helpers ─────────────────────────────────────────────────────

def _load_archive() -> Dict[str, dict]:
    try:
        if _ARCHIVE_FILE.exists():
            data = json.loads(_ARCHIVE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _articles_for_date(date_str: str) -> List[dict]:
    store = _load_archive()
    items = [v for v in store.values()
             if isinstance(v.get("published_at", ""), str)
             and v["published_at"][:10] == date_str]
    items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return items


def _all_dates() -> List[str]:
    store = _load_archive()
    dates = set()
    for v in store.values():
        pub = v.get("published_at", "")
        if isinstance(pub, str) and len(pub) >= 10:
            dates.add(pub[:10])
    return sorted(dates, reverse=True)


def _articles_for_range(start: str, end: str) -> List[dict]:
    """Return articles where start <= date <= end."""
    store = _load_archive()
    items = []
    for v in store.values():
        pub = v.get("published_at", "")
        if isinstance(pub, str) and len(pub) >= 10:
            d = pub[:10]
            if start <= d <= end:
                items.append(v)
    items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return items


def _load_insights_for_date(date_str: str) -> List[dict]:
    """Load 3-point marketing insights for a date (Korean, from today's articles)."""
    try:
        if _REPORT_FILE.exists():
            reports = json.loads(_REPORT_FILE.read_text(encoding="utf-8"))
            key = f"insights-{date_str}"
            if key in reports and isinstance(reports[key], list) and len(reports[key]) == 3:
                return reports[key]
    except Exception:
        pass
    return []


def _load_report(key: str) -> dict | None:
    try:
        if _REPORT_FILE.exists():
            reports = json.loads(_REPORT_FILE.read_text(encoding="utf-8"))
            if key in reports and isinstance(reports[key], dict):
                return reports[key]
    except Exception:
        pass
    return None


def _save_report_data(key: str, data) -> None:
    try:
        reports: dict = {}
        if _REPORT_FILE.exists():
            reports = json.loads(_REPORT_FILE.read_text(encoding="utf-8"))
        reports[key] = data
        _REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REPORT_FILE.write_text(json.dumps(reports, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save report: %s", e)


def _get_ai_tools_for_date(date_str: str) -> List[dict]:
    store = _load_archive()
    tools = [v for v in store.values()
             if isinstance(v.get("published_at", ""), str)
             and v["published_at"][:10] == date_str and v.get("is_ai_tool")]
    return tools[:6]


# ── LLM helpers ──────────────────────────────────────────────────────

def _fetch_live_ai_tools(limit: int = 12) -> List[dict]:
    """Fetch AI tool news live (for index page which needs fresh data)."""
    try:
        from collect_news import fetch_ai_tools_news
        tools = fetch_ai_tools_news(limit=limit, content_max=520)
        for t in tools:
            if isinstance(t.get("published_at"), datetime):
                t["published_at"] = t["published_at"].isoformat()
        return tools
    except Exception as e:
        logger.warning("Failed to fetch live AI tools: %s", e)
        return []


_HANGUL_RE = re.compile(r"[\u3130-\u318F\uAC00-\uD7A3]")


def _has_korean(text: str) -> bool:
    return bool(_HANGUL_RE.search(text or ""))


_INSIGHT_TEMPLATES = [
    {
        "tag": "AI & 기술",
        "keywords": ["ai", "llm", "gpt", "claude", "gemini", "model", "anthropic", "openai", "agent", "copilot"],
        "title": "AI 에이전트 경쟁, 마케터에게 '어떤 판단을 자동화할 것인가'를 묻다",
        "key_point": "에이전트 도입 전에 '사람만이 할 수 있는 판단'을 먼저 명확히 정의해야 합니다.",
        "body": (
            "OpenAI·Anthropic·Google이 잇따라 에이전트 기능을 출시하며 광고 집행·리포트 생성·콘텐츠 초안 작성 등 "
            "반복 업무의 자동화가 현실화되고 있습니다. "
            "성과를 낸 팀들의 공통점은 '무엇을 자동화할 것인가'가 아니라 "
            "'어떤 판단은 반드시 사람이 해야 하는가'를 먼저 정의했다는 것입니다. "
            "이 기준 없이 에이전트를 도입하면 자동화 이후 품질 관리 비용이 오히려 증가합니다."
        ),
    },
    {
        "tag": "퍼포먼스",
        "keywords": ["automation", "campaign", "ads", "performance", "targeting", "bidding", "roas", "cpa", "자동화", "광고", "입찰"],
        "title": "완전 자동화 시대의 ROAS: 캠페인 세팅보다 '신호 품질'이 성과를 결정한다",
        "key_point": "Meta Advantage+·Google PMax 환경에서 경쟁 우위는 캠페인 구조가 아닌 데이터 파이프라인 품질에서 납니다.",
        "body": (
            "입찰·타겟팅·소재 배분이 알고리즘에 이관된 자동화 성숙기에, "
            "성과 격차는 모델에 어떤 신호를 제공하는가로 결정됩니다. "
            "핵심 점검 항목은 픽셀 데이터 품질, 전환 이벤트 계층 설계, 오프라인 전환 매칭 체계입니다. "
            "이 세 가지 인프라를 고도화한 팀은 같은 예산으로 20~40%의 성과 격차를 만들어냅니다."
        ),
    },
    {
        "tag": "검색 & 콘텐츠",
        "keywords": ["search", "seo", "geo", "generative", "perplexity", "chatgpt", "content", "검색", "콘텐츠", "생성형"],
        "title": "GEO(Generative Engine Optimization): 클릭률에서 '인용 가능성'으로 성과 기준이 이동",
        "key_point": "AI 검색이 쿼리의 30% 이상을 처리하는 지금, SEO 예산 일부를 GEO 체계 구축에 재배분해야 합니다.",
        "body": (
            "Perplexity·ChatGPT 검색·Google AI Overviews가 주요 쿼리의 상당수를 처리하면서 "
            "클릭 유도형 헤드라인 콘텐츠의 노출 효율이 구조적으로 하락하고 있습니다. "
            "AI가 우선 인용하는 콘텐츠의 공통 속성은 E-E-A-T 신호, 구조화된 Q&A 포맷, "
            "업계 내 권위 있는 외부 링크 프로필입니다. "
            "'우리 브랜드가 AI 답변에 포함되는가'를 추적·개선하는 GEO 모니터링 지표를 지금 설정하세요."
        ),
    },
    {
        "tag": "브랜드 전략",
        "keywords": ["brand", "customer", "experience", "loyalty", "personalization", "crm", "브랜드", "고객", "경험", "충성", "개인화"],
        "title": "초개인화의 역설: 알고리즘이 메시지를 무한 생성할수록 브랜드 일관성이 핵심 자산이 된다",
        "key_point": "AI 출력물 검수 기준을 브랜드 가이드라인으로 재정의하는 '브랜드 거버넌스 레이어' 구축이 시급합니다.",
        "body": (
            "AI 기반 초개인화 도구가 보편화되면서, '어떤 브랜드인가'에 대한 명확한 포지션이 "
            "오히려 더 강력한 차별화 요소가 되고 있습니다. "
            "알고리즘이 무한히 개인화된 메시지를 생성할수록, 일관된 톤·비주얼·가치관이 "
            "실제 구매 결정의 신뢰 앵커로 작동합니다. "
            "지금 해야 할 일은 AI 사용 범위를 늘리는 것이 아니라, "
            "AI 출력물을 어떤 기준으로 검수할 것인가를 브랜드 가이드라인 수준으로 정의하는 것입니다."
        ),
    },
    {
        "tag": "데이터 & 측정",
        "keywords": ["data", "privacy", "cookie", "measurement", "analytics", "mmm", "attribution", "데이터", "개인정보", "측정", "어트리뷰션"],
        "title": "포스트 쿠키 측정 공백: MMM 단독 재도입이 아닌 3단 하이브리드 체계가 정답이다",
        "key_point": "MMM + 인크리멘탈리티 테스트 + 퍼스트파티 클린룸을 결합한 팀이 내년 예산 협상에서 유리한 고지를 점합니다.",
        "body": (
            "라스트클릭 어트리뷰션의 신뢰도가 무너진 상황에서 MMM만 재도입하면 6~8주 데이터 지연으로 "
            "실시간 최적화가 불가합니다. "
            "글로벌 선도 기업들은 ① MMM(장기 예산 배분) ② 인크리멘탈리티 테스트(채널 증분 효과 측정) "
            "③ 퍼스트파티 데이터 클린룸(실시간 시그널)을 병렬 운영하는 3단 체계로 전환 중입니다. "
            "이 인프라 구축에는 6~12개월이 필요하므로, 지금 시작하지 않으면 다음 예산 시즌에 뒤처집니다."
        ),
    },
    {
        "tag": "플랫폼 변화",
        "keywords": ["platform", "meta", "google", "tiktok", "retail", "amazon", "youtube", "플랫폼", "미디어", "리테일"],
        "title": "리테일 미디어 3.0: 구매 의도 신호 활용이 D2C 브랜드의 차세대 퍼포먼스 채널이 된다",
        "key_point": "쿠팡·무신사·네이버쇼핑 등 커머스 플랫폼 광고를 단순 노출이 아닌 구매 데이터 연동 관점에서 재설계해야 합니다.",
        "body": (
            "Amazon Ads를 필두로 리테일 미디어 네트워크가 글로벌 디지털 광고의 3대 축으로 성장하면서, "
            "국내에서도 쿠팡·무신사·네이버쇼핑 광고가 퍼포먼스 마케터의 핵심 채널이 되고 있습니다. "
            "리테일 미디어의 진짜 강점은 노출량이 아니라 구매 직전 의도 신호와 구매 후 행동 데이터의 연동입니다. "
            "크리에이티브 포맷·최소 예산·측정 체계가 기존 플랫폼과 완전히 다르므로, "
            "예산만 이동하고 전담 운영 역량 없이는 기대 ROAS를 달성하기 어렵습니다."
        ),
    },
    {
        "tag": "AI & 기술",
        "keywords": ["tool", "launch", "release", "product", "feature", "microsoft", "apple", "툴", "출시", "기능", "제품"],
        "title": "AI 툴 과잉 시대, '몇 개를 쓰는가'보다 '하나로 무엇을 바꿨는가'가 경쟁력이다",
        "key_point": "파일럿 → 성과 측정 → 전사 확산의 표준 평가 프레임워크 없이 AI 툴을 도입하면 비용만 늘고 성과는 분산됩니다.",
        "body": (
            "주당 수십 개의 AI 마케팅 툴이 출시되는 환경에서 '빠른 채택 = 경쟁력'이라는 압박이 강해지고 있습니다. "
            "그러나 성과와 연결된 팀들의 공통점은 도구 수가 아니라 '하나의 툴로 어떤 프로세스를 근본적으로 바꿨는가'의 명확성입니다. "
            "지금 필요한 것은 AI 툴 레이더 운영이 아니라, "
            "파일럿(4주) → KPI 측정 → 전사 확산 여부 결정의 표준 평가 프레임워크 수립입니다."
        ),
    },
]


def _fallback_three_insights(articles: List[dict]) -> List[dict]:
    """Keyword-matched strategic insights when LLM is unavailable."""
    combined_text = " ".join(
        ((a.get("title") or "") + " " + (a.get("content") or ""))[:300]
        for a in articles[:15]
    ).lower()

    scored: list[tuple[int, dict]] = []
    for tmpl in _INSIGHT_TEMPLATES:
        score = sum(1 for kw in tmpl["keywords"] if kw in combined_text)
        scored.append((score, tmpl))
    scored.sort(key=lambda x: -x[0])

    chosen = [t for _, t in scored[:3]]
    while len(chosen) < 3:
        chosen.append(_INSIGHT_TEMPLATES[len(chosen) % len(_INSIGHT_TEMPLATES)])

    out: List[dict] = []
    for i, tmpl in enumerate(chosen[:3]):
        evid_arts = [
            (a.get("title") or "")[:70]
            for a in articles[:10]
            if any(kw in ((a.get("title") or "") + (a.get("content") or "")).lower() for kw in tmpl["keywords"])
        ][:2]
        out.append({
            "title": tmpl["title"],
            "key_point": tmpl.get("key_point", ""),
            "body": tmpl["body"],
            "tag": tmpl.get("tag", ""),
            "evidence": evid_arts,
        })
    return out[:3]


def _generate_three_marketing_insights(articles: List[dict]) -> List[dict]:
    """LLM: exactly 3 insight points from today's collected articles (Korean)."""
    from ollama_client import ollama_generate

    if not articles:
        return _fallback_three_insights([])

    lines = []
    for a in articles[:12]:
        t = (a.get("title") or "")[:110]
        lines.append(f"- {t}")
    payload = "\n".join(lines)

    prompt = (
        "You are a global marketing strategy consultant. "
        "You MUST write ALL output in Korean (한국어). Do NOT use Chinese or any other language.\n\n"
        "Based on the articles below, write exactly 3 marketing insights.\n\n"
        "Rules:\n"
        "- No simple article summaries. Interpret what it means for marketers.\n"
        "- Each point covers a different angle (e.g. AI ads/automation, platform/media shifts, brand/customer strategy).\n"
        "- body: 3-4 sentences in Korean. Strategic and specific.\n"
        "- evidence: 1-3 article titles from the list below.\n\n"
        'Output ONLY a JSON array: [{"title":"한국어 인사이트 제목","body":"한국어 3-4문장","evidence":["기사제목1"]}]\n\n'
        f"Articles:\n{payload}"
    )
    try:
        raw = ollama_generate(prompt, timeout=180, retries=1)
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            if isinstance(parsed, list) and len(parsed) >= 3:
                norm: List[dict] = []
                for item in parsed[:3]:
                    if not isinstance(item, dict):
                        continue
                    norm.append({
                        "title": str(item.get("title") or item.get("headline") or "")[:120],
                        "body": str(item.get("body") or item.get("detail") or item.get("summary") or "")[:800],
                        "evidence": [str(e)[:100] for e in (item.get("evidence") or item.get("refs") or [])[:3] if e],
                    })
                if len(norm) == 3 and all(n.get("title") and n.get("body") for n in norm):
                    return norm
    except Exception as e:
        logger.warning("Ollama 3-point insights failed: %s", e)

    return _fallback_three_insights(articles)


# ── AI Tool Category System ──────────────────────────────────────────

_AI_TOOL_DB = _PROJECT_ROOT / "data" / "ai_tools_db.json"

_TOOL_CATEGORIES = {
    "coding": {
        "label": "코딩 · 개발",
        "icon": "💻",
        "keywords": ["code", "coding", "developer", "github", "copilot", "cursor",
                     "ide", "programming", "devtool", "api", "sdk", "debug",
                     "vscode", "코딩", "개발", "프로그래밍", "openclaw"],
    },
    "video": {
        "label": "영상 · 비디오",
        "icon": "🎬",
        "keywords": ["video", "film", "clip", "animation", "editing",
                     "sora", "runway", "pika", "kling", "영상", "비디오",
                     "동영상", "편집", "영화"],
    },
    "image": {
        "label": "이미지 · 디자인",
        "icon": "🎨",
        "keywords": ["image", "design", "photo", "illustration", "graphic",
                     "midjourney", "dall-e", "stable diffusion", "canva",
                     "figma", "adobe", "이미지", "디자인", "그래픽"],
    },
    "music": {
        "label": "음악 · 오디오",
        "icon": "🎵",
        "keywords": ["music", "audio", "sound", "voice", "song", "podcast",
                     "suno", "udio", "음악", "오디오", "음성", "노래"],
    },
    "writing": {
        "label": "글쓰기 · 콘텐츠",
        "icon": "✍️",
        "keywords": ["writing", "copywriting", "content", "blog", "article",
                     "text", "editor", "document", "jasper", "copy.ai",
                     "writesonic", "notion", "글쓰기", "콘텐츠", "문서"],
    },
    "marketing": {
        "label": "마케팅 · 광고",
        "icon": "📈",
        "keywords": ["marketing", "advertising", "ad ", "ads ", "campaign",
                     "seo", "analytics", "crm", "email marketing", "social media",
                     "hubspot", "salesforce", "마케팅", "광고", "분석"],
    },
    "productivity": {
        "label": "생산성 · 업무",
        "icon": "⚡",
        "keywords": ["productivity", "workflow", "automation", "assistant",
                     "agent", "schedule", "project", "task", "meeting",
                     "생산성", "업무", "자동화", "에이전트", "비서"],
    },
    "research": {
        "label": "리서치 · 학습",
        "icon": "🔬",
        "keywords": ["research", "study", "education", "learning", "search",
                     "perplexity", "scholar", "academic", "student",
                     "리서치", "연구", "학습", "검색", "교육"],
    },
    "finance": {
        "label": "금융 · 핀테크",
        "icon": "💰",
        "keywords": ["finance", "fintech", "trading", "investment", "banking",
                     "payment", "금융", "핀테크", "투자", "은행", "증권"],
    },
}


def _classify_tool_category(title: str, content: str) -> str:
    """Classify an AI tool into a category based on title + content keywords."""
    text = (title + " " + content).lower()
    scores: Dict[str, int] = {}
    for cat, info in _TOOL_CATEGORIES.items():
        score = sum(1 for kw in info["keywords"] if kw in text)
        if score > 0:
            scores[cat] = score
    if scores:
        return max(scores, key=scores.get)
    return "productivity"


def _load_tool_db() -> Dict[str, dict]:
    try:
        if _AI_TOOL_DB.exists():
            return json.loads(_AI_TOOL_DB.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_tool_db(db: Dict[str, dict]) -> None:
    _AI_TOOL_DB.parent.mkdir(parents=True, exist_ok=True)
    _AI_TOOL_DB.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_tools_to_db(tools: List[dict]) -> Dict[str, dict]:
    """Merge new tools into the persistent DB. Returns the full DB."""
    db = _load_tool_db()
    for t in tools:
        tool_id = t.get("id") or t.get("link", "")
        if not tool_id:
            continue
        if tool_id not in db:
            cat = _classify_tool_category(
                t.get("title_ko") or t.get("title", ""),
                t.get("summary_ko") or t.get("content", ""),
            )
            db[tool_id] = {
                "title": t.get("title_ko") or t.get("title", ""),
                "link": t.get("link", ""),
                "desc": (t.get("summary_ko") or t.get("content", ""))[:400],
                "source": t.get("source", ""),
                "category": cat,
                "published_at": t.get("published_at", ""),
                "is_new": t.get("is_new", False),
                "added_date": datetime.now().strftime("%Y-%m-%d"),
            }
    _save_tool_db(db)
    return db


_AI_TOOL_DIRECTORY: List[Dict[str, str]] = [
    {"cat": "coding",       "icon": "💻", "name": "GitHub Copilot",    "maker": "GitHub/Microsoft", "desc": "코드 자동완성 · AI 페어 프로그래밍", "url": "https://github.com/features/copilot"},
    {"cat": "coding",       "icon": "💻", "name": "Cursor",            "maker": "Anysphere",        "desc": "AI-네이티브 코드 에디터 (VS Code 기반)", "url": "https://cursor.com"},
    {"cat": "coding",       "icon": "💻", "name": "Replit AI",         "maker": "Replit",           "desc": "브라우저 기반 AI 코딩 환경", "url": "https://replit.com"},
    {"cat": "coding",       "icon": "💻", "name": "Codex CLI",         "maker": "OpenAI",           "desc": "터미널에서 자연어로 코딩", "url": "https://openai.com/index/codex/"},
    {"cat": "writing",      "icon": "✍️", "name": "ChatGPT",           "maker": "OpenAI",           "desc": "범용 AI 어시스턴트 · 글쓰기/분석/코딩", "url": "https://chat.openai.com"},
    {"cat": "writing",      "icon": "✍️", "name": "Claude",            "maker": "Anthropic",        "desc": "장문 분석 · 안전한 AI 어시스턴트", "url": "https://claude.ai"},
    {"cat": "writing",      "icon": "✍️", "name": "Jasper",            "maker": "Jasper AI",        "desc": "마케팅 카피 · 브랜드 콘텐츠 생성", "url": "https://jasper.ai"},
    {"cat": "writing",      "icon": "✍️", "name": "Notion AI",         "maker": "Notion",           "desc": "문서 요약 · 작성 · 브레인스토밍", "url": "https://notion.so/product/ai"},
    {"cat": "writing",      "icon": "✍️", "name": "Gemini",            "maker": "Google",           "desc": "멀티모달 AI · 검색 연동 어시스턴트", "url": "https://gemini.google.com"},
    {"cat": "image",        "icon": "🎨", "name": "Midjourney",        "maker": "Midjourney",       "desc": "고품질 AI 이미지 생성", "url": "https://midjourney.com"},
    {"cat": "image",        "icon": "🎨", "name": "DALL·E 3",          "maker": "OpenAI",           "desc": "텍스트→이미지 생성 (ChatGPT 내장)", "url": "https://openai.com/dall-e-3"},
    {"cat": "image",        "icon": "🎨", "name": "Adobe Firefly",     "maker": "Adobe",            "desc": "상업용 안전 AI 이미지 · 포토샵 통합", "url": "https://firefly.adobe.com"},
    {"cat": "image",        "icon": "🎨", "name": "Canva AI",          "maker": "Canva",            "desc": "디자인 자동화 · 마케팅 에셋 생성", "url": "https://canva.com"},
    {"cat": "image",        "icon": "🎨", "name": "Stable Diffusion",  "maker": "Stability AI",     "desc": "오픈소스 이미지 생성 모델", "url": "https://stability.ai"},
    {"cat": "video",        "icon": "🎬", "name": "Sora",              "maker": "OpenAI",           "desc": "텍스트→비디오 생성", "url": "https://openai.com/sora"},
    {"cat": "video",        "icon": "🎬", "name": "Runway Gen-3",      "maker": "Runway",           "desc": "AI 영상 생성 · 편집 스튜디오", "url": "https://runway.ml"},
    {"cat": "video",        "icon": "🎬", "name": "Pika",              "maker": "Pika Labs",        "desc": "텍스트/이미지→영상 변환", "url": "https://pika.art"},
    {"cat": "video",        "icon": "🎬", "name": "Kling AI",          "maker": "Kuaishou",         "desc": "고해상도 AI 영상 생성", "url": "https://kling.kuaishou.com"},
    {"cat": "music",        "icon": "🎵", "name": "Suno",              "maker": "Suno AI",          "desc": "텍스트→음악 생성 (보컬 포함)", "url": "https://suno.com"},
    {"cat": "music",        "icon": "🎵", "name": "Udio",              "maker": "Udio",             "desc": "AI 작곡 · 다양한 장르 지원", "url": "https://udio.com"},
    {"cat": "music",        "icon": "🎵", "name": "ElevenLabs",        "maker": "ElevenLabs",       "desc": "AI 음성 합성 · 더빙 · TTS", "url": "https://elevenlabs.io"},
    {"cat": "marketing",    "icon": "📈", "name": "HubSpot AI",        "maker": "HubSpot",          "desc": "CRM · 이메일 · 콘텐츠 마케팅 자동화", "url": "https://hubspot.com"},
    {"cat": "marketing",    "icon": "📈", "name": "Semrush AI",        "maker": "Semrush",          "desc": "SEO · 키워드 · 경쟁분석 AI", "url": "https://semrush.com"},
    {"cat": "marketing",    "icon": "📈", "name": "Copy.ai",           "maker": "Copy.ai",          "desc": "마케팅 카피 · 세일즈 이메일 자동 생성", "url": "https://copy.ai"},
    {"cat": "productivity", "icon": "⚡", "name": "Perplexity",        "maker": "Perplexity AI",    "desc": "AI 검색 엔진 · 출처 기반 답변", "url": "https://perplexity.ai"},
    {"cat": "productivity", "icon": "⚡", "name": "Zapier AI",         "maker": "Zapier",           "desc": "앱 간 자동화 워크플로 구축", "url": "https://zapier.com"},
    {"cat": "productivity", "icon": "⚡", "name": "Gamma",             "maker": "Gamma",            "desc": "AI 프레젠테이션 · 문서 자동 생성", "url": "https://gamma.app"},
    {"cat": "research",     "icon": "🔬", "name": "Elicit",            "maker": "Elicit",           "desc": "논문 검색 · 요약 · 연구 보조", "url": "https://elicit.com"},
    {"cat": "research",     "icon": "🔬", "name": "Consensus",         "maker": "Consensus",        "desc": "학술 논문 AI 검색 · 근거 기반 답변", "url": "https://consensus.app"},
    {"cat": "research",     "icon": "🔬", "name": "NotebookLM",        "maker": "Google",           "desc": "문서 기반 AI 연구 노트 · 팟캐스트 생성", "url": "https://notebooklm.google.com"},
]


def _tool_dir_tbody_for_cats(by_cat: Dict[str, list], cat_keys: List[str]) -> str:
    rows = ""
    for cat_key in cat_keys:
        tools = by_cat.get(cat_key, [])
        if not tools:
            continue
        info = _TOOL_CATEGORIES[cat_key]
        rows += f'<tr class="dir-cat-row"><td colspan="3">{info["icon"]} {escape(info["label"])}</td></tr>'
        for t in tools:
            rows += f"""<tr>
                <td class="dir-icon">{t['icon']}</td>
                <td class="dir-name"><a href="{escape(t['url'])}" target="_blank">{escape(t['name'])}</a><span class="dir-maker">{escape(t['maker'])}</span></td>
                <td class="dir-desc">{escape(t['desc'])}</td>
            </tr>"""
    return rows


def _render_tool_directory_table() -> str:
    """Render a compact two-column table of well-known AI tools by category."""
    by_cat: Dict[str, list] = {}
    for t in _AI_TOOL_DIRECTORY:
        by_cat.setdefault(t["cat"], []).append(t)

    present = [ck for ck in _TOOL_CATEGORIES if by_cat.get(ck)]
    left_keys: List[str] = []
    right_keys: List[str] = []
    left_n = right_n = 0
    for ck in present:
        n = len(by_cat[ck])
        if left_n <= right_n:
            left_keys.append(ck)
            left_n += n
        else:
            right_keys.append(ck)
            right_n += n

    thead = '<thead><tr><th class="dir-th-icon"></th><th>이름</th><th>특징</th></tr></thead>'
    col_a = _tool_dir_tbody_for_cats(by_cat, left_keys)
    col_b = _tool_dir_tbody_for_cats(by_cat, right_keys)

    if not col_b.strip():
        return f"""
    <p class="section-label section-label-hero">마케팅·크리에이티브 AI 툴 한눈에</p>
    <div class="tool-dir-wrap tool-dir-hero tool-dir-hero--grid">
        <table class="tool-dir-table tool-dir-table--compact" aria-label="AI 툴 목록">
            {thead}
            <tbody>{col_a}</tbody>
        </table>
    </div>"""

    return f"""
    <p class="section-label section-label-hero">마케팅·크리에이티브 AI 툴 한눈에</p>
    <div class="tool-dir-wrap tool-dir-hero tool-dir-hero--grid">
        <div class="tool-dir-cols" role="presentation">
            <div class="tool-dir-col">
                <table class="tool-dir-table tool-dir-table--compact" aria-label="AI 툴 목록 (1/2)">
                    {thead}
                    <tbody>{col_a}</tbody>
                </table>
            </div>
            <div class="tool-dir-col">
                <table class="tool-dir-table tool-dir-table--compact" aria-label="AI 툴 목록 (2/2)">
                    {thead}
                    <tbody>{col_b}</tbody>
                </table>
            </div>
        </div>
    </div>"""


_TOOL_TRANS_CACHE = _PROJECT_ROOT / "data" / "ai_tools_translation_cache.json"


def _load_tool_trans_cache() -> Dict[str, str]:
    try:
        if _TOOL_TRANS_CACHE.exists():
            return json.loads(_TOOL_TRANS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_tool_trans_cache(cache: Dict[str, str]) -> None:
    _TOOL_TRANS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _TOOL_TRANS_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _localize_ai_tools(tools: List[dict]) -> List[dict]:
    """Titles + summaries in Korean; uses disk cache for translated text."""
    if not tools:
        return []

    cache = _load_tool_trans_cache()
    to_translate: List[str] = []
    to_translate_keys: List[str] = []

    for t in tools:
        title = (t.get("title") or "")[:240]
        body = ((t.get("content") or ""))[:400]
        for text, suffix in [(title, ":title"), (body, ":body")]:
            key = text.strip()
            if key and not _has_korean(key) and key not in cache:
                to_translate.append(key)
                to_translate_keys.append(key)

    if to_translate:
        try:
            from translate import translate_batch
            translated = translate_batch(to_translate, "ko")
            for k, v in zip(to_translate_keys, translated):
                if v and v != k:
                    cache[k] = v
            _save_tool_trans_cache(cache)
        except Exception as e:
            logger.warning("AI tools batch translate failed: %s", e)

    out: List[dict] = []
    for t in tools:
        d = dict(t)
        title = (t.get("title") or "")[:240].strip()
        body = ((t.get("content") or ""))[:400].strip()
        d["title_ko"] = cache.get(title, title)
        d["summary_ko"] = cache.get(body, body)[:560]
        out.append(d)
    return out


def _localize_articles_display(articles: List[dict], limit: int) -> List[dict]:
    """Add display_title / display_snippet in Korean where needed."""
    sliced = [dict(a) for a in articles[:limit]]
    titles_en: List[str] = []
    idx_t: List[int] = []
    snips_en: List[str] = []
    idx_s: List[int] = []
    for i, a in enumerate(sliced):
        tit = a.get("title") or ""
        sn = (a.get("content") or "")[:200]
        a["display_title"] = tit
        a["display_snippet"] = sn
        if not _has_korean(tit):
            idx_t.append(i)
            titles_en.append(tit[:200])
        if not _has_korean(sn):
            idx_s.append(i)
            snips_en.append(sn)
    try:
        from translate import translate_batch
        if titles_en:
            kt = translate_batch(titles_en, "ko")
            for j, i in enumerate(idx_t):
                if j < len(kt) and kt[j]:
                    sliced[i]["display_title"] = kt[j]
        if snips_en:
            ks = translate_batch(snips_en, "ko")
            for j, i in enumerate(idx_s):
                if j < len(ks) and ks[j]:
                    sliced[i]["display_snippet"] = ks[j]
    except Exception as e:
        logger.warning("Article batch translate failed: %s", e)
    return sliced


def _generate_period_report(items: List[dict], period: str) -> dict:
    """Generate a weekly or monthly report via Ollama. Returns report dict."""
    from ollama_client import ollama_generate

    max_items = 15 if period == "monthly" else 10
    titles = "\n".join(
        f"- {i.get('title', '')} ({i.get('source', '')})"
        for i in items[:max_items]
    )

    period_kr = "월간" if period == "monthly" else "주간"
    prompt = (
        f"You are a marketing AI analyst. Write ALL output in Korean (한국어). Do NOT use Chinese.\n"
        f"Analyze {len(items)} articles and write a {period_kr} marketing AI report.\n"
        "Organize into 3 categories: 1)Generative Engine Optimization 2)AI Automation in Marketing Execution 3)Marketing AI Trend\n"
        'JSON format: {"period":"' + period + '","headline":"한국어 한줄 제목","executive_summary":"한국어 3줄 요약",'
        '"trend_sections":[{"category":"카테고리명","summary":"한국어 2줄","key_points":["한국어 핵심1","한국어 핵심2"],"notable_sources":["출처"]}],'
        '"strategic_outlook":"한국어 전망 2줄"}\n'
        f"Articles:\n{titles}"
    )

    try:
        raw = ollama_generate(prompt, timeout=120, retries=2)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict) and "trend_sections" in parsed:
                from zoneinfo import ZoneInfo
                parsed["generated_at"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
                parsed["article_count"] = len(items)
                return parsed
    except Exception as e:
        logger.warning("Ollama %s report generation failed: %s", period, e)

    return _fallback_period_report(items, period)


def _fallback_period_report(items: List[dict], period: str) -> dict:
    src_counter = Counter(i.get("source", "Unknown") for i in items)
    top_sources = [s for s, _ in src_counter.most_common(5)]
    ko_count = sum(1 for i in items if i.get("lang") == "ko")
    en_count = len(items) - ko_count
    total = len(items) or 1
    from zoneinfo import ZoneInfo

    base = {
        "generated_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "article_count": len(items),
        "trend_sections": [
            {"category": "Generative Engine Optimization",
             "summary": "AI 기반 검색 인터페이스 전환이 가속화되며 콘텐츠 인용 가능성이 핵심 지표로 부상했습니다.",
             "key_points": ["검색 노출 경쟁 축이 키워드에서 AI 인용 가능성으로 이동", "구조화 데이터 품질이 도달 효율의 핵심 변수"],
             "notable_sources": top_sources[:2]},
            {"category": "AI Automation in Marketing Execution",
             "summary": "자동화의 차별화 포인트가 도구 도입에서 학습 루프 설계와 전략 수립 영역으로 확장 중입니다.",
             "key_points": ["실행 자동화가 확산되며 차별화 포인트가 전략 설계 역량으로 이동", "AI 에이전트 기반 마케팅 운영 초기 단계 진입"],
             "notable_sources": top_sources[:2]},
            {"category": "Marketing AI Trend",
             "summary": "AI가 마케팅 조직의 역할 자체를 재정의하며 거버넌스와 혁신의 균형이 핵심 과제입니다.",
             "key_points": ["마케팅 팀 역할이 실행에서 설계+감독으로 전환", "브랜드 데이터 기반 의사결정 체계 강화가 공통 어젠다"],
             "notable_sources": top_sources[:2]},
        ],
    }

    if period == "monthly":
        base.update({
            "period": "monthly",
            "headline": f"이번 달 마케팅 AI 동향: {len(items)}건 분석",
            "executive_summary": f"지난 30일간 {len(items)}건의 마케팅 AI 관련 기사가 수집되었습니다. 생성형 AI의 검색 최적화 적용, 마케팅 자동화 확산, AI 거버넌스 이슈가 주요 흐름입니다.",
            "source_analysis": {"total_articles": len(items), "top_sources": top_sources, "language_split": f"KR {ko_count*100//total}% / EN {en_count*100//total}%"},
            "strategic_recommendations": ["AI 활용 역량과 퍼스트파티 데이터 기반 의사결정 체계 동시 강화", "GEO 체크리스트를 콘텐츠 파이프라인에 통합", "마케팅 조직 내 AI 거버넌스 프레임워크 수립"],
            "next_month_outlook": "생성형 AI 기반 광고 플랫폼의 신규 기능 출시와 주요 컨설팅 리포트 발행이 예상됩니다.",
        })
        for s in base["trend_sections"]:
            s["trend_direction"] = "accelerating"
    else:
        base.update({
            "period": "weekly",
            "headline": f"이번 주 마케팅 AI 핵심 동향: {len(items)}건 분석",
            "executive_summary": f"지난 7일간 {len(items)}건의 기사를 분석했습니다. 검색 AI 최적화, 마케팅 자동화 확대, AI 트렌드 변화가 주요 흐름입니다.",
            "top_sources": top_sources[:3],
            "strategic_outlook": "실행 자동화가 확산되며 차별화 포인트가 운영 효율에서 전략 설계 역량으로 이동하고 있습니다.",
        })
    return base


# ── HTML fragment builders ───────────────────────────────────────────

def _ai_tools_glance_html(tools: List[dict]) -> str:
    """Rule-based at-a-glance summary (no LLM): counts by category + top headlines."""
    if not tools:
        return ""
    n = len(tools)
    counter: Counter[str] = Counter()
    for t in tools:
        counter[
            _classify_tool_category(
                t.get("title_ko") or t.get("title", ""),
                t.get("summary_ko") or t.get("content", ""),
            )
        ] += 1
    chips = []
    for cat, cnt in counter.most_common(6):
        info = _TOOL_CATEGORIES.get(cat, {"icon": "🔧", "label": "기타"})
        chips.append(
            f'<span class="glance-chip">{info["icon"]} {escape(info["label"])} '
            f'<strong>{cnt}</strong></span>'
        )
    bullets = []
    for t in tools[:5]:
        title = (t.get("title_ko") or t.get("title") or "").strip()
        link = (t.get("link") or "#").strip()
        if not title:
            continue
        short = title if len(title) <= 80 else title[:77] + "…"
        bullets.append(
            f'<li><a href="{escape(link)}" target="_blank">{escape(short)}</a></li>'
        )
    headlines_block = ""
    if bullets:
        headlines_block = f"""
        <div class="ai-tools-glance-box ai-tools-glance-box--headlines">
            <p class="ai-tools-glance-sub">대표 헤드라인</p>
            <ul class="ai-tools-glance-list">{"".join(bullets)}</ul>
        </div>"""
    chips_html = " ".join(chips)
    return f"""
    <div class="ai-tools-glance">
        <div class="ai-tools-glance-box ai-tools-glance-box--header">
            <p class="ai-tools-glance-title">한눈에 요약</p>
            <p class="ai-tools-glance-meta">이번 수집 <strong>{n}</strong>건 · 분야별 비중</p>
        </div>
        <div class="ai-tools-glance-box ai-tools-glance-box--chips">
            <div class="glance-chips">{chips_html}</div>
        </div>
        {headlines_block}
    </div>"""


def _render_ai_tools_html(ai_tools: List[dict]) -> str:
    """Render AI tool news: glance summary + cards with category tags."""
    if not ai_tools:
        return ""
    glance = _ai_tools_glance_html(ai_tools)
    cards = ""
    for t in ai_tools:
        title = escape(t.get("title_ko") or t.get("title", ""))
        link = escape(t.get("link", "#"))
        desc = escape(t.get("summary_ko") or (t.get("content") or "")[:520])
        source = escape(t.get("source", ""))
        new_badge = '<span class="badge badge-new">신규</span>' if t.get("is_new") else ""
        cat = _classify_tool_category(
            t.get("title_ko") or t.get("title", ""),
            t.get("summary_ko") or t.get("content", ""),
        )
        cat_info = _TOOL_CATEGORIES.get(cat, {"icon": "🔧", "label": "AI 도구"})
        cat_badge = f'<span class="badge badge-tool">{cat_info["icon"]} {escape(cat_info["label"])}</span>'
        cards += f"""
        <div class="ai-tool-card">
            <p class="ai-tool-title"><a href="{link}" target="_blank">{title}</a></p>
            <p class="ai-tool-desc">{desc}</p>
            <div class="ai-tool-meta">{cat_badge}{new_badge}<span>{source}</span></div>
        </div>"""
    return f"""
    <p class="section-label">AI 툴 뉴스 모음</p>
    {glance}
    <div class="ai-tools-header"><span class="ai-tools-label">RSS 수집 <span style="background:#E8590C;color:#fff;font-size:9px;font-weight:800;padding:2px 7px;border-radius:10px;margin-left:6px;letter-spacing:.5px;vertical-align:middle">NEW</span></span><div class="ai-tools-line"></div></div>
    <div class="ai-tools-grid ai-tools-news-grid">{cards}</div>"""


def _render_insights_html(insights: List[dict]) -> str:
    """3 marketing insight cards — badge, tag, title, key_point, body, source chips."""
    if not insights:
        return ""
    cards = ""
    for idx, ins in enumerate(insights[:3], 1):
        title = escape(ins.get("title") or "")
        key_point = escape(ins.get("key_point") or "")
        body = escape(ins.get("body") or "")
        tag = escape(ins.get("tag") or "")
        evid = ins.get("evidence") or []
        tag_html = f'<span class="ins-tag">{tag}</span>' if tag else ""
        kp_html = (
            f'<p class="ins-keypoint"><span class="ins-kp-label">핵심 포인트</span>{key_point}</p>'
            if key_point else ""
        )
        src_tags = "".join(
            f'<span class="ins-src">{escape(str(e)[:55])}</span>'
            for e in evid[:3] if e
        )
        src_html = (
            f'<div class="ins-sources"><span class="ins-src-label">관련 기사</span>{src_tags}</div>'
            if src_tags else ""
        )
        cards += f"""
        <div class="insight-card">
            <div class="ins-card-header">
                <span class="ins-badge">0{idx}</span>
                {tag_html}
            </div>
            <h3 class="ins-title">{title}</h3>
            {kp_html}
            <p class="ins-text">{body}</p>
            {src_html}
        </div>"""
    return f"""
    <p class="section-label">오늘의 마케팅 인사이트</p>
    <div class="insight-cards">{cards}</div>"""


def _render_article_cards(articles: List[dict], limit: int = 18) -> str:
    if not articles:
        return ""
    cards = ""
    for a in articles[:limit]:
        title = escape(a.get("display_title") or a.get("title", ""))
        link = escape(a.get("link", "#"))
        source = escape(a.get("source", ""))
        content = escape(a.get("display_snippet") or (a.get("content") or "")[:200])
        badges = ""
        if a.get("is_new"):
            badges += '<span class="badge badge-new">신규</span>'
        if a.get("lang") == "ko":
            badges += '<span class="badge badge-ko">국문</span>'
        if a.get("is_research"):
            badges += '<span class="badge badge-research">리서치</span>'
        cards += f"""
        <div class="article-card">
            <div class="article-badges">{badges}</div>
            <p class="article-title"><a href="{link}" target="_blank">{title}</a></p>
            <p class="ai-tool-desc">{content}</p>
            <div class="article-meta"><span>{source}</span></div>
        </div>"""
    return f"""
    <p class="section-label">오늘의 기사</p>
    <div class="article-grid">{cards}</div>"""


def _render_period_report_html(report: dict) -> str:
    """Render a weekly or monthly report dict as HTML content fragment."""
    period = report.get("period", "weekly")
    period_label = "주간 리포트" if period == "weekly" else "월간 리포트"

    headline = escape(report.get("headline", ""))
    summary = escape(report.get("executive_summary", ""))

    sections_html = ""
    for idx, sec in enumerate(report.get("trend_sections", [])[:3], 1):
        bullets = "".join(f'<li>{escape(kp)}</li>' for kp in sec.get("key_points", [])[:4])
        direction = ""
        if sec.get("trend_direction"):
            direction = f' <span class="badge badge-new">{escape(sec["trend_direction"])}</span>'
        sources_html = ", ".join(escape(s) for s in sec.get("notable_sources", [])[:3])
        sections_html += f"""
        <div class="digest-card">
            <p class="digest-num">0{idx}{direction}</p>
            <h3 class="digest-title">{escape(sec.get("category", ""))}</h3>
            <p class="digest-summary">{escape(sec.get("summary", ""))}</p>
            <ul class="digest-kp">{bullets}</ul>
            <div class="digest-sources">{sources_html}</div>
        </div>"""

    extra = ""
    if period == "monthly":
        recs = report.get("strategic_recommendations", [])
        if recs:
            rec_items = "".join(f'<li>{escape(r)}</li>' for r in recs[:5])
            extra += f"""
            <p class="section-label">전략 제언</p>
            <ul class="digest-kp" style="margin-bottom:24px">{rec_items}</ul>"""
        sa = report.get("source_analysis", {})
        if sa:
            extra += f"""
            <div class="report-stats">
                <span>총 {sa.get("total_articles", 0)}건</span>
                <span>{escape(sa.get("language_split", ""))}</span>
                <span>주요 출처: {", ".join(escape(s) for s in sa.get("top_sources", [])[:3])}</span>
            </div>"""
        outlook = report.get("next_month_outlook", "")
        if outlook:
            extra += f'<p class="digest-insight" style="margin-top:16px"><strong>다음 달 전망:</strong> {escape(outlook)}</p>'
    else:
        outlook = report.get("strategic_outlook", "")
        if outlook:
            extra += f'<p class="digest-insight" style="margin-top:16px"><strong>전망:</strong> {escape(outlook)}</p>'

    count = report.get("article_count", 0)
    gen_at = report.get("generated_at", "")[:10]

    return f"""
    <p class="section-label">{period_label}</p>
    <div class="report-header">
        <h2 class="report-headline">{headline}</h2>
        <p class="report-meta">{count}건 분석 &middot; {gen_at}</p>
    </div>
    <p class="digest-summary" style="margin-bottom:20px">{summary}</p>
    <div class="digest-grid">{sections_html}</div>
    {extra}"""


# ── full page builders ───────────────────────────────────────────────

def _stylesheet_href(css_path: str) -> str:
    """Cache-bust so GitHub Pages / browsers load fresh style.css after deploys."""
    p = _DOCS_DIR / "style.css"
    try:
        v = str(int(p.stat().st_mtime)) if p.exists() else "1"
    except OSError:
        v = "1"
    return f"{css_path}?v={v}"


def _html_wrapper(title: str, css_path: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)}</title>
    <link rel="stylesheet" href="{_stylesheet_href(css_path)}">
</head>
<body>
<div class="container">
{body}
</div>
</body>
</html>"""


def build_daily_page(
    date_str: str,
    insights: List[dict],
    articles: List[dict],
    ai_tools: List[dict] | None = None,
    prev_date: str | None = None,
    next_date: str | None = None,
) -> str:
    ai_tools_ko = _localize_ai_tools(ai_tools or [])
    articles_ko = _localize_articles_display(articles, 18)
    nav_left = f'<a href="{prev_date}.html">&larr; {prev_date}</a>' if prev_date else '<span></span>'
    nav_right = f'<a href="{next_date}.html">{next_date} &rarr;</a>' if next_date else '<span></span>'
    tool_line = f" · AI 툴 {len(ai_tools_ko)}건" if ai_tools_ko else ""
    tool_dir_html = _render_tool_directory_table()

    body = f"""
    <header class="masthead">
        <div><p class="masthead-wordmark">Marketing AI Brief</p>
        <h1 class="masthead-title">오늘의 마케팅 AI 인사이트</h1></div>
        <div class="masthead-right">
            <p class="masthead-date">{escape(date_str)} (KST)</p>
            <p class="masthead-count">기사 {len(articles)}건{tool_line}</p>
        </div>
    </header>
    <nav class="nav">
        {nav_left}
        <span class="nav-center"><a href="../index.html">전체 목록</a></span>
        {nav_right}
    </nav>
    {tool_dir_html}
    {_render_ai_tools_html(ai_tools_ko)}
    {_render_insights_html(insights)}
    {_render_article_cards(articles_ko)}
    {_subscribe_banner_html()}
    <footer class="footer">
        <p>Marketing AI Brief &middot; Ollama + Streamlit</p>
        <p><a href="../index.html">전체 목록</a></p>
    </footer>"""
    return _html_wrapper(f"Marketing AI Brief — {date_str}", _CSS_REL_PATH, body)


def build_index_page(
    latest_date: str,
    latest_articles: List[dict],
    latest_insights: List[dict],
    latest_ai_tools: List[dict],
    recent_issues: List[dict],
    older_issues: List[dict],
    weekly_reports: List[dict],
    monthly_reports: List[dict],
) -> str:
    """Build the main index.html — dashboard style with today's content + archive."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Seoul"))

    ai_tools_ko = _localize_ai_tools(latest_ai_tools)
    articles_ko = _localize_articles_display(latest_articles, 9)

    subscribe_html = _subscribe_banner_html()

    ai_tools_html = _render_ai_tools_html(ai_tools_ko)
    tool_dir_html = _render_tool_directory_table()
    insights_html = _render_insights_html(latest_insights)
    articles_html = _render_article_cards(articles_ko, limit=9)

    tabs_html = _build_tabs(recent_issues, older_issues, weekly_reports, monthly_reports)
    tool_line = f" · AI 툴 {len(ai_tools_ko)}건" if ai_tools_ko else ""

    body = f"""
    <header class="masthead">
        <div><p class="masthead-wordmark">Marketing AI Brief</p>
        <h1 class="masthead-title">오늘의 마케팅 AI 인사이트</h1></div>
        <div class="masthead-right">
            <p class="masthead-date">최신 이슈일 {escape(latest_date)} · {escape(now.strftime("%Y-%m-%d %H:%M"))} KST</p>
            <p class="masthead-count">기사 {len(latest_articles)}건{tool_line}</p>
        </div>
    </header>
    {tool_dir_html}
    {ai_tools_html}
    {insights_html}
    {articles_html}
    {tabs_html}
    {subscribe_html}
    <footer class="footer">
        <p>Marketing AI Brief &middot; Ollama + Streamlit</p>
    </footer>"""
    return _html_wrapper("Marketing AI Brief", _CSS_INDEX_PATH, body)


def _build_tabs(
    recent_issues: List[dict],
    older_issues: List[dict],
    weekly_reports: List[dict],
    monthly_reports: List[dict],
) -> str:
    """Build tab-like sections for Daily Brief / Weekly / Monthly archives."""

    # ── Daily Archive ──
    recent_cards = ""
    for issue in recent_issues:
        date = escape(issue["date"])
        count = issue.get("article_count", 0)
        tools = issue.get("tool_count", 0)
        meta = f"기사 {count}건"
        if tools:
            meta += f" · AI 툴 {tools}건"
        recent_cards += f"""
        <div class="archive-card">
            <a href="issues/{date}.html" class="archive-card-link">
                <p class="archive-date">{date}</p>
                <p class="archive-meta">{meta}</p>
            </a>
        </div>"""

    older_list = ""
    for issue in older_issues:
        date = escape(issue["date"])
        count = issue.get("article_count", 0)
        older_list += f"""
        <li class="issue-item">
            <a class="issue-date" href="issues/{date}.html">{date}</a>
            <span class="issue-meta">기사 {count}건</span>
            <span class="issue-arrow">&rarr;</span>
        </li>"""

    # ── Weekly Reports ──
    weekly_html = ""
    if weekly_reports:
        items = ""
        for wr in weekly_reports:
            key = escape(wr["key"])
            label = escape(wr["label"])
            count = wr.get("article_count", 0)
            items += f"""
            <li class="issue-item">
                <a class="issue-date" href="reports/{key}.html">{label}</a>
                <span class="issue-meta">기사 {count}건</span>
                <span class="issue-arrow">&rarr;</span>
            </li>"""
        weekly_html = f"""
        <p class="section-label">📊 주간 리포트</p>
        <ul class="issue-list">{items}</ul>"""
    else:
        weekly_html = """
        <p class="section-label">📊 주간 리포트</p>
        <p class="coming-soon">기사가 충분히 누적되면 주간 리포트가 자동 생성됩니다.</p>"""

    # ── Monthly Reports ──
    monthly_html = ""
    if monthly_reports:
        items = ""
        for mr in monthly_reports:
            key = escape(mr["key"])
            label = escape(mr["label"])
            count = mr.get("article_count", 0)
            items += f"""
            <li class="issue-item">
                <a class="issue-date" href="reports/{key}.html">{label}</a>
                <span class="issue-meta">기사 {count}건</span>
                <span class="issue-arrow">&rarr;</span>
            </li>"""
        monthly_html = f"""
        <p class="section-label">📈 월간 리포트</p>
        <ul class="issue-list">{items}</ul>"""
    else:
        monthly_html = """
        <p class="section-label">📈 월간 리포트</p>
        <p class="coming-soon">기사가 충분히 누적되면 월간 리포트가 자동 생성됩니다.</p>"""

    archive_section = ""
    if recent_cards:
        archive_section += f"""
        <p class="section-label">데일리 브리프 아카이브</p>
        <div class="archive-grid">{recent_cards}</div>"""
    if older_list:
        archive_section += f"""
        <details class="archive-older">
            <summary class="archive-toggle">이전 기록 보기 ({len(older_issues)}건)</summary>
            <ul class="issue-list">{older_list}</ul>
        </details>"""

    return f"""
    <div class="tab-section">
        {archive_section}
        {weekly_html}
        {monthly_html}
    </div>"""


def build_report_page(report: dict, report_key: str) -> str:
    """Build an individual weekly/monthly report page."""
    period = report.get("period", "weekly")
    label = "주간 리포트" if period == "weekly" else "월간 리포트"
    body = f"""
    <header class="masthead">
        <div><p class="masthead-wordmark">Marketing AI Brief</p>
        <h1 class="masthead-title">{label}</h1></div>
        <div class="masthead-right">
            <p class="masthead-date">{escape(report.get("generated_at", "")[:10])}</p>
            <p class="masthead-count">{report.get("article_count", 0)}건 분석</p>
        </div>
    </header>
    <nav class="nav">
        <span></span>
        <span class="nav-center"><a href="../index.html">전체 목록</a></span>
        <span></span>
    </nav>
    {_render_period_report_html(report)}
    <footer class="footer">
        <p>Marketing AI Brief &middot; Ollama + Streamlit</p>
        <p><a href="../index.html">전체 목록</a></p>
    </footer>"""
    return _html_wrapper(f"Marketing AI Brief — {label}", _CSS_REPORTS_REL, body)


# ── publish orchestration ────────────────────────────────────────────

def publish_single_date(date_str: str, dates_list: List[str] | None = None, is_latest: bool = False) -> Path | None:
    articles = _articles_for_date(date_str)
    if not articles:
        logger.info("No articles for %s — skipping.", date_str)
        return None

    insights = _load_insights_for_date(date_str)
    if not insights:
        insights = _generate_three_marketing_insights(articles)
        _save_report_data(f"insights-{date_str}", insights)

    ai_tools = _get_ai_tools_for_date(date_str)
    if not ai_tools and is_latest:
        ai_tools = _fetch_live_ai_tools(limit=8)

    if dates_list is None:
        dates_list = _all_dates()
    idx = dates_list.index(date_str) if date_str in dates_list else -1
    prev_date = dates_list[idx + 1] if idx >= 0 and idx + 1 < len(dates_list) else None
    next_date = dates_list[idx - 1] if idx > 0 else None

    html = build_daily_page(date_str, insights, articles, ai_tools, prev_date, next_date)
    _ISSUES_DIR.mkdir(parents=True, exist_ok=True)
    out = _ISSUES_DIR / f"{date_str}.html"
    out.write_text(html, encoding="utf-8")
    logger.info("Generated %s (%d articles)", out, len(articles))
    return out


def _generate_weekly_reports() -> List[dict]:
    """Auto-generate weekly reports for complete weeks with >= 7 articles."""
    from zoneinfo import ZoneInfo
    dates = _all_dates()
    if not dates:
        return []

    all_articles = _load_archive()
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    reports_meta = []
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    seen_weeks = set()
    for d_str in dates:
        d = datetime.strptime(d_str, "%Y-%m-%d")
        week_key = f"weekly-{d.strftime('%Y-W%W')}"
        if week_key in seen_weeks:
            continue
        seen_weeks.add(week_key)

        monday = d - timedelta(days=d.weekday())
        sunday = monday + timedelta(days=6)
        if sunday.date() >= now.date():
            continue

        start_s = monday.strftime("%Y-%m-%d")
        end_s = sunday.strftime("%Y-%m-%d")
        items = _articles_for_range(start_s, end_s)

        if len(items) < 7:
            continue

        report = _load_report(week_key)
        if not report:
            report = _generate_period_report(items, "weekly")
            _save_report_data(week_key, report)

        html = build_report_page(report, week_key)
        out = _REPORTS_DIR / f"{week_key}.html"
        out.write_text(html, encoding="utf-8")

        reports_meta.append({
            "key": week_key,
            "label": f"{start_s} ~ {end_s}",
            "article_count": len(items),
        })
        logger.info("Generated %s (%d articles)", week_key, len(items))

    reports_meta.sort(key=lambda x: x["key"], reverse=True)
    return reports_meta


def _generate_monthly_reports() -> List[dict]:
    """Auto-generate monthly reports for complete months with >= 15 articles."""
    from zoneinfo import ZoneInfo
    dates = _all_dates()
    if not dates:
        return []

    now = datetime.now(ZoneInfo("Asia/Seoul"))
    reports_meta = []
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    seen_months = set()
    for d_str in dates:
        month_key = f"monthly-{d_str[:7]}"
        if month_key in seen_months:
            continue
        seen_months.add(month_key)

        year, month = int(d_str[:4]), int(d_str[5:7])
        if year == now.year and month == now.month:
            continue

        start_s = f"{d_str[:7]}-01"
        if month == 12:
            end_s = f"{year+1}-01-01"
        else:
            end_s = f"{year}-{month+1:02d}-01"
        end_d = datetime.strptime(end_s, "%Y-%m-%d") - timedelta(days=1)
        end_s = end_d.strftime("%Y-%m-%d")

        items = _articles_for_range(start_s, end_s)
        if len(items) < 15:
            continue

        report = _load_report(month_key)
        if not report:
            report = _generate_period_report(items, "monthly")
            _save_report_data(month_key, report)

        html = build_report_page(report, month_key)
        out = _REPORTS_DIR / f"{month_key}.html"
        out.write_text(html, encoding="utf-8")

        reports_meta.append({
            "key": month_key,
            "label": d_str[:7],
            "article_count": len(items),
        })
        logger.info("Generated %s (%d articles)", month_key, len(items))

    reports_meta.sort(key=lambda x: x["key"], reverse=True)
    return reports_meta


def publish_index() -> Path:
    """Regenerate the index.html as a full dashboard."""
    dates = _all_dates()
    if not dates:
        html = _html_wrapper("Marketing AI Brief", _CSS_INDEX_PATH,
                             '<header class="masthead"><div><p class="masthead-wordmark">Marketing AI Brief</p>'
                             '<h1 class="masthead-title">뉴스레터 아카이브</h1></div></header>'
                             '<p class="coming-soon">아직 수집된 기사가 없습니다.</p>')
        out = _DOCS_DIR / "index.html"
        out.write_text(html, encoding="utf-8")
        return out

    latest_date = dates[0]
    latest_articles = _articles_for_date(latest_date)
    latest_insights = _load_insights_for_date(latest_date)
    if not latest_insights:
        latest_insights = _generate_three_marketing_insights(latest_articles)
        _save_report_data(f"insights-{latest_date}", latest_insights)

    # AI tools: try archive first, then fetch live
    latest_ai_tools = _get_ai_tools_for_date(latest_date)
    if not latest_ai_tools:
        latest_ai_tools = _fetch_live_ai_tools(limit=12)

    # Recent = last 7 days of issues (as cards), older = the rest (collapsed)
    recent_issues = []
    older_issues = []
    for d in dates[1:]:  # skip today (already shown as hero)
        arts = _articles_for_date(d)
        tools = [a for a in arts if a.get("is_ai_tool")]
        entry = {"date": d, "article_count": len(arts), "tool_count": len(tools)}
        if len(recent_issues) < 7:
            recent_issues.append(entry)
        else:
            older_issues.append(entry)

    # Weekly / Monthly reports
    weekly_reports = _generate_weekly_reports()
    monthly_reports = _generate_monthly_reports()

    html = build_index_page(
        latest_date, latest_articles, latest_insights, latest_ai_tools,
        recent_issues, older_issues, weekly_reports, monthly_reports,
    )
    _DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out = _DOCS_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    logger.info("Generated index.html (latest: %s)", latest_date)
    return out


def publish_daily(date_str: str | None = None) -> None:
    if date_str is None:
        from zoneinfo import ZoneInfo
        date_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    dates = _all_dates()
    if date_str not in dates:
        dates = sorted(set(dates) | {date_str}, reverse=True)

    publish_single_date(date_str, dates, is_latest=True)
    publish_index()


def publish_all() -> None:
    dates = _all_dates()
    if not dates:
        logger.info("No archived dates found.")
        return
    for d in dates:
        publish_single_date(d, dates, is_latest=(d == dates[0]))
    publish_index()
    logger.info("Published %d issues.", len(dates))


def _find_git() -> str:
    """Find git executable, searching common install paths on Windows."""
    import shutil
    g = shutil.which("git")
    if g:
        return g
    candidates = [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    raise FileNotFoundError("git executable not found. Please install Git or add it to PATH.")


def git_push(message: str | None = None) -> bool:
    if message is None:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
        message = f"Newsletter update {today}"

    try:
        git = _find_git()
        subprocess.run([git, "add", "docs/"], cwd=str(_PROJECT_ROOT), check=True, capture_output=True)
        result = subprocess.run(
            [git, "diff", "--cached", "--quiet"],
            cwd=str(_PROJECT_ROOT), capture_output=True,
        )
        if result.returncode == 0:
            logger.info("No changes to commit.")
            return True
        subprocess.run(
            [git, "commit", "-m", message],
            cwd=str(_PROJECT_ROOT), check=True, capture_output=True,
        )
        subprocess.run(
            [git, "push"],
            cwd=str(_PROJECT_ROOT), check=True, capture_output=True,
        )
        logger.info("Pushed to remote: %s", message)
        return True
    except FileNotFoundError as e:
        logger.error("Git not found: %s", e)
        return False
    except subprocess.CalledProcessError as e:
        logger.error("Git operation failed: %s — %s", e, e.stderr)
        return False
