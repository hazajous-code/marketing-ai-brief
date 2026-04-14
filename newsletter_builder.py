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

def _fetch_live_ai_tools(limit: int = 8) -> List[dict]:
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
        "title": "AI 에이전트 경쟁의 진짜 전장은 마케터의 워크플로우다",
        "body": (
            "OpenAI·Anthropic·Google이 잇따라 에이전트 기능을 내놓으며 '자율 실행' 경쟁이 본격화되고 있습니다. "
            "마케터 입장에서 의미 있는 변화는 모델 성능 자체보다, 이 에이전트들이 광고 집행·리포트 생성·콘텐츠 초안 작성 등 "
            "반복 업무를 실제로 대체하기 시작했다는 사실입니다. "
            "도구 도입보다 앞서야 할 과제는 '어떤 판단은 반드시 사람이 해야 하는가'를 명확히 정의하는 것이며, "
            "이를 정리하지 않은 팀은 자동화 이후 오히려 품질 관리 부담이 늘어날 수 있습니다."
        ),
    },
    {
        "tag": "퍼포먼스",
        "keywords": ["automation", "campaign", "ads", "performance", "targeting", "bidding", "roas", "cpa", "자동화", "광고", "입찰"],
        "title": "자동화 성숙기: 운영 효율이 아닌 신호 품질이 ROAS를 결정한다",
        "body": (
            "Meta Advantage+·Google PMax 등 완전 자동화 캠페인이 기본값이 되면서, "
            "입찰·타겟팅·소재 배분은 이미 알고리즘이 담당하고 있습니다. "
            "성과 격차가 벌어지는 지점은 '어떤 신호를 모델에 주느냐' — 즉 픽셀 데이터 품질, "
            "전환 이벤트 설계, 오프라인 전환 매칭 체계입니다. "
            "지금 점검해야 할 것은 캠페인 세팅이 아니라, 모델에 입력되는 퍼스트파티 데이터의 정합성입니다."
        ),
    },
    {
        "tag": "검색 & 콘텐츠",
        "keywords": ["search", "seo", "geo", "generative", "perplexity", "chatgpt search", "content", "검색", "콘텐츠", "생성형"],
        "title": "GEO 시대, 클릭률 대신 '인용 가능성'이 콘텐츠 성과 지표가 된다",
        "body": (
            "Perplexity·ChatGPT 검색·Google AI Overviews가 쿼리의 30% 이상을 처리하면서, "
            "클릭을 유도하는 헤드라인 중심 콘텐츠의 유효성이 구조적으로 낮아지고 있습니다. "
            "AI가 우선 인용하는 콘텐츠의 공통 속성은 E-E-A-T 신호, 구조화된 답변 형식, "
            "업계 내 외부 링크 권위입니다. "
            "SEO 예산의 일부를 '우리 브랜드가 AI 답변에 포함되는가'를 추적·개선하는 GEO 체계로 "
            "재배분하는 전략을 지금 시작해야 합니다."
        ),
    },
    {
        "tag": "브랜드 전략",
        "keywords": ["brand", "customer", "experience", "loyalty", "personalization", "crm", "브랜드", "고객", "경험", "충성", "개인화"],
        "title": "개인화 고도화 역설: 브랜드 일관성이 다시 핵심 자산이 된다",
        "body": (
            "AI 기반 초개인화가 확산되면서 아이러니하게도 브랜드 정체성의 명확성이 더 중요해지고 있습니다. "
            "알고리즘이 무한히 개인화된 메시지를 생성할수록, '어떤 브랜드인가'에 대한 일관된 톤과 포지션이 "
            "실제 구매 결정의 앵커가 됩니다. "
            "지금 필요한 것은 AI 활용 범위를 넓히는 동시에, 브랜드 가이드라인을 AI 출력물 검수 기준으로 "
            "재정의하는 '브랜드 거버넌스 레이어' 구축입니다."
        ),
    },
    {
        "tag": "데이터 & 측정",
        "keywords": ["data", "privacy", "cookie", "measurement", "analytics", "mmm", "attribution", "데이터", "개인정보", "측정", "어트리뷰션"],
        "title": "포스트 쿠키 측정 공백, MMM 재도입이 아니라 하이브리드 체계가 답이다",
        "body": (
            "서드파티 쿠키 종료 이후 라스트클릭 어트리뷰션의 신뢰도가 무너지면서 "
            "MMM(마케팅 믹스 모델)에 대한 관심이 다시 높아지고 있습니다. "
            "그러나 MMM 단독으로는 6~8주 지연이 발생해 실시간 최적화가 불가합니다. "
            "선도 기업들은 MMM(장기 예산 배분) + 인크리멘탈리티 테스트(채널별 증분 효과) + "
            "퍼스트파티 데이터 클린룸(실시간 시그널)을 결합한 3단 체계로 전환하고 있습니다. "
            "이 인프라를 먼저 갖춘 팀이 내년 예산 시즌의 협상력에서 우위를 점합니다."
        ),
    },
    {
        "tag": "플랫폼 변화",
        "keywords": ["platform", "meta", "google", "tiktok", "retail media", "amazon", "youtube", "platform", "플랫폼", "미디어"],
        "title": "리테일 미디어 급성장, D2C 브랜드의 미디어 믹스 재구성이 필요하다",
        "body": (
            "Amazon Ads·쿠팡 광고·무신사 광고 등 리테일 미디어가 디지털 광고의 3대 축으로 자리잡으면서, "
            "기존 Meta·Google 중심의 퍼포먼스 미디어 믹스를 재구성해야 할 시점이 왔습니다. "
            "리테일 미디어의 핵심 강점은 구매 의도가 가장 높은 순간의 노출이지만, "
            "크리에이티브 포맷·측정 방식·최소 예산 요건이 기존 플랫폼과 완전히 다릅니다. "
            "전담 운영 역량 없이 예산만 이동하면 기대 ROAS를 달성하기 어렵습니다."
        ),
    },
    {
        "tag": "AI & 기술",
        "keywords": ["tool", "launch", "release", "product", "feature", "microsoft", "apple", "툴", "출시", "기능", "제품"],
        "title": "AI 툴 홍수 속에서 마케터의 진짜 질문은 '채택 속도'가 아니다",
        "body": (
            "매주 수십 개의 AI 마케팅 툴이 출시되는 환경에서, 빠른 채택이 곧 경쟁력이라는 인식이 퍼지고 있습니다. "
            "그러나 실제 성과와 연결된 팀들의 공통점은 도구 수가 아니라 '하나의 툴로 어떤 프로세스를 바꿨는가'의 명확성입니다. "
            "마케팅 조직에 지금 필요한 것은 AI 툴 레이더 운영보다, "
            "파일럿 → 성과 측정 → 전사 확산의 표준 평가 프레임워크 수립입니다."
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
            "body": tmpl["body"],
            "tag": tmpl.get("tag", ""),
            "evidence": evid_arts,
        })
    return out[:3]


def _generate_three_marketing_insights(articles: List[dict]) -> List[dict]:
    """LLM: exactly 3 insight points from today's collected articles (Korean)."""
    import requests as _req

    if not articles:
        return _fallback_three_insights([])

    lines = []
    for a in articles[:14]:
        t = (a.get("title") or "")[:110]
        s = ((a.get("content") or "")[:100]).replace("\n", " ")
        lines.append(f"- {t} | {s}")
    payload = "\n".join(lines)

    prompt = (
        "당신은 글로벌 마케팅 전략 컨설턴트입니다.\n"
        "아래 기사 목록을 바탕으로, 마케팅 전문가에게 실질적으로 유용한 인사이트 3개를 작성하세요.\n\n"
        "규칙:\n"
        "- 단순 기사 요약이나 사실 나열 금지. 반드시 '이 상황이 마케터에게 무엇을 의미하는가' 해석 중심으로 작성.\n"
        "- 각 포인트는 서로 다른 관점(예: AI 광고·자동화 / 플랫폼·미디어 변화 / 브랜드·고객전략).\n"
        "- body는 3~4문장. 구체적이고 전략적인 언어로.\n"
        "- evidence에는 위 목록에서 해당 인사이트의 근거가 되는 기사 제목(또는 출처명)을 1~3개.\n\n"
        '출력: JSON 배열만(설명 없이): [{"title":"한 줄 인사이트 제목(한국어)","body":"3~4문장 해석(한국어)","evidence":["기사제목1","기사제목2"]}]\n\n'
        f"기사:\n{payload}"
    )
    try:
        res = _req.post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3.1:8b", "prompt": prompt, "stream": False},
            timeout=60,
        )
        res.raise_for_status()
        raw = res.json().get("response", "").strip()
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


def _localize_ai_tools(tools: List[dict]) -> List[dict]:
    """Titles + summaries in Korean; richer text preserved."""
    if not tools:
        return []
    try:
        from translate import translate_batch
    except Exception:
        return [dict(t, title_ko=t.get("title", ""), summary_ko=(t.get("content") or "")[:520]) for t in tools]

    titles = [(t.get("title") or "")[:240] for t in tools]
    bodies = [((t.get("content") or ""))[:520] for t in tools]
    t_ko = list(titles)
    b_ko = list(bodies)
    try:
        if any(not _has_korean(x) for x in titles):
            t_ko = translate_batch(titles, "ko")
        if any(not _has_korean(x) for x in bodies):
            b_ko = translate_batch(bodies, "ko")
    except Exception as e:
        logger.warning("AI tools batch translate failed: %s", e)

    out: List[dict] = []
    for t, tk, bk in zip(tools, t_ko, b_ko):
        d = dict(t)
        d["title_ko"] = (tk or t.get("title") or "").strip()
        d["summary_ko"] = (bk or t.get("content") or "").strip()[:560]
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
    import requests as _req

    max_items = 15 if period == "monthly" else 10
    titles = "\n".join(
        f"- {i.get('title', '')} ({i.get('source', '')})"
        for i in items[:max_items]
    )

    period_kr = "월간" if period == "monthly" else "주간"
    prompt = (
        f"{len(items)}개 기사를 분석해 {period_kr} 마케팅 AI 리포트를 작성하세요.\n"
        "3개 카테고리별로 정리: 1)Generative Engine Optimization 2)AI Automation in Marketing Execution 3)Marketing AI Trend\n"
        'JSON 형식: {"period":"' + period + '","headline":"한줄 제목","executive_summary":"3줄 요약",'
        '"trend_sections":[{"category":"카테고리명","summary":"2줄","key_points":["핵심1","핵심2"],"notable_sources":["출처"]}],'
        '"strategic_outlook":"전망 2줄"}\n'
        f"기사목록:\n{titles}"
    )

    try:
        res = _req.post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3.1:8b", "prompt": prompt, "stream": False},
            timeout=300,
        )
        res.raise_for_status()
        raw = (res.json().get("response") or "").strip()
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

def _render_ai_tools_html(ai_tools: List[dict]) -> str:
    if not ai_tools:
        return ""
    cards = ""
    for t in ai_tools:
        title = escape(t.get("title_ko") or t.get("title", ""))
        link = escape(t.get("link", "#"))
        desc = escape(t.get("summary_ko") or (t.get("content") or "")[:520])
        source = escape(t.get("source", ""))
        new_badge = '<span class="badge badge-new">신규</span>' if t.get("is_new") else ""
        cards += f"""
        <div class="ai-tool-card">
            <p class="ai-tool-title"><a href="{link}" target="_blank">{title}</a></p>
            <p class="ai-tool-desc">{desc}</p>
            <div class="ai-tool-meta"><span class="badge badge-tool">AI 도구</span>{new_badge}<span>{source}</span></div>
        </div>"""
    return f"""
    <div class="ai-tools-header"><span class="ai-tools-label">신규 AI 툴</span><div class="ai-tools-line"></div></div>
    <div class="ai-tools-grid">{cards}</div>"""


def _render_insights_html(insights: List[dict]) -> str:
    """3 marketing insight cards — numbered badge, category tag, body, source chips."""
    if not insights:
        return ""
    cards = ""
    for idx, ins in enumerate(insights[:3], 1):
        title = escape(ins.get("title") or "")
        body = escape(ins.get("body") or "")
        tag = escape(ins.get("tag") or "")
        evid = ins.get("evidence") or []
        tag_html = f'<span class="ins-tag">{tag}</span>' if tag else ""
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

def _html_wrapper(title: str, css_path: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)}</title>
    <link rel="stylesheet" href="{css_path}">
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
    {_subscribe_banner_html()}
    {_render_ai_tools_html(ai_tools_ko)}
    {_render_insights_html(insights)}
    {_render_article_cards(articles_ko)}
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
    {subscribe_html}
    {ai_tools_html}
    {insights_html}
    {articles_html}
    {tabs_html}
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
        latest_ai_tools = _fetch_live_ai_tools(limit=8)

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
