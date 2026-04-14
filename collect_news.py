"""RSS-based news collector for Marketing AI Brief.

Fetches from curated AI/marketing sources, scores articles by relevance,
deduplicates, and returns a clean list sorted by recency.

Note: No lru_cache here — caching is handled by st.cache_data in app.py
so the Refresh button can clear it properly.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from io import BytesIO
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import urllib3

import feedparser
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Marketing-AI-Brief/1.0"})
_SESSION.verify = False  # corporate SSL proxy injects self-signed certs
_FEED_TIMEOUT = 8   # seconds per feed
_MAX_WORKERS  = 14  # parallel threads

# ── keyword config (English) ─────────────────────────────────────────
EN_KEYWORD_FILTERS: Tuple[str, ...] = (
    "AI marketing",
    "generative AI advertising",
    "marketing automation",
    "search AI",
    "recommendation system",
    "AI advertising platform",
    "performance marketing AI",
    "brand strategy AI",
    "retail media AI",
    "AI content marketing",
    "martech AI",
    "CMO artificial intelligence",
    "programmatic advertising",
    "customer data platform",
    "attribution model",
    "AI personalization",
    "predictive analytics marketing",
    "AI customer experience",
    "conversational AI",
    "marketing analytics",
    "AI SEO",
    "AI copywriting",
    "creative automation",
    "media mix model",
)

EN_INSIGHT_KEYWORDS: Tuple[str, ...] = (
    "ai marketing", "generative", "marketing automation",
    "search ai", "recommendation", "adtech", "advertising",
    "commerce", "performance marketing", "campaign optimization",
    "llm", "agentic",
    # Research / Consulting content signals
    "consumer behavior", "brand equity", "market share",
    "media spend", "roi", "cmo", "chief marketing",
    "digital transformation", "customer experience",
)

EN_PREFERRED_SOURCES: Tuple[str, ...] = (
    # Tech / Platform
    "openai", "google", "meta", "adweek", "microsoft",
    "marketingdive", "techcrunch", "arxiv", "venturebeat",
    # Research & Consulting
    "hbr", "mckinsey", "bcg", "deloitte", "forrester",
    "kantar", "nielsen", "gartner", "ipsos", "pwc",
    "accenture", "bain", "sloan", "warc", "wpp",
    "emarketer", "adexchanger", "mediapost",
    # Trade press
    "digiday", "thedrum", "campaign", "martech",
    "cmswire", "socialmediatoday", "stanford", "brookings",
)

# ── keyword config (Korean) ──────────────────────────────────────────
KO_KEYWORD_FILTERS: Tuple[str, ...] = (
    "AI 마케팅",
    "마케팅 자동화",
    "생성형 AI 광고",
    "퍼포먼스 마케팅",
    "디지털 마케팅 AI",
)

KO_INSIGHT_KEYWORDS: Tuple[str, ...] = (
    "마케팅", "광고", "자동화", "생성형", "퍼포먼스",
    "커머스", "검색", "추천", "캠페인", "데이터",
    "플랫폼", "브랜드", "콘텐츠", "ai", "llm",
    "애드테크", "타겟팅", "전환", "리텐션", "퍼널",
)

KO_PREFERRED_SOURCES: Tuple[str, ...] = (
    "bloter", "platum", "zdnet", "etnews",
    "marketingchosun", "ditoday", "kakao", "naver",
)

# ── feed URLs ───────────────────────────────────────────────────────
EN_SOURCE_FEEDS: Tuple[str, ...] = (
    # Big Tech AI blogs
    "https://openai.com/news/rss.xml",
    "https://blog.google/technology/ai/rss/",
    "https://ai.meta.com/blog/rss/",
    "https://blogs.microsoft.com/ai/feed/",
    "https://aws.amazon.com/blogs/machine-learning/feed/",
    # Advertising & Marketing trade press
    "https://www.adweek.com/feed/",
    "https://www.marketingdive.com/feeds/news/",
    "https://digiday.com/feed/",
    "https://searchengineland.com/feed",
    "https://martech.org/feed/",
    "https://www.cmswire.com/feed/",
    "https://www.socialmediatoday.com/feed/",
    # Tech press
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://techcrunch.com/tag/advertising-tech/feed/",
    "https://techcrunch.com/tag/e-commerce/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://www.technologyreview.com/feed/",
    "https://www.wired.com/feed/category/business/latest/rss",
    "https://arstechnica.com/ai/feed/",
    # Business & strategy
    "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
    "https://www.businessinsider.com/sai/rss",
    "https://www.emarketer.com/rss.xml",
    "https://www.thedrum.com/feeds/all/rss.xml",
    "https://www.campaignlive.co.uk/feed",
)

KO_SOURCE_FEEDS: Tuple[str, ...] = (
    # Direct RSS that actually work
    "https://platum.kr/feed",                             # Platum — works
    "https://tech.kakao.com/feed",                        # Kakao Tech — works
    "https://d2.naver.com/d2.atom",                       # Naver D2 — works
    "https://www.digitaltoday.co.kr/rss/allArticle.xml",  # DigitalToday — works
)

# Broken Korean RSS → Google News site: proxy
_KO_NEWS_SITES: Tuple[str, ...] = (
    "bloter.net", "zdnet.co.kr", "etnews.com", "it.chosun.com",
    "byline.network", "venturesquare.net",
)


def _ko_site_google_feeds() -> Tuple[str, ...]:
    from urllib.parse import quote
    queries = []
    for site in _KO_NEWS_SITES:
        queries.append(
            f"https://news.google.com/rss/search?q=site:{site}+AI+OR+%EB%A7%88%EC%BC%80%ED%8C%85+OR+%EA%B4%91%EA%B3%A0&hl=ko&gl=KR&ceid=KR:ko"
        )
    return tuple(queries)

RESEARCH_FEEDS_DIRECT: Tuple[str, ...] = (
    "https://sloanreview.mit.edu/feed/",                # MIT Sloan Management Review
    "https://www.ipsos.com/en/rss.xml",                 # Ipsos
    "https://hai.stanford.edu/news/rss.xml",            # Stanford HAI
    "https://www.brookings.edu/topic/artificial-intelligence/feed/", # Brookings AI
    # Academic journals & top-tier publications
    "https://www.nature.com/subjects/artificial-intelligence.rss",  # Nature AI
    "https://www.science.org/action/showFeed?type=searchTopic&taxonomyUri=%2Ftopic%2Fartificial-intelligence&feedType=rss", # Science AI
    "https://feeds.feedburner.com/acabortnews",         # ACM TechNews
    "https://blog.google/technology/ai/rss/",           # Google AI Blog
    "https://openai.com/blog/rss/",                     # OpenAI Blog
    "https://ai.meta.com/blog/rss/",                    # Meta AI Blog
    "https://deepmind.google/blog/rss.xml",             # DeepMind Blog
    "https://www.technologyreview.com/feed/",           # MIT Technology Review
    "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss", # IEEE Spectrum AI
    "https://distill.pub/rss.xml",                      # Distill (ML explainers)
    "https://blogs.microsoft.com/ai/feed/",             # Microsoft AI Blog
    # Korean research & institutions
    "https://www.aitimes.com/rss/allArticle.xml",       # AI 타임스
    "https://www.etnews.com/rss/news.xml",              # 전자신문
    "https://www.zdnet.co.kr/rss/",                     # ZDNet Korea
)

_RESEARCH_SITES: Tuple[str, ...] = (
    "hbr.org",              # Harvard Business Review
    "mckinsey.com",         # McKinsey
    "bcg.com",              # BCG
    "deloitte.com",         # Deloitte
    "accenture.com",        # Accenture
    "gartner.com",          # Gartner
    "forrester.com",        # Forrester
    "kantar.com",           # Kantar
    "nielsen.com",          # Nielsen
    "pwc.com",              # PwC
    "thinkwithgoogle.com",  # Think with Google
    "warc.com",             # WARC
    "bain.com",             # Bain & Company
    "wpp.com",              # WPP
    "emarketer.com",        # eMarketer / Insider Intelligence
    "adexchanger.com",      # AdExchanger
    "mediapost.com",        # MediaPost
    "chiefmartec.com",      # Chief Martec (MarTech landscape)
    "hubspot.com/marketing",  # HubSpot research
    # Academic / journal sites via Google News proxy
    "nature.com",           # Nature
    "science.org",          # Science
    "cell.com",             # Cell Press
    "technologyreview.com", # MIT Technology Review
    "wired.com",            # Wired
    "spectrum.ieee.org",    # IEEE Spectrum
    "arxiv.org",            # arXiv (via Google News for commentary)
)


def _research_google_feeds() -> Tuple[str, ...]:
    """Generate Google News RSS queries scoped to research/consulting domains."""
    queries = []
    for site in _RESEARCH_SITES:
        queries.append(
            f"https://news.google.com/rss/search?q=site:{site}+marketing+OR+AI+OR+advertising&hl=en-US&gl=US&ceid=US:en"
        )
    return tuple(queries)

ARXIV_FEEDS: Tuple[str, ...] = (
    "http://export.arxiv.org/api/query?search_query=all:marketing+AND+all:ai&start=0&max_results=5",
    "http://export.arxiv.org/api/query?search_query=all:recommendation+AND+all:advertising&start=0&max_results=5",
    "http://export.arxiv.org/api/query?search_query=all:generative+AND+all:marketing&start=0&max_results=5",
    "http://export.arxiv.org/api/query?search_query=all:large+language+model+AND+all:marketing&start=0&max_results=5",
    "http://export.arxiv.org/api/query?search_query=all:multimodal+AND+all:advertising&start=0&max_results=3",
)

# ── AI Tool launch feeds ──────────────────────────────────────────
AI_TOOL_FEEDS: Tuple[str, ...] = (
    # General AI tool launches
    "https://news.google.com/rss/search?q=%22AI+tool%22+OR+%22AI+launch%22+OR+%22new+AI+app%22&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=%22AI+startup%22+OR+%22AI+product%22+launch&hl=en-US&gl=US&ceid=US:en",
    # Marketing-specific AI tools
    "https://news.google.com/rss/search?q=%22AI+marketing+tool%22+OR+%22marketing+automation+AI%22+OR+%22AI+advertising+platform%22&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=%22AI+copywriting%22+OR+%22AI+content+creation%22+OR+%22AI+SEO+tool%22&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=%22AI+analytics%22+OR+%22AI+personalization%22+OR+%22martech+AI%22&hl=en-US&gl=US&ceid=US:en",
    # Korean AI tools
    "https://news.google.com/rss/search?q=AI+%ED%88%B4+OR+AI+%EC%B6%9C%EC%8B%9C+OR+%EC%83%9D%EC%84%B1%ED%98%95AI+%EC%84%9C%EB%B9%84%EC%8A%A4&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=%EB%A7%88%EC%BC%80%ED%8C%85+AI+%ED%88%B4+OR+%EA%B4%91%EA%B3%A0+AI+%EC%86%94%EB%A3%A8%EC%85%98&hl=ko&gl=KR&ceid=KR:ko",
    # Direct RSS feeds
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.producthunt.com/feed",
    "https://bensbites.beehiiv.com/feed",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://venturebeat.com/category/ai/feed/",
    "https://martech.org/feed/",
    "https://www.cmswire.com/feed/",
)

AI_TOOL_KEYWORDS: Tuple[str, ...] = (
    # General launch signals
    "launch", "released", "launches", "releasing", "new tool",
    "new ai", "ai tool", "ai app", "ai agent", "ai assistant",
    "ai platform", "introduces", "announces", "unveiled",
    "now available", "open source", "beta", "gpt", "copilot",
    # Marketing-specific signals
    "marketing tool", "marketing platform", "ad tool", "advertising tool",
    "content tool", "seo tool", "analytics tool", "crm ai",
    "email marketing ai", "social media ai", "creative ai",
    "copywriting ai", "personalization engine", "martech",
    "campaign tool", "automation platform", "customer data",
    # Korean signals
    "출시", "공개", "오픈", "베타", "론칭", "새로운", "서비스",
    "ai 툴", "ai 도구", "ai 서비스", "에이전트", "챗봇",
    "마케팅 툴", "광고 자동화", "콘텐츠 생성", "분석 도구",
)

AI_TOOL_BRAND_SIGNALS: Tuple[str, ...] = (
    # Global tech
    "openai", "google", "anthropic", "meta", "microsoft",
    "apple", "amazon", "nvidia", "midjourney", "stability",
    "runway", "notion", "canva", "adobe", "figma", "perplexity",
    "hugging face", "mistral", "cohere", "inflection",
    # MarTech / AdTech brands
    "hubspot", "salesforce", "mailchimp", "semrush", "ahrefs",
    "jasper", "copy.ai", "writesonic", "hootsuite", "sprout social",
    "buffer", "klaviyo", "braze", "amplitude", "mixpanel",
    "segment", "twilio", "intercom", "drift", "optimizely",
    # Korean brands
    "네이버", "카카오", "삼성", "lg", "sk", "토스", "당근",
    "뤼튼", "리턴제로", "업스테이지", "래블업",
)

# ── source priority tiers ────────────────────────────────────────────
# Tier 3 = authoritative research/consulting  (shown first, up to 8/domain)
# Tier 2 = primary media & platforms          (shown next,  up to 6/domain)
# Tier 1 = aggregators (Google News)          (fill gaps,   up to 4/domain)
# Tier 0 = academic preprints (arXiv)         (last resort, cap 4 total)
_SOURCE_PRIORITY: Dict[str, int] = {
    # Tier 3: Research, Consulting & Academic Journals
    "hbr.org": 3, "mckinsey.com": 3, "bcg.com": 3,
    "sloanreview.mit.edu": 3, "deloitte.com": 3,
    "accenture.com": 3, "forrester.com": 3, "gartner.com": 3,
    "kantar.com": 3, "nielsen.com": 3, "ipsos.com": 3,
    "pwc.com": 3, "warc.com": 3, "thinkwithgoogle.com": 3,
    "bain.com": 3, "wpp.com": 3,
    # Academic journals & labs
    "nature.com": 3, "science.org": 3, "cell.com": 3,
    "technologyreview.com": 3, "spectrum.ieee.org": 3,
    "hai.stanford.edu": 3, "brookings.edu": 3,
    "deepmind.google": 3, "distill.pub": 3,
    # Source name matches (Google News entries)
    "harvard business": 3, "mckinsey": 3, "boston consulting": 3,
    "deloitte": 3, "gartner": 3, "forrester": 3, "kantar": 3,
    "nielsen": 3, "ipsos": 3, "bain": 3, "mit sloan": 3,
    "think with google": 3, "warc": 3,
    "nature": 3, "science": 3, "ieee": 3, "mit technology": 3,
    "deepmind": 3, "stanford": 3,
    # Tier 2: Primary media & platforms
    "openai.com": 2, "blog.google": 2, "ai.meta.com": 2,
    "blogs.microsoft.com": 2, "openai.com/blog": 2,
    "adweek.com": 2, "marketingdive.com": 2, "techcrunch.com": 2,
    "wired.com": 2, "theverge.com": 2, "venturebeat.com": 2,
    "bloter.net": 2, "platum.kr": 2, "zdnet.co.kr": 2,
    "etnews.com": 2, "tech.kakao.com": 2, "d2.naver.com": 2,
    "digitaltoday.co.kr": 2, "it.chosun.com": 2,
    "byline.network": 2, "venturesquare.net": 2,
    "aitimes.com": 2,
    # Tier 1: Aggregators
    "news.google.com": 1,
    # Tier 0: Academic preprints
    "arxiv.org": 0, "export.arxiv.org": 0,
}
_TIER_DOMAIN_CAP: Dict[int, int] = {3: 5, 2: 4, 1: 3, 0: 2}
_ARXIV_TOTAL_CAP = 2  # hard ceiling: at most 2 arXiv papers in the final list


def _source_tier(link: str, feed_url: str, source: str = "") -> int:
    """Return priority tier for an article based on its link, feed URL, or source name."""
    combined = (link + " " + feed_url + " " + source).lower()
    best = -1
    for domain, tier in _SOURCE_PRIORITY.items():
        if domain in combined and tier > best:
            best = tier
    return best if best >= 0 else 1


def _en_google_feeds() -> Tuple[str, ...]:
    return tuple(
        f"https://news.google.com/rss/search?q={kw.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
        for kw in EN_KEYWORD_FILTERS
    )


def _ko_google_feeds() -> Tuple[str, ...]:
    from urllib.parse import quote
    return tuple(
        f"https://news.google.com/rss/search?q={quote(kw)}&hl=ko&gl=KR&ceid=KR:ko"
        for kw in KO_KEYWORD_FILTERS
    )


DEFAULT_FEEDS: Tuple[str, ...] = (
    EN_SOURCE_FEEDS
    + KO_SOURCE_FEEDS
    + RESEARCH_FEEDS_DIRECT
    + _en_google_feeds()
    + _ko_google_feeds()
    + _research_google_feeds()
    + _ko_site_google_feeds()
    + ARXIV_FEEDS
)


# ── helpers ─────────────────────────────────────────────────────────
def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(entry: Dict[str, Any]) -> datetime:
    for field in ("published", "updated", "created"):
        raw = entry.get(field)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return datetime.now(timezone.utc)


def _normalize_link(link: str) -> str:
    if not link:
        return ""
    try:
        p = urlsplit(link.strip())
        qs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
              if not k.lower().startswith("utm_")]
        return urlunsplit((
            p.scheme.lower(), p.netloc.lower(),
            re.sub(r"/+$", "", p.path or ""),
            urlencode(sorted(qs)), "",
        ))
    except Exception:
        return link.strip().lower()


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _strip_html(title).lower())


_DOMAIN_NICE_NAMES: Dict[str, str] = {
    "sloanreview.mit.edu": "MIT Sloan Review",
    "feeds.hbr.org": "Harvard Business Review",
    "tech.kakao.com": "Kakao Tech",
    "d2.naver.com": "Naver D2",
    "news.google.com": "Google News",
    "export.arxiv.org": "arXiv",
    "blog.google": "Google AI Blog",
    "ai.meta.com": "Meta AI",
    "digitaltoday.co.kr": "Digital Today",
    "platum.kr": "Platum",
    "www.aitimes.com": "AI타임스",
    "aitimes.com": "AI타임스",
    "www.etnews.com": "전자신문",
    "etnews.com": "전자신문",
    "zdnet.co.kr": "ZDNet Korea",
    "www.zdnet.co.kr": "ZDNet Korea",
}


def _domain_label(url: str) -> str:
    try:
        host = urlsplit(url).netloc.lower()
        nice = _DOMAIN_NICE_NAMES.get(host)
        if nice:
            return nice
        host = re.sub(r"^www\.", "", host)
        parts = host.split(".")
        if len(parts) >= 2:
            return parts[-2].capitalize()
        return host.capitalize() if host else ""
    except Exception:
        return ""


def _is_korean(text: str) -> bool:
    ko_chars = sum(1 for c in text if "\uAC00" <= c <= "\uD7A3")
    return ko_chars > len(text) * 0.15


def _relevance_score(title: str, content: str, source: str, link: str) -> int:
    blob = f"{title} {content} {source} {link}".lower()
    korean = _is_korean(title + content)

    if korean:
        score = sum(2 for kw in KO_INSIGHT_KEYWORDS if kw in blob)
        if any(s in blob for s in KO_PREFERRED_SOURCES):
            score += 3
        if any(t in blob for t in ("분석", "리포트", "트렌드", "전략", "인사이트", "연구")):
            score += 2
    else:
        score = sum(2 for kw in EN_INSIGHT_KEYWORDS if kw in blob)
        if any(s in blob for s in EN_PREFERRED_SOURCES):
            score += 3
        if any(t in blob for t in (
            "analysis", "report", "study", "research", "strategy",
            "survey", "benchmark", "index", "whitepaper", "forecast",
            "outlook", "barometer", "insight", "intelligence",
        )):
            score += 2

    if len(content) >= 120:
        score += 1
    return score


# ── feed fetcher (with timeout, parallel-safe) ───────────────────────
def _fetch_entries(url: str) -> List[Any]:
    """Download one RSS feed and return its entries list."""
    try:
        resp = _SESSION.get(url, timeout=_FEED_TIMEOUT)
        resp.raise_for_status()
        parsed = feedparser.parse(BytesIO(resp.content))
        if getattr(parsed, "bozo", False) and not getattr(parsed, "entries", None):
            return []
        return getattr(parsed, "entries", []) or []
    except Exception:
        return []


# ── main collector ──────────────────────────────────────────────────
def fetch_rss_news(feed_urls: Tuple[str, ...], limit: int = 60) -> List[Dict[str, Any]]:
    if not feed_urls or limit <= 0:
        return []

    now = datetime.now(timezone.utc)
    collected: List[Dict[str, Any]] = []
    seen: set = set()

    # Fetch all feeds in parallel — reduces wall time from Σlatency → max(latency)
    valid_urls = [u for u in feed_urls if u and isinstance(u, str)]
    url_entries: List[tuple[str, List[Any]]] = []
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(valid_urls) or 1)) as pool:
        future_to_url = {pool.submit(_fetch_entries, u): u for u in valid_urls}
        for future in as_completed(future_to_url):
            url_entries.append((future_to_url[future], future.result()))

    for url, entries in url_entries:
        for entry in entries:
            try:
                title = _strip_html(entry.get("title", "Untitled"))
                link = (entry.get("link") or "").strip()
                pub = _parse_date(entry)
                key = (_normalize_link(link), _normalize_title(title), pub.strftime("%Y-%m-%dT%H"))
                if key in seen:
                    continue
                seen.add(key)

                content = _strip_html(entry.get("summary") or entry.get("description") or "")
                source = (entry.get("source", {}) or {}).get("title") if isinstance(entry.get("source"), dict) else None
                if not source:
                    source = _domain_label(link) or _domain_label(url) or "Feed"

                korean = _is_korean(title + content)
                combined_url = (link + " " + url + " " + source).lower()
                is_research = any(s in combined_url for s in (
                    "hbr.org", "mckinsey.com", "bcg.com", "sloanreview",
                    "deloitte.com", "accenture.com", "forrester.com",
                    "kantar.com", "nielsen.com", "gartner.com",
                    "ipsos.com", "pwc.com", "warc.com", "thinkwithgoogle",
                    "bain.com", "wpp.com",
                    "harvard business", "mckinsey", "boston consulting",
                    "deloitte", "gartner", "forrester", "kantar",
                    "nielsen", "ipsos", "bain",
                ))
                is_arxiv = "arxiv" in (link + url).lower()
                if is_arxiv:
                    threshold = 8
                elif is_research:
                    threshold = 3
                elif korean:
                    threshold = 4
                else:
                    threshold = 5
                if not title or _relevance_score(title, content, source, link) < threshold:
                    continue

                tier = _source_tier(link, url, source)
                collected.append({
                    "id": _normalize_link(link) or f"{_normalize_title(title)}-{pub.isoformat()}",
                    "title": title,
                    "link": link,
                    "source": source,
                    "published_at": pub,
                    "published_str": pub.strftime("%Y-%m-%d %H:%M UTC"),
                    "content": content,
                    "is_new": now - pub <= timedelta(hours=24),
                    "lang": "ko" if korean else "en",
                    "is_research": is_research,
                    "_tier": tier,
                    "_feed_url": url,
                })
            except Exception:
                continue

    # Sort: highest tier first, then most recent within same tier
    collected.sort(key=lambda x: (-x["_tier"], -x["published_at"].timestamp()))

    # Apply per-source diversity caps & arXiv total cap
    domain_counts: Dict[str, int] = {}
    arxiv_total = 0
    result: List[Dict[str, Any]] = []
    for item in collected:
        tier = item["_tier"]
        is_arxiv = "arxiv" in item["link"].lower() or "arxiv" in item["_feed_url"].lower()
        if is_arxiv:
            if arxiv_total >= _ARXIV_TOTAL_CAP:
                continue
            arxiv_total += 1
        try:
            domain = urlsplit(item["link"]).netloc.lower()
        except Exception:
            domain = item["source"]
        cap = _TIER_DOMAIN_CAP.get(tier, 4)
        if domain_counts.get(domain, 0) >= cap:
            continue
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        result.append(item)
        if len(result) >= limit:
            break

    # Strip internal fields before returning
    for item in result:
        item.pop("_tier", None)
        item.pop("_feed_url", None)
    return result


# ── Korea AI radar (curated domestic RSS + light keyword gate) ───────
# Note: Naver D2 (d2.naver.com) is intentionally omitted here — it dominated the
# radar with engineering posts; D2 remains in KO_SOURCE_FEEDS for the main digest.
KR_AI_RADAR_FEEDS: Tuple[str, ...] = (
    "https://platum.kr/feed",
    "https://tech.kakao.com/feed",
    "https://www.digitaltoday.co.kr/rss/allArticle.xml",
    "https://www.aitimes.com/rss/allArticle.xml",
    "https://www.etnews.com/rss/news.xml",
    "https://zdnet.co.kr/rss/",
)

_KR_RADAR_NAVER_LINK_CAP = 0
_KR_RADAR_NAVER_IN_TITLE_CAP = 1


def _kr_radar_link_is_naver_host(link: str) -> bool:
    try:
        return "naver" in urlsplit((link or "").strip()).netloc.lower()
    except Exception:
        return False


def _kr_radar_naver_penalty(blob: str, link: str) -> int:
    """Push down Naver-centric items (corp churn) while keeping true AI + Naver product stories."""
    pen = 0
    b = blob or ""
    n = b.count("네이버")
    if n >= 1:
        pen += 8
    if n >= 2:
        pen += 12
    if n >= 3:
        pen += 15
    if _kr_radar_link_is_naver_host(link):
        pen += 25
    return pen


_KR_AI_RADAR_PAT = re.compile(
    r"인공지능|생성형|생성\s*형|\bAI\b|에이아이|ChatGPT|챗지피티|GPT|"
    r"LLM|거대\s*언어|언어\s*모델|딥러닝|추론|파운데이션|파인튜닝|"
    r"클로바|CLOVA|하이퍼클로바|HyperCLOVA|"
    r"오픈\s*AI|OpenAI|Anthropic|클로드|Llama|라마|Mistral|미스트랄|"
    r"AI마케|마케팅AI|마케팅\s*AI|애드테크|"
    r"네이버\s*(클로바|CLOVA|AI|LLM|생성형|검색|클라우드|웍스|지도|파이낸셜|페이)|"
    r"카카오|LG|삼성|SK텔레콤|SKT|"
    r"GPU|NPU|반도체|LLMOps|에이전트|Agent",
    re.I,
)


def _kr_ai_radar_match(blob: str) -> bool:
    if not blob or not blob.strip():
        return False
    if _KR_AI_RADAR_PAT.search(blob):
        return True
    low = blob.lower()
    return any(
        s in low
        for s in (
            " generative ",
            " artificial intelligence",
            "foundation model",
            "large language",
            "machine learning",
            "neural ",
            "openai",
        )
    )


_KR_MARKETING_KO: Tuple[str, ...] = (
    "마케팅",
    "광고",
    "브랜드",
    "캠페인",
    "애드테크",
    "에드테크",
    "퍼포먼스",
    "리테일",
    "커머스",
    "이커머스",
    "CRM",
    "콘텐츠",
    "디지털 마케팅",
    "바이럴",
    "인플루언서",
    "SNS",
    "키워드",
    "SEO",
    "SEM",
    "퍼널",
    "전환",
    "ROAS",
    "구독",
    "고객경험",
    "고객 경험",
    "마케터",
    "CMO",
    "미디어믹스",
    "미디어 믹스",
    "DSP",
    "RTB",
    "프로모션",
    "세일즈",
    "세일즈 마케팅",
)

_KR_MARKETING_EN: Tuple[str, ...] = (
    "marketing",
    "advertising",
    "adtech",
    "martech",
    "brand ",
    " campaign",
    "cmo",
    "retail media",
    "performance marketing",
    "customer experience",
    "conversion",
    "programmatic",
    "commerce",
    "e-commerce",
    "ecommerce",
)


def _kr_radar_marketing_priority(blob: str) -> int:
    """Higher = more marketing / growth / ad-related (used to sort KR radar)."""
    if not blob or not blob.strip():
        return 0
    score = 0
    bl = blob.lower()
    for kw in _KR_MARKETING_KO:
        if kw in blob:
            score += 4
    for kw in _KR_MARKETING_EN:
        if kw in bl:
            score += 3
    # AI × marketing crossover (strong signal for this product)
    has_ai = bool(
        _KR_AI_RADAR_PAT.search(blob)
        or "ai" in bl
        or "llm" in bl
        or "generative" in bl
        or "인공지능" in blob
        or "생성형" in blob
    )
    has_mkt = any(k in blob for k in ("마케팅", "광고", "브랜드", "캠페인", "애드테크", "퍼포먼스"))
    if has_ai and has_mkt:
        score += 12
    return score


def fetch_kr_ai_radar_updates(limit: int = 12, max_age_days: int = 12) -> List[Dict[str, Any]]:
    """Korean tech/biz RSS: AI gate, marketing-first sort, Naver noise penalty, diversify sources."""
    if limit <= 0:
        return []

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    collected: List[Dict[str, Any]] = []
    seen: set = set()

    valid_urls = [u for u in KR_AI_RADAR_FEEDS if u and isinstance(u, str)]
    url_entries: List[tuple[str, List[Any]]] = []
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(valid_urls) or 1)) as pool:
        future_to_url = {pool.submit(_fetch_entries, u): u for u in valid_urls}
        for future in as_completed(future_to_url):
            url_entries.append((future_to_url[future], future.result()))

    for url, entries in url_entries:
        for entry in entries:
            try:
                title = _strip_html(entry.get("title", "Untitled"))
                link = (entry.get("link") or "").strip()
                pub = _parse_date(entry)
                if pub < cutoff:
                    continue
                blob = f"{title} {_strip_html(entry.get('summary') or entry.get('description') or '')}"
                if not _kr_ai_radar_match(blob):
                    continue
                key = _normalize_link(link) or _normalize_title(title)
                if key in seen:
                    continue
                seen.add(key)

                content = _strip_html(entry.get("summary") or entry.get("description") or "")
                source = (entry.get("source", {}) or {}).get("title") if isinstance(entry.get("source"), dict) else None
                if not source:
                    source = _domain_label(link) or _domain_label(url) or "Feed"

                korean = _is_korean(title + content)
                rank_blob = f"{title} {content}"
                mkt = _kr_radar_marketing_priority(rank_blob)
                pen = _kr_radar_naver_penalty(rank_blob, link)
                collected.append({
                    "id": _normalize_link(link) or f"{_normalize_title(title)}-{pub.isoformat()}",
                    "title": title,
                    "link": link,
                    "source": source,
                    "published_at": pub,
                    "published_str": pub.strftime("%Y-%m-%d %H:%M UTC"),
                    "content": content[:400],
                    "is_new": now - pub <= timedelta(hours=36),
                    "lang": "ko" if korean else "en",
                    "_sort": mkt - pen,
                })
            except Exception:
                continue

    # Marketing + low Naver-noise first, then recency
    collected.sort(
        key=lambda x: (-x.get("_sort", 0), -x["published_at"].timestamp())
    )
    out: List[Dict[str, Any]] = []
    naver_links = 0
    naver_in_title = 0
    for item in collected:
        if len(out) >= limit:
            break
        link = (item.get("link") or "").strip()
        tit = item.get("title") or ""
        if _kr_radar_link_is_naver_host(link):
            if naver_links >= _KR_RADAR_NAVER_LINK_CAP:
                continue
            naver_links += 1
        if "네이버" in tit:
            if naver_in_title >= _KR_RADAR_NAVER_IN_TITLE_CAP:
                continue
            naver_in_title += 1
        item.pop("_sort", None)
        out.append(item)
    return out


# ── AI Tool news collector ──────────────────────────────────────────
def _ai_tool_score(title: str, content: str, source: str) -> int:
    """Score how likely an article is about a new AI tool/product launch."""
    blob = f"{title} {content} {source}".lower()
    score = 0
    for kw in AI_TOOL_KEYWORDS:
        if kw in blob:
            score += 2
    for brand in AI_TOOL_BRAND_SIGNALS:
        if brand in blob:
            score += 3
    if len(content) >= 80:
        score += 1
    return score


def fetch_ai_tools_news(limit: int = 12, content_max: int = 520) -> List[Dict[str, Any]]:
    """Collect recent AI tool launches from dedicated feeds."""
    now = datetime.now(timezone.utc)
    collected: List[Dict[str, Any]] = []
    seen: set = set()

    valid_urls = [u for u in AI_TOOL_FEEDS if u and isinstance(u, str)]
    url_entries: List[tuple[str, List[Any]]] = []
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(valid_urls) or 1)) as pool:
        future_to_url = {pool.submit(_fetch_entries, u): u for u in valid_urls}
        for future in as_completed(future_to_url):
            url_entries.append((future_to_url[future], future.result()))

    for url, entries in url_entries:
        for entry in entries:
            try:
                title = _strip_html(entry.get("title", "Untitled"))
                link = (entry.get("link") or "").strip()
                pub = _parse_date(entry)
                if now - pub > timedelta(days=7):
                    continue
                key = (_normalize_link(link), _normalize_title(title))
                if key in seen:
                    continue
                seen.add(key)

                content = _strip_html(
                    entry.get("summary") or entry.get("description") or ""
                )
                source = (
                    (entry.get("source", {}) or {}).get("title")
                    if isinstance(entry.get("source"), dict)
                    else None
                )
                if not source:
                    source = _domain_label(link) or _domain_label(url) or "Feed"

                score = _ai_tool_score(title, content, source)
                if score < 5:
                    continue

                korean = _is_korean(title + content)
                collected.append({
                    "id": _normalize_link(link) or f"{_normalize_title(title)}-{pub.isoformat()}",
                    "title": title,
                    "link": link,
                    "source": source,
                    "published_at": pub,
                    "published_str": pub.strftime("%Y-%m-%d %H:%M UTC"),
                    "content": content[:content_max],
                    "is_new": now - pub <= timedelta(hours=24),
                    "lang": "ko" if korean else "en",
                    "is_ai_tool": True,
                    "_score": score,
                })
            except Exception:
                continue

    collected.sort(key=lambda x: (-x["_score"], -x["published_at"].timestamp()))

    domain_counts: Dict[str, int] = {}
    result: List[Dict[str, Any]] = []
    for item in collected:
        try:
            domain = urlsplit(item["link"]).netloc.lower()
        except Exception:
            domain = item["source"]
        if domain_counts.get(domain, 0) >= 3:
            continue
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        item.pop("_score", None)
        result.append(item)
        if len(result) >= limit:
            break

    return result


# ── YouTube AI creator collector ──────────────────────────────────────
# (channel_name, channel_id, region)
YOUTUBE_AI_CHANNELS_GLOBAL: List[Tuple[str, str, str]] = [
    # ── Global AI / Tech ──
    ("Fireship",          "UCsBjURrPoezykLs9EqgamOA", "global"),
    ("Two Minute Papers", "UCbfYPyITQ-7l4upoX8nvctg", "global"),
    ("Lex Fridman",       "UCSHZKyawb77ixDdsGog4iWA", "global"),
    ("Yannic Kilcher",    "UCZHmQk67mSJgfCCTn7xBfew", "global"),
    ("Matt Wolfe",        "UCbmNph6atAoGfqLoCL_duAg", "global"),
    ("AI Explained",      "UCNJ1Ymd5yFuUPtn21xtRbbw", "global"),
    ("Wes Roth",          "UCx3-JSNXhOJn0RVFbcZ0KVA", "global"),
    ("The AI Breakdown",  "UCq80GDpRHdFHosVEBMCpKlA", "global"),
    # ── Korea — AI / Tech / Development ──
    ("테크몽 Techmong",    "UCtm0cSECNR04lkhRsiE4pjg", "kr"),
    ("노마드 코더 Nomad Coders", "UCUpJs89fSBXNolQGOYKn0YQ", "kr"),
    ("조코딩 JoCoding",    "UCQNE2JmbasNYbjGAcuBiRRg", "kr"),
    ("AI 리더 AILeader",   "UCzUNY-_QDyEnZ97-VgLcMVQ", "kr"),
    ("안될공학 - IT",       "UCVGsi0jm_IhpICcQSxab3qA", "kr"),
    ("셜록현준",            "UCjNaSmJ8fncLX-X5d0lVx5A", "kr"),
    ("AI 프렌즈",          "UCdMBMJdimVjfjHuy5VhJ4gg", "kr"),
    ("캐치딥 CatchDeep",   "UCnxSiqA4PUbJMn7RyNldXPg", "kr"),
    # ── Korea — Tech YouTubers (added) ──
    ("잇섭 itssub",        "UCF4Wxdo3inmxP-Y59wXDsFw", "kr"),
    ("테크플러스 Tech+",    "UCjNpjCIw1FG5foMYPFGH9MA", "kr"),
    ("디에디트 THE EDIT",   "UCwMfp5jfUBJGFBt40NOJKMA", "kr"),
    ("김짤 Kimzzal",       "UCzEvmCAKPvEtAGiT_9UBYpQ", "kr"),
    ("용호수 테크",         "UCsb3o2FS1JdZbk_DdbBGx6A", "kr"),
    ("코딩애플",            "UCSLrpBAzr-ROVNFT-lk4jUA", "kr"),
    ("드로우앤드류 DrawAndrew", "UCdeJUQ3mIkOgZ8GLEM0HhpQ", "kr"),
    ("곽튜브 AI",           "UClRNDVO8093rmRTtLe4GEPw", "kr"),
    ("테크읽어주는나무",      "UCSCRVBx8aFcbpGpf_z5kIOg", "kr"),
    ("AI양브로",            "UCWooBEh83M-a0pOLmmXX_4Q", "kr"),
]


def _yt_video_id(link: str) -> str:
    """Extract YouTube video ID from a watch URL."""
    m = re.search(r"(?:v=|/embed/|youtu\.be/)([a-zA-Z0-9_-]{11})", link or "")
    return m.group(1) if m else ""


def _clean_yt_description(text: str) -> str:
    """Keep only the first meaningful paragraph of a YouTube description."""
    text = _strip_html(text or "")
    # stop at first blank line or URL or timestamp block
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines:
                break
            continue
        if re.match(r"https?://", stripped) or re.match(r"\d+:\d+", stripped):
            break
        lines.append(stripped)
    cleaned = " ".join(lines)
    return cleaned[:400] + ("…" if len(cleaned) > 400 else "")


def fetch_youtube_ai_news(limit: int = 16, days: int = 14) -> List[Dict[str, Any]]:
    """Fetch recent videos from curated AI/marketing YouTube channels via RSS.

    Each video includes a `region` field: "global" or "kr".
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    feed_urls = [
        (name, f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", region)
        for name, cid, region in YOUTUBE_AI_CHANNELS_GLOBAL
    ]

    url_entries: List[tuple[str, str, str, List[Any]]] = []
    with ThreadPoolExecutor(max_workers=min(12, len(feed_urls))) as pool:
        future_to_meta = {
            pool.submit(_fetch_entries, url): (name, url, region)
            for name, url, region in feed_urls
        }
        for future in as_completed(future_to_meta):
            name, url, region = future_to_meta[future]
            url_entries.append((name, url, region, future.result()))

    collected: List[Dict[str, Any]] = []
    seen: set = set()

    for channel, url, region, entries in url_entries:
        for entry in entries[:5]:
            try:
                title = _strip_html(entry.get("title", "Untitled"))
                link = (entry.get("link") or "").strip()
                pub = _parse_date(entry)
                if pub < cutoff:
                    continue
                key = _normalize_link(link)
                if key in seen:
                    continue
                seen.add(key)

                raw_desc = (
                    entry.get("summary") or
                    entry.get("description") or
                    entry.get("yt_videodescription") or ""
                )
                description = _clean_yt_description(raw_desc)
                video_id = _yt_video_id(link)
                thumbnail = (
                    f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
                    if video_id else ""
                )

                collected.append({
                    "id": key or f"yt-{_normalize_title(title)}-{pub.isoformat()}",
                    "title": title,
                    "link": link,
                    "source": channel,
                    "published_at": pub,
                    "published_str": pub.strftime("%Y-%m-%d"),
                    "content": description,
                    "thumbnail": thumbnail,
                    "video_id": video_id,
                    "is_new": now - pub <= timedelta(hours=48),
                    "lang": "ko" if region == "kr" else "en",
                    "region": region,
                    "is_youtube": True,
                })
            except Exception:
                continue

    collected.sort(key=lambda x: -x["published_at"].timestamp())
    return collected[:limit]
